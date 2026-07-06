"""教育 ECharts option 生成单元测试。"""

from __future__ import annotations

import json

from src.agent.education.charts import build_chart_option


def test_knowledge_bar_uses_categories_and_values():
    raw = build_chart_option(
        "knowledge_bar",
        {"categories": ["集合及其运算", "分段函数"], "values": [90.0, 42.35]},
        "知识点得分率",
    )
    opt = json.loads(raw)
    assert opt["title"]["text"] == "知识点得分率"
    assert opt["yAxis"]["data"] == ["集合及其运算", "分段函数"]
    assert opt["series"][0]["data"] == [90.0, 42.35]


def test_bar_alias_with_segments_resolves_score_distribution():
    from src.agent.education.charts import resolve_chart_type

    assert resolve_chart_type("bar", {"segments": [{"label": "A", "count": 1}]}) == "score_distribution"


def test_subject_bar_ignores_categories_shorthand():
    """subject_bar 只认 subjects/metrics，误传 categories 时应为空图。"""
    raw = build_chart_option(
        "subject_bar",
        {"categories": ["A"], "values": [50.0]},
        "错误用法",
    )
    opt = json.loads(raw)
    assert opt["xAxis"]["data"] == []
    assert opt["series"] == []
