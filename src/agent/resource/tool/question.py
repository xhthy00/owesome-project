"""Request-bound Agentic question tool."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from src.agent.resource.tool.base import ToolParameter, ToolResult
from src.agent.resource.tool.function_tool import FunctionTool
from src.chat.service.agent_runner import EmitCallback
from src.chat.service.question_manager import QuestionManager, validate_questions

logger = logging.getLogger(__name__)


def _parse_questions(value: Any) -> list[dict[str, Any]]:
    parsed = value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("questions must be valid JSON") from exc
    if isinstance(parsed, dict):
        parsed = parsed.get("questions")
    return validate_questions(parsed)


def _format_answers(
    questions: list[dict[str, Any]],
    answers: list[list[str]],
) -> str:
    lines = ["The user answered the clarification questions:"]
    for question, values in zip(questions, answers):
        lines.append(f"- {question['header']}: {', '.join(values)}")
    return "\n".join(lines)


def _ctx_map(tool_runtime_ctx: dict[str, Any] | None) -> dict[str, Any]:
    return tool_runtime_ctx if isinstance(tool_runtime_ctx, dict) else {}


def _apply_choice_to_ctx(ctx: dict[str, Any], extra: dict[str, str]) -> None:
    from src.agent.education.direction_choice import merge_user_choices

    ctx["user_choices"] = merge_user_choices(ctx.get("user_choices"), extra)
    ctx["direction_asked"] = True
    report_type = extra.get("report_type")
    if report_type:
        route = dict(ctx.get("report_route") or {})
        route["report_type"] = report_type
        route["reason"] = "user_choice"
        ctx["report_route"] = route


def make_question(
    *,
    emit: EmitCallback,
    question_manager: QuestionManager,
    conversation_id: int | None,
    user_id: int,
    workspace_oid: int,
    timeout_seconds: float = 300,
) -> FunctionTool:
    """Create a question tool bound to one active chat request."""

    async def question(
        questions: str,
        tool_runtime_ctx: dict[str, Any] | None = None,
    ) -> ToolResult | str:
        """Ask the user one to three structured questions and wait for answers."""
        from src.agent.education.direction_choice import (
            apply_fallback_choice,
            apply_user_choice,
            da_question_mode,
            load_pending_choices,
            validate_question_payload,
        )

        ctx = _ctx_map(tool_runtime_ctx)
        pending = load_pending_choices(ctx.get("pending_choices"))
        if pending:
            choice, error = validate_question_payload(
                questions,
                pending,
                already_asked=bool(ctx.get("direction_asked")),
                user_choices=ctx.get("user_choices")
                if isinstance(ctx.get("user_choices"), dict)
                else None,
            )
            if error or choice is None:
                return ToolResult(content=error or "question 校验失败。", data={"error": "invalid_direction_question"})
            mode = da_question_mode()
            if mode == "off":
                return ToolResult(content="口径分叉未启用，请按原计划继续。", data={"error": "direction_question_disabled"})
            if mode == "shadow":
                extra = apply_fallback_choice(choice)
                _apply_choice_to_ctx(ctx, extra)
                logger.info(
                    "edu_da_question_shadow applied fallback kind=%s value=%s",
                    choice.kind,
                    extra,
                )
                return (
                    "shadow 已记录口径分叉，未向用户弹窗。"
                    f"请按 fallback={choice.fallback} 继续，禁止再次调用 question。"
                )
            payload = choice.question_payload()
            parsed = _parse_questions(payload)
            hitl = question_manager.create(
                conv_id=str(conversation_id or 0),
                user_id=user_id,
                workspace_oid=workspace_oid,
                questions=parsed,
            )
            await emit("question.asked", hitl.to_payload())
            logger.info(
                "HITL question asked request_id=%s conversation_id=%s headers=%s",
                hitl.request_id,
                hitl.conv_id,
                [item["header"] for item in parsed],
            )
            timed_out = False
            try:
                await asyncio.wait_for(hitl.event.wait(), timeout=timeout_seconds)
            except asyncio.TimeoutError:
                timed_out = True
                logger.info(
                    "HITL question timed out request_id=%s conversation_id=%s timeout_seconds=%s",
                    hitl.request_id,
                    hitl.conv_id,
                    timeout_seconds,
                )
                await emit(
                    "question.rejected",
                    {"request_id": hitl.request_id, "conv_id": hitl.conv_id},
                )
            finally:
                answers = hitl.answers
                rejected = hitl.rejected
                question_manager.remove(hitl.request_id)

            if timed_out or rejected or not answers:
                extra = apply_fallback_choice(choice)
                _apply_choice_to_ctx(ctx, extra)
                reason = "timed out" if timed_out else "dismissed"
                return (
                    f"User {reason}; proceeding with fallback={choice.fallback}. "
                    "禁止再次调用 question。"
                )
            extra = apply_user_choice(choice, answers[0][0] if answers[0] else choice.fallback)
            _apply_choice_to_ctx(ctx, extra)
            return _format_answers(parsed, answers)

        parsed = _parse_questions(questions)
        pending_hitl = question_manager.create(
            conv_id=str(conversation_id or 0),
            user_id=user_id,
            workspace_oid=workspace_oid,
            questions=parsed,
        )
        await emit("question.asked", pending_hitl.to_payload())
        logger.info(
            "HITL question asked request_id=%s conversation_id=%s headers=%s",
            pending_hitl.request_id,
            pending_hitl.conv_id,
            [item["header"] for item in parsed],
        )
        timed_out = False
        try:
            await asyncio.wait_for(pending_hitl.event.wait(), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            timed_out = True
            logger.info(
                "HITL question timed out request_id=%s conversation_id=%s timeout_seconds=%s",
                pending_hitl.request_id,
                pending_hitl.conv_id,
                timeout_seconds,
            )
            await emit(
                "question.rejected",
                {"request_id": pending_hitl.request_id, "conv_id": pending_hitl.conv_id},
            )
        finally:
            answers = pending_hitl.answers
            rejected = pending_hitl.rejected
            question_manager.remove(pending_hitl.request_id)

        if timed_out:
            return (
                f"Question timed out after {int(timeout_seconds)} seconds. "
                "Proceeding without user answer."
            )
        if rejected:
            return "The user dismissed the question. Proceeding without answer."
        return _format_answers(parsed, answers or [])

    return FunctionTool(
        fn=question,
        name="question",
        description=(
            "当范围约束含待确认口径，或存在多个同样合理且无法从数据、权限或上下文推断的"
            "分析方向时，向用户提出结构化选择并等待回答。"
            "不要用于查询数据库可获得的信息，不要询问学校/班级/考试/学生。"
        ),
        parameters=[
            ToolParameter(
                name="questions",
                type="string",
                description=(
                    "JSON 数组；每项含 question/header/options，"
                    "可选 multiple/custom，最多三项。"
                    "若范围约束给出了待确认口径 JSON，必须原样传入。"
                ),
                required=True,
            ),
            ToolParameter(
                name="tool_runtime_ctx",
                type="object",
                description="运行时上下文（由系统注入）",
                required=False,
            ),
        ],
    )


__all__ = ["make_question"]
