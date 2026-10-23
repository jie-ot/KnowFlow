"""Multi-note outlines use temporary datasets and a mock model; originals stay intact."""

from __future__ import annotations

import json
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import httpx
import pytest
from fastapi.testclient import TestClient

from app.llm import gateway
from app.db import connect
from app.seed import seed
from app.llm.gateway import LLMError
from app.routers.ai import _parse_outline


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "outline.sqlite")
    monkeypatch.setattr(config, "MATERIALS_DIR", tmp_path / "materials")
    from app.main import app
    with TestClient(app) as client:
        with closing(connect()) as conn:
            seed(conn)
        cfg = client.post("/api/ai/configs", json={"base_url": "https://mock.example.com", "api_key": "test-only",
                                                   "model": "mock"}).json()
        client.post(f"/api/ai/configs/{cfg['id']}/consent")
        yield client


def notes(client, *, second_title="同名笔记", second_content="第二篇原始内容"):
    course = client.get("/api/courses").json()[0]["id"]
    return [client.post("/api/notes", json={"course_id": course, "title": title, "content": content}).json()
            for title, content in [("同名笔记", "第一篇原始内容"), (second_title, second_content)]]


def model_output():
    return {"title": "综合复习提纲", "sections": [
        {"heading": "联系与区别", "body": "- 比较两个主题\n- 回忆关键要点", "source_ids": ["N1", "N2"]},
        {"heading": "自测", "body": "如何解释第一个主题？", "source_ids": ["N1"]},
    ]}


def run(client, monkeypatch, sources, output=None, *, finish="stop", on_request=None):
    payload = model_output() if output is None else output

    def respond(request):
        if on_request:
            on_request(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)},
                                                     "finish_reason": finish}]})

    monkeypatch.setattr(gateway, "TRANSPORT", httpx.MockTransport(respond))
    return client.post("/api/ai/run", json={"kind": "outline", "scope_note_ids": [source["id"] for source in sources],
                                           "instruction": "比较主题并提出自测问题"})


def events(response):
    assert response.status_code == 200, response.text
    return [(block.split("\n")[0][7:], json.loads(block.split("\n")[1][6:]))
            for block in response.text.strip().split("\n\n")]


def ready(response):
    result = events(response)
    assert result[-1][0] == "done", result
    return result[-1][1]["draft"]


def test_generated_outline_is_only_a_draft_and_same_titles_keep_distinct_sources(client, monkeypatch):
    sources = notes(client)
    sent = []
    count_before = client.get("/api/notes/page").json()["total"]
    draft = ready(run(client, monkeypatch, sources, on_request=sent.append))
    assert draft["kind"] == "outline" and draft["status"] == "ready"
    assert draft["note_id"] is None
    assert draft["params"]["scope_note_ids"] == [source["id"] for source in sources]
    assert "第一篇原始内容" in sent[0]["messages"][-1]["content"]
    assert "第二篇原始内容" in sent[0]["messages"][-1]["content"]
    content = draft["result"]["content"]
    for source in sources:
        assert f"[[id:{source['id']}|同名笔记]]" in content
        assert client.get(f"/api/notes/{source['id']}").json()["content"] == source["content"]
    assert client.get("/api/notes/page").json()["total"] == count_before


def test_edit_then_accept_creates_new_note_preserving_every_section_source_and_originals(client, monkeypatch):
    sources = notes(client)
    draft = ready(run(client, monkeypatch, sources))
    response = client.post(f"/api/ai/drafts/{draft['id']}/accept", json={"title": "我的复习提纲", "outline_sections": [
        {"heading": "重写比较", "body": "我的理解"}, {"heading": "回忆题", "body": "补充自测"},
    ]})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["draft"]["status"] == "edited"
    new = result["note"]
    assert new["id"] not in {source["id"] for source in sources}
    assert new["title"] == "我的复习提纲" and new["version"] == 1
    assert "## 重写比较\n\n我的理解\n\n来源：" in new["content"]
    assert new["content"].count(f"[[id:{sources[0]['id']}|") == 2
    assert new["content"].count(f"[[id:{sources[1]['id']}|") == 1
    for source in sources:
        actual = client.get(f"/api/notes/{source['id']}").json()
        assert (actual["version"], actual["title"], actual["content"]) == (source["version"], source["title"], source["content"])
        backlinks = client.get(f"/api/notes/{source['id']}/links").json()["backlinks"]
        assert any(link["id"] == new["id"] for link in backlinks)


