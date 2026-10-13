"""Request-level trace_id 串联。

使用方式（单个请求入口处）::

    from src.common.core.trace import new_trace_id, trace_scope

    tid = new_trace_id()
    with trace_scope(tid):
        ...  # 本请求所有异步/线程调用的日志都会带上 [tid] 前缀

设计要点：

- 用 :mod:`contextvars.ContextVar` 实现 **async-aware** 的 per-request 隔离：
  FastAPI endpoint 并发处理多个请求时，每个请求看到自己的 trace_id；
  ``asyncio.to_thread`` / ``asyncio.create_task`` 会自动 copy context，
  所以 runner / tool / persist 线程里仍能拿到正确的 trace_id。

- 不依赖 logging.Filter——通过替换全局 ``LogRecordFactory`` 注入
  ``record.trace_id`` 属性。只要 log format 里写了 ``%(trace_id)s`` 就会渲染，
  无需每个模块手动 ``logger.info(..., extra={...})``。未配置 format 的第三方
  logger 完全不受影响。

- trace_id 默认是 12 位 hex（时间戳前缀 + 随机），短但足够唯一，可读性好。
"""

from __future__ import annotations

import logging
import secrets
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

# "-" 表示"当前不在任何请求上下文里"——直接 log 的也能渲染，不会留空
_NO_TRACE = "-"

_trace_id_var: ContextVar[str] = ContextVar("awesome_trace_id", default=_NO_TRACE)


def new_trace_id() -> str:
    """生成一个新的 trace_id。

    格式：``<base36 时间秒后 6 位><6 位随机 hex>``，总长 12。
    - 时间前缀保证**大致按时间排序**，日志聚合后易于定位时段；
    - 随机后缀保证同一秒内并发请求不冲突（冲突概率 < 1e-7）。
    """
    ts = int(time.time())
    # base36 截取最后 6 位：约 2^30 秒范围内不重复（~34 年），对可读日志足够
    ts_part = _to_base36(ts)[-6:].rjust(6, "0")
    rand_part = secrets.token_hex(3)  # 6 位 hex
    return f"{ts_part}{rand_part}"


def _to_base36(n: int) -> str:
    if n == 0:
        return "0"
    chars = "0123456789abcdefghijklmnopqrstuvwxyz"
    out: list[str] = []
    x = abs(n)
    while x:
        out.append(chars[x % 36])
        x //= 36
    return "".join(reversed(out))


def get_trace_id() -> str:
    """返回当前上下文的 trace_id；无值时返回 ``"-"``。"""
    return _trace_id_var.get()


def set_trace_id(trace_id: str) -> object:
    """设置当前上下文的 trace_id，返回 ``reset()`` 所需的 token。"""
    return _trace_id_var.set(trace_id or _NO_TRACE)


@contextmanager
def trace_scope(trace_id: str | None = None) -> Iterator[str]:
    """上下文管理器：进入时设置 trace_id，退出时恢复原值。

    Args:
        trace_id: 指定 trace_id；为 None 时自动生成。
    Yields:
        当前生效的 trace_id 字符串。
    """
    tid = trace_id or new_trace_id()
    token = _trace_id_var.set(tid)
    try:
        yield tid
    finally:
        _trace_id_var.reset(token)


_FACTORY_INSTALLED = False


def install_trace_log_factory() -> None:
    """替换全局 LogRecordFactory，给每条 LogRecord 注入 ``trace_id`` 属性。

    幂等：重复调用只生效一次；避免链式包装导致 trace_id 被覆盖/错位。

    调用方一般只需在进程启动时（main 或 lifespan startup）调一次。
    """
    global _FACTORY_INSTALLED
    if _FACTORY_INSTALLED:
        return

    old_factory = logging.getLogRecordFactory()

    def record_factory(*args, **kwargs):
        record = old_factory(*args, **kwargs)
        record.trace_id = _trace_id_var.get()
        return record

    logging.setLogRecordFactory(record_factory)
    _FACTORY_INSTALLED = True


class TraceIdMiddleware:
    """绑定请求 trace_id。用纯 ASGI，避免把下载流整段读进内存。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        tid = new_trace_id()

        async def send_with_trace(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                headers.append((b"x-trace-id", tid.encode("ascii")))
                message = {**message, "headers": headers}
            await send(message)

        with trace_scope(tid):
            await self.app(scope, receive, send_with_trace)


def _reset_factory_for_testing() -> None:
    """仅供测试：允许重跑 install 以观察幂等性。生产代码别调。"""
    global _FACTORY_INSTALLED
    _FACTORY_INSTALLED = False
