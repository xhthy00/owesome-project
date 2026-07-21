"""add pass_ratio / excellent_ratio to edu_anomaly_config

Revision ID: 20260721_02
Revises: 20260721_01
Create Date: 2026-07-21
"""

import sqlalchemy as sa
from alembic import op

revision = "20260721_02"
down_revision = "20260721_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("edu_anomaly_config")}
    if "pass_ratio" not in cols:
        op.add_column(
            "edu_anomaly_config",
            sa.Column("pass_ratio", sa.Float(), nullable=False, server_default="0.6"),
        )
    if "excellent_ratio" not in cols:
        op.add_column(
            "edu_anomaly_config",
            sa.Column("excellent_ratio", sa.Float(), nullable=False, server_default="0.85"),
        )
    # 已有行：用绝对分 / 满分兜底回填比例（满分兜底为 0 时保持默认）
    op.execute(
        """
        UPDATE edu_anomaly_config
        SET
            pass_ratio = CASE
                WHEN default_full_score > 0 THEN pass_threshold / default_full_score
                ELSE 0.6
            END,
            excellent_ratio = CASE
                WHEN default_full_score > 0 THEN excellent_threshold / default_full_score
                ELSE 0.85
            END
        """
    )


def downgrade() -> None:
    op.drop_column("edu_anomaly_config", "excellent_ratio")
    op.drop_column("edu_anomaly_config", "pass_ratio")
