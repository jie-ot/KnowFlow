"""资料：上传、解析、查看原件与提取单元（同学 FR-01 / FR-04，NFR-01）。"""

from __future__ import annotations

import hashlib
import mimetypes
import sqlite3
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import config
from ..db import connect, dumps, get_db, new_id, now_iso, row_to_dict
from ..errors import AppError, not_found
from ..logic.extract import WARNING_TEXT, check_content_type, detect_format, extract
from ..logic.text import char_count
from ..repo import get_course
from ..services.coordination import current_generation, generation_scope
from ..services.transactions import atomic_write

router = APIRouter(prefix="/api/materials", tags=["资料"])


class MaterialPatch(BaseModel):
    display_title: str = Field(min_length=1, max_length=200)


class UnitPatch(BaseModel):
    text: str = Field(max_length=100_000)


def _material(conn: sqlite3.Connection, material_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM materials WHERE id = ?", (material_id,)).fetchone()
    if row is None:
        raise not_found("资料")
    return dict(row)


def _unit_out(row: sqlite3.Row) -> dict[str, Any]:
    data = row_to_dict(row, ("warnings",)) or {}
    data["warning_text"] = [WARNING_TEXT.get(w, w) for w in data.get("warnings") or []]
    data["char_count"] = char_count(data["text"])
    return data


def run_extraction(material_id: str, attempt: int = 0, generation: int | None = None) -> None:
    """Only the queued attempt may start and replace extracted units.

    Retry advances parse_attempt. An interrupted older worker may finish its
    expensive file read, but cannot overwrite a newer result or its failure.
    """
    generation = current_generation() if generation is None else generation
    conn = connect()
    claimed = False
    try:
        with generation_scope(generation), atomic_write(conn):
            started = conn.execute(
                "UPDATE materials SET status = 'extracting', updated_at = ? "
                "WHERE id = ? AND parse_attempt = ? AND status = 'uploaded'",
                (now_iso(), material_id, attempt),
            )
            if started.rowcount != 1:
                return
            claimed = True
            row = conn.execute("SELECT * FROM materials WHERE id = ?", (material_id,)).fetchone()
        path = config.MATERIALS_DIR / (row["id"] + row["ext"])
        result = extract(path, row["format"], row["ext"])
        total_chars = sum(char_count(u.text) for u in result.units)
        with generation_scope(generation), atomic_write(conn):
            completed = conn.execute(
                "UPDATE materials SET status = ?, error_code = ?, error_message = ?, updated_at = ? "
                "WHERE id = ? AND parse_attempt = ? AND status = 'extracting'",
                (result.status, result.error_code, result.error_message, now_iso(), material_id, attempt),
            )
            if completed.rowcount != 1 or result.status == "failed":
                return
            conn.execute("DELETE FROM material_units WHERE material_id = ?", (material_id,))
            conn.executemany(
                "INSERT INTO material_units(id, material_id, kind, ordinal, locator, label, text, warnings) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [(new_id(), material_id, u.kind, u.ordinal, u.locator, u.label, u.text, dumps(u.warnings))
                 for u in result.units],
            )
            conn.execute(
                "UPDATE materials SET unit_count = ?, char_count = ? WHERE id = ?",
                (len(result.units), total_chars, material_id),
            )
    except AppError:
        # In particular, an old generation must never write to a restored DB.
        raise
    except Exception as exc:
        if not claimed:
            raise
        # Also covers normalization or transaction/INSERT failures. The failed
        # replacement transaction rolls back before this status-only update.
        # If the DB itself cannot be written, propagate that error honestly.
        with generation_scope(generation), atomic_write(conn):
            conn.execute(
                "UPDATE materials SET status = 'failed', error_code = 'unexpected', error_message = ?, "
                "updated_at = ? WHERE id = ? AND parse_attempt = ? AND status = 'extracting'",
                (f"解析意外中断，可重试；原有片段已保留（{type(exc).__name__}）", now_iso(), material_id, attempt),
            )
    finally:
        conn.close()


