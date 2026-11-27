"""Standalone INI validator CLI (llama-mcp-config) for bridge.ini and models.ini."""

from __future__ import annotations

import argparse
import configparser
import importlib.metadata
import sys
from pathlib import Path

__all__ = [
    "check_ini_file",
    "main",
]


def check_ini_file(path: Path, kind: str = "auto") -> list[str]:
    """Validate bridge.ini or models.ini syntax, required keys, and referenced file paths."""
    errors: list[str] = []
    if not path.is_file():
        return [f"File not found: {path}"]

    cp = configparser.ConfigParser(interpolation=None)
    try:
        cp.read(path, encoding="utf-8")
    except configparser.Error as exc:
        return [f"INI syntax error in {path}: {exc}"]

    resolved_kind = kind
    if resolved_kind == "auto":
        resolved_kind = "bridge" if path.name.lower() == "bridge.ini" else "models"

    if resolved_kind == "bridge":
        if not cp.has_section("bridge"):
            errors.append("Missing required [bridge] section.")
            return errors
        residency = cp.get("bridge", "residency", fallback="moderate").strip().lower()
        if residency not in ("stingy", "moderate", "snappy"):
            errors.append(f"Invalid residency {residency!r}; expected 'stingy', 'moderate', or 'snappy'.")
        if cp.has_option("bridge", "port"):
            try:
                port = cp.getint("bridge", "port")
                if not (1 <= port <= 65535):
                    errors.append(f"Port {port} out of valid range 1-65535.")
            except ValueError:
                errors.append(f"Invalid non-integer port: {cp.get('bridge', 'port')!r}")
        for key in ("models_ini", "llama_server_bin"):
            if cp.has_option("bridge", key):
                raw = cp.get("bridge", key).strip()
                if raw:
                    target = Path(raw).expanduser()
                    if not target.is_absolute():
                        target = path.parent / target
                    if not target.exists():
                        errors.append(f"Referenced {key} path does not exist: {target}")
    else:
        presets = [s for s in cp.sections() if s != "*"]
        if not presets:
            errors.append("No preset sections found in models.ini.")
        for sec in presets:
            if not cp.has_option(sec, "model"):
                errors.append(f"Preset [{sec}] is missing required 'model' option.")
                continue
            for key in ("model", "mmproj"):
                if cp.has_option(sec, key):
                    raw = cp.get(sec, key).strip()
                    if raw and not Path(raw).expanduser().is_file():
                        errors.append(f"[{sec}] {key} file not found: {raw}")

    return errors


def main() -> None:
    """Entry point for llama-mcp-config CLI."""
    parser = argparse.ArgumentParser(description="Validate llama-mcp-bridge configuration files.")
    parser.add_argument(
        "--version",
        action="version",
        version=f"llama-mcp-config {importlib.metadata.version('llama-mcp-bridge')}",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    chk = sub.add_parser("check", help="Validate bridge.ini or models.ini.")
    chk.add_argument("path", type=str, help="Path to bridge.ini or models.ini.")
    chk.add_argument(
        "--type",
        choices=["auto", "bridge", "models"],
        default="auto",
        help="INI file type (default: auto-detect by filename).",
    )
    args = parser.parse_args()

    if args.command == "check":
        target = Path(args.path).expanduser()
        errors = check_ini_file(target, kind=args.type)
        if errors:
            for err in errors:
                print(f"FAIL: {err}")
            sys.exit(1)
        print(f"PASS: {target} is valid.")
