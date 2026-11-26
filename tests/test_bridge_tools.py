from pathlib import Path
from unittest.mock import MagicMock, patch

from llama_mcp_bridge.bridge import ask_digest, ask_vision


@patch("llama_mcp_bridge.bridge.LlamaSupervisor.ensure_running")
@patch("llama_mcp_bridge.bridge.requests.post")
def test_ask_digest_with_json_schema(mock_post, mock_supervisor, tmp_path: Path):
    sample = tmp_path / "sample.txt"
    sample.write_text("Test content")

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{"message": {"content": '{"status": "ok"}'}}]
    }
    mock_post.return_value = mock_response

    schema = {"type": "object", "properties": {"status": {"type": "string"}}}
    result = ask_digest(
        "Extract status",
        file_paths=[str(sample)],
        json_schema=schema,
    )

    assert result == '{"status": "ok"}'
    mock_supervisor.assert_called_once()
    call_kwargs = mock_post.call_args.kwargs
    assert call_kwargs["json"]["response_format"]["json_schema"]["schema"] == schema


@patch("llama_mcp_bridge.bridge.LlamaSupervisor.ensure_running")
@patch("llama_mcp_bridge.bridge.requests.post")
def test_ask_vision_multi_image(mock_post, mock_supervisor, tmp_path: Path):
    img1 = tmp_path / "a.png"
    img2 = tmp_path / "b.jpg"
    img1.write_bytes(b"\x89PNG\r\n\x1a\n")
    img2.write_bytes(b"\xff\xd8\xff\xe0")

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{"message": {"content": "Two images analysed."}}]
    }
    mock_post.return_value = mock_response

    result = ask_vision("Describe these images", image_paths=[str(img1), str(img2)])
    assert result == "Two images analysed."
    mock_supervisor.assert_called_once()
    user_content = mock_post.call_args.kwargs["json"]["messages"][1]["content"]
    image_parts = [p for p in user_content if p.get("type") == "image_url"]
    assert len(image_parts) == 2


@patch("llama_mcp_bridge.bridge.LlamaSupervisor.get_status")
def test_get_bridge_status_online_and_offline(mock_status):
    import json

    from llama_mcp_bridge.bridge import get_bridge_status

    mock_status.return_value = {
        "healthy": True,
        "server_url": "http://127.0.0.1:8080",
        "tier": "8gb",
        "vram_gb": 8.0,
        "gpu_vendor": "nvidia",
        "models": {"coder": "loaded"},
    }
    data = json.loads(get_bridge_status())
    assert data["healthy"] is True
    assert data["models"]["coder"] == "loaded"

    mock_status.return_value = {
        "healthy": False,
        "server_url": "http://127.0.0.1:8080",
        "tier": "6gb",
        "vram_gb": 0.0,
        "gpu_vendor": "cpu",
        "models": {},
    }
    offline = json.loads(get_bridge_status())
    assert offline["healthy"] is False
    assert offline["models"] == {}


@patch("llama_mcp_bridge.bridge.LlamaSupervisor.ensure_running")
@patch("llama_mcp_bridge.bridge.requests.post")
def test_oom_http_500_classification_without_fallback(mock_post, mock_ensure, monkeypatch, tmp_path: Path):
    from llama_mcp_bridge.bridge import ask_coder

    monkeypatch.setenv("LLAMA_MCP_HOME", str(tmp_path))
    (tmp_path / "bridge.ini").write_text("[bridge]\nfallback_on_oom = false\n", encoding="utf-8")

    err_resp = MagicMock(status_code=500, text="cudaMalloc failed: out of memory")
    mock_post.return_value = err_resp

    out = ask_coder("Write hello")
    assert "ran out of memory" in out
    assert "residency = stingy" in out
    mock_post.assert_called_once()


@patch("llama_mcp_bridge.bridge.LlamaSupervisor.ensure_running")
@patch("llama_mcp_bridge.bridge.requests.post")
def test_cold_swap_timeout_classification(mock_post, mock_ensure):
    import requests

    from llama_mcp_bridge.bridge import ask_coder

    mock_post.side_effect = requests.exceptions.Timeout("Read timed out")
    out = ask_coder("Write hello")
    assert "timed out during inference or cold-swap" in out
    assert "request_timeout_s" in out


