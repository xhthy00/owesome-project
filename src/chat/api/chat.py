"""Chat API routes based on SQLBot patterns."""

import asyncio
import json
import logging
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from audit.service.decorators import audit_access
from chat.crud import chat as chat_crud
from chat.schemas import (
    ChatRequest,
    ConversationCreate,
    ConversationDetailResponse,
    ConversationRecordResponse,
    ConversationResponse,
    ConversationUpdate,
    RecordReportsReplace,
    ReportReviewUpdate,
    SQLFormatRequest,
    SQLValidationRequest,
)
from common.core.database import get_session
from common.core.trace import new_trace_id, set_trace_id
from common.exceptions.base import NotFoundException
from common.schemas.response import success_response
from src.datasource.edu_connection import edu_db_connection
from system.api.auth_deps import get_current_user
from system.schemas import UserResponse
from system.workspace_scope import assert_datasource_accessible, get_workspace_oid

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def _edu_out_of_scope_message(
    user_id: int,
    question: str,
    *,
    datasource_id: int | None = None,
    workspace_oid: int | None = None,
) -> str | None:
    """教师/校管点名越权班校时拒绝；读范围失败则放行。"""
    from datasource.service.edu_permission import edu_scope_dict_for_user_id
    from src.agent.education.scope_guard import out_of_scope_question

    return out_of_scope_question(
        edu_scope_dict_for_user_id(user_id),
        question,
        datasource_id=datasource_id,
        workspace_oid=workspace_oid,
        connection_factory=edu_db_connection,
    )


# ============== Conversation Management ==============

@router.post("/conversations", summary="Create conversation")
def create_conversation(
    chat_request: ConversationCreate,
    session: Session = Depends(get_session),
    current_user: UserResponse = Depends(get_current_user),
    workspace_oid: int = Depends(get_workspace_oid),
):
    """Create a new conversation."""
    conversation = chat_crud.create_conversation(
        session=session,
        user_id=current_user.id,
        title=chat_request.title,
        datasource_id=chat_request.datasource_id,
        oid=workspace_oid,
    )
    return success_response(
        data=ConversationResponse.model_validate(conversation),
        message="Conversation created successfully"
    )


@router.get("/conversations", summary="List conversations")
def list_conversations(
    limit: int = 50,
    session: Session = Depends(get_session),
    current_user: UserResponse = Depends(get_current_user),
    workspace_oid: int = Depends(get_workspace_oid),
):
    """List user's conversations（按账号全局可见，暂不按工作空间过滤）。"""
    conversations = chat_crud.list_conversations(
        session=session,
        user_id=current_user.id,
        oid=workspace_oid,
        limit=limit,
    )
    items = [ConversationResponse.model_validate(c) for c in conversations]
    return success_response(
        data={"total": len(items), "items": items},
        message="Conversations retrieved successfully"
    )


