"""Database connection and session management for SQLModel."""

from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel, Session

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


def _ensure_columns() -> None:
    """Add columns that were introduced after the initial schema.

    SQLModel.create_all only creates missing tables, not columns. Use a
    minimal idempotent ALTER (Postgres / SQLite compatible) so dev databases
    don't need a manual migration when new columns are added.
    """
    from sqlalchemy import inspect, text

    additions = {
        "chat_conversation_record": {
            "reasoning": "TEXT",
            "steps": "TEXT",
        },
    }

    inspector = inspect(engine)
    with engine.begin() as conn:
        for table, cols in additions.items():
            if not inspector.has_table(table):
                continue
            existing = {c["name"] for c in inspector.get_columns(table)}
            for col, col_type in cols.items():
                if col in existing:
                    continue
                conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {col} {col_type}'))


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