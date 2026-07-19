"""审计日志数据模型：访问日志 / 操作日志 / 登录日志。"""
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import BigInteger, Column, DateTime, Index, func, text
from sqlmodel import Field, SQLModel


class _AuditBase(SQLModel):
    """三类审计日志公共字段。

    注意：SQLModel 在 ``table=True`` 子类间共享基类字段定义。若基类直接用
    ``sa_column=Column(...)`` 传入 Column 对象，该对象会被多张表复用，触发
    ``Column already assigned`` 错误。因此基类统一用 ``sa_type`` +
    ``sa_column_kwargs`` 声明，由 SQLModel 为每张表各自构造 Column，得到的列
    类型 / nullable / index 与 ``sa_column=Column(...)`` 等价。子类的 ``id``
    是各自独立声明的字段，仍可直接用 ``sa_column=Column(...)``。

    NOT NULL 列同时保留 Python ``default``/``default_factory`` 与 DB 侧
    ``server_default``：ORM 插入走 Python 默认值，裸 SQL / 批量回填走
    ``server_default``，避免非 ORM 写入违反 NOT NULL。
    """

    trace_id: str = Field(
        default="-", max_length=64, sa_column_kwargs={"server_default": "-"}
    )
    user_id: Optional[int] = Field(default=None, sa_type=BigInteger)
    user_account: Optional[str] = Field(default=None, max_length=255)
    workspace_oid: Optional[int] = Field(default=None, sa_type=BigInteger)
    ip: Optional[str] = Field(default=None, max_length=64)
    user_agent: Optional[str] = Field(default=None, max_length=500)
    success: bool = Field(
        default=True, sa_column_kwargs={"server_default": text("true")}
    )
    error_msg: Optional[str] = Field(default=None, max_length=500)
    elapsed_ms: Optional[int] = Field(default=None)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_type=DateTime,
        sa_column_kwargs={"nullable": False, "server_default": func.now()},
    )


class AuditAccessLog(_AuditBase, table=True):
    """访问日志：NL2SQL 与学情查询接口。"""

    __tablename__ = "audit_access_log"
    __table_args__ = (
        Index("idx_audit_access_log_user_id", "user_id"),
        Index("idx_audit_access_log_created_at", "created_at"),
    )

    id: Optional[int] = Field(
        default=None, sa_column=Column(BigInteger, primary_key=True, autoincrement=True)
    )
    request_method: str = Field(max_length=16)
    request_path: str = Field(max_length=255)
    datasource_id: Optional[int] = Field(default=None, sa_type=BigInteger)
    query_text: Optional[str] = Field(default=None, max_length=500)


class AuditOperationLog(_AuditBase, table=True):
    """操作日志：增删改写接口。"""

    __tablename__ = "audit_operation_log"
    __table_args__ = (
        Index("idx_audit_operation_log_user_id", "user_id"),
        Index("idx_audit_operation_log_created_at", "created_at"),
    )

    id: Optional[int] = Field(
        default=None, sa_column=Column(BigInteger, primary_key=True, autoincrement=True)
    )
    operation_type: str = Field(max_length=32)
    resource_type: str = Field(max_length=64)
    resource_id: Optional[str] = Field(default=None, max_length=128)
    request_method: str = Field(default="", max_length=16)
    request_path: str = Field(default="", max_length=255)
    detail: Optional[str] = Field(default=None, max_length=2000)


class AuditLoginLog(_AuditBase, table=True):
    """登录日志：登录成功 / 失败。"""

    __tablename__ = "audit_login_log"
    __table_args__ = (
        Index("idx_audit_login_log_account", "account"),
        Index("idx_audit_login_log_user_id", "user_id"),
        Index("idx_audit_login_log_created_at", "created_at"),
    )

    id: Optional[int] = Field(
        default=None, sa_column=Column(BigInteger, primary_key=True, autoincrement=True)
    )
    account: str = Field(max_length=255, nullable=False)
    fail_reason: Optional[str] = Field(default=None, max_length=255)