@router.get("/conversations/{conversation_id}", summary="Get conversation detail")
def get_conversation(
    conversation_id: int,
    session: Session = Depends(get_session),
    current_user: UserResponse = Depends(get_current_user),
    workspace_oid: int = Depends(get_workspace_oid),
):
    """Get conversation with records."""
    conversation = chat_crud.get_conversation_by_id(
        session=session,
        conversation_id=conversation_id,
        user_id=current_user.id,
        oid=workspace_oid,
    )
    if not conversation:
        raise NotFoundException("Conversation not found")

    records = chat_crud.get_conversation_records(
        session=session,
        conversation_id=conversation_id,
        user_id=current_user.id,
        for_history_detail=True,
    )

    def _parse_json(value, default):
        if value in (None, ""):
            return default
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return default

    # Parse JSON fields
    record_responses = []
    for record in records:
        record_dict = {
            "id": record.id,
            "conversation_id": record.conversation_id,
            "user_id": record.user_id,
            "question": record.question,
            "resolved_question": getattr(record, "resolved_question", None),
            "turn_type": getattr(record, "turn_type", "standalone") or "standalone",
            "parent_record_id": getattr(record, "parent_record_id", None),
            "context_summary": getattr(record, "context_summary", None),
            "sql": record.sql,
            "sql_answer": record.sql_answer,
            "sql_error": record.sql_error,
            "exec_result": _parse_json(record.exec_result, None),
            "chart_type": record.chart_type,
            "chart_config": _parse_json(record.chart_config, None),
            "is_success": record.is_success,
            "finish_time": record.finish_time,
            "create_time": record.create_time,
            "reasoning": getattr(record, "reasoning", "") or "",
            "steps": _parse_json(getattr(record, "steps", None), []),
            "agent_mode": getattr(record, "agent_mode", None),
            "plans": _parse_json(getattr(record, "plans", None), None),
            "sub_task_agents": _parse_json(getattr(record, "sub_task_agents", None), None),
            "plan_states": _parse_json(getattr(record, "plan_states", None), None),
            "tool_calls": _parse_json(getattr(record, "tool_calls", None), None),
            "summary": getattr(record, "summary", None),
            "reports": _parse_json(getattr(record, "reports", None), None),
            "total_tokens": getattr(record, "total_tokens", None),
            "elapsed_ms": getattr(record, "elapsed_ms", None),
        }
        record_responses.append(ConversationRecordResponse(**record_dict))

    response = ConversationDetailResponse(
        id=conversation.id,
        user_id=conversation.user_id,
        title=conversation.title,
        datasource_id=conversation.datasource_id,
        datasource_name=conversation.datasource_name or "",
        db_type=conversation.db_type or "",
        oid=int(getattr(conversation, "oid", 1) or 1),
        create_time=conversation.create_time,
        update_time=conversation.update_time,
        records=record_responses
    )

    return success_response(data=response, message="Conversation retrieved successfully")


@router.put("/conversations/{conversation_id}", summary="Update conversation")
def update_conversation(
    conversation_id: int,
    chat_request: ConversationUpdate,
    session: Session = Depends(get_session),
    current_user: UserResponse = Depends(get_current_user),
    workspace_oid: int = Depends(get_workspace_oid),
):
    """Update a conversation."""
    conversation = chat_crud.update_conversation(
        session=session,
        conversation_id=conversation_id,
        user_id=current_user.id,
        oid=workspace_oid,
        title=chat_request.title,
        datasource_id=chat_request.datasource_id
    )
    if not conversation:
        raise NotFoundException("Conversation not found")

    return success_response(
        data=ConversationResponse.model_validate(conversation),
        message="Conversation updated successfully"
    )


@router.delete("/conversations/{conversation_id}", summary="Delete conversation")
def delete_conversation(
    conversation_id: int,
    session: Session = Depends(get_session),
    current_user: UserResponse = Depends(get_current_user),
    workspace_oid: int = Depends(get_workspace_oid),
):
    """Delete a conversation."""
    success = chat_crud.delete_conversation(
        session=session,
        conversation_id=conversation_id,
        user_id=current_user.id,
        oid=workspace_oid,
    )
    if not success:
        raise NotFoundException("Conversation not found")

    return success_response(message="Conversation deleted successfully")


