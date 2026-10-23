"""Old learning data survives migration; failed migrations leave no partial DDL."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from app import config, db
from app.seed import seed


def old_database():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(db.SCHEMA_V1)
    conn.executescript(db.SCHEMA_V2)
    conn.execute("PRAGMA user_version=2")
    conn.commit()
    seed(conn)
    return conn


def test_v2_migration_preserves_existing_learning_rows():
    conn = old_database()
    tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    before = {table: [dict(row) for row in conn.execute(f"SELECT * FROM {table}")] for table in tables}
    assert not db.migrate_connection(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(db.MIGRATIONS)
    for table, rows in before.items():
        after = [dict(row) for row in conn.execute(f"SELECT * FROM {table}")]
        if rows:
            assert [{key: row[key] for key in rows[0]} for row in after] == rows
        else:
            assert after == []
    assert all(row[0] == 0 for row in conn.execute("SELECT review_revision FROM cards"))
    assert all(row[0] == 0 for row in conn.execute("SELECT parse_attempt FROM materials"))
    assert all(row[0] is None for row in conn.execute("SELECT source_card_id FROM tasks"))
    assert all(row[0] == '{}' for row in conn.execute("SELECT relation_types FROM note_revisions"))
    conn.close()


def test_failed_migration_rolls_back_schema_and_version_and_can_retry(monkeypatch):
    conn = old_database()
    migrations = db.MIGRATIONS
    monkeypatch.setattr(db, "MIGRATIONS", migrations[:2] + [db.SCHEMA_V3 + "\nINVALID SQL;\n"])
    with pytest.raises(sqlite3.Error):
        db.migrate_connection(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
    assert "review_revision" not in {row[1] for row in conn.execute("PRAGMA table_info(cards)")}
    assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='operation_receipts'").fetchone()
    monkeypatch.setattr(db, "MIGRATIONS", migrations)
    db.migrate_connection(conn)
    conn.close()


def test_concurrent_initialization_runs_migrations_once(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "concurrent.db")
    monkeypatch.setattr(config, "MATERIALS_DIR", tmp_path / "materials")
    with ThreadPoolExecutor(max_workers=4) as workers:
        results = list(workers.map(lambda _: db.init_db(), range(4)))
    assert results.count(True) == 1
    conn = db.connect()
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(db.MIGRATIONS)
    conn.close()
