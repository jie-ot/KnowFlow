"""Concurrent scoring/focus submissions must apply business changes once."""

from concurrent.futures import ThreadPoolExecutor
import sqlite3
import threading

import pytest
from fastapi.testclient import TestClient

from app import config, db
from app.errors import AppError
from app.routers.cards import ReviewIn, submit_review
from app.routers.extras import FocusIn, record_focus


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "MATERIALS_DIR", tmp_path / "materials")
    db.init_db()
    conn = db.connect()
    with conn:
        conn.execute("INSERT INTO courses(id, name, created_at, updated_at) VALUES ('course', 'Course', 'now', 'now')")
        conn.execute("INSERT INTO cards(id, course_id, front, back, due, created_at, updated_at) "
                     "VALUES ('card', 'course', 'Q', 'A', '2026-01-01', 'now', 'now')")
        conn.execute("INSERT INTO plans(id, goal, created_at, updated_at) VALUES ('plan', 'Goal', 'now', 'now')")
        conn.execute("INSERT INTO tasks(id, plan_id, title, kind, created_at, updated_at) "
                     "VALUES ('task', 'plan', 'Task', 'study', 'now', 'now')")
    conn.close()


def _call(action, payload, target):
    conn = db.connect()
    try:
        return action(target, payload, conn)
    finally:
        conn.close()


def _scalar(sql):
    conn = db.connect()
    try:
        return conn.execute(sql).fetchone()[0]
    finally:
        conn.close()


def _parallel(functions):
    barrier = threading.Barrier(len(functions))
    def run(function):
        barrier.wait(timeout=5)
        try:
            return function()
        except AppError as error:
            return error
    with ThreadPoolExecutor(max_workers=len(functions)) as workers:
        return list(workers.map(run, functions))


def test_concurrent_review_retry_updates_state_and_history_once(isolated):
    request = ReviewIn(grade="good", operation_id="grade-1", expected_revision=0)
    results = _parallel([lambda: _call(submit_review, request, "card") for _ in range(6)])
    assert all(result == results[0] for result in results)
    assert results[0]["n"] == 1 and results[0]["review_revision"] == 1
    assert _scalar("SELECT COUNT(*) FROM reviews") == 1
    assert _scalar("SELECT COUNT(*) FROM operation_receipts") == 1


def test_two_windows_cannot_score_the_same_revision_twice(isolated):
    requests = [ReviewIn(grade="forgot", operation_id=f"window-{index}", expected_revision=0) for index in range(2)]
    results = _parallel([lambda request=request: _call(submit_review, request, "card") for request in requests])
    assert len([result for result in results if isinstance(result, dict)]) == 1
    assert [result.code for result in results if isinstance(result, AppError)] == ["REVIEW_CONFLICT"]
    assert _scalar("SELECT forgot_count FROM cards WHERE id = 'card'") == 1
    assert _scalar("SELECT COUNT(*) FROM reviews") == 1


def test_retry_returns_original_response_and_new_round_is_legal(isolated):
    first_request = ReviewIn(grade="good", operation_id="round-1", expected_revision=0)
    first = _call(submit_review, first_request, "card")
    second = _call(submit_review, ReviewIn(grade="good", operation_id="round-2", expected_revision=1), "card")
    assert second["n"] == 2 and second["review_revision"] == 2
    assert _call(submit_review, first_request, "card") == first
    assert _scalar("SELECT COUNT(*) FROM reviews") == 2


def test_operation_id_cannot_be_reused_for_a_different_grade(isolated):
    _call(submit_review, ReviewIn(grade="hard", operation_id="same", expected_revision=0), "card")
    with pytest.raises(AppError) as error:
        _call(submit_review, ReviewIn(grade="forgot", operation_id="same", expected_revision=0), "card")
    assert error.value.code == "IDEMPOTENCY_CONFLICT"
    assert _scalar("SELECT COUNT(*) FROM reviews") == 1
    assert _scalar("SELECT forgot_count FROM cards") == 0


def test_concurrent_focus_retry_accumulates_once_and_allows_a_new_session(isolated):
    request = FocusIn(session_id="session-1", minutes=25, started_at="2026-10-23T10:00:00", done=False)
    results = _parallel([lambda: _call(record_focus, request, "task") for _ in range(6)])
    assert all(result == {"actual_minutes": 25, "status": "in_progress"} for result in results)
    assert _scalar("SELECT COUNT(*) FROM focus_sessions") == 1
    second = _call(record_focus, FocusIn(session_id="session-2", minutes=10, started_at="later", done=True), "task")
    assert second == {"actual_minutes": 35, "status": "done"}
    assert _call(record_focus, request, "task") == results[0]
    assert _scalar("SELECT actual_minutes FROM tasks") == 35
    assert _scalar("SELECT COUNT(*) FROM focus_sessions") == 2


