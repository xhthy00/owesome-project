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
    assert "知识点掌握" in html or "函数" in html
    assert "函数" in html


def test_build_diagnosis_summary_rich_layout():
    html = build_diagnosis_summary(
        school_name="南京市第一中学",
        exam_name="期末质量检测",
        subject_name="数学",
        stats={"count": 40, "avg": 86, "pass_rate": 90, "excellent_rate": 20},
        knowledge_rows=[{"knowledge_name": "函数", "score_rate": 52}],
    )
    assert "edu-diag" in html
    assert "edu-diag-overview" in html
    assert "edu-diag-chip" in html


def test_build_diagnosis_recommendations_for_weak_points():
    html = build_diagnosis_recommendations(
        knowledge_rows=[{"knowledge_name": "导数", "score_rate": 50}],
        item_rows=[{"question_no": 5, "knowledge_name": "导数", "score_rate": 40}],
    )
    assert "导数" in html
    assert "第 5 题" in html
    assert "edu-rec-group" in html
    assert "专题课" in html or "错题" in html


def test_build_diagnosis_recommendations_personal_differentiated():
    html = build_diagnosis_recommendations(
        knowledge_rows=[
            {"knowledge_name": "对数函数", "score_rate": 20},
            {"knowledge_name": "条件概率", "score_rate": 45},
        ],
        item_rows=[
            {"question_no": 8, "knowledge_name": "对数函数", "score_rate": 20, "question_type": "解答"},
            {"question_no": 3, "knowledge_name": "条件概率", "score_rate": 40, "question_type": "选择"},
        ],
        audience="student",
    )
    assert "edu-rec-intro" in html
    assert "严重薄弱" in html or "每天 15" in html
    assert "掌握不稳" in html or "错题本" in html
    assert "安排专项练习、错题回顾与专题课" not in html
    assert "薄弱知识点专项" in html
    assert "题型突破计划" in html


def test_build_diagnosis_recommendations_class_when_all_above_threshold():
    """整体达标时不应只给「保持现有节奏」，应有 KPI / 相对薄弱建设性建议。"""
    html = build_diagnosis_recommendations(
        knowledge_rows=[
            {"knowledge_name": "集合", "score_rate": 70},
            {"knowledge_name": "函数", "score_rate": 66},
            {"knowledge_name": "导数", "score_rate": 64},
        ],
        item_rows=[
            {"question_no": 1, "knowledge_name": "集合", "score_rate": 70, "question_type": "选择"},
            {"question_no": 8, "knowledge_name": "导数", "score_rate": 62, "question_type": "解答"},
            {"question_no": 5, "knowledge_name": "函数", "score_rate": 65, "question_type": "填空"},
            {"question_no": 10, "knowledge_name": "导数", "score_rate": 63, "question_type": "解答"},
        ],
        stats={
            "count": 52,
            "avg": 100,
            "pass_rate": 61.54,
            "excellent_rate": 21.15,
            "full_score": 150,
        },
        weak_threshold=60.0,
    )
    assert "保持现有节奏" not in html
    assert "班级提质目标" in html
    assert "及格临界" in html or "过关" in html
    assert "相对巩固知识点" in html or "导数" in html
    assert "题型突破" in html
    assert "edu-rec-intro" in html
    assert "限时" in html or "面批" in html or "专练" in html


def test_build_diagnosis_summary_dedupes_multi_exam_participants():
    """多场考试混算时参考人数按去重学生，不展示 52×3=156。"""
    score_rows = []
    for exam in ("一模", "二模", "三模"):
        for i in range(52):
            score_rows.append({
                "student_id": f"S{i:03d}",
                "exam_name": exam,
                "score": 100.0,
                "exam_score": 150.0,
            })
    html = build_diagnosis_summary(
        school_name="扬州中学",
        subject_name="数学",
        stats={"count": 156, "avg": 100, "pass_rate": 61.54, "excellent_rate": 21.15},
        score_rows=score_rows,
    )
    assert "参考人数" in html
    assert ">52<" in html or ">52</div>" in html or "52" in html
    assert "156" in html  # 仍提示共 156 条成绩
    assert "去重" in html or "跨 3 场" in html


