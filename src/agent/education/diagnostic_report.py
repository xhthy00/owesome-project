"""结构化诊断报告数据组装——一般性 / 特殊性 / 动态性三节。"""

from __future__ import annotations

from typing import Any

from src.agent.education.aggregation import aggregate_by
from src.agent.education.charts import build_chart_option
from src.agent.education.config import EducationConfig
from src.agent.education.cross_analysis import compare_groups, cross_analyze
from src.agent.education.report_types import ReportType, report_type_label
from src.agent.education.stats import (
    compute_score_stats,
    compute_top_progress_regress,
    compute_trend_distribution,
    describe_score_dispersion,
    identify_at_risk_students,
    normalize_segments,
)


def _fmt(v: Any) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.2f}"
    return str(v)


def _kpi_card(label: str, value: str, hint: str = "") -> str:
    hint_html = f'<div class="hint" style="margin-top:6px;font-size:11.5px;line-height:1.45;color:rgba(0,0,0,0.45)">{hint}</div>' if hint else ""
    return (
        f'<div class="edu-kpi"><div class="label">{label}</div>'
        f'<div class="value">{value}</div>{hint_html}</div>'
    )


def _table(headers: list[str], rows: list[list[str]], *, numeric_from: int = 1) -> str:
    head = "<tr>" + "".join(
        f"<th class='{'num' if i >= numeric_from else ''}'>{h}</th>"
        for i, h in enumerate(headers)
    ) + "</tr>"
    body = "".join(
        "<tr>"
        + "".join(
            f"<td class='{'num' if i >= numeric_from else ('edu-cell-text' if i == 0 else '')}'>{c}</td>"
            for i, c in enumerate(r)
        )
        + "</tr>"
        for r in rows
    )
    inner = f"<table class='edu-table'><thead>{head}</thead><tbody>{body}</tbody></table>"
    return f'<div class="edu-table-wrap">{inner}</div>'


