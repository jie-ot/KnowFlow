"""SQLite write transactions, including safe composition within a transaction."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from collections.abc import Iterator

from ..db import new_id


@contextmanager
def atomic_write(conn: sqlite3.Connection) -> Iterator[None]:
    """Lock before reading business state; never commit a caller's transaction."""
    if conn.in_transaction:
        savepoint = "operation_" + new_id().replace("-", "")
        conn.execute(f"SAVEPOINT {savepoint}")
        try:
            yield
        except BaseException:
            conn.execute(f"ROLLBACK TO {savepoint}")
            conn.execute(f"RELEASE {savepoint}")
            raise
        else:
            conn.execute(f"RELEASE {savepoint}")
    else:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            yield
