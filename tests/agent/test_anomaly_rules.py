"""异常规则配置：默认行为与历史硬编码一致；改阈值可影响判定。"""

from src.agent.education.config import (
    EducationConfig,
    anomaly_rules_as_dicts,
    build_default_anomaly_rules,
    resolve_anomaly_rules,
)
from src.agent.education.stats import identify_at_risk_students


def _sample_students():
    return [
        {"name": "张三", "subject": "数学", "score": 58, "prev_score": 80},
        {"name": "张三", "subject": "语文", "score": 92},
        {"name": "李四", "subject": "数学", "score": 72},
    ]


def test_default_rules_match_classic_thresholds():
    cfg = EducationConfig()
    rules = {r.anomaly_type: r for r in build_default_anomaly_rules(cfg)}
    assert rules["critical"].fluctuation_value == 5.0
    assert rules["critical"].range_lo_offset == -5.0
    assert rules["critical"].range_hi_offset == 5.0
    assert rules["regression"].threshold == -10.0
    assert rules["regression"].compare_target == "prev_exam"
    assert rules["imbalanced"].threshold == 20.0
    assert rules["imbalanced"].compare_target == "self_subjects"
    assert all(r.consecutive_n == 1 for r in rules.values())


def test_identify_default_parity_with_legacy_cases():
    """默认配置下名单与改造前用例一致。"""
    out = identify_at_risk_students(_sample_students(), EducationConfig())
    assert any(s["name"] == "张三" for s in out["critical"])
    assert any(s["name"] == "张三" for s in out["regression"])
    assert any(s["name"] == "张三" for s in out["imbalanced"])
    assert not any(s["name"] == "李四" for s in out["critical"])


def test_identify_respects_critical_margin_override():
    # margin=1 → [59,61)，58 不再临界
    cfg = EducationConfig(critical_margin=1.0)
    out = identify_at_risk_students(_sample_students(), cfg)
    assert not any(s["name"] == "张三" for s in out["critical"])


def test_identify_respects_regression_threshold_override():
    cfg = EducationConfig(regression_threshold=-30.0)
    out = identify_at_risk_students(_sample_students(), cfg)
    assert out["regression"] == []


def test_identify_respects_imbalance_gap_override():
    cfg = EducationConfig(imbalance_score_gap=40.0)
    out = identify_at_risk_students(_sample_students(), cfg)
    # 92-58=34 < 40 → 不再偏科
    assert out["imbalanced"] == []


def test_explicit_anomaly_rules_override():
    cfg = EducationConfig(
        anomaly_rules=[
            {
                "id": "critical",
                "anomaly_type": "critical",
                "enabled": True,
                "compare_target": "pass_line",
                "consecutive_n": 1,
                "fluctuation_mode": "abs",
                "fluctuation_value": 1,
                "range_lo_offset": -1,
                "range_hi_offset": 1,
            },
            {
                "id": "regression",
                "anomaly_type": "regression",
                "enabled": False,
                "compare_target": "prev_exam",
                "threshold": -10,
                "consecutive_n": 1,
                "fluctuation_mode": "abs",
            },
            {
                "id": "imbalanced",
                "anomaly_type": "imbalanced",
                "enabled": True,
                "compare_target": "self_subjects",
                "threshold": 20,
                "consecutive_n": 1,
                "fluctuation_mode": "abs",
            },
        ]
    )
    out = identify_at_risk_students(_sample_students(), cfg)
    assert out["regression"] == []
    assert not any(s["name"] == "张三" for s in out["critical"])
    assert any(s["name"] == "张三" for s in out["imbalanced"])


def test_resolve_falls_back_when_anomaly_rules_empty():
    cfg = EducationConfig(anomaly_rules=[])
    assert len(resolve_anomaly_rules(cfg)) == 3
    assert len(anomaly_rules_as_dicts(cfg)) == 3
