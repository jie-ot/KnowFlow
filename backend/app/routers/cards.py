"""知识点卡片、每日复习与学习统计（我的 FR-14–FR-18）。"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..db import get_db, get_settings, new_id, now_iso
from ..errors import AppError
from ..logic.sm2 import GRADE_Q, SM2State, schedule
from ..logic.timeutil import add_days, logical_day, now_local, today_str
from ..repo import card_to_dict, get_card_row, get_course, get_note_row
from ..services.idempotency import perform

router = APIRouter(prefix="/api", tags=["复习"])

WEAK_THRESHOLD = 3  # 遗忘 ≥ 3 次记为薄弱知识点（FR-18）


class CardIn(BaseModel):
    course_id: str
    front: str = Field(min_length=1, max_length=200)
    back: str = Field(min_length=1, max_length=2000)
    source_note_id: str | None = None


class CardPatch(BaseModel):
    front: str | None = Field(default=None, min_length=1, max_length=200)
    back: str | None = Field(default=None, min_length=1, max_length=2000)
    course_id: str | None = None


class ReviewIn(BaseModel):
    grade: Literal["forgot", "hard", "good"]
    operation_id: str | None = Field(default=None, min_length=1, max_length=120)
    expected_revision: int | None = Field(default=None, ge=0)


def _day_start(conn: sqlite3.Connection) -> int:
    return int(get_settings(conn)["day_start_hour"])


@router.get("/cards")
def list_cards(course_id: str | None = None, note_id: str | None = None, weak: bool = False,
               conn: sqlite3.Connection = Depends(get_db)) -> list[dict]:
    sql = "SELECT * FROM cards WHERE deleted_at IS NULL"
    params: list[Any] = []
    if course_id:
        sql += " AND course_id = ?"
        params.append(course_id)
    if note_id:
        sql += " AND source_note_id = ?"
        params.append(note_id)
    if weak:
        sql += " AND forgot_count >= ?"
        params.append(WEAK_THRESHOLD)
    sql += " ORDER BY due, created_at"
    return [card_to_dict(r) for r in conn.execute(sql, params)]


@router.post("/cards", status_code=201)
def create_card(body: CardIn, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    """手动建卡（FR-14）。新卡当天到期。"""
    get_course(conn, body.course_id)
    if body.source_note_id:
        get_note_row(conn, body.source_note_id)
    card_id, now = new_id(), now_iso()
    with conn:
        conn.execute(
            "INSERT INTO cards(id, course_id, source_note_id, front, back, due, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (card_id, body.course_id, body.source_note_id, body.front.strip(), body.back.strip(),
             today_str(_day_start(conn)), now, now),
        )
    return card_to_dict(get_card_row(conn, card_id))


@router.patch("/cards/{card_id}")
def update_card(card_id: str, body: CardPatch, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    get_card_row(conn, card_id)
    fields = {k: (v.strip() if isinstance(v, str) else v) for k, v in body.model_dump(exclude_unset=True).items()
              if v is not None}
    if "course_id" in fields:
        get_course(conn, fields["course_id"])
    if fields:
        sets = ", ".join(f"{k} = ?" for k in fields)
        with conn:
            conn.execute(f"UPDATE cards SET {sets}, updated_at = ? WHERE id = ?", (*fields.values(), now_iso(), card_id))
    return card_to_dict(get_card_row(conn, card_id))


@router.delete("/cards/{card_id}")
def delete_card(card_id: str, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    get_card_row(conn, card_id)
    with conn:
        conn.execute("UPDATE cards SET deleted_at = ?, delete_batch_id = ? WHERE id = ?", (now_iso(), new_id(), card_id))
    return {"ok": True}


@router.get("/review/queue")
def review_queue(course_id: str | None = None, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    """今日待复习：due ≤ 今天（凌晨 4 点分界），按到期日、创建时间排序（FR-16）。"""
    today = today_str(_day_start(conn))
    sql = ("SELECT k.*, c.name AS course_name, c.color AS course_color, n.title AS note_title "
           "FROM cards k JOIN courses c ON c.id = k.course_id "
           "LEFT JOIN notes n ON n.id = k.source_note_id AND n.deleted_at IS NULL "
           "WHERE k.deleted_at IS NULL AND k.due <= ?")
    params: list[Any] = [today]
    if course_id:
        sql += " AND k.course_id = ?"
        params.append(course_id)
    sql += " ORDER BY k.due, k.created_at"
    items = [card_to_dict(r, with_preview=True) for r in conn.execute(sql, params)]
    reviewed_today = conn.execute("SELECT COUNT(*) FROM reviews WHERE logical_day = ?", (today,)).fetchone()[0]
    next_due = conn.execute(
        "SELECT MIN(due) FROM cards WHERE deleted_at IS NULL AND due > ?" + (" AND course_id = ?" if course_id else ""),
        (today, course_id) if course_id else (today,),
    ).fetchone()[0]
    return {"today": today, "items": items, "reviewed_today": reviewed_today, "next_due": next_due}


@router.post("/review/{card_id}")
def submit_review(card_id: str, body: ReviewIn, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    """同一评分操作仅执行一次；之后的新一轮用新的操作编号及评分版本。"""
    def apply() -> dict:
        card = get_card_row(conn, card_id)
        if body.expected_revision is not None and card["review_revision"] != body.expected_revision:
            raise AppError(409, "REVIEW_CONFLICT", "这张卡片已在其他窗口评分，请更新复习队列",
                           extra={"current_revision": card["review_revision"]})
        moment = now_local()
        today = logical_day(moment, _day_start(conn)).isoformat()
        old = SM2State(n=card["n"], interval=card["interval"], ef=card["ef"])
        new = schedule(old, body.grade)
        due = add_days(today, new.interval)
        conn.execute(
            "UPDATE cards SET n = ?, interval = ?, ef = ?, due = ?, forgot_count = forgot_count + ?, updated_at = ?, "
            "review_revision = review_revision + 1 "
            "WHERE id = ?",
            (new.n, new.interval, new.ef, due, 1 if body.grade == "forgot" else 0, now_iso(), card_id),
        )
        conn.execute(
            "INSERT INTO reviews(id, card_id, course_id, grade, q, reviewed_at, logical_day, prev_interval, "
            "new_interval, prev_ef, new_ef) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (new_id(), card_id, card["course_id"], body.grade, GRADE_Q[body.grade],
             moment.isoformat(timespec="seconds"), today, old.interval, new.interval, old.ef, new.ef),
        )
        return card_to_dict(get_card_row(conn, card_id))

    return perform(conn, "review", body.operation_id,
                   {"card_id": card_id, "grade": body.grade, "expected_revision": body.expected_revision}, apply)


@router.get("/stats")
def stats(days: int = 30, course_id: str | None = None, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    """学习统计：每日复习数、正确率（记得 / 当日复习数）、各学科到期量、薄弱知识点（FR-17、FR-18）。"""
    if not 1 <= days <= 365:
        raise AppError(422, "VALIDATION", "days 需在 1–365 之间", field="days")
    today = date.fromisoformat(today_str(_day_start(conn)))
    start = (today - timedelta(days=days - 1)).isoformat()
    where = "logical_day >= ?" + (" AND course_id = ?" if course_id else "")
    params: list[Any] = [start] + ([course_id] if course_id else [])
    by_day = {
        r["logical_day"]: dict(r)
        for r in conn.execute(
            f"SELECT logical_day, COUNT(*) AS total, SUM(grade = 'good') AS good, SUM(grade = 'hard') AS hard, "
            f"SUM(grade = 'forgot') AS forgot FROM reviews WHERE {where} GROUP BY logical_day",
            params,
        )
    }
    series = []
    for i in range(days):
        d = (today - timedelta(days=days - 1 - i)).isoformat()
        row = by_day.get(d, {})
        total = row.get("total") or 0
        series.append({
            "day": d, "total": total, "good": row.get("good") or 0,
            "hard": row.get("hard") or 0, "forgot": row.get("forgot") or 0,
            "accuracy": round((row.get("good") or 0) / total, 3) if total else None,
        })
    streak = 0
    all_days = {r[0] for r in conn.execute("SELECT DISTINCT logical_day FROM reviews")}
    cursor = today if today.isoformat() in all_days else today - timedelta(days=1)
    while cursor.isoformat() in all_days:
        streak += 1
        cursor -= timedelta(days=1)
    week = [s for s in series[-7:] if s["total"]]
    week_total = sum(s["total"] for s in week)
    accuracy_7d = round(sum(s["good"] for s in week) / week_total, 3) if week_total else None
    due_by_course = [dict(r) for r in conn.execute(
        "SELECT c.id, c.name, c.color, COUNT(k.id) AS due FROM courses c "
        "LEFT JOIN cards k ON k.course_id = c.id AND k.deleted_at IS NULL AND k.due <= ? "
        "GROUP BY c.id ORDER BY c.sort, c.created_at", (today.isoformat(),)
    )]
    weak = [card_to_dict(r) for r in conn.execute(
        "SELECT * FROM cards WHERE deleted_at IS NULL AND forgot_count >= ?" + (" AND course_id = ?" if course_id else "")
        + " ORDER BY forgot_count DESC, updated_at DESC LIMIT 20",
        (WEAK_THRESHOLD, course_id) if course_id else (WEAK_THRESHOLD,),
    )]
    totals = conn.execute(
        "SELECT COUNT(*) AS cards, SUM(due <= ?) AS due FROM cards WHERE deleted_at IS NULL", (today.isoformat(),)
    ).fetchone()
    return {
        "today": today.isoformat(),
        "series": series,
        "streak": streak,
        "accuracy_7d": accuracy_7d,
        "reviewed_today": series[-1]["total"],
        "total_cards": totals["cards"] or 0,
        "due_today": totals["due"] or 0,
        "due_by_course": due_by_course,
        "weak_cards": weak,
    }
