"""Zero-dependency GGUF v2/v3 binary header parser and KV-cache auto-tuning heuristics."""

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from llama_mcp_bridge.hardware import SystemProfile

_GGUF_MAGIC = b"GGUF"
_SUPPORTED_VERSIONS = (2, 3)

_TYPE_UINT8 = 0
_TYPE_INT8 = 1
_TYPE_UINT16 = 2
_TYPE_INT16 = 3
_TYPE_UINT32 = 4
_TYPE_INT32 = 5
_TYPE_FLOAT32 = 6
_TYPE_BOOL = 7
_TYPE_STRING = 8
_TYPE_ARRAY = 9
_TYPE_UINT64 = 10
_TYPE_INT64 = 11
_TYPE_FLOAT64 = 12

_SCALAR_FORMATS: dict[int, tuple[str, int]] = {
    _TYPE_UINT8: ("<B", 1),
    _TYPE_INT8: ("<b", 1),
    _TYPE_UINT16: ("<H", 2),
    _TYPE_INT16: ("<h", 2),
    _TYPE_UINT32: ("<I", 4),
    _TYPE_INT32: ("<i", 4),
    _TYPE_FLOAT32: ("<f", 4),
    _TYPE_BOOL: ("<?", 1),
    _TYPE_UINT64: ("<Q", 8),
    _TYPE_INT64: ("<q", 8),
    _TYPE_FLOAT64: ("<d", 8),
}

_CACHE_BYTES_PER_ELEM: dict[str, float] = {
    "f32": 4.0,
    "f16": 2.0,
    "bf16": 2.0,
    "q8_0": 1.0625,
    "q4_0": 0.5625,
}


@dataclass
class GgufMetadata:
    """Structured model architecture metadata extracted from a GGUF header."""

    architecture: str
    n_layers: int
    max_context_length: int
    embedding_length: int
    n_heads: int
    head_count_kv: list[int]
    key_length: int
    value_length: int
    sliding_window: int | None
    swa_layers: list[bool]
    ssm_state_size: int = 0
    ssm_conv_kernel: int = 0
    ssm_inner_size: int = 0
    kv_lora_rank: int = 0
    qk_rope_head_dim: int = 0
    kv_shared_layers: int = 0
    file_size_bytes: int = 0


@dataclass
class GgufTuningRecommendation:
    """Heuristic n-gpu-layers and ctx-size recommendation for models.ini."""

    n_gpu_layers: int
    ctx_size: int
    kv_bytes_at_ctx: int
    headroom_ratio: float
    tune_mode: str
    notes: str


def _read_exact(fh: BinaryIO, size: int) -> bytes:
    data = fh.read(size)
    if len(data) != size:
        raise ValueError(f"Unexpected EOF while reading GGUF header (expected {size} bytes, got {len(data)}).")
    return data


def _read_string(fh: BinaryIO) -> str:
    (length,) = struct.unpack("<Q", _read_exact(fh, 8))
    if length > 16 * 1024 * 1024:
        raise ValueError(f"GGUF string length exceeds safety bound: {length}")
    return _read_exact(fh, length).decode("utf-8", errors="replace")


def _read_value(fh: BinaryIO, val_type: int) -> Any:
    if val_type in _SCALAR_FORMATS:
        fmt, size = _SCALAR_FORMATS[val_type]
        return struct.unpack(fmt, _read_exact(fh, size))[0]
    if val_type == _TYPE_STRING:
        return _read_string(fh)
    if val_type == _TYPE_ARRAY:
        elem_type, count = struct.unpack("<IQ", _read_exact(fh, 12))
        if count > 1_000_000:
            raise ValueError(f"GGUF array count exceeds safety bound: {count}")
        return [_read_value(fh, elem_type) for _ in range(count)]
    raise ValueError(f"Unsupported GGUF metadata value type: {val_type}")


def _normalize_head_count_kv(raw: Any, n_layers: int, default_heads: int) -> list[int]:
    if isinstance(raw, list):
        vals = [int(v) for v in raw[:n_layers]]
        if len(vals) < n_layers:
            vals.extend([vals[-1] if vals else default_heads] * (n_layers - len(vals)))
        return vals
    val = int(raw) if raw is not None else default_heads
    return [val] * n_layers


