"""query_parse 与 student_exam 数据组装测试。"""

from src.agent.education.query_parse import (
    extract_school_target,
    extract_student_target,
    extract_upstream_participant_count,
    report_matches_school,
    report_matches_student,
    report_participant_count_conflicts,
    student_matches,
)
from src.agent.education.student_exam import build_student_exam_data


def _sample_class_records():
    exams = ["一模", "二模", "三模"]
    records = []
    for i, exam in enumerate(exams):
        for sid, (yw, sx, yy, total) in [
            ("学生001", (120, 99, 130, 349)),
            ("学生009", (90, 110, 95, 295)),
        ]:
            records.append({
                "exam": exam,
                "student": sid,
                "subjects": {"语文": yw + i, "数学": sx, "英语": yy + i},
                "total": total + i * 5,
            })
    return records


def test_extract_student_target_from_quoted_name():
    assert extract_student_target('分析"学生001"这几次考试的成绩') == "学生001"


def test_extract_student_id_target():
    from src.agent.education.query_parse import extract_student_id_target, is_individual_student_analysis_query

    q = (
        "查询学生编号为：STU20240003，江苏省高一上学期数学期末质量检测成绩分析，"
        "哪些知识点需要加强，形成分析报告"
    )
    assert extract_student_id_target(q) == "STU20240003"
    assert extract_student_target(q) == "STU20240003"
    assert is_individual_student_analysis_query(q) is True
    assert is_individual_student_analysis_query("帮我分析全市数学成绩") is False


def test_extract_long_student_id_and_score_query_is_individual():
    from src.agent.education.query_parse import (
        extract_exam_name_hint,
        extract_student_id_target,
        extract_student_target,
        is_individual_student_analysis_query,
    )
    from src.agent.expand.planner import (
        build_individual_student_exam_plan_items,
        should_replace_with_individual_student_plan,
    )

    q = "查询学生2024_STU20260052_YZZX_3884在连淮扬镇考试中的得分情况"
    assert extract_student_id_target(q) == "2024_STU20260052_YZZX_3884"
    assert extract_student_target(q) == "2024_STU20260052_YZZX_3884"
    assert extract_exam_name_hint(q) == "连淮扬镇"
    assert is_individual_student_analysis_query(q) is True

    plan = build_individual_student_exam_plan_items(q)
    assert len(plan) == 2
    assert plan[0]["sub_task_agent"] == "DataAnalyst"
    assert plan[1]["sub_task_agent"] == "ToolExpert"
    assert "build_student_subject_diagnosis_tool" in plan[1]["sub_task"]
    assert "2024_STU20260052_YZZX_3884" in plan[1]["sub_task"]
    assert should_replace_with_individual_student_plan(q, [{"sub_task": q, "sub_task_agent": "DataAnalyst"}])


def test_extract_school_target_from_full_name():
    q = "帮我分析南京市第一中学在江苏省高一上学期数学期末质量检测的成绩"
    assert extract_school_target(q) == "南京市第一中学"


def test_extract_school_target_before_particle_zai():
    """「扬州中学在连淮扬镇…」校名后接「在」时仍能抽出。"""
    q = "扬州中学在连淮扬镇数学考试中各个班级的横向多维对比分析"
    assert extract_school_target(q) == "扬州中学"


def test_extract_school_target_from_quoted_name():
    assert extract_school_target('分析「北京市第四中学」数学成绩') == "北京市第四中学"


def test_extract_school_target_returns_none_when_absent():
    assert extract_school_target("查询全校数学平均分") is None


def test_student_matches_aliases():
    assert student_matches("学生 001", "学生001")
    assert student_matches("001", "学生001")


def test_report_matches_student_filters_wrong_student():
    assert report_matches_student("学生001 报告", "<h1>学生001</h1>", "学生001")
    assert not report_matches_student("学生009 报告", "<h1>学生009</h1>", "学生001")


def test_report_matches_school_filters_wrong_scope():
    html = "<span>南京市第一中学</span><p>参考人数 24 人</p>"
    assert report_matches_school("南京市第一中学数学诊断", html, "南京市第一中学")
    wrong = "<span>高一(1)班+高一(2)班</span><p>参考人数 40 人</p>"
    assert not report_matches_school("高一数学学科诊断报告", wrong, "南京市第一中学")


def test_extract_upstream_participant_count_from_final_answer():
    count = extract_upstream_participant_count({
        "sub_tasks": [
            {
                "sub_task_agent": "DataAnalyst",
                "final_answer": "共 24 人，均分 78",
                "exec_result": {"columns": ["学生"], "rows": [], "row_count": 0},
            }
        ]
    })
    assert count == 24


