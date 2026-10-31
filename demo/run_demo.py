"""Portable acceptance workspace. Only demo/.local is mutable; daily data is separate."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import zipfile
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "demo"
LOCAL = DEMO / ".local"
DATA = LOCAL / "data"
BASELINE = DEMO / "share" / "knowflow-demo.sqlite3"
MANIFEST = DEMO / "share" / "manifest.json"
ASSETS = DEMO / "share" / "materials.zip"
DATE_COLUMNS = {
    "courses": ("created_at", "updated_at"), "notes": ("created_at", "updated_at", "deleted_at"),
    "cards": ("due", "created_at", "updated_at", "deleted_at"),
    "reviews": ("reviewed_at", "logical_day"), "note_revisions": ("created_at",),
    "plans": ("deadline", "created_at", "updated_at"),
    "tasks": ("due_date", "completed_at", "created_at", "updated_at"),
    "focus_sessions": ("started_at", "ended_at", "logical_day"),
    "summaries": ("period_start", "period_end", "created_at"),
    "task_reschedules": ("old_date", "new_date", "changed_at"),
    "materials": ("uploaded_at", "updated_at"),
}


def sha(path: Path) -> str:
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def verified_manifest() -> dict:
    if not BASELINE.is_file() or not MANIFEST.is_file():
        raise RuntimeError("缺少演示数据库或 manifest.json，请完整拉取仓库。")
    m = json.loads(MANIFEST.read_text("utf-8"))
    if sha(BASELINE) != m["database_sha256"]:
        raise RuntimeError("演示数据库校验不符，未替换任何数据。请重新获取演示包。")
    return m


def shift_dates(conn: sqlite3.Connection, anchor: str, today: str) -> None:
    anchor_day = date.fromisoformat(anchor)
    delta = date.fromisoformat(today) - anchor_day
    if not delta.days:
        return
    with conn:
        for table, columns in DATE_COLUMNS.items():
            for column in columns:
                for rowid, value in conn.execute(f'SELECT rowid,"{column}" FROM "{table}" WHERE "{column}" IS NOT NULL').fetchall():
                    if len(value) == 10:
                        shifted = (date.fromisoformat(value) + delta).isoformat()
                    else:
                        shifted = (datetime.fromisoformat(value) + delta).isoformat(timespec="seconds")
                    conn.execute(f'UPDATE "{table}" SET "{column}"=? WHERE rowid=?', (shifted, rowid))
        # AI weekly summaries may mention their original reporting period.
        for rowid, content in conn.execute("SELECT rowid,content FROM summaries").fetchall():
            shifted = re.sub(r"(?<![\d-])\d{4}-\d{2}-\d{2}(?![\d-])", lambda match: (date.fromisoformat(match[0]) + delta).isoformat(), content)
            def short_day(match: re.Match[str]) -> str:
                candidates = []
                for year in (anchor_day.year - 1, anchor_day.year, anchor_day.year + 1):
                    try:
                        candidates.append(date(year, int(match[1]), int(match[2])))
                    except ValueError:
                        continue
                if not candidates:
                    return match[0]
                original = min(candidates, key=lambda candidate: abs((candidate - anchor_day).days))
                return (original + delta).strftime("%m-%d")
            shifted = re.sub(r"(?<![\d-])(\d{2})-(\d{2})(?![\d-])", short_day, shifted)
            conn.execute("UPDATE summaries SET content=? WHERE rowid=?", (shifted, rowid))


def snapshot(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def restore_baseline(baseline: Path, data: Path, anchor: str, today: str) -> Path | None:
    """Caller holds the workspace lock. Preserve local credentials and snapshot old DB."""
    data.mkdir(parents=True, exist_ok=True)
    current = data / "knowflow.db"
    retained = []
    backup = None
    if current.exists():
        backup = data.parent / "backups" / (datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".sqlite3")
        snapshot(current, backup)
        conn = sqlite3.connect(current)
        conn.row_factory = sqlite3.Row
        try:
            retained = [dict(row) for row in conn.execute("SELECT * FROM model_configs")]
        finally:
            conn.close()
    staged = data / ("restore-" + os.urandom(6).hex() + ".sqlite3")
    snapshot(baseline, staged)
    conn = sqlite3.connect(staged)
    try:
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("演示数据库完整性检查失败，当前数据未替换。")
        if conn.execute("SELECT COUNT(*) FROM model_configs WHERE api_key <> ''").fetchone()[0]:
            raise RuntimeError("共享数据库意外包含 Key，拒绝导入。")
        with conn:
            conn.execute("DELETE FROM model_configs")
            for item in retained:
                cols = list(item)
                conn.execute("INSERT INTO model_configs (" + ",".join(cols) + ") VALUES (" + ",".join("?" for _ in cols) + ")", [item[col] for col in cols])
        shift_dates(conn, anchor, today)
        with conn:
            conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES ('demo_anchor',?)", (json.dumps(today),))
        if conn.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("演示数据关联检查失败，当前数据未替换。")
    finally:
        conn.close()
    # Use SQLite's backup API for a preexisting WAL database; replacing just its
    # main file could leave a stale WAL attached to the newly restored snapshot.
    snapshot(staged, current)
    staged.unlink()
    return backup


def unpack_assets(manifest: dict) -> None:
    if not ASSETS.exists():
        raise RuntimeError("缺少 demo/share/materials.zip，请完整拉取仓库后重试。")
    if sha(ASSETS) != manifest["assets_sha256"]:
        raise RuntimeError("演示素材包版本或校验不符，请使用与数据库配套的素材包。")
    allowed = manifest["assets"]
    with zipfile.ZipFile(ASSETS) as z:
        if set(z.namelist()) != set(allowed):
            raise RuntimeError("素材包文件清单不符，未解压。")
        for name, expected in allowed.items():
            parts = Path(name).parts
            if len(parts) != 2 or parts[0] not in ("materials", "现场素材") or any(p in (".", "..") for p in parts) or "\\" in name:
                raise RuntimeError("素材包路径不符合约定。")
            raw = z.read(name)
            if hashlib.sha256(raw).hexdigest() != expected:
                raise RuntimeError("素材文件校验失败：" + name)
            target = (DATA / name) if parts[0] == "materials" else (LOCAL / name)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() and sha(target) == expected:
                continue
            if target.exists():
                raise RuntimeError("已有同名素材与演示包不同，未覆盖：" + str(target))
            target.write_bytes(raw)


def preflight() -> list[str]:
    issues = []
    current = DATA / "knowflow.db"
    if not current.exists():
        return ["演示数据库尚未初始化。"]
    conn = sqlite3.connect(current.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        for table in ("courses", "notes", "materials", "cards", "reviews", "plans", "tasks"):
            print(f"  {table}: {conn.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]}")
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            issues.append("数据库完整性检查失败。")
        missing = [name for mid, ext, name in conn.execute("SELECT id,ext,original_name FROM materials") if not (DATA / "materials" / (mid + ext)).is_file()]
        if missing:
            issues.append("缺少资料原件：" + "、".join(missing) + "。请检查 demo/share/materials.zip 是否完整。")
        if not conn.execute("SELECT 1 FROM model_configs WHERE is_default=1 AND api_key<>''").fetchone():
            issues.append("尚未设置本机模型 Key；已有内容可以展示，现场 AI 生成前需在设置中配置。")
        anchor = conn.execute("SELECT value FROM settings WHERE key='demo_anchor'").fetchone()
        if anchor:
            print("  演示日期：" + json.loads(anchor[0]))
    finally:
        conn.close()
    return issues


@contextmanager
def workspace_lock():
    LOCAL.mkdir(parents=True, exist_ok=True)
    f = (LOCAL / "runner.lock").open("a+b")
    f.seek(0)
    if not f.read(1):
        f.write(b"0"); f.flush()
    f.seek(0)
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        raise RuntimeError("演示服务已在运行。请先关闭它，再执行重置或检查。") from None
    try:
        yield
    finally:
        f.close()


def main() -> int:
    if sys.version_info < (3, 11):
        raise RuntimeError("需要 Python 3.11 或更新版本，请安装后重试。")
    parser = argparse.ArgumentParser(
        description="KnowFlow 演示：独立数据库、保留 Key、自动平移复习与计划日期",
        epilog="日常工作区：python run.py --personal；开发模式：python run.py --dev。",
    )
    parser.add_argument("--reset", action="store_true", help="备份当前演示数据库后恢复起始场景，保留本机模型配置")
    parser.add_argument("--check", action="store_true", help="只检查数据和依赖，不启动网站、不调用模型")
    parser.add_argument("--date", type=date.fromisoformat, help="场景基准日，默认本机逻辑日（4点分界）")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-open", action="store_true")
    parser.add_argument("--dev", action="store_true", help="启动前端开发服务器，需要 Node.js 20.9+")
    parser.add_argument("--frontend-port", type=int, default=3000, help="开发前端首选端口")
    args = parser.parse_args()
    today = args.date or (datetime.now().astimezone() - timedelta(hours=4)).date()
    with workspace_lock():
        manifest = verified_manifest()
        # Validate and stage source files before a reset can replace the DB.
        unpack_assets(manifest)
        # First use initializes automatically; ordinary starts preserve rehearsals.
        if args.reset or not (DATA / "knowflow.db").exists():
            backup = restore_baseline(BASELINE, DATA, manifest["anchor_date"], today.isoformat())
            print("[KnowFlow 演示] 起始场景已就绪，日期：" + today.isoformat(), flush=True)
            if backup:
                print("[KnowFlow 演示] 上一场数据已备份：" + str(backup), flush=True)
        issues = preflight()
        for issue in issues:
            print("[准备提示] " + issue, flush=True)
        print("[KnowFlow 演示] 现场文件：" + str(LOCAL / "现场素材"), flush=True)
        if args.check:
            return 1 if issues else 0
        env = {**os.environ, "KNOWFLOW_DATA_DIR": str(DATA), "PYTHONIOENCODING": "utf-8"}
        command = [sys.executable, str(ROOT / "run.py"), "--port", str(args.port)]
        if args.dev:
            command.extend(["--dev", "--frontend-port", str(args.frontend_port)])
        if args.no_open:
            command.append("--no-open")
        print("[KnowFlow 演示] 数据仅写入 demo/.local；日常 backend/data 不受影响。", flush=True)
        return subprocess.call(command, cwd=ROOT, env=env)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(0)
    except (RuntimeError, OSError, sqlite3.Error, zipfile.BadZipFile) as exc:
        print("[KnowFlow 演示] " + str(exc), file=sys.stderr)
        raise SystemExit(1)
