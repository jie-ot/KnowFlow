"""Shared plan queries and task presentation used by plans and AI acceptance."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..errors import not_found


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
