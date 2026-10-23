"""Accept a draft and all of its business results exactly once."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from contextlib import nullcontext
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..db import connect, dumps, get_settings, new_id, now_iso, row_to_dict
from ..errors import AppError, not_found
from ..logic.links import pin_title_references, prune_relation_types, resolve_links, safe_label, stable_target
from ..logic.plan_rules import validate_tasks
from ..logic.timeutil import today_str
from ..repo import get_course, get_note_row, note_detail
from .coordination import generation_scope
from .notes import MAX_CONTENT, save_revision, version_conflict
from .plans import get_plan_row, plan_tasks, task_out

from .transactions import atomic_write


def draft_out(row: sqlite3.Row | dict[str, Any] | None) -> dict[str, Any]:
    data = row_to_dict(row, ("params", "result", "warnings", "usage"))  # type: ignore[arg-type]
    if data is None:
        raise not_found("AI 草稿")
    return data


def get_draft(conn: sqlite3.Connection, draft_id: str) -> dict[str, Any]:
    draft = draft_out(conn.execute("SELECT * FROM ai_drafts WHERE id = ?", (draft_id,)).fetchone())
    if draft["kind"] == "outline":
        warnings = list(draft.get("warnings") or [])
        for source in (draft.get("params") or {}).get("source_notes", []):
            row = conn.execute("SELECT title, version, deleted_at FROM notes WHERE id = ?", (source["id"],)).fetchone()
            if row is None or row["deleted_at"]:
                warning = f"来源「{source['title']}」已被删除；提纲仍依据生成时的内容，请核对后保存"
            elif row["version"] != source["version"]:
                warning = f"来源「{source['title']}」在生成后已修改；提纲仍依据版本 {source['version']}，请核对或重新生成"
            else:
                continue
            if warning not in warnings:
                warnings.append(warning)
        draft["warnings"] = warnings
    return draft


def start_draft(conn: sqlite3.Connection, *, kind: str, note_id: str | None, plan_id: str | None,
                 course_id: str | None, params: dict[str, Any], base_version: int | None,
                 config_id: str, model: str, input_chars: int) -> str:
    draft_id = new_id()
    with atomic_write(conn):
        conn.execute(
            "INSERT INTO ai_drafts(id, kind, status, note_id, plan_id, course_id, params, base_version, config_id, "
            "model, input_chars, created_at) VALUES (?, ?, 'running', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (draft_id, kind, note_id, plan_id, course_id, dumps(params), base_version, config_id, model,
             input_chars, now_iso()),
        )
    return draft_id


def accept_once(conn: sqlite3.Connection, draft_id: str,
                apply: Callable[[dict[str, Any]], tuple[dict[str, Any], bool, str | None]]) -> dict[str, Any]:
    with atomic_write(conn):
        # BEGIN IMMEDIATE precedes this read: a second process waits and then sees
        # accepted/edited, rather than applying the stale ready state again.
        draft = get_draft(conn, draft_id)
        if draft["status"] in ("accepted", "edited"):
            result: dict[str, Any] = {"draft": draft, "already": True}
            return result
        if draft["status"] != "ready":
            raise AppError(409, "DRAFT_NOT_READY", "这份草稿不能采纳（已放弃、失败或仍在生成）")
        payload, edited, note_id = apply(draft)
        changed = conn.execute(
            "UPDATE ai_drafts SET status = ?, note_id = COALESCE(?, note_id), resolved_at = ? "
            "WHERE id = ? AND status = 'ready'",
            ("edited" if edited else "accepted", note_id, now_iso(), draft_id),
        )
        if changed.rowcount != 1:
            raise AppError(409, "DRAFT_NOT_READY", "草稿状态已变化，请重试")
        return {"draft": get_draft(conn, draft_id), **payload}


class AcceptTaskIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    title: str = Field(min_length=1, max_length=200)
    kind: Literal["learn", "review", "practice", "read"]
    due_date: str | None = None
    estimated_minutes: int | None = Field(default=None, ge=1, le=1440)
    note_ids: list[str] = Field(default_factory=list, max_length=100)


class AcceptIn(BaseModel):
    content: str | None = Field(default=None, max_length=200_000)  # organize / summarize / compose
    title: str | None = Field(default=None, max_length=100)  # compose
    items: list[dict[str, str]] | None = Field(default=None, max_length=50)  # extract
    tasks: list[AcceptTaskIn] | None = Field(default=None, max_length=60)  # plan
    course_id: str | None = None  # compose：存到哪个学科（默认资料所在的学科）
    outline_sections: list["OutlineSectionIn"] | None = Field(default=None, min_length=1, max_length=40)


class OutlineSectionIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    heading: str = Field(min_length=1, max_length=100)
    body: str = Field(min_length=1, max_length=200_000)


def outline_content(sections: list[dict[str, Any]], sources: list[dict[str, Any]]) -> str:
    """Citations stay bound to selected note identities when headings/body are edited."""
    by_id = {source["id"]: source for source in sources}
    parts = []
    for section in sections:
        links = "、".join(f"[[{stable_target(note_id)}|{safe_label(by_id[note_id]['title'])}]]"
                         for note_id in section["source_note_ids"])
        parts.append(f"## {section['heading'].strip()}\n\n{section['body'].strip()}\n\n来源：{links}")
    return "\n\n".join(parts)


def finish_draft(draft_id: str, *, generation: int | None = None, **fields: Any) -> dict[str, Any]:
    with generation_scope(generation) if generation is not None else nullcontext():
        conn = connect()
        try:
            for key in ("result", "warnings", "usage"):
                if key in fields and not isinstance(fields[key], str) and fields[key] is not None:
                    fields[key] = dumps(fields[key])
            sets = ", ".join(f"{k} = ?" for k in fields)
            with conn:
                conn.execute(f"UPDATE ai_drafts SET {sets} WHERE id = ? AND status = 'running'",
                             (*fields.values(), draft_id))
            return get_draft(conn, draft_id)
        finally:
            conn.close()


def discard_draft(conn: sqlite3.Connection, draft_id: str) -> dict[str, Any]:
    get_draft(conn, draft_id)
    with atomic_write(conn):
        conn.execute("UPDATE ai_drafts SET status = 'discarded', resolved_at = ? "
                     "WHERE id = ? AND status IN ('ready', 'failed', 'running', 'interrupted')", (now_iso(), draft_id))
        return get_draft(conn, draft_id)


def accept_draft(conn: sqlite3.Connection, draft_id: str, body: AcceptIn) -> dict[str, Any]:
    return accept_once(conn, draft_id, lambda draft: _apply_draft(conn, draft_id, draft, body))


SUMMARY_PREFIX = "> 摘要："


def _apply_summary(content: str, summary: str) -> str:
    """把摘要插入正文最前面；已有摘要块时替换它，不重复堆叠。"""
    lines = content.split("\n")
    if lines and lines[0].startswith(SUMMARY_PREFIX):
        end = 0
        while end < len(lines) and lines[end].startswith(">"):
            end += 1
        while end < len(lines) and not lines[end].strip():
            end += 1
        lines = lines[end:]
    summary_line = SUMMARY_PREFIX + " ".join(summary.split())
    rest = "\n".join(lines).lstrip("\n")
    return summary_line + ("\n\n" + rest if rest else "\n")


def _apply_draft(conn: sqlite3.Connection, draft_id: str, draft: dict[str, Any],
                 body: AcceptIn) -> tuple[dict[str, Any], bool, str | None]:
    """Called only inside accept_once's transaction; never commits independently."""
    kind = draft["kind"]
    now = now_iso()
    payload: dict[str, Any] = {}

    if kind in ("organize", "summarize"):
        note = get_note_row(conn, draft["note_id"])
        if note["version"] != draft["base_version"]:
            raise AppError(409, "VERSION_CONFLICT", "生成期间这篇笔记被修改过，为避免覆盖，请重新生成")
        text = (body.content if body.content is not None else draft["output"] or "").strip()
        if not text:
            raise AppError(422, "VALIDATION", "内容不能为空", field="content")
        edited = text != (draft["output"] or "").strip()
        new_content = text if kind == "organize" else _apply_summary(note["content"], text)
        if len(new_content) > MAX_CONTENT:
            raise AppError(422, "VALIDATION", f"采纳后的正文超过 {MAX_CONTENT} 字符限制，请缩短后重试", field="content")
        changed = conn.execute(
            "UPDATE notes SET content = ?, version = version + 1, updated_at = ? "
            "WHERE id = ? AND version = ? AND deleted_at IS NULL",
            (new_content, now, note["id"], draft["base_version"]),
        )
        if changed.rowcount != 1:
            raise version_conflict(get_note_row(conn, note["id"])["version"])
        save_revision(conn, note, "before_ai_organize" if kind == "organize" else "before_ai_summary", draft_id)
        resolve_links(conn, new_content, note["id"])
        prune_relation_types(conn, note["id"], new_content)
        payload["note"] = note_detail(conn, get_note_row(conn, note["id"]))

    elif kind == "extract":
        note = get_note_row(conn, draft["note_id"])
        items = body.items if body.items is not None else (draft["result"] or {}).get("items", [])
        clean = []
        for i, item in enumerate(items):
            front, back = str(item.get("front", "")).strip(), str(item.get("back", "")).strip()
            if not (1 <= len(front) <= 200 and 1 <= len(back) <= 2000):
                raise AppError(422, "VALIDATION", f"第 {i + 1} 张卡片的正面需 1–200 字、背面需 1–2000 字",
                               field=f"items.{i}")
            clean.append((front, back))
        if not clean:
            raise AppError(422, "VALIDATION", "至少保留一张卡片")
        edited = [{"front": f, "back": b} for f, b in clean] != (draft["result"] or {}).get("items")
        due = today_str(int(get_settings(conn)["day_start_hour"]))
        conn.executemany(
            "INSERT INTO cards(id, course_id, source_note_id, source_draft_id, front, back, due, created_at, "
            "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(new_id(), note["course_id"], note["id"], draft_id, f, b, due, now, now) for f, b in clean],
        )
        payload["created_cards"] = len(clean)

    elif kind in ("compose", "outline"):
        result = draft["result"] or {}
        title = (body.title if body.title is not None else result.get("title") or "资料整理笔记").strip()
        if not title or len(title) > 100:
            raise AppError(422, "VALIDATION", "标题需为 1–100 字", field="title")
        if kind == "outline":
            if body.content is not None:
                raise AppError(422, "VALIDATION", "请编辑提纲小节，来源链接将自动保留", field="content")
            sections = result.get("sections", [])
            sources = (draft.get("params") or {}).get("source_notes", [])
            if not sections or not sources:
                raise AppError(422, "VALIDATION", "提纲缺少小节或来源，请重新生成")
            if body.outline_sections is not None:
                if len(body.outline_sections) != len(sections):
                    raise AppError(422, "VALIDATION", "编辑时需保留全部提纲小节及来源", field="outline_sections")
                sections = [{**section, **edit.model_dump()} for section, edit in zip(sections, body.outline_sections)]
            if any(not section["heading"].strip() or not section["body"].strip() for section in sections):
                raise AppError(422, "VALIDATION", "小节标题和正文不能为空", field="outline_sections")
            allowed_ids = {source["id"] for source in sources}
            if any(not section.get("source_note_ids") or any(note_id not in allowed_ids for note_id in section["source_note_ids"])
                   for section in sections):
                raise AppError(422, "INVALID_SOURCE", "提纲包含不在所选笔记内的来源，请重新生成")
            content = outline_content(sections, sources)
        else:
            content = (body.content if body.content is not None else result.get("content", "")).strip()
        if not content:
            raise AppError(422, "VALIDATION", "内容不能为空", field="content")
        if len(content) > MAX_CONTENT:
            raise AppError(422, "VALIDATION", f"正文超过 {MAX_CONTENT} 字符限制，请缩短后重试", field="content")
        course_id = body.course_id or draft["course_id"]
        get_course(conn, course_id)
        note_id = new_id()
        edited = content != result.get("content", "").strip() or title != result.get("title")
        pin_title_references(conn, title)
        conn.execute(
            "INSERT INTO notes(id, course_id, title, content, tags, version, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, '[]', 1, ?, ?)",
            (note_id, course_id, title, content, now, now),
        )
        resolve_links(conn, content, note_id)
        conn.executemany(
            "INSERT INTO note_sources(id, note_id, ord, material_id, unit_id, material_name, locator, label, "
            "precision) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(new_id(), note_id, s["ord"], s["material_id"], s["unit_id"], s["material_name"], s["locator"],
              s["label"], s.get("precision", "unit")) for s in result.get("sources", [])] if kind == "compose" else [],
        )
        payload["note"] = note_detail(conn, get_note_row(conn, note_id))

    elif kind == "plan":
        plan = get_plan_row(conn, draft["plan_id"])
        today = today_str(int(get_settings(conn)["day_start_hour"]))
        tasks = [task.model_dump() for task in body.tasks] if body.tasks is not None else (draft["result"] or {}).get("tasks", [])
        try:
            tasks = [AcceptTaskIn.model_validate(task).model_dump() for task in tasks]
        except (ValidationError, TypeError):
            raise AppError(422, "VALIDATION", "任务草稿的字段或类型不正确，请编辑后重试", field="tasks") from None
        normalized = []
        for t in tasks:
            minutes = t.get("estimated_minutes")
            normalized.append({
                "title": str(t.get("title") or "").strip(),
                "kind": t.get("kind"),
                "due_date": t.get("due_date") or None,
                "estimated_minutes": int(minutes) if isinstance(minutes, (int, float)) and minutes else None,
                "note_ids": [n for n in t.get("note_ids") or [] if isinstance(n, str)],
            })
        if not normalized:
            raise AppError(422, "VALIDATION", "至少保留一个任务")
        existing = [t for t in plan_tasks(conn, plan["id"], today) if t["status"] != "done"]
        problems = validate_tasks(normalized, today=today, deadline=plan["deadline"],
                                  daily_minutes=plan["daily_minutes"], existing=existing)
        if problems:
            raise AppError(422, "PLAN_INVALID", "还有任务不满足计划的时间约束，请调整后再采纳",
                           extra={"problems": problems})
        edited = normalized != (draft["result"] or {}).get("tasks")
        start = conn.execute("SELECT COALESCE(MAX(sort), 0) FROM tasks WHERE plan_id = ?", (plan["id"],)).fetchone()[0]
        conn.executemany(
            "INSERT INTO tasks(id, plan_id, title, kind, due_date, estimated_minutes, note_ids, sort, created_at, "
            "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(new_id(), plan["id"], t["title"], t["kind"], t["due_date"], t["estimated_minutes"],
              dumps(t["note_ids"]), start + i + 1, now, now) for i, t in enumerate(normalized)],
        )
        conn.execute("UPDATE plans SET updated_at = ? WHERE id = ?", (now, plan["id"]))
        payload["created_tasks"] = len(normalized)
        payload["tasks"] = [task_out(r, today) for r in conn.execute(
            "SELECT * FROM tasks WHERE plan_id = ? ORDER BY sort DESC LIMIT ?", (plan["id"], len(normalized)))]

    else:
        raise AppError(422, "VALIDATION", "不支持采纳这种草稿")
    return payload, edited, payload.get("note", {}).get("id") if kind in ("compose", "outline") else None
