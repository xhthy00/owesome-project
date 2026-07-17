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


def _exam_avg_trend_from_rows(score_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按考试聚合均分：``[{exam, avg}, ...]``。"""
    buckets: dict[str, list[float]] = {}
    order: list[str] = []
    for r in score_rows:
        if not isinstance(r, dict) or r.get("score") is None:
            continue
        exam = str(r.get("exam_name") or r.get("exam") or "").strip()
        if not exam:
            continue
        try:
            score = float(r["score"])
        except (TypeError, ValueError):
            continue
        if exam not in buckets:
            buckets[exam] = []
            order.append(exam)
        buckets[exam].append(score)
    out: list[dict[str, Any]] = []
    for exam in order:
        vals = buckets.get(exam) or []
        if vals:
            out.append({"exam": exam, "avg": round(sum(vals) / len(vals), 2)})
    return out


def _student_progress_from_rows(score_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """多场成绩 → 学生进退步：``[{name, student, delta}, ...]``（末场−首场）。"""
    exam_order: list[str] = []
    exam_seen: set[str] = set()
    # student -> exam -> subject -> score；无科目时 subject=""
    by_stu: dict[str, dict[str, dict[str, float]]] = {}
    for r in score_rows:
        if not isinstance(r, dict) or r.get("score") is None:
            continue
        exam = str(r.get("exam_name") or r.get("exam") or "").strip()
        stu = str(
            r.get("student_name")
            or r.get("student")
            or r.get("student_id")
            or r.get("name")
            or ""
        ).strip()
        if not exam or not stu:
            continue
        try:
            score = float(r["score"])
        except (TypeError, ValueError):
            continue
        if exam not in exam_seen:
            exam_seen.add(exam)
            exam_order.append(exam)
        subj = str(r.get("subject") or r.get("subject_name") or "").strip() or "_"
        by_stu.setdefault(stu, {}).setdefault(exam, {})[subj] = score

    if len(exam_order) < 2:
        return []

    out: list[dict[str, Any]] = []
    for stu, exams in by_stu.items():
        totals = {
            e: sum(subs.values())
            for e, subs in exams.items()
            if subs
        }
        present = [e for e in exam_order if e in totals]
        if len(present) < 2:
            continue
        first, last = present[0], present[-1]
        delta = float(totals[last]) - float(totals[first])
        out.append({
            "name": stu,
            "student": stu,
            "delta": round(delta, 2),
            "first_exam": first,
            "last_exam": last,
            "first_score": round(float(totals[first]), 2),
            "last_score": round(float(totals[last]), 2),
        })
    return out


def _is_exam_avg_trend(records: list[dict[str, Any]] | None) -> bool:
    if not records:
        return False
    sample = records[0]
    return "avg" in sample and "exam" in sample and sample.get("delta") is None


def _is_progress_delta(records: list[dict[str, Any]] | None) -> bool:
    if not records:
        return False
    return any(r.get("delta") is not None for r in records)


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
    progress_records: list[dict[str, Any]] | None = None,
    config: EducationConfig | None = None,
    scope_label: str = "",
    exam_name: str = "",
    subject_name: str = "",
) -> dict[str, Any]:
    """从成绩行组装 diagnostic_report 模板 data。

    - ``trend_records``：考试均分走势 ``[{exam, avg}, ...]``，供一般性趋势图；
      若未传或形态不对，从 ``score_rows`` 自动聚合。
    - ``progress_records``：学生进退步 ``[{name, delta}, ...]``，供 S3 动态性；
      若未传，从 ``score_rows`` 按首末场自动计算。
    """
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

    # 考试均分趋势（一般性图）
    exam_trend = list(trend_records or []) if _is_exam_avg_trend(trend_records) else []
    if not exam_trend:
        exam_trend = _exam_avg_trend_from_rows(score_rows)

    # 学生进退步（S3）
    progress = list(progress_records or [])
    if not progress and _is_progress_delta(trend_records):
        # 兼容旧调用：把带 delta 的 trend_records 当进退步
        progress = list(trend_records or [])
    if not progress:
        progress = _student_progress_from_rows(score_rows)

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
    if exam_trend and len(exam_trend) >= 2:
        exams = [str(rec.get("exam") or "") for rec in exam_trend]
        avgs = [float(rec.get("avg") or 0) for rec in exam_trend]
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

    # S3 动态性：需要 ≥2 场考试 + 可对齐的学生进退步
    dynamic_insight = "<p>暂无多次考试数据，动态性分析略。</p>"
    progress_table = ""
    trend_line_chart = ""
    exam_count = len(exam_trend)
    deltas: list[dict[str, Any]] = []
    if exam_count >= 2 and progress:
        deltas = [
            {
                "name": str(r.get("name") or r.get("student") or ""),
                "delta": float(r.get("delta") or 0),
            }
            for r in progress
            if (r.get("name") or r.get("student")) and r.get("delta") is not None
        ]
        if deltas:
            pr = compute_top_progress_regress(deltas, top_n=5)
            dist = compute_trend_distribution(deltas)
            # S2「退步生」与 S3「退步」统一为跨场末场−首场口径（|Δ|>5）
            at_risk = {
                **at_risk,
                "regression": [
                    {
                        "name": str(d.get("name") or ""),
                        "delta": d.get("delta"),
                        "reason": (
                            f"跨场退步（末场−首场）：{_fmt(d.get('delta'))} 分"
                        ),
                    }
                    for d in (dist.get("regress") or [])
                ],
            }
            dynamic_insight = (
                f"<p>共 <strong>{exam_count}</strong> 场考试；"
                f"进步 {len(dist.get('progress') or [])} 人，"
                f"退步 {len(dist.get('regress') or [])} 人，"
                f"稳定 {len(dist.get('stable') or [])} 人。</p>"
            )
            prog_rows = [
                [str(p.get("name")), f"+{_fmt(p.get('delta'))}"]
                for p in (pr.get("progress") or [])
            ]
            reg_rows = [
                [str(p.get("name")), _fmt(p.get("delta"))]
                for p in (pr.get("regress") or [])
            ]
            table_rows = prog_rows + reg_rows
            progress_table = (
                _table(["学生", "变化（末场−首场）"], table_rows) if table_rows else ""
            )
            trend_line_chart = build_chart_option(
                "progress_regress_bar",
                {"items": pr.get("chart_items") or []},
                title="进退步分布",
            )
    elif exam_count >= 2:
        dynamic_insight = (
            f"<p>已覆盖 <strong>{exam_count}</strong> 场考试均分走势；"
            "但缺少可跨场对齐的学生成绩，个体进退步名单暂略。</p>"
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


__all__ = [
    "build_diagnostic_data",
    "_exam_avg_trend_from_rows",
    "_student_progress_from_rows",
]
