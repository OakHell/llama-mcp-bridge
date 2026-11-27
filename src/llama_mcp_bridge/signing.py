"""GPG and Sigstore (cosign) signature verification for downloaded llama.cpp releases."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "SignatureVerificationResult",
    "verify_release_signature",
]


@dataclass(frozen=True)
class SignatureVerificationResult:
    """Outcome of verifying a downloaded release archive signature."""

    verified: bool
    method: str
    message: str


def _find_signature_asset(
    archive_name: str, assets: list[dict[str, Any]]
) -> tuple[str, dict[str, Any]] | None:
    lower_target = archive_name.lower()
    for ext, kind in ((".bundle", "cosign-bundle"), (".sig", "sig"), (".asc", "gpg")):
        cand_name = f"{lower_target}{ext}"
        for asset in assets:
            if str(asset.get("name", "")).lower() == cand_name:
                return kind, asset
    return None


def verify_release_signature(
    archive_path: Path,
    assets: list[dict[str, Any]],
    verify_signatures: bool = True,
) -> SignatureVerificationResult:
    """Verify archive_path against matching .bundle/.sig/.asc release assets using cosign or gpg."""
    if not verify_signatures:
        return SignatureVerificationResult(
            verified=False,
            method="skipped",
            message="Signature verification disabled (--no-verify-signature).",
        )

    matched = _find_signature_asset(archive_path.name, assets)
    if matched is None:
        msg = f"No upstream signature asset (.bundle/.sig/.asc) found for {archive_path.name}; skipping signature check."
        print(f"Warning: {msg}")
        return SignatureVerificationResult(verified=False, method="none", message=msg)

    kind, sig_asset = matched
    sig_url = sig_asset.get("browser_download_url", "")
    if not sig_url:
        msg = f"Signature asset {sig_asset.get('name')} has no download URL."
        print(f"Warning: {msg}")
        return SignatureVerificationResult(verified=False, method="none", message=msg)

    cosign_bin = shutil.which("cosign")
    gpg_bin = shutil.which("gpg")

    if kind == "cosign-bundle" and cosign_bin:
        verifier = "cosign"
    elif kind in ("gpg", "sig") and gpg_bin:
        verifier = "gpg"
    elif kind == "sig" and cosign_bin:
        verifier = "cosign"
    else:
        msg = f"Signature asset {sig_asset.get('name')} present, but neither cosign nor gpg is installed."
        print(f"Warning: {msg}")
        return SignatureVerificationResult(verified=False, method="missing-tool", message=msg)

    with tempfile.TemporaryDirectory() as tmpdir:
        sig_path = Path(tmpdir) / str(sig_asset["name"])
        with urllib.request.urlopen(sig_url, timeout=30) as resp:
            sig_path.write_bytes(resp.read())

        if verifier == "cosign":
            cmd = [cosign_bin or "cosign", "verify-blob", "--bundle", str(sig_path), str(archive_path)]
            if kind == "sig":
                cmd = [cosign_bin or "cosign", "verify-blob", "--signature", str(sig_path), str(archive_path)]
        else:
            cmd = [gpg_bin or "gpg", "--verify", str(sig_path), str(archive_path)]

        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if res.returncode != 0:
            raise RuntimeError(
                f"Signature verification FAILED ({verifier}) for {archive_path.name}: {res.stderr.strip() or res.stdout.strip()}"
            )
        return SignatureVerificationResult(
            verified=True,
            method=verifier,
            message=f"Verified {archive_path.name} using {verifier}.",
        )
