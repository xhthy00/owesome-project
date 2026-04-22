"""Chat API routes based on SQLBot patterns."""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from typing import List, AsyncGenerator, Optional
import logging
import json
import asyncio
import time
from datetime import datetime

from common.core.database import get_session
from common.exceptions.base import NotFoundException, BadRequestException, UnauthorizedException
from common.schemas.response import success_response, ResponseModel
from chat.schemas import (
    ChatRequest,
    SQLValidationRequest,
    SQLFormatRequest,
    ConversationCreate,
    ConversationUpdate,
    ConversationResponse,
    ConversationListResponse,
    ConversationRecordResponse,
    ConversationDetailResponse,
    ChatResponse,
)
from chat.service.sql_generator import SQLGenerator
from chat.crud import chat as chat_crud

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])


# ============== Conversation Management ==============

@router.post("/conversations", summary="Create conversation")
def create_conversation(
    request: ConversationCreate,
    session: Session = Depends(get_session),
    current_user_id: int = 1  # TODO: Get from auth
):
    """Create a new conversation."""
    conversation = chat_crud.create_conversation(
        session=session,
        user_id=current_user_id,
        title=request.title,
        datasource_id=request.datasource_id,
    )
    return success_response(
        data=ConversationResponse.model_validate(conversation),
        message="Conversation created successfully"
    )


@router.get("/conversations", summary="List conversations")
def list_conversations(
    limit: int = 50,
    session: Session = Depends(get_session),
    current_user_id: int = 1  # TODO: Get from auth
):
    """List user's conversations."""
    conversations = chat_crud.list_conversations(
        session=session,
        user_id=current_user_id,
        limit=limit
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
    current_user_id: int = 1  # TODO: Get from auth
):
    """Get conversation with records."""
    conversation = chat_crud.get_conversation_by_id(
        session=session,
        conversation_id=conversation_id,
        user_id=current_user_id
    )
    if not conversation:
        raise NotFoundException("Conversation not found")

    records = chat_crud.get_conversation_records(
        session=session,
        conversation_id=conversation_id,
        user_id=current_user_id
    )

    # Parse JSON fields
    record_responses = []
    import json
    for record in records:
        try:
            steps = json.loads(record.steps) if getattr(record, "steps", None) else []
        except (TypeError, ValueError):
            steps = []
        record_dict = {
            "id": record.id,
            "conversation_id": record.conversation_id,
            "user_id": record.user_id,
            "question": record.question,
            "sql": record.sql,
            "sql_answer": record.sql_answer,
            "sql_error": record.sql_error,
            "exec_result": json.loads(record.exec_result) if record.exec_result else None,
            "chart_type": record.chart_type,
            "chart_config": json.loads(record.chart_config) if record.chart_config else None,
            "is_success": record.is_success,
            "finish_time": record.finish_time,
            "create_time": record.create_time,
            "reasoning": getattr(record, "reasoning", "") or "",
            "steps": steps,
        }
        record_responses.append(ConversationRecordResponse(**record_dict))

    response = ConversationDetailResponse(
        id=conversation.id,
        user_id=conversation.user_id,
        title=conversation.title,
        datasource_id=conversation.datasource_id,
        datasource_name=conversation.datasource_name or "",
        db_type=conversation.db_type or "",
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
    current_user_id: int = 1  # TODO: Get from auth
):
    """Update a conversation."""
    conversation = chat_crud.update_conversation(
        session=session,
        conversation_id=conversation_id,
        user_id=current_user_id,
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
    current_user_id: int = 1  # TODO: Get from auth
):
    """Delete a conversation."""
    success = chat_crud.delete_conversation(
        session=session,
        conversation_id=conversation_id,
        user_id=current_user_id
    )
    if not success:
        raise NotFoundException("Conversation not found")

    return success_response(message="Conversation deleted successfully")


# ============== SQL Generation & Execution ==============

