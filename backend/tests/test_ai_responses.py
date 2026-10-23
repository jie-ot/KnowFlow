"""AI failures and hostile tool shapes, using only mock transports and temporary data."""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.llm import gateway
from app.llm.gateway import LLMError
from app.routers.ai import _parse_compose, _parse_extract, _parse_plan


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "mock.sqlite")
    monkeypatch.setattr(config, "MATERIALS_DIR", tmp_path / "materials")
    from app.main import app
    with TestClient(app) as client:
        cfg = client.post("/api/ai/configs", json={"base_url": "https://mock.example.com", "api_key": "test-only",
                                                    "model": "mock"}).json()
        client.post(f"/api/ai/configs/{cfg['id']}/consent")
        yield client


def stream(chunks, *, terminated=True):
    return "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + ("data: [DONE]\n\n" if terminated else "")


def events(response):
    return [(block.split("\n")[0][7:], json.loads(block.split("\n")[1][6:]))
            for block in response.text.strip().split("\n\n")]


def ask(client):
    return client.post("/api/assistant/chat", json={"messages": [{"role": "user", "content": "Explain"}]})


@pytest.mark.parametrize("content,finish,expected", [
    ("", "stop", "EMPTY"),
    ("not json", "stop", "JSON"),
    ('{"links":{}}', "stop", "STRUCTURE"),
    ('{"links":[{"from":1,"to":"N2"}]}', "stop", "STRUCTURE"),
    ('{"links":[{"from":"N1","to":"N2","reason":{}}]}', "stop", "STRUCTURE"),
    ('{"links":', "length", "TRUNCATED"),
])
def test_recommendation_reports_distinct_output_errors_and_keeps_repair_retry(client, monkeypatch, content, finish, expected):
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": content}, "finish_reason": finish}]})

    monkeypatch.setattr(gateway, "TRANSPORT", httpx.MockTransport(respond))
    response = client.post("/api/graph/suggest", json={})
    assert response.status_code == 502
    assert response.json()["error"]["code"] == f"LLM_{expected}"
    assert len(requests) == 2
    assert "完整 JSON" in requests[1]["messages"][-1]["content"]


def test_network_error_does_not_turn_into_parse_error_or_repeat_request(client, monkeypatch):
    requests = []

    def respond(request):
        requests.append(request)
        raise httpx.ConnectError("mock offline", request=request)

    monkeypatch.setattr(gateway, "TRANSPORT", httpx.MockTransport(respond))
    response = client.post("/api/graph/suggest", json={})
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "LLM_NETWORK"
    assert len(requests) == 1


@pytest.mark.parametrize("response_body", [
    {"choices": [3]},
    {"choices": [{"message": []}]},
    {"choices": [{"message": {"content": {"text": "wrong shape"}}}]},
    {"choices": [{"message": {"content": "text"}, "finish_reason": []}]},
    {"choices": [{"message": {"content": "text"}}], "usage": []},
])
def test_nonstream_envelope_errors_are_handled_as_structure(client, monkeypatch, response_body):
    monkeypatch.setattr(gateway, "TRANSPORT", httpx.MockTransport(lambda _: httpx.Response(200, json=response_body)))
    response = client.post("/api/graph/suggest", json={})
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "LLM_STRUCTURE"


@pytest.mark.parametrize("content,finish,terminated,expected", [
    ("partial answer", "length", True, "TRUNCATED"),
    ("partial answer", None, False, "TRUNCATED"),
    ("", "stop", True, "EMPTY"),
    ("hidden partial", "content_filter", True, "STRUCTURE"),
])
def test_assistant_incomplete_or_empty_answer_never_emits_done(client, monkeypatch, content, finish, terminated, expected):
    text = stream([{"choices": [{"delta": {"content": content}, "finish_reason": finish}]}], terminated=terminated)
    monkeypatch.setattr(gateway, "TRANSPORT", httpx.MockTransport(lambda _: httpx.Response(200, text=text)))
    result = events(ask(client))
    assert result[-1][0] == "error"
    assert result[-1][1]["error"]["kind"] == expected
    assert all(kind != "done" for kind, _ in result)
    assert "".join(data["text"] for kind, data in result if kind == "delta") == content


