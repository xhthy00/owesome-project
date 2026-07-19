"""add audit log tables

Revision ID: 2a65f0580e52
Revises: 20260503_01
Create Date: 2026-07-17 15:13:59.825815

新增审计日志三张表：访问日志 / 操作日志 / 登录日志。
autogenerate 同时检测到线上库存在一批未在 model 中声明的表与索引
（chat_conversation、tool_call_log、sys_role、sys_user_role、sys_data_rule、
sys_resource_grant、student_score、chusan_zhengzhi 等运行时建表 / 教育样例数据表，
以及 sys_user.origin 的 nullable、sys_user_ws 的 uid/oid 单列索引），这些与审计
日志无关，属于历史遗留漂移，不纳入本迁移，故手动从 upgrade/downgrade 中剔除。
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2a65f0580e52"
down_revision: Union[str, None] = "20260503_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# 三张审计日志表的公共列：trace_id / user_id / user_account / workspace_oid /
# ip / user_agent / success / error_msg / elapsed_ms / created_at。
# NOT NULL 列带 server_default，便于裸 SQL / 批量回填等非 ORM 写入。
_COMMON_COLUMNS = [
    sa.Column("trace_id", sa.String(length=64), nullable=False, server_default="-"),
    sa.Column("user_id", sa.BigInteger(), nullable=True),
    sa.Column("user_account", sa.String(length=255), nullable=True),
    sa.Column("workspace_oid", sa.BigInteger(), nullable=True),
    sa.Column("ip", sa.String(length=64), nullable=True),
    sa.Column("user_agent", sa.String(length=500), nullable=True),
    sa.Column("success", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    sa.Column("error_msg", sa.String(length=500), nullable=True),
    sa.Column("elapsed_ms", sa.Integer(), nullable=True),
    sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
]


def upgrade() -> None:
    # 访问日志
    op.create_table(
        "audit_access_log",
        *_COMMON_COLUMNS,
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("request_method", sa.String(length=16), nullable=False),
        sa.Column("request_path", sa.String(length=255), nullable=False),
        sa.Column("datasource_id", sa.BigInteger(), nullable=True),
        sa.Column("query_text", sa.String(length=500), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_audit_access_log_user_id", "audit_access_log", ["user_id"], unique=False)
    op.create_index("idx_audit_access_log_created_at", "audit_access_log", ["created_at"], unique=False)

    # 操作日志
    op.create_table(
        "audit_operation_log",
        *_COMMON_COLUMNS,
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("operation_type", sa.String(length=32), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.String(length=128), nullable=True),
        sa.Column("detail", sa.String(length=2000), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_audit_operation_log_user_id", "audit_operation_log", ["user_id"], unique=False)
    op.create_index(
        "idx_audit_operation_log_created_at", "audit_operation_log", ["created_at"], unique=False
    )

    # 登录日志
    op.create_table(
        "audit_login_log",
        *_COMMON_COLUMNS,
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("account", sa.String(length=255), nullable=False),
        sa.Column("fail_reason", sa.String(length=255), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_audit_login_log_account", "audit_login_log", ["account"], unique=False)
    op.create_index("idx_audit_login_log_user_id", "audit_login_log", ["user_id"], unique=False)
    op.create_index("idx_audit_login_log_created_at", "audit_login_log", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_audit_login_log_created_at", table_name="audit_login_log")
    op.drop_index("idx_audit_login_log_user_id", table_name="audit_login_log")
    op.drop_index("idx_audit_login_log_account", table_name="audit_login_log")
    op.drop_table("audit_login_log")

    op.drop_index("idx_audit_operation_log_created_at", table_name="audit_operation_log")
    op.drop_index("idx_audit_operation_log_user_id", table_name="audit_operation_log")
    op.drop_table("audit_operation_log")

    op.drop_index("idx_audit_access_log_created_at", table_name="audit_access_log")
    op.drop_index("idx_audit_access_log_user_id", table_name="audit_access_log")
    op.drop_table("audit_access_log")
