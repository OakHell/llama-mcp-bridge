"""Ollama manifest parser and zero-copy GGUF blob symlinker for llama-mcp-bridge."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from llama_mcp_bridge.gguf_header import parse_gguf_header, recommend_gguf_tuning
from llama_mcp_bridge.hardware import SystemProfile
from llama_mcp_bridge.presets import merge_custom_preset_ini

__all__ = [
    "OllamaImportEntry",
    "OllamaImportReport",
    "import_ollama_models",
]

_MODEL_MEDIA_TYPE = "application/vnd.ollama.image.model"
_PROJECTOR_MEDIA_TYPE = "application/vnd.ollama.image.projector"


@dataclass(frozen=True)
class OllamaImportEntry:
    """Represents a single imported Ollama model symlinked into the bridge workspace."""

    name: str
    preset_name: str
    model_blob: Path
    mmproj_blob: Path | None
    symlink_path: Path
    mmproj_symlink: Path | None
    ctx_size: int
    n_gpu_layers: int


@dataclass
class OllamaImportReport:
    """Summary of an --import-ollama run."""

    imported: list[OllamaImportEntry] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _sanitise_token(raw: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", raw.strip()).strip("-")
    return cleaned or "model"


def _digest_to_blob_path(ollama_dir: Path, digest: str) -> Path | None:
    """Convert an OCI/Ollama digest ('sha256:<hex>') to blobs/sha256-<hex>."""
    norm = digest.strip().lower()
    if ":" not in norm:
        return None
    algo, hex_part = norm.split(":", 1)
    if not algo or not hex_part:
        return None
    return ollama_dir / "blobs" / f"{algo}-{hex_part}"


def _manifest_model_and_tag(manifest_path: Path, manifests_root: Path) -> tuple[str, str]:
    """Derive (model, tag) from <manifests_root>/<registry>/<ns>/<model>/<tag>."""
    rel = manifest_path.relative_to(manifests_root)
    parts = rel.parts
    if len(parts) >= 2:
        return _sanitise_token(parts[-2]), _sanitise_token(parts[-1])
    return _sanitise_token(manifest_path.name), "latest"


def _create_symlink(target_blob: Path, link_path: Path) -> None:
    """Create a symlink at link_path pointing to target_blob (never copies)."""
    link_path.parent.mkdir(parents=True, exist_ok=True)
    if link_path.is_symlink():
        if link_path.resolve() == target_blob.resolve():
            return
        link_path.unlink()
    elif link_path.exists():
        raise FileExistsError(f"Refusing to overwrite non-symlink file at {link_path}")
    try:
        os.symlink(target_blob, link_path)
    except OSError as exc:
        raise RuntimeError(
            f"Failed to create symlink {link_path} -> {target_blob}: {exc}. "
            "Symlinks are required for zero-copy Ollama import."
        ) from exc


def import_ollama_models(
    ollama_dir: Path | None,
    models_dir: Path,
    models_ini: Path,
    profile: SystemProfile,
    residency: str = "moderate",
    dry_run: bool = False,
    tune_mode: str = "conservative",
) -> OllamaImportReport:
    """Walk Ollama manifests, symlink GGUF blobs into models_dir, and auto-tune models.ini."""
    root = (ollama_dir or (Path.home() / ".ollama" / "models")).expanduser()
    manifests_root = root / "manifests"
    report = OllamaImportReport()

    if not manifests_root.is_dir():
        report.warnings.append(f"Ollama manifests directory not found: {manifests_root}")
        return report

    manifest_files = sorted(p for p in manifests_root.rglob("*") if p.is_file())
    for manifest_path in manifest_files:
        model_name, tag = _manifest_model_and_tag(manifest_path, manifests_root)
        label = f"{model_name}-{tag}"
        try:
            payload: Any = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            msg = f"Skipping unreadable manifest {manifest_path}: {exc}"
            report.warnings.append(msg)
            report.skipped.append((label, "invalid JSON manifest"))
            continue

        layers = payload.get("layers") if isinstance(payload, dict) else None
        if not isinstance(layers, list):
            report.skipped.append((label, "missing layers array"))
            continue

        model_layers = [
            layer
            for layer in layers
            if isinstance(layer, dict) and layer.get("mediaType") == _MODEL_MEDIA_TYPE
        ]
        if not model_layers:
            report.skipped.append((label, "no model layer"))
            continue
        if len(model_layers) > 1:
            msg = f"Rejecting split multi-blob Ollama model '{label}' ({len(model_layers)} model layers)."
            report.warnings.append(msg)
            report.skipped.append((label, "split multi-blob model unsupported"))
            continue

        blob_path = _digest_to_blob_path(root, str(model_layers[0].get("digest", "")))
        if blob_path is None or not blob_path.is_file():
            msg = f"Missing Ollama blob for '{label}': {blob_path}"
            report.warnings.append(msg)
            report.skipped.append((label, "missing model blob"))
            continue

        try:
            meta = parse_gguf_header(blob_path)
        except ValueError as exc:
            # Safetensors-backed Ollama models fail GGUF magic validation; warn and skip cleanly.
            msg = f"Skipping non-GGUF / safetensors-backed Ollama model '{label}' ({blob_path.name}): {exc}"
            report.warnings.append(msg)
            report.skipped.append((label, str(exc)))
            continue
        except OSError as exc:
            msg = f"Failed reading GGUF header for '{label}': {exc}"
            report.warnings.append(msg)
            report.skipped.append((label, str(exc)))
            continue

        rec = recommend_gguf_tuning(meta, profile, tune_mode=tune_mode)
        symlink_path = models_dir / f"{label}.gguf"

        proj_layers = [
            layer
            for layer in layers
            if isinstance(layer, dict) and layer.get("mediaType") == _PROJECTOR_MEDIA_TYPE
        ]
        mmproj_blob: Path | None = None
        mmproj_symlink: Path | None = None
        if proj_layers:
            cand_proj = _digest_to_blob_path(root, str(proj_layers[0].get("digest", "")))
            if cand_proj is not None and cand_proj.is_file():
                mmproj_blob = cand_proj
                mmproj_symlink = models_dir / f"{label}-mmproj.gguf"

        preset_name = re.sub(r"[^a-zA-Z0-9_.-]", "_", label)
        if not dry_run:
            _create_symlink(blob_path, symlink_path)
            if mmproj_blob is not None and mmproj_symlink is not None:
                _create_symlink(mmproj_blob, mmproj_symlink)
            merge_custom_preset_ini(
                existing_path=models_ini,
                profile=profile,
                residency=residency,
                models_dir=models_dir,
                preset_name=preset_name,
                model_path=symlink_path,
                mmproj_path=mmproj_symlink,
                ctx_size=rec.ctx_size,
                n_gpu_layers=rec.n_gpu_layers,
            )

        report.imported.append(
            OllamaImportEntry(
                name=label,
                preset_name=preset_name,
                model_blob=blob_path,
                mmproj_blob=mmproj_blob,
                symlink_path=symlink_path,
                mmproj_symlink=mmproj_symlink,
                ctx_size=rec.ctx_size,
                n_gpu_layers=rec.n_gpu_layers,
            )
        )

    return report
