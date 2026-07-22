"""create edu_anomaly_alert table

Revision ID: 20260721_03
Revises: 20260721_02
Create Date: 2026-07-21
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260721_03"
down_revision = "20260721_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "edu_anomaly_alert" in inspector.get_table_names():
        return

    op.create_table(
        "edu_anomaly_alert",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True, nullable=False),
        sa.Column("workspace_oid", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("datasource_id", sa.Integer(), nullable=False),
        sa.Column("school_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("class_name", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("student_id", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("exam_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("exam_name", sa.String(length=256), nullable=False, server_default=""),
        sa.Column("subject_name", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("anomaly_type", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("title", sa.String(length=256), nullable=False, server_default=""),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "payload_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("source", sa.String(length=64), nullable=False, server_default="score_import"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("dedupe_key", sa.String(length=512), nullable=False),
        sa.Column("confirmed_by", sa.BigInteger(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(), nullable=True),
        sa.Column("confirm_note", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("create_time", sa.DateTime(), nullable=True),
        sa.Column("update_time", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("dedupe_key", name="uq_edu_anomaly_alert_dedupe"),
    )
    op.create_index("ix_edu_anomaly_alert_workspace_oid", "edu_anomaly_alert", ["workspace_oid"])
    op.create_index("ix_edu_anomaly_alert_datasource_id", "edu_anomaly_alert", ["datasource_id"])
    op.create_index("ix_edu_anomaly_alert_school_id", "edu_anomaly_alert", ["school_id"])
    op.create_index("ix_edu_anomaly_alert_class_name", "edu_anomaly_alert", ["class_name"])
    op.create_index("ix_edu_anomaly_alert_student_id", "edu_anomaly_alert", ["student_id"])
    op.create_index("ix_edu_anomaly_alert_exam_id", "edu_anomaly_alert", ["exam_id"])
    op.create_index("ix_edu_anomaly_alert_anomaly_type", "edu_anomaly_alert", ["anomaly_type"])
    op.create_index("ix_edu_anomaly_alert_status", "edu_anomaly_alert", ["status"])


def downgrade() -> None:
    op.drop_table("edu_anomaly_alert")
