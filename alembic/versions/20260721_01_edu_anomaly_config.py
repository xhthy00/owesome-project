"""create edu_anomaly_config table

Revision ID: 20260721_01
Revises: 20260720_01
Create Date: 2026-07-21
"""

import json

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260721_01"
down_revision = "20260720_01"
branch_labels = None
depends_on = None

_DEFAULT_RULES_JSON = json.dumps(
    [
        {
            "id": "critical",
            "anomaly_type": "critical",
            "enabled": True,
            "threshold": None,
            "compare_target": "pass_line",
            "consecutive_n": 1,
            "fluctuation_mode": "abs",
            "fluctuation_value": 5.0,
            "range_lo": None,
            "range_hi": None,
            "range_lo_offset": -5.0,
            "range_hi_offset": 5.0,
        },
        {
            "id": "regression",
            "anomaly_type": "regression",
            "enabled": True,
            "threshold": -10.0,
            "compare_target": "prev_exam",
            "consecutive_n": 1,
            "fluctuation_mode": "abs",
            "fluctuation_value": 10.0,
            "range_lo": None,
            "range_hi": None,
            "range_lo_offset": None,
            "range_hi_offset": None,
        },
        {
            "id": "imbalanced",
            "anomaly_type": "imbalanced",
            "enabled": True,
            "threshold": 20.0,
            "compare_target": "self_subjects",
            "consecutive_n": 1,
            "fluctuation_mode": "abs",
            "fluctuation_value": 20.0,
            "range_lo": None,
            "range_hi": None,
            "range_lo_offset": None,
            "range_hi_offset": None,
        },
    ],
    ensure_ascii=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "edu_anomaly_config" not in inspector.get_table_names():
        op.create_table(
            "edu_anomaly_config",
            sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True, nullable=False),
            sa.Column("pass_threshold", sa.Float(), nullable=False, server_default="60"),
            sa.Column("excellent_threshold", sa.Float(), nullable=False, server_default="85"),
            sa.Column("default_full_score", sa.Float(), nullable=False, server_default="100"),
            sa.Column("critical_margin", sa.Float(), nullable=False, server_default="5"),
            sa.Column("regression_threshold", sa.Float(), nullable=False, server_default="-10"),
            sa.Column("imbalance_score_gap", sa.Float(), nullable=False, server_default="20"),
            sa.Column(
                "rules_json",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'[]'::jsonb"),
            ),
            sa.Column("update_time", sa.DateTime(), nullable=True),
        )
    # 无数据时写入默认种子
    conn = op.get_bind()
    count = conn.execute(sa.text("SELECT COUNT(*) FROM edu_anomaly_config")).scalar()
    if not count:
        rules_sql = _DEFAULT_RULES_JSON.replace("'", "''")
        op.execute(
            f"""
            INSERT INTO edu_anomaly_config (
                pass_threshold, excellent_threshold, default_full_score,
                critical_margin, regression_threshold, imbalance_score_gap,
                rules_json, update_time
            ) VALUES (
                60, 85, 100, 5, -10, 20,
                '{rules_sql}'::jsonb, NOW()
            )
            """
        )


def downgrade() -> None:
    op.drop_table("edu_anomaly_config")
