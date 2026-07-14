"""school_intervention 纯函数单元测试。"""

from __future__ import annotations

from src.agent.education.school_intervention import (
    build_class_compare_table_html,
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


def test_class_compare_count_narrows_multi_exam_inflation():
    """多场考试叠加人次时，班级对比人数应按单场去重学生计。"""
    from src.agent.education.aggregation import prepare_score_rows_for_kpi

    rows: list[dict] = []
    # 3 场考试 × 同批学生，模拟考试名模糊匹配膨胀
    for exam_id in ("1", "2", "3"):
        for i in range(52):
            rows.append({
                "exam_id": exam_id,
                "student_id": f"11-{i}",
                "class": "高三(11)班",
                "score": 100.0,
                "exam_score": 150.0,
            })
        for i in range(55):
            rows.append({
                "exam_id": exam_id,
                "student_id": f"9-{i}",
                "class": "高三(9)班",
                "score": 106.0,
                "exam_score": 150.0,
            })
        for i in range(52):
            rows.append({
                "exam_id": exam_id,
                "student_id": f"10-{i}",
                "class": "高三(10)班",
                "score": 109.0,
                "exam_score": 150.0,
            })
    assert len(rows) == 159 * 3
    cleaned = prepare_score_rows_for_kpi(rows)
    assert len(cleaned) == 159

    insights = build_school_intervention_insights(
        score_rows=rows,
        stats={"count": len(rows), "avg": 105.0, "pass_rate": 70.0, "full_score": 150},
    )
    by_class = {
        g["dimension_value"]: int(g["count"])
        for g in insights["class_compare"]
    }
    assert by_class["高三(11)班"] == 52
    assert by_class["高三(9)班"] == 55
    assert by_class["高三(10)班"] == 52
    assert insights["stats"]["count"] == 159
    html = build_class_compare_table_html(
        insights["class_compare"],
        insights.get("weak_classes"),
        school_stats=insights["stats"],
    )
    assert ">52<" in html or ">52</td>" in html
    assert "156" not in html
