"""Validate, stage and restore complete backups without partial live updates."""

from __future__ import annotations

import hashlib
import io
import json
import ntpath
import re
import shutil
import sqlite3
import stat
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from .. import config, db
from ..errors import AppError
from ..logic.timeutil import now_local
from .coordination import restore_scope

BACKUP_FORMAT = "knowflow-backup-1"
MAX_ARCHIVE_BYTES = 2_000_000_000
MAX_EXPANDED_BYTES = 4_000_000_000
MAX_ENTRY_BYTES = 2_000_000_000
MAX_ENTRIES = 50_000
_DEVICES = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def _bad(message: str = "备份文件不完整或格式不正确，没有恢复") -> AppError:
    return AppError(422, "BAD_BACKUP", message)


def _snapshot_db(target: Path, *, strip_keys: bool) -> None:
    src = db.connect()
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
    # A download holds a normal request lease, so it must not acquire the
    # exclusive restore gate. Instead copy, verify against the DB snapshot, and
    # retry a fresh snapshot if an ordinary concurrent delete changed its files.
    for attempt in range(3):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_copy, material_copy = root / "knowflow.db", root / "materials"
            material_copy.mkdir()
            try:
                _snapshot_db(db_copy, strip_keys=strip_keys)
                if config.MATERIALS_DIR.exists():
                    for path in config.MATERIALS_DIR.rglob("*"):
                        if path.is_symlink():
                            raise AppError(422, "INVALID_MATERIAL_PATH", "资料目录包含符号链接，无法完整备份")
                        if path.is_file():
                            relative = path.relative_to(config.MATERIALS_DIR)
                            _safe_parts(relative.as_posix())
                            target = material_copy / relative
                            target.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copyfile(path, target)
                snapshot = sqlite3.connect(db_copy)
                try:
                    _validate_material_files(snapshot, material_copy)
                finally:
                    snapshot.close()
            except AppError as exc:
                if exc.code != "BAD_BACKUP":
                    raise
                if attempt < 2:
                    continue
                raise AppError(409, "BACKUP_FAILED", "资料原件缺失、校验失败或正在变化，未生成备份，请检查资料后重试",
                               retryable=True) from exc
            except (OSError, sqlite3.Error) as exc:
                if attempt < 2:
                    continue
                raise AppError(409, "BACKUP_FAILED", "无法完整暂存数据库和资料，未生成备份，请稍后重试",
                               retryable=True) from exc
            # The ZIP only reads immutable temporary copies, never live files.
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("manifest.json", json.dumps({
                    "format": BACKUP_FORMAT, "app_version": config.APP_VERSION, "created_at": db.now_iso(),
                    "schema_version": len(db.MIGRATIONS), "api_keys_included": not strip_keys,
                }, ensure_ascii=False))
                zf.write(db_copy, "knowflow.db")
                for path in material_copy.rglob("*"):
                    if path.is_file():
                        zf.write(path, "materials/" + path.relative_to(material_copy).as_posix())
            return buf.getvalue()
    raise AssertionError("unreachable backup retry state")


def _safe_parts(name: str) -> tuple[str, ...]:
    """Apply Windows rules on every host, before resolving under the staging root."""
    if not name or "\\" in name or ntpath.splitdrive(name)[0] or name.startswith("/"):
        raise _bad("备份包含非法文件路径，没有恢复")
    parts = tuple(name.rstrip("/").split("/"))
    for part in parts:
        if (not part or part in (".", "..") or part[-1:] in (".", " ")
                or any(ord(c) < 32 or c in '<>:"|?*' for c in part)
                or part.split(".", 1)[0].upper() in _DEVICES):
            raise _bad("备份包含非法文件路径，没有恢复")
    return parts


def _schema(conn: sqlite3.Connection) -> dict[str, list[tuple[Any, ...]]]:
    names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
    return {name: list(conn.execute('PRAGMA table_info("' + name.replace('"', '""') + '")')) for name in names}


