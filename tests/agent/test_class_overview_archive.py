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


def test_enrich_fills_summary_and_recommendations_from_kpis():
    """占位的总体分析 / 改进建议应被 KPI 实数替换。"""
    from src.agent.resource.tool.business import _enrich_class_overview_archive

    out = _enrich_class_overview_archive(
        "education/class_overview.html",
        {
            "CLASS_NAME": "高三(10)班",
            "SUBJECT_NAME": "数学",
            "EXAM_NAME": "连淮扬镇",
            "SUMMARY": "<p>班级成绩总览：关注均分、及格率与分数段分布。</p>",
            "RECOMMENDATIONS": "<p>结合 KPI 与分数段，对薄弱区间安排巩固练习。</p>",
            "RANK_INFO": {
                "scope": "扬州中学高三年级 (共 3 个班)",
                "items": [
                    {"指标": "均分", "value": 108.5, "rank": 1, "total": 3},
                ],
                "summary": "高三(10)班均分位列年级第 1",
            },
        },
        tool_runtime_ctx={
            "last_exec_result": {
                "columns": ["student_id", "subject", "score", "exam_score"],
                "rows": [
                    ["a", "数学", 145, 150],
                    ["b", "数学", 72, 150],
                    ["c", "数学", 108, 150],
                    ["d", "数学", 55, 150],
                    ["e", "数学", 120, 150],
                ],
            }
        },
    )
    summary = out.get("SUMMARY") or ""
    rec = out.get("RECOMMENDATIONS") or ""
    assert "关注均分、及格率" not in summary
    assert "结合 KPI 与分数段" not in rec
    assert "高三(10)班" in summary
    assert "均分" in summary
    assert "及格率" in summary
    assert "年级第 1" in summary or "位列年级" in summary
    assert "<ol>" in rec
    assert "<li>" in rec
    assert out.get("TOTAL_COUNT") not in (None, "", "-")


def test_build_class_overview_summary_contains_metrics():
    from src.agent.education.subject_diagnosis import (
        build_class_overview_recommendations,
        build_class_overview_summary,
    )

    stats = {
        "count": 52,
        "avg": 108.5,
        "pass_rate": 75.0,
        "excellent_rate": 25.0,
        "good_rate": 40.0,
        "low_score_rate": 12.0,
        "max": 145,
        "min": 55,
        "stdev": 21.5,
        "full_score": 150,
        "segments": [
            {"label": "0-90", "count": 8, "ratio": 15.4},
            {"label": "90-120", "count": 28, "ratio": 53.8},
            {"label": "120-150", "count": 16, "ratio": 30.8},
        ],
    }
    summary = build_class_overview_summary(
        class_name="高三(10)班",
        subject_name="数学",
        stats=stats,
        stdev_level="适中",
        rank_summary="均分年级第 1",
    )
    assert "52" in summary
    assert "108.5" in summary
    assert "75.00%" in summary or "75%" in summary
    assert "90-120" in summary
    assert "均分年级第 1" in summary

    rec = build_class_overview_recommendations(stats=stats)
    assert "<ol>" in rec
    assert "及格" in rec or "优秀" in rec or "分数段" in rec or "巩固" in rec


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


def test_enrich_multi_subject_radar_uses_score_rate_scale():
    """多科雷达应按得分率 0–100 缩放，不能用各科满分之和当地轴。"""
    import json

    from src.agent.resource.tool.business import _enrich_class_overview_archive

    rows = []
    for sid in ("S1", "S2", "S3"):
        rows.extend([
            [sid, "语文", 112, 150],
            [sid, "数学", 86, 150],
            [sid, "历史", 79, 100],
        ])
    out = _enrich_class_overview_archive(
        "education/class_overview.html",
        {
            "CLASS_NAME": "高二(6)班",
            "SUBJECT_RADAR_CHART": "",
        },
        tool_runtime_ctx={
            "last_exec_result": {
                "columns": ["student_id", "subject", "score", "exam_score"],
                "rows": rows,
            }
        },
    )
    chart = out.get("SUBJECT_RADAR_CHART") or ""
    assert "得分率" in chart
    assert "各科能力画像" in chart
    option = json.loads(chart)
    indicators = option["radar"]["indicator"]
    assert all(float(i.get("max") or 0) == 100 for i in indicators)
    values = option["series"][0]["data"][0]["value"]
    # 语文 112/150≈74.7、历史 79/100=79，应明显大于中心（不会全 <10）
    assert min(values) > 40
    assert max(values) < 100


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




def test_class_overview_template_no_jinja_control_tags():
    """班级总览模板不得含 {% if %}，否则 Jinja 失败回退 regex 会把源码漏到页面。"""
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[2]
        / "src/agent/resource/templates/education/class_overview.html"
    )
    text = path.read_text(encoding="utf-8")
    assert "{%" not in text
    assert "{{SCORE_DIST_SECTION_TITLE}}" in text
    assert "{{SUBJECT_NAME_BADGE}}" in text
    assert "{{SUBJECT_KPI_SECTIONS}}" in text


def test_enrich_multi_subject_splits_score_distribution():
    """多科成绩不得混成同一分数段；应分科展示，顶层人数为实际学生数。"""
    from src.agent.resource.tool.business import _enrich_class_overview_archive

    # 2 名学生 × 3 科 = 6 行；混算会显示参考人数 6
    rows = []
    for sid, scores in [
        ("S1", {"语文": 90, "数学": 120, "英语": 100}),
        ("S2", {"语文": 70, "数学": 80, "英语": 85}),
    ]:
        for sub, sc in scores.items():
            full = 150 if sub == "数学" else 100
            rows.append([sid, sub, sc, full])

    out = _enrich_class_overview_archive(
        "education/class_overview.html",
        {
            "REPORT_TITLE": "高二(6)班班级总览报告",
            "CLASS_NAME": "高二(6)班",
            "EXAM_NAME": "期末",
            "SUBJECT_NAME": "全科",
        },
        tool_runtime_ctx={
            "last_exec_result": {
                "columns": ["student_id", "subject", "score", "exam_score"],
                "rows": rows,
            }
        },
    )
    assert out.get("IS_MULTI_SUBJECT") == "1"
    assert out.get("TOTAL_COUNT") == "2"
    sections = out.get("SUBJECT_KPI_SECTIONS") or ""
    assert "数学" in sections and "语文" in sections and "英语" in sections
    assert "data-edu-echart" in sections
    assert "subject-kpi-block" in sections
    breakdown = out.get("SUBJECT_BREAKDOWN") or ""
    assert "<table" in breakdown.lower()
    assert "数学" in breakdown
    # 顶层图为总分口径
    assert "总分" in (out.get("SCORE_DIST_CHART") or "") or "合计" in (
        out.get("SEGMENT_TABLE") or ""
    )
    assert out.get("SCORE_DIST_SECTION_TITLE") == "总分与分科分数段"
    assert "班级总分分数段" in (out.get("SCORE_DIST_SUBHEAD") or "")
    assert "{%" not in (out.get("SCORE_DIST_SECTION_TITLE") or "")


def test_build_student_archive_from_score_rows_still_works_for_other_reports():
    from src.agent.education.comprehensive import build_student_archive_from_score_rows

    rows = [
        {"student_id": "STU001", "subject": "数学", "score": 120, "exam_name": "期中"},
        {"student_id": "STU002", "subject": "数学", "score": 88, "exam_name": "期中"},
    ]
    html = build_student_archive_from_score_rows(rows, exam_name="期中", full_score=150)
    assert "archive-card" in html