@router.get("")
def list_materials(course_id: str | None = None, conn: sqlite3.Connection = Depends(get_db)) -> list[dict]:
    sql = "SELECT * FROM materials"
    params: list[Any] = []
    if course_id:
        sql += " WHERE course_id = ?"
        params.append(course_id)
    sql += " ORDER BY uploaded_at DESC"
    return [dict(r) for r in conn.execute(sql, params)]


@router.post("")
async def upload_materials(
    background: BackgroundTasks,
    course_id: str = Form(...),
    files: list[UploadFile] = File(...),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """一批 1–5 个文件，每个不超过 20 MB。超出数量整批拒绝；单个文件不合格只拒绝它自己。"""
    get_course(conn, course_id)
    if not files:
        raise AppError(422, "NO_FILES", "请选择要上传的文件")
    if len(files) > config.MAX_FILES_PER_BATCH:
        raise AppError(422, "TOO_MANY_FILES", f"一次最多上传 {config.MAX_FILES_PER_BATCH} 个文件，这次选了 {len(files)} 个")
    config.MATERIALS_DIR.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    for upload in files:
        name = (upload.filename or "未命名").replace("\\", "/").split("/")[-1][:200]
        fmt, ext, reason = detect_format(name)
        if fmt is None:
            results.append({"name": name, "ok": False, "code": "unsupported", "message": reason})
            continue
        data = await upload.read(config.MAX_FILE_BYTES + 1)
        if len(data) == 0:
            results.append({"name": name, "ok": False, "code": "empty", "message": "文件是空的（0 字节）"})
            continue
        if len(data) > config.MAX_FILE_BYTES:
            results.append({"name": name, "ok": False, "code": "too_large", "message": "文件超过 20 MB"})
            continue
        if not check_content_type(fmt, data[:8192]):
            results.append({"name": name, "ok": False, "code": "type_mismatch",
                            "message": f"文件内容与扩展名 {ext} 不符"})
            continue
        material_id, now = new_id(), now_iso()
        path = config.MATERIALS_DIR / (material_id + ext)
        try:
            path.write_bytes(data)
        except OSError:
            results.append({"name": name, "ok": False, "code": "storage", "message": "保存文件失败，请检查磁盘空间"})
            continue
        try:
            with conn:
                conn.execute(
                    "INSERT INTO materials(id, course_id, original_name, display_title, format, ext, size_bytes, "
                    "sha256, status, uploaded_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'uploaded', ?, ?)",
                    (material_id, course_id, name, name.rsplit(".", 1)[0], fmt, ext, len(data),
                     hashlib.sha256(data).hexdigest(), now, now),
                )
        except sqlite3.Error:
            path.unlink(missing_ok=True)  # 不留下“已上传成功”的假记录（NFR-07）
            results.append({"name": name, "ok": False, "code": "storage", "message": "写入数据库失败"})
            continue
        background.add_task(run_extraction, material_id, 0, current_generation())
        results.append({"name": name, "ok": True, "material_id": material_id})
    return {"results": results}


@router.get("/{material_id}")
def get_material(material_id: str, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    material = _material(conn, material_id)
    units = [_unit_out(r) for r in conn.execute(
        "SELECT * FROM material_units WHERE material_id = ? ORDER BY ordinal", (material_id,)
    )]
    used_by = [dict(r) for r in conn.execute(
        "SELECT DISTINCT n.id, n.title FROM note_sources s JOIN notes n ON n.id = s.note_id "
        "WHERE s.material_id = ? AND n.deleted_at IS NULL", (material_id,)
    )]
    return {**material, "units": units, "used_by": used_by}


@router.patch("/{material_id}")
def rename_material(material_id: str, body: MaterialPatch, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    _material(conn, material_id)
    with conn:
        conn.execute("UPDATE materials SET display_title = ?, updated_at = ? WHERE id = ?",
                     (body.display_title.strip(), now_iso(), material_id))
    return _material(conn, material_id)


@router.patch("/{material_id}/units/{unit_id}")
def edit_unit(material_id: str, unit_id: str, body: UnitPatch, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    """手动补充或校对提取的文字（同学 DG-02），并标记为“用户修订”。"""
    _material(conn, material_id)
    row = conn.execute("SELECT * FROM material_units WHERE id = ? AND material_id = ?",
                       (unit_id, material_id)).fetchone()
    if row is None:
        raise not_found("资料片段")
    with conn:
        conn.execute("UPDATE material_units SET text = ?, warnings = ? WHERE id = ?",
                     (body.text.strip(), dumps(["user_edited"]), unit_id))
        totals = conn.execute(
            "SELECT COUNT(*) AS n, SUM(CASE WHEN trim(text) = '' THEN 1 ELSE 0 END) AS empty "
            "FROM material_units WHERE material_id = ?", (material_id,)
        ).fetchone()
        chars = sum(char_count(r[0]) for r in conn.execute(
            "SELECT text FROM material_units WHERE material_id = ?", (material_id,)))
        status = "ready" if not totals["empty"] else "partial"
        conn.execute(
            "UPDATE materials SET status = ?, char_count = ?, error_code = CASE WHEN ? = 'ready' THEN NULL ELSE error_code END, "
            "error_message = CASE WHEN ? = 'ready' THEN NULL ELSE error_message END, updated_at = ? WHERE id = ?",
            (status, chars, status, status, now_iso(), material_id),
        )
    return _unit_out(conn.execute("SELECT * FROM material_units WHERE id = ?", (unit_id,)).fetchone())


@router.post("/{material_id}/retry")
def retry_extraction(material_id: str, background: BackgroundTasks,
                     conn: sqlite3.Connection = Depends(get_db)) -> dict:
    with atomic_write(conn):
        material = _material(conn, material_id)
        if material["status"] in ("uploaded", "extracting"):
            raise AppError(409, "BUSY", "这份资料已在解析队列中，请等待或先取消")
        path = config.MATERIALS_DIR / (material["id"] + material["ext"])
        if not path.is_file():
            raise AppError(409, "SOURCE_MISSING", "原件文件不存在，需要重新上传；已提取片段会保留")
        attempt = material["parse_attempt"] + 1
        conn.execute(
            "UPDATE materials SET status = 'uploaded', parse_attempt = ?, error_code = NULL, "
            "error_message = NULL, updated_at = ? WHERE id = ?",
            (attempt, now_iso(), material_id),
        )
    background.add_task(run_extraction, material_id, attempt, current_generation())
    return {"ok": True, "parse_attempt": attempt}


@router.get("/{material_id}/file")
def material_file(material_id: str, conn: sqlite3.Connection = Depends(get_db)) -> FileResponse:
    material = _material(conn, material_id)
    path = Path(config.MATERIALS_DIR / (material["id"] + material["ext"]))
    if not path.exists():
        raise not_found("原件文件")
    media = mimetypes.guess_type(material["original_name"])[0] or "application/octet-stream"
    if material["format"] in ("text", "md", "code"):
        media = "text/plain; charset=utf-8"
    return FileResponse(
        path, media_type=media,
        headers={"Content-Disposition": f"inline; filename*=UTF-8''{quote(material['original_name'])}"},
    )


@router.delete("/{material_id}")
def delete_material(material_id: str, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    """删除原件。引用它的笔记保留，来源显示为“原件已删除”（同学 BR-08）。"""
    material = _material(conn, material_id)
    with conn:
        conn.execute("DELETE FROM materials WHERE id = ?", (material_id,))
    Path(config.MATERIALS_DIR / (material["id"] + material["ext"])).unlink(missing_ok=True)
    return {"ok": True}