@pytest.mark.parametrize("edit", [
    {"content": "正文替换会丢失来源"},
    {"outline_sections": [{"heading": "只留一节", "body": "试图移除其他来源"}]},
    {"outline_sections": [{"heading": "x", "body": "y", "source_note_ids": ["fake"]}]},
    {"outline_sections": [{"heading": " ", "body": "x"}, {"heading": "x", "body": "y"}]},
    {"title": " "},
])
def test_invalid_edit_is_rejected_without_partial_creation(client, monkeypatch, edit):
    draft = ready(run(client, monkeypatch, notes(client)))
    count_before = client.get("/api/notes/page").json()["total"]
    response = client.post(f"/api/ai/drafts/{draft['id']}/accept", json=edit)
    assert response.status_code == 422
    assert client.get(f"/api/ai/drafts/{draft['id']}").json()["status"] == "ready"
    assert client.get("/api/notes/page").json()["total"] == count_before


def test_invalid_destination_rolls_back_then_acceptance_can_retry(client, monkeypatch):
    draft = ready(run(client, monkeypatch, notes(client)))
    count_before = client.get("/api/notes/page").json()["total"]
    response = client.post(f"/api/ai/drafts/{draft['id']}/accept", json={"course_id": "missing"})
    assert response.status_code == 404
    assert client.get("/api/notes/page").json()["total"] == count_before
    assert client.get(f"/api/ai/drafts/{draft['id']}").json()["status"] == "ready"
    assert client.post(f"/api/ai/drafts/{draft['id']}/accept", json={}).status_code == 200


def test_concurrent_acceptance_and_response_retry_create_exactly_one_new_note(client, monkeypatch):
    from app.db import connect
    from app.services.drafts import AcceptIn, accept_draft
    draft = ready(run(client, monkeypatch, notes(client)))
    count_before = client.get("/api/notes/page").json()["total"]
    barrier = Barrier(2)

    def accept():
        conn = connect()
        try:
            barrier.wait(timeout=5)
            return accept_draft(conn, draft["id"], AcceptIn())
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: accept(), range(2)))
    assert sum(result.get("already", False) for result in results) == 1
    created_id = next(result["note"]["id"] for result in results if "note" in result)
    assert {result["draft"]["note_id"] for result in results} == {created_id}
    retry = client.post(f"/api/ai/drafts/{draft['id']}/accept", json={}).json()
    assert retry["already"] and retry["draft"]["note_id"] == created_id
    assert client.get("/api/notes/page").json()["total"] == count_before + 1


def test_changed_or_deleted_source_warns_without_overwriting_its_new_state(client, monkeypatch):
    sources = notes(client)
    draft = ready(run(client, monkeypatch, sources))
    changed = client.patch(f"/api/notes/{sources[0]['id']}", json={"base_version": sources[0]["version"], "content": "更新的理解"})
    assert changed.status_code == 200, changed.text
    assert client.delete(f"/api/notes/{sources[1]['id']}").status_code == 200
    latest = client.get(f"/api/ai/drafts/{draft['id']}").json()
    assert any("已修改" in warning for warning in latest["warnings"])
    assert any("已被删除" in warning for warning in latest["warnings"])
    result = client.post(f"/api/ai/drafts/{draft['id']}/accept", json={}).json()
    assert len(result["draft"]["warnings"]) == len(latest["warnings"])
    assert client.get(f"/api/notes/{sources[0]['id']}").json()["content"] == "更新的理解"


