"""Portable demo reset uses disposable copies, never personal data or model traffic."""
import importlib.util
import json
import sqlite3
import sys
import zipfile
from datetime import date, timedelta
from pathlib import Path

import pytest


def runtime():
    p = Path(__file__).resolve().parents[2] / "demo/run_demo.py"
    spec = importlib.util.spec_from_file_location("acceptance_runtime", p)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_demo_reset_keeps_local_key_and_saves_prior_rehearsal(tmp_path):
    demo = runtime()
    manifest = demo.verified_manifest()
    before = demo.sha(demo.BASELINE)
    data = tmp_path / "data"
    demo.restore_baseline(demo.BASELINE, data, manifest["anchor_date"], manifest["anchor_date"])
    with sqlite3.connect(data / "knowflow.db") as c:
        course = c.execute("SELECT id FROM courses LIMIT 1").fetchone()[0]
        c.execute("INSERT INTO notes(id,course_id,title,created_at,updated_at) VALUES ('rehearsal-only',?,'彩排新增','2026-10-30','2026-10-30')", (course,))
        c.execute("INSERT INTO model_configs(id,name,base_url,api_key,model,is_default,created_at,updated_at) VALUES ('local','local','https://example.com','test-only-local-key','mock',1,'2026-10-30','2026-10-30')")
    backup = demo.restore_baseline(demo.BASELINE, data, manifest["anchor_date"], manifest["anchor_date"])
    with sqlite3.connect(data / "knowflow.db") as c:
        assert c.execute("SELECT api_key FROM model_configs").fetchone()[0] == "test-only-local-key"
        assert c.execute("SELECT COUNT(*) FROM notes WHERE id='rehearsal-only'").fetchone()[0] == 0
    with sqlite3.connect(backup) as c:
        assert c.execute("SELECT title FROM notes WHERE id='rehearsal-only'").fetchone()[0] == "彩排新增"
    assert demo.sha(demo.BASELINE) == before


def test_demo_dates_follow_presentation_day_across_months(tmp_path):
    demo = runtime()
    manifest = demo.verified_manifest()
    anchor = manifest["anchor_date"]
    future = (date.fromisoformat(anchor) + timedelta(days=35)).isoformat()
    data = tmp_path / "data"
    demo.restore_baseline(demo.BASELINE, data, anchor, future)
    with sqlite3.connect(demo.BASELINE) as old, sqlite3.connect(data / "knowflow.db") as new:
        for table, column in (("cards", "due"), ("tasks", "due_date"), ("reviews", "logical_day"), ("focus_sessions", "logical_day"), ("plans", "deadline")):
            original = dict(old.execute(f"SELECT id,{column} FROM {table} WHERE {column} IS NOT NULL"))
            shifted = dict(new.execute(f"SELECT id,{column} FROM {table} WHERE {column} IS NOT NULL"))
            assert shifted == {key: (date.fromisoformat(value) + timedelta(days=35)).isoformat() for key, value in original.items()}
        assert json.loads(new.execute("SELECT value FROM settings WHERE key='demo_anchor'").fetchone()[0]) == future
        assert not new.execute("PRAGMA foreign_key_check").fetchall()
        assert new.execute("SELECT COUNT(*) FROM model_configs").fetchone()[0] == 0


def test_shared_demo_contains_no_model_credentials():
    demo = runtime()
    manifest = demo.verified_manifest()
    with sqlite3.connect(demo.BASELINE) as c:
        assert c.execute("SELECT COUNT(*) FROM model_configs").fetchone()[0] == 0
        assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        for table, count in manifest["counts"].items():
            assert c.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] == count


def test_summary_inline_dates_follow_reset_across_new_year():
    demo = runtime()
    demo.DATE_COLUMNS = {}
    with sqlite3.connect(":memory:") as conn:
        conn.execute("CREATE TABLE summaries(content TEXT)")
        conn.execute("INSERT INTO summaries VALUES (?)", ("统计于2026-12-30；12-29已复习，01-01安排复盘，40-10不是日期。",))
        demo.shift_dates(conn, "2026-12-30", "2027-01-02")
        assert conn.execute("SELECT content FROM summaries").fetchone()[0] == "统计于2027-01-02；01-01已复习，01-04安排复盘，40-10不是日期。"


