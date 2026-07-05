"""Chat API routes based on SQLBot patterns."""

import asyncio
import json
import logging
import time
from typing import AsyncGenerator, Optional

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from chat.crud import chat as chat_crud
from chat.schemas import (
    ChatRequest,
    ConversationCreate,
    ConversationDetailResponse,
    ConversationRecordResponse,
    ConversationResponse,
    ConversationUpdate,
    SQLFormatRequest,
    SQLValidationRequest,
)
from chat.service.sql_generator import SQLGenerator
from common.core.database import get_session
from common.core.trace import new_trace_id, set_trace_id
from common.exceptions.base import NotFoundException
from common.schemas.response import success_response
from system.api.system import get_current_user
from system.schemas import UserResponse
from system.workspace_scope import assert_datasource_accessible, get_workspace_oid

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])


# ============== Conversation Management ==============

@router.post("/conversations", summary="Create conversation")
def create_conversation(
    request: ConversationCreate,
    session: Session = Depends(get_session),
    current_user: UserResponse = Depends(get_current_user),
    workspace_oid: int = Depends(get_workspace_oid),
):
    """Create a new conversation."""
    conversation = chat_crud.create_conversation(
        session=session,
        user_id=current_user.id,
        title=request.title,
        datasource_id=request.datasource_id,
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
    """List user's conversations."""
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
        user_id=current_user.id
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
    request: ConversationUpdate,
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
        title=request.title,
        datasource_id=request.datasource_id
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


# ============== SQL Generation & Execution ==============

@router.post("/generate-sql")
def generate_sql(
    request: ChatRequest,
    session: Session = Depends(get_session),
    current_user: UserResponse = Depends(get_current_user),
    workspace_oid: int = Depends(get_workspace_oid),
):
    """
    Generate SQL from natural language question.

    This endpoint:
    1. Fetches the datasource schema
    2. Builds a prompt with schema information using SQLBot patterns
    3. Calls LLM to generate SQL
    4. Validates the generated SQL
    5. Returns the result with chart type and tables info
    """
    assert_datasource_accessible(session, current_user, request.datasource_id, workspace_oid)

    generator = SQLGenerator()

    result = generator.generate_sql(
        question=request.question,
        datasource_id=request.datasource_id,
        session=session,
        need_title=True,
        user_id=current_user.id,
    )

    if not result["is_valid"]:
        return success_response(
            data={
                "sql": result["sql"],
                "is_valid": False,
                "error": result["error"],
                "formatted_sql": "",
                "tables": result.get("tables", []),
                "chart_type": result.get("chart_type", "table"),
                "brief": result.get("brief", ""),
            },
            message="SQL generation failed"
        )

    return success_response(
        data={
            "sql": result["sql"],
            "is_valid": True,
            "error": "",
            "formatted_sql": result["formatted_sql"],
            "tables": result.get("tables", []),
            "chart_type": result.get("chart_type", "table"),
            "brief": result.get("brief", ""),
        },
        message="SQL generated successfully"
    )


@router.post("/execute-sql", summary="Generate and execute SQL")
def execute_sql(
    request: ChatRequest,
    session: Session = Depends(get_session),
    current_user: UserResponse = Depends(get_current_user),
    workspace_oid: int = Depends(get_workspace_oid),
):
    """
    Generate and execute SQL from natural language question.

    This endpoint:
    1. Generates SQL using LLM with SQLBot patterns
    2. Executes the SQL on the target database
    3. Returns the results
    """

    from common.utils.aes import decrypt_conf
    from datasource.db.db import execute_sql as db_execute_sql
    from datasource.service.query_permission import (
        apply_permissions_for_execute,
        filter_exec_result_by_column_permissions,
        validate_sql_column_permissions,
    )

    datasource = assert_datasource_accessible(session, current_user, request.datasource_id, workspace_oid)

    config = decrypt_conf(datasource.configuration) if datasource.configuration else {}

    # Generate SQL
    generator = SQLGenerator()
    result = generator.generate_sql(
        question=request.question,
        datasource_id=request.datasource_id,
        session=session,
        need_title=False,
        user_id=current_user.id,
    )

    reasoning = result.get("reasoning", "")
    steps = list(result.get("steps", []))

    if not result["is_valid"]:
        record_id = _persist_record(
            session=session,
            current_user_id=current_user.id,
            request=request,
            question=request.question,
            sql=result.get("sql", ""),
            sql_error=result.get("error", ""),
            exec_result=None,
            chart_type=result.get("chart_type", "table"),
            is_success=False,
            reasoning=reasoning,
            steps=steps,
            workspace_oid=workspace_oid,
            agent_mode="legacy",
        )
        return success_response(
            data={
                "record_id": record_id,
                "sql": result["sql"],
                "result": None,
                "error": result["error"],
                "chart_type": result.get("chart_type", "table"),
                "reasoning": reasoning,
                "steps": steps,
            },
            message="SQL generation failed"
        )

    sql_exec = apply_permissions_for_execute(
        session,
        current_user,
        int(datasource.id),
        datasource.type or "pg",
        result["sql"],
        result.get("tables"),
    )
    col_perm_err = validate_sql_column_permissions(
        session,
        current_user,
        int(datasource.id),
        datasource.type or "pg",
        sql_exec,
    )
    if col_perm_err:
        steps.append(
            {
                "name": "execute",
                "label": "执行 SQL",
                "status": "error",
                "elapsed_ms": 0,
                "detail": col_perm_err,
            }
        )
        record_id = _persist_record(
            session=session,
            current_user_id=current_user.id,
            request=request,
            question=request.question,
            sql=sql_exec,
            sql_error=col_perm_err,
            exec_result=None,
            chart_type=result.get("chart_type", "table"),
            is_success=False,
            reasoning=reasoning,
            steps=steps,
            workspace_oid=workspace_oid,
            agent_mode="legacy",
        )
        return success_response(
            data={
                "record_id": record_id,
                "sql": sql_exec,
                "result": None,
                "error": col_perm_err,
                "chart_type": result.get("chart_type", "table"),
                "reasoning": reasoning,
                "steps": steps,
            },
            message="SQL violates column permission",
        )

    _t_exec = time.time()
    success, message, exec_result = db_execute_sql(
        db_type=datasource.type,
        config=config,
        sql=sql_exec,
    )
    if success and isinstance(exec_result, dict):
        exec_result = filter_exec_result_by_column_permissions(
            session,
            current_user,
            int(datasource.id),
            datasource.type or "pg",
            sql_exec,
            exec_result,
        )
    steps.append({
        "name": "execute",
        "label": "执行 SQL",
        "status": "ok" if success else "error",
        "elapsed_ms": int((time.time() - _t_exec) * 1000),
        "detail": (
            f"返回 {exec_result.get('row_count', 0)} 行" if success and isinstance(exec_result, dict)
            else (message or "执行失败")
        ),
    })

    record_id = _persist_record(
        session=session,
        current_user_id=current_user.id,
        request=request,
        question=request.question,
        sql=sql_exec,
        sql_error=None if success else message,
        exec_result=exec_result if success else None,
        chart_type=result.get("chart_type", "table"),
        is_success=success,
        reasoning=reasoning,
        steps=steps,
        workspace_oid=workspace_oid,
        agent_mode="legacy",
    )

    if not success:
        return success_response(
            data={
                "record_id": record_id,
                "sql": sql_exec,
                "result": None,
                "error": message,
                "chart_type": result.get("chart_type", "table"),
                "reasoning": reasoning,
                "steps": steps,
            },
            message="SQL execution failed"
        )

    return success_response(
        data={
            "record_id": record_id,
            "sql": sql_exec,
            "result": exec_result,
            "error": "",
            "chart_type": result.get("chart_type", "table"),
            "reasoning": reasoning,
            "steps": steps,
        },
        message="Query executed successfully"
    )


# ============== Persistence helper ==============

def _persist_record(
    session: Session,
    current_user_id: int,
    request: ChatRequest,
    question: str,
    sql: Optional[str],
    sql_error: Optional[str],
    exec_result,
    chart_type: str,
    is_success: bool,
    reasoning: str,
    steps,
    workspace_oid: int,
    agent_mode: Optional[str] = None,
    plans: Optional[list[str]] = None,
    sub_task_agents: Optional[list[str]] = None,
    plan_states: Optional[list[dict]] = None,
    tool_calls: Optional[list[dict]] = None,
    summary: Optional[str] = None,
    reports: Optional[list[dict]] = None,
) -> int:
    """Persist a conversation record. Returns record_id (0 if no conversation)."""
    if not request.conversation_id:
        return 0
    try:
        record = chat_crud.create_conversation_record(
            session=session,
            conversation_id=request.conversation_id,
            user_id=current_user_id,
            question=question,
            sql=sql,
            sql_error=sql_error,
            exec_result=exec_result,
            chart_type=chart_type or "table",
            is_success=is_success,
            reasoning=reasoning or None,
            steps=steps or None,
            agent_mode=agent_mode,
            plans=plans,
            sub_task_agents=sub_task_agents,
            plan_states=plan_states,
            tool_calls=tool_calls,
            summary=summary,
            reports=reports,
            workspace_oid=workspace_oid,
        )
        return record.id or 0
    except Exception as e:
        logger.warning(f"Failed to persist record: {e}")
        return 0


# ============== Streaming Chat ==============

@router.post("/chat-stream", summary="Chat with streaming output")
async def chat_stream(
    request: ChatRequest,
    session: Session = Depends(get_session),
    current_user: UserResponse = Depends(get_current_user),
    workspace_oid: int = Depends(get_workspace_oid),
):
    """
    Chat endpoint with Server-Sent Events (SSE) streaming output.

    Backends (switched by ``request.agent_mode``):
      - "agent"  (default): ReAct DataAnalystAgent with tools
      - "team"            : DataAnalyst → Charter → Summarizer 线性 DAG
      - "legacy"          : single-shot SQLGenerator pipeline

    Event vocabulary (agent/team add the ``*`` rows, legacy uses the rest):
      step           → pipeline / ReAct step status
      reasoning      → LLM natural-language reasoning (legacy only)
      sql            → final SQL + chart_type (both modes)
      result         → execution result {columns, rows, row_count} (both modes)
      agent_thought* → each ReAct round's raw LLM output
      tool_call*     → before a tool runs
      tool_result*   → after a tool runs (success / failure both)
      final_answer*  → terminate tool emitted
      agent_speak†   → (team only) 下游 Agent 开始/结束的广播
      chart†         → (team only) Charter 推荐的 chart_type + chart_config
      summary†       → (team only) Summarizer 生成的最终中文结论
      error          → terminal failure
      done           → terminates the stream, payload {record_id}

    Response header ``X-Trace-Id`` 带有本次请求的 trace_id；同一 trace_id 会
    出现在后端所有日志里（``[<trace_id>]`` 前缀），便于前端与后端对齐排查。
    """
    trace_id = new_trace_id()
    set_trace_id(trace_id)
    if request.datasource_id:
        assert_datasource_accessible(session, current_user, request.datasource_id, workspace_oid)

    logger.info(
        "chat_stream start mode=%s ds=%s conv=%s user=%s q_len=%d",
        request.agent_mode,
        request.datasource_id,
        request.conversation_id,
        current_user.id,
        len(request.question or ""),
    )

    current_user_id = current_user.id

    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_event_loop()
    SENTINEL = object()

    def push(event: str, data: dict) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, (event, data))

    async def emit_async(event: str, data: dict) -> None:
        queue.put_nowait((event, data))

    async def run_agent_branch(team: bool) -> None:
        """单 Agent 或 team 模式的 SSE 执行入口。

        ``team=True`` 时调 :func:`run_team_stream`，跑 DataAnalyst → Charter →
        Summarizer；否则走单 Agent 路径。两者对外的 SSE 事件集是 team 超集
        agent，前端可以只订阅 agent 的那套也能跑。
        """
        record_id = 0
        try:
            from src.chat.service.agent_runner import (
                run_agent_stream,
                run_team_stream,
            )

            runner = run_team_stream if team else run_agent_stream
            record_id = await runner(
                request=request,
                current_user_id=current_user_id,
                emit=emit_async,
                enable_tool_agent=request.enable_tool_agent,
                workspace_oid=workspace_oid,
            )
        except Exception as e:  # noqa: BLE001
            logger.error(f"Agent chat stream error: {e}")
            await emit_async("error", {"error": str(e)})
        finally:
            await emit_async("done", {"record_id": record_id})
            queue.put_nowait(SENTINEL)

    def run_legacy_pipeline() -> None:
        from src.common.core.database import get_db_session
        from src.common.utils.aes import decrypt_conf
        from src.datasource.crud import crud_datasource
        from src.datasource.db.db import execute_sql as db_execute_sql
        from src.datasource.service.query_permission import (
            apply_permissions_for_execute,
            filter_exec_result_by_column_permissions,
            validate_sql_column_permissions,
        )
        from src.system.crud.crud_user import get_user_by_id

        steps_acc = []
        reasoning_text = ""
        record_id = 0
        uid = current_user_id
        try:
            with get_db_session() as session:
                generator = SQLGenerator()

                def on_step(step):
                    steps_acc.append(step)
                    push("step", step)

                def on_reasoning(text):
                    nonlocal reasoning_text
                    reasoning_text = text
                    push("reasoning", {"text": text})

                gen_kwargs: dict = {}
                from src.agent.education.prompt_context import (
                    build_education_prompt_extras,
                    is_education_question,
                )
                if is_education_question(request.question):
                    term, training = build_education_prompt_extras()
                    gen_kwargs["terminologies"] = term
                    gen_kwargs["data_training"] = training

                result = generator.generate_sql(
                    question=request.question,
                    datasource_id=request.datasource_id,
                    session=session,
                    need_title=False,
                    step_callback=on_step,
                    reasoning_callback=on_reasoning,
                    user_id=uid,
                    **gen_kwargs,
                )

                steps_acc = list(result.get("steps", steps_acc))
                reasoning_text = result.get("reasoning", reasoning_text) or reasoning_text

                if not result["is_valid"]:
                    record_id = _persist_record(
                        session=session,
                        current_user_id=current_user_id,
                        request=request,
                        question=request.question,
                        sql=result.get("sql", ""),
                        sql_error=result.get("error", ""),
                        exec_result=None,
                        chart_type=result.get("chart_type", "table"),
                        is_success=False,
                        reasoning=reasoning_text,
                        steps=steps_acc,
                        workspace_oid=workspace_oid,
                        agent_mode="legacy",
                    )
                    push("error", {"error": result["error"]})
                    return

                datasource = crud_datasource.get_datasource_by_id(session, request.datasource_id)
                if not datasource:
                    push("error", {"error": "Datasource not found"})
                    return

                config = decrypt_conf(datasource.configuration) if datasource.configuration else {}

                user_row = get_user_by_id(session, uid)
                sql_exec = apply_permissions_for_execute(
                    session,
                    user_row,
                    int(datasource.id),
                    datasource.type or "pg",
                    result["sql"],
                    result.get("tables"),
                )
                push("sql", {
                    "sql": sql_exec,
                    "formatted_sql": result.get("formatted_sql", ""),
                    "tables": result.get("tables", []),
                    "chart_type": result.get("chart_type", "table"),
                })
                col_perm_err = validate_sql_column_permissions(
                    session,
                    user_row,
                    int(datasource.id),
                    datasource.type or "pg",
                    sql_exec,
                )
                if col_perm_err:
                    exec_step = {
                        "name": "execute",
                        "label": "执行 SQL",
                        "status": "error",
                        "elapsed_ms": 0,
                        "detail": col_perm_err,
                    }
                    steps_acc.append(exec_step)
                    push("step", exec_step)
                    record_id = _persist_record(
                        session=session,
                        current_user_id=current_user_id,
                        request=request,
                        question=request.question,
                        sql=sql_exec,
                        sql_error=col_perm_err,
                        exec_result=None,
                        chart_type=result.get("chart_type", "table"),
                        is_success=False,
                        reasoning=reasoning_text,
                        steps=steps_acc,
                        workspace_oid=workspace_oid,
                        agent_mode="legacy",
                    )
                    push("error", {"error": col_perm_err})
                    return

                t_exec = time.time()
                success, message, exec_result = db_execute_sql(
                    db_type=datasource.type,
                    config=config,
                    sql=sql_exec,
                )
                if success and isinstance(exec_result, dict):
                    exec_result = filter_exec_result_by_column_permissions(
                        session,
                        user_row,
                        int(datasource.id),
                        datasource.type or "pg",
                        sql_exec,
                        exec_result,
                    )
                exec_step = {
                    "name": "execute",
                    "label": "执行 SQL",
                    "status": "ok" if success else "error",
                    "elapsed_ms": int((time.time() - t_exec) * 1000),
                    "detail": (
                        f"返回 {exec_result.get('row_count', 0)} 行"
                        if success and isinstance(exec_result, dict)
                        else (message or "执行失败")
                    ),
                }
                steps_acc.append(exec_step)
                push("step", exec_step)

                record_id = _persist_record(
                    session=session,
                    current_user_id=current_user_id,
                    request=request,
                    question=request.question,
                    sql=sql_exec,
                    sql_error=None if success else message,
                    exec_result=exec_result if success else None,
                    chart_type=result.get("chart_type", "table"),
                    is_success=success,
                    reasoning=reasoning_text,
                    steps=steps_acc,
                    workspace_oid=workspace_oid,
                    agent_mode="legacy",
                )

                if not success:
                    push("error", {"error": message})
                    return

                push("result", exec_result)
        except Exception as e:
            logger.error(f"Chat stream pipeline error: {e}")
            push("error", {"error": str(e)})
        finally:
            push("done", {"record_id": record_id})
            loop.call_soon_threadsafe(queue.put_nowait, SENTINEL)

    if request.agent_mode == "legacy":
        asyncio.create_task(asyncio.to_thread(run_legacy_pipeline))
    else:
        asyncio.create_task(run_agent_branch(team=request.agent_mode == "team"))

    async def event_stream() -> AsyncGenerator[str, None]:
        while True:
            item = await queue.get()
            if item is SENTINEL:
                break
            event, data = item
            yield _sse_event(event, data)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "X-Trace-Id": trace_id,
        }
    )


def _sse_event(event: str, data: dict) -> str:
    """Format data as SSE event.

    ``default=str`` is a safety net for exotic DB types (Decimal / datetime /
    UUID) that can appear in ``tool_result.data`` or ``result`` rows — a single
    un-serializable value would otherwise tear down the whole SSE connection.
    """
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


@router.post("/validate-sql")
def validate_sql_endpoint(
    request: SQLValidationRequest,
    session: Session = Depends(get_session),
):
    """
    Validate a SQL query without executing it.

    This endpoint validates SQL syntax and security.
    The input should be a SQL query, not a natural language question.
    """
    from chat.utils.sql_validator import validate_sql

    is_valid, error_msg = validate_sql(request.sql)

    return success_response(
        data={
            "is_valid": is_valid,
            "error": error_msg,
        },
        message="SQL validation completed"
    )


@router.post("/format-sql")
def format_sql_endpoint(
    request: SQLFormatRequest,
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
    if request.datasource_id:
        datasource = assert_datasource_accessible(
            session, current_user, request.datasource_id, workspace_oid
        )

    db_type = datasource.type if datasource else "pg"

    formatted = format_sql(request.sql, db_type)

    return success_response(
        data={
            "original_sql": request.sql,
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
