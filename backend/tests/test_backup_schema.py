"""A valid column layout must not hide removed cascade/unique/index rules."""

from __future__ import annotations

import io
import hashlib
import json
import sqlite3
import zipfile

import pytest

from app import config, db
from app.errors import AppError
from app.services import backup


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "live.db")
    monkeypatch.setattr(config, "MATERIALS_DIR", tmp_path / "materials")
    db.init_db()
    conn = db.connect()
    with conn:
        conn.execute("INSERT INTO courses(id,name,created_at,updated_at) VALUES('live-course','Live','now','now')")
        conn.execute("INSERT INTO notes(id,course_id,title,created_at,updated_at) VALUES('live-note','live-course','Original','now','now')")
    conn.close()
    (config.MATERIALS_DIR / "original.txt").write_bytes(b"untouched")
    return tmp_path


def make_archive(root, schema_transform=lambda value: value, alteration=""):
    path = root / "candidate.db"
    conn = sqlite3.connect(path)
    for migration in db.MIGRATIONS:
        conn.executescript(schema_transform(migration))
    conn.execute(f"PRAGMA user_version = {len(db.MIGRATIONS)}")
    conn.execute("INSERT INTO courses(id,name,created_at,updated_at) VALUES('incoming-course','Incoming','now','now')")
    conn.execute("INSERT INTO notes(id,course_id,title,created_at,updated_at) VALUES('incoming-note','incoming-course','Incoming','now','now')")
    if alteration:
        conn.executescript(alteration)
    conn.commit()
    conn.close()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("manifest.json", json.dumps({"format": backup.BACKUP_FORMAT, "schema_version": len(db.MIGRATIONS)}))
        archive.write(path, "knowflow.db")
    return output.getvalue()


@pytest.mark.parametrize("transform,alteration", [
    (lambda value: value.replace(" REFERENCES courses(id) ON DELETE CASCADE", ""), ""),
    (lambda value: value.replace("REFERENCES courses(id) ON DELETE CASCADE", "REFERENCES courses(id) ON DELETE RESTRICT"), ""),
    (lambda value: value, "DROP INDEX idx_notes_course;"),
    (lambda value: value, "DROP INDEX idx_notes_course; CREATE UNIQUE INDEX idx_notes_course ON notes(course_id, deleted_at);"),
    (lambda value: value, "DROP INDEX idx_notes_course; CREATE INDEX idx_notes_course ON notes(deleted_at, course_id);"),
    (lambda value: value, "DROP INDEX idx_notes_course; CREATE INDEX idx_notes_course ON notes(course_id, deleted_at) WHERE deleted_at IS NULL;"),
    (lambda value: value, "DROP INDEX idx_notes_course; CREATE INDEX idx_notes_course ON notes(course_id COLLATE NOCASE, deleted_at DESC);"),
    (lambda value: value.replace(" CHECK(kind IN ('related', 'prerequisite', 'contains', 'applies'))", ""), ""),
    (lambda value: value.replace("CHECK(kind IN ('related', 'prerequisite', 'contains', 'applies'))", "CHECK(1)"), ""),
    (lambda value: value.replace("CHECK(kind IN ('related', 'prerequisite', 'contains', 'applies'))", "CHECK(kind IN ('related', 'prerequisite', 'contains', 'applies', 'invalid'))"), ""),
])
def test_missing_or_changed_constraints_rejected_before_live_mutation(isolated, transform, alteration):
    raw = make_archive(isolated, transform, alteration)
    with pytest.raises(AppError) as error:
        backup.restore_archive(raw)
    assert error.value.code == "BAD_BACKUP"
    conn = db.connect()
    try:
        assert conn.execute("SELECT title FROM notes WHERE id='live-note'").fetchone()[0] == "Original"
        assert conn.execute("SELECT COUNT(*) FROM courses WHERE id='incoming-course'").fetchone()[0] == 0
    finally:
        conn.close()
    assert (config.MATERIALS_DIR / "original.txt").read_bytes() == b"untouched"
    assert not list(isolated.glob("恢复前自动备份-*.zip"))


def test_matching_schema_restores_and_keeps_delete_cascades(isolated):
    backup.restore_archive(make_archive(isolated))
    conn = db.connect()
    try:
        with conn:
            conn.execute("DELETE FROM courses WHERE id='incoming-course'")
        assert conn.execute("SELECT COUNT(*) FROM notes WHERE id='incoming-note'").fetchone()[0] == 0
    finally:
        conn.close()


