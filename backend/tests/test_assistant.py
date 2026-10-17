"""AI 助手：模拟大模型先调用工具、再回答。"""

import json

import httpx
import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(config, "MATERIALS_DIR", tmp_path / "materials")
    from app.main import app
    with TestClient(app) as c:
        yield c


def sse(chunks):
    return "".join(f"data: {json.dumps(c, ensure_ascii=False)}\n\n" for c in chunks) + "data: [DONE]\n\n"


def test_assistant_tool_loop(client, monkeypatch):
    from app.llm import gateway
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        if len(seen) == 1:
            assert "使用手册" in body["messages"][0]["content"] and body["tools"]
            chunks = [{"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {
                "name": "read_note", "arguments": '{"title": "进程'}}]}}]},
                {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '与线程"}'}}]},
                              "finish_reason": "tool_calls"}]}]
        else:
            tool_msg = body["messages"][-1]
            assert tool_msg["role"] == "tool" and "fork" in tool_msg["content"]
            chunks = [{"choices": [{"delta": {"content": "fork 返回两次。"}, "finish_reason": "stop"}]}]
        return httpx.Response(200, text=sse(chunks), headers={"content-type": "text/event-stream"})

    monkeypatch.setattr(gateway, "TRANSPORT", httpx.MockTransport(handler))
    cfg = client.post("/api/ai/configs", json={"base_url": "https://api.example.com", "api_key": "sk-x", "model": "m"}).json()
    client.post(f"/api/ai/configs/{cfg['id']}/consent")
    r = client.post("/api/assistant/chat", json={"messages": [{"role": "user", "content": "fork 怎么返回？"}], "page": "/notes"})
    events = [b.split("\n") for b in r.text.strip().split("\n\n")]
    kinds = [e[0][7:] for e in events]
    assert "tool" in kinds and kinds[-1] == "done"
    assert "fork 返回两次" in r.text and len(seen) == 2
