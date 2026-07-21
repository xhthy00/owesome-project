"""异常规则 DB 读写（系统库 edu_anomaly_config）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlmodel import Session, select

from src.agent.education.config import (
    EducationConfig,
    anomaly_rules_as_dicts,
    build_default_anomaly_rules,
    load_config,
)
from src.agent.education.models_anomaly import EduAnomalyConfig


def _sync_abs_from_ratios(cfg: EducationConfig) -> None:
    """有满分时用比例；无满分列时用比例×满分兜底得到绝对分。"""
    full = float(cfg.default_full_score) or 100.0
    cfg.pass_threshold = round(float(cfg.pass_ratio) * full, 4)
    cfg.excellent_threshold = round(float(cfg.excellent_ratio) * full, 4)


def _row_to_config(row: EduAnomalyConfig) -> EducationConfig:
    rules = row.rules_json if isinstance(row.rules_json, list) else []
    pass_ratio = float(getattr(row, "pass_ratio", None) or 0.6)
    excellent_ratio = float(getattr(row, "excellent_ratio", None) or 0.85)
    return EducationConfig(
        pass_threshold=float(row.pass_threshold),
        excellent_threshold=float(row.excellent_threshold),
        pass_ratio=pass_ratio,
        excellent_ratio=excellent_ratio,
        default_full_score=float(row.default_full_score),
        critical_margin=float(row.critical_margin),
        regression_threshold=float(row.regression_threshold),
        imbalance_score_gap=float(row.imbalance_score_gap),
        anomaly_rules=[dict(x) for x in rules if isinstance(x, dict)] or None,
    )


def _ensure_row(session: Session) -> EduAnomalyConfig:
    row = session.exec(select(EduAnomalyConfig).order_by(EduAnomalyConfig.id.asc())).first()
    if row is not None:
        return row
    base = load_config()
    rules = [r.to_dict() for r in build_default_anomaly_rules(base)]
    row = EduAnomalyConfig(
        pass_threshold=base.pass_threshold,
        excellent_threshold=base.excellent_threshold,
        pass_ratio=base.pass_ratio,
        excellent_ratio=base.excellent_ratio,
        default_full_score=base.default_full_score,
        critical_margin=base.critical_margin,
        regression_threshold=base.regression_threshold,
        imbalance_score_gap=base.imbalance_score_gap,
        rules_json=rules,
        update_time=datetime.utcnow(),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def load_config_from_db(session: Session) -> EducationConfig:
    """从 DB 加载；无行则按代码/环境默认种子。"""
    row = _ensure_row(session)
    cfg = _row_to_config(row)
    # 若 rules 为空，用经典字段推导并回写
    if not cfg.anomaly_rules:
        cfg.anomaly_rules = anomaly_rules_as_dicts(cfg)
        row.rules_json = list(cfg.anomaly_rules)
        row.update_time = datetime.utcnow()
        session.add(row)
        session.commit()
    return cfg


def save_config_to_db(session: Session, cfg: EducationConfig) -> EducationConfig:
    """整份配置落库（比例 + 绝对兜底 + 规则）。"""
    _sync_abs_from_ratios(cfg)
    row = _ensure_row(session)
    row.pass_threshold = float(cfg.pass_threshold)
    row.excellent_threshold = float(cfg.excellent_threshold)
    row.pass_ratio = float(cfg.pass_ratio)
    row.excellent_ratio = float(cfg.excellent_ratio)
    row.default_full_score = float(cfg.default_full_score)
    row.critical_margin = float(cfg.critical_margin)
    row.regression_threshold = float(cfg.regression_threshold)
    row.imbalance_score_gap = float(cfg.imbalance_score_gap)
    rules = cfg.anomaly_rules
    if not rules:
        rules = anomaly_rules_as_dicts(cfg)
    row.rules_json = [dict(x) for x in rules if isinstance(x, dict)]
    row.update_time = datetime.utcnow()
    session.add(row)
    session.commit()
    session.refresh(row)
    return _row_to_config(row)


def reset_config_in_db(session: Session) -> EducationConfig:
    """恢复代码默认并写回 DB。"""
    base = EducationConfig()
    base.anomaly_rules = [r.to_dict() for r in build_default_anomaly_rules(base)]
    return save_config_to_db(session, base)


def apply_partial_to_config(cfg: EducationConfig, partial: dict[str, Any]) -> EducationConfig:
    """合并 API partial。

    - 传 ``pass_ratio`` / ``excellent_ratio``（0~1）时：以比例为准，并同步绝对分兜底；
    - 仅传绝对 ``pass_threshold`` / ``excellent_threshold``（兼容旧 API）：反推比例；
    - 改 ``default_full_score``：用当前比例重算绝对分。
    """
    float_keys = {
        "pass_threshold",
        "excellent_threshold",
        "pass_ratio",
        "excellent_ratio",
        "default_full_score",
        "critical_margin",
        "regression_threshold",
        "imbalance_score_gap",
    }
    touched_classic = False
    touched_ratio = False
    touched_abs = False
    for k, v in partial.items():
        if v is None:
            continue
        if k == "anomaly_rules" and isinstance(v, list):
            cfg.anomaly_rules = [dict(x) for x in v if isinstance(x, dict)]
            return cfg
        if k not in float_keys:
            continue
        setattr(cfg, k, float(v))
        touched_classic = True
        if k in ("pass_ratio", "excellent_ratio"):
            touched_ratio = True
        if k in ("pass_threshold", "excellent_threshold"):
            touched_abs = True

    full = float(cfg.default_full_score) or 100.0
    if touched_ratio or ("default_full_score" in partial and partial.get("default_full_score") is not None):
        _sync_abs_from_ratios(cfg)
    elif touched_abs and full > 0:
        if "pass_threshold" in partial and partial.get("pass_threshold") is not None:
            cfg.pass_ratio = float(cfg.pass_threshold) / full
        if "excellent_threshold" in partial and partial.get("excellent_threshold") is not None:
            cfg.excellent_ratio = float(cfg.excellent_threshold) / full

    if touched_classic:
        cfg.anomaly_rules = [r.to_dict() for r in build_default_anomaly_rules(cfg)]
    return cfg


__all__ = [
    "apply_partial_to_config",
    "load_config_from_db",
    "reset_config_in_db",
    "save_config_to_db",
]
