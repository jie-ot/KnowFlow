"""AI 助手：在任何页面都能提问。

它知道 KnowFlow 的全部用法（内置使用手册），也能通过“工具调用”查看用户自己的笔记、卡片和计划来回答。
工具全部是只读的：助手只能查看和解释，不能修改任何数据。
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import AsyncIterator
from typing import Any, Literal
from urllib.parse import parse_qs, urlparse

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..db import connect, get_settings
from ..errors import AppError
from ..llm import gateway
from ..llm.gateway import LLMError
from ..llm.manual import MANUAL
from ..logic.links import extract_links, snippet_around, title_index
from ..logic.search import search
from ..logic.timeutil import today_str
from .ai import _default_config, _sse

router = APIRouter(prefix="/api", tags=["AI 助手"])

MAX_ROUNDS = 5
TOOL_RESULT_CHARS = 6000


class Msg(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=8000)


class ChatIn(BaseModel):
    messages: list[Msg] = Field(min_length=1, max_length=40)
    page: str = Field(default="/", max_length=500)


TOOLS: list[dict[str, Any]] = [
    {"type": "function", "function": {
        "name": "search_knowflow",
        "description": "按关键词搜索用户的笔记、资料片段和复习卡片，返回标题和命中片段。多个关键词用空格分开。",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "关键词"},
            "types": {"type": "string", "description": "要搜的类型，逗号分隔：note,material,card；默认全部"},
        }, "required": ["query"]},
    }},
    {"type": "function", "function": {
        "name": "read_note",
        "description": "读取一篇笔记的全文（过长会截断）、标签、双链和反向链接。按标题查找，找不到时按关键词找最相关的一篇。",
        "parameters": {"type": "object", "properties": {
            "title": {"type": "string", "description": "笔记标题或关键词"},
        }, "required": ["title"]},
    }},
    {"type": "function", "function": {
        "name": "list_tasks",
        "description": "列出学习计划里的任务（标题、所属计划、日期、预计分钟、状态）。可按日期范围或计划目标关键词筛选。",
        "parameters": {"type": "object", "properties": {
            "start": {"type": "string", "description": "开始日期 YYYY-MM-DD，可省略"},
            "end": {"type": "string", "description": "结束日期 YYYY-MM-DD，可省略"},
            "plan": {"type": "string", "description": "计划目标里的关键词，可省略"},
            "include_done": {"type": "boolean", "description": "是否包含已完成的任务，默认 false"},
        }},
    }},
    {"type": "function", "function": {
        "name": "list_cards",
        "description": "列出复习卡片（正面、背面、到期日、忘记次数）。可只看今天到期的、只看薄弱的（忘记 3 次及以上），或按学科名筛选。",
        "parameters": {"type": "object", "properties": {
            "course": {"type": "string", "description": "学科名称关键词，可省略"},
            "due_only": {"type": "boolean", "description": "只看今天到期（含逾期）的"},
            "weak_only": {"type": "boolean", "description": "只看薄弱知识点"},
        }},
    }},
]

TOOL_LABEL = {
    "search_knowflow": "搜索",
    "read_note": "阅读笔记",
    "list_tasks": "查看计划任务",
    "list_cards": "查看复习卡片",
}

PAGE_NAME = {
    "/": "今天（首页）", "/notes": "笔记", "/materials": "资料", "/materials/view": "资料详情",
    "/review": "复习", "/plans": "计划", "/graph": "知识图谱", "/settings": "设置",
}


def _day_start(conn: sqlite3.Connection) -> int:
    return int(get_settings(conn)["day_start_hour"])


def overview(conn: sqlite3.Connection) -> str:
    """当前数据概况，放进系统提示，让简单问题不必调用工具。"""
    today = today_str(_day_start(conn))
    lines = [f"今天是 {today}（凌晨 4 点前算前一天）。"]
    courses = conn.execute(
        "SELECT c.id, c.name, "
        "(SELECT COUNT(*) FROM notes n WHERE n.course_id = c.id AND n.deleted_at IS NULL) AS notes, "
        "(SELECT COUNT(*) FROM materials m WHERE m.course_id = c.id) AS materials, "
        "(SELECT COUNT(*) FROM cards k WHERE k.course_id = c.id AND k.deleted_at IS NULL) AS cards, "
        "(SELECT COUNT(*) FROM cards k WHERE k.course_id = c.id AND k.deleted_at IS NULL AND k.due <= ?) AS due "
        "FROM courses c ORDER BY c.sort, c.created_at", (today,)
    ).fetchall()
    if courses:
        lines.append("学科：" + "；".join(
            f"{c['name']}（笔记 {c['notes']} 篇，资料 {c['materials']} 份，卡片 {c['cards']} 张，今天到期 {c['due']} 张）"
            for c in courses))
    else:
        lines.append("还没有任何学科。")
    reviewed = conn.execute("SELECT COUNT(*) FROM reviews WHERE logical_day = ?", (today,)).fetchone()[0]
    lines.append(f"今天已复习 {reviewed} 次。")
    tasks = conn.execute(
        "SELECT t.title, t.due_date, t.estimated_minutes, t.status, p.goal FROM tasks t JOIN plans p ON p.id = t.plan_id "
        "WHERE p.archived = 0 AND t.status != 'done' AND t.due_date IS NOT NULL AND t.due_date <= ? "
        "ORDER BY t.due_date LIMIT 15", (today,)
    ).fetchall()
    if tasks:
        lines.append("今天及逾期未完成的任务：" + "；".join(
            f"{t['title']}（{'逾期，原定 ' + t['due_date'] if t['due_date'] < today else '今天'}"
            f"{'，约 ' + str(t['estimated_minutes']) + ' 分钟' if t['estimated_minutes'] else ''}，计划：{t['goal']}）"
            for t in tasks))
    plans = conn.execute(
        "SELECT p.goal, p.deadline, p.daily_minutes, "
        "(SELECT COUNT(*) FROM tasks t WHERE t.plan_id = p.id) AS total, "
        "(SELECT COUNT(*) FROM tasks t WHERE t.plan_id = p.id AND t.status = 'done') AS done "
        "FROM plans p WHERE p.archived = 0 ORDER BY p.updated_at DESC LIMIT 10"
    ).fetchall()
    if plans:
        lines.append("进行中的计划：" + "；".join(
            f"{p['goal']}（完成 {p['done']}/{p['total']}{'，截止 ' + p['deadline'] if p['deadline'] else ''}）"
            for p in plans))
    notes = conn.execute(
        "SELECT title FROM notes WHERE deleted_at IS NULL ORDER BY updated_at DESC LIMIT 20"
    ).fetchall()
    if notes:
        lines.append("最近编辑的笔记：" + "、".join(n["title"] for n in notes))
    return "\n".join(lines)


def page_context(conn: sqlite3.Connection, page: str) -> str:
    url = urlparse(page)
    path = url.path.rstrip("/") or "/"
    query = parse_qs(url.query)
    text = f"用户当前在「{PAGE_NAME.get(path, path)}」页面（{page}）。"
    note_id = (query.get("id") or [None])[0]
    if path == "/notes" and note_id:
        row = conn.execute("SELECT title FROM notes WHERE id = ?", (note_id,)).fetchone()
        if row:
            text += f"正在看的笔记是《{row['title']}》，需要内容时用 read_note 读取。"
    if path == "/plans" and note_id:
        row = conn.execute("SELECT goal FROM plans WHERE id = ?", (note_id,)).fetchone()
        if row:
            text += f"正在看的计划是「{row['goal']}」。"
    return text


SYSTEM = """你是 KnowFlow 里的 AI 助手，用中文回答。KnowFlow 是用户自己电脑上运行的多学科笔记与复习应用。

