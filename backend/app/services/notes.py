"""Atomic note saves and reversible history restoration."""

from __future__ import annotations

import sqlite3
import json
from typing import Any

from ..db import new_id, now_iso
from ..errors import AppError, not_found
from ..logic.links import LinkResolver, pin_title_references, prune_relation_types, resolve_links, rewrite_target, stable_target
from ..repo import get_course, get_note_row, note_detail
from .transactions import atomic_write

MAX_CONTENT = 200_000


def validate_content(content: str) -> None:
    if len(content) > MAX_CONTENT:
        raise AppError(422, "CONTENT_TOO_LARGE", f"正文超过 {MAX_CONTENT} 字符限制，请先缩短或拆分笔记", field="content")


def save_revision(conn: sqlite3.Connection, note: dict[str, Any], reason: str,
                  draft_id: str | None = None) -> None:
    relation_types = {row["target_note_id"]: row["kind"] for row in conn.execute(
        "SELECT target_note_id, kind FROM note_relation_types WHERE source_note_id = ?", (note["id"],))}
    conn.execute(
        "INSERT INTO note_revisions(id, note_id, title, content, reason, draft_id, created_at, relation_types) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (new_id(), note["id"], note["title"], note["content"], reason, draft_id, now_iso(), json.dumps(relation_types)),
    )


def version_conflict(version: int) -> AppError:
    return AppError(409, "VERSION_CONFLICT", "这篇笔记已在别处被修改，本地草稿已保留，请比较后合并",
                    extra={"current_version": version})


def rename_backlinks(conn: sqlite3.Connection, note_id: str, old: str, new: str) -> int:
    """Rewrite only links bound to this identity, even when either title has duplicates."""
    if old.strip().lower() == new.strip().lower():
        return 0
    resolver = LinkResolver(conn)
    total = 0
    for row in conn.execute(
        "SELECT id, content FROM notes WHERE deleted_at IS NULL AND id != ? AND content LIKE ?",
        (note_id, "%[[%"),
    ).fetchall():
        content, count = row["content"], 0
        bound = resolver.resolve(old, row["id"], bind=False)
        if bound["note_id"] == note_id:
            content, changed = rewrite_target(content, old, note_id, new)
            count += changed
        # Stable IDs retain identity; update the generated old-title display label only.
        from ..logic.links import WIKILINK, _without_code, safe_label
        masked = _without_code(content)
        for match in reversed(list(WIKILINK.finditer(masked))):
            if match.group(1).strip().lower() != stable_target(note_id).lower():
                continue
            label = (match.group(2) or "")[1:].strip()
            if label and label.lower() != old.strip().lower():
                continue
            token = f"[[{stable_target(note_id)}|{safe_label(new)}]]"
            if content[match.start():match.end()] != token:
                content = content[:match.start()] + token + content[match.end():]
                count += 1
        if count:
            conn.execute("UPDATE notes SET content = ?, version = version + 1, updated_at = ? WHERE id = ?", (content, now_iso(), row["id"]))
            total += count
    return total


def update_note_fields(conn: sqlite3.Connection, note_id: str, base_version: int,
                       fields: dict[str, Any]) -> dict[str, Any]:
    if "content" in fields:
        validate_content(fields["content"])
    with atomic_write(conn):
        note = get_note_row(conn, note_id)
        if note["version"] != base_version:
            raise version_conflict(note["version"])
        if "course_id" in fields:
            get_course(conn, fields["course_id"])
        if not fields:
            return note_detail(conn, note)
        if "title" in fields and fields["title"].strip().lower() != note["title"].strip().lower():
            pin_title_references(conn, note["title"])
            pin_title_references(conn, fields["title"])
        sets = ", ".join(f"{key} = ?" for key in fields)
        updated = conn.execute(
            f"UPDATE notes SET {sets}, version = version + 1, updated_at = ? "
            "WHERE id = ? AND version = ? AND deleted_at IS NULL",
            (*fields.values(), now_iso(), note_id, base_version),
        )
        if updated.rowcount != 1:
            raise version_conflict(get_note_row(conn, note_id)["version"])
        if "content" in fields and not fields["content"].strip() and (note["content"] or "").strip():
            save_revision(conn, note, "before_clear")
        if "course_id" in fields:
            conn.execute("UPDATE cards SET course_id = ? WHERE source_note_id = ?", (fields["course_id"], note_id))
        renamed = 0
        if "title" in fields and fields["title"].strip().lower() != note["title"].strip().lower():
            renamed = rename_backlinks(conn, note_id, note["title"], fields["title"])
        resolve_links(conn, get_note_row(conn, note_id)["content"], note_id)
        if "content" in fields:
            prune_relation_types(conn, note_id, fields["content"])
        detail = note_detail(conn, get_note_row(conn, note_id))
        detail["renamed_links"] = renamed
        return detail


def restore_note_revision(conn: sqlite3.Connection, note_id: str, revision_id: str,
                          base_version: int | None = None) -> dict[str, Any]:
    with atomic_write(conn):
        note = get_note_row(conn, note_id)
        if base_version is not None and note["version"] != base_version:
            raise version_conflict(note["version"])
        revision = conn.execute(
            "SELECT * FROM note_revisions WHERE id = ? AND note_id = ?", (revision_id, note_id),
        ).fetchone()
        if revision is None:
            raise not_found("修订记录")
        pin_title_references(conn, note["title"])
        pin_title_references(conn, revision["title"])
        save_revision(conn, note, "before_restore")
        updated = conn.execute(
            "UPDATE notes SET title = ?, content = ?, version = version + 1, updated_at = ? "
            "WHERE id = ? AND version = ? AND deleted_at IS NULL",
            (revision["title"], revision["content"], now_iso(), note_id, note["version"]),
        )
        if updated.rowcount != 1:
            raise version_conflict(get_note_row(conn, note_id)["version"])
        renamed = rename_backlinks(conn, note_id, note["title"], revision["title"])
        resolve_links(conn, revision["content"], note_id)
        conn.execute("DELETE FROM note_relation_types WHERE source_note_id = ?", (note_id,))
        relation_types = json.loads(revision["relation_types"] or "{}")
        existing_ids = {row["id"] for row in conn.execute("SELECT id FROM notes")}
        conn.executemany("INSERT INTO note_relation_types(source_note_id, target_note_id, kind) VALUES (?, ?, ?)",
                         [(note_id, target, kind) for target, kind in relation_types.items()
                          if target in existing_ids and kind in {"related", "prerequisite", "contains", "applies"}])
        prune_relation_types(conn, note_id, revision["content"])
        detail = note_detail(conn, get_note_row(conn, note_id))
        detail["renamed_links"] = renamed
        return detail
