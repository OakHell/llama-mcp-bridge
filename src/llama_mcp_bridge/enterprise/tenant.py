"""Multi-tenant workspace isolation and cross-platform advisory file locking (E11)."""

from __future__ import annotations

import contextlib
import os
import re
from collections.abc import Iterator
from pathlib import Path

__all__ = ["advisory_file_lock", "resolve_tenant_workspace", "validate_tenant_name"]

_TENANT_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def validate_tenant_name(tenant: str) -> str:
    """Validate and return a safe tenant identifier."""
    cleaned = tenant.strip()
    if not cleaned or cleaned in {".", ".."} or not _TENANT_RE.match(cleaned):
        raise ValueError(f"Invalid tenant name {tenant!r}: must match ^[A-Za-z0-9._-]+$.")
    return cleaned


def resolve_tenant_workspace(base_workspace: Path, tenant: str) -> Path:
    """Return <base_workspace>/tenants/<tenant>/ with 0o700 permissions on POSIX."""
    safe_tenant = validate_tenant_name(tenant)
    tenant_dir = base_workspace / "tenants" / safe_tenant
    tenant_dir.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        try:
            os.chmod(tenant_dir, 0o700)
        except OSError:
            pass
    return tenant_dir


@contextlib.contextmanager
def advisory_file_lock(lock_path: Path) -> Iterator[None]:
    """Cross-platform advisory file lock (fcntl.flock on POSIX, msvcrt.locking on Windows)."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+b")
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)  # type: ignore[attr-defined]
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()
