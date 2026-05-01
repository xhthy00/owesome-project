"""Database connection and session management for SQLModel."""

from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlmodel import Session, SQLModel

from src.common.core.config import get_settings

settings = get_settings()

# Base class for SQLModel - used by Alembic for migrations
Base = SQLModel

engine = create_engine(
    settings.database_url,
    echo=settings.debug,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, class_=Session)


def init_db() -> None:
    """Initialize database tables and run lightweight column migrations."""
    SQLModel.metadata.create_all(bind=engine)
    _ensure_columns()
    _ensure_default_workspace_seed()


def _ensure_columns() -> None:
    """Add columns that were introduced after the initial schema.

    SQLModel.create_all only creates missing tables, not columns. Use a
    minimal idempotent ALTER (Postgres / SQLite compatible) so dev databases
    don't need a manual migration when new columns are added.
    """
    from sqlalchemy import inspect

    additions = {
        "chat_conversation_record": {
            "reasoning": "TEXT",
            "steps": "TEXT",
            "agent_mode": "TEXT",
            "plans": "TEXT",
            "sub_task_agents": "TEXT",
            "plan_states": "TEXT",
            "tool_calls": "TEXT",
            "summary": "TEXT",
            "reports": "TEXT",
        },
    }

    inspector = inspect(engine)
    with engine.begin() as conn:
        dialect = engine.dialect.name
        _ensure_tool_call_log_table(conn, dialect)
        for table, cols in additions.items():
            if not inspector.has_table(table):
                continue
            existing = {c["name"] for c in inspector.get_columns(table)}
            for col, col_type in cols.items():
                if col in existing:
                    continue
                conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {col} {col_type}'))
        if inspector.has_table("sys_user_ws"):
            existing = {c["name"] for c in inspector.get_columns("sys_user_ws")}
            if "create_time" not in existing:
                if dialect == "postgresql":
                    conn.execute(
                        text(
                            "ALTER TABLE sys_user_ws ADD COLUMN create_time "
                            "TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP"
                        )
                    )
                else:
                    conn.execute(
                        text(
                            "ALTER TABLE sys_user_ws ADD COLUMN create_time "
                            "DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
                        )
                    )


def _ensure_tool_call_log_table(conn, dialect: str) -> None:
    if dialect == "postgresql":
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS tool_call_log (
                    id BIGSERIAL PRIMARY KEY,
                    trace_id TEXT NOT NULL,
                    agent_name TEXT NOT NULL,
                    round_idx INTEGER NULL,
                    sub_task_index INTEGER NULL,
                    tool_name TEXT NOT NULL,
                    success BOOLEAN NOT NULL,
                    elapsed_ms INTEGER NULL,
                    args_json TEXT NOT NULL,
                    result_preview TEXT NOT NULL,
                    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL
                )
                """
            )
        )
    else:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS tool_call_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trace_id TEXT NOT NULL,
                    agent_name TEXT NOT NULL,
                    round_idx INTEGER NULL,
                    sub_task_index INTEGER NULL,
                    tool_name TEXT NOT NULL,
                    success BOOLEAN NOT NULL,
                    elapsed_ms INTEGER NULL,
                    args_json TEXT NOT NULL,
                    result_preview TEXT NOT NULL,
                    created_at DATETIME NOT NULL
                )
                """
            )
        )

    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_tool_call_log_trace_id ON tool_call_log(trace_id)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_tool_call_log_created_at ON tool_call_log(created_at)"))


def _ensure_default_workspace_seed() -> None:
    """Ensure default workspace and admin membership exist."""
    with engine.begin() as conn:
        if engine.dialect.name == "postgresql":
            conn.execute(
                text(
                    """
                    INSERT INTO sys_workspace(id, name, create_time)
                    OVERRIDING SYSTEM VALUE
                    VALUES (1, '默认工作空间', 1672531199000)
                    ON CONFLICT (id) DO NOTHING
                    """
                )
            )
        else:
            conn.execute(
                text(
                    """
                    INSERT INTO sys_workspace(id, name, create_time)
                    SELECT 1, '默认工作空间', 1672531199000
                    WHERE NOT EXISTS (
                        SELECT 1 FROM sys_workspace WHERE id = 1
                    )
                    """
                )
            )
        conn.execute(
            text(
                """
                INSERT INTO sys_user_ws(uid, oid, weight, create_time)
                SELECT 1, 1, 1, CURRENT_TIMESTAMP
                WHERE NOT EXISTS (
                    SELECT 1 FROM sys_user_ws WHERE uid = 1 AND oid = 1
                )
                """
            )
        )


def get_session() -> Generator[Session, None, None]:
    """Dependency for getting database session."""
    with SessionLocal() as session:
        try:
            yield session
        finally:
            session.close()


@contextmanager
def get_db_session() -> Generator[Session, None, None]:
    """Context manager for database session."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
