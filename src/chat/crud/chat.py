"""Chat CRUD operations for conversation management."""

from datetime import datetime
from typing import List, Optional, Any
import json

from sqlalchemy import and_, desc
from sqlmodel import Session, select

from src.chat.models.conversation import Conversation, ConversationRecord

#: 分析工具「保存到报告历史」写入的会话标题前缀；此类会话不进入聊天历史列表。
ANALYSIS_REPORT_TITLE_PREFIX = "[分析工具]"


def create_conversation(
    session: Session,
    user_id: int,
    title: str = "",
    datasource_id: Optional[int] = None,
    datasource_name: str = "",
    db_type: str = "",
    oid: int = 1,
) -> Conversation:
    """Create a new conversation."""
    conversation = Conversation(
        user_id=user_id,
        title=title or datetime.now().strftime("%Y-%m-%d %H:%M"),
        datasource_id=datasource_id,
        datasource_name=datasource_name,
        db_type=db_type,
        oid=oid,
        create_time=datetime.now(),
        update_time=datetime.now(),
    )
    session.add(conversation)
    session.commit()
    session.refresh(conversation)
    return conversation


def get_conversation_by_id(
    session: Session, conversation_id: int, user_id: int, oid: int
) -> Optional[Conversation]:
    """Get conversation by ID（限定工作空间）。"""
    statement = select(Conversation).where(
        and_(
            Conversation.id == conversation_id,
            Conversation.user_id == user_id,
            Conversation.oid == oid,
            Conversation.is_deleted == False
        )
    )
    return session.exec(statement).first()


def list_conversations(session: Session, user_id: int, oid: int, limit: int = 50) -> List[Conversation]:
    """List user's conversations in a workspace.

    排除分析工具报告历史会话（标题前缀 ``[分析工具]`` 或含 analysis_tool 记录），
    避免与侧栏「历史会话」混在一起。
    """
    analysis_conv_ids = (
        select(ConversationRecord.conversation_id)
        .where(ConversationRecord.agent_mode == "analysis_tool")
        .distinct()
    )
    statement = (
        select(Conversation)
        .where(
            and_(
                Conversation.user_id == user_id,
                Conversation.oid == oid,
                Conversation.is_deleted == False,  # noqa: E712
                ~Conversation.title.like(f"{ANALYSIS_REPORT_TITLE_PREFIX}%"),
                ~Conversation.id.in_(analysis_conv_ids),
            )
        )
        .order_by(desc(Conversation.update_time))
        .limit(limit)
    )
    return list(session.exec(statement).all())


def update_conversation(
    session: Session,
    conversation_id: int,
    user_id: int,
    oid: int,
    title: Optional[str] = None,
    datasource_id: Optional[int] = None
) -> Optional[Conversation]:
    """Update conversation."""
    conversation = get_conversation_by_id(session, conversation_id, user_id, oid)
    if not conversation:
        return None

    if title is not None:
        conversation.title = title[:64] if title else ""
    if datasource_id is not None:
        conversation.datasource_id = datasource_id

    conversation.update_time = datetime.now()
    session.add(conversation)
    session.commit()
    session.refresh(conversation)
    return conversation


def delete_conversation(session: Session, conversation_id: int, user_id: int, oid: int) -> bool:
    """Soft delete a conversation."""
    conversation = get_conversation_by_id(session, conversation_id, user_id, oid)
    if not conversation:
        return False

    conversation.is_deleted = True
    conversation.update_time = datetime.now()
    session.add(conversation)
    session.commit()
    return True


