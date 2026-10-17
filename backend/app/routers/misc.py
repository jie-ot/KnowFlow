"""检索、离线 HTML 导出、健康检查。"""

from __future__ import annotations

import sqlite3
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .. import config
from ..db import get_db, get_settings, now_iso, rows_to_dicts
from ..errors import AppError
from ..logic.export import build_html
from ..logic.search import search
from ..logic.timeutil import today_str
from .plans import get_plan_row, plan_tasks

router = APIRouter(prefix="/api", tags=["其他"])


@router.get("/health")
def health() -> dict:
    from ..logic.ocr import available

    return {"ok": True, "version": config.APP_VERSION, "ocr": available()}


@router.get("/search")
def search_all(q: str = "", course_id: str | None = None, types: str = "note,material,card",
               limit: int = 30, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    wanted = {t for t in types.split(",") if t in ("note", "material", "card")}
    if len(q) > 100:
        raise AppError(422, "VALIDATION", "关键词太长", field="q")
    items = search(conn, q, course_id=course_id or None, types=wanted, limit=max(1, min(limit, 100)))
    return {"items": items, "total": len(items)}


class ExportIn(BaseModel):
    note_ids: list[str] = Field(min_length=1, max_length=200)
    plan_id: str | None = None
    title: str = Field(default="复习资料", min_length=1, max_length=100)


@router.post("/export/html")
def export_html(body: ExportIn, conn: sqlite3.Connection = Depends(get_db)) -> Response:
    """把选中的笔记（和可选的计划）导出为单个离线 HTML（同学 FR-07 / DC-05）。"""
    placeholders = ",".join("?" * len(body.note_ids))
    rows = rows_to_dicts(conn.execute(
        f"SELECT * FROM notes WHERE id IN ({placeholders}) AND deleted_at IS NULL", body.note_ids
    ).fetchall(), ("tags",))
    if len(rows) != len(set(body.note_ids)):
        raise AppError(422, "INVALID_SELECTION", "选中的笔记有的已被删除，请刷新后重选")
    order = {nid: i for i, nid in enumerate(body.note_ids)}
    rows.sort(key=lambda r: order[r["id"]])
    courses = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM courses")}
    cards_by_note: dict[str, list[dict[str, Any]]] = {}
    for card in conn.execute(
        f"SELECT * FROM cards WHERE deleted_at IS NULL AND source_note_id IN ({placeholders}) ORDER BY created_at",
        body.note_ids,
    ):
        cards_by_note.setdefault(card["source_note_id"], []).append(dict(card))
    plan, tasks = None, []
    if body.plan_id:
        plan = get_plan_row(conn, body.plan_id)
        tasks = plan_tasks(conn, plan["id"], today_str(int(get_settings(conn)["day_start_hour"])))
    html_text = build_html(title=body.title.strip(), exported_at=now_iso(), courses=courses, notes=rows,
                           cards_by_note=cards_by_note, plan=plan, tasks=tasks)
    filename = f"{body.title.strip()}.html".replace("/", "_")
    return Response(
        content=html_text.encode("utf-8"),
        media_type="text/html; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )
