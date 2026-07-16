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


def test_pick_score_from_score_rows_skips_rank_when_only_self():
    """按学号过滤后仅 1 行时不得误算为第 1 名。"""
    from src.agent.education.tools import _pick_score_from_score_rows

    sid = "2024_STU20260002_YZZX_8955"
    score, rank = _pick_score_from_score_rows(
        [{"student_id": sid, "score": 122.0}],
        sid,
    )
    assert score == 122.0
    assert rank is None


def test_pick_student_overview_computes_rank_from_full_class_table():
    """上游全班得分表（无排名列）应推算出正确班排，不被后续单人结果覆盖。"""
    from src.agent.education.tools import _pick_student_overview_from_report

    sid = "2024_STU20260002_YZZX_8955"
    # 构造：本人 122，另有 16 人更高 → 第 17 名；后面再跟一条单人查询
    class_rows = []
    for i in range(16):
        class_rows.append([f"H{i}", 145 - i * 0.5, "高三(10)班"])
    class_rows.append([sid, 122.0, "高三(10)班"])
    for i in range(35):
        class_rows.append([f"L{i}", 100 - i * 0.5, "高三(10)班"])
    report_data = {
        "sub_tasks": [
            {
                "sub_task_agent": "DataAnalyst",
                "exec_result": {
                    "columns": ["student_id", "score", "class"],
                    "rows": class_rows,
                },
            },
            {
                "sub_task_agent": "DataAnalyst",
                "exec_result": {
                    "columns": ["student_id", "score"],
                    "rows": [[sid, 122.0]],
                },
            },
        ]
    }
    overview = _pick_student_overview_from_report(report_data, sid)
    assert overview.get("total_score") == 122.0
    assert overview.get("class_rank") == 17
    assert overview.get("class_name") == "高三(10)班"


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


def test_parse_rank_value_from_chinese():
    from src.agent.education.tools import _parse_rank_value

    assert _parse_rank_value("第 17 名") == 17
    assert _parse_rank_value(17) == 17
    assert _parse_rank_value("17.0") == 17
    assert _parse_rank_value("-") is None


def test_build_item_table_html_student_row_fields():
    from src.agent.education.subject_diagnosis import build_item_table_html

    html = build_item_table_html(
        [{"question_no": 1, "knowledge_name": "集合", "score": 4, "question_score": 5, "score_rate": 80.0}]
    )
    assert "<table" in html
    assert "得分" in html
    assert "4" in html
    assert "5" in html


def test_build_item_compare_table_html_per_class_avg():
    from src.agent.education.subject_diagnosis import build_item_compare_table_html

    html = build_item_compare_table_html(
        [
            {
                "class_name": "高一(1)班",
                "question_no": 1,
                "knowledge_name": "集合",
                "full_score": 5,
                "avg_score": 3.5,
                "score_rate": 70,
                "question_type": "选择",
            },
            {
                "class_name": "高一(2)班",
                "question_no": 1,
                "knowledge_name": "集合",
                "full_score": 5,
                "avg_score": 4.0,
                "score_rate": 80,
                "question_type": "选择",
            },
        ]
    )
    assert "高一(1)班均分" in html
    assert "高一(2)班均分" in html
    assert "3.50" in html
    assert "4.00" in html
    assert "得分率" not in html
    assert "区分度" not in html


def test_build_knowledge_compare_table_html_per_class_rate():
    from src.agent.education.subject_diagnosis import build_knowledge_compare_table_html

    html = build_knowledge_compare_table_html(
        [
            {"class_name": "一班", "knowledge_name": "诱导公式", "question_count": 1, "score_rate": 67.0},
            {"class_name": "二班", "knowledge_name": "诱导公式", "question_count": 1, "score_rate": 72.5},
        ]
    )
    assert "一班得分率" in html
    assert "二班得分率" in html
    assert "67.00%" in html
    assert "72.50%" in html
    assert "掌握水平" not in html


