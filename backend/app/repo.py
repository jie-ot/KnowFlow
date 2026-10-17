"""各路由共用的查询与序列化。"""

from __future__ import annotations

import sqlite3
from typing import Any

from .db import row_to_dict
from .errors import not_found
from .logic.sm2 import SM2State, ef_to_float, preview
from .logic.text import char_count, plain_excerpt


def get_course(conn: sqlite3.Connection, course_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM courses WHERE id = ?", (course_id,)).fetchone()
    if row is None:
        raise not_found("学科")
    return dict(row)


def get_note_row(conn: sqlite3.Connection, note_id: str, *, include_deleted: bool = False) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
    if row is None or (row["deleted_at"] and not include_deleted):
        raise not_found("笔记")
    return row_to_dict(row, ("tags",))  # type: ignore[return-value]


def note_summary(row: dict[str, Any], card_count: int = 0, source_count: int = 0) -> dict[str, Any]:
    return {
        "id": row["id"],
        "course_id": row["course_id"],
        "title": row["title"],
        "tags": row["tags"],
        "excerpt": plain_excerpt(row["content"], 110),
        "char_count": char_count(row["content"]),
        "card_count": card_count,
        "source_count": source_count,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "deleted_at": row.get("deleted_at"),
        "version": row["version"],
    }


def note_detail(conn: sqlite3.Connection, note: dict[str, Any]) -> dict[str, Any]:
    sources = []
    for s in conn.execute("SELECT * FROM note_sources WHERE note_id = ? ORDER BY ord", (note["id"],)):
        material = conn.execute("SELECT id FROM materials WHERE id = ?", (s["material_id"],)).fetchone()
        sources.append({
            "ord": s["ord"],
            "material_id": s["material_id"],
            "unit_id": s["unit_id"],
            "material_name": s["material_name"],
            "locator": s["locator"],
            "label": s["label"],
            "precision": s["precision"],
            "available": material is not None,
        })
    card_count = conn.execute(
        "SELECT COUNT(*) FROM cards WHERE source_note_id = ? AND deleted_at IS NULL", (note["id"],)
    ).fetchone()[0]
    revision_count = conn.execute(
        "SELECT COUNT(*) FROM note_revisions WHERE note_id = ?", (note["id"],)
    ).fetchone()[0]
    return {
        **note,
        "char_count": char_count(note["content"]),
        "card_count": card_count,
        "revision_count": revision_count,
        "sources": sources,
    }


def card_to_dict(row: sqlite3.Row | dict[str, Any], *, with_preview: bool = False) -> dict[str, Any]:
    data = dict(row)
    data["ef"] = ef_to_float(data["ef"])
    if with_preview:
        data["preview"] = preview(SM2State(n=row["n"], interval=row["interval"], ef=row["ef"]))
    return data


def get_card_row(conn: sqlite3.Connection, card_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM cards WHERE id = ? AND deleted_at IS NULL", (card_id,)).fetchone()
    if row is None:
        raise not_found("卡片")
    return row


def mask_key(key: str) -> str:
    if not key:
        return ""
    return "****" + key[-4:] if len(key) > 4 else "****"


def config_public(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    """模型配置的对外形式：永远不返回完整 Key。"""
    data = dict(row)
    key = data.pop("api_key", "") or ""
    data["api_key_masked"] = mask_key(key)
    data["has_key"] = bool(key)
    data["json_mode"] = bool(data["json_mode"])
    data["is_default"] = bool(data["is_default"])
    data["consented"] = bool(data.get("consented_base_url")) and data.get("consented_base_url") == data["base_url"]
    return data