@pytest.mark.parametrize("name,arguments,expected", [
    ("read_note", "[]", "STRUCTURE"),
    ("read_note", "null", "STRUCTURE"),
    ("read_note", '{"title":3}', "STRUCTURE"),
    ("read_note", '{"title":"x","extra":true}', "STRUCTURE"),
    ("read_note", '{}', "STRUCTURE"),
    ("search_knowflow", '{"types":"note"}', "STRUCTURE"),
    ("search_knowflow", '{"query":"x","types":"wrong"}', "STRUCTURE"),
    ("list_cards", '{"weak_only":"false"}', "STRUCTURE"),
    ("list_tasks", '{"start":"bad date"}', "STRUCTURE"),
    ("list_tasks", '{"start":"2026-10-25","end":"2026-10-20"}', "STRUCTURE"),
    ("not_a_tool", '{}', "STRUCTURE"),
    ("read_note", '{"title":', "JSON"),
])
def test_invalid_tool_arguments_produce_explicit_failure(client, monkeypatch, name, arguments, expected):
    text = stream([{"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "mock-call",
        "function": {"name": name, "arguments": arguments}}]}, "finish_reason": "tool_calls"}]}])
    monkeypatch.setattr(gateway, "TRANSPORT", httpx.MockTransport(lambda _: httpx.Response(200, text=text)))
    result = events(ask(client))
    assert result[-1][0] == "error"
    assert result[-1][1]["error"]["kind"] == expected
    assert all(kind not in ("done", "tool") for kind, _ in result)


@pytest.mark.parametrize("chunk", [
    {"choices": [3]},
    {"choices": [{"delta": []}]},
    {"choices": [{"delta": {"content": {"text": "x"}}}]},
    {"choices": [{"delta": {"tool_calls": {}}}]},
    {"choices": [{"delta": {"tool_calls": [{"index": "0", "function": {}}]}}]},
    {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": []}]}}]},
    {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": {}}}]}}]},
])
def test_malformed_stream_envelopes_are_structured_failures(client, monkeypatch, chunk):
    monkeypatch.setattr(gateway, "TRANSPORT", httpx.MockTransport(lambda _: httpx.Response(200, text=stream([chunk]))))
    result = events(ask(client))
    assert result[-1][0] == "error"
    assert result[-1][1]["error"]["kind"] == "STRUCTURE"


def test_malformed_stream_json_is_not_silently_ignored(client, monkeypatch):
    monkeypatch.setattr(gateway, "TRANSPORT", httpx.MockTransport(lambda _: httpx.Response(200, text="data: {broken\n\n")))
    result = events(ask(client))
    assert result[-1][1]["error"]["kind"] == "JSON"


def test_empty_http_response_is_distinct_from_truncated_output(client, monkeypatch):
    monkeypatch.setattr(gateway, "TRANSPORT", httpx.MockTransport(lambda _: httpx.Response(200, text="")))
    result = events(ask(client))
    assert result[-1][1]["error"]["kind"] == "EMPTY"
    response = client.post("/api/graph/suggest", json={})
    assert response.json()["error"]["code"] == "LLM_EMPTY"


def test_assistant_round_limit_is_incomplete(client, monkeypatch):
    from app.routers import assistant
    monkeypatch.setattr(assistant, "MAX_ROUNDS", 1)
    text = stream([{"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "mock-call",
        "function": {"name": "list_cards", "arguments": "{}"}}]}, "finish_reason": "tool_calls"}]}])
    monkeypatch.setattr(gateway, "TRANSPORT", httpx.MockTransport(lambda _: httpx.Response(200, text=text)))
    result = events(ask(client))
    assert any(kind == "tool" for kind, _ in result)
    assert result[-1][0] == "error" and result[-1][1]["error"]["kind"] == "TRUNCATED"