def _normalize_swa_layers(
    sliding_window: int | None,
    pattern: Any,
    n_layers: int,
) -> list[bool]:
    if not sliding_window or sliding_window <= 0:
        return [False] * n_layers
    if isinstance(pattern, list):
        flags = [bool(v) for v in pattern[:n_layers]]
        if len(flags) < n_layers:
            flags.extend([False] * (n_layers - len(flags)))
        return flags
    if isinstance(pattern, int) and pattern > 0:
        # Every `pattern`-th layer is global (non-SWA), others use SWA.
        return [((i + 1) % pattern) != 0 for i in range(n_layers)]
    return [True] * n_layers


def parse_gguf_header(path: Path | str) -> GgufMetadata:
    """Parse GGUF v2/v3 metadata header without reading tensor weights."""
    gguf_path = Path(path)
    if not gguf_path.is_file():
        raise FileNotFoundError(f"GGUF file not found: {gguf_path}")
    file_size = gguf_path.stat().st_size

    with gguf_path.open("rb") as fh:
        magic = _read_exact(fh, 4)
        if magic != _GGUF_MAGIC:
            raise ValueError(f"Invalid GGUF magic bytes in {gguf_path.name}: {magic!r}")
        (version,) = struct.unpack("<I", _read_exact(fh, 4))
        if version not in _SUPPORTED_VERSIONS:
            raise ValueError(f"Unsupported GGUF version {version} in {gguf_path.name}; expected 2 or 3.")
        _, kv_count = struct.unpack("<QQ", _read_exact(fh, 16))
        if kv_count > 100_000:
            raise ValueError(f"GGUF kv_count exceeds safety bound: {kv_count}")

        raw_kv: dict[str, Any] = {}
        for _ in range(kv_count):
            key = _read_string(fh)
            (val_type,) = struct.unpack("<I", _read_exact(fh, 4))
            raw_kv[key] = _read_value(fh, val_type)

    arch = str(raw_kv.get("general.architecture") or "").strip()
    if not arch:
        raise ValueError(f"Missing 'general.architecture' in GGUF header: {gguf_path.name}")

    n_layers_raw = raw_kv.get(f"{arch}.block_count")
    if n_layers_raw is None:
        raise ValueError(f"Missing '{arch}.block_count' in GGUF header: {gguf_path.name}")
    n_layers = int(n_layers_raw)
    if n_layers <= 0:
        raise ValueError(f"Invalid '{arch}.block_count'={n_layers} in {gguf_path.name}")

    n_heads = int(raw_kv.get(f"{arch}.attention.head_count") or 1)
    embedding_length = int(raw_kv.get(f"{arch}.embedding_length") or (n_heads * 128))
    max_ctx = int(raw_kv.get(f"{arch}.context_length") or 8192)
    head_dim_default = max(1, embedding_length // max(1, n_heads))
    key_length = int(raw_kv.get(f"{arch}.attention.key_length") or head_dim_default)
    value_length = int(raw_kv.get(f"{arch}.attention.value_length") or head_dim_default)
    head_count_kv = _normalize_head_count_kv(
        raw_kv.get(f"{arch}.attention.head_count_kv"), n_layers, n_heads
    )

    raw_sw = raw_kv.get(f"{arch}.attention.sliding_window")
    sliding_window = int(raw_sw) if raw_sw is not None and int(raw_sw) > 0 else None
    swa_layers = _normalize_swa_layers(
        sliding_window, raw_kv.get(f"{arch}.attention.sliding_window_pattern"), n_layers
    )

    return GgufMetadata(
        architecture=arch,
        n_layers=n_layers,
        max_context_length=max_ctx,
        embedding_length=embedding_length,
        n_heads=n_heads,
        head_count_kv=head_count_kv,
        key_length=key_length,
        value_length=value_length,
        sliding_window=sliding_window,
        swa_layers=swa_layers,
        ssm_state_size=int(raw_kv.get(f"{arch}.ssm.state_size") or 0),
        ssm_conv_kernel=int(raw_kv.get(f"{arch}.ssm.conv_kernel") or 0),
        ssm_inner_size=int(raw_kv.get(f"{arch}.ssm.inner_size") or 0),
        kv_lora_rank=int(raw_kv.get(f"{arch}.attention.kv_lora_rank") or 0),
        qk_rope_head_dim=int(raw_kv.get(f"{arch}.attention.qk_rope_head_dim") or 0),
        kv_shared_layers=int(raw_kv.get(f"{arch}.attention.kv_shared_layers") or 0),
        file_size_bytes=file_size,
    )


def estimate_kv_cache_bytes(
    meta: GgufMetadata,
    ctx_size: int,
    cache_type_k: str = "q8_0",
    cache_type_v: str = "q8_0",
) -> int:
    """Estimate KV-cache and recurrent SSM state memory in bytes for a given ctx_size."""
    bytes_k = _CACHE_BYTES_PER_ELEM.get(cache_type_k.lower(), 1.0625)
    bytes_v = _CACHE_BYTES_PER_ELEM.get(cache_type_v.lower(), 1.0625)
    active_layers = max(0, meta.n_layers - max(0, meta.kv_shared_layers))

    total = 0.0
    ssm_layers = 0
    for i in range(active_layers):
        kv_heads = meta.head_count_kv[i] if i < len(meta.head_count_kv) else 0
        if kv_heads <= 0:
            ssm_layers += 1
            continue
        is_swa = i < len(meta.swa_layers) and meta.swa_layers[i]
        eff_tokens = min(ctx_size, meta.sliding_window) if (is_swa and meta.sliding_window) else ctx_size
        if meta.kv_lora_rank > 0:
            total += eff_tokens * (meta.kv_lora_rank + meta.qk_rope_head_dim) * bytes_k
        else:
            total += eff_tokens * kv_heads * (meta.key_length * bytes_k + meta.value_length * bytes_v)

    if ssm_layers > 0 and meta.ssm_inner_size > 0:
        per_ssm = meta.ssm_inner_size * (meta.ssm_state_size + max(0, meta.ssm_conv_kernel - 1)) * 4
        total += ssm_layers * per_ssm
    return int(total)


def recommend_gguf_tuning(
    meta: GgufMetadata,
    profile: SystemProfile,
    tune_mode: str = "conservative",
    cache_type_k: str = "q8_0",
    cache_type_v: str = "q8_0",
) -> GgufTuningRecommendation:
    """Recommend n-gpu-layers and ctx-size from GGUF header metadata and hardware tier."""
    mode = tune_mode.strip().lower()
    if mode not in ("conservative", "approximate"):
        raise ValueError(f"Invalid tune_mode {tune_mode!r}; expected 'conservative' or 'approximate'.")

    headroom_ratio = 0.25 if mode == "conservative" else 0.12
    ctx_ceiling = 16384 if mode == "conservative" else 32768
    max_allowed_ctx = min(meta.max_context_length, ctx_ceiling)
    candidates = [c for c in (32768, 16384, 8192, 4096, 2048) if c <= max_allowed_ctx]
    if not candidates:
        candidates = [max(512, max_allowed_ctx)]

    if profile.gpu_vendor == "cpu" or profile.vram_gb <= 0:
        cpu_ctx = min(max_allowed_ctx, 4096 if mode == "conservative" else 8192)
        kv_bytes = estimate_kv_cache_bytes(meta, cpu_ctx, cache_type_k, cache_type_v)
        return GgufTuningRecommendation(
            n_gpu_layers=0,
            ctx_size=cpu_ctx,
            kv_bytes_at_ctx=kv_bytes,
            headroom_ratio=headroom_ratio,
            tune_mode=mode,
            notes="CPU-only profile: n-gpu-layers=0 with bounded context.",
        )

    avail_vram = profile.vram_gb * (1024**3) * (1.0 - headroom_ratio)
    chosen_ctx = candidates[-1]
    chosen_kv = estimate_kv_cache_bytes(meta, chosen_ctx, cache_type_k, cache_type_v)
    fits_full = False
    for cand in candidates:
        kv = estimate_kv_cache_bytes(meta, cand, cache_type_k, cache_type_v)
        if meta.file_size_bytes + kv <= avail_vram:
            chosen_ctx = cand
            chosen_kv = kv
            fits_full = True
            break

    if fits_full:
        n_gpu_layers = -1
        note = f"Full GPU offload fits within {100 - int(headroom_ratio * 100)}% VRAM budget."
    else:
        ratio = (avail_vram * 0.85) / max(1, meta.file_size_bytes)
        n_gpu_layers = max(0, min(meta.n_layers - 1, int(meta.n_layers * ratio)))
        note = (
            f"Weights + KV exceed {profile.vram_gb:.1f} GB VRAM ({mode} headroom); "
            f"partial offload ({n_gpu_layers}/{meta.n_layers} layers)."
        )

    return GgufTuningRecommendation(
        n_gpu_layers=n_gpu_layers,
        ctx_size=chosen_ctx,
        kv_bytes_at_ctx=chosen_kv,
        headroom_ratio=headroom_ratio,
        tune_mode=mode,
        notes=note,
    )
