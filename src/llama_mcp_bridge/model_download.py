"""Model downloading, .part resume, and SHA-256 verification for llama-mcp-bridge."""

from __future__ import annotations

import hashlib
import sys
import urllib.error
import urllib.request
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from rich.progress import Progress

from llama_mcp_bridge.hardware import SystemProfile
from llama_mcp_bridge.presets import (
    get_tier_model_specs,
    probe_model_metadata,
    search_hf_gguf_models,
)

__all__ = [
    "_compute_sha256",
    "_download_spec_list",
    "_download_tier_models",
    "_verify_and_finalize_part",
]


def _inst_attr(name: str, default: Any) -> Any:
    mod = sys.modules.get("llama_mcp_bridge.installer")
    return getattr(mod, name, default) if mod is not None else default


def _compute_sha256(path: Path) -> str:
    """Compute SHA-256 hex digest of a file in 1 MiB streaming chunks."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _verify_and_finalize_part(part: Path, target: Path, filename: str, expected_sha256: str | None) -> None:
    """Verify .part SHA-256 when expected_sha256 is present, then atomically rename."""
    if expected_sha256:
        actual = _compute_sha256(part)
        if actual.lower() != expected_sha256.lower():
            part.unlink(missing_ok=True)
            raise RuntimeError(
                f"SHA-256 mismatch for {filename}: expected {expected_sha256}, got {actual}"
            )
        print(f"Verified SHA-256: {actual[:12]}...")
    part.replace(target)


def _download_spec_list(
    specs: Sequence[dict[str, Any]],
    models_dir: Path,
    dry_run: bool = False,
) -> None:
    """Download a list of model specs into models_dir with .part resume and SHA-256 verification."""
    models_dir.mkdir(parents=True, exist_ok=True)

    for spec in specs:
        target = models_dir / spec["filename"]
        if target.exists() and target.stat().st_size > 0:
            print(f"Skipping existing model: {target.name}")
            continue
        if dry_run:
            size_label = f"{spec['size_gb']} GB" if spec.get("size_gb") else "unknown size"
            print(f"[DRY-RUN] Would download {spec['filename']} ({size_label}) from {spec['url']}")
            continue

        ok, _, expected_sha256 = _inst_attr("probe_model_metadata", probe_model_metadata)(spec["url"])
        if not ok:
            print(f"WARNING: {spec['filename']} not found at {spec['url']}; skipping.")
            stem = spec["filename"].replace(".gguf", "").split("-")[0]
            alts = _inst_attr("search_hf_gguf_models", search_hf_gguf_models)(stem, limit=3)
            if alts:
                print(f"  Suggested HF GGUF alternatives: {', '.join(a['id'] for a in alts)}")
            continue

        part = models_dir / f"{spec['filename']}.part"
        offset = part.stat().st_size if part.exists() else 0
        req = urllib.request.Request(spec["url"])
        if offset > 0:
            req.add_header("Range", f"bytes={offset}-")

        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                status = getattr(resp, "status", 200)
                mode = "ab" if status == 206 and offset > 0 else "wb"
                total = int(resp.headers.get("Content-Length", "0") or 0)
                is_resume = status == 206 and offset > 0
                full_total = (offset + total) if (is_resume and total > 0) else (total or None)
                completed = offset if is_resume else 0
                progress_cls = _inst_attr("Progress", Progress)
                with progress_cls() as progress:
                    task = progress.add_task(
                        f"Downloading {spec['filename']}", total=full_total, completed=completed
                    )
                    with part.open(mode) as fh:
                        while True:
                            chunk = resp.read(1024 * 1024)
                            if not chunk:
                                break
                            fh.write(chunk)
                            progress.update(task, advance=len(chunk))
            _verify_and_finalize_part(part, target, spec["filename"], expected_sha256)
        except urllib.error.HTTPError as err:
            if err.code == 416 and part.exists():
                _verify_and_finalize_part(part, target, spec["filename"], expected_sha256)
            else:
                raise


def _download_tier_models(
    profile: SystemProfile,
    models_dir: Path,
    dry_run: bool = False,
    presets: Sequence[str] | None = None,
) -> None:
    """Download curated GGUF models for the detected tier with .part resume support."""
    specs = get_tier_model_specs(profile.tier, presets=presets)
    _download_spec_list(specs, models_dir, dry_run=dry_run)
