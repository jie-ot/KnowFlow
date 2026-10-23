"""A weak-card study path; task progress never changes review mastery."""

from __future__ import annotations

import sqlite3
from typing import Any

from ..db import dumps, get_settings, new_id, now_iso
from ..errors import AppError
from ..logic.plan_rules import validate_tasks
from ..logic.timeutil import today_str
from ..repo import card_to_dict, get_card_row, get_note_row, note_detail
from .idempotency import perform
from .notes import save_revision, update_note_fields, validate_content
from .plans import get_plan_row, plan_tasks, task_out


def _today(conn: sqlite3.Connection) -> str:
    return today_str(int(get_settings(conn)["day_start_hour"]))


def card_learning(conn: sqlite3.Connection, card_id: str) -> dict[str, Any]:
    card = get_card_row(conn, card_id)
    note = None
    source_status = "manual"
    if card["source_note_id"]:
        row = conn.execute("SELECT * FROM notes WHERE id = ?", (card["source_note_id"],)).fetchone()
        source_status = "missing" if row is None else "deleted" if row["deleted_at"] else "available"
        if row is not None:
            detail = note_detail(conn, get_note_row(conn, row["id"], include_deleted=True))
            note = {key: detail[key] for key in ("id", "title", "version", "sources")}
            note["available"] = source_status == "available"
    tasks = [dict(row) for row in conn.execute(
        "SELECT t.*, p.goal AS plan_goal, p.archived AS plan_archived FROM tasks t "
        "JOIN plans p ON p.id = t.plan_id WHERE t.source_card_id = ? ORDER BY t.created_at DESC", (card_id,))]
    return {"card": card_to_dict(card), "source_status": source_status, "source_note": note,
            "reviews": [dict(row) for row in conn.execute(
                "SELECT grade, reviewed_at, logical_day FROM reviews WHERE card_id = ? "
                "ORDER BY reviewed_at DESC, id DESC LIMIT 10", (card_id,))],
            "tasks": [task_out(row, _today(conn)) for row in tasks]}


def append_understanding(conn: sqlite3.Connection, card_id: str, operation_id: str,
                         base_version: int, text: str) -> dict[str, Any]:
    def action() -> dict[str, Any]:
        card = get_card_row(conn, card_id)
        if not card["source_note_id"]:
            raise AppError(409, "SOURCE_UNAVAILABLE", "这张卡片没有来源笔记，可编辑卡片或创建学习任务")
        note = get_note_row(conn, card["source_note_id"])
        clean = text.strip()
        if not clean:
            raise AppError(422, "VALIDATION", "请填写补充理解", field="text")
        content = note["content"].rstrip() + "\n\n## 补充理解\n\n" + clean + "\n"
        validate_content(content)
        save_revision(conn, note, "before_understanding")
        updated = update_note_fields(conn, note["id"], base_version, {"content": content})
        return {"note_id": note["id"], "version": updated["version"]}
    return perform(conn, "learning-understanding", operation_id,
                   {"card_id": card_id, "base_version": base_version, "text": text}, action)


def create_learning_task(conn: sqlite3.Connection, card_id: str, operation_id: str,
                         fields: dict[str, Any]) -> dict[str, Any]:
    def action() -> dict[str, Any]:
        card = get_card_row(conn, card_id)
        plan_id, goal = fields.get("plan_id"), (fields.get("plan_goal") or "").strip()
        if bool(plan_id) == bool(goal):
            raise AppError(422, "VALIDATION", "请选择现有计划，或填写新计划名称", field="plan_id")
        now, today = now_iso(), _today(conn)
        if plan_id:
            plan = get_plan_row(conn, plan_id)
            if plan["archived"]:
                raise AppError(409, "PLAN_ARCHIVED", "该计划已归档，请选择进行中的计划")
            if plan["course_id"] and plan["course_id"] != card["course_id"]:
                raise AppError(422, "COURSE_MISMATCH", "计划和卡片的学科不一致，请选择同学科或通用计划")
        else:
            plan_id = new_id()
            plan = {"id": plan_id, "deadline": None, "daily_minutes": None}
        task = {"title": fields["title"].strip(), "kind": fields["kind"],
                "due_date": fields.get("due_date") or None, "estimated_minutes": fields.get("estimated_minutes")}
        problems = validate_tasks([task], today=today, deadline=plan["deadline"],
                                  daily_minutes=plan["daily_minutes"],
                                  existing=plan_tasks(conn, plan_id, today) if fields.get("plan_id") else [])
        if problems:
            raise AppError(422, "PLAN_CONSTRAINT", problems[0]["message"], extra={"problems": problems})
        if goal:
            conn.execute("INSERT INTO plans(id, course_id, goal, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                         (plan_id, card["course_id"], goal, now, now))
        source = conn.execute("SELECT id FROM notes WHERE id = ? AND deleted_at IS NULL",
                              (card["source_note_id"],)).fetchone()
        task_id = new_id()
        sort = conn.execute("SELECT COALESCE(MAX(sort), 0) + 1 FROM tasks WHERE plan_id = ?", (plan_id,)).fetchone()[0]
        conn.execute("INSERT INTO tasks(id, plan_id, title, kind, due_date, estimated_minutes, note_ids, sort, "
                     "created_at, updated_at, source_card_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (task_id, plan_id, task["title"], task["kind"], task["due_date"], task["estimated_minutes"],
                      dumps([source["id"]] if source else []), sort, now, now, card_id))
        conn.execute("UPDATE plans SET updated_at = ? WHERE id = ?", (now, plan_id))
        return task_out(conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone(), today)
    return perform(conn, "learning-task", operation_id, {"card_id": card_id, **fields}, action)
