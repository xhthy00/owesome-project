"""Tool call 审计写入（raw SQL + fire-and-forget）。

按当前阶段决策：
- 不引入 ORM model；
- 写入失败只打 warning，不影响主流程；
- 异步后台写（create_task + to_thread），避免阻塞 ReAct 回路。
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Any

from sqlalchemy import text

from src.common.core.database import get_db_session
from src.common.core.trace import get_trace_id

logger = logging.getLogger(__name__)

_INSERT_SQL = text(
    """
    INSERT INTO tool_call_log (
        trace_id,
        agent_name,
        round_idx,
        sub_task_index,
        tool_name,
        success,
        elapsed_ms,
        args_json,
        result_preview,
        error_code,
        error_detail,
        failure_domain,
        rule,
        created_at
    )
    VALUES (
        :trace_id,
        :agent_name,
        :round_idx,
        :sub_task_index,
        :tool_name,
        :success,
        :elapsed_ms,
        :args_json,
        :result_preview,
        :error_code,
        :error_detail,
        :failure_domain,
        :rule,
        :created_at
    )
    """
)


def _to_json(data: Any) -> str:
    try:
        return json.dumps(data, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001
        return json.dumps({"_repr": repr(data)}, ensure_ascii=False)


def _truncate(s: str, limit: int = 500) -> str:
    if len(s) <= limit:
        return s
    return s[: limit - 3] + "..."


def _insert_tool_call_log_sync(payload: dict[str, Any]) -> None:
    with get_db_session() as session:
        session.execute(_INSERT_SQL, payload)
        session.commit()


async def write_tool_call_log_async(
    *,
    agent_name: str,
    round_idx: int | None,
    sub_task_index: int | None,
    tool_name: str,
    success: bool,
    elapsed_ms: int | None,
    args: dict[str, Any] | None,
    result_preview: str,
    trace_id: str | None = None,
    error_code: str | None = None,
    error_detail: dict[str, Any] | None = None,
    failure_domain: str | None = None,
    rule: str | None = None,
) -> None:
    payload = {
        "trace_id": trace_id or get_trace_id(),
        "agent_name": agent_name or "",
        "round_idx": round_idx,
        "sub_task_index": sub_task_index,
        "tool_name": tool_name or "tool_call",
        "success": bool(success),
        "elapsed_ms": elapsed_ms,
        "args_json": _to_json(args or {}),
        "result_preview": _truncate(result_preview or ""),
        "error_code": error_code,
        "error_detail": _truncate(_to_json(error_detail), 4000) if error_detail else None,
        "failure_domain": failure_domain,
        "rule": rule,
        "created_at": datetime.now(),
    }
    await asyncio.to_thread(_insert_tool_call_log_sync, payload)


def log_tool_call_fire_and_forget(
    *,
    agent_name: str,
    round_idx: int | None,
    sub_task_index: int | None,
    tool_name: str,
    success: bool,
    elapsed_ms: int | None,
    args: dict[str, Any] | None,
    result_preview: str,
    error_code: str | None = None,
    error_detail: dict[str, Any] | None = None,
    failure_domain: str | None = None,
    rule: str | None = None,
) -> None:
    # Capture the trace id before leaving the request context; a fire-and-forget
    # task does not reliably inherit the logging context variable.
    trace_id = get_trace_id()
    task = asyncio.create_task(
        write_tool_call_log_async(
            agent_name=agent_name,
            round_idx=round_idx,
            sub_task_index=sub_task_index,
            tool_name=tool_name,
            success=success,
            elapsed_ms=elapsed_ms,
            args=args,
            result_preview=result_preview,
            trace_id=trace_id,
            error_code=error_code,
            error_detail=error_detail,
            failure_domain=failure_domain,
            rule=rule,
        )
    )

    def _done_callback(t: asyncio.Task[None]) -> None:
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            logger.warning("write tool_call_log failed: %s", exc)

    task.add_done_callback(_done_callback)

