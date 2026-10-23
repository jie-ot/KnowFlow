"""Keep an operation's business changes and retry receipt in one transaction."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from typing import Any

from ..db import now_iso
from ..errors import AppError


def fingerprint(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def perform(conn: sqlite3.Connection, scope: str, operation_id: str | None,
            payload: dict[str, Any], action: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    signature = fingerprint(payload)
    with conn:
        # Read-modify-write operations and competing retries acquire the writer
        # lock before reading either the receipt or the business state.
        conn.execute("BEGIN IMMEDIATE")
        if operation_id:
            receipt = conn.execute("SELECT fingerprint, response_json FROM operation_receipts "
                                   "WHERE scope = ? AND operation_id = ?", (scope, operation_id)).fetchone()
            if receipt:
                if receipt["fingerprint"] != signature:
                    raise AppError(409, "IDEMPOTENCY_CONFLICT", "同一操作编号不能用于不同内容，请重试原操作")
                return json.loads(receipt["response_json"])
        result = action()
        if operation_id:
            conn.execute("INSERT INTO operation_receipts(scope, operation_id, fingerprint, response_json, created_at) "
                         "VALUES (?, ?, ?, ?, ?)",
                         (scope, operation_id, signature, json.dumps(result, ensure_ascii=False), now_iso()))
        return result
