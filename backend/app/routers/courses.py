"""学科（课程）管理（我的 FR-02 / 同学 FR-02）。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from .. import config
from ..db import get_db, get_settings, new_id, now_iso
from ..errors import AppError
from ..logic.timeutil import today_str
from ..repo import get_course

router = APIRouter(prefix="/api/courses", tags=["学科"])


class CourseIn(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    color: str = "blue"
    description: str | None = Field(default=None, max_length=500)


class CoursePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=50)
    color: str | None = None
    description: str | None = Field(default=None, max_length=500)
    sort: int | None = None


def _check_color(color: str | None) -> None:
    if color is not None and color not in config.COURSE_COLORS:
        raise AppError(422, "VALIDATION", "不支持的颜色", field="color")


@router.get("")
def list_courses(conn: sqlite3.Connection = Depends(get_db)) -> list[dict]:
    today = today_str(int(get_settings(conn)["day_start_hour"]))
    rows = conn.execute(
        """
        SELECT c.*,
          (SELECT COUNT(*) FROM notes n WHERE n.course_id = c.id AND n.deleted_at IS NULL) AS note_count,
          (SELECT COUNT(*) FROM materials m WHERE m.course_id = c.id) AS material_count,
          (SELECT COUNT(*) FROM cards k WHERE k.course_id = c.id AND k.deleted_at IS NULL) AS card_count,
          (SELECT COUNT(*) FROM cards k WHERE k.course_id = c.id AND k.deleted_at IS NULL AND k.due <= ?) AS due_count,
          (SELECT COUNT(*) FROM plans p WHERE p.course_id = c.id) AS plan_count
        FROM courses c ORDER BY c.sort, c.created_at
        """,
        (today,),
    ).fetchall()
    return [dict(r) for r in rows]


@router.post("", status_code=201)
def create_course(body: CourseIn, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    _check_color(body.color)
    now = now_iso()
    course_id = new_id()
    sort = conn.execute("SELECT COALESCE(MAX(sort), 0) + 1 FROM courses").fetchone()[0]
    with conn:
        conn.execute(
            "INSERT INTO courses(id, name, color, description, sort, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (course_id, body.name.strip(), body.color, body.description, sort, now, now),
        )
    return get_course(conn, course_id)


@router.patch("/{course_id}")
def update_course(course_id: str, body: CoursePatch, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    get_course(conn, course_id)
    _check_color(body.color)
    fields = body.model_dump(exclude_unset=True)
    if "name" in fields and fields["name"] is not None:
        fields["name"] = fields["name"].strip()
    if fields:
        sets = ", ".join(f"{k} = ?" for k in fields)
        with conn:
            conn.execute(f"UPDATE courses SET {sets}, updated_at = ? WHERE id = ?",
                         (*fields.values(), now_iso(), course_id))
    return get_course(conn, course_id)


@router.delete("/{course_id}")
def delete_course(course_id: str, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    """永久删除学科及其全部内容（笔记、资料原件、卡片、计划）。前端会先展示数量并二次确认。"""
    get_course(conn, course_id)
    stored = [
        r["id"] + r["ext"]
        for r in conn.execute("SELECT id, ext FROM materials WHERE course_id = ?", (course_id,))
    ]
    with conn:
        note_ids = [r[0] for r in conn.execute("SELECT id FROM notes WHERE course_id = ?", (course_id,))]
        card_ids = [r[0] for r in conn.execute("SELECT id FROM cards WHERE course_id = ?", (course_id,))]
        conn.executemany("DELETE FROM reviews WHERE card_id = ?", [(c,) for c in card_ids])
        conn.execute("DELETE FROM cards WHERE course_id = ?", (course_id,))
        conn.executemany("DELETE FROM ai_drafts WHERE note_id = ?", [(n,) for n in note_ids])
        conn.execute("DELETE FROM notes WHERE course_id = ?", (course_id,))
        conn.execute("DELETE FROM materials WHERE course_id = ?", (course_id,))
        conn.execute("DELETE FROM plans WHERE course_id = ?", (course_id,))
        conn.execute("DELETE FROM courses WHERE id = ?", (course_id,))
    for name in stored:
        Path(config.MATERIALS_DIR / name).unlink(missing_ok=True)
    return {"ok": True}
