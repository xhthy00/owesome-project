"""Datasource permission models."""

from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, Boolean, Column, DateTime, Identity, Integer, String, Text
from sqlmodel import Field, SQLModel


class DsPermission(SQLModel, table=True):
    __tablename__ = "ds_permission"

    id: int = Field(sa_column=Column(Integer, Identity(always=True), primary_key=True, nullable=False))
    enable: bool = Field(sa_column=Column(Boolean, nullable=False, default=True))
    auth_target_type: str = Field(sa_column=Column(String(128), nullable=False))
    auth_target_id: Optional[int] = Field(default=None, sa_column=Column(BigInteger, nullable=True))
    type: str = Field(sa_column=Column(String(64), nullable=False))
    ds_id: Optional[int] = Field(default=None, sa_column=Column(BigInteger, nullable=True))
    table_id: Optional[int] = Field(default=None, sa_column=Column(BigInteger, nullable=True))
    expression_tree: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    permissions: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    white_list_user: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    create_time: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))


class DsRule(SQLModel, table=True):
    __tablename__ = "ds_rules"

    id: int = Field(sa_column=Column(Integer, Identity(always=True), primary_key=True, nullable=False))
    enable: bool = Field(sa_column=Column(Boolean, nullable=False, default=True))
    name: str = Field(max_length=128, nullable=False)
    description: Optional[str] = Field(default=None, max_length=512)
    permission_list: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    user_list: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    white_list_user: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    create_time: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))