def test_report_participant_count_conflicts_detects_mismatch():
    html = "<p>参考人数 40 人</p>"
    assert report_participant_count_conflicts(html, 24)
    assert not report_participant_count_conflicts("<p>参考人数 24 人</p>", 24)


def test_extract_exam_name_hint_rejects_vague_zhe_jici():
    from src.agent.education.query_parse import extract_exam_name_hint, is_vague_exam_name

    assert extract_exam_name_hint("分析学生001这几次的数学考试成绩") is None
    assert is_vague_exam_name("这几次") is True
    assert is_vague_exam_name("这几次考试") is True
    assert is_vague_exam_name("连淮扬镇") is False


def test_multi_exam_student_plan_uses_exam_report_tool():
    from src.agent.education.query_parse import is_multi_exam_student_analysis_query
    from src.agent.expand.planner import build_individual_student_exam_plan_items

    q = "分析学生2024_STU20260002_YZZX_8955这几次的数学考试成绩"
    assert is_multi_exam_student_analysis_query(q) is True
    plan = build_individual_student_exam_plan_items(q)
    assert len(plan) == 2
    assert "build_student_exam_report_data_tool" in plan[1]["sub_task"]
    assert plan[1]["sub_task"].index("build_student_exam_report_data_tool") < plan[1]["sub_task"].index("禁止")
    assert "数学" in plan[0]["sub_task"] or "subject_name=数学" in plan[1]["sub_task"]
    # exam_name 不应作为这几次传入正调用参数区
    call_part = plan[1]["sub_task"].split("禁止")[0]
    assert "exam_name=这几次" not in call_part
    assert "这几次" not in call_part or "禁止" in plan[1]["sub_task"]


def test_build_student_multi_exam_overview_html():
    from src.agent.education.tools import _build_student_multi_exam_overview_html

    title, badge, kpi, detail = _build_student_multi_exam_overview_html(
        [
            {"exam_name": "一模", "score": 100, "class_avg": 90, "class_max": 140, "gap_to_first": 40, "class_rank": 20},
            {"exam_name": "二模", "score": 120, "class_avg": 95, "class_max": 145, "gap_to_first": 25, "class_rank": 15},
            {"exam_name": "三模", "score": 125, "class_avg": 98, "class_max": 148, "gap_to_first": 23, "class_rank": 12},
        ],
        student_id="STU001",
    )
    assert "共3次" in title
    assert "一模" in badge and "二模" in badge
    assert "多次均分" in kpi
    assert "115" in kpi or "115.00" in kpi  # (100+120+125)/3
    assert "与第1名差距" in detail
    assert "一模" in detail and "三模" in detail
    assert "这几次" not in badge


def test_build_student_exam_data_shows_exam_count_and_gap():
    records = _sample_class_records()
    data = build_student_exam_data(records, "学生001", exam_order=["一模", "二模", "三模"], class_name="初三1班")
    assert "共3次考试" in data["EXAM_NAME"]
    assert data["EXAM_COUNT"] == "3"
    assert data["MULTI_EXAM_AVG"] not in ("", "-", None)
    assert "与第1名" in data["COVER_META"] or data.get("GAP_TO_FIRST") not in (None, "")
    assert "共3次" in data["OVERVIEW_INSIGHT"] or "3" in data["OVERVIEW_INSIGHT"]
    assert "这几次" not in data["EXAM_NAME"]


def test_build_student_exam_data_rich_sections():
    records = _sample_class_records()
    data = build_student_exam_data(records, "学生001", exam_order=["一模", "二模", "三模"], class_name="初三1班")
    assert "学生001" in data["REPORT_TITLE"]
    assert "一、总体成绩概览" not in data["REPORT_TITLE"]  # title is report name
    assert "349" in data["SCORE_SUMMARY_TABLE"] or "354" in data["SCORE_SUMMARY_TABLE"]
    assert data["SUBJECT_ANALYSIS_HTML"]
    assert "五、总体结论" not in data["ASSESSMENT"]  # assessment is content not header
    assert data["TOTAL_TREND_CHART"]
    assert data["CLASS_DIFF_TABLE"]
    assert not data["IS_SINGLE_SUBJECT"]
    assert data["OVERVIEW_SECTION_TITLE"] == "一、总体成绩概览"
    assert data["SUBJECT_RADAR_CHART"]