def build_diagnostic_data(
    score_rows: list[dict[str, Any]],
    *,
    trend_records: list[dict[str, Any]] | None = None,
    config: EducationConfig | None = None,
    scope_label: str = "",
    exam_name: str = "",
    subject_name: str = "",
) -> dict[str, Any]:
    """从成绩行组装 diagnostic_report 模板 data。"""
    if config is None:
        from src.agent.education.config_store import get_config
        config = get_config()

    scores = [float(r["score"]) for r in score_rows if r.get("score") is not None]
    full_score = None
    for r in score_rows:
        if r.get("exam_score") is not None:
            full_score = float(r["exam_score"])
            break
    overall = compute_score_stats(scores, config, full_score)

    # S1 一般性
    grade_agg = aggregate_by("grade", score_rows, config)
    district_agg = aggregate_by("district", score_rows, config) if any(
        r.get("district") for r in score_rows
    ) else []
    general_insight = (
        f'<p class="edu-insight-line">参考 <strong>{overall.get("count", 0)}</strong> 人，均分 <strong>{_fmt(overall.get("avg"))}</strong>，'
        f'及格率 <strong>{_fmt(overall.get("pass_rate"))}%</strong>，良好率 <strong>{_fmt(overall.get("good_rate"))}%</strong>。</p>'
    )
    if grade_agg:
        top = max(grade_agg, key=lambda x: float(x.get("avg") or 0))
        low = min(grade_agg, key=lambda x: float(x.get("avg") or 0))
        general_insight += (
            f"<p>年级维度：{top.get('dimension_value')} 均分最高（{_fmt(top.get('avg'))}），"
            f"{low.get('dimension_value')} 相对薄弱（{_fmt(low.get('avg'))}）。</p>"
        )
    if district_agg:
        top_d = max(district_agg, key=lambda x: float(x.get("avg") or 0))
        low_d = min(district_agg, key=lambda x: float(x.get("avg") or 0))
        general_insight += (
            f"<p>区县维度：{top_d.get('dimension_value')} 均分领先（{_fmt(top_d.get('avg'))}），"
            f"{low_d.get('dimension_value')} 需重点关注（{_fmt(low_d.get('avg'))}）。</p>"
        )
    elif scope_label in ("全市", "全年级", "") and score_rows:
        general_insight += (
            "<p class='edu-sub'>区县对比数据暂不可用（tb_school.district 未回填或未执行 DDL）。"
            "全市 KPI 仍基于全量成绩统计。</p>"
        )

    district_compare_chart = ""
    if district_agg:
        ranked = sorted(district_agg, key=lambda x: float(x.get("avg") or 0), reverse=True)
        district_compare_chart = build_chart_option(
            "class_compare_bar",
            {
                "classes": [str(g.get("dimension_value") or "") for g in ranked],
                "values": [float(g.get("avg") or 0) for g in ranked],
            },
            title="各区县均分对比",
        )

    general_trend_chart = ""
    if trend_records and len(trend_records) >= 2:
        exams = []
        avgs = []
        for rec in trend_records:
            exams.append(str(rec.get("exam") or ""))
            avgs.append(float(rec.get("avg") or 0))
        general_trend_chart = build_chart_option(
            "trend_line",
            {"x_labels": exams, "series": [{"name": "均分", "values": avgs}]},
            title="整体均分趋势",
        )

    # S2 特殊性
    class_cross = cross_analyze("class", "score_segment", score_rows)
    heatmap_chart = build_chart_option("heatmap", class_cross, title="班级×分数段热力图") if class_cross.get("matrix") else ""

    class_agg = aggregate_by("class", score_rows, config)
    base = class_agg[0] if class_agg else overall
    comparisons = compare_groups(base, class_agg[1:], ["avg", "pass_rate"]) if len(class_agg) > 1 else []
    notable = [c for c in comparisons if c.get("notable")]
    special_insight = "<p>班级间差异整体可控。</p>"
    if notable:
        names = "、".join(c.get("name", "") for c in notable[:5])
        special_insight = f"<p>以下班级与基准差异显著，需重点关注：{names}。</p>"

    at_risk = identify_at_risk_students(
        [{"name": r.get("student_id"), "subject": r.get("subject"), "score": r.get("score")}
         for r in score_rows if r.get("student_id")],
        config,
    )
    segment_rows = [
        [str(s.get("label", "")), str(s.get("count", 0)), f"{_fmt(s.get('ratio'))}%"]
        for s in normalize_segments(overall.get("segments") or [], full_score=overall.get("full_score"))
    ]
    segment_table = _table(["分数段", "人数", "占比"], segment_rows) if segment_rows else ""

    # S3 动态性
    dynamic_insight = "<p>暂无多次考试数据，动态性分析略。</p>"
    progress_table = ""
    trend_line_chart = ""
    if trend_records and len(trend_records) >= 2:
        deltas = [
            {"name": str(r.get("student") or ""), "delta": float(r.get("delta") or 0)}
            for r in trend_records if r.get("delta") is not None
        ]
        if deltas:
            pr = compute_top_progress_regress(deltas, top_n=5)
            dist = compute_trend_distribution(deltas)
            dynamic_insight = (
                f"<p>进步 {len(dist.get('progress') or [])} 人，"
                f"退步 {len(dist.get('regress') or [])} 人，"
                f"稳定 {len(dist.get('stable') or [])} 人。</p>"
            )
            prog_rows = [[str(p.get("name")), f"+{_fmt(p.get('delta'))}"] for p in pr.get("progress") or []]
            progress_table = _table(["学生", "变化"], prog_rows) if prog_rows else ""
            trend_line_chart = build_chart_option(
                "progress_regress_bar",
                {"items": pr.get("chart_items") or []},
                title="进退步分布",
            )

    disp = describe_score_dispersion(
        overall.get("stdev"),
        full_score=overall.get("full_score"),
        variance=overall.get("variance"),
    )
    kpi_html = "".join([
        _kpi_card("参考人数", str(overall.get("count") or 0)),
        _kpi_card("平均分", _fmt(overall.get("avg"))),
        _kpi_card("最高分", _fmt(overall.get("max"))),
        _kpi_card("最低分", _fmt(overall.get("min"))),
        _kpi_card("及格率", f"{_fmt(overall.get('pass_rate'))}%"),
        _kpi_card("良好率", f"{_fmt(overall.get('good_rate'))}%"),
        _kpi_card("低分率", f"{_fmt(overall.get('low_score_rate'))}%"),
        _kpi_card("标准差", _fmt(overall.get("stdev")), hint=str(disp.get("stdev_hint") or "")),
        _kpi_card("方差", _fmt(disp.get("variance")), hint=str(disp.get("variance_hint") or "")),
    ])
    tip = str(disp.get("tip") or "")
    kpi_grid = f'<div class="edu-grid">{kpi_html}</div>'
    if tip:
        kpi_grid += (
            f'<p class="dispersion-tip" style="margin-top:12px;padding:10px 12px;'
            f'font-size:12.5px;line-height:1.65;color:rgba(0,0,0,0.65);'
            f'background:#fafafa;border:1px dashed #f0f0f0;border-radius:8px">{tip}</p>'
        )

    title_parts = [p for p in (scope_label, subject_name, exam_name) if p]
    return {
        "REPORT_TITLE": f"{' · '.join(title_parts)}结构化诊断报告".strip(" ·"),
        "REPORT_TYPE": report_type_label(ReportType.DIAGNOSTIC_REPORT),
        "REPORT_SUBTITLE": "一般性 → 特殊性 → 动态性",
        "REPORT_TIME": "",
        "SCOPE": scope_label or "全年级",
        "EXAM_NAME": exam_name or "本次考试",
        "SUBJECT_NAME": subject_name or "全科",
        "KPI_GRID": kpi_grid,
        "GENERAL_TREND_CHART": general_trend_chart,
        "GENERAL_INSIGHT": general_insight,
        "DISTRICT_COMPARE_CHART": district_compare_chart,
        "CLASS_DIFF_HEATMAP": heatmap_chart,
        "SEGMENT_COMPARE_TABLE": segment_table,
        "SPECIAL_INSIGHT": special_insight,
        "TREND_LINE_CHART": trend_line_chart,
        "PROGRESS_REGRESS_TABLE": progress_table,
        "DYNAMIC_INSIGHT": dynamic_insight,
        "DISTRICT_SUMMARY": _table(
            ["区县", "人数", "均分", "及格率"],
            [[g.get("dimension_value", ""), str(g.get("count", 0)),
              _fmt(g.get("avg")), f"{_fmt(g.get('pass_rate'))}%"]
             for g in district_agg],
        ) if district_agg else "",
        "AT_RISK_SUMMARY": (
            f"临界生 {len(at_risk.get('critical') or [])} 人，"
            f"退步生 {len(at_risk.get('regression') or [])} 人，"
            f"偏科生 {len(at_risk.get('imbalanced') or [])} 人"
        ),
        "SUMMARY": general_insight + special_insight + dynamic_insight,
        "RECOMMENDATIONS": "<ul class='edu-list'>"
        "<li>结合一般性趋势制定年级共性复习计划</li>"
        "<li>针对特殊性差异开展分层辅导</li>"
        "<li>跟踪动态性进退步学生并及时干预</li></ul>",
    }


__all__ = ["build_diagnostic_data"]