@pytest.fixture
def export_workspace(tmp_path):
    demo = runtime()
    spec = importlib.util.spec_from_file_location("demo_export", demo.DEMO / "export_demo.py")
    exporter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(exporter)
    local = tmp_path / "source"
    data = local / "data"
    demo.snapshot(demo.BASELINE, data / "knowflow.db")
    with zipfile.ZipFile(demo.ASSETS) as archive:
        for name in demo.verified_manifest()["assets"]:
            target = data / name if name.startswith("materials/") else local / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(name))
    return demo, exporter, data, local / "现场素材", tmp_path / "release"


def test_export_strips_credentials_preserves_source_and_restores_referenced_files(export_workspace):
    demo, exporter, data, live, output = export_workspace
    secret = "local-only-export-regression-credential"
    with sqlite3.connect(data / "knowflow.db") as conn:
        conn.execute("INSERT INTO model_configs(id,name,base_url,api_key,model,is_default,created_at,updated_at) VALUES ('local','local','https://example.com',?,'mock',1,'2026-10-30','2026-10-30')", (secret,))
        conn.execute("INSERT INTO operation_receipts VALUES ('test','receipt','fingerprint','{}','2026-10-30')")
        conn.execute("INSERT INTO ai_drafts(id,kind,status,created_at) VALUES ('local-draft','organize','pending','2026-10-30')")
    before = demo.sha(data / "knowflow.db")
    (data / "materials/unreferenced.txt").write_text("Do not share", "utf-8")
    (live / "local-scratch.txt").write_text("Do not share", "utf-8")
    manifest = exporter.build_bundle(data, live, output)
    assert demo.sha(data / "knowflow.db") == before
    assert {p.name for p in output.iterdir()} == set(exporter.RELEASE_FILES)
    baseline = output / "knowflow-demo.sqlite3"
    assert secret.encode() not in baseline.read_bytes()
    with sqlite3.connect(baseline) as conn:
        for table in ("model_configs", "ai_drafts", "operation_receipts"):
            assert conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] == 0
    demo.LOCAL = output.parent / "teammate"
    demo.DATA = demo.LOCAL / "data"
    demo.ASSETS = output / "materials.zip"
    demo.unpack_assets(manifest)
    demo.restore_baseline(baseline, demo.DATA, manifest["anchor_date"], manifest["anchor_date"])
    assert not list(demo.LOCAL.rglob("*scratch*"))
    assert not (demo.DATA / "materials/unreferenced.txt").exists()
    with sqlite3.connect(demo.DATA / "knowflow.db") as conn:
        for table, count in manifest["counts"].items():
            assert conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] == count
        for mid, ext, digest in conn.execute("SELECT id,ext,sha256 FROM materials"):
            assert demo.sha(demo.DATA / "materials" / (mid + ext)) == digest
        assert not conn.execute("SELECT 1 FROM model_configs").fetchone()
    for name in exporter.LIVE_FILES:
        assert (demo.LOCAL / "现场素材" / name).read_bytes() == (live / name).read_bytes()


def test_export_rejects_changed_original_without_replacing_release(export_workspace):
    demo, exporter, data, live, output = export_workspace
    output.mkdir()
    for name in exporter.RELEASE_FILES:
        (output / name).write_bytes(b"previous-release")
    with sqlite3.connect(data / "knowflow.db") as conn:
        mid, ext = conn.execute("SELECT id,ext FROM materials LIMIT 1").fetchone()
    (data / "materials" / (mid + ext)).write_bytes(b"changed after upload")
    with pytest.raises(RuntimeError, match="校验不符"):
        exporter.build_bundle(data, live, output)
    assert {p.name for p in output.iterdir()} == set(exporter.RELEASE_FILES)
    assert all((output / name).read_bytes() == b"previous-release" for name in exporter.RELEASE_FILES)


def test_missing_bundle_cannot_reset_existing_workspace(tmp_path, monkeypatch):
    demo = runtime()
    manifest = demo.verified_manifest()
    demo.LOCAL = tmp_path / "local"
    demo.DATA = demo.LOCAL / "data"
    demo.ASSETS = tmp_path / "missing.zip"
    demo.restore_baseline(demo.BASELINE, demo.DATA, manifest["anchor_date"], manifest["anchor_date"])
    before = demo.sha(demo.DATA / "knowflow.db")
    monkeypatch.setattr(sys, "argv", ["run_demo.py", "--reset"])
    with pytest.raises(RuntimeError, match="materials.zip"):
        demo.main()
    assert demo.sha(demo.DATA / "knowflow.db") == before
    assert not (demo.LOCAL / "backups").exists()
