"""Existing AI draft discard stops later requests and stale writes; models are mocked."""

from __future__ import annotations

import asyncio
import hashlib
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.db import connect, new_id, now_iso
from app.llm import gateway
from app.routers import ai
from app.services.drafts import discard_draft
@pytest.fixture()
def client(tmp_path, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "ai-cancellation.sqlite")
    monkeypatch.setattr(config, "MATERIALS_DIR", tmp_path / "materials")
    config.MATERIALS_DIR.mkdir()
    from app.main import app
    with TestClient(app) as test_client:
        cfg = test_client.post("/api/ai/configs", json={"base_url": "https://mock.invalid", "api_key": "mock-secret",
                                                        "model": "mock"}).json()
        test_client.post(f"/api/ai/configs/{cfg['id']}/consent")
        yield test_client


def course_id(client):
    return client.get("/api/courses").json()[0]["id"]


def note(client, title="source"):
    response = client.post("/api/notes", json={"course_id": course_id(client), "title": title, "content": "source body"})
    assert response.status_code == 201
    return response.json()


def material(client, *, status="uploaded", old_unit=False):
    from app import config
    material_id, stamp = new_id(), now_iso()
    content = b"temporary source"
    (config.MATERIALS_DIR / (material_id + ".txt")).write_bytes(content)
    conn = connect()
    try:
        with conn:
            conn.execute("INSERT INTO materials(id,course_id,original_name,display_title,format,ext,size_bytes,sha256,"
                         "status,uploaded_at,updated_at,unit_count,char_count) VALUES(?,?,'source.txt','source','text',"
                         "'.txt',?,?,?,?,?,?,?)", (material_id, course_id(client), len(content), hashlib.sha256(content).hexdigest(),
                                                 status, stamp, stamp, 1 if old_unit else 0, 8 if old_unit else 0))
            if old_unit:
                conn.execute("INSERT INTO material_units(id,material_id,kind,ordinal,locator,label,text,warnings) "
                             "VALUES(?,?,'text',1,'text:1','old','old text','[\"user_edited\"]')", (new_id(), material_id))
        return material_id
    finally:
        conn.close()


def events(response):
    assert response.status_code == 200
    return [(block.split("\n")[0][7:], json.loads(block.split("\n")[1][6:]))
            for block in response.text.strip().split("\n\n")]


def mock_json(request, data):
    return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(data)}, "finish_reason": "stop"}]})


def test_cancel_ai_during_response_prevents_json_retry_and_revival(client, monkeypatch):
    source = note(client)
    calls = []

    def cancel_in_response(request):
        calls.append(1)
        conn = connect()
        try:
            job_id = conn.execute("SELECT id FROM ai_drafts WHERE status = 'running'").fetchone()[0]
            discard_draft(conn, job_id)
        finally:
            conn.close()
        return mock_json(request, {"wrong": "structure would normally retry"})

    monkeypatch.setattr(gateway, "TRANSPORT", httpx.MockTransport(cancel_in_response))
    result = events(client.post("/api/ai/run", json={"kind": "extract", "note_id": source["id"]}))
    assert result[-1][0] == "error" and result[-1][1]["error"]["kind"] == "CANCELLED"
    assert len(calls) == 1 and all(event != "done" for event, _value in result)
    assert client.get(f"/api/ai/drafts/{result[0][1]['draft_id']}").json()["status"] == "discarded"


def test_cancel_compose_stops_before_second_batch(client, monkeypatch):
    material_id = material(client, status="ready")
    conn = connect()
    unit_ids = [new_id(), new_id()]
    try:
        with conn:
            conn.executemany("INSERT INTO material_units(id,material_id,kind,ordinal,locator,label,text) "
                             "VALUES(?,?,'text',?,'text:1','unit','body')",
                             [(unit_id, material_id, i) for i, unit_id in enumerate(unit_ids)])
    finally:
        conn.close()
    monkeypatch.setattr(ai, "COMPOSE_BATCH_UNITS", 1)
    calls = []

    def cancel_first_batch(_request):
        calls.append(1)
        conn = connect()
        try:
            job_id = conn.execute("SELECT id FROM ai_drafts WHERE status='running'").fetchone()[0]
            discard_draft(conn, job_id)
        finally:
            conn.close()
        output = json.dumps({"title": "draft", "sections": [{"heading": "section", "body": "body", "source_ids": ["S1"]}]})
        packet = {"choices": [{"delta": {"content": output}, "finish_reason": "stop"}]}
        return httpx.Response(200, text="data: " + json.dumps(packet) + "\n\ndata: [DONE]\n\n")

    monkeypatch.setattr(gateway, "TRANSPORT", httpx.MockTransport(cancel_first_batch))
    result = events(client.post("/api/ai/run", json={"kind": "compose", "unit_ids": unit_ids}))
    assert result[-1][1]["error"]["kind"] == "CANCELLED" and len(calls) == 1
    assert all(event != "done" for event, _value in result)


@pytest.mark.parametrize("streaming", [False, True])
def test_canceled_task_waiting_for_model_slot_sends_no_http(monkeypatch, streaming):
    calls = []
    monkeypatch.setattr(gateway, "TRANSPORT", httpx.MockTransport(lambda request: calls.append(request)))

    async def scenario():
        sem = asyncio.Semaphore(0)
        monkeypatch.setattr(gateway, "_semaphore", lambda: sem)
        canceled = False

        def check():
            if canceled:
                raise ai._TaskStopped("discarded")

        async def request():
            kwargs = dict(max_tokens=10, temperature=0, idle_timeout=1, total_timeout=1, before_request=check)
            cfg = gateway.ModelConfig("https://mock.invalid", "test-only", "mock")
            if streaming:
                async for _event in gateway.stream_chat(cfg, [], **kwargs):
                    pass
            else:
                await gateway.chat(cfg, [], **kwargs)

        pending = asyncio.create_task(request())
        await asyncio.sleep(0)
        canceled = True
        sem.release()
        with pytest.raises(ai._TaskStopped):
            await pending

    asyncio.run(scenario())
    assert calls == []


def test_cancel_racing_ready_write_never_reports_done(client, monkeypatch):
    source = note(client)
    monkeypatch.setattr(gateway, "TRANSPORT", httpx.MockTransport(
        lambda request: mock_json(request, {"items": [{"front": "q", "back": "a"}]})))
    finish = ai._finish_draft

    def cancel_before_finish(draft_id, **fields):
        if fields.get("status") == "ready":
            conn = connect()
            try:
                discard_draft(conn, draft_id)
            finally:
                conn.close()
        return finish(draft_id, **fields)

    monkeypatch.setattr(ai, "_finish_draft", cancel_before_finish)
    result = events(client.post("/api/ai/run", json={"kind": "extract", "note_id": source["id"]}))
    assert result[-1][1]["error"]["kind"] == "CANCELLED"
    assert all(event != "done" for event, _value in result)
