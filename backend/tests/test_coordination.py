"""Restoring a dataset must wait for streams and reject obsolete workers."""

import asyncio
import threading

import pytest

from app.errors import AppError
from app.services.coordination import (
    assert_generation, async_request_scope, current_generation, generation_scope, restore_scope,
)
from tests.test_core import client  # noqa: F401


def test_restore_drains_requests_and_blocks_new_requests():
    async def scenario():
        worker_entered = threading.Event()
        worker_release = threading.Event()
        restore_attempted = threading.Event()
        restore_entered = threading.Event()
        restore_release = threading.Event()
        newcomer_entered = threading.Event()

        async def active_stream():
            async with async_request_scope():
                worker_entered.set()
                await asyncio.to_thread(worker_release.wait, 5)

        def restore():
            restore_attempted.set()
            with restore_scope():
                restore_entered.set()
                assert restore_release.wait(5)

        async def newcomer():
            async with async_request_scope():
                newcomer_entered.set()

        active = asyncio.create_task(active_stream())
        assert await asyncio.to_thread(worker_entered.wait, 5)
        restoring = asyncio.create_task(asyncio.to_thread(restore))
        assert await asyncio.to_thread(restore_attempted.wait, 5)
        assert not restore_entered.is_set()
        worker_release.set()
        assert await asyncio.to_thread(restore_entered.wait, 5)
        arriving = asyncio.create_task(newcomer())
        await asyncio.sleep(0.05)
        assert not newcomer_entered.is_set()
        restore_release.set()
        await asyncio.wait_for(asyncio.gather(active, restoring, arriving), 5)
        assert newcomer_entered.is_set()

    asyncio.run(scenario())


def test_restore_generation_rejects_old_worker_even_after_failure():
    generation = current_generation()
    with pytest.raises(RuntimeError):
        with restore_scope():
            raise RuntimeError("simulated replacement failure")
    with pytest.raises(AppError) as error:
        assert_generation(generation)
    assert error.value.code == "RESTORE_INTERRUPTED"
    assert error.value.retryable
    # The gate reopens even on rollback.
    async def read():
        async with async_request_scope():
            assert_generation(current_generation())
    asyncio.run(read())


def test_detached_write_cannot_run_during_or_after_restore():
    old = current_generation()
    with generation_scope(old):
        assert_generation(old)
    with restore_scope():
        # A newly started detached worker cannot write into an installation.
        with pytest.raises(AppError):
            with generation_scope(current_generation()):
                pytest.fail("write lease acquired during installation")
    with pytest.raises(AppError):
        with generation_scope(old):
            pytest.fail("obsolete write lease acquired after restore")


def test_extraction_started_before_restore_cannot_replace_units(tmp_path, monkeypatch):
    from app import config, db
    from app.logic.extract import extract
    from app.routers import materials

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "MATERIALS_DIR", tmp_path / "materials")
    db.init_db()
    source = config.MATERIALS_DIR / "m.txt"
    source.write_text("old parsing result", encoding="utf-8")
    conn = db.connect()
    with conn:
        conn.execute("INSERT INTO courses(id,name,created_at,updated_at) VALUES('c','test','now','now')")
        conn.execute("INSERT INTO materials(id,course_id,original_name,display_title,format,ext,size_bytes,sha256,status,uploaded_at,updated_at) "
                     "VALUES('m','c','m.txt','m','text','.txt',18,'hash','uploaded','now','now')")
        conn.execute("INSERT INTO material_units(id,material_id,kind,ordinal,locator,label,text) "
                     "VALUES('restored-unit','m','paragraph',1,'1','1','restored data')")

    def finish_after_restore(path, fmt, ext):
        result = extract(path, fmt, ext)
        with restore_scope():
            pass  # Simulate an exclusive dataset installation during parsing.
        return result

    monkeypatch.setattr(materials, "extract", finish_after_restore)
    with pytest.raises(AppError) as error:
        materials.run_extraction("m")
    assert error.value.code == "RESTORE_INTERRUPTED"
    assert conn.execute("SELECT text FROM material_units WHERE material_id='m'").fetchall()[0][0] == "restored data"
    conn.close()


def test_old_browser_dataset_token_cannot_write_after_restore(client):
    note = client.get("/api/notes").json()[0]
    backup = client.get("/api/backup")
    old_token = backup.headers["x-knowflow-dataset"]
    restored = client.post("/api/restore", files={"file": ("backup.zip", backup.content, "application/zip")})
    assert restored.status_code == 200
    assert restored.headers["x-knowflow-dataset"] != old_token
    response = client.patch(f"/api/notes/{note['id']}", headers={"X-KnowFlow-Dataset": old_token},
                            json={"base_version": note["version"], "content": "obsolete window"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "RESTORE_INTERRUPTED"
    current = client.get(f"/api/notes/{note['id']}").json()
    assert current["content"] != "obsolete window"
    assert current["version"] == note["version"]
