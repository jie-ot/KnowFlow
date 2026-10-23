"""Weak-study actions survive retries, conflicts and missing source data."""

from concurrent.futures import ThreadPoolExecutor
import sqlite3
import threading

import pytest

from app import db
from app.errors import AppError
from app.services.learning import append_understanding, create_learning_task
from tests.test_core import client  # noqa: F401


def setup(client, *, manual=False):
    course = client.get("/api/courses").json()[0]["id"]
    note = client.post("/api/notes", json={"course_id": course, "title": "Source", "content": "Original"}).json()
    card = client.post("/api/cards", json={"course_id": course, "source_note_id": None if manual else note["id"],
                                          "front": "Question", "back": "Answer"}).json()
    return course, note, card


def scalar(sql, args=()):
    conn = db.connect()
    try:
        return conn.execute(sql, args).fetchone()[0]
    finally:
        conn.close()


def parallel(action):
    barrier = threading.Barrier(5)
    def invoke(_):
        conn = db.connect()
        try:
            barrier.wait(timeout=5)
            return action(conn)
        finally:
            conn.close()
    with ThreadPoolExecutor(max_workers=5) as executor:
        return list(executor.map(invoke, range(5)))


def test_understanding_concurrent_retry_appends_and_revises_once_and_can_undo(client):
    _, note, card = setup(client)
    results = parallel(lambda conn: append_understanding(conn, card["id"], "same-understanding", 1, "My own explanation"))
    assert all(result == results[0] for result in results)
    assert results[0]["version"] == 2
    latest = client.get(f"/api/notes/{note['id']}").json()
    assert latest["content"].count("My own explanation") == 1
    revisions = client.get(f"/api/notes/{note['id']}/revisions").json()
    assert len(revisions) == 1 and revisions[0]["reason"] == "before_understanding"
    restored = client.post(f"/api/notes/{note['id']}/revisions/{revisions[0]['id']}/restore", json={"base_version": 2})
    assert restored.status_code == 200
    assert restored.json()["content"] == "Original"
    # The lost original response still returns its receipt after a later undo.
    response = client.post(f"/api/learning/cards/{card['id']}/understanding", json={
        "operation_id": "same-understanding", "base_version": 1, "text": "My own explanation"})
    assert response.json() == results[0]
    assert client.get(f"/api/notes/{note['id']}").json()["content"] == "Original"


def test_conflict_and_oversize_roll_back_history_and_receipts(client):
    _, note, card = setup(client)
    client.patch(f"/api/notes/{note['id']}", json={"base_version": 1, "content": "Changed elsewhere"})
    path = f"/api/learning/cards/{card['id']}/understanding"
    request = {"operation_id": "stale-understanding", "base_version": 1, "text": "Keep my draft"}
    response = client.post(path, json=request)
    assert response.status_code == 409 and response.json()["error"]["code"] == "VERSION_CONFLICT"
    assert scalar("SELECT COUNT(*) FROM note_revisions WHERE note_id = ?", (note["id"],)) == 0
    assert scalar("SELECT COUNT(*) FROM operation_receipts WHERE operation_id = ?", (request["operation_id"],)) == 0
    assert client.post(path, json={**request, "base_version": 2, "text": "x" * 200000}).status_code == 422
    assert client.get(f"/api/notes/{note['id']}").json()["content"] == "Changed elsewhere"
    assert client.post(path, json={**request, "base_version": 2}).status_code == 200


def test_competing_windows_cannot_append_to_the_same_source_version(client):
    _, note, card = setup(client)
    barrier = threading.Barrier(2)
    def append(index):
        conn = db.connect()
        try:
            barrier.wait(timeout=5)
            return append_understanding(conn, card["id"], f"study-window-{index}", 1, f"Explanation {index}")
        except AppError as error:
            return error.code
        finally:
            conn.close()
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(append, range(2)))
    assert sum(isinstance(result, dict) for result in results) == 1
    assert "VERSION_CONFLICT" in results
    assert scalar("SELECT version FROM notes WHERE id = ?", (note["id"],)) == 2
    assert scalar("SELECT COUNT(*) FROM note_revisions WHERE note_id = ?", (note["id"],)) == 1