def test_build_student_exam_single_subject_layout():
    """仅一科时关闭各科雷达，改用单科表头与趋势/分布/散点/热力图。"""
    exams = ["一模", "二模"]
    records = []
    for exam, score in zip(exams, [132.0, 127.0]):
        for sid, sc in [("学生001", score), ("学生009", score - 20)]:
            records.append({
                "exam": exam,
                "student": sid,
                "subjects": {"数学": sc},
                "total": sc,
            })
    data = build_student_exam_data(
        records, "学生001", exam_order=exams, class_name="高三(10)班"
    )
    assert data["IS_SINGLE_SUBJECT"] == "1"
    assert "数学" in data["REPORT_TITLE"]
    assert "单科" in data["REPORT_SUBTITLE"]
    assert data["SUBJECT_NAME"] == "数学"
    assert not data["SUBJECT_RADAR_CHART"]
    assert data["TREND_LINE_CHART"]
    assert data["SCORE_DIST_CHART"]
    assert data["SCATTER_CHART"]
    assert data["HEATMAP_CHART"]  # 无知识点时退化为相对位置热力图
    assert "总分" not in data["SCORE_SUMMARY_TABLE"]
    assert "数学得分" in data["SCORE_SUMMARY_TABLE"]
    assert data["PARENT_SUBJECT_TITLE"] == "数学成绩表现"
    assert "各科" not in data["SUBJECT_SECTION_TITLE"]


def test_build_student_exam_single_subject_charts_with_knowledge():
    """单科有知识点时生成能力雷达与知识点热力图。"""
    exams = ["一模", "二模"]
    records = []
    for exam, score in zip(exams, [132.0, 127.0]):
        for sid, sc in [("学生001", score), ("学生009", score - 20)]:
            records.append({
                "exam": exam,
                "student": sid,
                "subjects": {"数学": sc},
                "total": sc,
            })
    insight = {
        "weak_items": [
            {"exam_name": "一模", "question_no": 12, "knowledge_name": "导数", "score_rate": 25.0},
            {"exam_name": "二模", "question_no": 15, "knowledge_name": "导数", "score_rate": 40.0},
            {"exam_name": "一模", "question_no": 8, "knowledge_name": "数列", "score_rate": 50.0},
            {"exam_name": "二模", "question_no": 9, "knowledge_name": "数列", "score_rate": 55.0},
            {"exam_name": "一模", "question_no": 3, "knowledge_name": "集合", "score_rate": 90.0},
            {"exam_name": "二模", "question_no": 4, "knowledge_name": "集合", "score_rate": 88.0},
        ],
        "weak_knowledge": [
            {"knowledge_name": "导数", "score_rate": 32.0, "question_count": 2},
            {"knowledge_name": "数列", "score_rate": 52.0, "question_count": 2},
        ],
        "strong_knowledge": [
            {"knowledge_name": "集合", "score_rate": 89.0, "question_count": 2},
        ],
        "knowledge_rows": [
            {"knowledge_name": "导数", "score_rate": 32.0, "question_count": 2},
            {"knowledge_name": "数列", "score_rate": 52.0, "question_count": 2},
            {"knowledge_name": "集合", "score_rate": 89.0, "question_count": 2},
        ],
    }
    data = build_student_exam_data(
        records, "学生001", exam_order=exams, class_name="高三(10)班", item_insight=insight
    )
    assert data["ABILITY_RADAR_CHART"]
    assert "heatmap" in data["HEATMAP_CHART"] or "导数" in data["HEATMAP_CHART"]
    assert data["SCORE_DIST_CHART"]
    assert data["SCATTER_CHART"]


def test_build_student_exam_only_one_student_in_title():
    records = _sample_class_records()
    data = build_student_exam_data(records, "学生001", exam_order=["一模", "二模", "三模"])
    assert "学生009" not in data["REPORT_TITLE"]


