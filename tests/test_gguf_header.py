import configparser
import struct
from pathlib import Path
from unittest.mock import patch

import pytest

from llama_mcp_bridge.gguf_header import (
    estimate_kv_cache_bytes,
    parse_gguf_header,
    recommend_gguf_tuning,
)
from llama_mcp_bridge.hardware import SystemProfile


def _pack_string(s: str) -> bytes:
    raw = s.encode("utf-8")
    return struct.pack("<Q", len(raw)) + raw


def _pack_kv(key: str, val_type: int, value) -> bytes:
    buf = bytearray(_pack_string(key))
    buf += struct.pack("<I", val_type)
    if val_type == 4:  # uint32
        buf += struct.pack("<I", int(value))
    elif val_type == 5:  # int32
        buf += struct.pack("<i", int(value))
    elif val_type == 7:  # bool
        buf += struct.pack("<?", bool(value))
    elif val_type == 8:  # string
        buf += _pack_string(str(value))
    elif val_type == 9:  # array of (elem_type, items)
        elem_type, items = value
        buf += struct.pack("<IQ", elem_type, len(items))
        for item in items:
            if elem_type == 4:
                buf += struct.pack("<I", int(item))
            elif elem_type == 7:
                buf += struct.pack("<?", bool(item))
            else:
                raise ValueError(f"Unsupported test elem_type {elem_type}")
    else:
        raise ValueError(f"Unsupported test val_type {val_type}")
    return bytes(buf)


def _write_synthetic_gguf(
    path: Path,
    kv_entries: list[tuple[str, int, object]],
    pad_bytes: int = 0,
    magic: bytes = b"GGUF",
    version: int = 3,
) -> Path:
    data = bytearray()
    data += magic
    data += struct.pack("<I", version)
    data += struct.pack("<QQ", 0, len(kv_entries))
    for k, vt, v in kv_entries:
        data += _pack_kv(k, vt, v)
    if pad_bytes > 0:
        data += b"\x00" * pad_bytes
    path.write_bytes(bytes(data))
    return path


def test_parse_invalid_magic_raises(tmp_path: Path):
    bad = tmp_path / "bad.gguf"
    bad.write_bytes(b"NOTG\x03\x00\x00\x00")
    with pytest.raises(ValueError, match="Invalid GGUF magic"):
        parse_gguf_header(bad)


def test_parse_standard_gqa_and_sizing(tmp_path: Path):
    gguf = _write_synthetic_gguf(
        tmp_path / "llama.gguf",
        [
            ("general.architecture", 8, "llama"),
            ("llama.block_count", 4, 32),
            ("llama.context_length", 4, 32768),
            ("llama.embedding_length", 4, 4096),
            ("llama.attention.head_count", 4, 32),
            ("llama.attention.head_count_kv", 4, 8),
        ],
    )
    meta = parse_gguf_header(gguf)
    assert meta.architecture == "llama"
    assert meta.n_layers == 32
    assert meta.head_count_kv == [8] * 32
    assert meta.key_length == 128

    kv_8k = estimate_kv_cache_bytes(meta, 8192, "q8_0", "q8_0")
    assert kv_8k > 0

    gpu = SystemProfile("linux", "x86_64", "nvidia", "RTX 4090", 24.0, "sm_89", "ubuntu", "24gb+")
    rec_cons = recommend_gguf_tuning(meta, gpu, tune_mode="conservative")
    rec_approx = recommend_gguf_tuning(meta, gpu, tune_mode="approximate")
    assert rec_cons.n_gpu_layers == -1
    assert rec_cons.ctx_size == 16384
    assert rec_approx.ctx_size == 32768

    cpu = SystemProfile("linux", "x86_64", "cpu", "CPU", 0.0, "", "ubuntu", "6gb")
    rec_cpu = recommend_gguf_tuning(meta, cpu, tune_mode="conservative")
    assert rec_cpu.n_gpu_layers == 0
    assert rec_cpu.ctx_size == 4096


def test_parse_swa_alternating_global_reduces_kv_cache(tmp_path: Path):
    base_entries = [
        ("general.architecture", 8, "gemma3"),
        ("gemma3.block_count", 4, 8),
        ("gemma3.context_length", 4, 32768),
        ("gemma3.embedding_length", 4, 2048),
        ("gemma3.attention.head_count", 4, 8),
        ("gemma3.attention.head_count_kv", 4, 4),
    ]
    no_swa = parse_gguf_header(_write_synthetic_gguf(tmp_path / "full.gguf", base_entries))
    with_swa = parse_gguf_header(
        _write_synthetic_gguf(
            tmp_path / "swa.gguf",
            [
                *base_entries,
                ("gemma3.attention.sliding_window", 4, 1024),
                ("gemma3.attention.sliding_window_pattern", 4, 4),
            ],
        )
    )
    assert with_swa.swa_layers == [True, True, True, False, True, True, True, False]
    assert estimate_kv_cache_bytes(with_swa, 16384) < estimate_kv_cache_bytes(no_swa, 16384)


