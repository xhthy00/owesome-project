"""class_overview KPI / 分数段 / 结构化字段补齐。"""

from __future__ import annotations


def test_class_overview_template_no_student_archive_section():
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[2]
        / "src/agent/resource/templates/education/class_overview.html"
    )
    text = path.read_text(encoding="utf-8")
    assert "{{SEGMENT_TABLE}}" in text
    assert "scoreDistEmpty" in text
    assert "每位学生详细档案" not in text
    assert "{{STUDENT_ARCHIVE_TABLE}}" not in text
    assert "prose-card" in text or "prose-stack" in text
    assert "edu-table-wrap" in text
    assert "edu-card > h2::before" in text or ".edu-card > h2::before" in text


def test_polish_injects_styles_for_inline_class_overview():
    from src.agent.resource.tool.business import _polish_class_overview_html

    html = (
        "<html><head></head><body>"
        "<h1>班级总览</h1>"
        "<table><tr><th>a</th></tr><tr><td>1</td></tr></table>"
        "<p>" + ("分析内容" * 20) + "</p>"
        "<ol><li>建议一</li></ol>"
        "</body></html>"
    )
    out = _polish_class_overview_html(html, title="某某班班级总览报告")
    assert 'id="edu-class-overview-polish"' in out
    assert "edu-table-wrap" in out or "edu-class-overview-polish-js" in out
    assert "edu-prose-card" in out or "prose-card" in out



def test_enrich_fills_segments_with_inferred_full_score_150():
    """满分缺省时，分数>100 应推断 150，避免分数段落在 0-100 全 0。"""
    from src.agent.resource.tool.business import _enrich_class_overview_archive

    data = {
        "REPORT_TITLE": "高三(10)班班级成绩分析报告",
        "REPORT_SUBTITLE": "2026年连淮扬镇高三数学统一考试 · 数学 (满分150)",
        "EXAM_NAME": "连淮扬镇",
        "CLASS_NAME": "高三(10)班",
        "SCORE_DIST_CHART": "",
        "SEGMENT_TABLE": (
            "<table class='edu-table'><tbody>"
            "<tr><td>0-60</td><td class='num'>0</td><td class='num'>0.00%</td></tr>"
            "</tbody></table>"
        ),
        "SUBJECT_BREAKDOWN": (
            "[{'科目': '数学', '满分': 150, '参考人数': 52, '均分': 108.5, "
            "'最高': 145, '最低': 72, '及格率': '75.00%', '优秀率': '25.00%', '标准差': 21.73}]"
        ),
        "RANK_INFO": (
            "{'年级排名': '第 1 名 (共 3 个班级, 159 人)', "
            "'均分对比': '领先高三(9)班 4.25 分', '排名等级': '年级第一'}"
        ),
        "STUDENT_ARCHIVE_TABLE": "<div class='archive-card'>应被清空</div>",
    }
    ctx = {
        "last_exec_result": {
            "columns": ["student_id", "subject", "score"],
            "rows": [
                ["A01", "数学", 145],
                ["A02", "数学", 72],
                ["A03", "数学", 108],
                ["A04", "数学", 120],
            ],
        }
    }
    out = _enrich_class_overview_archive(
        "education/class_overview.html",
        data,
        tool_runtime_ctx=ctx,
    )
    assert out.get("REPORT_TYPE") == "班级总览报告"
    assert out.get("STUDENT_ARCHIVE_TABLE") == ""
    assert out.get("SCORE_DIST_CHART")
    assert "90-100" not in (out.get("SEGMENT_TABLE") or "") or "90-135" in (
        out.get("SEGMENT_TABLE") or ""
    ) or "135-150" in (out.get("SEGMENT_TABLE") or "")
    # 满分 150 的分数段应有人数 > 0
    seg = out.get("SEGMENT_TABLE") or ""
    assert "<table" in seg.lower()
    assert not all(
        c == "0"
        for c in __import__("re").findall(
            r"class=['\"]num['\"][^>]*>\s*(\d+)\s*<", seg
        )
    )
    assert "<table" in (out.get("SUBJECT_BREAKDOWN") or "").lower()
    assert "108.5" in (out.get("SUBJECT_BREAKDOWN") or "")
    assert "'科目'" not in (out.get("SUBJECT_BREAKDOWN") or "")
    assert "<table" in (out.get("RANK_INFO") or "").lower()
    assert "年级排名" in (out.get("RANK_INFO") or "")
    assert "{'" not in (out.get("RANK_INFO") or "")