def test_organize_truncation_keeps_partial_failed_draft_and_original_note(client, monkeypatch):
    course = client.get("/api/courses").json()[0]["id"]
    note = client.post("/api/notes", json={"course_id": course, "title": "source", "content": "original"}).json()
    text = stream([{"choices": [{"delta": {"content": "partial"}, "finish_reason": "length"}]}])
    monkeypatch.setattr(gateway, "TRANSPORT", httpx.MockTransport(lambda _: httpx.Response(200, text=text)))
    result = events(client.post("/api/ai/run", json={"kind": "organize", "note_id": note["id"]}))
    assert result[-1][0] == "error" and result[-1][1]["error"]["kind"] == "TRUNCATED"
    draft = client.get(f"/api/ai/drafts/{result[0][1]['draft_id']}").json()
    assert (draft["status"], draft["output"]) == ("failed", "partial")
    assert client.get(f"/api/notes/{note['id']}").json()["content"] == "original"


@pytest.mark.parametrize("data,parser", [
    ({"items": [{"front": {}, "back": "answer"}]}, lambda data: _parse_extract(data, 5)),
    ({"sections": [{"body": "body", "source_ids": 3}]}, lambda data: _parse_compose(data, {})),
    ({"sections": [{"body": {}, "source_ids": []}]}, lambda data: _parse_compose(data, {})),
    ({"tasks": [{"title": "x", "note_ids": [{}]}]}, None),
    ({"tasks": [{"title": "x", "kind": {}}]}, None),
    ({"questions": 3}, None),
])
def test_model_structures_fail_with_llm_error_instead_of_type_error(data, parser):
    if parser is None:
        parser = lambda value: _parse_plan(value, {"plan": {"deadline": None, "daily_minutes": None},
                                                  "note_aliases": {}, "today": "2026-10-23", "existing": []})
    with pytest.raises(LLMError) as error:
        parser(data)
    assert error.value.kind == "STRUCTURE"


def test_read_note_asks_for_identity_when_titles_are_ambiguous(client):
    from app.db import connect
    from app.routers.assistant import run_tool
    course = client.get("/api/courses").json()[0]["id"]
    notes = [client.post("/api/notes", json={"course_id": course, "title": "duplicate", "content": text}).json()
             for text in ("first body", "second body")]
    conn = connect()
    try:
        ambiguous = json.loads(run_tool(conn, "read_note", {"title": "duplicate"}))
        assert {item["id"] for item in ambiguous["candidates"]} == {note["id"] for note in notes}
        chosen = json.loads(run_tool(conn, "read_note", {"note_id": notes[0]["id"]}))
        assert chosen["id"] == notes[0]["id"] and chosen["content"] == "first body"
    finally:
        conn.close()


def test_ai_compose_duplicate_title_preserves_preexisting_legacy_target(client):
    from app.db import connect, dumps, new_id, now_iso
    from app.logic.links import LinkResolver
    course = client.get("/api/courses").json()[0]["id"]
    target = client.post("/api/notes", json={"course_id": course, "title": "unique before AI"}).json()
    source = client.post("/api/notes", json={"course_id": course, "title": "references"}).json()
    conn = connect()
    draft_id = new_id()
    try:
        # Simulate an older database whose title reference has never been resolved.
        with conn:
            conn.execute("UPDATE notes SET content = ? WHERE id = ?", ("[[unique before AI]]", source["id"]))
            conn.execute("DELETE FROM legacy_link_bindings WHERE source_note_id = ?", (source["id"],))
            conn.execute("INSERT INTO ai_drafts(id, kind, status, course_id, result, created_at) "
                         "VALUES (?, 'compose', 'ready', ?, ?, ?)",
                         (draft_id, course, dumps({"title": "unique before AI", "content": "AI draft", "sources": []}), now_iso()))
        response = client.post(f"/api/ai/drafts/{draft_id}/accept", json={})
        assert response.status_code == 200
        assert response.json()["note"]["id"] != target["id"]
        resolved = LinkResolver(conn).resolve("unique before AI", source["id"], bind=False)
        assert resolved["status"] == "resolved" and resolved["note_id"] == target["id"]
    finally:
        conn.close()
