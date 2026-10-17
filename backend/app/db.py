"""SQLite 连接、表结构与通用工具。

每个请求使用独立连接；写操作用 `with conn:` 包成一个事务，
保证“一次采纳要么全部写入、要么全部撤销”（同学文档 NFR-07）。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterator
from datetime import datetime
from typing import Any

from . import config

SCHEMA_V1 = """
CREATE TABLE courses (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    color       TEXT NOT NULL DEFAULT 'blue',
    description TEXT,
    sort        INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE notes (
    id              TEXT PRIMARY KEY,
    course_id       TEXT NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    title           TEXT NOT NULL,
    content         TEXT NOT NULL DEFAULT '',
    tags            TEXT NOT NULL DEFAULT '[]',
    version         INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    deleted_at      TEXT,
    delete_batch_id TEXT
);
CREATE INDEX idx_notes_course ON notes(course_id, deleted_at);

CREATE TABLE note_revisions (
    id         TEXT PRIMARY KEY,
    note_id    TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    title      TEXT NOT NULL,
    content    TEXT NOT NULL,
    reason     TEXT NOT NULL,
    draft_id   TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_revisions_note ON note_revisions(note_id, created_at);

CREATE TABLE note_sources (
    id          TEXT PRIMARY KEY,
    note_id     TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    ord         INTEGER NOT NULL,
    material_id TEXT NOT NULL,
    unit_id     TEXT,
    material_name TEXT NOT NULL,
    locator     TEXT NOT NULL,
    label       TEXT NOT NULL,
    precision   TEXT NOT NULL,
    quote       TEXT
);
CREATE INDEX idx_sources_note ON note_sources(note_id);

CREATE TABLE materials (
    id            TEXT PRIMARY KEY,
    course_id     TEXT NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    original_name TEXT NOT NULL,
    display_title TEXT NOT NULL,
    format        TEXT NOT NULL,
    ext           TEXT NOT NULL,
    size_bytes    INTEGER NOT NULL,
    sha256        TEXT NOT NULL,
    status        TEXT NOT NULL,
    error_code    TEXT,
    error_message TEXT,
    unit_count    INTEGER NOT NULL DEFAULT 0,
    char_count    INTEGER NOT NULL DEFAULT 0,
    uploaded_at   TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX idx_materials_course ON materials(course_id);

CREATE TABLE material_units (
    id          TEXT PRIMARY KEY,
    material_id TEXT NOT NULL REFERENCES materials(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,
    ordinal     INTEGER NOT NULL,
    locator     TEXT NOT NULL,
    label       TEXT NOT NULL,
    text        TEXT NOT NULL,
    warnings    TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX idx_units_material ON material_units(material_id, ordinal);

CREATE TABLE cards (
    id              TEXT PRIMARY KEY,
    course_id       TEXT NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    source_note_id  TEXT,
    source_draft_id TEXT,
    front           TEXT NOT NULL,
    back            TEXT NOT NULL,
    n               INTEGER NOT NULL DEFAULT 0,
    interval        INTEGER NOT NULL DEFAULT 0,
    ef              INTEGER NOT NULL DEFAULT 250,
    due             TEXT NOT NULL,
    forgot_count    INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    deleted_at      TEXT,
    delete_batch_id TEXT
);
CREATE INDEX idx_cards_due ON cards(deleted_at, due);
CREATE INDEX idx_cards_note ON cards(source_note_id);

CREATE TABLE reviews (
    id           TEXT PRIMARY KEY,
    card_id      TEXT NOT NULL,
    course_id    TEXT NOT NULL,
    grade        TEXT NOT NULL,
    q            INTEGER NOT NULL,
    reviewed_at  TEXT NOT NULL,
    logical_day  TEXT NOT NULL,
    prev_interval INTEGER NOT NULL,
    new_interval INTEGER NOT NULL,
    prev_ef      INTEGER NOT NULL,
    new_ef       INTEGER NOT NULL
);
CREATE INDEX idx_reviews_day ON reviews(logical_day);

CREATE TABLE plans (
    id            TEXT PRIMARY KEY,
    course_id     TEXT REFERENCES courses(id) ON DELETE CASCADE,
    goal          TEXT NOT NULL,
    progress_text TEXT NOT NULL DEFAULT '',
    deadline      TEXT,
    daily_minutes INTEGER,
    archived      INTEGER NOT NULL DEFAULT 0,
    version       INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE tasks (
    id                TEXT PRIMARY KEY,
    plan_id           TEXT NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
    title             TEXT NOT NULL,
    kind              TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'todo',
    needs_review      INTEGER NOT NULL DEFAULT 0,
    due_date          TEXT,
    estimated_minutes INTEGER,
    note_ids          TEXT NOT NULL DEFAULT '[]',
    completed_at      TEXT,
    sort              INTEGER NOT NULL DEFAULT 0,
    version           INTEGER NOT NULL DEFAULT 1,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);
CREATE INDEX idx_tasks_plan ON tasks(plan_id, due_date);

CREATE TABLE task_reschedules (
    id         TEXT PRIMARY KEY,
    task_id    TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    old_date   TEXT,
    new_date   TEXT,
    changed_at TEXT NOT NULL
);

CREATE TABLE ai_drafts (
    id            TEXT PRIMARY KEY,
    kind          TEXT NOT NULL,
    status        TEXT NOT NULL,
    note_id       TEXT,
    plan_id       TEXT,
    course_id     TEXT,
    params        TEXT NOT NULL DEFAULT '{}',
    base_version  INTEGER,
    config_id     TEXT,
    model         TEXT,
    output        TEXT,
    result        TEXT,
    warnings      TEXT NOT NULL DEFAULT '[]',
    error_kind    TEXT,
    error_message TEXT,
    input_chars   INTEGER NOT NULL DEFAULT 0,
    usage         TEXT,
    created_at    TEXT NOT NULL,
    resolved_at   TEXT
);

CREATE TABLE model_configs (
    id                 TEXT PRIMARY KEY,
    name               TEXT NOT NULL,
    base_url           TEXT NOT NULL,
    api_key            TEXT NOT NULL,
    model              TEXT NOT NULL,
    json_mode          INTEGER NOT NULL DEFAULT 0,
    max_output_tokens  INTEGER NOT NULL DEFAULT 8192,
    is_default         INTEGER NOT NULL DEFAULT 0,
    consented_base_url TEXT,
    consented_at       TEXT,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL
);

CREATE TABLE settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

MIGRATIONS = [SCHEMA_V1]


def now_iso() -> str:
    """当前本地时间，带时区偏移，例如 2026-10-16T19:30:00+08:00。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def new_id() -> str:
    return str(uuid.uuid4())


def connect() -> sqlite3.Connection:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def init_db() -> bool:
    """建表或升级表结构。返回 True 表示这是一个新建的数据库。"""
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.MATERIALS_DIR.mkdir(parents=True, exist_ok=True)
    conn = connect()
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        current = conn.execute("PRAGMA user_version").fetchone()[0]
        is_new = current == 0
        for index in range(current, len(MIGRATIONS)):
            conn.executescript(MIGRATIONS[index])
            conn.execute(f"PRAGMA user_version = {index + 1}")
        conn.commit()
        return is_new
    finally:
        conn.close()


def get_db() -> Iterator[sqlite3.Connection]:
    """FastAPI 依赖：每个请求一个连接。"""
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


def row_to_dict(row: sqlite3.Row | None, json_fields: tuple[str, ...] = ()) -> dict[str, Any] | None:
    if row is None:
        return None
    data = dict(row)
    for field in json_fields:
        if field in data and isinstance(data[field], str):
            try:
                data[field] = json.loads(data[field])
            except json.JSONDecodeError:
                data[field] = None
    return data


def rows_to_dicts(rows: list[sqlite3.Row], json_fields: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    return [row_to_dict(r, json_fields) for r in rows]  # type: ignore[misc]


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


# ---------- 设置 ----------

def get_settings(conn: sqlite3.Connection) -> dict[str, Any]:
    values = dict(config.DEFAULT_SETTINGS)
    for row in conn.execute("SELECT key, value FROM settings"):
        try:
            values[row["key"]] = json.loads(row["value"])
        except json.JSONDecodeError:
            continue
    return values


def set_setting(conn: sqlite3.Connection, key: str, value: Any) -> None:
    conn.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, json.dumps(value, ensure_ascii=False)),
    )
