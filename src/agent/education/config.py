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

异常规则（验收最小版）：
- 不建库表；规则由 ``EducationConfig`` 经典字段推导，或可选 ``anomaly_rules`` 覆盖；
- 默认三条（临界 / 退步 / 偏科）与历史硬编码行为一致；
- 五类参数字段齐全，其中「连续次数 / 非 abs 波动 / 班均对比」仅占位，行为与现状相同。
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


_DEFAULT_CONFIG_PATH = Path("config/education.json")

#: 对比对象：一期仅支持下列取值（与现实现一致）
COMPARE_PASS_LINE = "pass_line"
COMPARE_PREV_EXAM = "prev_exam"
COMPARE_SELF_SUBJECTS = "self_subjects"

ANOMALY_CRITICAL = "critical"
ANOMALY_REGRESSION = "regression"
ANOMALY_IMBALANCED = "imbalanced"


@dataclass
class AnomalyRule:
    """单条异常规则（五类参数）。

    - ``threshold``：主阈值（退步为负数分差上限；偏科为科间分差下限）
    - ``compare_target``：对比对象
    - ``consecutive_n``：连续次数（一期仅 ``1`` 生效）
    - ``fluctuation_mode`` / ``fluctuation_value``：波动（一期仅 ``abs``）
    - ``range_lo`` / ``range_hi``：绝对分范围；临界生优先用相对及格线的 offset
    """

    id: str
    anomaly_type: str
    enabled: bool = True
    threshold: float | None = None
    compare_target: str = COMPARE_PASS_LINE
    consecutive_n: int = 1
    fluctuation_mode: str = "abs"
    fluctuation_value: float | None = None
    range_lo: float | None = None
    range_hi: float | None = None
    #: 临界生：相对及格线的下/上偏移（与历史 critical_margin 一致）
    range_lo_offset: float | None = None
    range_hi_offset: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def anomaly_rule_from_dict(raw: dict[str, Any]) -> AnomalyRule | None:
    if not isinstance(raw, dict):
        return None
    rid = str(raw.get("id") or raw.get("anomaly_type") or "").strip()
    atype = str(raw.get("anomaly_type") or "").strip()
    if not rid or not atype:
        return None

    def _f(key: str) -> float | None:
        v = raw.get(key)
        if v is None or v == "":
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    def _i(key: str, default: int) -> int:
        v = raw.get(key)
        if v is None or v == "":
            return default
        try:
            return max(1, int(v))
        except (TypeError, ValueError):
            return default

    return AnomalyRule(
        id=rid,
        anomaly_type=atype,
        enabled=bool(raw.get("enabled", True)),
        threshold=_f("threshold"),
        compare_target=str(raw.get("compare_target") or COMPARE_PASS_LINE).strip()
        or COMPARE_PASS_LINE,
        consecutive_n=_i("consecutive_n", 1),
        fluctuation_mode=str(raw.get("fluctuation_mode") or "abs").strip() or "abs",
        fluctuation_value=_f("fluctuation_value"),
        range_lo=_f("range_lo"),
        range_hi=_f("range_hi"),
        range_lo_offset=_f("range_lo_offset"),
        range_hi_offset=_f("range_hi_offset"),
    )


@dataclass
class EducationConfig:
    """学情分析业务阈值。"""

    pass_threshold: float = 60.0
    excellent_threshold: float = 85.0
    #: 当提供 ``full_score`` 时，及格/优秀线 = full_score × ratio（优先于绝对阈值）。
    pass_ratio: float = 0.6
    excellent_ratio: float = 0.85
    #: 当提供 ``full_score`` 时，分数段边界 = full_score × ratio 列表。
    score_segment_ratios: list[float] = field(default_factory=lambda: [0.6, 0.7, 0.8, 0.9])
    #: 及格线上下 ``critical_margin`` 分视为临界生。
    critical_margin: float = 5.0
    #: 相邻考试退步超过该分视为大幅退步（负数表示下降）。
    regression_threshold: float = -10.0
    #: 偏科：同生最高科−最低科分差下限（历史硬编码 20）。
    imbalance_score_gap: float = 20.0
    #: 分数段上界列表，自动补 0 与满分（或 100）。
    score_segments: list[float] = field(default_factory=lambda: [60, 70, 80, 90])
    #: 满分兜底——当数据无法推断满分时用此值归一化优秀率。
    default_full_score: float = 100.0
    #: 良好率阈值（占满分比例）。
    good_ratio: float = 0.70
    #: 低分率阈值（占满分比例，低于此视为低分）。
    low_score_ratio: float = 0.40
    #: 知识点薄弱得分率阈值（百分数）。
    weak_knowledge_threshold: float = 60.0
    #: 可选：显式异常规则列表；``None``/空则由经典字段推导（保证与历史行为一致）。
    anomaly_rules: list[dict[str, Any]] | None = None

    def resolved_segments(self, full_score: float | None = None) -> list[float]:
        """返回排序去重、含 0 与满分的分数段边界。"""
        upper = full_score if full_score is not None else self.default_full_score
        segs = {0.0, float(upper)}
        if full_score is not None and self.score_segment_ratios:
            segs.update(float(full_score) * float(r) for r in self.score_segment_ratios)
        else:
            segs.update(float(s) for s in self.score_segments)
        return sorted(segs)


