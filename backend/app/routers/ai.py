"""AI：模型配置、隐私同意、任务执行（流式）与草稿确认。

所有 AI 产出先存为草稿（E-07），用户在对比视图或审核页确认后才写入正式数据
（我的 FR-11 / 同学 BR-02）。草稿状态：
running → ready / failed / discarded（页面关闭）/ interrupted（服务重启）
ready → accepted / edited（修改后采纳）/ discarded
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..db import connect, dumps, get_db, get_settings, new_id, now_iso, row_to_dict, set_setting
from ..errors import AppError, not_found
from ..llm import gateway, prompts
from ..llm.gateway import LLMError, ModelConfig
from ..logic.facts import check_preserve, check_summary
from ..logic.plan_rules import TASK_KINDS, validate_tasks
from ..logic.text import char_count, estimate_tokens, plain_excerpt
from ..logic.timeutil import parse_day, today_str
from ..repo import config_public, get_course, get_note_row, note_detail
from .notes import save_revision
from .plans import get_plan_row, plan_tasks, task_out

router = APIRouter(prefix="/api", tags=["AI"])


# ---------------------------------------------------------------- 模型配置

class ConfigIn(BaseModel):
    name: str = Field(default="默认配置", min_length=1, max_length=50)
    base_url: str = Field(min_length=8, max_length=300)
    api_key: str = Field(min_length=1, max_length=500)
    model: str = Field(min_length=1, max_length=100)
    json_mode: bool | None = None
    max_output_tokens: int = Field(default=8192, ge=256, le=65536)
    is_default: bool = True


class ConfigPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=50)
    base_url: str | None = Field(default=None, min_length=8, max_length=300)
    api_key: str | None = Field(default=None, max_length=500)  # 留空表示不修改
    model: str | None = Field(default=None, min_length=1, max_length=100)
    json_mode: bool | None = None
    max_output_tokens: int | None = Field(default=None, ge=256, le=65536)


class TestIn(BaseModel):
    config_id: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None


def _check_url(url: str) -> str:
    url = url.strip()
    if not url.startswith(("https://", "http://")):
        raise AppError(422, "VALIDATION", "Base URL 需要以 https:// 开头", field="base_url")
    return url.rstrip("/")


def _config_row(conn: sqlite3.Connection, config_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM model_configs WHERE id = ?", (config_id,)).fetchone()
    if row is None:
        raise not_found("模型配置")
    return row


@router.get("/ai/configs")
def list_configs(conn: sqlite3.Connection = Depends(get_db)) -> list[dict]:
    rows = conn.execute("SELECT * FROM model_configs ORDER BY is_default DESC, created_at").fetchall()
    return [config_public(r) for r in rows]


@router.post("/ai/configs", status_code=201)
def create_config(body: ConfigIn, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    base_url = _check_url(body.base_url)
    json_mode = body.json_mode if body.json_mode is not None else "deepseek" in base_url
    config_id, now = new_id(), now_iso()
    has_any = conn.execute("SELECT COUNT(*) FROM model_configs").fetchone()[0] > 0
    make_default = body.is_default or not has_any
    with conn:
        if make_default:
            conn.execute("UPDATE model_configs SET is_default = 0")
        conn.execute(
            "INSERT INTO model_configs(id, name, base_url, api_key, model, json_mode, max_output_tokens, is_default, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (config_id, body.name.strip(), base_url, body.api_key.strip(), body.model.strip(),
             1 if json_mode else 0, body.max_output_tokens, 1 if make_default else 0, now, now),
        )
    return config_public(_config_row(conn, config_id))


@router.patch("/ai/configs/{config_id}")
def update_config(config_id: str, body: ConfigPatch, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    row = _config_row(conn, config_id)
    fields = body.model_dump(exclude_unset=True)
    if not (fields.get("api_key") or "").strip():
        fields.pop("api_key", None)
    if "base_url" in fields and fields["base_url"] is not None:
        fields["base_url"] = _check_url(fields["base_url"])
    if "json_mode" in fields:
        fields["json_mode"] = 1 if fields["json_mode"] else 0
    for key in ("name", "model", "api_key"):
        if isinstance(fields.get(key), str):
            fields[key] = fields[key].strip()
    fields = {k: v for k, v in fields.items() if v is not None}
    if fields:
        sets = ", ".join(f"{k} = ?" for k in fields)
        with conn:
            conn.execute(f"UPDATE model_configs SET {sets}, updated_at = ? WHERE id = ?",
                         (*fields.values(), now_iso(), config_id))
            # 更换端点后需要重新同意（同学 NFR-09）
            if fields.get("base_url") and fields["base_url"] != row["base_url"]:
                conn.execute("UPDATE model_configs SET consented_base_url = NULL, consented_at = NULL WHERE id = ?",
                             (config_id,))
    return config_public(_config_row(conn, config_id))


@router.delete("/ai/configs/{config_id}")
def delete_config(config_id: str, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    row = _config_row(conn, config_id)
    with conn:
        conn.execute("DELETE FROM model_configs WHERE id = ?", (config_id,))
        if row["is_default"]:
            first = conn.execute("SELECT id FROM model_configs ORDER BY created_at LIMIT 1").fetchone()
            if first:
                conn.execute("UPDATE model_configs SET is_default = 1 WHERE id = ?", (first["id"],))
    return {"ok": True}


@router.post("/ai/configs/{config_id}/default")
def set_default(config_id: str, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    _config_row(conn, config_id)
    with conn:
        conn.execute("UPDATE model_configs SET is_default = CASE WHEN id = ? THEN 1 ELSE 0 END", (config_id,))
    return config_public(_config_row(conn, config_id))


@router.post("/ai/configs/{config_id}/consent")
def give_consent(config_id: str, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    row = _config_row(conn, config_id)
    with conn:
        conn.execute("UPDATE model_configs SET consented_base_url = ?, consented_at = ? WHERE id = ?",
                     (row["base_url"], now_iso(), config_id))
    return config_public(_config_row(conn, config_id))


@router.delete("/ai/configs/{config_id}/consent")
def revoke_consent(config_id: str, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    _config_row(conn, config_id)
    with conn:
        conn.execute("UPDATE model_configs SET consented_base_url = NULL, consented_at = NULL WHERE id = ?", (config_id,))
    return config_public(_config_row(conn, config_id))


@router.post("/ai/configs/test")
async def test_connection(body: TestIn) -> dict:
    """测试连接（FR-07）：发一条不含笔记内容的 ping，返回成功或具体失败原因。"""
    conn = connect()
    try:
        stored = _config_row(conn, body.config_id) if body.config_id else None
    finally:
        conn.close()
    base_url = _check_url(body.base_url or (stored["base_url"] if stored else ""))
    api_key = (body.api_key or "").strip() or (stored["api_key"] if stored else "")
    model = (body.model or "").strip() or (stored["model"] if stored else "")
    if not api_key or not model:
        raise AppError(422, "VALIDATION", "请填写 API Key 和模型名称")
    cfg = ModelConfig(base_url=base_url, api_key=api_key, model=model)
    started = time.monotonic()
    try:
        result = await gateway.chat(cfg, [{"role": "user", "content": "ping"}], max_tokens=8,
                                    temperature=0, idle_timeout=20, total_timeout=30)
    except LLMError as exc:
        return {"ok": False, "kind": exc.kind, "message": exc.message, "detail": exc.detail}
    latency = int((time.monotonic() - started) * 1000)
    return {"ok": True, "latency_ms": latency, "message": f"连接成功，用时 {latency / 1000:.1f} 秒",
            "usage": result.usage}


@router.get("/ai/status")
def ai_status(conn: sqlite3.Connection = Depends(get_db)) -> dict:
    row = conn.execute("SELECT * FROM model_configs WHERE is_default = 1").fetchone()
    settings = get_settings(conn)
    if row is None:
        return {"configured": False, "config": None, "consent_needed": False,
                "max_input_chars": settings["max_input_chars"]}
    public = config_public(row)
    return {"configured": True, "config": public, "consent_needed": not public["consented"],
            "provider_host": gateway.host_of(row["base_url"]), "max_input_chars": settings["max_input_chars"]}


# ---------------------------------------------------------------- 设置

class SettingsPatch(BaseModel):
    max_input_chars: int | None = Field(default=None, ge=1000, le=100_000)
    idle_timeout_s: int | None = Field(default=None, ge=10, le=600)
    total_timeout_s: int | None = Field(default=None, ge=10, le=1800)
    temperature: float | None = Field(default=None, ge=0, le=1.5)


@router.get("/settings")
def read_settings(conn: sqlite3.Connection = Depends(get_db)) -> dict:
    return get_settings(conn)


@router.patch("/settings")
def write_settings(body: SettingsPatch, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    values = body.model_dump(exclude_unset=True)
    with conn:
        for key, value in values.items():
            if value is not None:
                set_setting(conn, key, value)
    return get_settings(conn)


# ---------------------------------------------------------------- 任务执行

class RunIn(BaseModel):
    kind: Literal["organize", "summarize", "extract", "compose", "plan"]
    note_id: str | None = None
    length: Literal[100, 300] = 100
    count: int = Field(default=10, ge=5, le=20)
    unit_ids: list[str] = Field(default_factory=list, max_length=300)
    instruction: str = Field(default="", max_length=500)
    plan_id: str | None = None
    scope_note_ids: list[str] = Field(default_factory=list, max_length=30)


@dataclass
class TaskSpec:
    kind: str
    messages: list[dict[str, str]]
    max_tokens: int
    stream: bool
    input_chars: int
    note_id: str | None = None
    plan_id: str | None = None
    course_id: str | None = None
    base_version: int | None = None
    params: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)


def _default_config(conn: sqlite3.Connection) -> tuple[str, ModelConfig, sqlite3.Row]:
    row = conn.execute("SELECT * FROM model_configs WHERE is_default = 1").fetchone()
    if row is None:
        raise AppError(409, "NO_CONFIG", "还没有配置大模型，请先在设置里填写 Base URL、API Key 和模型名称")
    if row["consented_base_url"] != row["base_url"]:
        raise AppError(409, "CONSENT_REQUIRED", "首次使用这个服务商前需要你确认内容发送范围",
                       extra={"config_id": row["id"], "provider_host": gateway.host_of(row["base_url"]),
                              "model": row["model"]})
    cfg = ModelConfig(base_url=row["base_url"], api_key=row["api_key"], model=row["model"],
                      json_mode=bool(row["json_mode"]), max_output_tokens=row["max_output_tokens"])
    return row["id"], cfg, row


def _check_length(text_chars: int, limit: int, sample: str) -> None:
    if text_chars > limit:
        raise AppError(422, "TOO_LONG",
                       f"内容共 {text_chars} 字，超过单次 {limit} 字的上限，请分段后再试",
                       extra={"chars": text_chars, "limit": limit, "estimated_tokens": estimate_tokens(sample)})


def _prepare(conn: sqlite3.Connection, body: RunIn, cfg: ModelConfig, limit: int) -> TaskSpec:
    if body.kind in ("organize", "summarize", "extract"):
        if not body.note_id:
            raise AppError(422, "VALIDATION", "缺少笔记", field="note_id")
        note = get_note_row(conn, body.note_id)
        content = note["content"] or ""
        chars = char_count(content)
        if chars == 0:
            raise AppError(422, "EMPTY_INPUT", "这篇笔记还是空的，先写点内容吧")
        _check_length(chars, limit, content)
        common = dict(input_chars=chars, note_id=note["id"], course_id=note["course_id"],
                      base_version=note["version"], context={"original": content, "title": note["title"]})
        if body.kind == "organize":
            max_tokens = min(cfg.max_output_tokens, max(1024, int(estimate_tokens(content) * 1.5)))
            return TaskSpec(kind="organize", stream=True, max_tokens=max_tokens, messages=[
                {"role": "system", "content": prompts.ORGANIZE_SYSTEM},
                {"role": "user", "content": prompts.organize_user(note["title"], content)},
            ], **common)
        if body.kind == "summarize":
            return TaskSpec(kind="summarize", stream=True, max_tokens=256 if body.length == 100 else 512,
                            params={"length": body.length}, messages=[
                                {"role": "system", "content": prompts.summarize_system(body.length)},
                                {"role": "user", "content": prompts.organize_user(note["title"], content)},
                            ], **common)
        return TaskSpec(kind="extract", stream=False, max_tokens=min(cfg.max_output_tokens, 4096),
                        params={"count": body.count}, messages=[
                            {"role": "system", "content": prompts.extract_system(body.count)},
                            {"role": "user", "content": prompts.organize_user(note["title"], content)},
                        ], **common)

    if body.kind == "compose":
        if not body.unit_ids:
            raise AppError(422, "VALIDATION", "请至少选择一段资料", field="unit_ids")
        placeholders = ",".join("?" * len(body.unit_ids))
        rows = conn.execute(
            f"SELECT u.*, m.display_title, m.original_name, m.course_id, m.uploaded_at FROM material_units u "
            f"JOIN materials m ON m.id = u.material_id WHERE u.id IN ({placeholders}) "
            f"ORDER BY m.uploaded_at, u.ordinal", body.unit_ids,
        ).fetchall()
        if len(rows) != len(set(body.unit_ids)):
            raise AppError(422, "INVALID_SOURCE", "选中的资料片段有的已不存在，请刷新后重选")
        courses = {r["course_id"] for r in rows}
        if len(courses) != 1:
            raise AppError(422, "MIXED_COURSES", "一次只能整理同一个学科的资料")
        units = [r for r in rows if r["text"].strip()]
        if not units:
            raise AppError(422, "EMPTY_INPUT", "选中的片段没有文字。扫描件或图片可以先手动补充文字")
        aliases: dict[str, dict[str, Any]] = {}
        blocks: list[str] = []
        for i, r in enumerate(units, start=1):
            alias = f"S{i}"
            aliases[alias] = {"unit_id": r["id"], "material_id": r["material_id"],
                              "material_name": r["original_name"], "locator": r["locator"], "label": r["label"]}
            blocks.append(f"[{alias}] 《{r['original_name']}》{r['label']}\n{r['text']}")
        joined = "\n\n".join(blocks)
        chars = sum(char_count(r["text"]) for r in units)
        _check_length(chars, limit, joined)
        # 资料多时分批整理：每批的输出都放得进模型的输出上限，避免 JSON 被截断
        batches: list[list[str]] = [[]]
        size = 0
        for alias, block in zip(aliases, blocks):
            n = char_count(block)
            if batches[-1] and (size + n > COMPOSE_BATCH_CHARS or len(batches[-1]) >= COMPOSE_BATCH_UNITS):
                batches.append([])
                size = 0
            batches[-1].append(alias)
            size += n
        max_tokens = min(cfg.max_output_tokens, 8192)
        return TaskSpec(
            kind="compose", stream=False, max_tokens=max_tokens, input_chars=chars,
            course_id=units[0]["course_id"],
            params={"unit_ids": [r["id"] for r in units], "instruction": body.instruction},
            context={"aliases": aliases, "batches": batches, "blocks": dict(zip(aliases, blocks)),
                     "instruction": body.instruction},
            messages=[{"role": "system", "content": prompts.COMPOSE_SYSTEM},
                      {"role": "user", "content": prompts.compose_user(body.instruction, blocks)}],
        )

    # plan
    if not body.plan_id:
        raise AppError(422, "VALIDATION", "缺少计划", field="plan_id")
    plan = get_plan_row(conn, body.plan_id)
    today = today_str(int(get_settings(conn)["day_start_hour"]))
    if plan["deadline"] and plan["deadline"] < today:
        raise AppError(422, "DEADLINE_PASSED", f"截止日 {plan['deadline']} 已过，请先修改截止日")
    note_aliases: dict[str, str] = {}
    scope_lines: list[str] = []
    for i, note_id in enumerate(body.scope_note_ids, start=1):
        note = get_note_row(conn, note_id)
        course = get_course(conn, note["course_id"])
        alias = f"N{i}"
        note_aliases[alias] = note["id"]
        scope_lines.append(f"{alias}：{note['title']}（{course['name']}）{plain_excerpt(note['content'], 80)}")
    existing = [t for t in plan_tasks(conn, plan["id"], today) if t["status"] != "done"]
    existing_lines = [
        f"- {t['title']}（{t['due_date'] or '未排期'}，{t['estimated_minutes'] or '?'} 分钟）" for t in existing
    ]
    user = prompts.plan_user(plan["goal"], plan["progress_text"], scope_lines, existing_lines)
    _check_length(char_count(user), limit, user)
    return TaskSpec(
        kind="plan", stream=False, max_tokens=min(cfg.max_output_tokens, 2048), input_chars=char_count(user),
        plan_id=plan["id"], course_id=plan["course_id"],
        params={"scope_note_ids": body.scope_note_ids},
        context={"note_aliases": note_aliases, "today": today, "plan": plan, "existing": existing},
        messages=[{"role": "system", "content": prompts.plan_system(today, plan["deadline"], plan["daily_minutes"])},
                  {"role": "user", "content": user}],
    )


# ---------- 结构化结果的解析与校验

COMPOSE_BATCH_CHARS = 3000
COMPOSE_BATCH_UNITS = 10


async def _compose_batches(cfg: ModelConfig, spec: TaskSpec, settings: dict[str, Any]) -> AsyncIterator[tuple[str, Any]]:
    """分批整理资料。产出 ("progress", {...}) 若干次，最后产出 ("result", (result, warnings, raw))。

    某一批输出被截断时，把这一批对半拆开重来；解析失败时这一批自动重试一次。
    """
    ctx = spec.context
    queue = [list(b) for b in ctx["batches"]]
    total = len(queue)
    done = 0
    sections: list[Any] = []
    title = ""
    while queue:
        batch = queue.pop(0)
        step = done + 1
        expected = int(sum(char_count(ctx["blocks"][a]) for a in batch) * 1.1) + 200
        label = batch[0] if len(batch) == 1 else f"{batch[0]}–{batch[-1]}"
        yield "progress", {"step": step, "total": total, "label": label, "chars": 0, "expected": expected, "done": done}
        messages = [{"role": "system", "content": prompts.COMPOSE_SYSTEM},
                    {"role": "user", "content": prompts.compose_user(ctx["instruction"], [ctx["blocks"][a] for a in batch],
                                                                     step, total)}]
        split = False
        for attempt in range(2):
            parts: list[str] = []
            finish = None
            ticks = 0
            async for kind, value in gateway.stream_chat(
                cfg, messages, max_tokens=spec.max_tokens, temperature=float(settings["temperature"]),
                idle_timeout=float(settings["idle_timeout_s"]), total_timeout=float(settings["total_timeout_s"]),
                json_mode=cfg.json_mode,
            ):
                if kind == "delta":
                    parts.append(value)
                    ticks += 1
                    if ticks % 25 == 0:
                        yield "progress", {"step": step, "total": total, "label": label, "done": done,
                                           "chars": sum(len(x) for x in parts), "expected": expected}
                elif kind == "finish":
                    finish = value
            text = "".join(parts)
            if finish == "length":
                if len(batch) > 1:
                    half = len(batch) // 2
                    queue[0:0] = [batch[:half], batch[half:]]
                    total += 1
                    split = True
                    break
                raise LLMError("PARSE", "单个片段的内容太长，模型输出被截断；可以把这个片段的文字拆短后再试",
                               detail=text[-500:])
            try:
                data = gateway.parse_json_object(text)
                got = data.get("sections")
                if not isinstance(got, list) or not got:
                    raise LLMError("PARSE")
            except LLMError:
                if attempt == 1:
                    raise LLMError("PARSE", f"第 {step} 部分的返回格式不正确，可以重试", detail=text[:2000]) from None
                continue
            sections.extend(got)
            title = title or str(data.get("title") or "")
            break
        if not split:
            done += 1
    yield "progress", {"step": total, "total": total, "label": "", "done": total, "chars": 0, "expected": 1}
    merged = {"title": title, "sections": sections}
    result, warnings = _parse_compose(merged, ctx["aliases"])
    yield "result", (result, warnings, json.dumps(merged, ensure_ascii=False))


def _parse_extract(data: dict[str, Any], count: int) -> tuple[dict[str, Any], list[str]]:
    items_raw = data.get("items")
    if not isinstance(items_raw, list):
        raise LLMError("PARSE")
    items, dropped = [], 0
    for item in items_raw:
        if not isinstance(item, dict):
            dropped += 1
            continue
        front = str(item.get("front") or "").strip()
        back = str(item.get("back") or "").strip()
        if not (1 <= len(front) <= 200 and 1 <= len(back) <= 2000):
            dropped += 1
            continue
        items.append({"front": front, "back": back})
    if not items:
        raise LLMError("PARSE", "模型没有返回可用的知识点")
    warnings = []
    if dropped:
        warnings.append(f"有 {dropped} 个知识点格式不合规（为空或超长），已略去")
    if len(items) > count:
        items = items[:count]
    elif len(items) < count:
        warnings.append(f"要求 {count} 个，模型只给出 {len(items)} 个")
    return {"items": items}, warnings


def _parse_compose(data: dict[str, Any], aliases: dict[str, dict[str, Any]]) -> tuple[dict[str, Any], list[str]]:
    sections_raw = data.get("sections")
    if not isinstance(sections_raw, list) or not sections_raw:
        raise LLMError("PARSE")
    warnings: list[str] = []
    order: list[str] = []  # 按首次出现给脚注编号
    sections = []
    invalid_refs = 0
    for sec in sections_raw:
        if not isinstance(sec, dict):
            continue
        heading = str(sec.get("heading") or "").strip()[:100] or "小节"
        body_md = str(sec.get("body") or "").strip()
        if not body_md:
            continue
        ids = []
        for raw in sec.get("source_ids") or []:
            alias = str(raw).strip().strip("[]")
            if alias in aliases:
                if alias not in ids:
                    ids.append(alias)
            else:
                invalid_refs += 1
        evidence = "ai_supplement" if sec.get("evidence") == "ai_supplement" else "source_based"
        if evidence == "source_based" and not ids:
            evidence = "ai_supplement"
            warnings.append(f"「{heading}」没有标注有效来源，已标为 AI 补充说明")
        for alias in ids:
            if alias not in order:
                order.append(alias)
        sections.append({"heading": heading, "body": body_md, "evidence": evidence, "source_ids": ids})
    if not sections:
        raise LLMError("PARSE", "模型没有返回可用的笔记内容")
    if invalid_refs:
        warnings.append(f"模型引用了 {invalid_refs} 个不存在的片段编号，已忽略")
    unused = [a for a in aliases if a not in order]
    if unused:
        warnings.append(f"有 {len(unused)} 个选中的片段没有被任何小节引用")

    number = {alias: i for i, alias in enumerate(order, start=1)}
    parts = []
    for sec in sections:
        text = f"## {sec['heading']}\n\n"
        if sec["evidence"] == "ai_supplement":
            text += "> AI 补充说明，需核对\n\n"
        text += sec["body"]
        if sec["source_ids"]:
            text += "\n\n来源" + "".join(f"[^{number[a]}]" for a in sec["source_ids"])
        parts.append(text)
    sources = []
    defs = []
    for alias in order:
        info = aliases[alias]
        ord_ = number[alias]
        sources.append({"ord": ord_, **info, "precision": "unit"})
        defs.append(f"[^{ord_}]: [《{info['material_name']}》{info['label']}]"
                    f"(/materials/view?id={info['material_id']}&unit={info['unit_id']})")
    content = "\n\n".join(parts) + ("\n\n" + "\n".join(defs) if defs else "")
    title = str(data.get("title") or "").strip()[:100] or "资料整理笔记"
    return {"title": title, "content": content, "sources": sources, "sections": sections}, warnings


def _parse_plan(data: dict[str, Any], ctx: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    plan = ctx["plan"]
    result_type = "needs_clarification" if data.get("result_type") == "needs_clarification" else "draft"
    questions = [str(q).strip() for q in data.get("questions") or [] if str(q).strip()][:5]
    assumptions = [str(a).strip() for a in data.get("assumptions") or [] if str(a).strip()][:5]
    warnings: list[str] = []
    tasks = []
    has_time = bool(plan["deadline"] or plan["daily_minutes"])
    stripped_dates = 0
    for raw in data.get("tasks") or []:
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title") or "").strip()[:200]
        if not title:
            continue
        kind = raw.get("kind") if raw.get("kind") in TASK_KINDS else "learn"
        due = raw.get("due_date")
        due = str(due).strip() if due not in (None, "", "null") else None
        if due and parse_day(due) is None:
            due = None
        if due and not has_time:
            due = None
            stripped_dates += 1
        minutes = raw.get("estimated_minutes")
        try:
            minutes = int(minutes) if minutes not in (None, "") else None
        except (TypeError, ValueError):
            minutes = None
        if minutes is not None and not 1 <= minutes <= 1440:
            minutes = None
        note_ids = [ctx["note_aliases"][a] for a in raw.get("note_ids") or [] if a in ctx["note_aliases"]]
        tasks.append({"title": title, "kind": kind, "due_date": due, "estimated_minutes": minutes,
                      "note_ids": note_ids})
    if stripped_dates:
        warnings.append("计划没有设截止日和每日时间，已去掉 AI 给出的日期，任务按顺序排列")
    if result_type == "draft" and not tasks:
        if questions:
            result_type = "needs_clarification"
        else:
            raise LLMError("PARSE", "模型没有返回任务")
    problems = validate_tasks(tasks, today=ctx["today"], deadline=plan["deadline"],
                              daily_minutes=plan["daily_minutes"], existing=ctx["existing"])
    return {"result_type": result_type, "questions": questions, "assumptions": assumptions,
            "tasks": tasks, "problems": problems}, warnings


# ---------- 草稿读写

def draft_out(row: sqlite3.Row | dict[str, Any] | None) -> dict[str, Any]:
    data = row_to_dict(row, ("params", "result", "warnings", "usage"))  # type: ignore[arg-type]
    if data is None:
        raise not_found("AI 草稿")
    return data


def _get_draft(conn: sqlite3.Connection, draft_id: str) -> dict[str, Any]:
    return draft_out(conn.execute("SELECT * FROM ai_drafts WHERE id = ?", (draft_id,)).fetchone())


def _finish_draft(draft_id: str, **fields: Any) -> dict[str, Any]:
    conn = connect()
    try:
        for key in ("result", "warnings", "usage"):
            if key in fields and not isinstance(fields[key], str) and fields[key] is not None:
                fields[key] = dumps(fields[key])
        sets = ", ".join(f"{k} = ?" for k in fields)
        with conn:
            conn.execute(f"UPDATE ai_drafts SET {sets} WHERE id = ?", (*fields.values(), draft_id))
        return _get_draft(conn, draft_id)
    finally:
        conn.close()


def _sse(event: str, data: dict[str, Any]) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode()


async def _call_json(cfg: ModelConfig, spec: TaskSpec, settings: dict[str, Any], parse) -> tuple[dict, list, Any]:  # type: ignore[no-untyped-def]
    """非流式 JSON 任务：解析失败或返回为空时自动重试 1 次（EX-04）。"""
    last_error: LLMError | None = None
    raw_output = ""
    for _attempt in range(2):
        result = await gateway.chat(
            cfg, spec.messages, max_tokens=spec.max_tokens, temperature=float(settings["temperature"]),
            idle_timeout=float(settings["idle_timeout_s"]), total_timeout=float(settings["total_timeout_s"]),
            json_mode=cfg.json_mode,
        )
        raw_output = result.content
        try:
            if not result.content.strip():
                raise LLMError("EMPTY")
            if result.finish_reason == "length":
                raise LLMError("PARSE", "输出被截断，可以减少数量或缩小范围后重试")
            data = gateway.parse_json_object(result.content)
            parsed, warnings = parse(data)
            return parsed, warnings, (result.usage, raw_output)
        except LLMError as exc:
            last_error = exc
            continue
    assert last_error is not None
    last_error.detail = (raw_output or "")[:2000] or last_error.detail
    raise last_error


@router.post("/ai/run")
async def run_task(body: RunIn) -> StreamingResponse:
    """执行 AI 任务并以 SSE 返回：start → delta… → done / error。"""
    conn = connect()
    try:
        settings = get_settings(conn)
        config_id, cfg, _row = _default_config(conn)
        spec = _prepare(conn, body, cfg, int(settings["max_input_chars"]))
        draft_id = new_id()
        with conn:
            conn.execute(
                "INSERT INTO ai_drafts(id, kind, status, note_id, plan_id, course_id, params, base_version, config_id, "
                "model, input_chars, created_at) VALUES (?, ?, 'running', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (draft_id, spec.kind, spec.note_id, spec.plan_id, spec.course_id, dumps(spec.params),
                 spec.base_version, config_id, cfg.model, spec.input_chars, now_iso()),
            )
    finally:
        conn.close()

    async def events() -> AsyncIterator[bytes]:
        parts: list[str] = []
        finished = False
        yield _sse("start", {"draft_id": draft_id, "kind": spec.kind, "input_chars": spec.input_chars,
                             "estimated_tokens": estimate_tokens("".join(m["content"] for m in spec.messages)),
                             "model": cfg.model})
        try:
            if spec.stream:
                finish_reason, usage = None, None
                async for kind, value in gateway.stream_chat(
                    cfg, spec.messages, max_tokens=spec.max_tokens, temperature=float(settings["temperature"]),
                    idle_timeout=float(settings["idle_timeout_s"]),
                    total_timeout=float(settings["total_timeout_s"]),
                ):
                    if kind == "delta":
                        parts.append(value)
                        yield _sse("delta", {"text": value})
                    elif kind == "finish":
                        finish_reason = value
                    elif kind == "usage":
                        usage = value
                output = "".join(parts).strip()
                if not output:
                    raise LLMError("EMPTY")
                warnings: list[str] = []
                if spec.kind == "organize":
                    warnings = check_preserve(spec.context["original"], output)
                else:
                    warnings = check_summary(spec.context["original"], output, spec.params["length"])
                if finish_reason == "length":
                    warnings.insert(0, "输出达到长度上限被截断，建议把笔记分段后再整理")
                draft = _finish_draft(draft_id, status="ready", output=output, warnings=warnings, usage=usage,
                                      resolved_at=None)
            elif spec.kind == "compose":
                result = warnings = raw = None
                async for kind, value in _compose_batches(cfg, spec, settings):
                    if kind == "progress":
                        yield _sse("progress", value)
                    else:
                        result, warnings, raw = value
                draft = _finish_draft(draft_id, status="ready", output=raw, result=result, warnings=warnings, usage=None)
            else:
                if spec.kind == "extract":
                    parse = lambda d: _parse_extract(d, spec.params["count"])  # noqa: E731
                elif spec.kind == "compose":
                    parse = lambda d: _parse_compose(d, spec.context["aliases"])  # noqa: E731
                else:
                    parse = lambda d: _parse_plan(d, spec.context)  # noqa: E731
                result, warnings, (usage, raw) = await _call_json(cfg, spec, settings, parse)
                draft = _finish_draft(draft_id, status="ready", output=raw, result=result, warnings=warnings,
                                      usage=usage)
            finished = True
            yield _sse("done", {"draft": draft})
        except LLMError as exc:
            finished = True
            _finish_draft(draft_id, status="failed", output="".join(parts) or exc.detail, error_kind=exc.kind,
                          error_message=exc.message, resolved_at=now_iso())
            yield _sse("error", {"draft_id": draft_id, "error": exc.to_dict(), "partial": "".join(parts)})
        except asyncio.CancelledError:
            # 用户关闭页面或取消生成：未确认的产出作废，原笔记不受影响（EX-06）
            finished = True
            _finish_draft(draft_id, status="discarded", output="".join(parts) or None, resolved_at=now_iso())
            raise
        except Exception as exc:  # 兜底：不让草稿停在 running
            finished = True
            _finish_draft(draft_id, status="failed", error_kind="UNKNOWN", error_message=str(exc)[:300],
                          resolved_at=now_iso())
            yield _sse("error", {"draft_id": draft_id, "error": {"kind": "UNKNOWN", "message": "处理时出错",
                                                                 "retryable": False}})
        finally:
            if not finished:
                _finish_draft(draft_id, status="discarded", resolved_at=now_iso())

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------- 草稿

class AcceptIn(BaseModel):
    content: str | None = Field(default=None, max_length=200_000)  # organize / summarize / compose
    title: str | None = Field(default=None, max_length=100)  # compose
    items: list[dict[str, str]] | None = Field(default=None, max_length=50)  # extract
    tasks: list[dict[str, Any]] | None = Field(default=None, max_length=60)  # plan


@router.get("/ai/drafts/{draft_id}")
def get_draft(draft_id: str, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    return _get_draft(conn, draft_id)


@router.post("/ai/drafts/{draft_id}/discard")
def discard_draft(draft_id: str, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    draft = _get_draft(conn, draft_id)
    if draft["status"] in ("ready", "failed", "running"):
        with conn:
            conn.execute("UPDATE ai_drafts SET status = 'discarded', resolved_at = ? WHERE id = ?", (now_iso(), draft_id))
    return _get_draft(conn, draft_id)


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


@router.post("/ai/drafts/{draft_id}/accept")
def accept_draft(draft_id: str, body: AcceptIn, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    draft = _get_draft(conn, draft_id)
    if draft["status"] in ("accepted", "edited"):
        # 重复点击采纳：幂等返回，不重复写入（同学 IF-03）
        return {"draft": draft, "already": True}
    if draft["status"] != "ready":
        raise AppError(409, "DRAFT_NOT_READY", "这份草稿不能采纳（已放弃、失败或仍在生成）")
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
        with conn:
            save_revision(conn, note, "before_ai_organize" if kind == "organize" else "before_ai_summary", draft_id)
            conn.execute("UPDATE notes SET content = ?, version = version + 1, updated_at = ? WHERE id = ?",
                         (new_content, now, note["id"]))
            conn.execute("UPDATE ai_drafts SET status = ?, resolved_at = ? WHERE id = ?",
                         ("edited" if edited else "accepted", now, draft_id))
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
        with conn:
            conn.executemany(
                "INSERT INTO cards(id, course_id, source_note_id, source_draft_id, front, back, due, created_at, "
                "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(new_id(), note["course_id"], note["id"], draft_id, f, b, due, now, now) for f, b in clean],
            )
            conn.execute("UPDATE ai_drafts SET status = ?, resolved_at = ? WHERE id = ?",
                         ("edited" if edited else "accepted", now, draft_id))
        payload["created_cards"] = len(clean)

    elif kind == "compose":
        result = draft["result"] or {}
        content = (body.content if body.content is not None else result.get("content", "")).strip()
        title = (body.title or result.get("title") or "资料整理笔记").strip()[:100]
        if not content:
            raise AppError(422, "VALIDATION", "内容不能为空", field="content")
        get_course(conn, draft["course_id"])
        note_id = new_id()
        edited = content != result.get("content", "").strip() or title != result.get("title")
        with conn:
            conn.execute(
                "INSERT INTO notes(id, course_id, title, content, tags, version, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, '[]', 1, ?, ?)",
                (note_id, draft["course_id"], title, content, now, now),
            )
            conn.executemany(
                "INSERT INTO note_sources(id, note_id, ord, material_id, unit_id, material_name, locator, label, "
                "precision) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(new_id(), note_id, s["ord"], s["material_id"], s["unit_id"], s["material_name"], s["locator"],
                  s["label"], s.get("precision", "unit")) for s in result.get("sources", [])],
            )
            conn.execute("UPDATE ai_drafts SET status = ?, note_id = ?, resolved_at = ? WHERE id = ?",
                         ("edited" if edited else "accepted", note_id, now, draft_id))
        payload["note"] = note_detail(conn, get_note_row(conn, note_id))

    elif kind == "plan":
        plan = get_plan_row(conn, draft["plan_id"])
        today = today_str(int(get_settings(conn)["day_start_hour"]))
        tasks = body.tasks if body.tasks is not None else (draft["result"] or {}).get("tasks", [])
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
        with conn:
            conn.executemany(
                "INSERT INTO tasks(id, plan_id, title, kind, due_date, estimated_minutes, note_ids, sort, created_at, "
                "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(new_id(), plan["id"], t["title"], t["kind"], t["due_date"], t["estimated_minutes"],
                  dumps(t["note_ids"]), start + i + 1, now, now) for i, t in enumerate(normalized)],
            )
            conn.execute("UPDATE plans SET updated_at = ? WHERE id = ?", (now, plan["id"]))
            conn.execute("UPDATE ai_drafts SET status = ?, resolved_at = ? WHERE id = ?",
                         ("edited" if edited else "accepted", now, draft_id))
        payload["created_tasks"] = len(normalized)
        payload["tasks"] = [task_out(r, today) for r in conn.execute(
            "SELECT * FROM tasks WHERE plan_id = ? ORDER BY sort DESC LIMIT ?", (plan["id"], len(normalized)))]

    return {"draft": _get_draft(conn, draft_id), **payload}
