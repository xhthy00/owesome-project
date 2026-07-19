"""add request_method and request_path to audit_operation_log

Revision ID: 4cc7782595df
Revises: 2a65f0580e52
Create Date: 2026-07-18 10:00:00.000000

为操作日志表补充请求方法 / 请求路径字段，便于审计时定位具体操作端点。
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4cc7782595df"
down_revision: Union[str, None] = "2a65f0580e52"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "audit_operation_log",
        sa.Column("request_method", sa.String(length=16), nullable=False, server_default=""),
    )
    op.add_column(
        "audit_operation_log",
        sa.Column("request_path", sa.String(length=255), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("audit_operation_log", "request_path")
    op.drop_column("audit_operation_log", "request_method")
