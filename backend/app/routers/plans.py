"""学习计划与任务（同学 FR-06、DC-06、BR-04/05）。

计划由用户主导：AI 只生成草稿（见 ai.py），日期、进度和完成状态都由用户修改。
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..db import dumps, get_db, get_settings, new_id, now_iso
from ..errors import AppError, not_found
from ..logic.plan_rules import TASK_KINDS, validate_tasks
from ..logic.timeutil import parse_day, today_str
from ..repo import get_course

router = APIRouter(prefix="/api", tags=["计划"])

Kind = Literal["learn", "review", "practice", "read"]
Status = Literal["todo", "in_progress", "done"]


class PlanIn(BaseModel):
    goal: str = Field(min_length=1, max_length=200)
    course_id: str | None = None
    progress_text: str = Field(default="", max_length=2000)
    deadline: str | None = None
    daily_minutes: int | None = Field(default=None, ge=1, le=1440)


class PlanPatch(BaseModel):
    goal: str | None = Field(default=None, min_length=1, max_length=200)
    course_id: str | None = None
    progress_text: str | None = Field(default=None, max_length=2000)
    deadline: str | None = None
    daily_minutes: int | None = Field(default=None, ge=1, le=1440)
    archived: bool | None = None


class TaskIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    kind: Kind = "learn"
    due_date: str | None = None
    estimated_minutes: int | None = Field(default=None, ge=1, le=1440)
    note_ids: list[str] = Field(default_factory=list)


class TaskPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    kind: Kind | None = None
    status: Status | None = None
    needs_review: bool | None = None
    due_date: str | None = None
    estimated_minutes: int | None = Field(default=None, ge=1, le=1440)
    note_ids: list[str] | None = None


def _today(conn: sqlite3.Connection) -> str:
    return today_str(int(get_settings(conn)["day_start_hour"]))


def _check_day(value: str | None, field: str) -> None:
    if value and parse_day(value) is None:
        raise AppError(422, "VALIDATION", "日期格式应为 YYYY-MM-DD", field=field)


def get_plan_row(conn: sqlite3.Connection, plan_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
    if row is None:
        raise not_found("计划")
    return dict(row)


def task_out(row: sqlite3.Row | dict[str, Any], today: str) -> dict[str, Any]:
    data = dict(row)
    data["note_ids"] = json.loads(data["note_ids"] or "[]")
    data["needs_review"] = bool(data["needs_review"])
    # 计算视图（不是另一种存储状态）：逾期 = 有日期、早于今天、未完成
    data["overdue"] = bool(data["due_date"] and data["due_date"] < today and data["status"] != "done")
    data["unscheduled"] = not data["due_date"]
    return data


def plan_tasks(conn: sqlite3.Connection, plan_id: str, today: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM tasks WHERE plan_id = ? ORDER BY due_date IS NULL, due_date, sort, created_at", (plan_id,)
    ).fetchall()
    return [task_out(r, today) for r in rows]


def plan_out(conn: sqlite3.Connection, plan: dict[str, Any], today: str, with_tasks: bool) -> dict[str, Any]:
    tasks = plan_tasks(conn, plan["id"], today)
    data = {
        **plan,
        "archived": bool(plan["archived"]),
        "task_total": len(tasks),
        "task_done": sum(1 for t in tasks if t["status"] == "done"),
        "task_overdue": sum(1 for t in tasks if t["overdue"]),
        "minutes_total": sum(t["estimated_minutes"] or 0 for t in tasks),
        "minutes_done": sum(t["estimated_minutes"] or 0 for t in tasks if t["status"] == "done"),
    }
    if plan.get("course_id"):
        course = conn.execute("SELECT name, color FROM courses WHERE id = ?", (plan["course_id"],)).fetchone()
        data["course_name"] = course["name"] if course else None
        data["course_color"] = course["color"] if course else None
    if with_tasks:
        data["tasks"] = tasks
        data["budget_warnings"] = validate_tasks(
            [], today=today, deadline=None, daily_minutes=plan.get("daily_minutes"),
            existing=[t for t in tasks if t["due_date"] and t["due_date"] >= today],
        )
    return data


@router.get("/plans")
def list_plans(conn: sqlite3.Connection = Depends(get_db)) -> list[dict]:
    today = _today(conn)
    rows = conn.execute("SELECT * FROM plans ORDER BY archived, updated_at DESC").fetchall()
    return [plan_out(conn, dict(r), today, with_tasks=False) for r in rows]


@router.post("/plans", status_code=201)
def create_plan(body: PlanIn, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    if body.course_id:
        get_course(conn, body.course_id)
    _check_day(body.deadline, "deadline")
    plan_id, now = new_id(), now_iso()
    with conn:
        conn.execute(
            "INSERT INTO plans(id, course_id, goal, progress_text, deadline, daily_minutes, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (plan_id, body.course_id, body.goal.strip(), body.progress_text.strip(), body.deadline,
             body.daily_minutes, now, now),
        )
    return plan_out(conn, get_plan_row(conn, plan_id), _today(conn), with_tasks=True)


@router.get("/plans/{plan_id}")
def get_plan(plan_id: str, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    return plan_out(conn, get_plan_row(conn, plan_id), _today(conn), with_tasks=True)


@router.patch("/plans/{plan_id}")
def update_plan(plan_id: str, body: PlanPatch, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    get_plan_row(conn, plan_id)
    fields = body.model_dump(exclude_unset=True)
    if fields.get("course_id"):
        get_course(conn, fields["course_id"])
    _check_day(fields.get("deadline"), "deadline")
    if "archived" in fields:
        fields["archived"] = 1 if fields["archived"] else 0
    for key in ("goal", "progress_text"):
        if isinstance(fields.get(key), str):
            fields[key] = fields[key].strip()
    if fields:
        sets = ", ".join(f"{k} = ?" for k in fields)
        with conn:
            conn.execute(f"UPDATE plans SET {sets}, version = version + 1, updated_at = ? WHERE id = ?",
                         (*fields.values(), now_iso(), plan_id))
    return plan_out(conn, get_plan_row(conn, plan_id), _today(conn), with_tasks=True)


@router.delete("/plans/{plan_id}")
def delete_plan(plan_id: str, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    get_plan_row(conn, plan_id)
    with conn:
        conn.execute("DELETE FROM ai_drafts WHERE plan_id = ?", (plan_id,))
        conn.execute("DELETE FROM plans WHERE id = ?", (plan_id,))
    return {"ok": True}


@router.post("/plans/{plan_id}/tasks", status_code=201)
def create_task(plan_id: str, body: TaskIn, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    get_plan_row(conn, plan_id)
    _check_day(body.due_date, "due_date")
    task_id, now = new_id(), now_iso()
    sort = conn.execute("SELECT COALESCE(MAX(sort), 0) + 1 FROM tasks WHERE plan_id = ?", (plan_id,)).fetchone()[0]
    with conn:
        conn.execute(
            "INSERT INTO tasks(id, plan_id, title, kind, due_date, estimated_minutes, note_ids, sort, created_at, "
            "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (task_id, plan_id, body.title.strip(), body.kind, body.due_date, body.estimated_minutes,
             dumps(body.note_ids), sort, now, now),
        )
        conn.execute("UPDATE plans SET updated_at = ? WHERE id = ?", (now, plan_id))
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return task_out(row, _today(conn))


@router.patch("/tasks/{task_id}")
def update_task(task_id: str, body: TaskPatch, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        raise not_found("任务")
    fields = body.model_dump(exclude_unset=True)
    _check_day(fields.get("due_date"), "due_date")
    now = now_iso()
    if "status" in fields:
        # 只有“已完成”记录完成时间；重新打开时清空（DC-06）
        fields["completed_at"] = now if fields["status"] == "done" else None
    if "needs_review" in fields:
        fields["needs_review"] = 1 if fields["needs_review"] else 0
    if "note_ids" in fields:
        fields["note_ids"] = dumps(fields["note_ids"] or [])
    if not fields:
        return task_out(row, _today(conn))
    with conn:
        if "due_date" in fields and fields["due_date"] != row["due_date"]:
            conn.execute("INSERT INTO task_reschedules(id, task_id, old_date, new_date, changed_at) VALUES (?, ?, ?, ?, ?)",
                         (new_id(), task_id, row["due_date"], fields["due_date"], now))
        sets = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(f"UPDATE tasks SET {sets}, version = version + 1, updated_at = ? WHERE id = ?",
                     (*fields.values(), now, task_id))
        conn.execute("UPDATE plans SET updated_at = ? WHERE id = ?", (now, row["plan_id"]))
    return task_out(conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone(), _today(conn))


@router.delete("/tasks/{task_id}")
def delete_task(task_id: str, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    with conn:
        cur = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    if cur.rowcount == 0:
        raise not_found("任务")
    return {"ok": True}


@router.get("/calendar")
def calendar(start: str, end: str, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    """月历：区间内有日期的任务、未排期任务、每天到期的复习卡片数。"""
    _check_day(start, "start")
    _check_day(end, "end")
    if end < start:
        raise AppError(422, "VALIDATION", "结束日期不能早于开始日期", field="end")
    today = _today(conn)
    base = ("SELECT t.*, p.goal AS plan_goal, p.course_id AS course_id, c.name AS course_name, "
            "c.color AS course_color FROM tasks t JOIN plans p ON p.id = t.plan_id "
            "LEFT JOIN courses c ON c.id = p.course_id WHERE p.archived = 0 ")

    def rows(sql: str, params: tuple) -> list[dict[str, Any]]:
        out = []
        for r in conn.execute(base + sql, params):
            item = task_out(r, today)
            item.update({"plan_goal": r["plan_goal"], "course_id": r["course_id"],
                         "course_name": r["course_name"], "course_color": r["course_color"]})
            out.append(item)
        return out

    scheduled = rows("AND t.due_date BETWEEN ? AND ? ORDER BY t.due_date, t.sort", (start, end))
    unscheduled = rows("AND t.due_date IS NULL AND t.status != 'done' ORDER BY t.sort", ())
    reviews: dict[str, int] = {}
    for r in conn.execute(
        "SELECT CASE WHEN due < ? THEN ? ELSE due END AS day, COUNT(*) AS n FROM cards "
        "WHERE deleted_at IS NULL AND due <= ? GROUP BY day", (today, today, end)
    ):
        if start <= r["day"] <= end:
            reviews[r["day"]] = r["n"]
    plans = [{"id": r["id"], "goal": r["goal"], "deadline": r["deadline"], "course_id": r["course_id"]}
             for r in conn.execute("SELECT * FROM plans WHERE archived = 0 ORDER BY updated_at DESC")]
    return {"today": today, "tasks": scheduled, "unscheduled": unscheduled, "reviews": reviews, "plans": plans}


@router.get("/tasks/today")
def today_tasks(conn: sqlite3.Connection = Depends(get_db)) -> dict:
    """首页用：今天到期和已逾期的未完成任务，以及今天完成的任务。"""
    today = _today(conn)
    rows = conn.execute(
        "SELECT t.*, p.goal AS plan_goal, c.name AS course_name, c.color AS course_color FROM tasks t "
        "JOIN plans p ON p.id = t.plan_id LEFT JOIN courses c ON c.id = p.course_id "
        "WHERE p.archived = 0 AND t.due_date IS NOT NULL AND ("
        "  (t.status != 'done' AND t.due_date <= ?) OR (t.status = 'done' AND t.due_date = ?)"
        ") ORDER BY t.status = 'done', t.due_date, t.sort",
        (today, today),
    ).fetchall()
    items = []
    for r in rows:
        item = task_out(r, today)
        item.update({"plan_goal": r["plan_goal"], "course_name": r["course_name"], "course_color": r["course_color"]})
        items.append(item)
    return {"today": today, "items": items, "kinds": TASK_KINDS}
