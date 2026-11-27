# Failure Classification & OOM Fallback

`llama-mcp-bridge` distinguishes **VRAM/RAM out-of-memory (OOM) failures** from **timeouts and transport errors** using server-side `stderr` evidence rather than guessing from HTTP status codes alone.

## Server-Side Log Tail (`supervisor.py`)

When `LlamaSupervisor` spawns `llama-server`, stdout and stderr are redirected to `llama-server.log` (configurable via `server_log` in `bridge.ini`), and `classify_inference_error` inspects the `server-log tail` when classifying failures.

## Classification (`classify_inference_error`)

On HTTP 500/503 or a request/timeout exception in `bridge.py`:

1. `classify_inference_error` scans the `server-log tail` (and exception text) for `_OOM_SIGNATURES` (e.g. `cudaMalloc`, `out of memory`, `failed to allocate`, `vk::OutOfDeviceMemoryError`, `metal: out of memory`, `ggml_metal_buffer_init: failed`, `insufficient memory`).
2. **If OOM is detected:** Returns `(True, diagnostic)` advising the user to lower `ctx-size`, switch `residency = stingy`, or enable `fallback_on_oom = true` for their hardware tier.
3. **If no OOM signature is found:** Returns `(False, diagnostic)` identifying a timeout or connection failure and surfacing the `stderr` tail for debugging.

## Structured OOM Degradation (`fallback_on_oom`)

When `fallback_on_oom = true` in `bridge.ini` and a non-`digest` preset hits a classified OOM:

- `query_llama` automatically retries the text prompt on the lightweight `digest` preset.
- If the original request included images, images are dropped and the response is prefixed with an explicit notice (`"(text-only; images dropped)"`) so the calling agent knows visual context was omitted.
