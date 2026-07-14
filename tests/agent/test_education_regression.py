"""教育学情改造非回归与新增能力测试。"""

from __future__ import annotations

from src.agent.education.aggregation import aggregate_by
from src.agent.education.config import EducationConfig
from src.agent.education.diagnostic_report import build_diagnostic_data
from src.agent.education.dimension_parse import parse_grade_from_class
from src.agent.education.report_types import ReportType
from src.agent.education.stats import (
    compute_item_metrics,
    compute_knowledge_mastery,
    compute_score_stats,
    normalize_segments,
)
from src.agent.education.subject_diagnosis import build_segment_table_html
from src.agent.education.query_parse import (
    extract_score_rows_from_report_data,
    is_citywide_analysis_query,
)
from src.agent.education.orchestrator import ReportIntentResolver
from src.agent.education.templates import select_report_template


def test_compute_score_stats_legacy_fields_unchanged():
    cfg = EducationConfig()
    stats = compute_score_stats([60, 70, 80, 90], cfg, 100.0)
    assert stats["count"] == 4
    assert stats["avg"] == 75.0
    assert stats["pass_rate"] == 100.0
    assert stats["excellent_rate"] == 25.0
    assert stats["fail_rate"] == 0.0
    assert len(stats["segments"]) == 5


def test_compute_score_stats_new_fields():
    cfg = EducationConfig()
    stats = compute_score_stats([30, 50, 75, 90], cfg, 100.0)
    assert stats["good_rate"] == 50.0
    assert stats["low_score_rate"] == 25.0
    assert stats["max"] == 90
    assert stats["min"] == 30


def test_parse_grade_from_class():
    assert parse_grade_from_class("高一(1)班") == "高一"
    assert parse_grade_from_class("初三2班") == "初三"
    assert parse_grade_from_class("九年级3班") == "九年级"


def test_aggregate_by_grade_from_class():
    cfg = EducationConfig()
    rows = [
        {"score": 80, "class": "高一(1)班", "exam_score": 100},
        {"score": 70, "class": "高一(2)班", "exam_score": 100},
        {"score": 60, "class": "高二(1)班", "exam_score": 100},
    ]
    result = aggregate_by("grade", rows, cfg)
    keys = {r["dimension_value"] for r in result}
    assert "高一" in keys
    assert "高二" in keys


def test_score_segment_label_maps_score():
    from src.agent.education.stats import score_segment_label

    assert score_segment_label(95, full_score=150) == "90-105"
    assert score_segment_label(50, full_score=150) == "0-90"


def test_cross_analyze_class_score_segment_has_labels():
    from src.agent.education.cross_analysis import cross_analyze

    rows = [
        {"class": "高一(1)班", "score": 100, "exam_score": 150},
        {"class": "高一(1)班", "score": 80, "exam_score": 150},
        {"class": "高一(2)班", "score": 110, "exam_score": 150},
    ]
    out = cross_analyze("class", "score_segment", rows)
    assert "未知" not in out["cols"]
    assert len(out["cols"]) >= 2


def test_build_segment_table_has_wrap_and_num_class():
    from src.agent.education.subject_diagnosis import build_segment_table_html

    html = build_segment_table_html([{"count": 10}, {"count": 5}], full_score=150)
    assert "edu-table-wrap" in html
    assert "num" in html


def test_diagnostic_report_data_keys():
    rows = [
        {"score": 85, "class": "高一(1)班", "exam_score": 100, "district": "鼓楼区"},
        {"score": 75, "class": "高一(2)班", "exam_score": 100, "district": "鼓楼区"},
    ]
    data = build_diagnostic_data(rows, scope_label="南京市第一中学", subject_name="数学")
    assert "GENERAL_INSIGHT" in data
    assert "SPECIAL_INSIGHT" in data
    assert "DYNAMIC_INSIGHT" in data
    assert "KPI_GRID" in data


def test_diagnostic_report_type_registered():
    info = select_report_template(ReportType.DIAGNOSTIC_REPORT)
    assert info["template_name"] == "education/diagnostic_report.html"
    assert "KPI_GRID" in info["data_keys"]


def test_item_metrics_difficulty():
    items = [{"question_no": 1, "score_rate": 80.0}]
    out = compute_item_metrics(items)
    assert out[0]["difficulty"] == 0.2


def test_knowledge_mastery_by_level():
    rows = [
        {"knowledge_name": "K1", "score_rate": 50, "ability_level": "basic"},
        {"knowledge_name": "K2", "score_rate": 90, "ability_level": "advanced"},
    ]
    m = compute_knowledge_mastery(rows)
    assert len(m["by_ability_level"]) == 2


