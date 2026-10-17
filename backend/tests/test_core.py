"""关键流程测试：SM-2（AC-07）、逻辑日（AC-14）、笔记与 AI 流程（模拟大模型）。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from app.logic.sm2 import SM2State, schedule
from app.logic.timeutil import logical_day


def test_ac07_sm2():
    cases = [
        (SM2State(0, 0, 250), "forgot", 1, 250),
        (SM2State(1, 1, 250), "hard", 6, 236),
        (SM2State(2, 6, 250), "good", 16, 260),
        (SM2State(3, 16, 260), "good", 43, 270),
        (SM2State(3, 15, 130), "hard", 20, 130),
    ]
    for state, grade, interval, ef in cases:
        new = schedule(state, grade)
        assert (new.interval, new.ef) == (interval, ef)


def test_ac14_logical_day():
    tz = timezone(timedelta(hours=8))
    assert logical_day(datetime(2026, 10, 16, 1, 30, tzinfo=tz)).isoformat() == "2026-10-15"
    assert logical_day(datetime(2026, 10, 16, 4, 0, tzinfo=tz)).isoformat() == "2026-10-16"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(config, "MATERIALS_DIR", tmp_path / "materials")
    from app.main import app
    with TestClient(app) as c:
        yield c


def mock_llm(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    assert request.headers["authorization"] == "Bearer sk-test1234"
    system = body["messages"][0]["content"]
    if body.get("stream"):
        text = "## 进程\n\n进程是资源分配单位。"
        chunks = "".join(
            f'data: {json.dumps({"choices": [{"delta": {"content": ch}, "finish_reason": None}]})}\n\n' for ch in text
        ) + "data: [DONE]\n\n"
        return httpx.Response(200, text=chunks, headers={"content-type": "text/event-stream"})
    if "复习卡片" in system:
        content = json.dumps({"items": [{"front": "什么是进程", "back": "资源分配的基本单位"}]}, ensure_ascii=False)
    else:
        content = "pong"
    return httpx.Response(200, json={"choices": [{"message": {"content": content}, "finish_reason": "stop"}]})


def sse_events(text: str):
    for block in text.strip().split("\n\n"):
        lines = block.split("\n")
        yield lines[0][7:], json.loads(lines[1][6:])


def test_full_flow(client, monkeypatch):
    from app.llm import gateway
    monkeypatch.setattr(gateway, "TRANSPORT", httpx.MockTransport(mock_llm))

    courses = client.get("/api/courses").json()
    assert len(courses) == 2  # 示例数据
    cid = courses[0]["id"]
    note = client.post("/api/notes", json={"course_id": cid, "title": "测试", "content": "进程 线程 1 2 3"}).json()

    # 未配置 → 409；配置后需同意 → 409；同意后可运行
    assert client.post("/api/ai/run", json={"kind": "organize", "note_id": note["id"]}).status_code == 409
    cfg = client.post("/api/ai/configs", json={"base_url": "https://api.example.com", "api_key": "sk-test1234",
                                               "model": "m"}).json()
    assert "sk-test1234" not in json.dumps(cfg) and cfg["api_key_masked"] == "****1234"
    r = client.post("/api/ai/run", json={"kind": "organize", "note_id": note["id"]})
    assert r.json()["error"]["code"] == "CONSENT_REQUIRED"
    client.post(f"/api/ai/configs/{cfg['id']}/consent")

    r = client.post("/api/ai/run", json={"kind": "organize", "note_id": note["id"]})
    events = list(sse_events(r.text))
    assert events[-1][0] == "done"
    draft = events[-1][1]["draft"]
    assert any("数字" in w for w in draft["warnings"])  # 1 2 3 被丢掉，事实校验提示
    acc = client.post(f"/api/ai/drafts/{draft['id']}/accept", json={}).json()
    assert acc["note"]["content"].startswith("## 进程")
    again = client.post(f"/api/ai/drafts/{draft['id']}/accept", json={}).json()
    assert again["already"] is True

    revs = client.get(f"/api/notes/{note['id']}/revisions").json()
    restored = client.post(f"/api/notes/{note['id']}/revisions/{revs[0]['id']}/restore").json()
    assert restored["content"] == "进程 线程 1 2 3"

    r = client.post("/api/ai/run", json={"kind": "extract", "note_id": note["id"], "count": 5})
    draft = list(sse_events(r.text))[-1][1]["draft"]
    assert client.post(f"/api/ai/drafts/{draft['id']}/accept", json={}).json()["created_cards"] == 1

    queue = client.get("/api/review/queue").json()
    card = queue["items"][0]
    out = client.post(f"/api/review/{card['id']}", json={"grade": "good"}).json()
    assert out["interval"] == 1 and out["n"] == 1

    # 删除进回收站并恢复
    assert client.delete(f"/api/notes/{note['id']}").json()["deleted_cards"] == 1
    assert client.get("/api/trash").json()[0]["id"] == note["id"]
    client.post(f"/api/notes/{note['id']}/restore")
    assert len(client.get("/api/cards", params={"note_id": note["id"]}).json()) == 1

    assert client.get("/api/search", params={"q": "进程"}).json()["total"] >= 1
    html = client.post("/api/export/html", json={"note_ids": [n["id"] for n in client.get("/api/notes").json()]})
    assert "<math" in html.text and "<script" not in html.text

    # 非本机来源的写请求被拒绝
    assert client.post("/api/courses", json={"name": "x"}, headers={"origin": "https://evil.com"}).status_code == 403


def test_material_upload(client):
    cid = client.get("/api/courses").json()[0]["id"]
    files = [("files", ("a.cpp", b"int main(){}\n" * 50, "text/plain")),
             ("files", ("b.doc", b"xx", "application/msword"))]
    res = client.post("/api/materials", data={"course_id": cid}, files=files).json()["results"]
    assert res[0]["ok"] and not res[1]["ok"]
    m = client.get(f"/api/materials/{res[0]['material_id']}").json()
    assert m["status"] == "ready" and m["units"][0]["locator"] == "cpp:L0001-L0040"


def test_plan_rules():
    from app.logic.plan_rules import validate_tasks
    tasks = [{"title": "a", "kind": "learn", "due_date": "2026-10-17", "estimated_minutes": 50},
             {"title": "b", "kind": "learn", "due_date": "2026-10-17", "estimated_minutes": 50}]
    problems = validate_tasks(tasks, today="2026-10-16", deadline="2026-10-20", daily_minutes=60)
    assert any("超过" in p["message"] for p in problems)
