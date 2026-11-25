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
