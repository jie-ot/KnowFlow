"""双链：在笔记里写 [[笔记标题]] 或 [[笔记标题|显示文字]]（需求 A-FR-05）。

新链接使用 [[id:笔记ID|显示文字]]。旧标题链接只在首次能唯一解析时绑定身份；
同名链接必须由用户明确选择，已经绑定的链接不会因为删除或重命名改指别的笔记。
代码块和行内代码里的 [[…]] 不算链接。
"""

from __future__ import annotations

import re
import sqlite3
import json
from typing import Any

WIKILINK = re.compile(r"\[\[([^\[\]\n|]{1,100})(\|[^\[\]\n]{0,100})?\]\]")
_CODE = re.compile(r"```[\s\S]*?```|`[^`\n]*`")
_ID = re.compile(r"^id:([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$", re.I)


def stable_id(target: str) -> str | None:
    match = _ID.fullmatch(target.strip())
    return match.group(1).lower() if match else None


def stable_target(note_id: str) -> str:
    return f"id:{note_id}"


def safe_label(label: str) -> str:
    return re.sub(r"[\[\]|\r\n]", " ", label).strip()


class LinkResolver:
    """One snapshot and a single binding lookup for a batch of notes, avoiding N+1 queries.

    Callers that persist first-time bindings must enclose the batch in atomic_write().
    """

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.notes = {row["id"]: dict(row) for row in conn.execute(
            "SELECT id, title, course_id, updated_at FROM notes WHERE deleted_at IS NULL")}
        self.titles: dict[str, list[dict[str, Any]]] = {}
        for note in self.notes.values():
            self.titles.setdefault(note["title"].strip().lower(), []).append(note)
        self.bindings = {(row["source_note_id"], row["target_key"]): dict(row) for row in conn.execute(
            "SELECT source_note_id, target_key, target_id, candidate_ids FROM legacy_link_bindings")}

    def resolve(self, target: str, source_id: str | None = None, *, bind: bool = True) -> dict[str, Any]:
        target = target.strip()
        key = target.lower()
        note_id = stable_id(target)
        candidates: list[dict[str, Any]] = []
        binding = None if note_id or not source_id else self.bindings.get((source_id, key))
        if not note_id:
            current = self.titles.get(key, [])
            if binding is None and source_id and bind and current:
                proposed_id = current[0]["id"] if len(current) == 1 else None
                self.conn.execute(
                    "INSERT OR IGNORE INTO legacy_link_bindings(source_note_id, target_key, target_id, candidate_ids) VALUES (?, ?, ?, ?)",
                    (source_id, key, proposed_id, json.dumps([n["id"] for n in current] if len(current) > 1 else [])),
                )
                binding = dict(self.conn.execute(
                    "SELECT source_note_id, target_key, target_id, candidate_ids FROM legacy_link_bindings WHERE source_note_id = ? AND target_key = ?",
                    (source_id, key),
                ).fetchone())
                self.bindings[(source_id, key)] = binding
            if binding:
                note_id = binding["target_id"]
                if note_id is None:
                    remembered = json.loads(binding["candidate_ids"] or "[]")
                    candidate_ids = dict.fromkeys([*remembered, *(n["id"] for n in current)])
                    candidates = [self.notes[item] for item in candidate_ids if item in self.notes]
            elif len(current) == 1:
                note_id = current[0]["id"]
            elif len(current) > 1:
                candidates = current
        note = self.notes.get(note_id or "")
        ambiguous = bool(not note_id and (candidates or binding))
        return {"target": target, "title": note["title"] if note else target,
                "note_id": note["id"] if note else None, "course_id": note["course_id"] if note else None,
                "bound_id": note_id,
                "status": "resolved" if note else "ambiguous" if ambiguous else "missing",
                "candidates": [{"id": n["id"], "title": n["title"], "course_id": n["course_id"]} for n in candidates]}


def resolve_links(conn: sqlite3.Connection, text: str, source_id: str | None = None,
                  resolver: LinkResolver | None = None) -> list[dict[str, Any]]:
    resolver = resolver or LinkResolver(conn)
    seen: set[str] = set()
    result = []
    for match in WIKILINK.finditer(_without_code(text)):
        target = match.group(1).strip()
        if not target or target.lower() in seen:
            continue
        seen.add(target.lower())
        resolved = resolver.resolve(target, source_id)
        resolved["label"] = (match.group(2) or "")[1:].strip() or resolved["title"]
        result.append(resolved)
    return result


def prune_relation_types(conn: sqlite3.Connection, source_id: str, text: str) -> None:
    """Remove metadata only when the body no longer points at that identity.

    A target in the recycle bin still has a bound identity and keeps its relation type.
    """
    typed = list(conn.execute("SELECT target_note_id FROM note_relation_types WHERE source_note_id = ?", (source_id,)))
    if not typed:
        return
    present = {link["bound_id"] for link in resolve_links(conn, text, source_id)}
    conn.executemany("DELETE FROM note_relation_types WHERE source_note_id = ? AND target_note_id = ?",
                     [(source_id, row["target_note_id"]) for row in typed if row["target_note_id"] not in present])


def pin_title_references(conn: sqlite3.Connection, title: str) -> None:
    """Pin existing title references before a title's set of candidates changes."""
    key = title.strip().lower()
    resolver = LinkResolver(conn)
    for row in conn.execute("SELECT id, content FROM notes WHERE deleted_at IS NULL AND content LIKE '%[[%'").fetchall():
        if any(target.lower() == key for target in extract_links(row["content"])):
            resolver.resolve(title, row["id"])


