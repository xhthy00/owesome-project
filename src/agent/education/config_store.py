"""教育报告配置存储。

优先级：
1. 系统库表 ``edu_anomaly_config``（持久化，推荐）；
2. 若 DB 不可用，回落环境变量 / 代码默认（``load_config``）。

进程内覆盖仍保留为短时缓存，写入时同步落库。
"""

from __future__ import annotations

import copy
import logging
import threading
from dataclasses import fields
from typing import Any

from src.agent.education.config import EducationConfig, load_config

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_override: dict[str, Any] = {}

_FLOAT_KEYS = frozenset(
    {
        "pass_threshold",
        "excellent_threshold",
        "pass_ratio",
        "excellent_ratio",
        "default_full_score",
        "critical_margin",
        "regression_threshold",
        "imbalance_score_gap",
    }
)
_ALLOWED_KEYS = _FLOAT_KEYS | {"anomaly_rules"}


def _load_db_config() -> EducationConfig | None:
    try:
        from src.agent.education.anomaly_persistence import load_config_from_db
        from src.common.core.database import get_db_session

        with get_db_session() as session:
            return load_config_from_db(session)
    except Exception as exc:  # noqa: BLE001
        logger.warning("load edu_anomaly_config from DB failed, fallback to env/defaults: %s", exc)
        return None


def _save_db_config(cfg: EducationConfig) -> EducationConfig | None:
    try:
        from src.agent.education.anomaly_persistence import save_config_to_db
        from src.common.core.database import get_db_session

        with get_db_session() as session:
            return save_config_to_db(session, cfg)
    except Exception as exc:  # noqa: BLE001
        logger.warning("save edu_anomaly_config to DB failed: %s", exc)
        return None


def get_config() -> EducationConfig:
    """返回当前生效配置：DB（或环境默认）∪ 进程内覆盖。"""
    base = _load_db_config() or load_config()
    if not _override:
        return base
    with _lock:
        kwargs: dict[str, Any] = {}
        for f in fields(EducationConfig):
            if f.name in _override:
                kwargs[f.name] = copy.deepcopy(_override[f.name])
            else:
                kwargs[f.name] = copy.deepcopy(getattr(base, f.name))
        return EducationConfig(**kwargs)


def update_config(partial: dict[str, Any]) -> EducationConfig:
    """合并字段，写入 DB，并刷新进程覆盖。"""
    from src.agent.education.anomaly_persistence import apply_partial_to_config

    cfg = apply_partial_to_config(get_config(), partial)
    saved = _save_db_config(cfg)
    if saved is not None:
        cfg = saved
    with _lock:
        _override.clear()
        for k in _FLOAT_KEYS:
            _override[k] = float(getattr(cfg, k))
        if cfg.anomaly_rules:
            _override["anomaly_rules"] = copy.deepcopy(cfg.anomaly_rules)
    return cfg


def reset_config() -> EducationConfig:
    """恢复代码默认并写回 DB。"""
    try:
        from src.agent.education.anomaly_persistence import reset_config_in_db
        from src.common.core.database import get_db_session

        with get_db_session() as session:
            cfg = reset_config_in_db(session)
    except Exception as exc:  # noqa: BLE001
        logger.warning("reset edu_anomaly_config in DB failed: %s", exc)
        cfg = EducationConfig()
    with _lock:
        _override.clear()
    return cfg


__all__ = ["get_config", "reset_config", "update_config"]
