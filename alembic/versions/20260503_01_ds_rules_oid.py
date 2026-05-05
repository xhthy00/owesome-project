"""add ds_rules.oid for workspace isolation

Revision ID: 20260503_01
Revises: 20260430_01
Create Date: 2026-05-03
"""

from alembic import op
import sqlalchemy as sa


revision = "20260503_01"
down_revision = "20260430_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ds_rules",
        sa.Column("oid", sa.BigInteger(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("ds_rules", "oid")
