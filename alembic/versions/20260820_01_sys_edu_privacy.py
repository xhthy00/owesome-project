"""create sys_edu_privacy table

Revision ID: 20260820_01
Revises: 20260721_03
Create Date: 2026-08-20
"""

import sqlalchemy as sa

from alembic import op

revision = "20260820_01"
down_revision = "20260721_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "sys_edu_privacy" not in inspector.get_table_names():
        op.create_table(
            "sys_edu_privacy",
            sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True, nullable=False),
            sa.Column("anonymize_display", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("create_time", sa.DateTime(), nullable=True),
            sa.Column("update_time", sa.DateTime(), nullable=True),
        )


def downgrade() -> None:
    op.drop_table("sys_edu_privacy")