def create_conversation_record(
    session: Session,
    conversation_id: int,
    user_id: int,
    question: str,
    sql: Optional[str] = None,
    sql_answer: Optional[str] = None,
    sql_error: Optional[str] = None,
    exec_result: Optional[Any] = None,
    chart_type: str = "table",
    chart_config: Optional[dict] = None,
    is_success: bool = True,
    reasoning: Optional[str] = None,
    steps: Optional[List[Any]] = None,
    agent_mode: Optional[str] = None,
    plans: Optional[List[str]] = None,
    sub_task_agents: Optional[List[str]] = None,
    plan_states: Optional[List[Any]] = None,
    tool_calls: Optional[List[Any]] = None,
    summary: Optional[str] = None,
    reports: Optional[List[Any]] = None,
    workspace_oid: int = 1,
) -> ConversationRecord:
    """Create a new conversation record。"""
    exec_result_str = None
    if exec_result is not None:
        if isinstance(exec_result, dict):
            exec_result_str = json.dumps(exec_result, ensure_ascii=False)
        else:
            exec_result_str = str(exec_result)

    chart_config_str = None
    if chart_config is not None:
        chart_config_str = json.dumps(chart_config, ensure_ascii=False)

    steps_str = None
    if steps:
        steps_str = json.dumps(steps, ensure_ascii=False)
    plans_str = json.dumps(plans, ensure_ascii=False) if plans is not None else None
    sub_task_agents_str = (
        json.dumps(sub_task_agents, ensure_ascii=False)
        if sub_task_agents is not None
        else None
    )
    plan_states_str = (
        json.dumps(plan_states, ensure_ascii=False) if plan_states is not None else None
    )
    tool_calls_str = (
        json.dumps(tool_calls, ensure_ascii=False) if tool_calls is not None else None
    )
    reports_str = json.dumps(reports, ensure_ascii=False) if reports is not None else None

    record = ConversationRecord(
        conversation_id=conversation_id,
        user_id=user_id,
        question=question,
        sql=sql,
        sql_answer=sql_answer,
        sql_error=sql_error,
        exec_result=exec_result_str,
        chart_type=chart_type,
        chart_config=chart_config_str,
        is_success=is_success,
        finish_time=datetime.now(),
        create_time=datetime.now(),
        reasoning=reasoning,
        steps=steps_str,
        agent_mode=agent_mode,
        plans=plans_str,
        sub_task_agents=sub_task_agents_str,
        plan_states=plan_states_str,
        tool_calls=tool_calls_str,
        summary=summary,
        reports=reports_str,
    )
    session.add(record)

    # Update conversation's update_time
    conversation = get_conversation_by_id(session, conversation_id, user_id, workspace_oid)
    if conversation:
        conversation.update_time = datetime.now()
        session.add(conversation)

    session.commit()
    session.refresh(record)
    return record


def get_conversation_records(
    session: Session,
    conversation_id: int,
    user_id: int,
    limit: int = 100
) -> List[ConversationRecord]:
    """Get conversation records."""
    statement = (
        select(ConversationRecord)
        .where(
            and_(
                ConversationRecord.conversation_id == conversation_id,
                ConversationRecord.user_id == user_id
            )
        )
        .order_by(ConversationRecord.create_time)
        .limit(limit)
    )
    return session.exec(statement).all()


def get_record_by_id(session: Session, record_id: int, user_id: int) -> Optional[ConversationRecord]:
    """Get a single conversation record."""
    statement = select(ConversationRecord).where(
        and_(
            ConversationRecord.id == record_id,
            ConversationRecord.user_id == user_id
        )
    )
    return session.exec(statement).first()


def update_conversation_record(
    session: Session,
    record_id: int,
    user_id: int,
    sql: Optional[str] = None,
    sql_answer: Optional[str] = None,
    sql_error: Optional[str] = None,
    exec_result: Optional[Any] = None,
    chart_type: Optional[str] = None,
    chart_config: Optional[dict] = None,
    is_success: Optional[bool] = None,
    reports: Optional[List[Any]] = None,
) -> Optional[ConversationRecord]:
    """Update a conversation record."""
    record = get_record_by_id(session, record_id, user_id)
    if not record:
        return None

    if sql is not None:
        record.sql = sql
    if sql_answer is not None:
        record.sql_answer = sql_answer
    if sql_error is not None:
        record.sql_error = sql_error
    if exec_result is not None:
        if isinstance(exec_result, dict):
            record.exec_result = json.dumps(exec_result, ensure_ascii=False)
        else:
            record.exec_result = str(exec_result)
    if chart_type is not None:
        record.chart_type = chart_type
    if chart_config is not None:
        record.chart_config = json.dumps(chart_config, ensure_ascii=False)
    if is_success is not None:
        record.is_success = is_success
    if reports is not None:
        record.reports = json.dumps(reports, ensure_ascii=False)

    record.finish_time = datetime.now()
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


def get_recent_questions(session: Session, datasource_id: int, user_id: int, limit: int = 10) -> List[str]:
    """Get recent questions for a datasource to suggest follow-up questions."""
    statement = (
        select(ConversationRecord.question)
        .where(
            and_(
                ConversationRecord.user_id == user_id,
                ConversationRecord.sql.isnot(None),
                ConversationRecord.sql_error.is_(None)
            )
        )
        .order_by(desc(ConversationRecord.create_time))
        .limit(limit)
    )
    results = session.exec(statement).all()
    return list(results)

def list_analysis_tool_records(
    session: Session,
    user_id: int,
    oid: int,
    limit: int = 50,
) -> list[tuple[ConversationRecord, Conversation]]:
    """列出分析工具保存的报告记录（agent_mode=analysis_tool）。"""
    statement = (
        select(ConversationRecord, Conversation)
        .join(Conversation, Conversation.id == ConversationRecord.conversation_id)
        .where(
            and_(
                ConversationRecord.user_id == user_id,
                Conversation.user_id == user_id,
                Conversation.oid == oid,
                Conversation.is_deleted == False,  # noqa: E712
                ConversationRecord.agent_mode == "analysis_tool",
            )
        )
        .order_by(desc(ConversationRecord.create_time))
        .limit(limit)
    )
    rows = session.exec(statement).all()
    return list(rows)
