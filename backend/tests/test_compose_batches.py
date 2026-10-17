"""资料整理分批：长资料拆成几部分依次整理，输出被截断时自动对半拆开重来。"""

import json
import re

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


def test_compose_in_batches(client, monkeypatch):
    from app.llm import gateway
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        user = body["messages"][-1]["content"]
        ids = sorted(set(re.findall(r"\[(S\d+)\]", user)), key=lambda x: int(x[1:]))
        calls.append(ids)
        if len(calls) == 1:  # 第一次假装输出被截断
            chunks = [{"choices": [{"delta": {"content": '{"title": "x", "sec'}, "finish_reason": "length"}]}]
        else:
            content = json.dumps({"title": "操作系统", "sections": [
                {"heading": f"第{ids[0]}节", "body": "内容", "evidence": "source_based", "source_ids": ids}]}, ensure_ascii=False)
            chunks = [{"choices": [{"delta": {"content": content}, "finish_reason": "stop"}]}]
        text = "".join(f"data: {json.dumps(c, ensure_ascii=False)}\n\n" for c in chunks) + "data: [DONE]\n\n"
        return httpx.Response(200, text=text, headers={"content-type": "text/event-stream"})

    monkeypatch.setattr(gateway, "TRANSPORT", httpx.MockTransport(handler))
    cfg = client.post("/api/ai/configs", json={"base_url": "https://api.example.com", "api_key": "sk-x", "model": "m"}).json()
    client.post(f"/api/ai/configs/{cfg['id']}/consent")
    cid = client.get("/api/courses").json()[0]["id"]
    text = "\n".join(f"第 {i} 行：进程同步与互斥的要点说明文字" for i in range(1, 321)).encode()
    mid = client.post("/api/materials", data={"course_id": cid}, files=[("files", ("a.txt", text, "text/plain"))]).json()["results"][0]["material_id"]
    units = [u["id"] for u in client.get(f"/api/materials/{mid}").json()["units"]]
    assert len(units) == 8
    r = client.post("/api/ai/run", json={"kind": "compose", "unit_ids": units})
    events = [(b.split("\n")[0][7:], json.loads(b.split("\n")[1][6:])) for b in r.text.strip().split("\n\n")]
    kinds = [e[0] for e in events]
    assert kinds[-1] == "done", events[-1]
    progress = [e[1] for e in events if e[0] == "progress"]
    assert progress[0]["total"] >= 2 and max(p["total"] for p in progress) > progress[0]["total"]  # 截断后拆成更多份
    covered = {a for c in calls[1:] for a in c}
    assert covered == {f"S{i}" for i in range(1, 9)}
    content = events[-1][1]["draft"]["result"]["content"]
    assert content.count("## ") == len(calls) - 1
