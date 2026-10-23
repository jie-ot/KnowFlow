"""Coordinate whole-dataset restore with requests and background generations.

The middleware must hold ``async_request_scope`` through streaming responses and
background tasks, excluding the restore endpoint itself. Restore runs in a worker
thread because it waits for those leases to drain. Generation checks additionally
reject work that was detached from an earlier request.
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from contextlib import asynccontextmanager, contextmanager
from collections.abc import AsyncIterator, Iterator

from ..errors import AppError

_condition = threading.Condition()
_active = 0
_restoring = False
_installing = False
_generation = 0
_process_id = uuid.uuid4().hex


def dataset_token() -> str:
    """An opaque token changes on restart and on every dataset replacement."""
    with _condition:
        return f"{_process_id}:{_generation}"


def current_generation() -> int:
    with _condition:
        return _generation


def assert_generation(generation: int) -> None:
    with _condition:
        if generation != _generation:
            raise AppError(409, "RESTORE_INTERRUPTED", "数据已恢复，旧任务已中断，请重新执行", retryable=True)


@contextmanager
def generation_scope(generation: int) -> Iterator[None]:
    """Lease a worker's short write atomically with its generation check.

    Existing requests may finish writes while restore is draining them. During
    actual installation no detached worker can acquire a lease, even if it has
    obtained the new generation number.
    """
    global _active
    with _condition:
        if generation != _generation or _installing:
            raise AppError(409, "RESTORE_INTERRUPTED", "数据已恢复或正在恢复，任务已中断，请重试", retryable=True)
        _active += 1
    try:
        yield
    finally:
        with _condition:
            _active -= 1
            _condition.notify_all()


@asynccontextmanager
async def async_request_scope() -> AsyncIterator[int]:
    global _active
    while True:
        with _condition:
            if not _restoring:
                _active += 1
                generation = _generation
                break
        # Never block the event loop while existing streams need it to finish.
        await asyncio.sleep(0.02)
    try:
        yield generation
    finally:
        with _condition:
            _active -= 1
            _condition.notify_all()


@contextmanager
def restore_scope() -> Iterator[int]:
    global _restoring, _installing, _generation
    with _condition:
        while _restoring:
            _condition.wait()
        _restoring = True
        try:
            while _active:
                _condition.wait()
            _generation += 1
            _installing = True
        except BaseException:
            _restoring = False
            _condition.notify_all()
            raise
    try:
        yield _generation
    finally:
        with _condition:
            _installing = False
            _restoring = False
            _condition.notify_all()