def test_normalize_segments_fills_label_and_ratio():
    raw = [{"count": 1}, {"count": 21}, {"count": 18}]
    out = normalize_segments(raw, full_score=150.0)
    assert len(out) == 3
    assert all(s.get("label") for s in out)
    assert out[0]["ratio"] == round(1 / 40 * 100, 2)
    html = build_segment_table_html(raw, full_score=150.0)
    assert "90-135" in html or "段" in html
    assert "-%" not in html


def test_is_citywide_analysis_query():
    q = "帮我分析全市的江苏省高一上学期数学期末质量检测试卷的成绩分析，形成详细报告"
    assert is_citywide_analysis_query(q) is True


def test_intent_resolver_student_knowledge_routes_profile():
    q = (
        "查询学生编号为：STU20240003，江苏省高一上学期数学期末质量检测成绩分析，"
        "哪些知识点需要加强，形成分析报告"
    )
    spec = ReportIntentResolver().resolve(q)
    assert spec.report_type == ReportType.STUDENT_PROFILE


def test_intent_resolver_citywide_routes_diagnostic():
    ir = ReportIntentResolver()
    q = "帮我分析全市的江苏省高一上学期数学期末质量检测试卷的成绩分析，形成详细报告"
    spec = ir.resolve(q)
    assert spec.report_type == ReportType.DIAGNOSTIC_REPORT
    assert spec.filters.get("scope") == "全市"


def test_extract_score_rows_from_report_data():
    report_data = {
        "sub_tasks": [
            {
                "sub_task_agent": "DataAnalyst",
                "exec_result": {
                    "columns": ["score", "exam_score", "district", "class"],
                    "rows": [[80 + i, 100, "鼓楼区", f"高一({i})班"] for i in range(40)],
                    "row_count": 40,
                },
            }
        ]
    }
    rows = extract_score_rows_from_report_data(report_data)
    assert len(rows) == 40
    assert rows[0]["district"] == "鼓楼区"


def test_diagnostic_report_district_summary():
    rows = [
        {"score": 85, "class": "高一(1)班", "exam_score": 100, "district": "鼓楼区"},
        {"score": 75, "class": "高一(2)班", "exam_score": 100, "district": "玄武区"},
    ]
    data = build_diagnostic_data(rows, scope_label="全市", subject_name="数学", exam_name="期末检测")
    assert data.get("DISTRICT_SUMMARY")
    assert "鼓楼区" in data["DISTRICT_SUMMARY"]
    assert data.get("DISTRICT_COMPARE_CHART")


def test_fetch_blocks_repeat_in_build_subtask():
    from src.agent.education.tools import fetch_subject_diagnosis_data_tool

    result = fetch_subject_diagnosis_data_tool._fn(
        datasource_id=1,
        sub_task="调 build_diagnostic_report_data_tool(scope_label=全市, render=true)",
    )
    assert result.data.get("error") == "fetch_not_allowed_in_build_subtask"


def test_fetch_allowed_in_planner_fetch_subtask_text():
    from src.agent.education.tools import fetch_subject_diagnosis_data_tool

    sub_task = (
        "调 fetch_subject_diagnosis_data_tool(subject_name=数学, exam_name=期末质量检测) "
        "查询全市小题明细与知识点——**本步仅 fetch，禁止 render**；"
        "完成后 terminate（**禁止**调 build_diagnostic_report_data_tool）"
    )
    result = fetch_subject_diagnosis_data_tool._fn(
        datasource_id=1,
        sub_task=sub_task,
    )
    assert result.data.get("error") != "fetch_not_allowed_in_build_subtask"


def test_build_diagnostic_blocks_render_in_fetch_subtask():
    from src.agent.education.tools import build_diagnostic_report_data_tool

    result = build_diagnostic_report_data_tool._fn(
        sub_task="调 fetch_subject_diagnosis_data_tool(subject_name=数学)",
        render=True,
    )
    assert result.data.get("error") == "render_not_allowed_in_fetch_subtask"


def test_build_diagnostic_blocks_render_in_planner_fetch_subtask_text():
    from src.agent.education.tools import build_diagnostic_report_data_tool

    sub_task = (
        "调 fetch_subject_diagnosis_data_tool(subject_name=数学, exam_name=期末) "
        "——**本步仅 fetch**；terminate（**禁止**调 build_diagnostic_report_data_tool）"
    )
    result = build_diagnostic_report_data_tool._fn(
        sub_task=sub_task,
        render=True,
    )
    assert result.data.get("error") == "render_not_allowed_in_fetch_subtask"


def test_build_diagnostic_allowed_in_planner_build_subtask_text():
    from src.agent.education.tools import build_diagnostic_report_data_tool

    sub_task = (
        "调 build_diagnostic_report_data_tool(scope_label=全市, exam_name=期末, "
        "subject_name=数学, render=true)；**禁止**再调 fetch_subject_diagnosis_data_tool"
    )
    result = build_diagnostic_report_data_tool._fn(
        sub_task=sub_task,
        render=False,
    )
    assert result.data.get("error") != "render_not_allowed_in_fetch_subtask"