def build_default_anomaly_rules(cfg: EducationConfig) -> list[AnomalyRule]:
    """由经典阈值推导三条默认规则（与改造前判定完全一致）。"""
    margin = float(cfg.critical_margin)
    return [
        AnomalyRule(
            id="critical",
            anomaly_type=ANOMALY_CRITICAL,
            enabled=True,
            threshold=None,
            compare_target=COMPARE_PASS_LINE,
            consecutive_n=1,
            fluctuation_mode="abs",
            fluctuation_value=margin,
            range_lo_offset=-margin,
            range_hi_offset=margin,
        ),
        AnomalyRule(
            id="regression",
            anomaly_type=ANOMALY_REGRESSION,
            enabled=True,
            threshold=float(cfg.regression_threshold),
            compare_target=COMPARE_PREV_EXAM,
            consecutive_n=1,
            fluctuation_mode="abs",
            fluctuation_value=abs(float(cfg.regression_threshold)),
        ),
        AnomalyRule(
            id="imbalanced",
            anomaly_type=ANOMALY_IMBALANCED,
            enabled=True,
            threshold=float(cfg.imbalance_score_gap),
            compare_target=COMPARE_SELF_SUBJECTS,
            consecutive_n=1,
            fluctuation_mode="abs",
            fluctuation_value=float(cfg.imbalance_score_gap),
        ),
    ]


def resolve_anomaly_rules(cfg: EducationConfig) -> list[AnomalyRule]:
    """生效规则：显式 ``anomaly_rules`` 优先，否则经典字段推导。"""
    raw = cfg.anomaly_rules
    if isinstance(raw, list) and raw:
        parsed: list[AnomalyRule] = []
        for item in raw:
            rule = anomaly_rule_from_dict(item) if isinstance(item, dict) else None
            if rule is not None:
                parsed.append(rule)
        if parsed:
            return parsed
    return build_default_anomaly_rules(cfg)


def anomaly_rules_as_dicts(cfg: EducationConfig) -> list[dict[str, Any]]:
    return [r.to_dict() for r in resolve_anomaly_rules(cfg)]


def load_config(config_path: Path | str | None = None) -> EducationConfig:
    """加载配置：环境变量 > JSON 文件 > 默认值。

    环境变量名见下方映射；JSON 文件结构同 ``EducationConfig`` 字段。
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
        "EDU_IMBALANCE_SCORE_GAP": ("imbalance_score_gap", float),
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
        elif key == "score_segment_ratios" and isinstance(val, list):
            cfg.score_segment_ratios = [float(x) for x in val]
        elif key == "anomaly_rules":
            if val is None:
                cfg.anomaly_rules = None
            elif isinstance(val, list):
                cfg.anomaly_rules = [dict(x) for x in val if isinstance(x, dict)]
        elif isinstance(val, (int, float, bool)):
            setattr(cfg, key, type(getattr(cfg, key))(val))
        elif isinstance(val, str):
            try:
                cur = getattr(cfg, key)
                setattr(cfg, key, type(cur)(float(val)) if isinstance(cur, float) else val)
            except (TypeError, ValueError):
                pass
    return cfg


def config_to_public_dict(cfg: EducationConfig) -> dict[str, Any]:
    """API / 文档用：比例（主）+ 绝对兜底 + 生效异常规则。"""
    return {
        "pass_ratio": cfg.pass_ratio,
        "excellent_ratio": cfg.excellent_ratio,
        "pass_percent": round(float(cfg.pass_ratio) * 100, 4),
        "excellent_percent": round(float(cfg.excellent_ratio) * 100, 4),
        "pass_threshold": cfg.pass_threshold,
        "excellent_threshold": cfg.excellent_threshold,
        "default_full_score": cfg.default_full_score,
        "critical_margin": cfg.critical_margin,
        "regression_threshold": cfg.regression_threshold,
        "imbalance_score_gap": cfg.imbalance_score_gap,
        "anomaly_rules": anomaly_rules_as_dicts(cfg),
    }


__all__ = [
    "ANOMALY_CRITICAL",
    "ANOMALY_IMBALANCED",
    "ANOMALY_REGRESSION",
    "AnomalyRule",
    "COMPARE_PASS_LINE",
    "COMPARE_PREV_EXAM",
    "COMPARE_SELF_SUBJECTS",
    "EducationConfig",
    "anomaly_rule_from_dict",
    "anomaly_rules_as_dicts",
    "build_default_anomaly_rules",
    "config_to_public_dict",
    "load_config",
    "resolve_anomaly_rules",
]
