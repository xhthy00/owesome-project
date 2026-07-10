"""school_intervention 纯函数单元测试。"""

from __future__ import annotations

from src.agent.education.school_intervention import (
    build_intervention_section_html,
    build_school_intervention_insights,
    identify_concern_segments,
    identify_weak_classes,
    identify_weak_question_types,
)


def _sample_score_rows() -> list[dict]:
    return [
        {"class": "高一1班", "score": 92, "exam_score": 150},
        {"class": "高一1班", "score": 88, "exam_score": 150},
        {"class": "高一1班", "score": 85, "exam_score": 150},
        {"class": "高一1班", "score": 80, "exam_score": 150},
        {"class": "高一2班", "score": 55, "exam_score": 150},
        {"class": "高一2班", "score": 58, "exam_score": 150},
        {"class": "高一2班", "score": 62, "exam_score": 150},
        {"class": "高一2班", "score": 48, "exam_score": 150},
        {"class": "高一3班", "score": 75, "exam_score": 150},
        {"class": "高一3班", "score": 78, "exam_score": 150},
        {"class": "高一3班", "score": 72, "exam_score": 150},
        {"class": "高一3班", "score": 70, "exam_score": 150},
    ]


def test_identify_weak_classes_flags_low_performers():
    stats = {"avg": 74.0, "pass_rate": 75.0, "stdev": 12.0, "full_score": 150}
    weak = identify_weak_classes(_sample_score_rows(), stats)
    names = [c["class_name"] for c in weak]
    assert "高一2班" in names
    assert any("及格率" in "；".join(c.get("reasons") or []) for c in weak)


def test_identify_concern_segments_flags_low_band():
    stats = {
        "full_score": 150,
        "segments": [
            {"label": "0-90", "count": 8, "ratio": 20.0},
            {"label": "90-105", "count": 12, "ratio": 30.0},
            {"label": "105-120", "count": 10, "ratio": 25.0},
            {"label": "120-135", "count": 6, "ratio": 15.0},
            {"label": "135-150", "count": 4, "ratio": 10.0},
        ],
    }
    concerns = identify_concern_segments(stats=stats)
    assert concerns
    assert concerns[0]["label"] == "0-90"


def test_identify_weak_question_types():
    items = [
        {"question_type": "选择题", "score_rate": 75},
        {"question_type": "填空题", "score_rate": 45},
        {"question_type": "解答题", "score_rate": 52},
    ]
    weak = identify_weak_question_types(items, weak_threshold=60)
    types = {w["question_type"] for w in weak}
    assert "填空题" in types
    assert "解答题" in types


def test_build_school_intervention_insights_and_html():
    stats = {
        "count": 12,
        "avg": 74.0,
        "pass_rate": 75.0,
        "stdev": 12.0,
        "full_score": 150,
        "segments": [
            {"label": "0-90", "count": 4, "ratio": 33.3},
            {"label": "90-105", "count": 3, "ratio": 25.0},
            {"label": "105-120", "count": 3, "ratio": 25.0},
            {"label": "120-135", "count": 1, "ratio": 8.3},
            {"label": "135-150", "count": 1, "ratio": 8.3},
        ],
    }
    insights = build_school_intervention_insights(
        score_rows=_sample_score_rows(),
        stats=stats,
        knowledge_rows=[
            {"knowledge_name": "函数", "score_rate": 48, "ability_level": "applied"},
            {"knowledge_name": "集合", "score_rate": 82, "ability_level": "basic"},
        ],
        item_rows=[
            {"question_type": "填空题", "score_rate": 42},
            {"question_type": "选择题", "score_rate": 80},
        ],
    )
    assert insights["has_intervention"]
    html = build_intervention_section_html(insights)
    assert "需重点干预的班级" in html
    assert "需关注的分数段" in html
    assert "需加强的知识点" in html
    assert "学科薄弱环节" in html
    assert "高一2班" in html
    assert "函数" in html