def test_extract_upstream_participant_count_prefers_sql_row_count():
    from src.agent.education.query_parse import extract_upstream_participant_count

    count = extract_upstream_participant_count({
        "sub_tasks": [
            {
                "sub_task_agent": "DataAnalyst",
                "final_answer": "共 20 人，均分 89",
                "exec_result": {
                    "columns": ["score", "exam_score", "district", "class"],
                    "rows": [[80 + i, 100, "鼓楼区", f"高一({i})班"] for i in range(40)],
                    "row_count": 40,
                },
            }
        ]
    })
    assert count == 40


def test_resolve_diagnostic_score_rows_prefers_upstream_over_llm_preview():
    from src.agent.education.query_parse import resolve_diagnostic_score_rows

    report_data = {
        "sub_tasks": [
            {
                "sub_task_agent": "DataAnalyst",
                "exec_result": {
                    "columns": ["score", "exam_score", "district", "class"],
                    "rows": [[80 + i, 100, "鼓楼区", f"高一({i})班"] for i in range(40)],
                    "row_count": 40,
                },
            }
        ]
    }
    llm_rows = [{"score": 80 + i, "exam_score": 100} for i in range(20)]
    rows = resolve_diagnostic_score_rows(score_rows=llm_rows, report_data=report_data)
    assert len(rows) == 40


def test_resolve_stats_input_prefers_last_exec_result():
    from src.agent.education.query_parse import resolve_stats_input

    full_rows = [[80 + i, 150] for i in range(40)]
    scores, er = resolve_stats_input(
        scores=[float(r[0]) for r in full_rows[:20]],
        exec_result={
            "columns": ["score", "exam_score"],
            "rows": full_rows[:20],
            "row_count": 20,
        },
        last_exec_result={
            "columns": ["score", "exam_score"],
            "rows": full_rows,
            "row_count": 40,
        },
    )
    assert scores is None
    assert er is not None
    assert er["row_count"] == 40


def test_resolve_comprehensive_table_input_prefers_full_sql():
    from src.agent.education.query_parse import resolve_comprehensive_table_input

    full_rows = [[f"考{e}", f"s{s}", 90] for e in range(4) for s in range(52)]
    records, rows, columns, used = resolve_comprehensive_table_input(
        records=[{"exam": "考0", "student": f"s{i}", "subjects": {"数学": 90}, "total": 90} for i in range(20)],
        last_exec_result={
            "columns": ["exam_name", "student_name", "score"],
            "rows": full_rows,
            "row_count": 208,
        },
    )
    assert used is True
    assert records is None
    assert len(rows) == 208
    assert columns == ["exam_name", "student_name", "score"]


def test_extract_best_exec_prefers_student_detail_over_kpi():
    from src.agent.education.query_parse import extract_best_exec_result_from_report_data

    report_data = {
        "sub_tasks": [
            {
                "sub_task_agent": "DataAnalyst",
                "exec_result": {
                    "columns": ["exam_name", "avg_score", "pass_rate"],
                    "rows": [["摸底", 108.5, 0.75], ["一模", 110.8, 0.77]],
                    "row_count": 2,
                },
            },
            {
                "sub_task_agent": "DataAnalyst",
                "exec_result": {
                    "columns": ["exam_name", "student_id", "score"],
                    "rows": [[f"考{e}", f"STU{s}", 90] for e in range(4) for s in range(52)],
                    "row_count": 208,
                },
            },
        ]
    }
    best = extract_best_exec_result_from_report_data(report_data)
    assert best is not None
    assert best["row_count"] == 208
    assert "student_id" in best["columns"]


def test_extract_best_exec_from_tool_calls_when_exec_result_missing():
    from src.agent.education.query_parse import extract_best_exec_result_from_report_data

    rows = [[f"考{e}", f"STU{s}", 90] for e in range(2) for s in range(10)]
    report_data = {
        "sub_tasks": [
            {
                "sub_task_agent": "DataAnalyst",
                "exec_result": None,
                "tool_calls": [
                    {
                        "tool": "execute_sql",
                        "success": True,
                        "data": {
                            "columns": ["exam_name", "student_id", "score"],
                            "rows": rows,
                            "row_count": len(rows),
                        },
                    }
                ],
            }
        ]
    }
    best = extract_best_exec_result_from_report_data(report_data)
    assert best is not None
    assert best["row_count"] == 20


