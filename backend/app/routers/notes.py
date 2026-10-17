"""笔记：增删改查、回收站、修订历史、.md 导入导出（我的 FR-01/02/04/11，D6）。"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .. import config
from ..db import dumps, get_db, new_id, now_iso, rows_to_dicts
from ..errors import AppError, not_found
from ..logic.export import note_to_markdown, parse_front_matter
from ..logic.links import add_link, extract_links, remove_link, rename_links, snippet_around, title_index
from ..logic.text import plain_excerpt
from ..repo import get_course, get_note_row, note_detail, note_summary

router = APIRouter(prefix="/api", tags=["笔记"])

MAX_CONTENT = 200_000


class NoteIn(BaseModel):
    course_id: str
    title: str = Field(default="未命名笔记", max_length=100)
    content: str = Field(default="", max_length=MAX_CONTENT)
    tags: list[str] = Field(default_factory=list)


class NotePatch(BaseModel):
    base_version: int
    title: str | None = Field(default=None, max_length=100)
    content: str | None = Field(default=None, max_length=MAX_CONTENT)
    tags: list[str] | None = None
    course_id: str | None = None


def clean_tags(tags: list[str]) -> list[str]:
    seen: list[str] = []
    for tag in tags:
        t = tag.strip().lstrip("#").strip()
        if not t:
            continue
        if len(t) > 20:
            raise AppError(422, "VALIDATION", f"标签「{t[:20]}…」超过 20 个字", field="tags")
        if t not in seen:
            seen.append(t)
    if len(seen) > 20:
        raise AppError(422, "VALIDATION", "一篇笔记最多 20 个标签", field="tags")
    return seen


def save_revision(conn: sqlite3.Connection, note: dict[str, Any], reason: str, draft_id: str | None = None) -> None:
    conn.execute(
        "INSERT INTO note_revisions(id, note_id, title, content, reason, draft_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (new_id(), note["id"], note["title"], note["content"], reason, draft_id, now_iso()),
    )


@router.get("/notes")
def list_notes(course_id: str | None = None, tag: str | None = None,
               conn: sqlite3.Connection = Depends(get_db)) -> list[dict]:
    sql = "SELECT * FROM notes WHERE deleted_at IS NULL"
    params: list[Any] = []
    if course_id:
        sql += " AND course_id = ?"
        params.append(course_id)
    sql += " ORDER BY updated_at DESC"
    rows = rows_to_dicts(conn.execute(sql, params).fetchall(), ("tags",))
    if tag:
        rows = [r for r in rows if tag in (r["tags"] or [])]
    cards = dict(conn.execute(
        "SELECT source_note_id, COUNT(*) FROM cards WHERE deleted_at IS NULL GROUP BY source_note_id"
    ).fetchall())
    sources = dict(conn.execute("SELECT note_id, COUNT(*) FROM note_sources GROUP BY note_id").fetchall())
    return [note_summary(r, cards.get(r["id"], 0), sources.get(r["id"], 0)) for r in rows]


@router.get("/tags")
def list_tags(course_id: str | None = None, conn: sqlite3.Connection = Depends(get_db)) -> list[dict]:
    sql = "SELECT tags FROM notes WHERE deleted_at IS NULL"
    params: list[Any] = []
    if course_id:
        sql += " AND course_id = ?"
        params.append(course_id)
    counts: dict[str, int] = {}
    for row in rows_to_dicts(conn.execute(sql, params).fetchall(), ("tags",)):
        for t in row["tags"] or []:
            counts[t] = counts.get(t, 0) + 1
    return [{"tag": t, "count": c} for t, c in sorted(counts.items(), key=lambda x: (-x[1], x[0]))]


@router.post("/notes", status_code=201)
def create_note(body: NoteIn, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    get_course(conn, body.course_id)
    now = now_iso()
    note_id = new_id()
    title = body.title.strip() or "未命名笔记"
    with conn:
        conn.execute(
            "INSERT INTO notes(id, course_id, title, content, tags, version, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
            (note_id, body.course_id, title, body.content, dumps(clean_tags(body.tags)), now, now),
        )
    return note_detail(conn, get_note_row(conn, note_id))


@router.get("/notes/{note_id}")
def get_note(note_id: str, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    return note_detail(conn, get_note_row(conn, note_id))


@router.patch("/notes/{note_id}")
def update_note(note_id: str, body: NotePatch, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    note = get_note_row(conn, note_id)
    if note["version"] != body.base_version:
        raise AppError(409, "VERSION_CONFLICT", "这篇笔记已在别处被修改，请刷新后再编辑",
                       extra={"current_version": note["version"]})
    fields: dict[str, Any] = {}
    if body.title is not None:
        fields["title"] = body.title.strip() or "未命名笔记"
    if body.content is not None:
        fields["content"] = body.content
    if body.tags is not None:
        fields["tags"] = dumps(clean_tags(body.tags))
    if body.course_id is not None and body.course_id != note["course_id"]:
        get_course(conn, body.course_id)
        fields["course_id"] = body.course_id
    if not fields:
        return note_detail(conn, note)
    sets = ", ".join(f"{k} = ?" for k in fields)
    renamed = 0
    with conn:
        # 正文被清空时先存一份旧版本，误操作也能在“修订历史”里找回
        if "content" in fields and not fields["content"].strip() and (note["content"] or "").strip():
            save_revision(conn, note, "before_clear")
        conn.execute(
            f"UPDATE notes SET {sets}, version = version + 1, updated_at = ? WHERE id = ?",
            (*fields.values(), now_iso(), note_id),
        )
        if "course_id" in fields:  # 卡片跟随笔记换学科
            conn.execute("UPDATE cards SET course_id = ? WHERE source_note_id = ?", (fields["course_id"], note_id))
        if "title" in fields and fields["title"].strip().lower() != note["title"].strip().lower():
            renamed = _rename_backlinks(conn, note_id, note["title"], fields["title"])
    detail = note_detail(conn, get_note_row(conn, note_id))
    detail["renamed_links"] = renamed
    return detail


def _rename_backlinks(conn: sqlite3.Connection, note_id: str, old: str, new: str) -> int:
    """笔记改名后，把其他笔记里指向旧标题的 [[链接]] 一起改掉（不改它们的更新时间）。"""
    other = conn.execute(
        "SELECT id FROM notes WHERE deleted_at IS NULL AND lower(title) = lower(?) AND id != ?", (old, note_id)
    ).fetchone()
    if other:  # 还有别的笔记叫这个名字，链接仍然有效，不动
        return 0
    total = 0
    for row in conn.execute(
        "SELECT id, content FROM notes WHERE deleted_at IS NULL AND id != ? AND content LIKE ?",
        (note_id, "%[[%"),
    ).fetchall():
        content, count = rename_links(row["content"], old, new)
        if count:
            conn.execute("UPDATE notes SET content = ?, version = version + 1 WHERE id = ?", (content, row["id"]))
            total += count
    return total


@router.get("/notes/{note_id}/links")
def note_links(note_id: str, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    """出链（这篇笔记链接到谁）与反向链接（谁链接到这篇笔记）。"""
    note = get_note_row(conn, note_id)
    index = title_index(conn)
    outgoing = []
    for title in extract_links(note["content"]):
        target = index.get(title.lower())
        outgoing.append({"title": title, "note_id": target["id"] if target else None,
                         "course_id": target["course_id"] if target else None})
    backlinks = []
    key = note["title"].strip().lower()
    for row in conn.execute(
        "SELECT id, title, course_id, content, updated_at FROM notes "
        "WHERE deleted_at IS NULL AND id != ? AND content LIKE ? ORDER BY updated_at DESC",
        (note_id, "%[[%"),
    ):
        if any(t.lower() == key for t in extract_links(row["content"])):
            backlinks.append({"id": row["id"], "title": row["title"], "course_id": row["course_id"],
                              "snippet": snippet_around(row["content"], note["title"])})
    return {"outgoing": outgoing, "backlinks": backlinks}


class LinkIn(BaseModel):
    target_id: str


def _write_content(conn: sqlite3.Connection, note_id: str, content: str) -> None:
    conn.execute("UPDATE notes SET content = ?, version = version + 1, updated_at = ? WHERE id = ?",
                 (content, now_iso(), note_id))


@router.post("/notes/{note_id}/links")
def link_notes(note_id: str, body: LinkIn, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    """在知识图谱里手动连线：在源笔记末尾的“相关笔记：”里加上 [[目标标题]]。"""
    note = get_note_row(conn, note_id)
    target = get_note_row(conn, body.target_id)
    if note["id"] == target["id"]:
        raise AppError(422, "VALIDATION", "不能链接到自己")
    content, changed = add_link(note["content"], target["title"])
    if changed:
        with conn:
            _write_content(conn, note_id, content)
    return {"ok": True, "changed": changed}


@router.delete("/notes/{note_id}/links/{target_id}")
def unlink_notes(note_id: str, target_id: str, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    """删除连线：去掉源笔记里指向目标的 [[链接]]（正文里的改回普通文字）。"""
    note = get_note_row(conn, note_id)
    target = get_note_row(conn, target_id)
    content, removed = remove_link(note["content"], target["title"])
    if removed:
        with conn:
            _write_content(conn, note_id, content)
    return {"ok": True, "removed": removed}


@router.get("/graph")
def knowledge_graph(course_id: str | None = None, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    """知识关系图：笔记为节点，[[双链]] 为边（同学 B-FR-10）。"""
    sql = "SELECT id, title, course_id, content, updated_at FROM notes WHERE deleted_at IS NULL"
    params: list[Any] = []
    if course_id:
        sql += " AND course_id = ?"
        params.append(course_id)
    rows = conn.execute(sql, params).fetchall()
    index = title_index(conn)
    ids = {r["id"] for r in rows}
    cards = dict(conn.execute(
        "SELECT source_note_id, COUNT(*) FROM cards WHERE deleted_at IS NULL GROUP BY source_note_id"
    ).fetchall())
    edges: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    unresolved = 0
    for row in rows:
        for title in extract_links(row["content"]):
            target = index.get(title.lower())
            if not target:
                unresolved += 1
                continue
            if target["id"] == row["id"] or target["id"] not in ids:
                continue
            pair = (row["id"], target["id"])
            if pair in seen:
                continue
            seen.add(pair)
            edges.append({"source": row["id"], "target": target["id"]})
    degree: dict[str, int] = {}
    for e in edges:
        degree[e["source"]] = degree.get(e["source"], 0) + 1
        degree[e["target"]] = degree.get(e["target"], 0) + 1
    nodes = [{"id": r["id"], "title": r["title"], "course_id": r["course_id"], "degree": degree.get(r["id"], 0),
              "card_count": cards.get(r["id"], 0), "excerpt": plain_excerpt(r["content"], 80)} for r in rows]
    return {"nodes": nodes, "edges": edges, "unresolved": unresolved}


@router.get("/notes/{note_id}/delete-preview")
def delete_preview(note_id: str, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    get_note_row(conn, note_id)
    count = conn.execute(
        "SELECT COUNT(*) FROM cards WHERE source_note_id = ? AND deleted_at IS NULL", (note_id,)
    ).fetchone()[0]
    return {"card_count": count, "retention_days": config.TRASH_RETENTION_DAYS}


@router.delete("/notes/{note_id}")
def delete_note(note_id: str, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    """移入回收站：笔记与由它生成的卡片同批删除，30 天内可一起恢复（EX-09、D6）。"""
    get_note_row(conn, note_id)
    batch, now = new_id(), now_iso()
    with conn:
        conn.execute("UPDATE notes SET deleted_at = ?, delete_batch_id = ? WHERE id = ?", (now, batch, note_id))
        cur = conn.execute(
            "UPDATE cards SET deleted_at = ?, delete_batch_id = ? WHERE source_note_id = ? AND deleted_at IS NULL",
            (now, batch, note_id),
        )
    return {"ok": True, "deleted_cards": cur.rowcount}


@router.post("/notes/{note_id}/restore")
def restore_note(note_id: str, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    note = get_note_row(conn, note_id, include_deleted=True)
    if not note["deleted_at"]:
        return note_detail(conn, note)
    with conn:
        conn.execute("UPDATE notes SET deleted_at = NULL, delete_batch_id = NULL, updated_at = ? WHERE id = ?",
                     (now_iso(), note_id))
        conn.execute("UPDATE cards SET deleted_at = NULL, delete_batch_id = NULL WHERE delete_batch_id = ?",
                     (note["delete_batch_id"],))
    return note_detail(conn, get_note_row(conn, note_id))


@router.delete("/notes/{note_id}/purge")
def purge_note(note_id: str, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    note = get_note_row(conn, note_id, include_deleted=True)
    if not note["deleted_at"]:
        raise AppError(409, "NOT_IN_TRASH", "只能彻底删除回收站中的笔记")
    purge_batch(conn, note_id, note["delete_batch_id"])
    return {"ok": True}


def purge_batch(conn: sqlite3.Connection, note_id: str, batch_id: str | None) -> None:
    with conn:
        if batch_id:
            ids = [r[0] for r in conn.execute("SELECT id FROM cards WHERE delete_batch_id = ?", (batch_id,))]
            conn.executemany("DELETE FROM reviews WHERE card_id = ?", [(i,) for i in ids])
            conn.execute("DELETE FROM cards WHERE delete_batch_id = ?", (batch_id,))
        conn.execute("DELETE FROM ai_drafts WHERE note_id = ?", (note_id,))
        conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))


def purge_expired(conn: sqlite3.Connection) -> int:
    cutoff = (datetime.now().astimezone() - timedelta(days=config.TRASH_RETENTION_DAYS)).isoformat(timespec="seconds")
    expired = conn.execute(
        "SELECT id, delete_batch_id FROM notes WHERE deleted_at IS NOT NULL AND deleted_at < ?", (cutoff,)
    ).fetchall()
    for row in expired:
        purge_batch(conn, row["id"], row["delete_batch_id"])
    return len(expired)


@router.get("/trash")
def list_trash(conn: sqlite3.Connection = Depends(get_db)) -> list[dict]:
    rows = rows_to_dicts(conn.execute(
        "SELECT * FROM notes WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC"
    ).fetchall(), ("tags",))
    now = datetime.now().astimezone()
    result = []
    for row in rows:
        deleted = datetime.fromisoformat(row["deleted_at"])
        days_left = max(0, config.TRASH_RETENTION_DAYS - (now - deleted).days)
        cards = conn.execute("SELECT COUNT(*) FROM cards WHERE delete_batch_id = ?",
                             (row["delete_batch_id"],)).fetchone()[0]
        result.append({**note_summary(row), "days_left": days_left, "card_count": cards})
    return result


@router.get("/notes/{note_id}/revisions")
def list_revisions(note_id: str, conn: sqlite3.Connection = Depends(get_db)) -> list[dict]:
    get_note_row(conn, note_id)
    rows = conn.execute(
        "SELECT id, title, reason, draft_id, created_at, length(content) AS length "
        "FROM note_revisions WHERE note_id = ? ORDER BY created_at DESC, rowid DESC",
        (note_id,),
    ).fetchall()
    return [dict(r) for r in rows]


@router.get("/notes/{note_id}/revisions/{revision_id}")
def get_revision(note_id: str, revision_id: str, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    row = conn.execute("SELECT * FROM note_revisions WHERE id = ? AND note_id = ?", (revision_id, note_id)).fetchone()
    if row is None:
        raise not_found("修订记录")
    return dict(row)


@router.post("/notes/{note_id}/revisions/{revision_id}/restore")
def restore_revision(note_id: str, revision_id: str, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    """撤销 AI 修改：先把当前内容存为一条修订，再用旧快照覆盖，所以撤销本身也能再撤销。"""
    note = get_note_row(conn, note_id)
    rev = conn.execute("SELECT * FROM note_revisions WHERE id = ? AND note_id = ?", (revision_id, note_id)).fetchone()
    if rev is None:
        raise not_found("修订记录")
    with conn:
        save_revision(conn, note, "before_restore")
        conn.execute(
            "UPDATE notes SET title = ?, content = ?, version = version + 1, updated_at = ? WHERE id = ?",
            (rev["title"], rev["content"], now_iso(), note_id),
        )
    return note_detail(conn, get_note_row(conn, note_id))


@router.get("/notes/{note_id}/export.md")
def export_markdown(note_id: str, conn: sqlite3.Connection = Depends(get_db)) -> Response:
    note = get_note_row(conn, note_id)
    course = get_course(conn, note["course_id"])
    text = note_to_markdown(note, course["name"])
    filename = (note["title"] or "笔记").replace("/", "_").replace("\\", "_") + ".md"
    return Response(
        content=text.encode("utf-8"),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@router.post("/notes/import")
async def import_markdown(course_id: str = Form(...), files: list[UploadFile] = File(...),
                          conn: sqlite3.Connection = Depends(get_db)) -> dict:
    """导入 .md 为笔记（我的 FR-04）。非 UTF-8 的文件拒绝并列出原因，其余照常导入（EX-07）。"""
    get_course(conn, course_id)
    if len(files) > 20:
        raise AppError(422, "TOO_MANY_FILES", "一次最多导入 20 个文件")
    created, failed = [], []
    for upload in files:
        name = upload.filename or "未命名.md"
        if not name.lower().endswith((".md", ".markdown", ".txt")):
            failed.append({"name": name, "reason": "只能导入 .md 或 .txt 文件"})
            continue
        raw = await upload.read()
        if len(raw) > config.MAX_FILE_BYTES:
            failed.append({"name": name, "reason": "文件超过 20 MB"})
            continue
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            failed.append({"name": name, "reason": "不是 UTF-8 编码，请转换后再导入"})
            continue
        meta, body = parse_front_matter(text.replace("\r\n", "\n"))
        title = (meta.get("title") or name.rsplit(".", 1)[0]).strip()[:100] or "未命名笔记"
        now, note_id = now_iso(), new_id()
        try:
            tags = clean_tags(meta.get("tags") or [])
        except AppError:
            tags = []
        with conn:
            conn.execute(
                "INSERT INTO notes(id, course_id, title, content, tags, version, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
                (note_id, course_id, title, body.strip()[:MAX_CONTENT], dumps(tags), now, now),
            )
        created.append({"id": note_id, "title": title})
    return {"created": created, "failed": failed}
