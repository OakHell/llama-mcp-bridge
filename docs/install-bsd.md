# FreeBSD & BSD Installation

FreeBSD (`14.x` / `15.x`) is verified in CI via `vmactions/freebsd-vm`.

## Prerequisites

Install Python 3.11+, `uv`, and `llama.cpp` (`llama-server`) via `pkg` or ports:

```sh
pkg install -y python312 uv
```

## Recommended Mode (`system-bin`)

Prebuilt `llama.cpp` GitHub releases do not ship FreeBSD binaries. Install `llama-server` from ports/packages or build from source, then run:

```sh
llama-mcp-install --mode system-bin -y
llama-mcp-install --doctor
```

## GPU Notes

- NVIDIA GPUs on FreeBSD are probed via `nvidia-smi` when available.
- Otherwise, the installer falls back cleanly to the `6gb` CPU tier (`n-gpu-layers = 0`, `device = none`).

## Service Autostart

`--install-service` on FreeBSD registers an `@reboot` `crontab` entry (requiring an absolute `llama_server_bin` path when `dry_run=False`). See [services.md](services.md).
