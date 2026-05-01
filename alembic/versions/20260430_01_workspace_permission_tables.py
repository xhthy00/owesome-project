"""create workspace and permission tables

Revision ID: 20260430_01
Revises:
Create Date: 2026-04-30 20:45:00
"""

from alembic import op
import sqlalchemy as sa


revision = "20260430_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sys_workspace",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True, nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("create_time", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.create_index("ix_sys_workspace_name", "sys_workspace", ["name"], unique=True)

    op.create_table(
        "sys_user_ws",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True, nullable=False),
        sa.Column("uid", sa.BigInteger(), nullable=False),
        sa.Column("oid", sa.BigInteger(), nullable=False),
        sa.Column("weight", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_sys_user_ws_uid_oid", "sys_user_ws", ["uid", "oid"], unique=True)

    op.create_table(
        "ds_permission",
        sa.Column("id", sa.Integer(), sa.Identity(always=True), primary_key=True, nullable=False),
        sa.Column("enable", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("auth_target_type", sa.String(length=128), nullable=False),
        sa.Column("auth_target_id", sa.BigInteger(), nullable=True),
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column("ds_id", sa.BigInteger(), nullable=True),
        sa.Column("table_id", sa.BigInteger(), nullable=True),
        sa.Column("expression_tree", sa.Text(), nullable=True),
        sa.Column("permissions", sa.Text(), nullable=True),
        sa.Column("white_list_user", sa.Text(), nullable=True),
        sa.Column("create_time", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_ds_permission_ds_id", "ds_permission", ["ds_id"], unique=False)

    op.create_table(
        "ds_rules",
        sa.Column("id", sa.Integer(), sa.Identity(always=True), primary_key=True, nullable=False),
        sa.Column("enable", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.String(length=512), nullable=True),
        sa.Column("permission_list", sa.Text(), nullable=True),
        sa.Column("user_list", sa.Text(), nullable=True),
        sa.Column("white_list_user", sa.Text(), nullable=True),
        sa.Column("create_time", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_ds_rules_name", "ds_rules", ["name"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_ds_rules_name", table_name="ds_rules")
    op.drop_table("ds_rules")
    op.drop_index("ix_ds_permission_ds_id", table_name="ds_permission")
    op.drop_table("ds_permission")
    op.drop_index("ix_sys_user_ws_uid_oid", table_name="sys_user_ws")
    op.drop_table("sys_user_ws")
    op.drop_index("ix_sys_workspace_name", table_name="sys_workspace")
    op.drop_table("sys_workspace")
