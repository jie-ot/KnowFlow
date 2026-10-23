"""Real SQLite contention, rollback and lossless import regressions (temporary data only)."""

from __future__ import annotations

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app.db import connect, dumps, init_db, new_id, now_iso
from app.errors import AppError
from app.routers.ai import AcceptIn, _finish_draft, accept_draft, discard_draft
from app.routers.notes import NoteIn, NotePatch, create_note, update_note
from app.services.notes import MAX_CONTENT, restore_note_revision, save_revision


@pytest.fixture()
def database(tmp_path, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.sqlite")
    monkeypatch.setattr(config, "MATERIALS_DIR", tmp_path / "materials")
    init_db()
    conn = connect()
    course_id = new_id()
    with conn:
        conn.execute("INSERT INTO courses(id, name, created_at, updated_at) VALUES (?, 'test', ?, ?)",
                     (course_id, now_iso(), now_iso()))
    yield conn, course_id
    conn.close()


def make_note(database, title="Original", content="old content"):
    conn, course_id = database
    return create_note(NoteIn(course_id=course_id, title=title, content=content), conn)


def make_draft(database, kind, note):
    conn, course_id = database
    draft_id = new_id()
    result = None
    plan_id = None
    if kind == "extract":
        result = {"items": [{"front": "Question", "back": "Answer"}]}
    elif kind == "compose":
        result = {"title": "New composed note", "content": "composed", "sources": []}
    elif kind == "plan":
        plan_id = new_id()
        result = {"tasks": [{"title": "Learn", "kind": "learn", "due_date": None,
                              "estimated_minutes": None, "note_ids": []}]}
    with conn:
        if plan_id:
            conn.execute("INSERT INTO plans(id, goal, created_at, updated_at) VALUES (?, 'test', ?, ?)",
                         (plan_id, now_iso(), now_iso()))
        conn.execute(
            "INSERT INTO ai_drafts(id, kind, status, note_id, plan_id, course_id, base_version, output, result, created_at) "
            "VALUES (?, ?, 'ready', ?, ?, ?, ?, 'new content', ?, ?)",
            (draft_id, kind, note["id"], plan_id, course_id, note["version"], dumps(result), now_iso()),
        )
    return draft_id


def concurrent_calls(call, count=6):
    start = threading.Barrier(count)

    def worker():
        connection = connect()
        try:
            start.wait(timeout=5)
            try:
                return call(connection)
            except AppError as exc:
                return exc
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=count) as pool:
        return list(pool.map(lambda _: worker(), range(count)))


def test_concurrent_note_saves_have_one_winner_and_one_clear_revision(database):
    conn, _ = database
    note = make_note(database)
    outcomes = concurrent_calls(lambda c: update_note(note["id"], NotePatch(base_version=1, content=""), c))
    assert sum(isinstance(o, dict) for o in outcomes) == 1
    assert sum(isinstance(o, AppError) and o.code == "VERSION_CONFLICT" for o in outcomes) == 5
    saved = conn.execute("SELECT content, version FROM notes WHERE id = ?", (note["id"],)).fetchone()
    assert tuple(saved) == ("", 2)
    revisions = conn.execute("SELECT title, content FROM note_revisions WHERE note_id = ?", (note["id"],)).fetchall()
    assert [tuple(r) for r in revisions] == [("Original", "old content")]


@pytest.mark.parametrize("kind", ["organize", "summarize", "extract", "compose", "plan"])
def test_concurrent_draft_accept_creates_business_result_once(database, kind):
    conn, _ = database
    note = make_note(database)
    draft_id = make_draft(database, kind, note)
    outcomes = concurrent_calls(lambda c: accept_draft(draft_id, AcceptIn(), c))
    assert all(isinstance(o, dict) for o in outcomes)
    assert sum(not o.get("already") for o in outcomes) == 1
    assert conn.execute("SELECT status FROM ai_drafts WHERE id = ?", (draft_id,)).fetchone()[0] == "accepted"
    assert conn.execute("SELECT COUNT(*) FROM cards WHERE source_draft_id = ?", (draft_id,)).fetchone()[0] == (kind == "extract")
    assert conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == (2 if kind == "compose" else 1)
    assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == (kind == "plan")
    assert conn.execute("SELECT COUNT(*) FROM note_revisions").fetchone()[0] == (kind in ("organize", "summarize"))
    assert conn.execute("SELECT version FROM notes WHERE id = ?", (note["id"],)).fetchone()[0] == (2 if kind in ("organize", "summarize") else 1)