def test_build_student_exam_includes_knowledge_detail_and_advice():
    records = _sample_class_records()
    insight = {
        "weak_items": [
            {
                "exam_name": "一模",
                "question_no": 12,
                "knowledge_name": "导数与极值最值",
                "score_rate": 25.0,
            },
            {
                "exam_name": "二模",
                "question_no": 15,
                "knowledge_name": "等差数列",
                "score_rate": 40.0,
            },
        ],
        "weak_knowledge": [
            {"knowledge_name": "导数与极值最值", "score_rate": 35.0, "question_count": 3},
            {"knowledge_name": "等差数列", "score_rate": 48.0, "question_count": 2},
        ],
        "strong_knowledge": [
            {"knowledge_name": "集合与逻辑", "score_rate": 92.0, "question_count": 2},
        ],
        "knowledge_rows": [
            {"knowledge_name": "导数与极值最值", "score_rate": 35.0, "question_count": 3},
            {"knowledge_name": "等差数列", "score_rate": 48.0, "question_count": 2},
            {"knowledge_name": "集合与逻辑", "score_rate": 92.0, "question_count": 2},
        ],
    }
    data = build_student_exam_data(
        records,
        "学生001",
        exam_order=["一模", "二模", "三模"],
        item_insight=insight,
    )
    assert "导数与极值最值" in data["KNOWLEDGE_TABLE"]
    assert "等差数列" in data["WEAK_ITEM_TABLE"]
    assert "知识点薄弱" in data["RECOMMENDATIONS"] or "导数与极值最值" in data["RECOMMENDATIONS"]
    assert "导数与极值最值" in data["ASSESSMENT"]
    assert data["KNOWLEDGE_CHART"]
    assert "薄弱知识点" in data["KNOWLEDGE_INSIGHT"]


def test_extract_item_detail_rows_from_upstream_sql():
    from src.agent.education.query_parse import extract_item_detail_rows_from_report_data

    report_data = {
        "sub_tasks": [
            {
                "sub_task_agent": "DataAnalyst",
                "tool_calls": [
                    {
                        "tool": "execute_sql",
                        "success": True,
                        "data": {
                            "columns": [
                                "student_id",
                                "exam_name",
                                "question_no",
                                "knowledge_name",
                                "score",
                                "full_score",
                                "score_rate",
                            ],
                            "rows": [
                                [
                                    "2024_STU20260052_YZZX_3884",
                                    "2026年扬州市高三数学调研测试",
                                    12,
                                    "导数与极值最值",
                                    2,
                                    5,
                                    40.0,
                                ],
                                [
                                    "2024_STU20260052_YZZX_3884",
                                    "2026年扬州市高三数学调研测试",
                                    15,
                                    "等差数列",
                                    3,
                                    5,
                                    60.0,
                                ],
                                [
                                    "OTHER",
                                    "一模",
                                    1,
                                    "集合",
                                    5,
                                    5,
                                    100.0,
                                ],
                            ],
                            "row_count": 3,
                        },
                    }
                ],
            }
        ]
    }
    rows = extract_item_detail_rows_from_report_data(
        report_data, student_id="2024_STU20260052_YZZX_3884"
    )
    assert len(rows) == 2
    assert rows[0]["knowledge_name"] == "导数与极值最值"


def test_build_student_exam_uses_upstream_item_rows_without_datasource():
    """无 datasource 时也能吃上游知识点 SQL，第四节不应空白。"""
    from src.agent.education.tools import build_student_exam_report_data_tool

    records = _sample_class_records()
    # 把学生001 换成学号形态，贴近真实报告
    for r in records:
        if r["student"] == "学生001":
            r["student"] = "2024_STU20260052_YZZX_3884"

    report_data = {
        "sub_tasks": [
            {
                "sub_task_agent": "DataAnalyst",
                "exec_result": {
                    "columns": ["exam_name", "student_id", "subject_name", "score"],
                    "rows": [
                        ["一模", "2024_STU20260052_YZZX_3884", "数学", 99],
                        ["一模", "学生009", "数学", 110],
                        ["二模", "2024_STU20260052_YZZX_3884", "数学", 99],
                        ["二模", "学生009", "数学", 110],
                    ],
                    "row_count": 4,
                },
                "tool_calls": [
                    {
                        "tool": "execute_sql",
                        "data": {
                            "columns": [
                                "student_id",
                                "exam_name",
                                "question_no",
                                "knowledge_name",
                                "score_rate",
                            ],
                            "rows": [
                                [
                                    "2024_STU20260052_YZZX_3884",
                                    "一模",
                                    12,
                                    "导数与极值最值",
                                    25.0,
                                ],
                                [
                                    "2024_STU20260052_YZZX_3884",
                                    "二模",
                                    8,
                                    "等差数列",
                                    40.0,
                                ],
                            ],
                            "row_count": 2,
                        },
                    }
                ],
            }
        ]
    }
    result = build_student_exam_report_data_tool._fn(
        student_id="2024_STU20260052_YZZX_3884",
        render=False,
        report_data=report_data,
        tool_runtime_ctx={"report_data": report_data},
        records=records,
    )
    assert result.data.get("error") != "missing input"
    assert "导数与极值最值" in (result.data.get("KNOWLEDGE_TABLE") or "")
    assert "知识点薄弱" in (result.data.get("RECOMMENDATIONS") or "") or "导数" in (
        result.data.get("RECOMMENDATIONS") or ""
    )
