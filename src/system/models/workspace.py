"""Workspace and membership models."""

from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, Identity, Integer
from sqlmodel import Field, SQLModel


class SysWorkspace(SQLModel, table=True):
    __tablename__ = "sys_workspace"

    id: int = Field(sa_column=Column(BigInteger, Identity(always=True), primary_key=True, nullable=False))
    name: str = Field(max_length=255, nullable=False)
    create_time: int = Field(sa_column=Column(BigInteger, nullable=False, default=0))


class SysUserWorkspace(SQLModel, table=True):
    __tablename__ = "sys_user_ws"

    id: int = Field(sa_column=Column(BigInteger, Identity(always=True), primary_key=True, nullable=False))
    uid: int = Field(sa_column=Column(BigInteger, nullable=False))
    oid: int = Field(sa_column=Column(BigInteger, nullable=False))
    weight: int = Field(sa_column=Column(Integer, nullable=False, default=0))
    create_time: datetime = Field(
        sa_column=Column(DateTime(timezone=False), nullable=False, default=datetime.now)
    )
