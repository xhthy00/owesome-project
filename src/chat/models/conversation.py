"""Chat models for conversation history storage."""

from datetime import datetime
from typing import Optional, List
from sqlmodel import SQLModel, Field
from sqlalchemy import Column, BigInteger, DateTime, Boolean, Text, Integer


class Conversation(SQLModel, table=True):
    """Chat conversation model."""
    __tablename__ = "chat_conversation"

    id: Optional[int] = Field(default=None, sa_column=Column(BigInteger, primary_key=True, autoincrement=True))
    user_id: int = Field(default=0, sa_column=Column(BigInteger, nullable=False, index=True))
    title: str = Field(default="", max_length=64, sa_column=Column(Text))
    datasource_id: Optional[int] = Field(default=None, sa_column=Column(BigInteger, nullable=True))
    datasource_name: Optional[str] = Field(default="", sa_column=Column(Text))
    db_type: Optional[str] = Field(default="", sa_column=Column(Text))
    create_time: Optional[datetime] = Field(default_factory=datetime.now, sa_column=Column(DateTime(timezone=False)))
    update_time: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=False)))
    is_deleted: bool = Field(default=False, sa_column=Column(Boolean))


class ConversationRecord(SQLModel, table=True):
    """Chat conversation record model for storing chat messages."""
    __tablename__ = "chat_conversation_record"

    id: Optional[int] = Field(default=None, sa_column=Column(BigInteger, primary_key=True, autoincrement=True))
    conversation_id: int = Field(default=0, sa_column=Column(BigInteger, nullable=False, index=True))
    user_id: int = Field(default=0, sa_column=Column(BigInteger, nullable=False, index=True))
    question: str = Field(default="", sa_column=Column(Text))
    sql: Optional[str] = Field(default=None, sa_column=Column(Text))
    sql_answer: Optional[str] = Field(default=None, sa_column=Column(Text))
    sql_error: Optional[str] = Field(default=None, sa_column=Column(Text))
    exec_result: Optional[str] = Field(default=None, sa_column=Column(Text))
    chart_type: Optional[str] = Field(default="table", sa_column=Column(Text))
    chart_config: Optional[str] = Field(default=None, sa_column=Column(Text))
    is_success: bool = Field(default=True, sa_column=Column(Boolean))
    finish_time: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=False)))
    create_time: Optional[datetime] = Field(default_factory=datetime.now, sa_column=Column(DateTime(timezone=False)))
    reasoning: Optional[str] = Field(default=None, sa_column=Column(Text))
    steps: Optional[str] = Field(default=None, sa_column=Column(Text))
