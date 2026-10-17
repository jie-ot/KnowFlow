"""统一错误格式（同学文档 6.1）：

{"error": {"code": "...", "message": "...", "retryable": false, "field": null, "request_id": "..."}}
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class AppError(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        field: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.retryable = retryable
        self.field = field
        self.extra = extra or {}


def not_found(what: str = "内容") -> AppError:
    return AppError(404, "NOT_FOUND", f"{what}不存在或已被删除")


def error_body(request: Request, code: str, message: str, *, retryable: bool = False,
               field: str | None = None, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "code": code,
        "message": message,
        "retryable": retryable,
        "field": field,
        "request_id": getattr(request.state, "request_id", None),
    }
    if extra:
        body.update(extra)
    return {"error": body}


def install_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            error_body(request, exc.code, exc.message, retryable=exc.retryable,
                       field=exc.field, extra=exc.extra),
            status_code=exc.status,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        loc = [str(p) for p in first.get("loc", []) if p not in ("body", "query", "path")]
        field = ".".join(loc) or None
        message = "提交的内容格式不正确"
        if field:
            message = f"字段 {field} 不符合要求：{first.get('msg', '')}"
        return JSONResponse(
            error_body(request, "VALIDATION", message, field=field), status_code=422
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = "NOT_FOUND" if exc.status_code == 404 else "HTTP_ERROR"
        message = "请求的地址不存在" if exc.status_code == 404 else str(exc.detail)
        return JSONResponse(error_body(request, code, message), status_code=exc.status_code)
