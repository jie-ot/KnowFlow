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
from collections.abc import AsyncIterator, Callable
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
    "JSON": "模型返回的 JSON 无法解析",
    "STRUCTURE": "模型返回的字段或数据类型不符合要求",
    "TRUNCATED": "模型输出被截断，结果尚未完成，请缩小范围或增加输出上限后重试",
    "UNKNOWN": "服务商返回了未知错误",
}

RETRYABLE = {"RATE_LIMIT", "SERVER", "OVERLOADED", "TIMEOUT", "NETWORK", "EMPTY", "PARSE", "JSON", "STRUCTURE", "TRUNCATED"}


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


def _stream_choice(data: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    try:
        obj = json.loads(data)
    except json.JSONDecodeError:
        raise LLMError("JSON", "服务商返回了无法解析的流式 JSON") from None
    if not isinstance(obj, dict):
        raise LLMError("STRUCTURE", "流式响应应为 JSON 对象")
    if obj.get("error"):
        raise LLMError("UNKNOWN", detail=_provider_message(data))
    choices, usage = obj.get("choices"), obj.get("usage")
    if usage is not None and not isinstance(usage, dict):
        raise LLMError("STRUCTURE", "响应 usage 应为对象")
    if choices is None and usage is not None:
        return None, usage
    if not isinstance(choices, list) or (choices and not isinstance(choices[0], dict)):
        raise LLMError("STRUCTURE", "响应 choices 应为对象数组")
    if not choices:
        return None, usage
    choice = choices[0]
    delta = choice.get("delta")
    if delta is not None and not isinstance(delta, dict):
        raise LLMError("STRUCTURE", "响应 delta 应为对象")
    if delta and delta.get("content") is not None and not isinstance(delta["content"], str):
        raise LLMError("STRUCTURE", "响应 content 应为文字")
    if choice.get("finish_reason") is not None and not isinstance(choice["finish_reason"], str):
        raise LLMError("STRUCTURE", "响应 finish_reason 应为文字")
    return choice, usage


async def stream_chat(
    cfg: ModelConfig,
    messages: list[dict[str, str]],
    *,
    max_tokens: int,
    temperature: float,
    idle_timeout: float,
    total_timeout: float,
    json_mode: bool = False,
    before_request: Callable[[], None] | None = None,
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
    terminated = False
    saw_packet = False
    async with _semaphore():
        if before_request is not None:
            before_request()
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
                        saw_packet = True
                        if data == "[DONE]":
                            terminated = True
                            break
                        choice, usage = _stream_choice(data)
                        if usage:
                            yield "usage", usage
                        if choice is None:
                            continue
                        delta = (choice.get("delta") or {}).get("content")
                        if delta:
                            yield "delta", delta
                        if choice.get("finish_reason"):
                            terminated = True
                            yield "finish", choice["finish_reason"]
                    if not terminated:
                        if not saw_packet:
                            raise LLMError("EMPTY")
                        raise LLMError("TRUNCATED", "连接在完整结束标记之前关闭，结果尚未完成")
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
    terminated = False
    saw_packet = False
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
                        saw_packet = True
                        if data == "[DONE]":
                            terminated = True
                            break
                        choice, _usage = _stream_choice(data)
                        if choice is None:
                            continue
                        delta = choice.get("delta") or {}
                        if delta.get("content"):
                            yield "delta", delta["content"]
                        tool_calls = delta.get("tool_calls")
                        if tool_calls is not None and not isinstance(tool_calls, list):
                            raise LLMError("STRUCTURE", "工具调用应为数组")
                        for tc in tool_calls or []:
                            if not isinstance(tc, dict) or type(tc.get("index")) is not int or not 0 <= tc["index"] < 32:
                                raise LLMError("STRUCTURE", "工具调用 index 应为 0–31 的整数")
                            if tc.get("id") is not None and not isinstance(tc["id"], str):
                                raise LLMError("STRUCTURE", "工具调用 id 应为文字")
                            fn = tc.get("function")
                            if fn is not None and not isinstance(fn, dict):
                                raise LLMError("STRUCTURE", "工具调用 function 应为对象")
                            fn = fn or {}
                            if any(fn.get(key) is not None and not isinstance(fn[key], str) for key in ("name", "arguments")):
                                raise LLMError("STRUCTURE", "工具名称和参数应为文字")
                            slot = calls.setdefault(tc["index"], {"id": "", "name": "", "arguments": ""})
                            slot["id"] = tc.get("id") or slot["id"]
                            slot["name"] += fn.get("name") or ""
                            slot["arguments"] += fn.get("arguments") or ""
                            if len(slot["arguments"]) > 16_000 or len(slot["name"]) > 100:
                                raise LLMError("STRUCTURE", "工具调用名称或参数超过长度限制")
                        if choice.get("finish_reason"):
                            terminated = True
                            yield "finish", choice["finish_reason"]
                    if not terminated:
                        if not saw_packet:
                            raise LLMError("EMPTY")
                        raise LLMError("TRUNCATED", "连接在完整结束标记之前关闭，回答尚未完成")
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
    before_request: Callable[[], None] | None = None,
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
        if before_request is not None:
            before_request()
        try:
            async with asyncio.timeout(total_timeout):
                async with httpx.AsyncClient(timeout=_timeout(idle_timeout), transport=TRANSPORT) as client:
                    resp = await client.post(endpoint(cfg.base_url), json=payload,
                                             headers=_headers(cfg, False))
        except (httpx.HTTPError, TimeoutError) as exc:
            raise _wrap_transport_error(exc, cfg) from None
    if resp.status_code != 200:
        raise map_http_error(resp.status_code, resp.text)
    if not resp.content.strip():
        raise LLMError("EMPTY")
    try:
        data = resp.json()
    except ValueError:
        raise LLMError("JSON", "服务商响应不是有效 JSON", detail=resp.text[:300]) from None
    try:
        if not isinstance(data, dict) or not isinstance(data.get("choices"), list):
            raise TypeError
        choice = data["choices"][0]
        if not isinstance(choice, dict) or not isinstance(choice.get("message"), dict):
            raise TypeError
        content = choice["message"].get("content")
        if content is None:
            content = ""
        if not isinstance(content, str):
            raise TypeError
        if choice.get("finish_reason") is not None and not isinstance(choice["finish_reason"], str):
            raise TypeError
        if data.get("usage") is not None and not isinstance(data["usage"], dict):
            raise TypeError
    except (KeyError, IndexError, TypeError):
        raise LLMError("STRUCTURE", "服务商返回的不是 OpenAI 兼容格式", detail=resp.text[:300]) from None
    return ChatResult(
        content=content,
        finish_reason=choice.get("finish_reason"),
        usage=data.get("usage"),
        elapsed_ms=int((time.monotonic() - started) * 1000),
    )


def parse_json_object(text: str) -> dict[str, Any]:
    """从模型输出中取出 JSON 对象：容忍 ```json 包裹和前后多余文字。"""
    if not isinstance(text, str):
        raise LLMError("STRUCTURE", "模型正文应为文字")
    cleaned = text.strip()
    if not cleaned:
        raise LLMError("EMPTY")
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else ""
        if cleaned.rstrip().endswith("```"):
            cleaned = cleaned.rstrip()[:-3]
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end <= start:
            raise LLMError("JSON") from None
        try:
            value = json.loads(cleaned[start:end + 1])
        except json.JSONDecodeError:
            raise LLMError("JSON") from None
    if isinstance(value, list):
        value = {"items": value}
    if not isinstance(value, dict):
        raise LLMError("STRUCTURE", "模型 JSON 应为对象")
    return value