def test_build_knowledge_class_rows_from_items_unifies_per_question():
    """各班同一题号挂到不同知识点时，统一后不膨胀，缺班格可补齐。"""
    from src.agent.education.subject_diagnosis import (
        build_knowledge_class_rows_from_items,
        build_knowledge_compare_table_html,
    )

    item_rows = [
        {
            "class_name": "高三(10)班",
            "question_no": 15,
            "knowledge_name": "圆的方程",
            "avg_score": 3.92,
            "full_score": 5,
            "score_rate": 78.46,
        },
        {
            "class_name": "高三(11)班",
            "question_no": 15,
            "knowledge_name": "未关联知识点",  # 关联异常
            "avg_score": 3.25,
            "full_score": 5,
            "score_rate": 65.0,
        },
        {
            "class_name": "高三(9)班",
            "question_no": 15,
            "knowledge_name": "圆的方程",
            "avg_score": 3.2,
            "full_score": 5,
            "score_rate": 64.0,
        },
        {
            "class_name": "高三(10)班",
            "question_no": 1,
            "knowledge_name": "集合",
            "avg_score": 4.0,
            "full_score": 5,
            "score_rate": 80.0,
        },
        {
            "class_name": "高三(11)班",
            "question_no": 1,
            "knowledge_name": "集合",
            "avg_score": 3.5,
            "full_score": 5,
            "score_rate": 70.0,
        },
        {
            "class_name": "高三(9)班",
            "question_no": 1,
            "knowledge_name": "集合",
            "avg_score": 3.8,
            "full_score": 5,
            "score_rate": 76.0,
        },
    ]
    kn_rows = build_knowledge_class_rows_from_items(item_rows)
    names = {r["knowledge_name"] for r in kn_rows}
    assert names == {"圆的方程", "集合"}
    assert "未关联知识点" not in names
    assert sum(1 for r in kn_rows if r["knowledge_name"] == "圆的方程") == 3
    rates_circle = {
        r["class_name"]: r["score_rate"]
        for r in kn_rows
        if r["knowledge_name"] == "圆的方程"
    }
    assert rates_circle["高三(11)班"] == 65.0
    html = build_knowledge_compare_table_html(kn_rows)
    assert "圆的方程" in html
    assert "65.00%" in html
    # 涉及题数之和应等于卷面题数（每题唯一知识点）
    q_counts = {
        r["knowledge_name"]: r["question_count"]
        for r in kn_rows
    }
    assert q_counts["圆的方程"] == 1
    assert q_counts["集合"] == 1
    assert sum(q_counts.values()) == 2


def test_apply_grade_compare_uses_knowledge_class_sql_rows():
    """横向对比：知识点表只信加权 SQL 行，不再由小题行反推覆盖。"""
    from src.agent.education.tools import _apply_grade_compare_section_tables

    data: dict = {}
    _apply_grade_compare_section_tables(
        data,
        items=[],
        knowledge=[],
        item_class_rows=[
            {
                "class_name": "1班",
                "question_no": 3,
                "knowledge_name": "圆的方程",
                "avg_score": 4,
                "full_score": 5,
                "score_rate": 80,
                "question_type": "选择",
            },
            {
                "class_name": "2班",
                "question_no": 3,
                "knowledge_name": "函数、导数",
                "avg_score": 3,
                "full_score": 5,
                "score_rate": 60,
                "question_type": "选择",
            },
        ],
        knowledge_class_rows=[
            {"class_name": "1班", "knowledge_name": "圆的方程", "question_count": 1, "score_rate": 80},
            {"class_name": "2班", "knowledge_name": "圆的方程", "question_count": 1, "score_rate": 60},
        ],
        is_grade_compare=True,
    )
    assert "圆的方程" in data["KNOWLEDGE_TABLE"]
    assert "函数" not in data["KNOWLEDGE_TABLE"]
    assert "60.00%" in data["KNOWLEDGE_TABLE"]
    assert "80.00%" in data["KNOWLEDGE_TABLE"]


