# Changelog

## [1.0.1] - 2026-11-27

- **Fixed:** `LlamaSupervisor._start_server()` now passes `--host`, `--port`, `--models-preset`, `--models-dir`, and residency flags (`--models-max`, `--sleep-idle-seconds`) when auto-spawning `llama-server`, preventing `llama-server` from binding the default port `9931` with zero models instead of the configured `bridge.ini` endpoint.
- **Fixed:** `LlamaServerConfig` and `create_supervisor()` now forward `models_ini`, `models_dir`, and `residency` parameters to `_start_server()`.
- **Added:** Optional `models_max` and `sleep_idle_seconds` overrides in `bridge.ini` (`BridgeConfig` / `LlamaServerConfig`) to override tier+residency defaults.
- **Tests:** Added unit tests in `tests/test_supervisor_spawn_args.py` verifying CLI flags across `stingy`, `moderate`, and `snappy` residency profiles.
