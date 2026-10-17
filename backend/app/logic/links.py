"""双链：在笔记里写 [[笔记标题]] 或 [[笔记标题|显示文字]]（需求 A-FR-05）。

链接按标题匹配（不区分大小写）；同名笔记取最近更新的一篇。
代码块和行内代码里的 [[…]] 不算链接。
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

WIKILINK = re.compile(r"\[\[([^\[\]\n|]{1,100})(\|[^\[\]\n]{0,100})?\]\]")
_CODE = re.compile(r"```[\s\S]*?```|`[^`\n]*`")


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
    """小写标题 → 笔记（同名取最近更新的一篇）。"""
    index: dict[str, dict[str, Any]] = {}
    for row in conn.execute(
        "SELECT id, title, course_id, updated_at FROM notes WHERE deleted_at IS NULL ORDER BY updated_at"
    ):
        index[row["title"].strip().lower()] = dict(row)
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


def add_link(text: str, title: str) -> tuple[str, bool]:
    """在正文末尾的“相关笔记：”一行里加上 [[title]]（没有这一行就新建）。已经链接过则不动。"""
    if any(t.lower() == title.strip().lower() for t in extract_links(text)):
        return text, False
    lines = (text or "").rstrip("\n").split("\n")
    if lines and lines[-1].startswith(RELATED):
        lines[-1] = lines[-1].rstrip() + f"、[[{title}]]"
    elif lines == [""]:
        lines = [f"{RELATED}[[{title}]]"]
    else:
        lines += ["", f"{RELATED}[[{title}]]"]
    return "\n".join(lines) + "\n", True


def remove_link(text: str, title: str) -> tuple[str, int]:
    """去掉指向 title 的链接：“相关笔记：”行里的直接删掉，正文里的改回普通文字。返回 (新正文, 去掉的处数)。"""
    key = title.strip().lower()
    removed = 0
    lines = []
    for line in (text or "").split("\n"):
        if line.startswith(RELATED):
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