def test_check_constraint_formatting_is_compatible_and_enforced_after_restore(isolated):
    def formatting(value):
        return value.replace("CHECK(kind IN (", 'check /* compatible formatting */ ( "kind" in (')

    backup.restore_archive(make_archive(isolated, formatting))
    conn = db.connect()
    try:
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            with conn:
                conn.execute("INSERT INTO note_relation_types(source_note_id,target_note_id,kind) "
                             "VALUES('incoming-note','incoming-note','invalid')")
        assert conn.execute("SELECT COUNT(*) FROM note_relation_types").fetchone()[0] == 0
    finally:
        conn.close()


def add_material(content=b"original source"):
    conn = db.connect()
    with conn:
        conn.execute("INSERT INTO materials(id,course_id,original_name,display_title,format,ext,size_bytes,sha256,status,uploaded_at,updated_at) "
                     "VALUES('m','live-course','m.txt','m','text','.txt',?,?,'ready','now','now')",
                     (len(content), hashlib.sha256(content).hexdigest()))
    conn.close()
    path = config.MATERIALS_DIR / "m.txt"
    path.write_bytes(content)
    return path


def test_backup_retries_snapshot_when_concurrent_delete_removes_required_file(isolated, monkeypatch):
    path = add_material()
    snapshot_db = backup._snapshot_db
    snapshots = []

    def snapshot_then_delete(target, *, strip_keys):
        snapshot_db(target, strip_keys=strip_keys)
        snapshots.append(target)
        if len(snapshots) == 1:
            conn = db.connect()
            with conn:
                conn.execute("DELETE FROM materials WHERE id='m'")
            conn.close()
            path.unlink()

    monkeypatch.setattr(backup, "_snapshot_db", snapshot_then_delete)
    raw = backup.build_backup()
    assert len(snapshots) == 2
    monkeypatch.setattr(backup, "_snapshot_db", snapshot_db)
    assert backup.restore_archive(raw)["ok"] is True
    conn = db.connect()
    try:
        assert conn.execute("SELECT COUNT(*) FROM materials").fetchone()[0] == 0
    finally:
        conn.close()


def test_backup_packs_staged_files_even_if_live_file_changes_after_copy(isolated, monkeypatch):
    path = add_material()
    validate = backup._validate_material_files

    def validate_then_change_live(conn, materials):
        validate(conn, materials)
        path.write_bytes(b"concurrent modification")

    monkeypatch.setattr(backup, "_validate_material_files", validate_then_change_live)
    raw = backup.build_backup()
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        assert archive.read("materials/m.txt") == b"original source"
    monkeypatch.setattr(backup, "_validate_material_files", validate)
    # Keep the live dataset valid for the separate pre-restore safety backup.
    path.write_bytes(b"original source")
    assert backup.restore_archive(raw)["ok"] is True
    assert path.read_bytes() == b"original source"


def test_backup_reports_persistent_missing_file_instead_of_successful_bad_zip(isolated, monkeypatch):
    path = add_material()
    path.unlink()
    snapshot_db = backup._snapshot_db
    calls = []

    def record_snapshot(target, *, strip_keys):
        calls.append(target)
        snapshot_db(target, strip_keys=strip_keys)

    monkeypatch.setattr(backup, "_snapshot_db", record_snapshot)
    with pytest.raises(AppError) as error:
        backup.build_backup()
    assert error.value.code == "BACKUP_FAILED" and error.value.retryable
    assert len(calls) == 3
    assert not list(isolated.glob("恢复前自动备份-*.zip"))


def test_backup_retries_when_source_disappears_during_file_copy(isolated, monkeypatch):
    path = add_material()
    copyfile = backup.shutil.copyfile
    failed_once = []

    def delete_while_copying(source, target):
        if source.name == "m.txt" and not failed_once:
            failed_once.append(source)
            conn = db.connect()
            with conn:
                conn.execute("DELETE FROM materials WHERE id='m'")
            conn.close()
            path.unlink()
        return copyfile(source, target)

    monkeypatch.setattr(backup.shutil, "copyfile", delete_while_copying)
    raw = backup.build_backup()
    assert len(failed_once) == 1
    assert backup.restore_archive(raw)["ok"] is True
