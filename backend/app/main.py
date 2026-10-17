"""KnowFlow 后端入口：同时提供 /api 接口和编译好的前端网页。只监听 127.0.0.1。"""

from __future__ import annotations

import re
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

from . import config
from .db import connect, init_db, now_iso
from .errors import AppError, install_handlers
from .routers import ai, assistant, cards, courses, extras, materials, misc, notes, plans
from .seed import seed


_LOCAL = re.compile(config.LOCAL_ORIGIN_REGEX)


class GuardMiddleware:
    """为每个请求生成 request_id；拒绝来自非本机前端的写请求（同学 NFR-10）。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = str(uuid.uuid4())
        scope.setdefault("state", {})["request_id"] = request_id
        headers = dict(scope.get("headers") or [])
        origin = headers.get(b"origin", b"").decode()
        if scope["method"] not in ("GET", "HEAD", "OPTIONS") and origin and not _LOCAL.match(origin):
            body = b'{"error":{"code":"ORIGIN_FORBIDDEN","message":"\xe5\x8f\xaa\xe6\x8e\xa5\xe5\x8f\x97\xe6\x9c\xac\xe6\x9c\xba\xe5\x89\x8d\xe7\xab\xaf\xe7\x9a\x84\xe8\xaf\xb7\xe6\xb1\x82","retryable":false,"field":null}}'
            await send({"type": "http.response.start", "status": 403,
                        "headers": [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": body})
            return

        async def send_with_id(message):  # type: ignore[no-untyped-def]
            if message["type"] == "http.response.start":
                message.setdefault("headers", []).append((b"x-request-id", request_id.encode()))
            await send(message)

        await self.app(scope, receive, send_with_id)


def startup_tasks() -> None:
    is_new = init_db()
    conn = connect()
    try:
        with conn:
            # 服务重启时仍在进行的任务标为中断，需要手动重试（同学 DC-06）
            conn.execute("UPDATE ai_drafts SET status = 'interrupted', resolved_at = ? WHERE status = 'running'",
                         (now_iso(),))
            conn.execute("UPDATE materials SET status = 'failed', error_code = 'interrupted', "
                         "error_message = '解析被中断，请重试' WHERE status IN ('uploaded', 'extracting')")
            conn.execute("DELETE FROM ai_drafts WHERE resolved_at IS NOT NULL AND resolved_at < datetime('now', ?)",
                         (f"-{config.DRAFT_RETENTION_DAYS} days",))
        notes.purge_expired(conn)
        if is_new:
            seed(conn)
    finally:
        conn.close()


@asynccontextmanager
async def lifespan(_app: FastAPI):  # type: ignore[no-untyped-def]
    startup_tasks()
    yield


app = FastAPI(title="KnowFlow API", version=config.APP_VERSION, lifespan=lifespan)
app.add_middleware(GuardMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=config.LOCAL_ORIGIN_REGEX,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition", "X-Request-ID"],
)
install_handlers(app)
for module in (courses, notes, materials, cards, plans, ai, assistant, extras, misc):
    app.include_router(module.router)


@app.api_route("/api/{path:path}", methods=["GET", "POST", "PATCH", "PUT", "DELETE"], include_in_schema=False)
def api_not_found(path: str) -> None:
    raise AppError(404, "NOT_FOUND", "请求的接口不存在")


# 前端静态网页（npm run build 生成到 app/web/）；开发时前端由 next dev 单独提供
if config.WEB_DIR.exists():
    app.mount("/", StaticFiles(directory=config.WEB_DIR, html=True), name="web")