@router.patch(
    "/conversations/{conversation_id}/records/{record_id}/reports/{report_index}",
    summary="Update report recommendations or review status",
)
def update_report_review(
    conversation_id: int,
    record_id: int,
    report_index: int,
    chat_request: ReportReviewUpdate,
    session: Session = Depends(get_session),
    current_user: UserResponse = Depends(get_current_user),
    workspace_oid: int = Depends(get_workspace_oid),
):
    """编辑报告建议区或审核通过。审核通过后不可再改建议。"""
    conversation = chat_crud.get_conversation_by_id(
        session=session,
        conversation_id=conversation_id,
        user_id=current_user.id,
        oid=workspace_oid,
    )
    if not conversation:
        raise NotFoundException("Conversation not found")

    record = chat_crud.get_record_by_id(session, record_id, current_user.id)
    if not record or int(record.conversation_id) != int(conversation_id):
        raise NotFoundException("Conversation record not found")

    try:
        reports = json.loads(record.reports) if record.reports else []
    except (TypeError, ValueError):
        reports = []
    if not isinstance(reports, list):
        reports = []
    if report_index < 0 or report_index >= len(reports):
        try:
            tool_calls = json.loads(record.tool_calls) if record.tool_calls else []
        except (TypeError, ValueError):
            tool_calls = []
        from src.chat.utils.report_payload import coalesce_record_reports

        reports = coalesce_record_reports(reports, tool_calls)
    if not isinstance(reports, list) or report_index < 0 or report_index >= len(reports):
        raise HTTPException(status_code=404, detail="Report not found")

    item = reports[report_index]
    if not isinstance(item, dict):
        raise HTTPException(status_code=400, detail="Invalid report payload")

    current_status = str(item.get("review_status") or "pending")
    if current_status == "approved" and chat_request.recommendations_text is not None:
        raise HTTPException(status_code=400, detail="报告已审核，不可再编辑建议")

    from src.agent.education.report_edit import (
        has_recommendations_section,
        replace_recommendations_html,
    )

    html = str(item.get("html") or "")
    if chat_request.recommendations_text is not None:
        if not has_recommendations_section(html):
            raise HTTPException(status_code=400, detail="本报告无可编辑的建议区")
        item["html"] = replace_recommendations_html(html, chat_request.recommendations_text)
        item["recommendations_text"] = chat_request.recommendations_text

    if chat_request.review_status is not None:
        if chat_request.review_status == "approved" and current_status == "approved":
            pass
        elif chat_request.review_status == "pending" and current_status == "approved":
            raise HTTPException(status_code=400, detail="报告已审核，不可回退")
        else:
            item["review_status"] = chat_request.review_status

    if "review_status" not in item:
        item["review_status"] = "pending"

    reports[report_index] = item
    updated = chat_crud.update_conversation_record(
        session=session,
        record_id=record_id,
        user_id=current_user.id,
        reports=reports,
    )
    if not updated:
        raise NotFoundException("Conversation record not found")

    return success_response(
        data={
            "record_id": record_id,
            "report_index": report_index,
            "report": item,
        },
        message="Report updated successfully",
    )


@router.put(
    "/conversations/{conversation_id}/records/{record_id}/reports",
    summary="Replace all reports on a conversation record",
)
def replace_record_reports(
    conversation_id: int,
    record_id: int,
    chat_request: RecordReportsReplace,
    session: Session = Depends(get_session),
    current_user: UserResponse = Depends(get_current_user),
    workspace_oid: int = Depends(get_workspace_oid),
):
    conversation = chat_crud.get_conversation_by_id(
        session=session,
        conversation_id=conversation_id,
        user_id=current_user.id,
        oid=workspace_oid,
    )
    if not conversation:
        raise NotFoundException("Conversation not found")

    record = chat_crud.get_record_by_id(session, record_id, current_user.id)
    if not record or int(record.conversation_id) != int(conversation_id):
        raise NotFoundException("Conversation record not found")

    cleaned: list[dict] = []
    for item in chat_request.reports or []:
        if not isinstance(item, dict):
            continue
        row = dict(item)
        if "review_status" not in row:
            row["review_status"] = "pending"
        cleaned.append(row)

    updated = chat_crud.update_conversation_record(
        session=session,
        record_id=record_id,
        user_id=current_user.id,
        reports=cleaned,
    )
    if not updated:
        raise NotFoundException("Conversation record not found")

    return success_response(
        data={"record_id": record_id, "reports": cleaned},
        message="Reports replaced successfully",
    )


# ============== SQL Generation & Execution ==============

def _assert_query_scope(session, current_user, workspace_oid, chat_request):
    assert_datasource_accessible(session, current_user, chat_request.datasource_id, workspace_oid)
    if chat_request.conversation_id:
        conversation = chat_crud.get_conversation_by_id(
            session,
            int(chat_request.conversation_id),
            current_user.id,
            workspace_oid,
        )
        if conversation is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        if (
            conversation.datasource_id is not None
            and int(conversation.datasource_id) != int(chat_request.datasource_id)
        ):
            raise HTTPException(
                status_code=409,
                detail="Conversation datasource does not match the request",
            )