@patch("llama_mcp_bridge.bridge.LlamaSupervisor.ensure_running")
@patch("llama_mcp_bridge.bridge.requests.post")
def test_oom_fallback_on_oom_retries_digest(mock_post, mock_ensure, monkeypatch, tmp_path: Path):
    from llama_mcp_bridge.bridge import ask_coder

    monkeypatch.setenv("LLAMA_MCP_HOME", str(tmp_path))
    (tmp_path / "bridge.ini").write_text("[bridge]\nfallback_on_oom = true\n", encoding="utf-8")

    oom_resp = MagicMock(status_code=500, text="out of memory")
    ok_resp = MagicMock(status_code=200)
    ok_resp.json.return_value = {"choices": [{"message": {"content": "Fallback answer"}}]}
    mock_post.side_effect = [oom_resp, ok_resp]

    out = ask_coder("Write hello")
    assert "[Notice: Preset 'coder' hit VRAM/RAM OOM; automatically fell back to 'digest'.]" in out
    assert "Fallback answer" in out
    assert mock_post.call_count == 2
    assert mock_post.call_args_list[1].kwargs["json"]["model"] == "digest"


@patch("llama_mcp_bridge.bridge.LlamaSupervisor.ensure_running")
@patch("llama_mcp_bridge.bridge.requests.post")
def test_unconfigured_preset_returns_add_guidance(mock_post, mock_ensure, monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LLAMA_MCP_HOME", str(tmp_path))
    (tmp_path / "models.ini").write_text("[*]\nmodels-max = 1\n\n[digest]\nmodel = d.gguf\n", encoding="utf-8")
    (tmp_path / "bridge.ini").write_text(f"[bridge]\nmodels_ini = {tmp_path / 'models.ini'}\n", encoding="utf-8")

    img = tmp_path / "a.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")

    out = ask_vision("Describe", image_paths=[str(img)])
    assert out == "Preset 'vision' is not configured in models.ini. Run: llama-mcp-models add vision"
    mock_ensure.assert_not_called()
    mock_post.assert_not_called()


@patch("llama_mcp_bridge.bridge.LlamaSupervisor.ensure_running")
@patch("llama_mcp_bridge.bridge.requests.post")
def test_ask_preset_and_dynamic_custom_tool_registration(mock_post, mock_ensure, monkeypatch, tmp_path: Path):
    from llama_mcp_bridge.bridge import _register_custom_preset_tools, ask_preset

    monkeypatch.setenv("LLAMA_MCP_HOME", str(tmp_path))
    (tmp_path / "models.ini").write_text(
        "[*]\nmodels-max = 1\n\n[custom-qwen]\nmodel = custom.gguf\n",
        encoding="utf-8",
    )
    (tmp_path / "bridge.ini").write_text(f"[bridge]\nmodels_ini = {tmp_path / 'models.ini'}\n", encoding="utf-8")

    ok_resp = MagicMock(status_code=200)
    ok_resp.json.return_value = {"choices": [{"message": {"content": "Custom OK"}}]}
    mock_post.return_value = ok_resp

    registered = _register_custom_preset_tools()
    assert "ask_custom_qwen" in registered

    out = ask_preset(preset="custom-qwen", prompt="Hello")
    assert out == "Custom OK"
    assert mock_post.call_args.kwargs["json"]["model"] == "custom-qwen"


def test_configured_tools_filtering_omits_unconfigured_builtins(monkeypatch, tmp_path: Path):
    import asyncio

    from fastmcp import FastMCP

    from llama_mcp_bridge.bridge import _register_configured_tools, ask_preset, get_bridge_status

    monkeypatch.setenv("LLAMA_MCP_HOME", str(tmp_path))
    (tmp_path / "models.ini").write_text(
        "[*]\nmodels-max = 1\n\n[digest]\nmodel = d.gguf\n\n[qwen_coder]\nmodel = q.gguf\n",
        encoding="utf-8",
    )
    (tmp_path / "bridge.ini").write_text(f"[bridge]\nmodels_ini = {tmp_path / 'models.ini'}\n", encoding="utf-8")

    srv = FastMCP("test-filtered")
    srv.tool()(get_bridge_status)
    srv.tool()(ask_preset)
    _register_configured_tools(target_mcp=srv)

    tool_names = {t.name for t in asyncio.run(srv.list_tools())}
    assert {"get_bridge_status", "ask_preset", "ask_digest", "ask_qwen_coder"} <= tool_names
    assert "ask_vision" not in tool_names
    assert "ask_coder_long" not in tool_names
