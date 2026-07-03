"""教育学情阈值与分数段配置。

设计目标：让"及格线 60 / 优秀线 85 / 临界 ±5 / 退步 -10 / 分数段"这类
**业务参数**脱离代码硬编码，可由环境变量或工作区 JSON 覆盖——不同学校、
不同学段（小学 vs 高中）标准差异很大。

加载优先级（高 → 低）：

1. 环境变量 ``EDU_PASS_THRESHOLD`` 等；
2. 工作区 ``config/education.json``（Phase 3 接入管理台后写入）；
3. 内置默认值。

为保持 Phase 1 极简，**只实现环境变量 + 默认值**两层；JSON 文件读取留到
Phase 3 配置 API 时再加，避免现在引入文件 IO 与 schema 校验的复杂度。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


_DEFAULT_CONFIG_PATH = Path("config/education.json")


@dataclass
class EducationConfig:
    """学情分析业务阈值。"""

    pass_threshold: float = 60.0
    excellent_threshold: float = 85.0
    #: 及格线上下 ``critical_margin`` 分视为临界生。
    critical_margin: float = 5.0
    #: 相邻考试退步超过该分视为大幅退步（负数表示下降）。
    regression_threshold: float = -10.0
    #: 分数段上界列表，自动补 0 与满分（或 100）。
    score_segments: list[float] = field(default_factory=lambda: [60, 70, 80, 90])
    #: 满分兜底——当数据无法推断满分时用此值归一化优秀率。
    default_full_score: float = 100.0

    def resolved_segments(self, full_score: float | None = None) -> list[float]:
        """返回排序去重、含 0 与满分的分数段边界。"""
        upper = full_score if full_score is not None else self.default_full_score
        segs = {0.0, float(upper)}
        segs.update(float(s) for s in self.score_segments)
        return sorted(segs)


def load_config(config_path: Path | str | None = None) -> EducationConfig:
    """加载配置：环境变量 > JSON 文件 > 默认值。

    环境变量名见 ``_ENV_KEYS``；JSON 文件结构同 ``EducationConfig`` 字段。
    缺失或解析失败一律回落默认值——配置是"锦上添花"，不能拖垮报告生成。
    """
    cfg = EducationConfig()

    path = Path(config_path) if config_path else _DEFAULT_CONFIG_PATH
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                cfg = _merge_into(cfg, raw)
        except Exception:
            # 配置损坏不抛，用默认值继续——日志由调用方按需打印
            pass

    env_overrides = {
        "EDU_PASS_THRESHOLD": ("pass_threshold", float),
        "EDU_EXCELLENT_THRESHOLD": ("excellent_threshold", float),
        "EDU_CRITICAL_MARGIN": ("critical_margin", float),
        "EDU_REGRESSION_THRESHOLD": ("regression_threshold", float),
        "EDU_DEFAULT_FULL_SCORE": ("default_full_score", float),
    }
    for env_key, (field_name, caster) in env_overrides.items():
        val = os.environ.get(env_key)
        if val is None or val.strip() == "":
            continue
        try:
            setattr(cfg, field_name, caster(float(val)))
        except (TypeError, ValueError):
            continue

    seg_env = os.environ.get("EDU_SCORE_SEGMENTS")
    if seg_env and seg_env.strip():
        try:
            cfg.score_segments = [float(x) for x in seg_env.split(",") if x.strip()]
        except ValueError:
            pass

    return cfg


def _merge_into(cfg: EducationConfig, raw: dict[str, Any]) -> EducationConfig:
    for key, val in raw.items():
        if not hasattr(cfg, key):
            continue
        if key == "score_segments" and isinstance(val, list):
            cfg.score_segments = [float(x) for x in val]
        elif isinstance(val, (int, float, bool)):
            setattr(cfg, key, type(getattr(cfg, key))(val))
        elif isinstance(val, str):
            try:
                cur = getattr(cfg, key)
                setattr(cfg, key, type(cur)(float(val)) if isinstance(cur, float) else val)
            except (TypeError, ValueError):
                pass
    return cfg


__all__ = ["EducationConfig", "load_config"]