def test_ability_portrait_insight_points_out_strengths_and_weaknesses():
    from src.agent.education.knowledge_tier import (
        build_ability_tier_insight,
        build_ability_tier_summary,
    )

    knowledge = [
        {"knowledge_name": "命题及其关系", "score_rate": 100},
        {"knowledge_name": "对数函数", "score_rate": 20},
        {"knowledge_name": "条件概率与全概率公式", "score_rate": 40},
    ]
    items = [
        {"question_type": "填空", "score_rate": 60},
        {"question_type": "选择", "score_rate": 60},
        {"question_type": "多选", "score_rate": 73.33},
        {"question_type": "解答", "score_rate": 75.28},
    ]
    html = build_ability_tier_insight(
        build_ability_tier_summary(knowledge),
        knowledge_rows=knowledge,
        item_rows=items,
    )
    assert "整体平稳" not in html
    assert "诊断结论" in html
    assert "题型分化" in html or "问题题型" in html
    assert "选择" in html or "填空" in html
    assert "对数函数" in html
    assert "命题及其关系" in html or "亮点" in html


def test_ability_portrait_insight_no_bland_when_empty_levels_only():
    from src.agent.education.knowledge_tier import build_ability_tier_insight

    html = build_ability_tier_insight({"weak_levels": [], "by_ability_level": []})
    assert "整体平稳" not in html
    assert "暂无足够" in html


def test_pick_student_overview_rejects_student_id_as_score():
    from src.agent.education.tools import (
        _coerce_numeric_score,
        _pick_score_from_score_rows,
        _pick_student_overview_from_report,
    )

    sid = "2024_STU20260052_YZZX_3884"
    report_data = {
        "sub_tasks": [
            {
                "sub_task_agent": "DataAnalyst",
                "exec_result": {
                    "columns": ["student_id", "class_rank", "exam_score"],
                    "rows": [[sid, 30, 150]],
                },
            }
        ]
    }
    overview = _pick_student_overview_from_report(report_data, sid)
    assert overview.get("total_score") is None
    assert overview.get("class_rank") == 30
    assert _coerce_numeric_score(sid) is None

    score, rank = _pick_score_from_score_rows(
        [
            {"student_id": "other", "score": 120},
            {"student_id": sid, "score": 103},
            {"student_id": "x", "score": 90},
        ],
        sid,
    )
    assert score == 103
    assert rank == 2


def test_pick_student_overview_prefers_score_column():
    from src.agent.education.tools import _pick_student_overview_from_report

    sid = "STU001"
    report_data = {
        "sub_tasks": [
            {
                "sub_task_agent": "DataAnalyst",
                "exec_result": {
                    "columns": ["student_id", "得分", "班级排名"],
                    "rows": [[sid, 91.5, 3]],
                },
            }
        ]
    }
    overview = _pick_student_overview_from_report(report_data, sid)
    assert overview.get("total_score") == 91.5
    assert overview.get("class_rank") == 3


def test_build_item_table_html_student_row_fields():
    from src.agent.education.subject_diagnosis import build_item_table_html

    html = build_item_table_html(
        [{"question_no": 1, "knowledge_name": "集合", "score": 4, "question_score": 5, "score_rate": 80.0}]
    )
    assert "<table" in html
    assert "得分" in html
    assert "4" in html
    assert "5" in html


def test_coerce_report_table_fields_from_list():
    from src.agent.education.subject_diagnosis import coerce_report_table_fields

    data = coerce_report_table_fields(
        {
            "ITEM_TABLE": [
                {"question_no": 1, "knowledge_name": "集合", "score": 4, "question_score": 5, "score_rate": 80.0}
            ]
        }
    )
    assert isinstance(data["ITEM_TABLE"], str)
    assert "edu-table-wrap" in data["ITEM_TABLE"]
    assert "集合" in data["ITEM_TABLE"]


def test_coerce_report_table_fields_from_python_repr_string():
    from src.agent.education.subject_diagnosis import coerce_report_table_fields

    raw = "[{'question_no': 1, 'knowledge_name': '集合', 'score': 4, 'question_score': 5, 'score_rate': 80.0}]"
    data = coerce_report_table_fields({"ITEM_TABLE": raw})
    assert "<table" in data["ITEM_TABLE"]
    assert "集合" in data["ITEM_TABLE"]