def rewrite_target(text: str, target: str, target_id: str, title: str) -> tuple[str, int]:
    """Convert a selected legacy target to an ID while preserving explicit display labels."""
    masked = _without_code(text)
    parts, last, count = [], 0, 0
    for match in WIKILINK.finditer(masked):
        if match.group(1).strip().lower() != target.strip().lower():
            continue
        label = (match.group(2) or "")[1:].strip() or title
        parts.append(text[last:match.start()])
        parts.append(f"[[{stable_target(target_id)}|{safe_label(label)}]]")
        last, count = match.end(), count + 1
    parts.append(text[last:])
    return "".join(parts), count


def _without_code(text: str) -> str:
    return _CODE.sub(lambda m: " " * len(m.group(0)), text or "")


def extract_links(text: str) -> list[str]:
    """按出现顺序返回链接到的标题（去重，保留原始大小写）。"""
    seen: dict[str, str] = {}
    for m in WIKILINK.finditer(_without_code(text)):
        title = m.group(1).strip()
        if title and title.lower() not in seen:
            seen[title.lower()] = title
    return list(seen.values())


def rename_links(text: str, old: str, new: str) -> tuple[str, int]:
    """把正文里指向 old 的链接改成指向 new，保留 |显示文字。返回 (新正文, 改动处数)。"""
    masked = _without_code(text)
    count = 0
    parts: list[str] = []
    last = 0
    for m in WIKILINK.finditer(masked):
        if m.group(1).strip().lower() != old.strip().lower():
            continue
        parts.append(text[last:m.start()])
        parts.append(f"[[{new}{m.group(2) or ''}]]")
        last = m.end()
        count += 1
    parts.append(text[last:])
    return "".join(parts), count


def map_links(text: str, fn) -> str:  # type: ignore[no-untyped-def]
    """把代码以外的每个 [[标题|文字]] 替换成 fn(标题, 文字) 的结果。"""
    out, last = [], 0
    for m in _CODE.finditer(text or ""):
        out.append(WIKILINK.sub(lambda w: fn(w.group(1).strip(), ((w.group(2) or "")[1:].strip() or w.group(1).strip())),
                                text[last:m.start()]))
        out.append(m.group(0))
        last = m.end()
    out.append(WIKILINK.sub(lambda w: fn(w.group(1).strip(), ((w.group(2) or "")[1:].strip() or w.group(1).strip())),
                            (text or "")[last:]))
    return "".join(out)


def title_index(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    """Unique lowercase title and stable ID → note. Ambiguous titles are deliberately absent."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    index = {}
    for row in conn.execute(
        "SELECT id, title, course_id, updated_at FROM notes WHERE deleted_at IS NULL ORDER BY updated_at"
    ):
        grouped.setdefault(row["title"].strip().lower(), []).append(dict(row))
        index[stable_target(row["id"])] = dict(row)
    index.update({key: notes[0] for key, notes in grouped.items() if len(notes) == 1})
    return index


def snippet_around(text: str, title: str, width: int = 40) -> str:
    """反向链接里展示“在哪里提到了它”：转成纯文字，截取链接前后各一小段。"""
    from .text import plain_excerpt

    key = title.strip().lower()
    mark = "\u2063"  # 不可见分隔符，用来标出链接位置

    def sub(m: re.Match) -> str:
        target = m.group(1).strip()
        label = (m.group(2) or "")[1:].strip() or target
        return f"{mark}{label}{mark}" if target.lower() == key else label

    flat = plain_excerpt(WIKILINK.sub(sub, _without_code(text)), 100_000).rstrip("…")
    pos = flat.find(mark)
    if pos == -1:
        return flat[: width * 2]
    close = flat.find(mark, pos + 1)
    start = max(0, pos - width)
    end = min(len(flat), (close if close != -1 else pos) + width)
    return ("…" if start else "") + flat[start:end].replace(mark, "") + ("…" if end < len(flat) else "")


RELATED = "相关笔记："


def add_link(text: str, title: str, target_id: str | None = None) -> tuple[str, bool]:
    """在正文末尾的“相关笔记：”一行里加上 [[title]]（没有这一行就新建）。已经链接过则不动。"""
    target = stable_target(target_id) if target_id else title
    if any(t.lower() == target.strip().lower() for t in extract_links(text)):
        return text, False
    token = f"[[{target}|{safe_label(title)}]]" if target_id else f"[[{title}]]"
    lines = (text or "").rstrip("\n").split("\n")
    if lines and lines[-1].startswith(RELATED):
        lines[-1] = lines[-1].rstrip() + f"、{token}"
    elif lines == [""]:
        lines = [f"{RELATED}{token}"]
    else:
        lines += ["", f"{RELATED}{token}"]
    return "\n".join(lines) + "\n", True


def remove_link(text: str, title: str) -> tuple[str, int]:
    """去掉指向 title 的链接：“相关笔记：”行里的直接删掉，正文里的改回普通文字。返回 (新正文, 去掉的处数)。"""
    key = title.strip().lower()
    removed = 0
    lines = []
    masked = _without_code(text)
    offset = 0
    for line in (text or "").split("\n"):
        in_prose = masked[offset:offset + len(RELATED)] == RELATED
        offset += len(line) + 1
        if line.startswith(RELATED) and in_prose:
            items = [m.group(0) for m in WIKILINK.finditer(line)]
            keep = [i for i in items if WIKILINK.match(i).group(1).strip().lower() != key]  # type: ignore[union-attr]
            if len(keep) != len(items):
                removed += len(items) - len(keep)
                if not keep:
                    continue
                line = RELATED + "、".join(keep)
        lines.append(line)
    text = "\n".join(lines)

    def unwrap(t: str, label: str) -> str:
        nonlocal removed
        if t.lower() == key:
            removed += 1
            return label
        return f"[[{t}|{label}]]" if label != t else f"[[{t}]]"

    text = map_links(text, unwrap)
    return re.sub(r"\n{3,}$", "\n", text), removed