def _constraints(conn: sqlite3.Connection, table: str) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    """Column layouts alone do not describe cascade rules or uniqueness.

    Preserve SQLite's generated primary-key index names as well as explicit
    indexes. xinfo includes sort direction and collation; the stored definition
    additionally distinguishes partial-index predicates and expressions.
    """
    quoted_table = '"' + table.replace('"', '""') + '"'
    foreign_keys = sorted(tuple(row) for row in conn.execute(f"PRAGMA foreign_key_list({quoted_table})"))
    indexes = []
    for row in conn.execute(f"PRAGMA index_list({quoted_table})"):
        _, name, unique, origin, partial = row
        quoted_index = '"' + name.replace('"', '""') + '"'
        columns = tuple(tuple(column) for column in conn.execute(f"PRAGMA index_xinfo({quoted_index})"))
        definition = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?", (name,)).fetchone()
        indexes.append((name, unique, origin, partial, columns, definition[0] if definition else None))
    return foreign_keys, sorted(indexes)


_SQL_TOKENS = re.compile(r"--[^\r\n]*|/\*[\s\S]*?\*/|'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|`(?:``|[^`])*`|\[[^\]]*\]|[A-Za-z_][A-Za-z_0-9]*|\d+(?:\.\d+)?|[^\s]"
                         )


def _table_definition(conn: sqlite3.Connection, table: str) -> tuple[tuple[str, str], ...]:
    """Compare the app's migration-defined constraints, including CHECK clauses.

    PRAGMA table_info/FK/index omit CHECK and some table-level attributes. Token
    normalization tolerates formatting, comments, keyword case and identifier
    quoting while preserving string literals and expression structure.
    """
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone()
    tokens = []
    for match in _SQL_TOKENS.finditer(row[0] if row else ""):
        value = match.group()
        if value.startswith(("--", "/*")):
            continue
        if value.startswith("'"):
            tokens.append(("literal", value[1:-1].replace("''", "'")))
        elif value.startswith(('"', '`', '[')):
            quote = value[0]
            value = value[1:-1].replace(quote * 2, quote) if quote != '[' else value[1:-1]
            tokens.append(("word", value.casefold()))
        else:
            tokens.append(("word", value.casefold()))
    return tuple(tokens)


def _validate_material_files(conn: sqlite3.Connection, materials: Path) -> None:
    for mid, ext, size, digest in conn.execute("SELECT id, ext, size_bytes, sha256 FROM materials"):
        parts = _safe_parts(str(mid) + str(ext))
        if len(parts) != 1:
            raise _bad("备份中的资料文件名不合法")
        source = materials.joinpath(*parts)
        if not source.is_file() or source.stat().st_size != size:
            raise _bad("备份缺少资料原件或文件大小不匹配，没有恢复")
        with source.open("rb") as stream:
            actual_hash = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual_hash != digest:
            raise _bad("备份资料校验失败，没有恢复")