def test_source_changes_during_slow_model_call_appear_on_ready_draft(client, monkeypatch):
    from app.db import connect, now_iso
    sources = notes(client)

    def change_source(_):
        conn = connect()
        try:
            with conn:
                conn.execute("UPDATE notes SET content = 'changed while generating', version = version + 1, updated_at = ? WHERE id = ?",
                             (now_iso(), sources[0]["id"]))
        finally:
            conn.close()

    draft = ready(run(client, monkeypatch, sources, on_request=change_source))
    assert any("已修改" in warning for warning in draft["warnings"])
    assert draft["params"]["source_notes"][0]["version"] == sources[0]["version"]


@pytest.mark.parametrize("scope", [[], ["one"], ["same", "same"], [str(i) for i in range(31)]])
def test_invalid_source_selection_never_calls_model(client, monkeypatch, scope):
    calls = []
    monkeypatch.setattr(gateway, "TRANSPORT", httpx.MockTransport(lambda request: calls.append(request)))
    response = client.post("/api/ai/run", json={"kind": "outline", "scope_note_ids": scope})
    assert response.status_code == 422
    assert calls == []


def test_empty_source_and_input_limit_are_explicit_rejections_without_silent_truncation(client, monkeypatch):
    sources = notes(client, second_content="")
    response = client.post("/api/ai/run", json={"kind": "outline", "scope_note_ids": [source["id"] for source in sources]})
    assert response.status_code == 422 and response.json()["error"]["code"] == "EMPTY_INPUT"
    changed = client.patch(f"/api/notes/{sources[1]['id']}", json={"base_version": 1, "content": "很长的正文" * 400})
    assert changed.status_code == 200
    assert client.patch("/api/settings", json={"max_input_chars": 1000}).status_code == 200
    calls = []
    monkeypatch.setattr(gateway, "TRANSPORT", httpx.MockTransport(lambda request: calls.append(request)))
    response = client.post("/api/ai/run", json={"kind": "outline", "scope_note_ids": [source["id"] for source in sources]})
    assert response.status_code == 422 and response.json()["error"]["code"] == "TOO_LONG"
    assert response.json()["error"]["chars"] > 1000 and calls == []


@pytest.mark.parametrize("bad_section", [
    {"heading": "x", "body": "y", "source_ids": []},
    {"heading": "x", "body": "y", "source_ids": ["N3"]},
    {"heading": "x", "body": "y", "source_ids": [3]},
    {"heading": "x", "body": "y", "source_ids": "N1"},
    {"heading": "x", "body": "y"},
    {"heading": "x", "body": [], "source_ids": ["N1"]},
    {"heading": "x", "body": " ", "source_ids": ["N1"]},
    "not a section",
])
def test_unusable_or_unselected_section_sources_are_errors_not_silent_redirects(client, monkeypatch, bad_section):
    result = events(run(client, monkeypatch, notes(client), {"title": "提纲", "sections": [bad_section]}))
    assert result[-1][0] == "error" and result[-1][1]["error"]["kind"] == "STRUCTURE"
    assert not any(kind == "done" for kind, _ in result)


def test_unused_selected_note_requires_visible_warning(client, monkeypatch):
    output = {"title": "提纲", "sections": [{"heading": "一节", "body": "内容", "source_ids": ["N1"]}]}
    draft = ready(run(client, monkeypatch, notes(client), output))
    assert any("1 篇所选笔记未被" in warning for warning in draft["warnings"])


def test_truncated_outline_is_failed_and_cannot_be_accepted(client, monkeypatch):
    result = events(run(client, monkeypatch, notes(client), finish="length"))
    assert result[-1][0] == "error" and result[-1][1]["error"]["kind"] == "TRUNCATED"
    draft_id = result[0][1]["draft_id"]
    assert client.get(f"/api/ai/drafts/{draft_id}").json()["status"] == "failed"
    assert client.post(f"/api/ai/drafts/{draft_id}/accept", json={}).status_code == 409


def test_parser_rejects_oversized_outline_instead_of_truncating():
    source = {"id": "test-id", "title": "来源", "version": 1}
    with pytest.raises(LLMError, match="200000"):
        _parse_outline({"title": "标题", "sections": [{"heading": "节", "body": "x" * 200_000, "source_ids": ["N1"]}]},
                       {"aliases": {"N1": source}, "sources": [source]})
