"""教育报告配置存储（Phase 3）。

``load_config`` 仅从环境变量读取；本模块在其之上叠加一层**进程内可写覆盖**，
使「阈值配置 API」能在不引入 DB 迁移的前提下生效——满足 Phase 3 验收
「配置及格线为 50 分后，报告 KPI 自动重算」。

设计：
- ``get_config()`` = 环境默认 ∪ 覆盖项（覆盖优先）；
- ``update_config(partial)`` 合并入覆盖项，``reset_config()`` 清空覆盖；
- 线程安全（``threading.Lock``），单进程多线程 FastAPI 安全；
- 多实例/持久化留待 Phase 4（工作区 JSON 或 DB 表）。
"""

from __future__ import annotations

import threading
from typing import Any

from src.agent.education.config import EducationConfig, load_config

_lock = threading.Lock()
_override: dict[str, float] = {}


def get_config() -> EducationConfig:
    """返回当前生效配置：环境默认值叠加进程内覆盖。"""
    base = load_config()
    if not _override:
        return base
    with _lock:
        merged = {
            "pass_threshold": _override.get("pass_threshold", base.pass_threshold),
            "excellent_threshold": _override.get("excellent_threshold", base.excellent_threshold),
            "default_full_score": _override.get("default_full_score", base.default_full_score),
            "critical_margin": _override.get("critical_margin", base.critical_margin),
            "regression_threshold": _override.get("regression_threshold", base.regression_threshold),
        }
    return EducationConfig(**merged)


def update_config(partial: dict[str, Any]) -> EducationConfig:
    """合并部分字段到覆盖项，返回更新后的配置。未知字段忽略。"""
    allowed = {
        "pass_threshold",
        "excellent_threshold",
        "default_full_score",
        "critical_margin",
        "regression_threshold",
    }
    with _lock:
        for k, v in partial.items():
            if k in allowed and v is not None:
                _override[k] = float(v)
    return get_config()


def reset_config() -> None:
    """清空覆盖，回到环境默认。"""
    with _lock:
        _override.clear()


__all__ = ["get_config", "reset_config", "update_config"]
