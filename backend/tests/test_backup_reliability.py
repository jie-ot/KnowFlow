"""Restore regressions use isolated databases/files and never model interfaces."""

from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

from app import config, db
from app.errors import AppError
from app.services import backup


def _course_note(conn, title="original"):
    conn.execute("INSERT INTO courses(id, name, created_at, updated_at) VALUES ('course', 'Course', 'now', 'now')")
    conn.execute("INSERT INTO notes(id, course_id, title, content, created_at, updated_at) "
                 "VALUES ('note', 'course', ?, 'body [[link]]', 'now', 'now')", (title,))
    conn.commit()


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "live.db")
    monkeypatch.setattr(config, "MATERIALS_DIR", tmp_path / "materials")
    db.init_db()
    conn = db.connect()
    try:
        _course_note(conn)
    finally:
        conn.close()
    (config.MATERIALS_DIR / "original.txt").write_bytes(b"original material")
    return tmp_path


def _current_title():
    conn = db.connect()
    try:
        return conn.execute("SELECT title FROM notes WHERE id = 'note'").fetchone()[0]
    finally:
        conn.close()


def _rewrite_archive(raw, *, entries=None, edit_db=None, edit_manifest=None):
    source = zipfile.ZipFile(io.BytesIO(raw))
    values = {name: source.read(name) for name in source.namelist()}
    source.close()
    if edit_db:
        values["knowflow.db"] = edit_db(values["knowflow.db"])
    if edit_manifest:
        manifest = json.loads(values["manifest.json"])
        edit_manifest(manifest)
        values["manifest.json"] = json.dumps(manifest).encode()
    values.update(entries or {})
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_STORED) as archive:
        for name, value in values.items():
            archive.writestr(name, value)
    return output.getvalue()


def _edit_db(tmp_path, sql):
    def edit(raw):
        path = tmp_path / "edit.db"
        path.write_bytes(raw)
        conn = sqlite3.connect(path)
        try:
            conn.executescript(sql)
            conn.commit()
        finally:
            conn.close()
        result = path.read_bytes()
        path.unlink()
        return result
    return edit


@pytest.mark.parametrize("name", [
    "materials/../../outside", "materials/C:/outside", "C:/outside", "/materials/outside",
    "materials/\\outside", "materials/a\\..\\outside", "materials/a:stream", "materials/NUL.txt",
    "materials/trailing. ", "materials//empty", "materials/./same",
])
def test_invalid_windows_paths_rejected_before_live_changes(isolated, name):
    raw = _rewrite_archive(backup.build_backup(), entries={name: b"bad"})
    with pytest.raises(AppError) as error:
        backup.restore_archive(raw)
    assert error.value.code == "BAD_BACKUP"
    assert _current_title() == "original"
    assert (config.MATERIALS_DIR / "original.txt").read_bytes() == b"original material"
    assert not list(isolated.glob("恢复前自动备份-*.zip"))


def test_late_crc_error_is_detected_before_database_or_files_change(isolated):
    raw = _rewrite_archive(backup.build_backup(), entries={"materials/late.txt": b"UNIQUE-CRC-PAYLOAD"})
    raw = raw.replace(b"UNIQUE-CRC-PAYLOAD", b"BROKEN-CRC-PAYLOAD")
    with pytest.raises(AppError):
        backup.restore_archive(raw)
    assert _current_title() == "original"
    assert sorted(p.name for p in config.MATERIALS_DIR.iterdir()) == ["original.txt"]
    assert not list(isolated.glob("恢复前自动备份-*.zip"))


@pytest.mark.parametrize("sql", ["PRAGMA user_version = 999;", "DROP TABLE note_revisions;"])
def test_incompatible_database_rejected_without_changes(isolated, sql):
    raw = _rewrite_archive(backup.build_backup(), edit_db=_edit_db(isolated, sql))
    with pytest.raises(AppError):
        backup.restore_archive(raw)
    assert _current_title() == "original"
    assert not list(isolated.glob("恢复前自动备份-*.zip"))


def test_expanded_limit_and_case_collisions_checked_before_restore(isolated, monkeypatch):
    raw = backup.build_backup()
    monkeypatch.setattr(backup, "MAX_EXPANDED_BYTES", 100)
    with pytest.raises(AppError):
        backup.restore_archive(raw)
    monkeypatch.setattr(backup, "MAX_EXPANDED_BYTES", 4_000_000_000)
    raw = _rewrite_archive(raw, entries={"materials/ORIGINAL.TXT": b"case collision"})
    with pytest.raises(AppError):
        backup.restore_archive(raw)
    assert _current_title() == "original"


def test_partial_database_install_rolls_back_database_and_materials(isolated, monkeypatch):
    raw = _rewrite_archive(backup.build_backup(), edit_db=_edit_db(isolated, "UPDATE notes SET title = 'incoming';"),
                           entries={"materials/new.txt": b"new material"})
    original_replace = backup._replace_db
    calls = []

    def partially_replace_then_fail(path):
        calls.append(path.name)
        original_replace(path)
        if len(calls) == 1:
            assert _current_title() == "incoming"
            raise OSError("simulated post-write failure")

    monkeypatch.setattr(backup, "_replace_db", partially_replace_then_fail)
    with pytest.raises(AppError) as error:
        backup.restore_archive(raw)
    assert error.value.code == "RESTORE_FAILED"
    assert calls == ["incoming.db", "rollback.db"]
    assert _current_title() == "original"
    assert sorted(p.name for p in config.MATERIALS_DIR.iterdir()) == ["original.txt"]
    assert (isolated / error.value.extra["safety_copy"]).is_file()


