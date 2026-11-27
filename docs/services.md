# Background Service Installation (`--install-service`)

Run `llama-mcp-install --install-service` (add `--dry-run` to preview) to register OS-native autostart for `llama-server`:

- **Linux (`systemd`):** Generates a user unit under `~/.config/systemd/user/llama-mcp-server.service` and enables/starts it with `systemctl --user`.
- **Non-systemd Linux & FreeBSD:** Registers an `@reboot` `crontab` entry (requires an absolute `llama_server_bin` path when `dry_run=False`).
- **macOS (`launchd`):** Generates a `LaunchAgents` `.plist` with `WorkingDirectory` and `LLAMA_MCP_HOME` and loads it via `launchctl bootstrap gui/<uid>`.
- **Windows (`schtasks`):** Generates a wrapper `.bat` and registers a Task Scheduler task using `conhost.exe --headless` for windowless startup.

To remove the service and installed binaries while preserving `models/` and `.ini` files:

```bash
llama-mcp-install --uninstall
```