def _migrate_and_validate(path: Path, manifest: dict[str, Any], materials: Path) -> None:
    conn = sqlite3.connect(path)
    expected = sqlite3.connect(":memory:")
    try:
        conn.execute("PRAGMA trusted_schema = OFF")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if not 1 <= version <= len(db.MIGRATIONS):
            raise _bad("备份数据库版本不兼容，请使用相同或更新版本的 KnowFlow")
        declared = manifest.get("schema_version", version)  # backups before this field remain valid
        if type(declared) is not int or declared != version:
            raise _bad("备份声明的数据库版本与实际不一致")
        for migration in db.MIGRATIONS[:version]:
            expected.executescript(migration)
        actual_schema = _schema(conn)
        expected_schema = _schema(expected)
        if any(actual_schema.get(name) != columns for name, columns in expected_schema.items()):
            raise _bad("备份数据库表结构不完整或不兼容，没有恢复")
        if any(_constraints(conn, table) != _constraints(expected, table) for table in expected_schema):
            raise _bad("备份数据库的外键、级联规则或索引不完整或不兼容，没有恢复")
        if any(_table_definition(conn, table) != _table_definition(expected, table) for table in expected_schema):
            raise _bad("备份数据库的建表约束不完整或不兼容，没有恢复")
        # Application backups do not contain executable triggers/views. Reject
        # unexpected ones before migrations or copying local model credentials.
        unexpected = set(conn.execute("SELECT type, name, sql FROM sqlite_master WHERE type IN ('trigger', 'view')"))
        approved = set(expected.execute("SELECT type, name, sql FROM sqlite_master WHERE type IN ('trigger', 'view')"))
        if unexpected != approved:
            raise _bad("备份包含不兼容的数据库对象，没有恢复")
        if conn.execute("PRAGMA integrity_check").fetchall() != [("ok",)] or conn.execute("PRAGMA foreign_key_check").fetchone():
            raise _bad("备份里的数据库不完整，没有恢复")
        db.migrate_connection(conn)
        _validate_material_files(conn, materials)
        with conn:
            now = db.now_iso()
            conn.execute("UPDATE ai_drafts SET status = 'interrupted', error_kind = 'INTERRUPTED', "
                         "error_message = '备份中的任务无法续跑，请重新执行', resolved_at = ? "
                         "WHERE status = 'running'", (now,))
            conn.execute("UPDATE materials SET status = 'failed', error_code = 'interrupted', "
                         "error_message = '恢复后需重新解析，请重试', updated_at = ? "
                         "WHERE status IN ('uploaded', 'extracting')", (now,))
    except sqlite3.Error as exc:
        raise _bad("备份数据库损坏或迁移失败，没有恢复") from exc
    finally:
        expected.close()
        conn.close()


def _stage(raw: bytes, root: Path) -> tuple[Path, Path, dict[str, Any], int]:
    if len(raw) > MAX_ARCHIVE_BYTES:
        raise AppError(413, "TOO_LARGE", "备份文件太大")
    materials = root / "materials-incoming"
    materials.mkdir()
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_ENTRIES or sum(i.file_size for i in infos) > MAX_EXPANDED_BYTES:
                raise _bad("备份解压后的大小或文件数量超出限制")
            seen: set[str] = set()
            count = 0
            for info in infos:
                parts = _safe_parts(info.filename)
                normalized = "/".join(parts).casefold()
                if normalized in seen or info.file_size > MAX_ENTRY_BYTES or info.flag_bits & 1:
                    raise _bad("备份包含重复、加密或过大的文件")
                seen.add(normalized)
                kind = stat.S_IFMT(info.external_attr >> 16)
                if kind not in (0, stat.S_IFREG, stat.S_IFDIR):
                    raise _bad("备份包含不支持的文件类型")
                if parts == ("manifest.json",) and not info.is_dir():
                    if info.file_size > 64_000:
                        raise _bad("备份清单过大")
                    target = root / "manifest.json"
                elif parts == ("knowflow.db",) and not info.is_dir():
                    target = root / "incoming.db"
                elif parts[0] == "materials":
                    target = materials.joinpath(*parts[1:])
                    # Resolve even after lexical validation so the filesystem
                    # boundary is enforced independently of ZIP path syntax.
                    if not target.resolve().is_relative_to(materials.resolve()):
                        raise _bad("备份资料路径超出指定目录")
                    if not info.is_dir():
                        if len(parts) == 1:
                            raise _bad("备份资料目录格式不正确")
                        count += 1
                else:
                    raise _bad("备份包含未知文件，没有恢复")
                if info.is_dir():
                    if info.file_size:
                        raise _bad("备份目录条目包含非法内容")
                    with archive.open(info) as source:
                        source.read()  # verify even empty directory entries' CRC
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, target.open("xb") as output:
                    # Opening and fully reading each entry also verifies its CRC.
                    shutil.copyfileobj(source, output, length=1024 * 1024)
            manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            if not isinstance(manifest, dict) or manifest.get("format") != BACKUP_FORMAT:
                raise _bad()
            incoming = root / "incoming.db"
            if not incoming.is_file():
                raise _bad("备份缺少数据库")
        _migrate_and_validate(incoming, manifest, materials)
        return incoming, materials, manifest, count
    except AppError:
        raise
    except (OSError, ValueError, KeyError, RuntimeError, zipfile.BadZipFile, NotImplementedError, EOFError) as exc:
        raise _bad() from exc


