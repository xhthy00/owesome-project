"""请求级 LLM token 累计，经 ContextVar 旁路上报 SSE（不改 LlmClient 返回类型）。"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from contextvars import ContextVar, Token
from typing import Any, AsyncIterator, Awaitable, Callable, Optional

EmitFn = Callable[[str, dict[str, Any]], Awaitable[None]]

_sink_var: ContextVar[Optional["UsageSink"]] = ContextVar("llm_usage_sink", default=None)

# 节流：同一累计值不重复推；最短间隔避免刷屏
_MIN_EMIT_INTERVAL_S = 0.2


def extract_usage_from_response(response: Any) -> tuple[int, int, int] | None:
    """从 LangChain AIMessage / 厂商 response 抽取 (prompt, completion, total)。"""
    if response is None:
        return None

    meta = getattr(response, "usage_metadata", None)
    if meta is not None:
        if isinstance(meta, dict):
            p = meta.get("input_tokens", meta.get("prompt_tokens"))
            c = meta.get("output_tokens", meta.get("completion_tokens"))
            t = meta.get("total_tokens")
        else:
            p = getattr(meta, "input_tokens", None) or getattr(meta, "prompt_tokens", None)
            c = getattr(meta, "output_tokens", None) or getattr(meta, "completion_tokens", None)
            t = getattr(meta, "total_tokens", None)
        parsed = _coerce_triple(p, c, t)
        if parsed is not None:
            return parsed

    resp_meta = getattr(response, "response_metadata", None) or {}
    if not isinstance(resp_meta, dict):
        return None
    token_usage = (
        resp_meta.get("token_usage")
        or resp_meta.get("usage")
        or resp_meta.get("tokenUsage")
        or {}
    )
    if not isinstance(token_usage, dict):
        return None
    return _coerce_triple(
        token_usage.get("prompt_tokens") or token_usage.get("input_tokens"),
        token_usage.get("completion_tokens") or token_usage.get("output_tokens"),
        token_usage.get("total_tokens"),
    )


def _coerce_triple(p: Any, c: Any, t: Any) -> tuple[int, int, int] | None:
    try:
        prompt = int(p or 0)
        completion = int(c or 0)
        total = int(t) if t is not None else prompt + completion
    except (TypeError, ValueError):
        return None
    if prompt <= 0 and completion <= 0 and total <= 0:
        return None
    if total <= 0:
        total = prompt + completion
    return prompt, completion, total


class UsageSink:
    def __init__(self, emit: EmitFn) -> None:
        self._emit = emit
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.started_at = time.monotonic()
        self._last_emit_total = -1
        self._last_emit_at = 0.0

    def snapshot_for_persist(self) -> dict[str, int]:
        elapsed_ms = int(max(0.0, time.monotonic() - self.started_at) * 1000)
        return {
            "total_tokens": int(self.total_tokens),
            "elapsed_ms": elapsed_ms,
        }

    async def record(self, prompt: int, completion: int, total: int) -> None:
        self.prompt_tokens += max(0, int(prompt))
        self.completion_tokens += max(0, int(completion))
        delta = max(0, int(total))
        if delta <= 0:
            delta = max(0, int(prompt)) + max(0, int(completion))
        self.total_tokens += delta
        await self._maybe_emit(delta_total=delta)

    async def _maybe_emit(self, *, delta_total: int, force: bool = False) -> None:
        now = time.monotonic()
        if self.total_tokens == self._last_emit_total and not force:
            return
        if (
            not force
            and self._last_emit_total >= 0
            and (now - self._last_emit_at) < _MIN_EMIT_INTERVAL_S
        ):
            return
        self._last_emit_total = self.total_tokens
        self._last_emit_at = now
        await self._emit(
            "usage",
            {
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "total_tokens": self.total_tokens,
                "delta_total": delta_total,
            },
        )


def get_usage_sink() -> UsageSink | None:
    return _sink_var.get()


@asynccontextmanager
async def usage_tracking(emit: EmitFn) -> AsyncIterator[UsageSink]:
    sink = UsageSink(emit)
    token: Token = _sink_var.set(sink)
    try:
        yield sink
    finally:
        # 结束时强制再推一次，避免节流丢掉最后增量
        try:
            if sink.total_tokens > 0 and sink.total_tokens != sink._last_emit_total:
                await sink._maybe_emit(delta_total=0, force=True)
        except Exception:  # noqa: BLE001 - 收尾上报失败不阻断主流程
            pass
        _sink_var.reset(token)


__all__ = [
    "UsageSink",
    "extract_usage_from_response",
    "get_usage_sink",
    "usage_tracking",
]
