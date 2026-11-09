"""Persist a DeepAgents turn for conversation history."""

import asyncio
import logging
import re
from typing import Any

from src.agent.adapter.usage_sink import get_usage_sink
from src.chat.schemas import ChatRequest

logger = logging.getLogger(__name__)
_THINK_BLOCK_RE = re.compile(r"<think\b[^>]*>.*?</think>", re.DOTALL | re.IGNORECASE)


def _compact_context_summary(text: str | None, limit: int = 400) -> str | None:
    """生成下一轮可用的短结论，排除思考块、HTML 与多余空白。"""
    value = _THINK_BLOCK_RE.sub("", str(text or "")).strip()
    if not value:
        return None
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    if not value:
        return None
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _persist_sync(
    *,
    request: ChatRequest,
    current_user_id: int,
    question: str,
    resolved_question: str | None = None,
    turn_type: str = "standalone",
    sql: str,
    sql_error: str | None,
    exec_result: dict[str, Any] | None,
    is_success: bool,
    reasoning: str,
    steps: list[dict[str, Any]],
    chart_type: str = "table",
    chart_config: dict[str, Any] | None = None,
    agent_mode: str | None = None,
    plans: list[str] | None = None,
    plan_states: list[dict[str, Any]] | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
    summary: str | None = None,
    total_tokens: int | None = None,
    elapsed_ms: int | None = None,
    workspace_oid: int = 1,
) -> int:
    """在工作线程里开短事务并写 record。失败吞掉返回 0。"""
    if not request.conversation_id:
        return 0
    try:
        from src.chat.crud import chat as chat_crud
        from src.common.core.database import get_db_session

        with get_db_session() as session:
            record = chat_crud.create_conversation_record(
                session=session,
                conversation_id=request.conversation_id,
                user_id=current_user_id,
                question=question,
                resolved_question=resolved_question or request.question,
                turn_type=turn_type,
                context_summary=_compact_context_summary(
                    summary or reasoning or sql_error
                ),
                sql=sql or None,
                sql_error=sql_error,
                exec_result=exec_result,
                chart_type=chart_type,
                chart_config=chart_config,
                is_success=is_success,
                reasoning=reasoning or None,
                steps=steps or None,
                agent_mode=agent_mode,
                plans=plans,
                plan_states=plan_states,
                tool_calls=tool_calls,
                summary=summary,
                total_tokens=total_tokens,
                elapsed_ms=elapsed_ms,
                workspace_oid=workspace_oid,
            )
            rid = record.id or 0

        return rid
    except Exception as e:  # noqa: BLE001
        logger.warning("persist chat record failed: %s", e)
        return 0


async def persist_turn(**kwargs: Any) -> int:
    sink = get_usage_sink()
    if sink is not None:
        usage = sink.snapshot_for_persist()
        kwargs.update(total_tokens=usage["total_tokens"] or None, elapsed_ms=usage["elapsed_ms"] or None)
        if sink.context:
            kwargs["exec_result"] = {**(kwargs.get("exec_result") or {}), "context_usage": sink.context}
    return await asyncio.to_thread(_persist_sync, **kwargs)