def test_focus_retry_requires_the_original_snapshot_and_legacy_start_is_stable(isolated):
    request = FocusIn(minutes=15, started_at="legacy-start")
    assert _call(record_focus, request, "task") == _call(record_focus, request, "task")
    assert _scalar("SELECT actual_minutes FROM tasks") == 15
    with pytest.raises(AppError) as error:
        _call(record_focus, FocusIn(minutes=20, started_at="legacy-start", done=True), "task")
    assert error.value.code == "IDEMPOTENCY_CONFLICT"
    assert _scalar("SELECT actual_minutes FROM tasks") == 15


@pytest.mark.parametrize("scope", ["review", "focus"])
def test_failed_receipt_insert_rolls_back_business_result_before_retry(isolated, scope):
    conn = db.connect()
    with conn:
        conn.execute("CREATE TRIGGER fail_receipt BEFORE INSERT ON operation_receipts "
                     "BEGIN SELECT RAISE(ABORT, 'simulated receipt disk failure'); END")
    conn.close()
    action, request, target = (
        (submit_review, ReviewIn(grade="good", operation_id="retry", expected_revision=0), "card") if scope == "review" else
        (record_focus, FocusIn(session_id="retry", minutes=25, started_at="start", done=True), "task")
    )
    with pytest.raises(sqlite3.IntegrityError):
        _call(action, request, target)
    assert _scalar("SELECT COUNT(*) FROM operation_receipts") == 0
    assert _scalar("SELECT COUNT(*) FROM reviews") == 0
    assert _scalar("SELECT COUNT(*) FROM focus_sessions") == 0
    assert _scalar("SELECT n FROM cards") == 0
    assert _scalar("SELECT actual_minutes FROM tasks") == 0
    assert _scalar("SELECT status FROM tasks") == "todo"
    conn = db.connect()
    with conn:
        conn.execute("DROP TRIGGER fail_receipt")
    conn.close()
    _call(action, request, target)
    _call(action, request, target)
    assert _scalar("SELECT COUNT(*) FROM operation_receipts") == 1
    assert _scalar("SELECT COUNT(*) FROM " + ("reviews" if scope == "review" else "focus_sessions")) == 1


def test_http_contract_retries_validation_and_new_revision(isolated):
    from app.main import app
    with TestClient(app) as client:
        request = {"grade": "good", "operation_id": "http-grade", "expected_revision": 0}
        first = client.post("/api/review/card", json=request)
        assert first.status_code == 200 and first.json()["review_revision"] == 1
        retry = client.post("/api/review/card", json=request)
        assert retry.json() == first.json()
        stale = client.post("/api/review/card", json={**request, "operation_id": "other-window"})
        assert stale.status_code == 409 and stale.json()["error"]["code"] == "REVIEW_CONFLICT"
        assert client.post("/api/review/card", json={**request, "operation_id": "new-round", "expected_revision": 1}).status_code == 200
        focus = {"session_id": "http-focus", "minutes": 25, "started_at": "2026-10-23T10:00:00Z", "done": True}
        assert client.post("/api/tasks/task/focus", json=focus).json() == {"actual_minutes": 25, "status": "done"}
        assert client.post("/api/tasks/task/focus", json=focus).json() == {"actual_minutes": 25, "status": "done"}
        assert client.post("/api/tasks/task/focus", json={**focus, "minutes": 30}).status_code == 409
        assert client.post("/api/review/card", json={"grade": "bad", "expected_revision": -1}).status_code == 422
    assert _scalar("SELECT COUNT(*) FROM reviews") == 2
    assert _scalar("SELECT COUNT(*) FROM focus_sessions") == 1


def test_overnight_focus_records_exact_minutes_with_http_validation(isolated):
    from app.main import app
    with TestClient(app) as client:
        body = {"session_id": "overnight", "minutes": 601, "started_at": "2026-10-22T20:00:00Z"}
        assert client.post("/api/tasks/task/focus", json=body).json()["actual_minutes"] == 601
        assert client.post("/api/tasks/task/focus", json=body).json()["actual_minutes"] == 601
        assert client.post("/api/tasks/task/focus", json={**body, "session_id": "too-long", "minutes": 44_641}).status_code == 422
    assert _scalar("SELECT actual_minutes FROM tasks") == 601
