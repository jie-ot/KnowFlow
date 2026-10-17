"""番茄钟专注记录、AI 每周小结、数据备份与恢复、使用说明。"""

from __future__ import annotations

import io
import json
import shutil
import sqlite3
import tempfile
import zipfile
from collections.abc import AsyncIterator
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from .. import config
from ..db import connect, get_db, get_settings, new_id, now_iso
from ..errors import AppError, not_found
from ..llm import gateway
from ..llm.gateway import LLMError
from ..llm.manual import MANUAL
from ..logic.timeutil import now_local, today_str
from .ai import _default_config, _sse

router = APIRouter(prefix="/api", tags=["扩展"])


def _today(conn: sqlite3.Connection) -> str:
    return today_str(int(get_settings(conn)["day_start_hour"]))


# ---------------------------------------------------------------- 使用说明

@router.get("/manual")
def manual() -> dict:
    return {"markdown": MANUAL.strip()}


# ---------------------------------------------------------------- 番茄钟

class FocusIn(BaseModel):
    minutes: int = Field(ge=1, le=600)
    started_at: str = Field(max_length=40)
    done: bool = False


@router.post("/tasks/{task_id}/focus")
def record_focus(task_id: str, body: FocusIn, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    """记录一段专注：累加到任务的实际用时；done 为 true 时顺便把任务标为完成。"""
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        raise not_found("任务")
    now = now_iso()
    with conn:
        conn.execute(
            "INSERT INTO focus_sessions(id, task_id, minutes, started_at, ended_at, logical_day) VALUES (?, ?, ?, ?, ?, ?)",
            (new_id(), task_id, body.minutes, body.started_at, now, _today(conn)),
        )
        if body.done:
            conn.execute("UPDATE tasks SET actual_minutes = actual_minutes + ?, status = 'done', completed_at = ?, "
                         "updated_at = ? WHERE id = ?", (body.minutes, now, now, task_id))
        else:
            conn.execute("UPDATE tasks SET actual_minutes = actual_minutes + ?, "
                         "status = CASE WHEN status = 'todo' THEN 'in_progress' ELSE status END, updated_at = ? "
                         "WHERE id = ?", (body.minutes, now, task_id))
    task = conn.execute("SELECT actual_minutes, status FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return {"actual_minutes": task["actual_minutes"], "status": task["status"]}


@router.get("/focus/today")
def focus_today(conn: sqlite3.Connection = Depends(get_db)) -> dict:
    today = _today(conn)
    total = conn.execute("SELECT COALESCE(SUM(minutes), 0), COUNT(*) FROM focus_sessions WHERE logical_day = ?",
                         (today,)).fetchone()
    return {"today": today, "minutes": total[0], "sessions": total[1]}


# ---------------------------------------------------------------- AI 每周小结

WEEKLY_PROMPT = """你是学生的学习教练。根据下面这一周的真实学习数据，用中文写一份简短的周小结，Markdown 格式：
## 这一周
用 2–3 句话概括投入和成果，引用具体数字。
## 做得好的
1–3 条。
## 需要注意的
1–3 条，例如总是忘的知识点（点名具体卡片）、逾期的任务、复习中断的日子。
## 下周建议
3 条以内，具体可执行（哪天做什么、先复习哪些）。
不要编造数据里没有的内容；数据很少时就如实说并给出起步建议。总字数 400 字以内。"""


def weekly_stats(conn: sqlite3.Connection, end: str) -> tuple[str, str, dict[str, Any]]:
    """统计 [end-6, end] 这 7 天的学习数据。"""
    start = (date.fromisoformat(end) - timedelta(days=6)).isoformat()
    by_day = {r["logical_day"]: dict(r) for r in conn.execute(
        "SELECT logical_day, COUNT(*) AS total, SUM(grade = 'good') AS good, SUM(grade = 'forgot') AS forgot "
        "FROM reviews WHERE logical_day BETWEEN ? AND ? GROUP BY logical_day", (start, end))}
    by_course = [dict(r) for r in conn.execute(
        "SELECT c.name, COUNT(*) AS total, SUM(r.grade = 'good') AS good FROM reviews r JOIN courses c ON c.id = r.course_id "
        "WHERE r.logical_day BETWEEN ? AND ? GROUP BY c.id", (start, end))]
    forgot = [dict(r) for r in conn.execute(
        "SELECT k.front, COUNT(*) AS times FROM reviews r JOIN cards k ON k.id = r.card_id "
        "WHERE r.logical_day BETWEEN ? AND ? AND r.grade = 'forgot' GROUP BY k.id ORDER BY times DESC LIMIT 8", (start, end))]
    done = [dict(r) for r in conn.execute(
        "SELECT t.title, t.estimated_minutes, t.actual_minutes, p.goal FROM tasks t JOIN plans p ON p.id = t.plan_id "
        "WHERE t.status = 'done' AND substr(t.completed_at, 1, 10) BETWEEN ? AND ? LIMIT 30", (start, end))]
    overdue = [dict(r) for r in conn.execute(
        "SELECT t.title, t.due_date, p.goal FROM tasks t JOIN plans p ON p.id = t.plan_id "
        "WHERE p.archived = 0 AND t.status != 'done' AND t.due_date IS NOT NULL AND t.due_date <= ? LIMIT 20", (end,))]
    focus = conn.execute("SELECT COALESCE(SUM(minutes), 0) FROM focus_sessions WHERE logical_day BETWEEN ? AND ?",
                         (start, end)).fetchone()[0]
    notes_new = conn.execute("SELECT COUNT(*) FROM notes WHERE deleted_at IS NULL AND substr(created_at, 1, 10) BETWEEN ? AND ?",
                             (start, end)).fetchone()[0]
    notes_edit = conn.execute("SELECT COUNT(*) FROM notes WHERE deleted_at IS NULL AND substr(updated_at, 1, 10) BETWEEN ? AND ?",
                              (start, end)).fetchone()[0]
    cards_new = conn.execute("SELECT COUNT(*) FROM cards WHERE deleted_at IS NULL AND substr(created_at, 1, 10) BETWEEN ? AND ?",
                             (start, end)).fetchone()[0]
    days = []
    for i in range(7):
        d = (date.fromisoformat(start) + timedelta(days=i)).isoformat()
        r = by_day.get(d, {})
        days.append({"day": d, "reviews": r.get("total") or 0, "good": r.get("good") or 0, "forgot": r.get("forgot") or 0})
    total = sum(d["reviews"] for d in days)
    good = sum(d["good"] for d in days)
    return start, end, {
        "period": f"{start} 至 {end}", "reviews_total": total, "remember_rate": round(good / total, 2) if total else None,
        "active_days": sum(1 for d in days if d["reviews"]), "by_day": days, "by_course": by_course,
        "most_forgotten": forgot, "tasks_done": done, "tasks_overdue": overdue, "focus_minutes": focus,
        "notes_created": notes_new, "notes_edited": notes_edit, "cards_created": cards_new,
    }


@router.get("/summaries")
def list_summaries(conn: sqlite3.Connection = Depends(get_db)) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM summaries ORDER BY created_at DESC LIMIT 20")]


@router.get("/summaries/week-stats")
def week_stats(conn: sqlite3.Connection = Depends(get_db)) -> dict:
    return weekly_stats(conn, _today(conn))[2]


@router.post("/summaries/weekly")
async def weekly_summary() -> StreamingResponse:
    """生成最近 7 天的学习小结（流式），完成后保存，可以在首页回看。"""
    conn = connect()
    try:
        settings = get_settings(conn)
        _cid, cfg, _row = _default_config(conn)
        start, end, stats = weekly_stats(conn, _today(conn))
    finally:
        conn.close()
    messages = [{"role": "system", "content": WEEKLY_PROMPT},
                {"role": "user", "content": json.dumps(stats, ensure_ascii=False)}]

    async def events() -> AsyncIterator[bytes]:
        parts: list[str] = []
        try:
            async for kind, value in gateway.stream_chat(
                cfg, messages, max_tokens=1500, temperature=0.4,
                idle_timeout=float(settings["idle_timeout_s"]), total_timeout=float(settings["total_timeout_s"]),
            ):
                if kind == "delta":
                    parts.append(value)
                    yield _sse("delta", {"text": value})
            content = "".join(parts).strip()
            if not content:
                raise LLMError("EMPTY")
            db = connect()
            try:
                sid = new_id()
                with db:
                    db.execute("INSERT INTO summaries(id, kind, period_start, period_end, content, model, created_at) "
                               "VALUES (?, 'weekly', ?, ?, ?, ?, ?)", (sid, start, end, content, cfg.model, now_iso()))
            finally:
                db.close()
            yield _sse("done", {"id": sid, "period_start": start, "period_end": end})
        except LLMError as exc:
            yield _sse("error", {"error": exc.to_dict()})

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.delete("/summaries/{summary_id}")
def delete_summary(summary_id: str, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    with conn:
        conn.execute("DELETE FROM summaries WHERE id = ?", (summary_id,))
    return {"ok": True}


# ---------------------------------------------------------------- 备份与恢复

BACKUP_FORMAT = "knowflow-backup-1"


def _snapshot_db(target: Path, *, strip_keys: bool) -> None:
    """用 SQLite 的在线备份接口拷一份一致的数据库；可选去掉 API Key。"""
    src = connect()
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
        if strip_keys:
            dst.execute("UPDATE model_configs SET api_key = ''")
            dst.commit()
    finally:
        dst.close()
        src.close()


def build_backup(*, strip_keys: bool = True) -> bytes:
    buf = io.BytesIO()
    with tempfile.TemporaryDirectory() as tmp:
        db_copy = Path(tmp) / "knowflow.db"
        _snapshot_db(db_copy, strip_keys=strip_keys)
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("manifest.json", json.dumps({
                "format": BACKUP_FORMAT, "app_version": config.APP_VERSION, "created_at": now_iso(),
                "api_keys_included": not strip_keys,
            }, ensure_ascii=False))
            zf.write(db_copy, "knowflow.db")
            if config.MATERIALS_DIR.exists():
                for f in config.MATERIALS_DIR.rglob("*"):
                    if f.is_file():
                        zf.write(f, "materials/" + f.relative_to(config.MATERIALS_DIR).as_posix())
    return buf.getvalue()


@router.get("/backup")
def download_backup() -> Response:
    """导出全部数据（笔记、资料原件、卡片、复习记录、计划、设置）。API Key 不放进备份。"""
    data = build_backup(strip_keys=True)
    name = f"KnowFlow-备份-{now_local().strftime('%Y%m%d-%H%M')}.zip"
    from urllib.parse import quote
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})


REQUIRED_TABLES = {"courses", "notes", "cards", "reviews", "plans", "tasks", "materials", "model_configs"}


@router.post("/restore")
async def restore_backup(file: UploadFile = File(...)) -> dict:
    """从备份恢复：先校验，再把当前数据另存一份，最后整体替换。当前的大模型配置（含 API Key）会保留。"""
    raw = await file.read()
    if len(raw) > 2_000_000_000:
        raise AppError(413, "TOO_LARGE", "备份文件太大")
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
        manifest = json.loads(zf.read("manifest.json"))
        if manifest.get("format") != BACKUP_FORMAT:
            raise ValueError
        db_bytes = zf.read("knowflow.db")
    except (zipfile.BadZipFile, KeyError, ValueError, json.JSONDecodeError):
        raise AppError(422, "BAD_BACKUP", "这不是 KnowFlow 导出的备份文件") from None

    with tempfile.TemporaryDirectory() as tmp:
        incoming = Path(tmp) / "incoming.db"
        incoming.write_bytes(db_bytes)
        check = sqlite3.connect(incoming)
        try:
            tables = {r[0] for r in check.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            if not REQUIRED_TABLES <= tables or check.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise AppError(422, "BAD_BACKUP", "备份里的数据库不完整，没有恢复")
            # 保留本机现有的大模型配置（备份里不含 API Key）
            current = connect()
            try:
                configs = [dict(r) for r in current.execute("SELECT * FROM model_configs")]
            finally:
                current.close()
            if configs:
                check.execute("DELETE FROM model_configs")
                cols = list(configs[0].keys())
                incoming_cols = {r[1] for r in check.execute("PRAGMA table_info(model_configs)")}
                cols = [c for c in cols if c in incoming_cols]
                check.executemany(
                    f"INSERT INTO model_configs({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
                    [[c[k] for k in cols] for c in configs])
                check.commit()
        finally:
            check.close()

        # 恢复前把当前数据存一份，万一选错了文件还能找回
        safety = config.DATA_DIR / f"恢复前自动备份-{now_local().strftime('%Y%m%d-%H%M%S')}.zip"
        safety.write_bytes(build_backup(strip_keys=False))

        # 替换数据库：用备份接口写入现有文件，正在使用的连接也能看到新数据
        src = sqlite3.connect(incoming)
        dst = connect()
        try:
            src.backup(dst)
        finally:
            src.close()
            dst.close()

    # 替换资料原件
    shutil.rmtree(config.MATERIALS_DIR, ignore_errors=True)
    config.MATERIALS_DIR.mkdir(parents=True, exist_ok=True)
    count = 0
    for name in zf.namelist():
        if not name.startswith("materials/") or name.endswith("/"):
            continue
        rel = Path(name[len("materials/"):])
        if rel.is_absolute() or ".." in rel.parts:
            continue
        out = config.MATERIALS_DIR / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(zf.read(name))
        count += 1

    from ..db import init_db
    init_db()  # 备份来自旧版本时补齐表结构
    return {"ok": True, "materials": count, "safety_copy": safety.name, "backup_created_at": manifest.get("created_at")}