def test_failed_material_swap_restores_original_directory(isolated, monkeypatch):
    raw = backup.build_backup()
    rename = Path.rename

    def fail_incoming(self, target):
        if self.name == "materials-incoming":
            raise OSError("simulated Windows sharing violation")
        return rename(self, target)

    monkeypatch.setattr(Path, "rename", fail_incoming)
    with pytest.raises(AppError) as error:
        backup.restore_archive(raw)
    assert error.value.code == "RESTORE_FAILED"
    assert _current_title() == "original"
    assert (config.MATERIALS_DIR / "original.txt").read_bytes() == b"original material"


def test_database_rollback_error_still_restores_files_and_keeps_recovery(isolated, monkeypatch):
    raw = _rewrite_archive(backup.build_backup(), entries={"materials/new.txt": b"new"})

    def always_fail(_path):
        raise OSError("disk write refused")

    monkeypatch.setattr(backup, "_replace_db", always_fail)
    with pytest.raises(AppError) as error:
        backup.restore_archive(raw)
    assert error.value.code == "RESTORE_ROLLBACK_FAILED"
    assert (config.MATERIALS_DIR / "original.txt").read_bytes() == b"original material"
    assert not (config.MATERIALS_DIR / "new.txt").exists()
    assert (isolated / error.value.extra["safety_copy"]).is_file()
    assert (isolated / error.value.extra["recovery_directory"] / "rollback.db").is_file()


def test_old_schema_is_migrated_before_install_and_running_jobs_interrupted(isolated):
    incoming = isolated / "old.db"
    conn = sqlite3.connect(incoming)
    conn.executescript(db.SCHEMA_V1)
    conn.execute("PRAGMA user_version = 1")
    _course_note(conn, "old backup note")
    material = b"source text"
    conn.execute("INSERT INTO materials(id, course_id, original_name, display_title, format, ext, size_bytes, "
                 "sha256, status, uploaded_at, updated_at) VALUES ('mat', 'course', 'source.txt', 'source', "
                 "'text', '.txt', ?, ?, 'extracting', 'now', 'now')",
                 (len(material), hashlib.sha256(material).hexdigest()))
    conn.execute("INSERT INTO ai_drafts(id, kind, status, created_at) VALUES ('draft', 'organize', 'running', 'now')")
    conn.execute("INSERT INTO plans(id, goal, created_at, updated_at) VALUES ('plan', 'goal', 'now', 'now')")
    conn.execute("INSERT INTO tasks(id, plan_id, title, kind, created_at, updated_at) "
                 "VALUES ('task', 'plan', 'Task', 'study', 'now', 'now')")
    conn.execute("INSERT INTO cards(id, course_id, front, back, due, created_at, updated_at) "
                 "VALUES ('card', 'course', 'Q', 'A', '2026-01-01', 'now', 'now')")
    conn.commit()
    conn.close()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("manifest.json", json.dumps({"format": backup.BACKUP_FORMAT, "created_at": "old"}))
        archive.write(incoming, "knowflow.db")
        archive.writestr("materials/mat.txt", material)
    result = backup.restore_archive(output.getvalue())
    assert result["materials"] == 1 and result["backup_created_at"] == "old"
    conn = db.connect()
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == len(db.MIGRATIONS)
        assert conn.execute("SELECT status FROM ai_drafts WHERE id = 'draft'").fetchone()[0] == "interrupted"
        assert conn.execute("SELECT status, error_code FROM materials").fetchone()[:] == ("failed", "interrupted")
        assert conn.execute("SELECT actual_minutes FROM tasks WHERE id = 'task'").fetchone()[0] == 0
        assert conn.execute("SELECT front FROM cards WHERE id = 'card'").fetchone()[0] == "Q"
        assert conn.execute("PRAGMA foreign_key_check").fetchone() is None
    finally:
        conn.close()
    assert _current_title() == "old backup note"
    assert (config.MATERIALS_DIR / "mat.txt").read_bytes() == material


def test_config_keys_preserved_locally_and_in_safety_only(isolated):
    conn = db.connect()
    conn.execute("INSERT INTO model_configs(id, name, base_url, api_key, model, created_at, updated_at) "
                 "VALUES ('cfg', 'local', 'https://example.test', 'sk-local-secret', 'mock', 'now', 'now')")
    conn.commit()
    conn.close()
    public = backup.build_backup()
    with zipfile.ZipFile(io.BytesIO(public)) as archive:
        assert b"sk-local-secret" not in archive.read("knowflow.db")
    result = backup.restore_archive(public)
    conn = db.connect()
    try:
        assert conn.execute("SELECT api_key FROM model_configs").fetchone()[0] == "sk-local-secret"
    finally:
        conn.close()
    with zipfile.ZipFile(isolated / result["safety_copy"]) as archive:
        assert b"sk-local-secret" in archive.read("knowflow.db")


def test_missing_or_modified_referenced_material_rejected(isolated):
    content = b"source"
    conn = db.connect()
    conn.execute("INSERT INTO materials(id, course_id, original_name, display_title, format, ext, size_bytes, "
                 "sha256, status, uploaded_at, updated_at) VALUES ('source', 'course', 's.txt', 's', 'text', '.txt', "
                 "?, ?, 'ready', 'now', 'now')", (len(content), hashlib.sha256(content).hexdigest()))
    conn.commit()
    conn.close()
    (config.MATERIALS_DIR / "source.txt").write_bytes(content)
    raw = backup.build_backup()
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw)) as source, zipfile.ZipFile(output, "w") as archive:
        for name in source.namelist():
            if name != "materials/source.txt":
                archive.writestr(name, source.read(name))
    with pytest.raises(AppError):
        backup.restore_archive(output.getvalue())
    raw = _rewrite_archive(raw, entries={"materials/source.txt": b"wrong!"})
    with pytest.raises(AppError):
        backup.restore_archive(raw)
    assert _current_title() == "original"