@router.post("/generate-sql")
def generate_sql(
    request: ChatRequest,
    session: Session = Depends(get_session),
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
    generator = SQLGenerator()

    result = generator.generate_sql(
        question=request.question,
        datasource_id=request.datasource_id,
        session=session,
        need_title=True,
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
    current_user_id: int = 1,  # TODO: Get from auth
):
    """
    Generate and execute SQL from natural language question.

    This endpoint:
    1. Generates SQL using LLM with SQLBot patterns
    2. Executes the SQL on the target database
    3. Returns the results
    """
    from datasource.crud import crud_datasource
    from datasource.db.db import execute_sql as db_execute_sql
    from common.utils.aes import decrypt_conf
    import json

    # Get datasource
    datasource = crud_datasource.get_datasource_by_id(session, request.datasource_id)
    if not datasource:
        raise NotFoundException("Datasource not found")

    config = decrypt_conf(datasource.configuration) if datasource.configuration else {}

    # Generate SQL
    generator = SQLGenerator()
    result = generator.generate_sql(
        question=request.question,
        datasource_id=request.datasource_id,
        session=session,
        need_title=False,
    )

    reasoning = result.get("reasoning", "")
    steps = list(result.get("steps", []))

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
            reasoning=reasoning,
            steps=steps,
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

    _t_exec = time.time()
    success, message, exec_result = db_execute_sql(
        db_type=datasource.type,
        config=config,
        sql=result["sql"],
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
        current_user_id=current_user_id,
        request=request,
        question=request.question,
        sql=result["sql"],
        sql_error=None if success else message,
        exec_result=exec_result if success else None,
        chart_type=result.get("chart_type", "table"),
        is_success=success,
        reasoning=reasoning,
        steps=steps,
    )

    if not success:
        return success_response(
            data={
                "record_id": record_id,
                "sql": result["sql"],
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
            "sql": result["sql"],
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
        )
        return record.id or 0
    except Exception as e:
        logger.warning(f"Failed to persist record: {e}")
        return 0


# ============== Streaming Chat ==============

@router.post("/chat-stream", summary="Chat with streaming output")
async def chat_stream(
    request: ChatRequest,
    current_user_id: int = 1,  # TODO: Get from auth
):
    """
    Chat endpoint with Server-Sent Events (SSE) streaming output.

    Event order:
      step      → one for each pipeline step as it completes
      reasoning → LLM natural-language reasoning text (when extracted)
      sql       → generated SQL + chart_type
      result    → execution result {columns, rows, row_count}
      error     → terminal failure
      done      → terminates the stream, payload {record_id}

    Note: we intentionally do NOT use Depends(get_session) here — the pipeline
    runs in a worker thread and SQLAlchemy sessions must not be shared across
    threads. Instead the worker opens its own session via get_db_session().
    """
    from src.datasource.crud import crud_datasource
    from src.datasource.db.db import execute_sql as db_execute_sql
    from src.common.utils.aes import decrypt_conf
    from src.common.core.database import get_db_session

    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_event_loop()
    SENTINEL = object()

    def push(event: str, data: dict) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, (event, data))

    def run_pipeline() -> None:
        steps_acc = []
        reasoning_text = ""
        record_id = 0
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

                result = generator.generate_sql(
                    question=request.question,
                    datasource_id=request.datasource_id,
                    session=session,
                    need_title=False,
                    step_callback=on_step,
                    reasoning_callback=on_reasoning,
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
                    )
                    push("error", {"error": result["error"]})
                    return

                push("sql", {
                    "sql": result["sql"],
                    "formatted_sql": result.get("formatted_sql", ""),
                    "tables": result.get("tables", []),
                    "chart_type": result.get("chart_type", "table"),
                })

                datasource = crud_datasource.get_datasource_by_id(session, request.datasource_id)
                if not datasource:
                    push("error", {"error": "Datasource not found"})
                    return

                config = decrypt_conf(datasource.configuration) if datasource.configuration else {}

                t_exec = time.time()
                success, message, exec_result = db_execute_sql(
                    db_type=datasource.type,
                    config=config,
                    sql=result["sql"],
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
                    sql=result["sql"],
                    sql_error=None if success else message,
                    exec_result=exec_result if success else None,
                    chart_type=result.get("chart_type", "table"),
                    is_success=success,
                    reasoning=reasoning_text,
                    steps=steps_acc,
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

    asyncio.create_task(asyncio.to_thread(run_pipeline))

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
        }
    )


def _sse_event(event: str, data: dict) -> str:
    """Format data as SSE event."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


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
):
    """
    Format a SQL query for specific database type.

    This endpoint formats SQL with proper indentation and keywords.
    The input should be a SQL query, not a natural language question.
    """
    from chat.utils.sql_validator import format_sql
    from datasource.crud import crud_datasource

    # Get datasource to determine database type
    datasource = None
    if request.datasource_id:
        datasource = crud_datasource.get_datasource_by_id(session, request.datasource_id)

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
    current_user_id: int = 1  # TODO: Get from auth
):
    """Get recent questions for a datasource to suggest follow-up questions."""
    questions = chat_crud.get_recent_questions(
        session=session,
        datasource_id=datasource_id,
        user_id=current_user_id,
        limit=limit
    )
    return success_response(
        data={"questions": questions},
        message="Recent questions retrieved successfully"
    )