@pytest.mark.parametrize("kind", ["organize", "extract", "compose", "plan"])
def test_accept_failure_rolls_back_business_writes_and_allows_retry(database, kind):
    conn, _ = database
    note = make_note(database)
    draft_id = make_draft(database, kind, note)
    with conn:
        conn.execute("CREATE TRIGGER reject_accept BEFORE UPDATE OF status ON ai_drafts "
                     "WHEN NEW.status IN ('accepted', 'edited') BEGIN SELECT RAISE(ABORT, 'injected failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match="injected failure"):
        accept_draft(draft_id, AcceptIn(), conn)
    assert conn.execute("SELECT status FROM ai_drafts WHERE id = ?", (draft_id,)).fetchone()[0] == "ready"
    assert conn.execute("SELECT content, version FROM notes WHERE id = ?", (note["id"],)).fetchone()["version"] == 1
    assert conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 1
    for table in ("cards", "tasks", "note_revisions"):
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    with conn:
        conn.execute("DROP TRIGGER reject_accept")
    assert "already" not in accept_draft(draft_id, AcceptIn(), conn)


def test_ai_version_conflict_preserves_ready_draft_and_history(database):
    conn, _ = database
    note = make_note(database)
    draft_id = make_draft(database, "organize", note)
    update_note(note["id"], NotePatch(base_version=1, content="user edit"), conn)
    with pytest.raises(AppError) as error:
        accept_draft(draft_id, AcceptIn(), conn)
    assert error.value.code == "VERSION_CONFLICT"
    assert conn.execute("SELECT status FROM ai_drafts WHERE id = ?", (draft_id,)).fetchone()[0] == "ready"
    assert conn.execute("SELECT COUNT(*) FROM note_revisions").fetchone()[0] == 0
    assert conn.execute("SELECT content FROM notes WHERE id = ?", (note["id"],)).fetchone()[0] == "user edit"


def test_revision_restore_renames_alias_links_and_can_itself_be_undone(database):
    conn, _ = database
    note = make_note(database, "Old", "old body")
    linking = make_note(database, "Linking", "[[Old]] [[Old|alias]] `[[Old]]`")
    with conn:
        save_revision(conn, note, "snapshot")
    revision_id = conn.execute("SELECT id FROM note_revisions WHERE note_id = ?", (note["id"],)).fetchone()[0]
    update_note(note["id"], NotePatch(base_version=1, title="New", content="new body"), conn)
    restored = restore_note_revision(conn, note["id"], revision_id, 2)
    assert (restored["title"], restored["content"]) == ("Old", "old body")
    restored_links = conn.execute("SELECT content FROM notes WHERE id = ?", (linking["id"],)).fetchone()[0]
    assert restored_links == f"[[id:{note['id']}|Old]] [[id:{note['id']}|alias]] `[[Old]]`"
    undo_id = conn.execute("SELECT id FROM note_revisions WHERE reason = 'before_restore'").fetchone()[0]
    undone = restore_note_revision(conn, note["id"], undo_id, 3)
    assert (undone["title"], undone["content"]) == ("New", "new body")
    undone_links = conn.execute("SELECT content FROM notes WHERE id = ?", (linking["id"],)).fetchone()[0]
    assert undone_links == f"[[id:{note['id']}|New]] [[id:{note['id']}|alias]] `[[Old]]`"
    with pytest.raises(AppError) as error:
        restore_note_revision(conn, note["id"], revision_id, 3)
    assert error.value.code == "VERSION_CONFLICT"


def test_import_rejects_oversized_markdown_without_truncating_or_partial_note(database):
    conn, course_id = database
    from app.main import app
    with TestClient(app) as client:
        response = client.post("/api/notes/import", data={"course_id": course_id}, files=[
            ("files", ("too-large.md", b"a" * (MAX_CONTENT + 1), "text/markdown")),
            ("files", ("fits.md", b"b" * MAX_CONTENT, "text/markdown")),
        ])
    assert response.status_code == 200
    result = response.json()
    assert len(result["failed"]) == 1 and "200000" in result["failed"][0]["reason"]
    assert len(result["created"]) == 1
    content = conn.execute("SELECT content FROM notes WHERE id = ?", (result["created"][0]["id"],)).fetchone()[0]
    assert content == "b" * MAX_CONTENT
    assert conn.execute("SELECT COUNT(*) FROM notes WHERE title = 'too-large'").fetchone()[0] == 0


def test_late_generation_cannot_revive_discarded_or_accepted_draft(database):
    conn, _ = database
    note = make_note(database)
    draft_id = make_draft(database, "extract", note)
    discard_draft(draft_id, conn)
    assert _finish_draft(draft_id, status="ready", output="late result")["status"] == "discarded"
    another = make_draft(database, "extract", note)
    accept_draft(another, AcceptIn(), conn)
    assert _finish_draft(another, status="failed", error_kind="NETWORK")["status"] == "accepted"


def test_writeback_from_before_restore_is_rejected(database):
    from app.services.coordination import current_generation, restore_scope
    conn, _ = database
    note = make_note(database)
    draft_id = make_draft(database, "extract", note)
    generation = current_generation()
    with restore_scope():
        pass
    with pytest.raises(AppError) as error:
        _finish_draft(draft_id, generation=generation, status="ready", output="obsolete")
    assert error.value.code == "RESTORE_INTERRUPTED"
    assert conn.execute("SELECT output FROM ai_drafts WHERE id = ?", (draft_id,)).fetchone()[0] == "new content"
