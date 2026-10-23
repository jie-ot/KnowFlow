"""Material attempts survive interruption and failed replacement using temporary data."""

from __future__ import annotations

import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import BackgroundTasks
from fastapi.testclient import TestClient

from app.db import connect, new_id, now_iso
from app.errors import AppError
from app.logic.extract import ExtractResult, Unit
from app.routers import materials


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "material-attempts.sqlite")
    monkeypatch.setattr(config, "MATERIALS_DIR", tmp_path / "materials")
    config.MATERIALS_DIR.mkdir()
    from app.main import app
    with TestClient(app) as test_client:
        yield test_client


def interrupt_attempt(material_id):
    """Simulate the interrupted status recorded on service restart/restore."""
    conn = connect()
    try:
        with conn:
            conn.execute("UPDATE materials SET status='failed', error_code='interrupted', "
                         "error_message='test interruption' WHERE id=?", (material_id,))
    finally:
        conn.close()
def course_id(client):
    return client.get("/api/courses").json()[0]["id"]


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


def extracted(text="new text"):
    return ExtractResult("ready", [Unit("text", 1, "text:1", "new", text)])


def test_interrupted_running_worker_cannot_replace_preserved_units(client, monkeypatch):
    material_id = material(client, old_unit=True)
    started, finish = threading.Event(), threading.Event()

    def slow_extract(*_args):
        started.set()
        assert finish.wait(5)
        return extracted("obsolete output")

    monkeypatch.setattr(materials, "extract", slow_extract)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(materials.run_extraction, material_id)
        try:
            assert started.wait(5)
            interrupt_attempt(material_id)
        finally:
            finish.set()
        future.result(timeout=5)
    data = client.get(f"/api/materials/{material_id}").json()
    assert (data["status"], data["error_code"], data["parse_attempt"]) == ("failed", "interrupted", 0)
    assert [unit["text"] for unit in data["units"]] == ["old text"]


def test_superseded_queued_attempt_never_starts_parser(client, monkeypatch):
    material_id = material(client)
    interrupt_attempt(material_id)
    conn = connect()
    try:
        retry = materials.retry_extraction(material_id, BackgroundTasks(), conn)
        assert retry["parse_attempt"] == 1
    finally:
        conn.close()
    monkeypatch.setattr(materials, "extract", lambda *_args: pytest.fail("superseded parser must not run"))
    materials.run_extraction(material_id, 0)
    assert client.get(f"/api/materials/{material_id}").json()["status"] == "uploaded"


def test_retry_new_success_cannot_be_overwritten_by_old_worker_exception(client, monkeypatch):
    material_id = material(client, old_unit=True)
    started, finish = threading.Event(), threading.Event()
    calls = []

    def extracting(*_args):
        calls.append(1)
        if len(calls) == 1:
            started.set()
            assert finish.wait(5)
            raise RuntimeError("old parser failed late")
        return extracted("current result")

    monkeypatch.setattr(materials, "extract", extracting)
    with ThreadPoolExecutor(max_workers=1) as pool:
        old = pool.submit(materials.run_extraction, material_id, 0)
        try:
            assert started.wait(5)
            interrupt_attempt(material_id)
            response = client.post(f"/api/materials/{material_id}/retry")
            assert response.status_code == 200 and response.json()["parse_attempt"] == 1
        finally:
            finish.set()
        old.result(timeout=5)
    data = client.get(f"/api/materials/{material_id}").json()
    assert data["status"] == "ready" and data["error_code"] is None
    assert [unit["text"] for unit in data["units"]] == ["current result"]


def test_simultaneous_material_retries_queue_only_one_attempt(client):
    material_id = material(client, status="failed")
    barrier = threading.Barrier(2)

    def retry():
        conn = connect()
        try:
            barrier.wait(timeout=5)
            try:
                result = materials.retry_extraction(material_id, BackgroundTasks(), conn)
                return result["parse_attempt"]
            except AppError as exc:
                return exc.code
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _index: retry(), range(2)))
    assert set(results) == {1, "BUSY"}


@pytest.mark.parametrize("unexpected", [True, False])
def test_parser_failure_preserves_units_and_is_retryable(client, monkeypatch, unexpected):
    material_id = material(client, old_unit=True)

    def fail(*_args):
        if unexpected:
            raise RuntimeError("not exposed to user")
        return ExtractResult("failed", [], "bad_file", "文件解析失败")

    monkeypatch.setattr(materials, "extract", fail)
    materials.run_extraction(material_id)
    data = client.get(f"/api/materials/{material_id}").json()
    assert data["status"] == "failed" and data["unit_count"] == 1
    assert [unit["text"] for unit in data["units"]] == ["old text"]
    if unexpected:
        assert data["error_code"] == "unexpected" and "not exposed" not in data["error_message"]
    monkeypatch.setattr(materials, "extract", lambda *_args: extracted())
    assert client.post(f"/api/materials/{material_id}/retry").status_code == 200
    assert client.get(f"/api/materials/{material_id}").json()["status"] == "ready"


def test_missing_source_requires_reupload_without_losing_units(client):
    from app import config
    material_id = material(client, status="failed", old_unit=True)
    (config.MATERIALS_DIR / (material_id + ".txt")).unlink()
    response = client.post(f"/api/materials/{material_id}/retry")
    assert response.status_code == 409 and response.json()["error"]["code"] == "SOURCE_MISSING"
    assert client.get(f"/api/materials/{material_id}").json()["units"][0]["text"] == "old text"


def test_result_write_failure_rolls_back_units_and_marks_current_attempt_failed(client, monkeypatch):
    material_id = material(client, old_unit=True)
    conn = connect()
    try:
        with conn:
            conn.execute("CREATE TRIGGER fail_new_units BEFORE INSERT ON material_units "
                         "BEGIN SELECT RAISE(FAIL, 'simulated result write failure'); END")
    finally:
        conn.close()
    monkeypatch.setattr(materials, "extract", lambda *_args: extracted("new result"))
    materials.run_extraction(material_id)
    data = client.get(f"/api/materials/{material_id}").json()
    assert (data["status"], data["error_code"], data["unit_count"]) == ("failed", "unexpected", 1)
    assert [(unit["text"], unit["warnings"]) for unit in data["units"]] == [("old text", ["user_edited"])]
    assert "IntegrityError" in data["error_message"]