你能做两件事：
1. 解释 KnowFlow 每个功能怎么用。以下面的使用手册为准，手册没写的功能不要编造，直接说目前没有。
2. 回答关于用户自己学习内容的问题（笔记里写了什么、今天该复习什么、计划进度如何、某个知识点怎么理解等）。需要具体内容时调用工具查看，不要凭空猜测用户的数据；工具查不到就如实说。

要求：
- 回答简洁直接，先给结论；步骤类问题用编号列表。
- 提到应用里的页面或功能时，尽量给出可点击的应用内链接，例如 [打开月历](/plans?view=calendar)、[今日复习](/review)。提到某篇笔记时用 [[笔记标题]] 写法，用户点击就能打开。
- 你只能查看，不能修改数据。用户想修改时，告诉他在哪里、怎么操作。
- 解释知识点时可以结合用户笔记内容，并说明哪些是你补充的。
- 数学公式用 $...$ 或 $$...$$。

{page}

## 用户数据概况
{overview}

{manual}
"""


def _clip(data: Any) -> str:
    text = json.dumps(data, ensure_ascii=False)
    return text if len(text) <= TOOL_RESULT_CHARS else text[:TOOL_RESULT_CHARS] + "…（已截断）"


def run_tool(conn: sqlite3.Connection, name: str, args: dict[str, Any]) -> str:
    today = today_str(_day_start(conn))
    if name == "search_knowflow":
        types = {t.strip() for t in str(args.get("types") or "note,material,card").split(",") if t.strip()}
        items = search(conn, str(args.get("query") or ""), types=types or None, limit=10)
        return _clip([{"type": i["type"], "title": i["title"], "snippet": i["snippet"]["text"],
                       **({"location": i["unit_label"]} if i.get("unit_label") else {})} for i in items[:10]])
    if name == "read_note":
        key = str(args.get("title") or "").strip()
        index = title_index(conn)
        hit = index.get(key.lower())
        note_id = hit["id"] if hit else None
        if not note_id and key:
            found = search(conn, key, types={"note"}, limit=1)
            note_id = found[0]["id"] if found else None
        if not note_id:
            return _clip({"error": f"没有找到标题或内容包含「{key}」的笔记"})
        row = conn.execute(
            "SELECT n.*, c.name AS course FROM notes n JOIN courses c ON c.id = n.course_id WHERE n.id = ?", (note_id,)
        ).fetchone()
        backlinks = []
        for other in conn.execute(
            "SELECT title, content FROM notes WHERE deleted_at IS NULL AND id != ? AND content LIKE ?", (note_id, "%[[%")
        ):
            if any(t.lower() == row["title"].strip().lower() for t in extract_links(other["content"])):
                backlinks.append({"title": other["title"], "context": snippet_around(other["content"], row["title"])})
        cards = conn.execute(
            "SELECT COUNT(*) FROM cards WHERE source_note_id = ? AND deleted_at IS NULL", (note_id,)
        ).fetchone()[0]
        return _clip({
            "title": row["title"], "course": row["course"], "tags": json.loads(row["tags"] or "[]"),
            "updated_at": row["updated_at"][:10], "card_count": cards,
            "links_to": extract_links(row["content"]), "backlinks": backlinks[:10],
            "content": row["content"][:5000] + ("…（后面省略）" if len(row["content"]) > 5000 else ""),
        })
    if name == "list_tasks":
        sql = ("SELECT t.title, t.kind, t.status, t.due_date, t.estimated_minutes, t.needs_review, p.goal "
               "FROM tasks t JOIN plans p ON p.id = t.plan_id WHERE p.archived = 0")
        params: list[Any] = []
        if not args.get("include_done"):
            sql += " AND t.status != 'done'"
        if args.get("start"):
            sql += " AND t.due_date >= ?"
            params.append(str(args["start"]))
        if args.get("end"):
            sql += " AND t.due_date <= ?"
            params.append(str(args["end"]))
        if args.get("plan"):
            sql += " AND p.goal LIKE ?"
            params.append(f"%{args['plan']}%")
        sql += " ORDER BY t.due_date IS NULL, t.due_date, t.sort LIMIT 60"
        rows = conn.execute(sql, params).fetchall()
        return _clip({"today": today, "tasks": [
            {"title": r["title"], "plan": r["goal"], "date": r["due_date"] or "未排期", "minutes": r["estimated_minutes"],
             "status": {"todo": "待办", "in_progress": "进行中", "done": "已完成"}.get(r["status"], r["status"]),
             "overdue": bool(r["due_date"] and r["due_date"] < today and r["status"] != "done"),
             "needs_review": bool(r["needs_review"])} for r in rows]})
    if name == "list_cards":
        sql = ("SELECT k.front, k.back, k.due, k.interval, k.forgot_count, c.name AS course, n.title AS note "
               "FROM cards k JOIN courses c ON c.id = k.course_id LEFT JOIN notes n ON n.id = k.source_note_id "
               "WHERE k.deleted_at IS NULL")
        params = []
        if args.get("course"):
            sql += " AND c.name LIKE ?"
            params.append(f"%{args['course']}%")
        if args.get("due_only"):
            sql += " AND k.due <= ?"
            params.append(today)
        if args.get("weak_only"):
            sql += " AND k.forgot_count >= 3"
        sql += " ORDER BY k.forgot_count DESC, k.due LIMIT 40"
        rows = conn.execute(sql, params).fetchall()
        return _clip({"today": today, "cards": [
            {"front": r["front"], "back": r["back"][:300], "due": r["due"], "interval_days": r["interval"],
             "forgot": r["forgot_count"], "course": r["course"], "note": r["note"]} for r in rows]})
    return _clip({"error": f"没有名为 {name} 的工具"})


@router.post("/assistant/chat")
async def assistant_chat(body: ChatIn) -> StreamingResponse:
    """SSE：delta（文字）、tool（正在查看什么）、done、error。"""
    conn = connect()
    try:
        settings = get_settings(conn)
        _config_id, cfg, _row = _default_config(conn)
        system = SYSTEM.format(page=page_context(conn, body.page), overview=overview(conn), manual=MANUAL)
    finally:
        conn.close()
    history = [{"role": m.role, "content": m.content} for m in body.messages[-20:]]
    messages: list[dict[str, Any]] = [{"role": "system", "content": system}, *history]

    async def events() -> AsyncIterator[bytes]:
        use_tools = True
        wrote = False
        try:
            for _round in range(MAX_ROUNDS):
                parts: list[str] = []
                calls: list[dict[str, Any]] = []
                try:
                    if wrote:
                        yield _sse("delta", {"text": "\n\n"})
                    async for kind, value in gateway.stream_chat_tools(
                        cfg, messages, tools=TOOLS if use_tools else None, max_tokens=2048,
                        temperature=float(settings["temperature"]),
                        idle_timeout=float(settings["idle_timeout_s"]),
                        total_timeout=float(settings["total_timeout_s"]),
                    ):
                        if kind == "delta":
                            parts.append(value)
                            wrote = True
                            yield _sse("delta", {"text": value})
                        elif kind == "tool_calls":
                            calls = value
                except LLMError as exc:
                    # 有的模型不支持工具调用：去掉工具，只靠手册和数据概况再答一次
                    if use_tools and exc.kind == "BAD_REQUEST" and not parts:
                        use_tools = False
                        continue
                    raise
                if not calls:
                    yield _sse("done", {})
                    return
                messages.append({
                    "role": "assistant", "content": "".join(parts) or None,
                    "tool_calls": [{"id": c["id"] or f"call_{i}", "type": "function",
                                    "function": {"name": c["name"], "arguments": c["arguments"] or "{}"}}
                                   for i, c in enumerate(calls)],
                })
                db = connect()
                try:
                    for i, c in enumerate(calls):
                        try:
                            args = json.loads(c["arguments"] or "{}")
                        except json.JSONDecodeError:
                            args = {}
                        hint = args.get("title") or args.get("query") or args.get("plan") or args.get("course") or ""
                        yield _sse("tool", {"name": c["name"], "label": TOOL_LABEL.get(c["name"], c["name"]),
                                            "detail": str(hint)[:40]})
                        result = run_tool(db, c["name"], args if isinstance(args, dict) else {})
                        messages.append({"role": "tool", "tool_call_id": c["id"] or f"call_{i}", "content": result})
                finally:
                    db.close()
            yield _sse("done", {})
        except LLMError as exc:
            yield _sse("error", {"error": exc.to_dict()})
        except Exception as exc:  # noqa: BLE001
            yield _sse("error", {"error": {"kind": "UNKNOWN", "message": "助手处理时出错", "retryable": True,
                                           "detail": str(exc)[:200]}})

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------- AI 推荐连线

class SuggestIn(BaseModel):
    course_id: str | None = None


SUGGEST_PROMPT = """你在帮学生整理知识网络。下面是他的笔记清单（编号、标题、学科、开头摘录、已有链接）。
找出内容上确实相关、值得互相链接、但目前还没有链接的笔记对，最多 12 对。
只根据给出的信息判断，不确定的不要推荐；标题相同或只是同一学科不算理由。
只输出 JSON：{"links": [{"from": "N1", "to": "N3", "reason": "不超过 30 字的理由"}]}
"from" 是应该写上链接的那篇（通常是更具体、更后学的那篇），"to" 是被引用的那篇。"""


@router.post("/graph/suggest")
async def suggest_links(body: SuggestIn) -> dict:
    """让 AI 推荐哪些笔记之间应该连线。只返回建议，用户确认后再由 /notes/{id}/links 写入。"""
    conn = connect()
    try:
        settings = get_settings(conn)
        _config_id, cfg, _row = _default_config(conn)
        sql = "SELECT id, title, course_id, content FROM notes WHERE deleted_at IS NULL"
        params: list[Any] = []
        if body.course_id:
            sql += " AND course_id = ?"
            params.append(body.course_id)
        rows = conn.execute(sql + " ORDER BY updated_at DESC LIMIT 80", params).fetchall()
        courses = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM courses")}
    finally:
        conn.close()
    if len(rows) < 2:
        return {"links": [], "message": "至少要有两篇笔记才能推荐连线"}
    from ..logic.text import plain_excerpt

    alias = {f"N{i + 1}": r for i, r in enumerate(rows)}
    existing = set()
    lines = []
    for key, r in alias.items():
        out = extract_links(r["content"])
        existing.update((r["title"].lower(), t.lower()) for t in out)
        lines.append(f"{key}｜{r['title']}｜{courses.get(r['course_id'], '')}｜{plain_excerpt(r['content'], 120)}"
                     f"｜已链接：{'、'.join(out) or '无'}")
    messages = [{"role": "system", "content": SUGGEST_PROMPT}, {"role": "user", "content": "\n".join(lines)}]
    try:
        result = await gateway.chat(cfg, messages, max_tokens=1500, temperature=0.2,
                                    idle_timeout=float(settings["idle_timeout_s"]),
                                    total_timeout=float(settings["total_timeout_s"]), json_mode=cfg.json_mode)
        data = gateway.parse_json_object(result.content)
    except LLMError as exc:
        raise AppError(502, f"LLM_{exc.kind}", exc.message, retryable=exc.retryable) from None
    links, seen = [], set()
    for item in data.get("links") or []:
        if not isinstance(item, dict):
            continue
        a, b = alias.get(str(item.get("from"))), alias.get(str(item.get("to")))
        if not a or not b or a["id"] == b["id"]:
            continue
        pair = (a["title"].lower(), b["title"].lower())
        if pair in existing or (pair[1], pair[0]) in existing or pair in seen:
            continue
        seen.add(pair)
        links.append({"source": a["id"], "target": b["id"], "source_title": a["title"], "target_title": b["title"],
                      "reason": str(item.get("reason") or "")[:60]})
    return {"links": links[:12]}