def test_format_report_display_title_name_then_type():
    from src.agent.education.report_types import (
        ReportType,
        format_report_display_title,
        strip_report_type_markers,
    )

    assert strip_report_type_markers("【class_overview】扬州中学报告") == "扬州中学报告"
    assert (
        format_report_display_title(
            "【class_overview】扬州中学高三(10)班连淮扬镇数学考试班级成绩分析报告",
            ReportType.CLASS_OVERVIEW,
        )
        == "扬州中学高三(10)班连淮扬镇数学考试班级成绩分析报告【班级总览报告】"
    )


def test_describe_score_dispersion_levels():
    from src.agent.education.stats import describe_score_dispersion

    # 满分 150，标准差 12 → 8% → 较集中
    tight = describe_score_dispersion(12, full_score=150)
    assert tight["level"] == "较集中"
    assert "较集中" in tight["stdev_hint"]
    assert tight["variance"] == 144.0

    # 21.52 / 150 ≈ 14.3% → 适中
    mid = describe_score_dispersion(21.52, full_score=150)
    assert mid["level"] == "适中"

    # 30 / 150 = 20% → 分化明显
    wide = describe_score_dispersion(30, full_score=150)
    assert wide["level"] == "分化明显"
    assert "方差=标准差" in wide["variance_hint"]


def test_enrich_fills_ability_portrait_radar():
    from src.agent.resource.tool.business import _enrich_class_overview_archive

    out = _enrich_class_overview_archive(
        "education/class_overview.html",
        {
            "CLASS_NAME": "高三(10)班",
            "SUBJECT_NAME": "数学",
            "REPORT_SUBTITLE": "满分150",
            "SUBJECT_RADAR_CHART": "",
        },
        tool_runtime_ctx={
            "last_exec_result": {
                "columns": ["student_id", "subject", "score", "exam_score"],
                "rows": [
                    ["a", "数学", 145, 150],
                    ["b", "数学", 72, 150],
                    ["c", "数学", 108, 150],
                    ["d", "数学", 120, 150],
                ],
            }
        },
    )
    chart = out.get("SUBJECT_RADAR_CHART") or ""
    assert chart and chart not in ("{}", "null")
    assert "平均分" in chart or "radar" in chart
    assert "能力画像" in chart or "数学" in chart


def test_format_rank_info_nested_scope_items_summary():
    from src.agent.resource.tool.business import _coerce_class_overview_structured_fields

    out = {
        "RANK_INFO": {
            "scope": "扬州中学高三年级 (共 3 个班)",
            "items": [
                {"指标": "均分", "value": 108.5, "rank": 1, "total": 3, "cohort_avg": 104.25},
                {"指标": "及格率", "value": "75.00%", "rank": 1, "total": 3, "cohort_avg": "65.45%"},
                {"指标": "优秀率", "value": "25.00%", "rank": 1, "total": 3, "cohort_avg": "21.15%"},
            ],
            "summary": "高三(10)班在均分、及格率、优秀率三项 KPI 上均位列年级第 1",
        }
    }
    _coerce_class_overview_structured_fields(out)
    html = out["RANK_INFO"]
    assert "<table" in html.lower()
    assert "均分" in html
    assert "第 1 / 共 3 班" in html
    assert "年级对照" in html or "104.25" in html
    assert "[{'" not in html
    assert "对比范围" in html
    assert "位列年级第 1" in html


def test_format_rank_info_from_python_literal_string():
    from src.agent.resource.tool.business import _coerce_class_overview_structured_fields

    out = {
        "RANK_INFO": (
            "{'scope': '扬州中学高三年级 (共 3 个班)', "
            "'items': [{'指标': '均分', 'value': 108.5, 'rank': 1, 'total': 3, 'cohort_avg': 104.25}], "
            "'summary': '综合排名第 1'}"
        )
    }
    _coerce_class_overview_structured_fields(out)
    html = out["RANK_INFO"]
    assert "均分" in html
    assert "'rank'" not in html
    assert "<table" in html.lower()




def test_build_student_archive_from_score_rows_still_works_for_other_reports():
    from src.agent.education.comprehensive import build_student_archive_from_score_rows

    rows = [
        {"student_id": "STU001", "subject": "数学", "score": 120, "exam_name": "期中"},
        {"student_id": "STU002", "subject": "数学", "score": 88, "exam_name": "期中"},
    ]
    html = build_student_archive_from_score_rows(rows, exam_name="期中", full_score=150)
    assert "archive-card" in html