def test_http_requires_receipt_id_and_distinguishes_retry_from_a_new_task(client):
    _, _, card = setup(client)
    path = f"/api/learning/cards/{card['id']}/tasks"
    request = {"operation_id": "task-retry-contract", "plan_goal": "Learn", "title": "Explain once"}
    assert client.post(path, json={"plan_goal": "Learn", "title": "No receipt"}).status_code == 422
    first = client.post(path, json=request)
    assert first.status_code == 201
    assert client.post(path, json=request).json() == first.json()
    conflict = client.post(path, json={**request, "title": "Changed retry"})
    assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    later = client.post(path, json={**request, "operation_id": "new-learning-round", "plan_id": first.json()["plan_id"], "plan_goal": None})
    assert later.status_code == 201 and later.json()["id"] != first.json()["id"]
    assert scalar("SELECT COUNT(*) FROM tasks WHERE source_card_id = ?", (card["id"],)) == 2


@pytest.mark.parametrize("kind", ["understanding", "tasks"])
def test_original_receipt_remains_retriable_after_source_and_card_are_trashed(client, kind):
    _, note, card = setup(client)
    path = f"/api/learning/cards/{card['id']}/{kind}"
    request = ({"operation_id": "deleted-understanding", "base_version": 1, "text": "Committed explanation"}
               if kind == "understanding" else
               {"operation_id": "deleted-task-receipt", "plan_goal": "Committed plan", "title": "Committed task"})
    first = client.post(path, json=request)
    assert first.status_code in (200, 201)
    client.delete(f"/api/notes/{note['id']}")
    assert client.get(f"/api/learning/cards/{card['id']}").status_code == 404
    retry = client.post(path, json=request)
    assert retry.status_code == first.status_code and retry.json() == first.json()
    # A new operation still respects deletion; receipt lookup cannot create new
    # business data for a card which has been removed in the meantime.
    assert client.post(path, json={**request, "operation_id": "new-after-deletion"}).status_code == 404
    assert scalar("SELECT COUNT(*) FROM operation_receipts WHERE operation_id = ?", (request["operation_id"],)) == 1


def test_task_concurrent_retry_creates_one_plan_and_task_and_progress_does_not_change_mastery(client):
    _, _, card = setup(client)
    fields = {"plan_id": None, "plan_goal": "Strengthen concepts", "title": "Explain with an example", "kind": "learn",
              "due_date": None, "estimated_minutes": 25}
    before = scalar("SELECT COUNT(*) FROM plans")
    results = parallel(lambda conn: create_learning_task(conn, card["id"], "same-learning-task", fields))
    assert len({result["id"] for result in results}) == 1
    assert scalar("SELECT COUNT(*) FROM plans") == before + 1
    task = results[0]
    assert scalar("SELECT COUNT(*) FROM tasks WHERE source_card_id = ?", (card["id"],)) == 1
    state = tuple(scalar(f"SELECT {key} FROM cards WHERE id = ?", (card["id"],)) for key in ("n", "interval", "forgot_count", "review_revision"))
    assert client.patch(f"/api/tasks/{task['id']}", json={"status": "done"}).status_code == 200
    assert tuple(scalar(f"SELECT {key} FROM cards WHERE id = ?", (card["id"],)) for key in ("n", "interval", "forgot_count", "review_revision")) == state
    detail = client.get(f"/api/learning/cards/{card['id']}").json()
    assert detail["tasks"][0]["status"] == "done" and detail["source_status"] == "available"


