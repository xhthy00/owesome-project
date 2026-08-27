"""难度曲线：分箱、缺考、题号抽取、整卷/单题分流。"""

from __future__ import annotations

import json

from src.agent.education.charts import build_chart_option
from src.agent.education.difficulty_curve import (
    BAND_WIDTH,
    build_paper_curve,
    curve_insight,
    extract_question_target,
    match_question_nos,
    shape_item_curves,
    subject_column,
)
from src.agent.education.intent_router import classify_report_intent_sync
from src.agent.education.query_parse import (
    is_difficulty_curve_report_query,
    is_item_difficulty_curve_query,
    is_score_band_report_query,
)
from src.agent.education.report_types import ReportType
from src.agent.expand.planner import build_fact_query_plan_items, coerce_plan_items_if_needed


def test_extract_question_target_variants():
    assert extract_question_target("第15题第1问") == "15_1"
    assert extract_question_target("15题第1小题") == "15_1"
    assert extract_question_target("单选1难度曲线") == "单选1"
    assert extract_question_target("单选题第1题") == "单选1"
    assert extract_question_target("3月数学第15题难度曲线") == "15"
    assert extract_question_target("小题15") == "15"
    assert extract_question_target("15_1") == "15_1"
    assert extract_question_target("3月数学难度曲线") is None
    assert extract_question_target("2026届高三5月模拟语文题目1的难度曲线") == "1"


def test_match_question_nos_parent_vs_subs():
    assert match_question_nos("15", ["15", "15_1"]) == ["15"]
    assert match_question_nos("15", ["15_1", "15_2"]) == ["15_1", "15_2"]
    assert match_question_nos("单选1", ["单选1", "单选2"]) == ["单选1"]
    assert match_question_nos("99", ["单选1", "15"]) == []
    assert match_question_nos("1", ["单选1", "单选2"]) == ["单选1"]
    assert match_question_nos("1", ["1", "单选1"]) == ["单选1"]


def test_paper_curve_excludes_zero_and_rises():
    students = [
        {"anon_stu_id": "a", "sx": 20, "xsxz": "在籍生"},
        {"anon_stu_id": "b", "sx": 90, "xsxz": "在籍生"},
        {"anon_stu_id": "c", "sx": 0, "xsxz": "在籍生"},
        {"anon_stu_id": "d", "sx": 90, "xsxz": "市报生"},
    ]
    curve = build_paper_curve(students, subject_col="sx", full_score=150, width=BAND_WIDTH)
    assert all(p["n"] > 0 for p in curve)
    assert 0 not in [p["band_lo"] for p in curve]
    lows = [p for p in curve if p["band_lo"] == 11]
    highs = [p for p in curve if p["band_lo"] == 81]
    assert lows and highs
    assert lows[0]["n"] == 1
    assert highs[0]["n"] == 1
    assert lows[0]["score_rate"] < highs[0]["score_rate"]
    assert abs(lows[0]["score_rate"] - round(20 / 150 * 100, 2)) < 0.01


def test_paper_curve_is_subject_over_full_not_item_avg():
    students = [{"anon_stu_id": "a", "sx": 75, "xsxz": "在籍生"}]
    curve = build_paper_curve(students, subject_col="sx", full_score=150)
    assert curve[0]["score_rate"] == round(75 / 150 * 100, 2)


def test_shape_item_curves_and_insight():
    rows = [
        {"band_lo": 11, "question_no": "15", "score_rate": 20.0, "n": 10},
        {"band_lo": 81, "question_no": "15", "score_rate": 80.0, "n": 10},
    ]
    shaped = shape_item_curves(rows)
    assert list(shaped) == ["15"]
    assert shaped["15"][0]["score_rate"] == 20.0
    assert shaped["15"][0]["band_label"] == "20"
    assert shaped["15"][1]["band_label"] == "90"
    paper = [
        {"band_lo": 11, "band_label": "11 - 20", "n": 10, "score_rate": 30.0},
        {"band_lo": 81, "band_label": "81 - 90", "n": 10, "score_rate": 70.0},
    ]
    text = curve_insight(paper)
    assert "上升" in text


def test_difficulty_curve_chart_y_is_rate():
    raw = build_chart_option(
        "difficulty_curve",
        {
            "x_labels": ["11 - 20", "81 - 90"],
            "series": [{"name": "全卷", "values": [30.0, 70.0]}],
        },
        "数学难度曲线",
    )
    opt = json.loads(raw)
    assert opt["yAxis"]["max"] == 100
    assert "得分率" in opt["yAxis"]["name"]
    assert opt["xAxis"]["data"] == ["11 - 20", "81 - 90"]
    assert opt["series"][0]["data"] == [30.0, 70.0]
    assert opt["series"][0]["smooth"] is False


def test_subject_column_uses_converted_for_assign():
    assert subject_column("数学") == "sx"
    assert subject_column("化学") == "hxzh"
    assert subject_column("语文") == "yw"


def test_paper_report_route_not_score_band_or_diagnosis():
    q = "2026届高三3月数学难度曲线"
    assert is_difficulty_curve_report_query(q) is True
    assert is_item_difficulty_curve_query(q) is False
    assert is_score_band_report_query(q) is False
    route = classify_report_intent_sync(q)
    assert route.needs_report is True
    assert route.report_type == ReportType.DIFFICULTY_CURVE


def test_difficulty_analysis_and_paper_rate_route_to_curve_report():
    for q in (
        "2026届高三5月数学难度分析",
        "帮我生成2026届高三5月数学试卷得分率分析",
    ):
        assert is_difficulty_curve_report_query(q) is True
        assert is_item_difficulty_curve_query(q) is False
        route = classify_report_intent_sync(q)
        assert route.needs_report is True
        assert route.report_type == ReportType.DIFFICULTY_CURVE


