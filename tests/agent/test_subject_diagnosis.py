"""subject_diagnosis 纯函数单元测试。"""

from __future__ import annotations

from src.agent.education.subject_diagnosis import (
    build_diagnosis_recommendations,
    build_diagnosis_summary,
    identify_weak_knowledge,
)


def test_identify_weak_knowledge_by_score_rate():
    rows = [
        {"knowledge_name": "集合", "score_rate": 85},
        {"knowledge_name": "函数", "score_rate": 55},
        {"knowledge_name": "三角", "score_rate": 48},
    ]
    weak = identify_weak_knowledge(rows, weak_threshold=60)
    assert len(weak) == 2
    assert weak[0]["knowledge_name"] == "三角"


def test_build_diagnosis_summary_lists_weak_knowledge():
    html = build_diagnosis_summary(
        school_name="南京市第一中学",
        exam_name="期末质量检测",
        subject_name="数学",
        stats={"count": 40, "avg": 86, "pass_rate": 90, "excellent_rate": 20},
        knowledge_rows=[
            {"knowledge_name": "函数", "score_rate": 52},
            {"knowledge_name": "集合", "score_rate": 80},
        ],
        item_rows=[{"question_no": 3, "knowledge_name": "函数", "score_rate": 45}],
    )
    assert "薄弱知识点" in html
    assert "函数" in html


def test_build_diagnosis_recommendations_for_weak_points():
    html = build_diagnosis_recommendations(
        knowledge_rows=[{"knowledge_name": "导数", "score_rate": 50}],
        item_rows=[{"question_no": 5, "knowledge_name": "导数", "score_rate": 40}],
    )
    assert "导数" in html
    assert "第 5 题" in html
