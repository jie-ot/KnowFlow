"""笔记：增删改查、回收站、修订历史、.md 导入导出（我的 FR-01/02/04/11，D6）。"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from typing import Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, Body, Depends, File, Form, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .. import config
from ..db import dumps, get_db, new_id, now_iso, rows_to_dicts
from ..errors import AppError, not_found
from ..logic.export import note_to_markdown, parse_front_matter
from ..logic.links import (LinkResolver, add_link, extract_links, pin_title_references, remove_link,
                           prune_relation_types, resolve_links, rewrite_target, snippet_around, stable_id, stable_target)
from ..logic.text import plain_excerpt
from ..repo import get_course, get_note_row, note_detail, note_summary
from ..services.notes import MAX_CONTENT, rename_backlinks as _rename_backlinks, restore_note_revision, save_revision, update_note_fields, validate_content
from ..services.transactions import atomic_write

router = APIRouter(prefix="/api", tags=["笔记"])

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


class RestoreRevisionIn(BaseModel):
    base_version: int


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


@router.get("/notes/index")
def note_index(conn: sqlite3.Connection = Depends(get_db)) -> list[dict]:
    """Lightweight global identities for wiki resolution and editor completion."""
    return [dict(row) for row in conn.execute(
        "SELECT id, title, course_id FROM notes WHERE deleted_at IS NULL ORDER BY title COLLATE NOCASE, id")]


@router.get("/notes/page")
def paged_notes(course_id: str | None = None, tag: str | None = None, q: str = Query(default="", max_length=100),
                offset: int = Query(default=0, ge=0), limit: int = Query(default=40, ge=1, le=200),
                conn: sqlite3.Connection = Depends(get_db)) -> dict:
    """Filter before pagination and compute summaries for only the requested page."""
    where, params = ["deleted_at IS NULL"], []
    if course_id:
        where.append("course_id = ?")
        params.append(course_id)
    if tag:
        where.append("EXISTS (SELECT 1 FROM json_each(notes.tags) WHERE json_each.value = ?)")
        params.append(tag)
    if q.strip():
        escaped = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        where.append("(title LIKE ? ESCAPE '\\' OR content LIKE ? ESCAPE '\\' OR tags LIKE ? ESCAPE '\\')")
        params.extend([f"%{escaped}%"] * 3)
    clause = " AND ".join(where)
    total = conn.execute(f"SELECT COUNT(*) FROM notes WHERE {clause}", params).fetchone()[0]
    rows = rows_to_dicts(conn.execute(
        f"SELECT * FROM notes WHERE {clause} ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?",
        [*params, limit, offset],
    ).fetchall(), ("tags",))
    ids = [row["id"] for row in rows]
    cards, sources = {}, {}
    if ids:
        placeholders = ",".join("?" * len(ids))
        cards = dict(conn.execute(
            f"SELECT source_note_id, COUNT(*) FROM cards WHERE deleted_at IS NULL AND source_note_id IN ({placeholders}) GROUP BY source_note_id", ids,
        ).fetchall())
        sources = dict(conn.execute(
            f"SELECT note_id, COUNT(*) FROM note_sources WHERE note_id IN ({placeholders}) GROUP BY note_id", ids,
        ).fetchall())
    return {"items": [note_summary(row, cards.get(row["id"], 0), sources.get(row["id"], 0)) for row in rows],
            "total": total, "offset": offset, "limit": limit, "has_more": offset + len(rows) < total}


@router.post("/notes", status_code=201)
def create_note(body: NoteIn, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    get_course(conn, body.course_id)
    now = now_iso()
    note_id = new_id()
    title = body.title.strip() or "未命名笔记"
    with atomic_write(conn):
        pin_title_references(conn, title)
        conn.execute(
            "INSERT INTO notes(id, course_id, title, content, tags, version, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
            (note_id, body.course_id, title, body.content, dumps(clean_tags(body.tags)), now, now),
        )
        resolve_links(conn, body.content, note_id)
    return note_detail(conn, get_note_row(conn, note_id))


@router.get("/notes/{note_id}")
def get_note(note_id: str, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    return note_detail(conn, get_note_row(conn, note_id))


@router.patch("/notes/{note_id}")
def update_note(note_id: str, body: NotePatch, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    fields: dict[str, Any] = {}
    if body.title is not None:
        fields["title"] = body.title.strip() or "未命名笔记"
    if body.content is not None:
        fields["content"] = body.content
    if body.tags is not None:
        fields["tags"] = dumps(clean_tags(body.tags))
    if body.course_id is not None:
        fields["course_id"] = body.course_id
    return update_note_fields(conn, note_id, body.base_version, fields)


@router.get("/notes/{note_id}/links")
def note_links(note_id: str, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    """出链（这篇笔记链接到谁）与反向链接（谁链接到这篇笔记）。"""
    with atomic_write(conn):
        note = get_note_row(conn, note_id)
        resolver = LinkResolver(conn)
        outgoing = resolve_links(conn, note["content"], note_id, resolver)
        backlinks = []
        for row in conn.execute(
            "SELECT id, title, course_id, content, updated_at FROM notes "
            "WHERE deleted_at IS NULL AND id != ? AND content LIKE ? ORDER BY updated_at DESC",
            (note_id, "%[[%"),
        ):
            bound = next((link for link in resolve_links(conn, row["content"], row["id"], resolver) if link["note_id"] == note_id), None)
            if bound:
                backlinks.append({"id": row["id"], "title": row["title"], "course_id": row["course_id"],
                                  "snippet": snippet_around(row["content"], bound["target"])})
    return {"outgoing": outgoing, "backlinks": backlinks}


class LinkIn(BaseModel):
    target_id: str
    kind: Literal["related", "prerequisite", "contains", "applies"] | None = None


class RelationIn(BaseModel):
    kind: Literal["related", "prerequisite", "contains", "applies"]


def _set_relation_type(conn: sqlite3.Connection, source_id: str, target_id: str, kind: str) -> None:
    conn.execute(
        "INSERT INTO note_relation_types(source_note_id, target_note_id, kind) VALUES (?, ?, ?) "
        "ON CONFLICT(source_note_id, target_note_id) DO UPDATE SET kind = excluded.kind",
        (source_id, target_id, kind),
    )


def _write_content(conn: sqlite3.Connection, note_id: str, content: str) -> None:
    validate_content(content)
    save_revision(conn, get_note_row(conn, note_id), "before_link_edit")
    conn.execute("UPDATE notes SET content = ?, version = version + 1, updated_at = ? WHERE id = ?",
                 (content, now_iso(), note_id))


@router.post("/notes/{note_id}/links")
def link_notes(note_id: str, body: LinkIn, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    """Explicit target IDs produce stable links and never resolve through a duplicate title."""
    with atomic_write(conn):
        note = get_note_row(conn, note_id)
        target = get_note_row(conn, body.target_id)
        if note["id"] == target["id"]:
            raise AppError(422, "VALIDATION", "不能链接到自己")
        existing = resolve_links(conn, note["content"], note_id)
        if any(link["note_id"] == target["id"] for link in existing):
            changed = False
        else:
            content, changed = add_link(note["content"], target["title"], target["id"])
        if changed:
            _write_content(conn, note_id, content)
        old_type = conn.execute("SELECT kind FROM note_relation_types WHERE source_note_id = ? AND target_note_id = ?", (note_id, target["id"])).fetchone()
        old_kind = old_type["kind"] if old_type else "related"
        kind_changed = body.kind is not None and body.kind != old_kind
        if kind_changed:
            if not changed:
                _write_content(conn, note_id, note["content"])
            _set_relation_type(conn, note_id, target["id"], body.kind)
    return {"ok": True, "changed": changed or kind_changed, "kind": body.kind or old_kind}


@router.patch("/notes/{note_id}/links/{target_id}")
def set_relation_type(note_id: str, target_id: str, body: RelationIn,
                      conn: sqlite3.Connection = Depends(get_db)) -> dict:
    with atomic_write(conn):
        note = get_note_row(conn, note_id)
        get_note_row(conn, target_id)
        if not any(link["note_id"] == target_id for link in resolve_links(conn, note["content"], note_id)):
            raise AppError(404, "LINK_NOT_FOUND", "这条方向的链接不存在，请重新读取图谱")
        old_type = conn.execute("SELECT kind FROM note_relation_types WHERE source_note_id = ? AND target_note_id = ?", (note_id, target_id)).fetchone()
        changed = (old_type["kind"] if old_type else "related") != body.kind
        if changed:
            _write_content(conn, note_id, note["content"])
            _set_relation_type(conn, note_id, target_id, body.kind)
    return {"ok": True, "changed": changed, "kind": body.kind}


class ResolveLinkIn(BaseModel):
    target: str = Field(min_length=1, max_length=100)
    target_id: str
    base_version: int


@router.post("/notes/{note_id}/links/resolve")
def resolve_note_link(note_id: str, body: ResolveLinkIn, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    with atomic_write(conn):
        note = get_note_row(conn, note_id)
        target = get_note_row(conn, body.target_id)
        if note_id == body.target_id:
            raise AppError(422, "VALIDATION", "不能链接到自己")
        content, changed = rewrite_target(note["content"], body.target, target["id"], target["title"])
        if not changed:
            raise AppError(409, "LINK_CHANGED", "这条链接已被修改，请重新读取笔记")
        return update_note_fields(conn, note_id, body.base_version, {"content": content})


@router.delete("/notes/{note_id}/links/{target_id}")
def unlink_notes(note_id: str, target_id: str, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    """删除连线：去掉源笔记里指向目标的 [[链接]]（正文里的改回普通文字）。"""
    with atomic_write(conn):
        note = get_note_row(conn, note_id)
        get_note_row(conn, target_id)
        content, removed = note["content"], 0
        for link in resolve_links(conn, content, note_id):
            if link["note_id"] == target_id:
                content, count = remove_link(content, link["target"])
                removed += count
        if removed:
            _write_content(conn, note_id, content)
            prune_relation_types(conn, note_id, content)
    return {"ok": True, "removed": removed}


@router.get("/graph")
def knowledge_graph(course_id: str | None = None, center_id: str | None = None,
                    depth: int = Query(default=1, ge=1, le=2), auto_local: bool = False,
                    conn: sqlite3.Connection = Depends(get_db)) -> dict:
    """知识关系图：笔记为节点，[[双链]] 为边（同学 B-FR-10）。"""
    sql = "SELECT id, title, course_id, content, updated_at FROM notes WHERE deleted_at IS NULL"
    params: list[Any] = []
    if course_id:
        sql += " AND course_id = ?"
        params.append(course_id)
    with atomic_write(conn):
        rows = conn.execute(sql, params).fetchall()
        resolver = LinkResolver(conn)
        ids = {r["id"] for r in rows}
        cards = dict(conn.execute(
            "SELECT source_note_id, COUNT(*) FROM cards WHERE deleted_at IS NULL GROUP BY source_note_id"
        ).fetchall())
        kinds = {(row["source_note_id"], row["target_note_id"]): row["kind"] for row in conn.execute("SELECT * FROM note_relation_types")}
        edges: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        unresolved, ambiguous = 0, 0
        for row in rows:
            for link in resolve_links(conn, row["content"], row["id"], resolver):
                target_id = link["note_id"]
                if not target_id:
                    unresolved += 1
                    ambiguous += link["status"] == "ambiguous"
                    continue
                if target_id == row["id"] or target_id not in ids:
                    continue
                pair = (row["id"], target_id)
                if pair in seen:
                    continue
                seen.add(pair)
                edges.append({"source": row["id"], "target": target_id, "kind": kinds.get(pair, "related")})
    total_nodes, total_edges = len(rows), len(edges)
    if auto_local and not center_id and total_nodes > 300:
        center_id = rows[0]["id"]
    if center_id:
        if center_id not in ids:
            raise AppError(404, "NOT_FOUND", "中心笔记不存在、已删除或不在当前学科中")
        adjacent: dict[str, set[str]] = {}
        for edge in edges:
            adjacent.setdefault(edge["source"], set()).add(edge["target"])
            adjacent.setdefault(edge["target"], set()).add(edge["source"])
        included, frontier = {center_id}, {center_id}
        for _ in range(depth):
            frontier = {neighbor for node in frontier for neighbor in adjacent.get(node, ())} - included
            included.update(frontier)
        rows = [row for row in rows if row["id"] in included]
        edges = [edge for edge in edges if edge["source"] in included and edge["target"] in included]
    degree: dict[str, int] = {}
    for e in edges:
        degree[e["source"]] = degree.get(e["source"], 0) + 1
        degree[e["target"]] = degree.get(e["target"], 0) + 1
    nodes = [{"id": r["id"], "title": r["title"], "course_id": r["course_id"], "degree": degree.get(r["id"], 0),
              "card_count": cards.get(r["id"], 0), "excerpt": plain_excerpt(r["content"], 80)} for r in rows]
    return {"nodes": nodes, "edges": edges, "unresolved": unresolved, "ambiguous": ambiguous,
            "center_id": center_id, "depth": depth if center_id else None,
            "total_nodes": total_nodes, "total_edges": total_edges}


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
    batch, now = new_id(), now_iso()
    with atomic_write(conn):
        note = get_note_row(conn, note_id)
        pin_title_references(conn, note["title"])
        conn.execute("UPDATE notes SET deleted_at = ?, delete_batch_id = ? WHERE id = ?", (now, batch, note_id))
        cur = conn.execute(
            "UPDATE cards SET deleted_at = ?, delete_batch_id = ? WHERE source_note_id = ? AND deleted_at IS NULL",
            (now, batch, note_id),
        )
    return {"ok": True, "deleted_cards": cur.rowcount}


@router.post("/notes/{note_id}/restore")
def restore_note(note_id: str, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    with atomic_write(conn):
        note = get_note_row(conn, note_id, include_deleted=True)
        if not note["deleted_at"]:
            return note_detail(conn, note)
        pin_title_references(conn, note["title"])
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
def restore_revision(note_id: str, revision_id: str, body: RestoreRevisionIn | None = Body(default=None),
                     conn: sqlite3.Connection = Depends(get_db)) -> dict:
    """撤销 AI 修改：先把当前内容存为一条修订，再用旧快照覆盖，所以撤销本身也能再撤销。"""
    return restore_note_revision(conn, note_id, revision_id, body.base_version if body else None)


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
        body = body.strip()
        if len(body) > MAX_CONTENT:
            failed.append({"name": name, "reason": f"正文有 {len(body)} 字符，超过 {MAX_CONTENT} 字符限制；请拆分文件后导入"})
            continue
        title = (meta.get("title") or name.rsplit(".", 1)[0]).strip()[:100] or "未命名笔记"
        now, note_id = now_iso(), new_id()
        try:
            tags = clean_tags(meta.get("tags") or [])
        except AppError:
            tags = []
        with atomic_write(conn):
            pin_title_references(conn, title)
            conn.execute(
                "INSERT INTO notes(id, course_id, title, content, tags, version, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
                (note_id, course_id, title, body, dumps(tags), now, now),
            )
            resolve_links(conn, body, note_id)
        created.append({"id": note_id, "title": title})
    return {"created": created, "failed": failed}