async def _semantic_sql_response(chat_request, http_request, session, current_user, workspace_oid, *, execute):
    from src.chat.service.deepagents_runtime import run_chat_stream

    _assert_query_scope(session, current_user, workspace_oid, chat_request)
    denied = _edu_out_of_scope_message(
        current_user.id, chat_request.question, datasource_id=chat_request.datasource_id,
        workspace_oid=workspace_oid,
    )
    events = {}
    steps = []
    record_id = 0

    async def emit(event, data):
        if event == "step":
            steps.append(data)
        else:
            events[event] = data

    if denied:
        events["error"] = {"error": denied}
    else:
        request = chat_request if execute else chat_request.model_copy(
            update={"conversation_id": None, "resume_interrupt_id": None},
        )
        try:
            record_id = await run_chat_stream(
                request=request, current_user_id=current_user.id, workspace_oid=workspace_oid,
                emit=emit, checkpointer=http_request.app.state.chat_checkpointer, execute=execute,
            )
        except Exception:
            logger.exception("DataSemantic SQL request failed")
            events["error"] = {"error": "语义问数执行失败，请稍后重试。"}

    plan = events.get("sql", {})
    clarification = events.get("clarify")
    error = events.get("error", {}).get("error", "")
    if not plan and not error:
        error = clarification["prompt"] if clarification else "本轮未生成 DataSemantic 查询计划。"
    data = {
        "sql": plan.get("sql", ""), "error": error, "chart_type": "table",
        **{key: plan[key] for key in ("bundle_id", "manifest_hash", "plan_request_id") if key in plan},
    }
    if execute:
        data.update(
            record_id=record_id, result=events.get("result"), steps=steps,
            reasoning=events.get("summary", {}).get("content", ""),
        )
        if clarification:
            data["clarify"] = clarification
    else:
        data.update(
            is_valid=bool(plan) and not error, formatted_sql=data["sql"],
            tables=plan.get("tables", []), brief="",
        )
    return success_response(data=data)


@router.post("/generate-sql")
@audit_access(datasource_id_arg="chat_request.datasource_id", query_arg="chat_request.question")
async def generate_sql(
    chat_request: ChatRequest,
    http_request: Request,
    session: Session = Depends(get_session),
    current_user: UserResponse = Depends(get_current_user),
    workspace_oid: int = Depends(get_workspace_oid),
):
    return await _semantic_sql_response(
        chat_request, http_request, session, current_user, workspace_oid, execute=False,
    )


@router.post("/execute-sql", summary="Generate and execute semantic SQL")
@audit_access(datasource_id_arg="chat_request.datasource_id", query_arg="chat_request.question")
async def execute_sql(
    chat_request: ChatRequest,
    http_request: Request,
    session: Session = Depends(get_session),
    current_user: UserResponse = Depends(get_current_user),
    workspace_oid: int = Depends(get_workspace_oid),
):
    return await _semantic_sql_response(
        chat_request, http_request, session, current_user, workspace_oid, execute=True,
    )


@router.post("/chat-stream", summary="Chat with streaming output")
@audit_access(datasource_id_arg="chat_request.datasource_id", query_arg="chat_request.question")
async def chat_stream(
    chat_request: ChatRequest,
    http_request: Request,
    session: Session = Depends(get_session),
    current_user: UserResponse = Depends(get_current_user),
    workspace_oid: int = Depends(get_workspace_oid),
):
    """Stream a DeepAgents turn, retaining the chat API and SSE event contract."""
    trace_id = new_trace_id()
    set_trace_id(trace_id)
    if chat_request.datasource_id or chat_request.conversation_id:
        _assert_query_scope(session, current_user, workspace_oid, chat_request)

    logger.info(
        "chat_stream start mode=%s ds=%s conv=%s user=%s q_len=%d",
        chat_request.agent_mode,
        chat_request.datasource_id,
        chat_request.conversation_id,
        current_user.id,
        len(chat_request.question or ""),
    )

    current_user_id = current_user.id
    denied = _edu_out_of_scope_message(
        int(current_user_id),
        chat_request.question,
        datasource_id=chat_request.datasource_id,
        workspace_oid=workspace_oid,
    )
    if denied:

        async def deny_stream() -> AsyncGenerator[str, None]:
            yield _sse_event("error", {"error": denied})
            yield _sse_event("done", {"record_id": 0})

        return StreamingResponse(
            deny_stream(),
            media_type="text/event-stream",
            headers={**_SSE_HEADERS, "X-Trace-Id": trace_id},
        )

    queue: asyncio.Queue = asyncio.Queue()
    sentinel = object()

    async def emit_async(event: str, data: dict) -> None:
        queue.put_nowait((event, data))

    async def run() -> None:
        from src.chat.service.deepagents_runtime import run_chat_stream

        record_id = 0
        try:
            record_id = await run_chat_stream(
                request=chat_request,
                current_user_id=current_user_id,
                workspace_oid=workspace_oid,
                emit=emit_async,
                checkpointer=http_request.app.state.chat_checkpointer,
            )
        except Exception:
            logger.exception("DeepAgents chat stream failed")
            await emit_async("error", {"error": "问数执行失败，请稍后重试。"})
        finally:
            await emit_async("done", {"record_id": record_id})
            queue.put_nowait(sentinel)

    background_task = asyncio.create_task(run())

    async def event_stream() -> AsyncGenerator[str, None]:
        try:
            while True:
                try:
                    item = await asyncio.wait_for(
                        queue.get(), timeout=_SSE_KEEPALIVE_INTERVAL
                    )
                except asyncio.TimeoutError:
                    # 刷新浏览器、中间代理和负载均衡器的空闲计时器。
                    yield ": keepalive\n\n"
                    continue
                if item is sentinel:
                    break
                event, data = item
                yield _sse_event(event, data)
        finally:
            if not background_task.done():
                background_task.cancel()
                await asyncio.gather(background_task, return_exceptions=True)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={**_SSE_HEADERS, "X-Trace-Id": trace_id},
    )