def test_parse_hybrid_ssm_array_and_mla(tmp_path: Path):
    hybrid = parse_gguf_header(
        _write_synthetic_gguf(
            tmp_path / "hybrid.gguf",
            [
                ("general.architecture", 8, "jamba"),
                ("jamba.block_count", 4, 4),
                ("jamba.context_length", 4, 16384),
                ("jamba.embedding_length", 4, 1024),
                ("jamba.attention.head_count", 4, 8),
                ("jamba.attention.head_count_kv", 9, (4, [4, 0, 4, 0])),
                ("jamba.ssm.state_size", 4, 16),
                ("jamba.ssm.conv_kernel", 4, 4),
                ("jamba.ssm.inner_size", 4, 2048),
            ],
        )
    )
    assert hybrid.head_count_kv == [4, 0, 4, 0]
    kv_4k = estimate_kv_cache_bytes(hybrid, 4096)
    kv_8k = estimate_kv_cache_bytes(hybrid, 8192)
    assert kv_8k > kv_4k

    mla = parse_gguf_header(
        _write_synthetic_gguf(
            tmp_path / "deepseek2.gguf",
            [
                ("general.architecture", 8, "deepseek2"),
                ("deepseek2.block_count", 4, 16),
                ("deepseek2.context_length", 4, 32768),
                ("deepseek2.embedding_length", 4, 2048),
                ("deepseek2.attention.head_count", 4, 16),
                ("deepseek2.attention.head_count_kv", 4, 16),
                ("deepseek2.attention.kv_lora_rank", 4, 512),
                ("deepseek2.attention.qk_rope_head_dim", 4, 64),
                ("deepseek2.attention.kv_shared_layers", 4, 2),
            ],
        )
    )
    assert mla.kv_lora_rank == 512
    assert mla.kv_shared_layers == 2
    assert estimate_kv_cache_bytes(mla, 8192) > 0


def test_conservative_vs_approximate_vram_partial_offload(tmp_path: Path):
    gguf = _write_synthetic_gguf(
        tmp_path / "big.gguf",
        [
            ("general.architecture", 8, "llama"),
            ("llama.block_count", 4, 32),
            ("llama.context_length", 4, 16384),
            ("llama.embedding_length", 4, 4096),
            ("llama.attention.head_count", 4, 32),
            ("llama.attention.head_count_kv", 4, 8),
        ],
    )
    meta = parse_gguf_header(gguf)
    meta.file_size_bytes = int(6.5 * (1024**3))
    gpu_8gb = SystemProfile("linux", "x86_64", "nvidia", "RTX 3070", 8.0, "sm_86", "ubuntu", "8gb")

    cons = recommend_gguf_tuning(meta, gpu_8gb, tune_mode="conservative")
    approx = recommend_gguf_tuning(meta, gpu_8gb, tune_mode="approximate")
    assert 0 < cons.n_gpu_layers < 32
    assert approx.n_gpu_layers == -1 or approx.n_gpu_layers > cons.n_gpu_layers


def test_models_cli_add_file_with_tune_mode_approximate(capsys, monkeypatch, tmp_path: Path):
    from llama_mcp_bridge.models_cli import main as models_main

    monkeypatch.setenv("LLAMA_MCP_HOME", str(tmp_path))
    gguf = _write_synthetic_gguf(
        tmp_path / "qwen3.gguf",
        [
            ("general.architecture", 8, "qwen3"),
            ("qwen3.block_count", 4, 24),
            ("qwen3.context_length", 4, 32768),
            ("qwen3.embedding_length", 4, 2048),
            ("qwen3.attention.head_count", 4, 16),
            ("qwen3.attention.head_count_kv", 4, 8),
        ],
    )
    gpu_16gb = SystemProfile("linux", "x86_64", "nvidia", "RTX 4080", 16.0, "sm_89", "ubuntu", "16gb")
    with (
        patch("llama_mcp_bridge.models_cli.detect_hardware", return_value=gpu_16gb),
        patch(
            "sys.argv",
            ["llama-mcp-models", "add", "my_qwen3", "--file", str(gguf), "--tune-mode", "approximate"],
        ),
    ):
        models_main()

    out = capsys.readouterr().out
    assert "GGUF Header (qwen3): 24 layers" in out
    assert "Auto-Tune (approximate):" in out
    assert "Disclaimer: Heuristic auto-tuning" in out

    cp = configparser.ConfigParser()
    cp.read(tmp_path / "models.ini", encoding="utf-8")
    assert cp.get("my_qwen3", "n-gpu-layers") == "-1"
    assert cp.get("my_qwen3", "ctx-size") == "32768"
