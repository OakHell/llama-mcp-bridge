"""Unit tests for model_download.py: HEAD size_bytes preflight (B14), HTTP 416 (B15), chunk retry (B16)."""

from __future__ import annotations

import email.message
import io
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from llama_mcp_bridge.model_download import _download_spec_list


def test_b14_head_size_bytes_preflight_used_over_static_size_gb(tmp_path: Path) -> None:
    """B14: ensure_disk_space must use HEAD size_bytes when available, falling back to size_gb."""
    spec = {
        "filename": "m.gguf",
        "url": "https://example.com/m.gguf",
        "size_gb": 1.0,
    }
    resp = MagicMock()
    resp.status = 200
    resp.headers = {"Content-Length": "4"}
    resp.read.side_effect = [b"data", b""]
    resp.__enter__ = lambda s: s
    resp.__exit__ = lambda s, *a: None

    with (
        patch("llama_mcp_bridge.installer.probe_model_metadata", return_value=(True, 123456, None)),
        patch("llama_mcp_bridge.model_download.ensure_disk_space") as mock_space,
        patch("llama_mcp_bridge.model_download.DEFAULT_HTTP_CLIENT.urlopen", return_value=resp),
    ):
        _download_spec_list([spec], tmp_path)

    mock_space.assert_called_once_with(tmp_path, 123456, "m.gguf")


def test_b15_http_416_validates_content_range_before_finalizing(tmp_path: Path) -> None:
    """B15: HTTP 416 finalizes .part only when offset == Content-Range total."""
    spec = {"filename": "m.gguf", "url": "https://example.com/m.gguf", "size_gb": 0.0}
    part = tmp_path / "m.gguf.part"
    part.write_bytes(b"12345")

    hdr_ok = email.message.Message()
    hdr_ok["Content-Range"] = "bytes */5"
    err_ok = urllib.error.HTTPError(spec["url"], 416, "Range Not Satisfiable", hdr_ok, io.BytesIO())

    with (
        patch("llama_mcp_bridge.installer.probe_model_metadata", return_value=(True, 5, None)),
        patch("llama_mcp_bridge.model_download.DEFAULT_HTTP_CLIENT.urlopen", side_effect=err_ok),
    ):
        _download_spec_list([spec], tmp_path)
    assert (tmp_path / "m.gguf").read_bytes() == b"12345"

    # Mismatched / oversized .part must raise
    (tmp_path / "m.gguf").unlink()
    part.write_bytes(b"123456789")
    hdr_bad = email.message.Message()
    hdr_bad["Content-Range"] = "bytes */5"
    err_bad = urllib.error.HTTPError(spec["url"], 416, "Range Not Satisfiable", hdr_bad, io.BytesIO())

    with (
        patch("llama_mcp_bridge.installer.probe_model_metadata", return_value=(True, 5, None)),
        patch("llama_mcp_bridge.model_download.DEFAULT_HTTP_CLIENT.urlopen", side_effect=err_bad),
        pytest.raises(RuntimeError, match="HTTP 416"),
    ):
        _download_spec_list([spec], tmp_path)


def test_b16_chunk_read_timeout_retries_and_resumes(tmp_path: Path) -> None:
    """B16: Transient TimeoutError mid-stream must retry and resume from current .part offset."""
    spec = {"filename": "m.gguf", "url": "https://example.com/m.gguf", "size_gb": 0.0}

    resp1 = MagicMock()
    resp1.status = 200
    resp1.headers = {"Content-Length": "6"}
    resp1.read.side_effect = [b"abc", TimeoutError("socket timed out")]
    resp1.__enter__ = lambda s: s
    resp1.__exit__ = lambda s, *a: None

    resp2 = MagicMock()
    resp2.status = 206
    resp2.headers = {"Content-Length": "3"}
    resp2.read.side_effect = [b"def", b""]
    resp2.__enter__ = lambda s: s
    resp2.__exit__ = lambda s, *a: None

    with (
        patch("llama_mcp_bridge.installer.probe_model_metadata", return_value=(True, 6, None)),
        patch("llama_mcp_bridge.model_download.time.sleep"),
        patch("llama_mcp_bridge.model_download.DEFAULT_HTTP_CLIENT.urlopen", side_effect=[resp1, resp2]),
    ):
        _download_spec_list([spec], tmp_path)

    assert (tmp_path / "m.gguf").read_bytes() == b"abcdef"


def test_n13_sha256_mismatch_preserves_part_corrupt(tmp_path: Path) -> None:
    """N13: SHA-256 mismatch renames .part to .part.corrupt and includes path in error."""
    spec = {"filename": "bad.gguf", "url": "https://example.com/bad.gguf", "size_gb": 0.0}
    resp = MagicMock()
    resp.status = 200
    resp.headers = {"Content-Length": "7"}
    resp.read.side_effect = [b"corrupt", b""]
    resp.__enter__ = lambda s: s
    resp.__exit__ = lambda s, *a: None

    with (
        patch("llama_mcp_bridge.installer.probe_model_metadata", return_value=(True, 7, "0" * 64)),
        patch("llama_mcp_bridge.model_download.DEFAULT_HTTP_CLIENT.urlopen", return_value=resp),
        pytest.raises(RuntimeError, match=r"bad\.gguf\.part\.corrupt"),
    ):
        _download_spec_list([spec], tmp_path)

    assert (tmp_path / "bad.gguf.part.corrupt").read_bytes() == b"corrupt"
    assert not (tmp_path / "bad.gguf.part").exists()
