"""Export saved demo data and its referenced files, excluding local credentials.

Run with Python 3.11+: python demo/export_demo.py
Only the sanitized SQLite, materials.zip and manifest.json are release files.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "demo"
RELEASE_FILES = ("knowflow-demo.sqlite3", "materials.zip", "manifest.json")
LIVE_FILES = ("现场练习_连接代价与索引.md", "现场练习_CHECK与空值.md", "两阶段锁_课件第62页.png", "空文件.txt")
COUNT_TABLES = ("courses", "notes", "materials", "material_units", "cards", "reviews", "plans", "tasks", "focus_sessions", "summaries")


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def copy_database(source: Path, target: Path) -> None:
    src = sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def build_bundle(data_dir: Path, live_dir: Path, output_dir: Path) -> dict:
    if not (data_dir / "knowflow.db").is_file():
        raise RuntimeError("找不到需要导出的本机数据库。")
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".staging-", dir=output_dir) as temporary:
        staging = Path(temporary)
        db_path = staging / "knowflow-demo.sqlite3"
        copy_database(data_dir / "knowflow.db", db_path)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            # All metadata below comes from this one consistent snapshot.
            keys = [row[0] for row in conn.execute("SELECT api_key FROM model_configs WHERE api_key<>''")]
            setting = conn.execute("SELECT value FROM settings WHERE key='day_start_hour'").fetchone()
            day_start = int(json.loads(setting[0])) if setting else 4
            captured = datetime.now().astimezone()
            anchor = (captured - timedelta(hours=day_start)).date().isoformat()
            conn.execute("PRAGMA secure_delete=ON")
            with conn:
                conn.execute("DELETE FROM model_configs")
                conn.execute("DELETE FROM ai_drafts")
                conn.execute("DELETE FROM operation_receipts")
                conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES ('demo_anchor',?)", (json.dumps(anchor),))
            conn.execute("VACUUM")
            conn.execute("PRAGMA journal_mode=DELETE")
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or conn.execute("PRAGMA foreign_key_check").fetchall():
                raise RuntimeError("数据库完整性或关联检查失败，未发布导出文件。")
            counts = {table: conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] for table in COUNT_TABLES}
            notes = {row["title"]: row["id"] for row in conn.execute("SELECT id,title FROM notes WHERE deleted_at IS NULL")}
            materials = [dict(row) for row in conn.execute(
                "SELECT id,original_name,format,ext,size_bytes,unit_count,sha256 FROM materials ORDER BY id")]
        finally:
            conn.close()
        raw_database = db_path.read_bytes()
        if any(key.encode() in raw_database for key in keys):
            raise RuntimeError("共享数据库仍包含凭据内容，已停止导出。")

        assets = {}
        archive_path = staging / "materials.zip"
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            def add(name: str, raw: bytes) -> None:
                if any(key.encode() in raw for key in keys):
                    raise RuntimeError("待共享文件中发现凭据内容，已停止导出。")
                # Stable archive metadata avoids binary churn on unchanged files.
                info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, raw)
                assets[name] = sha(raw)

            for item in materials:
                filename = item["id"] + item["ext"]
                if Path(filename).name != filename or "/" in filename or "\\" in filename:
                    raise RuntimeError("资料文件名不符合当前应用格式。")
                source = data_dir / "materials" / filename
                raw = source.read_bytes()
                if sha(raw) != item["sha256"]:
                    raise RuntimeError("资料已变化或校验不符：" + item["original_name"])
                add("materials/" + filename, raw)
            for name in LIVE_FILES:
                add("现场素材/" + name, (live_dir / name).read_bytes())

        manifest = {
            "format": "knowflow-acceptance-1", "snapshot_at": captured.isoformat(timespec="seconds"),
            "anchor_date": anchor, "database_sha256": sha(raw_database),
            "assets_archive": "materials.zip", "assets_sha256": sha(archive_path.read_bytes()),
            "assets": assets, "counts": counts, "notes": notes, "materials": materials,
            "privacy": "数据库已移除模型配置、API Key、AI 临时草稿和设备操作回执。materials.zip 包含清单中的资料原件及现场文件。",
            "history": "保留当前已保存的笔记、卡片、计划、修订及复习记录；原预置学习记录仍属于验收样例，不代表长期学习效果。",
        }
        (staging / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), "utf-8")
        existing = output_dir / "knowflow-demo.sqlite3"
        if existing.exists():
            # Close any old WAL state before replacing the portable database.
            old = sqlite3.connect(existing)
            try:
                old.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                old.execute("PRAGMA journal_mode=DELETE")
            finally:
                old.close()
        for name in RELEASE_FILES:
            os.replace(staging / name, output_dir / name)
        return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="导出当前验收工作区的可共享副本；不会修改本机运行库")
    parser.add_argument("--data-dir", type=Path, default=DEMO / ".local/data")
    parser.add_argument("--live-dir", type=Path, default=DEMO / ".local/现场素材")
    args = parser.parse_args()
    output = DEMO / "share"
    previous = [output / name for name in RELEASE_FILES if (output / name).exists()]
    if previous:
        history = DEMO / ".local/bundle-history" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        history.mkdir(parents=True)
        for path in previous:
            if path.suffix == ".sqlite3":
                copy_database(path, history / path.name)
            else:
                shutil.copyfile(path, history / path.name)
    manifest = build_bundle(args.data_dir, args.live_dir, output)
    print(json.dumps({"snapshot_at": manifest["snapshot_at"], "counts": manifest["counts"],
                      "database_bytes": (output / RELEASE_FILES[0]).stat().st_size,
                      "assets_bytes": (output / RELEASE_FILES[1]).stat().st_size,
                      "model_keys_included": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
