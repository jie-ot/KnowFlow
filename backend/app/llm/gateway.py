"""大模型网关：唯一的对外网络出口（设计文档 M-05）。

- 只用 OpenAI 兼容的 /chat/completions 格式（D2），换服务商只改配置（NFR-11）。
- 两种超时：首字或相邻数据超过 idle_timeout 秒、或总时长超过 total_timeout 秒，任一触发即判超时。
- 不自动重试网络错误；只有解析失败（PARSE / EMPTY）由调用方自动重试 1 次。
- API Key 只出现在请求头里，不写日志、不返回前端（NFR-07 / 同学 NFR-09）。
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

from .. import config

_SEMAPHORES: dict[int, asyncio.Semaphore] = {}


def _semaphore() -> asyncio.Semaphore:
    """每个事件循环一个信号量，限制同时进行的 AI 请求数。"""
    loop_id = id(asyncio.get_running_loop())
    sem = _SEMAPHORES.get(loop_id)
    if sem is None:
        sem = _SEMAPHORES[loop_id] = asyncio.Semaphore(config.MAX_CONCURRENT_AI)
    return sem

# 测试时注入 httpx.MockTransport；正常运行为 None
TRANSPORT: httpx.AsyncBaseTransport | None = None

MESSAGES = {
    "CONTEXT_TOO_LONG": "内容超出模型的长度上限，请分段处理",
    "BAD_REQUEST": "请求参数有误，请检查模型名称等配置",
    "AUTH": "API Key 无效或没有权限，请检查模型配置",
    "BALANCE": "服务商账户余额不足",
    "RATE_LIMIT": "请求过于频繁，请稍后重试",
    "SERVER": "服务商出现故障，请稍后重试",
    "OVERLOADED": "服务商繁忙，请稍后重试",
    "TIMEOUT": "请求超时，没有在规定时间内收到结果",
    "NETWORK": "无法连接到服务商，请检查网络或 Base URL",
    "EMPTY": "模型没有返回内容",
    "PARSE": "模型返回的格式不正确",
    "UNKNOWN": "服务商返回了未知错误",
}

RETRYABLE = {"RATE_LIMIT", "SERVER", "OVERLOADED", "TIMEOUT", "NETWORK", "EMPTY", "PARSE"}


class LLMError(Exception):
    def __init__(self, kind: str, message: str | None = None, *, status: int | None = None,
                 detail: str | None = None) -> None:
        self.kind = kind
        self.message = message or MESSAGES.get(kind, MESSAGES["UNKNOWN"])
        self.status = status
        self.detail = detail
        self.retryable = kind in RETRYABLE
        super().__init__(self.message)

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "message": self.message, "retryable": self.retryable,
                "status": self.status, "detail": self.detail}


@dataclass
class ModelConfig:
    base_url: str
    api_key: str
    model: str
    json_mode: bool = False
    max_output_tokens: int = 8192


@dataclass
class ChatResult:
    content: str
    finish_reason: str | None
    usage: dict[str, Any] | None
    elapsed_ms: int


def endpoint(base_url: str) -> str:
    url = base_url.strip().rstrip("/")
    if url.endswith("/chat/completions"):
        return url
    return url + "/chat/completions"


def host_of(base_url: str) -> str:
    return urlparse(base_url.strip()).netloc or base_url


def _provider_message(body: str) -> str:
    try:
        data = json.loads(body)
        err = data.get("error") if isinstance(data, dict) else None
        if isinstance(err, dict):
            return str(err.get("message") or err)[:300]
        if isinstance(err, str):
            return err[:300]
        if isinstance(data, dict) and data.get("message"):
            return str(data["message"])[:300]
    except (json.JSONDecodeError, AttributeError):
        pass
    return body.strip()[:300]


def map_http_error(status: int, body: str) -> LLMError:
    detail = _provider_message(body)
    lowered = detail.lower()
    if status == 400 and ("length" in lowered or "token" in lowered or "context" in lowered):
        kind = "CONTEXT_TOO_LONG"
    elif status in (400, 404, 422):
        kind = "BAD_REQUEST"
    elif status in (401, 403):
        kind = "AUTH"
    elif status == 402:
        kind = "BALANCE"
    elif status == 429:
        kind = "RATE_LIMIT"
    elif status == 503:
        kind = "OVERLOADED"
    elif status >= 500:
        kind = "SERVER"
    else:
        kind = "UNKNOWN"
    return LLMError(kind, status=status, detail=detail or None)


def _headers(cfg: ModelConfig, stream: bool) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {cfg.api_key}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream" if stream else "application/json",
    }


def _timeout(idle: float) -> httpx.Timeout:
    return httpx.Timeout(connect=15.0, read=idle, write=30.0, pool=15.0)


def _wrap_transport_error(exc: Exception, cfg: ModelConfig) -> LLMError:
    if isinstance(exc, (httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout, TimeoutError)):
        return LLMError("TIMEOUT")
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
        return LLMError("NETWORK", f"无法连接到 {host_of(cfg.base_url)}，请检查网络或 Base URL")
    if isinstance(exc, httpx.UnsupportedProtocol):
        return LLMError("BAD_REQUEST", "Base URL 需要以 https:// 或 http:// 开头")
    if isinstance(exc, (httpx.RemoteProtocolError, httpx.ReadError)):
        return LLMError("NETWORK", "连接中途断开，请重试")
    return LLMError("UNKNOWN", detail=f"{type(exc).__name__}")


async def stream_chat(
    cfg: ModelConfig,
    messages: list[dict[str, str]],
    *,
    max_tokens: int,
    temperature: float,
    idle_timeout: float,
    total_timeout: float,
    json_mode: bool = False,
) -> AsyncIterator[tuple[str, Any]]:
    """流式调用。依次产出 ("delta", 文本)、("finish", 原因)、("usage", 用量)。

    总时长用截止时刻手动检查，而不是 asyncio.timeout：生成器在 yield 处挂起时，
    取消信号会落到调用方身上，无法与“用户关闭页面”区分。
    """
    payload = {
        "model": cfg.model,
        "messages": messages,
        "stream": True,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    deadline = time.monotonic() + total_timeout
    async with _semaphore():
        try:
            async with httpx.AsyncClient(timeout=_timeout(idle_timeout), transport=TRANSPORT) as client:
                async with client.stream("POST", endpoint(cfg.base_url), json=payload,
                                         headers=_headers(cfg, True)) as resp:
                    if resp.status_code != 200:
                        body = (await resp.aread()).decode("utf-8", errors="replace")
                        raise map_http_error(resp.status_code, body)
                    lines = resp.aiter_lines()
                    while True:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise LLMError("TIMEOUT")
                        try:
                            line = await asyncio.wait_for(anext(lines), timeout=min(idle_timeout, remaining))
                        except StopAsyncIteration:
                            break
                        line = line.strip()
                        if not line or line.startswith(":") or not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            obj = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        if not isinstance(obj, dict):
                            continue
                        if obj.get("error"):
                            raise LLMError("UNKNOWN", detail=_provider_message(data))
                        if obj.get("usage"):
                            yield "usage", obj["usage"]
                        choices = obj.get("choices") or []
                        if not choices:
                            continue
                        choice = choices[0]
                        delta = (choice.get("delta") or {}).get("content")
                        if delta:
                            yield "delta", delta
                        if choice.get("finish_reason"):
                            yield "finish", choice["finish_reason"]
        except LLMError:
            raise
        except (httpx.HTTPError, TimeoutError) as exc:
            raise _wrap_transport_error(exc, cfg) from None


async def stream_chat_tools(
    cfg: ModelConfig,
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None,
    max_tokens: int,
    temperature: float,
    idle_timeout: float,
    total_timeout: float,
) -> AsyncIterator[tuple[str, Any]]:
    """带工具调用的流式对话（OpenAI function calling 格式）。

    依次产出 ("delta", 文本)，最后产出 ("tool_calls", [...]) 和 ("finish", 原因)。
    """
    payload: dict[str, Any] = {
        "model": cfg.model,
        "messages": messages,
        "stream": True,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if tools:
        payload["tools"] = tools
    deadline = time.monotonic() + total_timeout
    calls: dict[int, dict[str, Any]] = {}
    async with _semaphore():
        try:
            async with httpx.AsyncClient(timeout=_timeout(idle_timeout), transport=TRANSPORT) as client:
                async with client.stream("POST", endpoint(cfg.base_url), json=payload,
                                         headers=_headers(cfg, True)) as resp:
                    if resp.status_code != 200:
                        body = (await resp.aread()).decode("utf-8", errors="replace")
                        raise map_http_error(resp.status_code, body)
                    lines = resp.aiter_lines()
                    while True:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise LLMError("TIMEOUT")
                        try:
                            line = await asyncio.wait_for(anext(lines), timeout=min(idle_timeout, remaining))
                        except StopAsyncIteration:
                            break
                        line = line.strip()
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            obj = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        if not isinstance(obj, dict):
                            continue
                        if obj.get("error"):
                            raise LLMError("UNKNOWN", detail=_provider_message(data))
                        choices = obj.get("choices") or []
                        if not choices:
                            continue
                        choice = choices[0]
                        delta = choice.get("delta") or {}
                        if delta.get("content"):
                            yield "delta", delta["content"]
                        for tc in delta.get("tool_calls") or []:
                            slot = calls.setdefault(int(tc.get("index", 0)), {"id": "", "name": "", "arguments": ""})
                            slot["id"] = tc.get("id") or slot["id"]
                            fn = tc.get("function") or {}
                            slot["name"] += fn.get("name") or ""
                            slot["arguments"] += fn.get("arguments") or ""
                        if choice.get("finish_reason"):
                            yield "finish", choice["finish_reason"]
        except LLMError:
            raise
        except (httpx.HTTPError, TimeoutError) as exc:
            raise _wrap_transport_error(exc, cfg) from None
    if calls:
        yield "tool_calls", [calls[i] for i in sorted(calls)]


async def chat(
    cfg: ModelConfig,
    messages: list[dict[str, str]],
    *,
    max_tokens: int,
    temperature: float,
    idle_timeout: float,
    total_timeout: float,
    json_mode: bool = False,
) -> ChatResult:
    """非流式调用。"""
    payload: dict[str, Any] = {
        "model": cfg.model,
        "messages": messages,
        "stream": False,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    started = time.monotonic()
    async with _semaphore():
        try:
            async with asyncio.timeout(total_timeout):
                async with httpx.AsyncClient(timeout=_timeout(idle_timeout), transport=TRANSPORT) as client:
                    resp = await client.post(endpoint(cfg.base_url), json=payload,
                                             headers=_headers(cfg, False))
        except (httpx.HTTPError, TimeoutError) as exc:
            raise _wrap_transport_error(exc, cfg) from None
    if resp.status_code != 200:
        raise map_http_error(resp.status_code, resp.text)
    try:
        data = resp.json()
        choice = data["choices"][0]
        content = (choice.get("message") or {}).get("content") or ""
    except (ValueError, KeyError, IndexError, TypeError):
        raise LLMError("PARSE", "服务商返回的不是 OpenAI 兼容格式", detail=resp.text[:300]) from None
    return ChatResult(
        content=content,
        finish_reason=choice.get("finish_reason"),
        usage=data.get("usage"),
        elapsed_ms=int((time.monotonic() - started) * 1000),
    )


def parse_json_object(text: str) -> dict[str, Any]:
    """从模型输出中取出 JSON 对象：容忍 ```json 包裹和前后多余文字。"""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else ""
        if cleaned.rstrip().endswith("```"):
            cleaned = cleaned.rstrip()[:-3]
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end <= start:
            raise LLMError("PARSE") from None
        try:
            value = json.loads(cleaned[start:end + 1])
        except json.JSONDecodeError:
            raise LLMError("PARSE") from None
    if isinstance(value, list):
        value = {"items": value}
    if not isinstance(value, dict):
        raise LLMError("PARSE")
    return value