def _replace_db(source: Path) -> None:
    src = sqlite3.connect(source)
    dst = db.connect()
    try:
        src.backup(dst)
    finally:
        src.close()
        dst.close()


def _retain_model_configs(incoming: Path) -> None:
    current = db.connect()
    staged = sqlite3.connect(incoming)
    try:
        rows = [dict(row) for row in current.execute("SELECT * FROM model_configs")]
        # Preserve existing convention: an empty local config list leaves the
        # backup's (normally keyless) configs available for reconfiguration.
        if rows:
            columns = list(rows[0])
            with staged:
                staged.execute("DELETE FROM model_configs")
                staged.executemany(
                    "INSERT INTO model_configs(" + ", ".join(columns) + ") VALUES (" + ", ".join("?" for _ in columns) + ")",
                    [[row[column] for column in columns] for row in rows],
                )
    finally:
        current.close()
        staged.close()


def restore_archive(raw: bytes) -> dict[str, Any]:
    """All validation precedes exclusive live mutation; both resources roll back."""
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".restore-", dir=config.DATA_DIR) as temp:
        root = Path(temp)
        incoming, staged_materials, manifest, count = _stage(raw, root)
        with restore_scope():
            _retain_model_configs(incoming)
            safety = config.DATA_DIR / ("恢复前自动备份-" + now_local().strftime("%Y%m%d-%H%M%S") + "-" + db.new_id()[:8] + ".zip")
            safety.write_bytes(build_backup(strip_keys=False))
            rollback_db = root / "rollback.db"
            _snapshot_db(rollback_db, strip_keys=False)
            old_materials = root / "materials-previous"
            had_materials = config.MATERIALS_DIR.exists()
            moved_old = False
            installed_new = False
            db_attempted = False
            try:
                if had_materials:
                    config.MATERIALS_DIR.rename(old_materials)
                    moved_old = True
                staged_materials.rename(config.MATERIALS_DIR)
                installed_new = True
                db_attempted = True
                _replace_db(incoming)
            except BaseException as exc:
                # A failed backup operation can have started writing the DB, so
                # restore its snapshot even when the replacement raised midway.
                rollback_errors: list[BaseException] = []
                if db_attempted:
                    try:
                        _replace_db(rollback_db)
                    except BaseException as rollback_error:
                        rollback_errors.append(rollback_error)
                if installed_new:
                    try:
                        config.MATERIALS_DIR.rename(root / "materials-failed")
                    except BaseException as rollback_error:
                        rollback_errors.append(rollback_error)
                if moved_old:
                    try:
                        old_materials.rename(config.MATERIALS_DIR)
                    except BaseException as rollback_error:
                        rollback_errors.append(rollback_error)
                if rollback_errors:
                    # Keep recovery files when the OS also refuses the rollback.
                    recovery = config.DATA_DIR / ("恢复回滚资料-" + db.new_id())
                    shutil.copytree(root, recovery)
                    raise AppError(500, "RESTORE_ROLLBACK_FAILED", "恢复及自动回滚失败，安全备份和回滚资料已保留",
                                   extra={"safety_copy": safety.name, "recovery_directory": recovery.name}) from rollback_errors[0]
                raise AppError(500, "RESTORE_FAILED", "恢复失败，原数据库和资料已回滚，可重试", retryable=True,
                               extra={"safety_copy": safety.name}) from exc
        return {"ok": True, "materials": count, "safety_copy": safety.name, "backup_created_at": manifest.get("created_at")}
