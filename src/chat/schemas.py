"""Chat schemas for request/response validation."""

from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


# ============== Conversation Schemas ==============

class ConversationCreate(BaseModel):
    """Create conversation request."""
    title: str = Field(default="", max_length=64, description="Conversation title")
    datasource_id: Optional[int] = Field(None, description="Associated datasource ID")


class ConversationUpdate(BaseModel):
    """Update conversation request."""
    title: Optional[str] = Field(None, max_length=64, description="New conversation title")
    datasource_id: Optional[int] = Field(None, description="New datasource ID")


class ConversationResponse(BaseModel):
    """Conversation response."""
    id: int
    user_id: int
    title: str
    datasource_id: Optional[int] = None
    datasource_name: Optional[str] = ""
    db_type: Optional[str] = ""
    create_time: Optional[datetime] = None
    update_time: Optional[datetime] = None

    class Config:
        from_attributes = True


class ConversationListResponse(BaseModel):
    """Conversation list response."""
    total: int
    items: List[ConversationResponse]


# ============== Conversation Record Schemas ==============

class ConversationRecordCreate(BaseModel):
    """Create conversation record request."""
    conversation_id: int = Field(..., description="Conversation ID")
    question: str = Field(..., min_length=1, max_length=1000, description="User's question")


class ConversationRecordUpdate(BaseModel):
    """Update conversation record request."""
    sql: Optional[str] = Field(None, description="Generated SQL")
    sql_answer: Optional[str] = Field(None, description="SQL answer/response")
    sql_error: Optional[str] = Field(None, description="SQL error message")
    exec_result: Optional[Dict[str, Any]] = Field(None, description="Execution result")
    chart_type: Optional[str] = Field(None, description="Chart type")
    chart_config: Optional[Dict[str, Any]] = Field(None, description="Chart configuration")
    is_success: Optional[bool] = Field(None, description="Whether execution was successful")


class ConversationRecordResponse(BaseModel):
    """Conversation record response."""
    id: int
    conversation_id: int
    user_id: int
    question: str
    sql: Optional[str] = None
    sql_answer: Optional[str] = None
    sql_error: Optional[str] = None
    exec_result: Optional[Dict[str, Any]] = None
    chart_type: str = "table"
    chart_config: Optional[Dict[str, Any]] = None
    is_success: bool = True
    finish_time: Optional[datetime] = None
    create_time: Optional[datetime] = None
    reasoning: Optional[str] = ""
    steps: List[Dict[str, Any]] = []

    class Config:
        from_attributes = True


class ConversationDetailResponse(BaseModel):
    """Conversation detail with records."""
    id: int
    user_id: int
    title: str
    datasource_id: Optional[int] = None
    datasource_name: Optional[str] = ""
    db_type: Optional[str] = ""
    create_time: Optional[datetime] = None
    update_time: Optional[datetime] = None
    records: List[ConversationRecordResponse] = []


# ============== Chat Request/Response Schemas ==============

class ChatRequest(BaseModel):
    """Chat request schema for SQL generation."""
    question: str = Field(..., min_length=1, max_length=1000, description="User's natural language question")
    datasource_id: int = Field(..., description="ID of the datasource to query")
    conversation_id: Optional[int] = Field(None, description="Conversation ID for context")


class SQLValidationRequest(BaseModel):
    """SQL validation request schema."""
    sql: str = Field(..., min_length=1, max_length=5000, description="SQL query to validate")


class SQLFormatRequest(BaseModel):
    """SQL format request schema."""
    sql: str = Field(..., min_length=1, max_length=5000, description="SQL query to format")
    datasource_id: Optional[int] = Field(None, description="ID of the datasource (for db_type detection)")


class SQLGenerationResult(BaseModel):
    """SQL generation result."""
    sql: str = Field(..., description="Generated SQL query")
    is_valid: bool = Field(..., description="Whether SQL is valid")
    error: str = Field(default="", description="Error message if invalid")
    formatted_sql: str = Field(default="", description="Formatted SQL")
    tables: List[str] = Field(default_factory=list, description="Tables used in SQL")
    chart_type: str = Field(default="table", description="Recommended chart type")
    brief: str = Field(default="", description="Conversation title")


class SQLExecutionResult(BaseModel):
    """SQL execution result."""
    sql: str = Field(..., description="Executed SQL query")
    error: str = Field(default="", description="Error message if failed")
    result: Optional[Dict[str, Any]] = Field(None, description="Query result")
    tables: List[str] = Field(default_factory=list, description="Tables used in SQL")
    chart_type: str = Field(default="table", description="Recommended chart type")


class SQLValidationResult(BaseModel):
    """SQL validation result."""
    is_valid: bool = Field(..., description="Whether SQL is valid")
    error: str = Field(default="", description="Error message if invalid")


class SQLFormatResult(BaseModel):
    """SQL format result."""
    original_sql: str = Field(..., description="Original SQL")
    formatted_sql: str = Field(..., description="Formatted SQL")
    db_type: str = Field(..., description="Database type")


class ReasoningStep(BaseModel):
    """A single step in the LLM reasoning / SQL generation pipeline."""
    name: str = Field(..., description="Step identifier")
    label: str = Field(..., description="Human-readable label")
    status: str = Field(default="ok", description="ok | error")
    elapsed_ms: int = Field(default=0, description="Step duration in milliseconds")
    detail: str = Field(default="", description="Optional detail message")


class ChatResponse(BaseModel):
    """Full chat response with conversation record."""
    record_id: int = Field(..., description="Conversation record ID")
    sql: str = Field(..., description="Generated SQL")
    result: Optional[Dict[str, Any]] = Field(None, description="Query result")
    chart_type: str = Field(default="table", description="Recommended chart type")
    chart_config: Optional[Dict[str, Any]] = Field(None, description="Chart configuration")
    error: str = Field(default="", description="Error message if any")
    reasoning: str = Field(default="", description="LLM natural-language reasoning text")
    steps: List[ReasoningStep] = Field(default_factory=list, description="Pipeline steps")