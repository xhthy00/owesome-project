"""create sys_menu_visible table

Revision ID: 20260720_01
Revises: 20260503_01
Create Date: 2026-07-20 10:00:00
"""

import sqlalchemy as sa

from alembic import op

revision = "20260720_01"
down_revision = "4cc7782595df"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "sys_menu_visible" not in inspector.get_table_names():
        op.create_table(
            "sys_menu_visible",
            sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True, nullable=False),
            sa.Column("menu_key", sa.String(length=64), nullable=False),
            sa.Column("visible", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("create_time", sa.DateTime(), nullable=True),
            sa.Column("update_time", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_sys_menu_visible_menu_key", "sys_menu_visible", ["menu_key"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_sys_menu_visible_menu_key", table_name="sys_menu_visible")
    op.drop_table("sys_menu_visible")
