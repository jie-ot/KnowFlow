"""关键词检索（需求 FR-03 / 同学 FR-05）。

按空格拆成多个关键词，全部命中才算匹配；标题命中权重高于正文。
返回的片段只是纯文本 + 高亮区间，由前端负责安全渲染。
1000 篇笔记的规模下逐条扫描足够快（NFR-02 目标 ≤ 1 s）。
"""

from __future__ import annotations

import sqlite3
import heapq
from typing import Any

from .text import plain_excerpt

SNIPPET_BEFORE = 30
SNIPPET_AFTER = 90


def _terms(query: str) -> list[str]:
    return [t for t in query.strip().split() if t][:6]


def _like_clause(columns: list[str], terms: list[str]) -> tuple[str, list[str]]:
    parts: list[str] = []
    params: list[str] = []
    for term in terms:
        escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        ors = " OR ".join(f"{col} LIKE ? ESCAPE '\\'" for col in columns)
        parts.append(f"({ors})")
        params.extend([f"%{escaped}%"] * len(columns))
    return " AND ".join(parts), params


def snippet(text: str, terms: list[str]) -> dict[str, Any]:
    """在文本中找第一个命中位置，截取前后若干字，返回片段与高亮区间。"""
    flat = " ".join((text or "").split())
    lower = flat.lower()
    first = -1
    for term in terms:
        pos = lower.find(term.lower())
        if pos != -1 and (first == -1 or pos < first):
            first = pos
    if first == -1:
        return {"text": flat[: SNIPPET_BEFORE + SNIPPET_AFTER], "highlights": []}
    start = max(0, first - SNIPPET_BEFORE)
    end = min(len(flat), first + SNIPPET_AFTER)
    piece = flat[start:end]
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(flat) else ""
    piece_lower = piece.lower()
    highlights: list[list[int]] = []
    for term in terms:
        t = term.lower()
        pos = piece_lower.find(t)
        while pos != -1:
            highlights.append([pos + len(prefix), pos + len(prefix) + len(t)])
            pos = piece_lower.find(t, pos + len(t))
    highlights.sort()
    return {"text": prefix + piece + suffix, "highlights": highlights}


def _score(title: str, body: str, terms: list[str]) -> int:
    score = 0
    tl, bl = (title or "").lower(), (body or "").lower()
    for term in terms:
        t = term.lower()
        if t in tl:
            score += 10
        score += min(bl.count(t), 10)
    return score


def search(conn: sqlite3.Connection, query: str, course_id: str | None = None,
           types: set[str] | None = None, limit: int = 30) -> list[dict[str, Any]]:
    types = types or {"note", "material", "card"}
    terms = _terms(query)
    results: list[dict[str, Any]] = []
    best_results: list[tuple[int, str, int, dict[str, Any]]] = []
    ordinal = 0

    def offer(item: dict[str, Any]) -> None:
        nonlocal ordinal
        entry = (item["_score"], item["updated_at"], -ordinal, item)
        ordinal += 1
        if len(best_results) < limit:
            heapq.heappush(best_results, entry)
        elif entry[:3] > best_results[0][:3]:
            heapq.heapreplace(best_results, entry)

    if not terms:
        if "note" in types:
            sql = "SELECT * FROM notes WHERE deleted_at IS NULL"
            params: list[Any] = []
            if course_id:
                sql += " AND course_id = ?"
                params.append(course_id)
            sql += " ORDER BY updated_at DESC LIMIT ?"
            params.append(limit)
            for row in conn.execute(sql, params):
                results.append({
                    "type": "note", "id": row["id"], "title": row["title"],
                    "course_id": row["course_id"], "updated_at": row["updated_at"],
                    "snippet": {"text": plain_excerpt(row["content"], 90), "highlights": []},
                })
        return results

    if "note" in types:
        clause, params = _like_clause(["title", "content"], terms)
        sql = f"SELECT id, title, content, course_id, updated_at FROM notes WHERE deleted_at IS NULL AND {clause}"
        if course_id:
            sql += " AND course_id = ?"
            params.append(course_id)
        for row in conn.execute(sql, params):
            offer({
                "type": "note", "id": row["id"], "title": row["title"],
                "course_id": row["course_id"], "updated_at": row["updated_at"],
                "_body": row["content"],
                "_score": _score(row["title"], row["content"], terms),
            })

    if "material" in types:
        # 资料：文件名或某个提取单元命中即可；每份资料只返回得分最高的单元
        clause, params = _like_clause(["u.text", "m.display_title", "m.original_name"], terms)
        sql = (
            "SELECT m.id AS mid, m.display_title, m.course_id, m.updated_at, "
            "u.id AS uid, u.label, u.text FROM material_units u "
            f"JOIN materials m ON m.id = u.material_id WHERE {clause}"
        )
        if course_id:
            sql += " AND m.course_id = ?"
            params.append(course_id)
        best: dict[str, dict[str, Any]] = {}
        for row in conn.execute(sql, params):
            score = _score(row["display_title"], row["text"], terms)
            current = best.get(row["mid"])
            if current is None or score > current["_score"]:
                best[row["mid"]] = {
                    "type": "material", "id": row["mid"], "title": row["display_title"],
                    "course_id": row["course_id"], "updated_at": row["updated_at"],
                    "unit_id": row["uid"], "unit_label": row["label"],
                    "_body": row["text"], "_score": score,
                }
        for item in best.values():
            offer(item)

    if "card" in types:
        clause, params = _like_clause(["front", "back"], terms)
        sql = f"SELECT id, course_id, source_note_id, front, back, updated_at FROM cards WHERE deleted_at IS NULL AND {clause}"
        if course_id:
            sql += " AND course_id = ?"
            params.append(course_id)
        for row in conn.execute(sql, params):
            offer({
                "type": "card", "id": row["id"], "title": row["front"],
                "course_id": row["course_id"], "updated_at": row["updated_at"],
                "source_note_id": row["source_note_id"],
                "_body": row["back"],
                "_score": _score(row["front"], row["back"], terms),
            })

    # Only visible hits need a snippet. Rank ties keep the original category and
    # row order, matching the previous stable sorts without retaining all bodies.
    results = [entry[3] for entry in sorted(best_results, reverse=True)]
    for r in results:
        r.pop("_score", None)
        r["snippet"] = snippet(r.pop("_body"), terms)
    return results