def test_build_student_exam_report_uses_upstream_report_data():
    from src.agent.education.tools import build_student_exam_report_data_tool

    report_data = {
        "sub_tasks": [
            {
                "sub_task_agent": "DataAnalyst",
                "exec_result": {
                    "columns": ["exam_name", "student_id", "subject_name", "score"],
                    "rows": [
                        ["摸底", "学生001", "数学", 90],
                        ["摸底", "学生002", "数学", 80],
                        ["一模", "学生001", "数学", 95],
                        ["一模", "学生002", "数学", 85],
                    ],
                    "row_count": 4,
                },
            }
        ]
    }
    result = build_student_exam_report_data_tool._fn(
        student_name="学生001",
        class_name="高三（10）班",
        render=False,
        report_data=report_data,
        tool_runtime_ctx={"report_data": report_data},
    )
    assert result.data is not None
    assert result.data.get("error") != "missing input"
    assert "学生001" in (result.data.get("REPORT_TITLE") or result.content or "")


def test_build_diagnostic_report_kpi_uses_full_upstream_rows():
    from src.agent.education.tools import build_diagnostic_report_data_tool

    report_data = {
        "sub_tasks": [
            {
                "sub_task_agent": "DataAnalyst",
                "exec_result": {
                    "columns": ["score", "exam_score", "district", "class"],
                    "rows": [[80 + i, 100, "鼓楼区", f"高一({i})班"] for i in range(40)],
                    "row_count": 40,
                },
            }
        ]
    }
    llm_rows = [{"score": 80 + i, "exam_score": 100} for i in range(20)]
    result = build_diagnostic_report_data_tool._fn(
        score_rows=llm_rows,
        scope_label="全市",
        exam_name="期末质量检测",
        subject_name="数学",
        render=False,
        report_data=report_data,
        sub_task="调 build_diagnostic_report_data_tool(scope_label=全市, render=true)",
    )
    assert "参考人数" in result.data.get("KPI_GRID", "")
    assert ">40<" in result.data.get("KPI_GRID", "")
    assert "<strong>40</strong>" in result.data.get("GENERAL_INSIGHT", "")


def test_build_diagnostic_ignores_empty_llm_fetch_and_uses_upstream():
    """空 fetch_data / item_rows 不得盖掉上游非空小题与成绩。"""
    from src.agent.education.tools import build_diagnostic_report_data_tool

    fetch_payload = {
        "item_rows": [
            {"question_no": 1, "knowledge_name": "函数", "score_rate": 55.0},
        ],
        "knowledge_rows": [
            {"knowledge_name": "函数", "score_rate": 55.0, "question_count": 1},
        ],
        "score_rows": [{"score": 88.0, "exam_score": 150.0, "district": "广陵区"}] * 5,
        "score_result": {
            "columns": ["score", "exam_score"],
            "rows": [[88.0, 150.0]] * 5,
        },
    }
    report_data = {
        "sub_tasks": [
            {
                "sub_task_agent": "DataAnalyst",
                "exec_result": {
                    "columns": ["score", "exam_score", "district"],
                    "rows": [[88.0, 150.0, "广陵区"]] * 5,
                    "row_count": 5,
                },
            },
            {
                "sub_task_agent": "ToolExpert",
                "tool_calls": [
                    {
                        "tool": "fetch_subject_diagnosis_data_tool",
                        "success": True,
                        "data": fetch_payload,
                    }
                ],
            },
        ]
    }
    result = build_diagnostic_report_data_tool._fn(
        fetch_data={"item_rows": [], "knowledge_rows": [], "score_rows": []},
        item_rows=[],
        knowledge_rows=[],
        score_rows=[],
        scope_label="全市",
        exam_name="期末",
        subject_name="数学",
        render=False,
        report_data=report_data,
        sub_task="调 build_diagnostic_report_data_tool(scope_label=全市, render=true)",
    )
    assert "ITEM_TABLE" in result.data
    assert "函数" in result.data["ITEM_TABLE"]
    assert "KNOWLEDGE_TABLE" in result.data
    assert "KPI_GRID" in result.data
    assert ">5<" in result.data.get("KPI_GRID", "")


def test_build_diagnostic_uses_last_fetch_data_ctx():
    from src.agent.education.tools import build_diagnostic_report_data_tool

    fetch_payload = {
        "item_rows": [{"question_no": 3, "knowledge_name": "导数", "score_rate": 40.0}],
        "knowledge_rows": [{"knowledge_name": "导数", "score_rate": 40.0, "question_count": 1}],
        "score_rows": [{"score": 90.0, "exam_score": 150.0}] * 3,
        "score_result": {
            "columns": ["score", "exam_score"],
            "rows": [[90.0, 150.0]] * 3,
        },
    }
    result = build_diagnostic_report_data_tool._fn(
        scope_label="全市",
        subject_name="数学",
        render=False,
        tool_runtime_ctx={"last_fetch_data": fetch_payload},
        sub_task="调 build_diagnostic_report_data_tool(scope_label=全市, render=true)",
    )
    assert "导数" in result.data.get("ITEM_TABLE", "")
    assert "导数" in result.data.get("KNOWLEDGE_TABLE", "")