#: SSE 空闲心跳间隔（秒）。team 流水线在 LLM 调用 / SQL 执行 / 持久化等阶段会有
#: 数十秒无事件输出的"静默期"；浏览器到 :3001 之间链路上的中间网元（云 ELB /
#: 企业代理 / 运营商 NAT / Next 代理 body 超时）普遍有 30~60s 空闲超时，会在
#: 静默期把连接按"空闲"掐断——表现为前端"卡死"、但后端仍在跑且最终落库（刷新即
#: 看到完整结果）。每 15s 发一行 SSE 注释刷新所有中间网元空闲计时器；注释行被
#: 前端解析器跳过、不产生事件。本地无中间网元，故不复现。
_SSE_KEEPALIVE_INTERVAL = 15.0


def _sse_event(event: str, data: dict) -> str:
    """Format data as SSE event.

    ``default=str`` is a safety net for exotic DB types (Decimal / datetime /
    UUID) that can appear in ``tool_result.data`` or ``result`` rows — a single
    un-serializable value would otherwise tear down the whole SSE connection.
    """
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


@router.post("/validate-sql")
def validate_sql_endpoint(
    chat_request: SQLValidationRequest,
    session: Session = Depends(get_session),
):
    """
    Validate a SQL query without executing it.

    This endpoint validates SQL syntax and security.
    The input should be a SQL query, not a natural language question.
    """
    from chat.utils.sql_validator import validate_sql

    is_valid, error_msg = validate_sql(chat_request.sql)

    return success_response(
        data={
            "is_valid": is_valid,
            "error": error_msg,
        },
        message="SQL validation completed"
    )


@router.post("/format-sql")
def format_sql_endpoint(
    chat_request: SQLFormatRequest,
    session: Session = Depends(get_session),
    current_user: UserResponse = Depends(get_current_user),
    workspace_oid: int = Depends(get_workspace_oid),
):
    """
    Format a SQL query for specific database type.

    This endpoint formats SQL with proper indentation and keywords.
    The input should be a SQL query, not a natural language question.
    """
    from chat.utils.sql_validator import format_sql

    # Get datasource to determine database type
    datasource = None
    if chat_request.datasource_id:
        datasource = assert_datasource_accessible(
            session, current_user, chat_request.datasource_id, workspace_oid
        )

    db_type = datasource.type if datasource else "pg"

    formatted = format_sql(chat_request.sql, db_type)

    return success_response(
        data={
            "original_sql": chat_request.sql,
            "formatted_sql": formatted,
            "db_type": db_type,
        },
        message="SQL formatted successfully"
    )


# ============== Recent Questions ==============

@router.get("/recent-questions/{datasource_id}", summary="Get recent questions")
def get_recent_questions(
    datasource_id: int,
    limit: int = 10,
    session: Session = Depends(get_session),
    current_user=Depends(get_current_user),
):
    """Get recent questions for a datasource to suggest follow-up questions."""
    questions = chat_crud.get_recent_questions(
        session=session,
        datasource_id=datasource_id,
        user_id=current_user.id,
        limit=limit
    )
    return success_response(
        data={"questions": questions},
        message="Recent questions retrieved successfully"
    )