def test_knowledge_score_rate_is_not_difficulty_curve():
    q = "2026届高三5月数学知识点得分率分析"
    assert is_difficulty_curve_report_query(q) is False


def test_item_query_is_fact_and_calls_tool_with_question_no():
    q = "2026届高三3月数学第15题难度曲线"
    assert extract_question_target(q) == "15"
    assert is_item_difficulty_curve_query(q) is True
    assert is_difficulty_curve_report_query(q) is False
    route = classify_report_intent_sync(q)
    assert route.needs_report is False
    blob = build_fact_query_plan_items(q)[0]["sub_task"]
    assert "build_difficulty_curve_report_data_tool" in blob
    assert "question_no" in blob
    wrong = [
        {
            "sub_task": "调 build_subject_diagnosis_sections_tool(render=true)",
            "sub_task_agent": "ToolExpert",
        },
    ]
    fixed = coerce_plan_items_if_needed(q, wrong)
    fixed_blob = " ".join(p["sub_task"] for p in fixed)
    assert "build_subject_diagnosis_sections_tool" not in fixed_blob
    assert "build_difficulty_curve_report_data_tool" in fixed_blob


def test_paper_without_items_still_has_curve():
    students = [{"anon_stu_id": "a", "sx": 80, "xsxz": "在籍生"}]
    from src.agent.education.difficulty_curve import (
        build_curve_report_data,
        render_curve_tables,
    )

    curve = build_paper_curve(students, subject_col="sx", full_score=150)
    assert curve
    _, secondary = render_curve_tables(
        paper_curve=curve, item_curves={}, item_only=False
    )
    assert "未导入小题" in secondary
    data = build_curve_report_data(
        exam_name="3月",
        subject_name="数学",
        students=students,
        item_rows=[],
        catalog=[],
    )
    assert data.get("error") is None
    assert "CURVE_CHART" in data
    assert not data.get("ITEM_CHARTS_HTML")


def test_paper_report_includes_each_item_curve():
    from src.agent.education.difficulty_curve import build_curve_report_data

    students = [
        {"anon_stu_id": "a", "sx": 20, "xsxz": "在籍生"},
        {"anon_stu_id": "b", "sx": 90, "xsxz": "在籍生"},
    ]
    item_rows = [
        {"band_lo": 11, "question_no": "单选1", "score_rate": 20.0, "n": 10},
        {"band_lo": 81, "question_no": "单选1", "score_rate": 80.0, "n": 10},
        {"band_lo": 11, "question_no": "15", "score_rate": 10.0, "n": 10},
        {"band_lo": 81, "question_no": "15", "score_rate": 70.0, "n": 10},
    ]
    data = build_curve_report_data(
        exam_name="3月",
        subject_name="语文",
        students=students,
        item_rows=item_rows,
        catalog=["单选1", "15"],
    )
    html = data.get("ITEM_CHARTS_HTML") or ""
    assert "单选1" in html
    assert "15" in html
    assert "语文单选1难度曲线" in html
    assert html.count("item-curve-host") == 2
    assert "单选1" in (data.get("SECONDARY_TABLE") or "")
    assert "15" in (data.get("SECONDARY_TABLE") or "")
    mark = "itemCurve0Data"
    start = html.find(">", html.find(mark)) + 1
    end = html.find("</script>", start)
    opt = json.loads(html[start:end])
    assert opt["yAxis"]["max"] == 100
    assert opt["series"][0]["name"] in {"单选1", "15"}


def test_missing_question_no_returns_error_not_paper():
    from src.agent.education.difficulty_curve import build_curve_report_data

    data = build_curve_report_data(
        exam_name="3月",
        subject_name="数学",
        students=[{"anon_stu_id": "a", "sx": 80, "xsxz": "在籍生"}],
        item_rows=[],
        catalog=["单选1", "15"],
        question_clue="99",
    )
    assert data.get("error") == "item_not_found"
    assert "未找到" in str(data.get("message") or "")
    assert "CURVE_CHART" not in data
    assert "paper_curve" not in data


def test_item_choice_query_extracts_danxuan():
    q = "2026届高三3月数学单选1难度曲线"
    assert extract_question_target(q) == "单选1"
    assert classify_report_intent_sync(q).needs_report is False


def test_named_school_item_curve_still_fact():
    q = "扬州中学3月数学第15题难度曲线"
    assert is_item_difficulty_curve_query(q) is True
    assert classify_report_intent_sync(q).needs_report is False


def test_timu1_query_is_item_fact_not_paper():
    q = "2026届高三5月模拟语文题目1的难度曲线"
    assert extract_question_target(q) == "1"
    assert is_item_difficulty_curve_query(q) is True
    assert is_difficulty_curve_report_query(q) is False
    assert classify_report_intent_sync(q).needs_report is False


def test_item_band_sql_uses_paper_full_not_earned_score():
    from src.agent.education.difficulty_curve import item_band_sql

    sql = item_band_sql("2026届高三5月模拟", "语文", "yw", ["1"])
    assert "LEFT JOIN tb_score_detail sd" in sql
    assert "COALESCE(sd.score, 0)" in sql
    assert "JOIN tb_exam_question eq ON eq.exam_id = sc.exam_id" in sql
    assert "AND eq.question_no IN" in sql
    assert "JOIN tb_score_detail sd ON sd.student_id = ov.anon_stu_id" not in sql
    assert "ov.xsxz = '在籍生'" not in sql
