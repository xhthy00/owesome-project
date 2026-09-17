"""add sys_user.sys_role for super_admin / platform_admin

Revision ID: 20260917_01
Revises: 20260908_02
Create Date: 2026-09-17
"""

import sqlalchemy as sa

from alembic import op

revision = "20260917_01"
down_revision = "20260908_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "sys_user" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("sys_user")}
    if "sys_role" not in existing:
        op.add_column("sys_user", sa.Column("sys_role", sa.String(length=32), nullable=True))
    op.execute(
        sa.text(
            "UPDATE sys_user SET sys_role = 'super_admin' "
            "WHERE id = 1 AND account = 'admin' "
            "AND (sys_role IS NULL OR sys_role = '')"
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "sys_user" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("sys_user")}
    if "sys_role" in existing:
        op.drop_column("sys_user", "sys_role")
