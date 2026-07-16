"""成绩趋势报告：确定性组装，避免 TREND_CHART 空白。"""

from __future__ import annotations

from src.agent.education.query_parse import (
    is_multi_exam_class_analysis_query,
    is_trend_tracking_query,
)
from src.agent.education.report_types import ReportType
from src.agent.education.orchestrator import ReportIntentResolver
from src.agent.education.trend_tracking import build_trend_tracking_data
from src.agent.expand.planner import (
    build_trend_tracking_plan_items,
    coerce_plan_items_if_needed,
)


def _sample_records():
    return [
        {"exam": "摸底", "student": "A", "subjects": {"数学": 80}, "total": 80},
        {"exam": "摸底", "student": "B", "subjects": {"数学": 70}, "total": 70},
        {"exam": "期中", "student": "A", "subjects": {"数学": 85}, "total": 85},
        {"exam": "期中", "student": "B", "subjects": {"数学": 75}, "total": 75},
        {"exam": "一模", "student": "A", "subjects": {"数学": 90}, "total": 90},
        {"exam": "一模", "student": "B", "subjects": {"数学": 78}, "total": 78},
        {"exam": "二模", "student": "A", "subjects": {"数学": 88}, "total": 88},
        {"exam": "二模", "student": "B", "subjects": {"数学": 82}, "total": 82},
    ]


def test_build_trend_tracking_data_fills_chart_and_table():
    data = build_trend_tracking_data(
        _sample_records(),
        ["摸底", "期中", "一模", "二模"],
        class_name="高三(10)班",
        school_name="扬州中学",
        subject_name="数学",
    )
    assert data["EXAM_COUNT"] == 4
    assert data["TREND_CHART"]
    assert '"type": "line"' in data["TREND_CHART"] or '"type":"line"' in data["TREND_CHART"]
    assert "摸底" in data["TREND_TABLE"]
    assert "参考人数" in data["TREND_TABLE"]
    assert "上升" in data["CHANGE_INFO"] or "下降" in data["CHANGE_INFO"] or "持平" in data["CHANGE_INFO"]
    assert "成绩趋势报告" in data["REPORT_TYPE"]


def test_trend_tracking_query_not_swallowed_by_comprehensive():
    q = "扬州中学高三(10)班数学成绩走势与进退步分析"
    assert is_trend_tracking_query(q) is True
    assert is_multi_exam_class_analysis_query(q) is False
    assert ReportIntentResolver().resolve(q).report_type == ReportType.TREND_TRACKING


def test_comprehensive_query_still_comprehensive():
    q = "扬州中学高三(10)班所有数学考试综合分析"
    assert is_trend_tracking_query(q) is False
    assert is_multi_exam_class_analysis_query(q) is True


def test_coerce_plan_uses_trend_tool():
    q = "高三(10)班数学历次成绩趋势报告"
    plans = coerce_plan_items_if_needed(q, [{"sub_task": q, "sub_task_agent": "DataAnalyst"}])
    blob = " ".join(p["sub_task"] for p in plans)
    assert "build_trend_tracking_report_data_tool" in blob
    assert "调 build_comprehensive_report_data_tool" not in blob


def test_build_trend_tracking_plan_items_shape():
    items = build_trend_tracking_plan_items("扬州中学高三(10)班数学成绩趋势")
    assert len(items) == 2
    assert "build_trend_tracking_report_data_tool" in items[1]["sub_task"]


def test_trend_tracking_tool_renders_html():
    import asyncio

    from src.agent.education.tools import build_trend_tracking_report_data_tool

    result = asyncio.run(
        build_trend_tracking_report_data_tool.execute(
            class_name="高三(10)班",
            school_name="扬州中学",
            subject_name="数学",
            records=_sample_records(),
            exam_order=["摸底", "期中", "一模", "二模"],
            render=True,
        )
    )
    assert result.is_final
    assert result.data.get("output_type") == "html"
    html = result.data.get("html") or ""
    assert "历次成绩趋势" in html
    assert "trendChartData" in html
    assert "xAxis" in html or "series" in html
    assert "参考人数" in html
    assert "摸底" in html
