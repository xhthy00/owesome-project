"""教育异常规则 / 学情阈值配置表（系统库，单例一行）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import BigInteger, Column, DateTime, Float
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class EduAnomalyConfig(SQLModel, table=True):
    """单例配置（通常 id=1）：经典阈值 + 异常规则列表。

    存平台库 ``DATABASE_URL``（如 awesome），不进教育业务数据源。
    """

    __tablename__ = "edu_anomaly_config"

    id: Optional[int] = Field(
        default=None,
        sa_column=Column(BigInteger, primary_key=True, autoincrement=True),
    )
    pass_threshold: float = Field(default=60.0, sa_column=Column(Float, nullable=False))
    excellent_threshold: float = Field(default=85.0, sa_column=Column(Float, nullable=False))
    #: 占卷面满分比例（有 exam_score 时优先用）；默认 0.6 / 0.85
    pass_ratio: float = Field(default=0.6, sa_column=Column(Float, nullable=False))
    excellent_ratio: float = Field(default=0.85, sa_column=Column(Float, nullable=False))
    default_full_score: float = Field(default=100.0, sa_column=Column(Float, nullable=False))
    critical_margin: float = Field(default=5.0, sa_column=Column(Float, nullable=False))
    regression_threshold: float = Field(default=-10.0, sa_column=Column(Float, nullable=False))
    imbalance_score_gap: float = Field(default=20.0, sa_column=Column(Float, nullable=False))
    #: 异常规则列表（五类参数），元素结构同 ``AnomalyRule.to_dict``
    rules_json: list[dict[str, Any]] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default="[]"),
    )
    update_time: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime, nullable=True, default=datetime.utcnow, onupdate=datetime.utcnow),
    )