def test_build_question_type_compare_table_html():
    from src.agent.education.knowledge_tier import build_question_type_compare_table_html

    html = build_question_type_compare_table_html(
        [
            {"class_name": "A班", "question_type": "选择", "question_no": 1, "score_rate": 70},
            {"class_name": "A班", "question_type": "选择", "question_no": 2, "score_rate": 80},
            {"class_name": "B班", "question_type": "选择", "question_no": 1, "score_rate": 60},
            {"class_name": "B班", "question_type": "选择", "question_no": 2, "score_rate": 70},
        ]
    )
    assert "A班得分率" in html
    assert "B班得分率" in html
    assert "选择" in html
    assert "75.00%" in html  # A班 (70+80)/2
    assert "65.00%" in html  # B班


def test_apply_grade_compare_skips_student_archive_logic():
    """横向对比组装：使用各班表且不依赖学生档案。"""
    from src.agent.education.tools import _apply_grade_compare_section_tables

    data: dict = {}
    _apply_grade_compare_section_tables(
        data,
        items=[{"question_no": 1, "knowledge_name": "集合", "avg_score": 3, "full_score": 5, "score_rate": 60}],
        knowledge=[{"knowledge_name": "集合", "score_rate": 60, "question_count": 1, "level": "及格"}],
        item_class_rows=[
            {
                "class_name": "1班",
                "question_no": 1,
                "knowledge_name": "集合",
                "avg_score": 3,
                "full_score": 5,
                "score_rate": 60,
                "question_type": "选择",
            },
            {
                "class_name": "2班",
                "question_no": 1,
                "knowledge_name": "集合",
                "avg_score": 4,
                "full_score": 5,
                "score_rate": 80,
                "question_type": "选择",
            },
        ],
        knowledge_class_rows=[
            {"class_name": "1班", "knowledge_name": "集合", "question_count": 1, "score_rate": 60},
            {"class_name": "2班", "knowledge_name": "集合", "question_count": 1, "score_rate": 80},
        ],
        is_grade_compare=True,
    )
    assert "1班均分" in data["ITEM_TABLE"]
    assert "2班均分" in data["ITEM_TABLE"]
    assert "得分率" not in data["ITEM_TABLE"]
    assert "1班得分率" in data["KNOWLEDGE_TABLE"]
    assert "group_compare_bar" in str(data.get("KNOWLEDGE_CHART") or "") or "1班" in str(
        data.get("KNOWLEDGE_CHART") or ""
    )
    assert data.get("QUESTION_TYPE_TABLE")
    assert "1班得分率" in data["QUESTION_TYPE_TABLE"]


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


def test_subject_diagnosis_template_hides_empty_student_archive():
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[2]
        / "src/agent/resource/templates/education/subject_diagnosis.html"
    )
    text = path.read_text(encoding="utf-8")
    assert "studentArchiveBody" in text
    assert "sec.style.display = 'none'" in text

def test_normalize_link_weights_equal_and_unequal():
    from src.agent.education.subject_diagnosis import (
        normalize_link_weights,
        weighted_score_contributions,
    )

    assert normalize_link_weights([1.0]) == [1.0]
    assert normalize_link_weights([1, 1]) == [0.5, 0.5]
    assert normalize_link_weights([1, 3]) == [0.25, 0.75]

    half = weighted_score_contributions(8.0, 10.0, [1, 1])
    assert half == [(4.0, 5.0), (4.0, 5.0)]
    split = weighted_score_contributions(8.0, 10.0, [1, 3])
    assert split == [(2.0, 2.5), (6.0, 7.5)]
    # 单链接 weight=1 与整题计入一致
    single = weighted_score_contributions(8.0, 10.0, [1])
    assert single == [(8.0, 10.0)]


def test_resolve_question_knowledge_map_canonicalizes_multi_name():
    from src.agent.education.subject_diagnosis import resolve_question_knowledge_map

    mapping = resolve_question_knowledge_map(
        [
            {"question_no": 1, "knowledge_name": "导数、函数"},
            {"question_no": 1, "knowledge_name": "函数、导数"},
        ]
    )
    assert mapping[1] == "函数、导数"
