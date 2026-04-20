"""User model for SQLModel."""

from typing import Optional

from sqlalchemy import BigInteger, Integer, Column
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import SQLModel, Field


class SysUser(SQLModel, table=True):
    """System user table."""
    __tablename__ = "sys_user"

    id: int = Field(
        sa_column=Column(BigInteger, primary_key=True, autoincrement=True)
    )
    account: str = Field(max_length=255, nullable=False)
    name: str = Field(max_length=255, nullable=False)
    password: str = Field(max_length=255, nullable=False)
    email: Optional[str] = Field(default=None, max_length=255)
    oid: int = Field(sa_column=Column(BigInteger, nullable=False, default=1))
    status: int = Field(sa_column=Column(Integer, nullable=False, default=1))
    create_time: int = Field(sa_column=Column(BigInteger, nullable=False))
    language: str = Field(max_length=255, default="zh-CN")
    origin: int = Field(sa_column=Column(Integer, default=0))
    system_variables: Optional[dict] = Field(default=None, sa_column=Column(JSONB, nullable=True))