@pytest.mark.parametrize("source", ["manual", "deleted", "purged"])
def test_missing_note_still_allows_card_edit_and_task_creation(client, source):
    _, note, card = setup(client, manual=source == "manual")
    if source in ("deleted", "purged"):
        client.delete(f"/api/notes/{note['id']}")
        # Normal note deletion also trashes its cards. Simulate a legacy active
        # card whose source was removed, without changing that established rule.
        conn = db.connect()
        with conn:
            conn.execute("UPDATE cards SET deleted_at = NULL WHERE id = ?", (card["id"],))
        conn.close()
    if source == "purged":
        conn = db.connect()
        with conn:
            conn.execute("DELETE FROM notes WHERE id = ?", (note["id"],))
        conn.close()
    response = client.get(f"/api/learning/cards/{card['id']}")
    assert response.status_code == 200
    assert response.json()["source_status"] == ("missing" if source == "purged" else source)
    assert client.patch(f"/api/cards/{card['id']}", json={"front": "Clearer question"}).status_code == 200
    task = client.post(f"/api/learning/cards/{card['id']}/tasks", json={
        "operation_id": "missing-source-task", "plan_goal": "Study", "title": "Read again"})
    assert task.status_code == 201 and task.json()["note_ids"] == []
    understanding = client.post(f"/api/learning/cards/{card['id']}/understanding", json={
        "operation_id": "missing-source-understanding", "base_version": 1, "text": "Keep"})
    assert understanding.status_code in (404, 409)


def test_plan_course_archive_date_deadline_and_daily_budget_constraints(client):
    course, _, card = setup(client)
    other = next(c["id"] for c in client.get("/api/courses").json() if c["id"] != course)
    path = f"/api/learning/cards/{card['id']}/tasks"
    base = {"operation_id": "plan-constraint", "title": "Learn again", "estimated_minutes": 25}
    different = client.post("/api/plans", json={"goal": "Other", "course_id": other}).json()
    assert client.post(path, json={**base, "plan_id": different["id"]}).json()["error"]["code"] == "COURSE_MISMATCH"
    plan = client.post("/api/plans", json={"goal": "Same", "course_id": course, "deadline": "2099-01-02", "daily_minutes": 30}).json()
    request = {**base, "plan_id": plan["id"], "due_date": "2099-01-02"}
    assert client.post(path, json=request).status_code == 201
    assert client.post(path, json={**request, "operation_id": "over-budget"}).status_code == 422
    assert client.post(path, json={**base, "operation_id": "after-deadline", "plan_id": plan["id"], "due_date": "2099-01-03"}).status_code == 422
    assert client.post(path, json={**base, "operation_id": "before-today", "plan_id": plan["id"], "due_date": "2000-01-01"}).status_code == 422
    client.patch(f"/api/plans/{plan['id']}", json={"archived": True})
    assert client.post(path, json={**base, "operation_id": "archived-task", "plan_id": plan["id"]}).json()["error"]["code"] == "PLAN_ARCHIVED"
    assert client.post(path, json={**base, "operation_id": "ambiguous-plan", "plan_id": plan["id"], "plan_goal": "Both"}).status_code == 422


@pytest.mark.parametrize("kind", ["understanding", "task"])
def test_receipt_failure_rolls_back_all_business_changes(client, kind):
    _, note, card = setup(client)
    conn = db.connect()
    with conn:
        conn.execute("CREATE TRIGGER fail_study_receipt BEFORE INSERT ON operation_receipts BEGIN SELECT RAISE(ABORT, 'disk failure'); END")
    before = conn.execute("SELECT COUNT(*) FROM plans").fetchone()[0]
    try:
        with pytest.raises(sqlite3.IntegrityError):
            if kind == "understanding":
                append_understanding(conn, card["id"], "failed-understanding", 1, "Explanation")
            else:
                create_learning_task(conn, card["id"], "failed-task", {"plan_goal": "New", "title": "Read", "kind": "learn"})
        assert conn.execute("SELECT content FROM notes WHERE id = ?", (note["id"],)).fetchone()[0] == "Original"
        assert conn.execute("SELECT COUNT(*) FROM note_revisions WHERE note_id = ?", (note["id"],)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM tasks WHERE source_card_id = ?", (card["id"],)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM plans").fetchone()[0] == before
    finally:
        conn.close()
