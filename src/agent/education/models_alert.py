"""教育异常提醒事件表（系统库）：校内待办，非配置表。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import JSON, BigInteger, Column, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class EduAnomalyAlert(SQLModel, table=True):
    """一条可确认的异常报告（同校同场同班同源一条）。

    ``anomaly_type=tier_alert`` 时，明细在 ``payload_json``（critical/regression/imbalanced）。
    存平台库 ``DATABASE_URL``。校长看本校、老师看本班；教育局不用作待办。
    """

    __tablename__ = "edu_anomaly_alert"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_edu_anomaly_alert_dedupe"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    workspace_oid: int = Field(default=1, sa_column=Column(Integer, nullable=False, index=True))
    datasource_id: int = Field(sa_column=Column(Integer, nullable=False, index=True))
    school_id: str = Field(default="", sa_column=Column(String(64), nullable=False, index=True))
    class_name: str = Field(default="", sa_column=Column(String(128), nullable=False, index=True))
    student_id: str = Field(default="", sa_column=Column(String(128), nullable=False, index=True))
    exam_id: str = Field(default="", sa_column=Column(String(64), nullable=False, index=True))
    exam_name: str = Field(default="", sa_column=Column(String(256), nullable=False))
    subject_name: str = Field(default="", sa_column=Column(String(64), nullable=False))
    anomaly_type: str = Field(
        default="",
        sa_column=Column(String(32), nullable=False, index=True),
        description="tier_alert（报告）；历史 critical/regression/imbalanced 会合并为报告",
    )
    title: str = Field(default="", sa_column=Column(String(256), nullable=False))
    reason: str = Field(default="", sa_column=Column(Text, nullable=False))
    payload_json: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(
            JSON().with_variant(JSONB(), "postgresql"),
            nullable=False,
            server_default="{}",
        ),
    )
    source: str = Field(
        default="score_import",
        sa_column=Column(String(64), nullable=False),
        description="score_import | tier_alert_report | manual_scan",
    )
    status: str = Field(
        default="pending",
        sa_column=Column(String(16), nullable=False, index=True),
        description="pending | confirmed",
    )
    dedupe_key: str = Field(default="", sa_column=Column(String(512), nullable=False))
    confirmed_by: Optional[int] = Field(default=None, sa_column=Column(BigInteger, nullable=True))
    confirmed_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime, nullable=True))
    confirm_note: str = Field(default="", sa_column=Column(String(512), nullable=False, default=""))
    create_time: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime, nullable=True, default=datetime.utcnow),
    )
    update_time: Optional[datetime] = Field(
        default=None,
        sa_column=Column(
            DateTime,
            nullable=True,
            default=datetime.utcnow,
            onupdate=datetime.utcnow,
        ),
    )
