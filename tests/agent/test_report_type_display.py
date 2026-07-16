"""九大类标准报告类型展示。"""

from __future__ import annotations

from pathlib import Path

from src.agent.education.report_types import REPORT_TYPE_LABELS, ReportType, report_type_label
from src.agent.education.templates import (
    ensure_report_type_in_data,
    resolve_report_type_from_template,
    select_report_template,
)


def test_nine_standard_report_types_defined():
    assert len(ReportType) == 9
    assert len(REPORT_TYPE_LABELS) == 9
    assert set(REPORT_TYPE_LABELS) == set(ReportType)


def test_each_report_type_has_template_and_report_type_key():
    for rt in ReportType:
        info = select_report_template(rt)
        assert info["template_name"], f"{rt} 缺模板"
        keys = info["data_keys"]
        assert "REPORT_TYPE" in keys, f"{rt} REQUIRED_KEYS 缺 REPORT_TYPE"
        assert report_type_label(rt)


def test_primary_templates_show_report_type_placeholder():
    """九大类主模板页头应展示 {{REPORT_TYPE}}。"""
    for rt in ReportType:
        name = select_report_template(rt)["template_name"]
        assert isinstance(name, str) and name
        path = (
            Path(__file__).resolve().parents[2]
            / "src/agent/resource/templates"
            / name
        )
        text = path.read_text(encoding="utf-8")
        assert "{{REPORT_TYPE}}" in text, f"{name} 未展示 REPORT_TYPE"


def test_resolve_and_ensure_report_type():
    assert resolve_report_type_from_template("education/class_overview.html") == ReportType.CLASS_OVERVIEW
    assert resolve_report_type_from_template("education/trend_tracking.html") == ReportType.TREND_TRACKING
    assert resolve_report_type_from_template("education/student_exam_analysis.html") == ReportType.STUDENT_PROFILE
    assert resolve_report_type_from_template("education/comprehensive.html") == ReportType.COMPREHENSIVE

    filled = ensure_report_type_in_data("education/tier_alert.html", {})
    assert filled["REPORT_TYPE"] == "分层预警报告"
    keep = ensure_report_type_in_data("education/tier_alert.html", {"REPORT_TYPE": "自定义"})
    assert keep["REPORT_TYPE"] == "自定义"


def test_render_html_injects_report_type_for_grade_comparison():
    import asyncio

    from src.agent.resource.tool.business import render_html_report

    result = asyncio.run(
        render_html_report.execute(
            template_name="education/grade_comparison.html",
            title="各班对比",
            data={
                "REPORT_TITLE": "各班对比",
                "REPORT_SUBTITLE": "",
                "REPORT_TIME": "2026-07-14",
                "GRADE_NAME": "高三",
                "EXAM_NAME": "一模",
                "SUBJECT_NAME": "数学",
                "CLASS_COMPARE_CHART": "",
                "CLASS_RANKING_TABLE": "",
                "DISPERSION_INFO": "",
                "SUMMARY": "",
                "RECOMMENDATIONS": "",
            },
        )
    )
    assert result.data
    assert result.data.get("report_type_label") == "班级横向对比报告"
    assert result.data.get("report_type") == "grade_comparison"
    assert (result.data.get("title") or "").endswith("【班级横向对比报告】")
    assert not (result.data.get("title") or "").startswith("【")
    assert "班级横向对比报告" in (result.data.get("html") or "")


def test_render_html_injects_report_type_for_trend_tracking():
    import asyncio

    from src.agent.education.tools import build_trend_tracking_report_data_tool

    result = asyncio.run(
        build_trend_tracking_report_data_tool.execute(
            class_name="高三(10)班",
            subject_name="数学",
            records=[
                {"exam": "月考1", "student": "A", "subjects": {"数学": 78}, "total": 78},
                {"exam": "期中", "student": "A", "subjects": {"数学": 72}, "total": 72},
            ],
            render=True,
        )
    )
    assert result.data
    assert result.data.get("report_type_label") == "成绩趋势报告"
    assert result.data.get("report_type") == "trend_tracking"
    assert "成绩趋势报告" in (result.data.get("html") or "") or "成绩趋势报告" in (
        result.data.get("title") or ""
    )
