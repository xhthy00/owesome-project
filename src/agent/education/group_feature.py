"""群体特征报告：按班级/区县等维度聚合画像，对齐班级横向对比报告质量。"""

from __future__ import annotations

from typing import Any

from src.agent.education.aggregation import aggregate_by
from src.agent.education.charts import build_chart_option
from src.agent.education.config import EducationConfig
from src.agent.education.report_types import ReportType, report_type_label
from src.agent.education.stats import compute_score_stats


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt(value: Any, *, digits: int = 1) -> str:
    v = _num(value)
    if v is None:
        return "—"
    return f"{v:.{digits}f}"


def _fmt_pct(value: Any) -> str:
    v = _num(value)
    if v is None:
        return "—"
    return f"{v:.1f}%"


def _table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return "<p>暂无</p>"
    head = "<tr>" + "".join(f"<th>{h}</th>" for h in headers) + "</tr>"
    body = "".join(
        "<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows
    )
    return (
        f'<div class="edu-table-wrap"><table class="edu-table">'
        f"<thead>{head}</thead><tbody>{body}</tbody></table></div>"
    )


def _kpi_card(label: str, value: str, *, hint: str = "") -> str:
    hint_html = f'<div class="hint">{hint}</div>' if hint else ""
    return (
        f'<div class="edu-kpi"><div class="label">{label}</div>'
        f'<div class="value">{value}</div>{hint_html}</div>'
    )


_DIM_LABELS = {
    "class": "班级",
    "district": "区县",
    "grade": "年级",
    "subject": "科目",
    "school": "学校",
}


def classify_group_feature(
    group: dict[str, Any],
    *,
    school_avg: float | None,
    school_stdev: float | None,
) -> tuple[str, str]:
    """返回 (特征标签, 简短说明)。"""
    avg = _num(group.get("avg"))
    stdev = _num(group.get("stdev"))
    gmax = _num(group.get("max"))
    gmin = _num(group.get("min"))
    spread = (gmax - gmin) if gmax is not None and gmin is not None else None

    avg_gap = (avg - school_avg) if avg is not None and school_avg is not None else 0.0
    high_disp = False
    if stdev is not None and school_stdev is not None and school_stdev > 0:
        high_disp = stdev >= school_stdev * 1.15
    elif spread is not None and school_avg:
        high_disp = spread >= max(30.0, school_avg * 0.35)

    if avg_gap >= 3 and not high_disp:
        return "优势均衡", "均分高于校级且分布相对集中"
    if avg_gap >= 3 and high_disp:
        return "优势两极", "均分领先但高低分落差大，需关注低分端"
    if avg_gap <= -3 and high_disp:
        return "薄弱两极", "均分偏低且分布发散，优先分层补差"
    if avg_gap <= -3 and not high_disp:
        return "整体偏弱", "均分低于校级，需整体性提质"
    if high_disp:
        return "中间分化", "均分接近校级但内部差异明显"
    return "中间稳健", "均分接近校级且分布相对集中"


def enrich_group_features(
    groups: list[dict[str, Any]],
    *,
    school_stats: dict[str, Any],
) -> list[dict[str, Any]]:
    school_avg = _num(school_stats.get("avg"))
    school_stdev = _num(school_stats.get("stdev"))
    out: list[dict[str, Any]] = []
    for g in groups:
        item = dict(g)
        gmax = _num(g.get("max"))
        gmin = _num(g.get("min"))
        item["score_range"] = (
            round(gmax - gmin, 2) if gmax is not None and gmin is not None else None
        )
        label, reason = classify_group_feature(
            g, school_avg=school_avg, school_stdev=school_stdev
        )
        item["feature_label"] = label
        item["feature_reason"] = reason
        out.append(item)
    return out


def build_group_feature_data(
    score_rows: list[dict[str, Any]],
    *,
    dimension: str = "class",
    config: EducationConfig | None = None,
    school_name: str = "",
    subject_name: str = "",
    exam_name: str = "",
    knowledge_class_rows: list[dict[str, Any]] | None = None,
    item_class_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """组装群体特征报告模板 data。"""
    if config is None:
        from src.agent.education.config_store import get_config

        config = get_config()

    dim = (dimension or "class").strip().lower()
    dim_label = _DIM_LABELS.get(dim, dim or "分组")
    rows = [dict(r) for r in score_rows if isinstance(r, dict)]

    scores = [_num(r.get("score")) for r in rows]
    scores = [s for s in scores if s is not None]
    full_score = None
    for r in rows:
        fs = _num(r.get("exam_score") or r.get("full_score"))
        if fs is not None:
            full_score = fs
            break
    school_stats = compute_score_stats(scores, config, full_score)
    groups_raw = aggregate_by(dim, rows, config)
    # 过滤「未知」空组（除非整批都未知）
    known = [
        g
        for g in groups_raw
        if str(g.get("dimension_value") or "") not in ("", "未知班级", "未知区县", "未知学校", "未知")
    ]
    groups_raw = known if len(known) >= 2 else groups_raw
    groups = enrich_group_features(groups_raw, school_stats=school_stats)
    sorted_groups = sorted(groups, key=lambda g: float(g.get("avg") or 0), reverse=True)

    # ---- KPI ----
    kpi_grid = (
        '<div class="edu-grid">'
        + _kpi_card("参考人数", str(school_stats.get("count") or 0))
        + _kpi_card("校级均分", _fmt(school_stats.get("avg")))
        + _kpi_card("及格率", _fmt_pct(school_stats.get("pass_rate")))
        + _kpi_card("优秀率", _fmt_pct(school_stats.get("excellent_rate")))
        + _kpi_card("最高分", _fmt(school_stats.get("max")))
        + _kpi_card("最低分", _fmt(school_stats.get("min")))
        + _kpi_card("标准差", _fmt(school_stats.get("stdev"), digits=2))
        + "</div>"
    )

    # ---- charts ----
    labels = [str(g.get("dimension_value") or "") for g in sorted_groups]
    avgs = [float(g.get("avg") or 0) for g in sorted_groups]
    pass_rates = [float(g.get("pass_rate") or 0) for g in sorted_groups]
    avg_chart = build_chart_option(
        "class_compare_bar",
        {"classes": labels, "values": avgs},
        title=f"各{dim_label}均分对比",
    )
    pass_chart = build_chart_option(
        "class_compare_bar",
        {"classes": labels, "values": pass_rates},
        title=f"各{dim_label}及格率对比（%）",
    )

    # ---- ranking table ----
    table_rows: list[list[str]] = []
    for rank, g in enumerate(sorted_groups, start=1):
        table_rows.append(
            [
                str(rank),
                str(g.get("dimension_value") or ""),
                str(g.get("count") or 0),
                _fmt(g.get("avg")),
                _fmt_pct(g.get("pass_rate")),
                _fmt_pct(g.get("excellent_rate")),
                _fmt(g.get("stdev"), digits=2),
                _fmt(g.get("max")),
                _fmt(g.get("min")),
                _fmt(g.get("score_range")),
                str(g.get("feature_label") or ""),
            ]
        )
    group_table = _table(
        [
            "排名",
            dim_label,
            "人数",
            "均分",
            "及格率",
            "优秀率",
            "标准差",
            "最高",
            "最低",
            "极差",
            "特征",
        ],
        table_rows,
    )

    # ---- feature cards ----
    feature_bits: list[str] = []
    for g in sorted_groups:
        name = str(g.get("dimension_value") or "")
        feature_bits.append(
            "<div class='edu-feature-card'>"
            f"<div class='edu-feature-title'>{name}"
            f"<span class='edu-badge'>{g.get('feature_label') or ''}</span></div>"
            f"<p>均分 <strong>{_fmt(g.get('avg'))}</strong>，"
            f"及格率 {_fmt_pct(g.get('pass_rate'))}，"
            f"极差 {_fmt(g.get('score_range'))}；"
            f"{g.get('feature_reason') or ''}。</p>"
            "</div>"
        )
    feature_cards = (
        f'<div class="edu-feature-grid">{"".join(feature_bits)}</div>'
        if feature_bits
        else "<p>暂无分组特征。</p>"
    )

    # ---- diff / intervention ----
    diff_info = "<p>各组差异整体可控。</p>"
    if len(sorted_groups) >= 2:
        top, low = sorted_groups[0], sorted_groups[-1]
        gap = float(top.get("avg") or 0) - float(low.get("avg") or 0)
        diff_info = (
            f"<p>均分最高：<strong>{top.get('dimension_value')}</strong>"
            f"（{_fmt(top.get('avg'))}，{top.get('feature_label')}）；"
            f"最低：<strong>{low.get('dimension_value')}</strong>"
            f"（{_fmt(low.get('avg'))}，{low.get('feature_label')}）；"
            f"极差 <strong>{_fmt(gap)}</strong> 分。</p>"
        )
        polar = [g for g in sorted_groups if "两极" in str(g.get("feature_label") or "")]
        if polar:
            names = "、".join(str(g.get("dimension_value")) for g in polar[:4])
            diff_info += f"<p>需重点关注两极分化：{names}。</p>"

    intervention = "<ul>"
    weakish = [
        g
        for g in sorted_groups
        if str(g.get("feature_label") or "").startswith(("薄弱", "整体偏弱"))
        or "两极" in str(g.get("feature_label") or "")
    ]
    if weakish:
        for g in weakish[:4]:
            intervention += (
                f"<li><strong>{g.get('dimension_value')}</strong>："
                f"{g.get('feature_label')}——{g.get('feature_reason')}；"
                "建议分层辅导与临界生盯盯。</li>"
            )
    else:
        intervention += f"<li>各{dim_label}相对均衡，维持学情跟踪与培优补差节奏。</li>"
    intervention += (
        f"<li>对照校级均分 {_fmt(school_stats.get('avg'))}，"
        f"推动高表现{dim_label}经验迁移。</li></ul>"
    )

    # ---- optional knowledge / item compare (reuse grade-compare builders) ----
    knowledge_html = ""
    item_html = ""
    qtype_html = ""
    if knowledge_class_rows:
        from src.agent.education.subject_diagnosis import build_knowledge_compare_table_html

        knowledge_html = build_knowledge_compare_table_html(knowledge_class_rows) or ""
    if item_class_rows:
        from src.agent.education.subject_diagnosis import build_item_compare_table_html
        from src.agent.education.knowledge_tier import (
            build_question_type_compare_table_html,
        )

        item_html = build_item_compare_table_html(item_class_rows) or ""
        qtype_html = build_question_type_compare_table_html(item_class_rows) or ""

    scope = school_name or "本范围"
    title_bits = [p for p in (school_name, subject_name, f"按{dim_label}") if p]
    title = f"{' · '.join(title_bits)}群体特征报告" if title_bits else "群体特征报告"
    summary = (
        f"<p>按 <strong>{dim_label}</strong> 共识别 <strong>{len(sorted_groups)}</strong> 组，"
        f"覆盖 <strong>{school_stats.get('count') or 0}</strong> 人；"
        f"校级均分 {_fmt(school_stats.get('avg'))}，"
        f"及格率 {_fmt_pct(school_stats.get('pass_rate'))}。</p>"
    )

    return {
        "REPORT_TITLE": title,
        "REPORT_TYPE": report_type_label(ReportType.GROUP_FEATURE),
        "REPORT_SUBTITLE": scope,
        "SCOPE": scope,
        "EXAM_NAME": exam_name or "本次考试",
        "SUBJECT_NAME": subject_name or "全科",
        "GROUP_DIMENSION": dim_label,
        "KPI_GRID": kpi_grid,
        "GROUP_COMPARE_CHART": avg_chart,
        "PASS_COMPARE_CHART": pass_chart,
        "GROUP_TABLE": group_table,
        "FEATURE_CARDS": feature_cards,
        "DIFF_INFO": diff_info,
        "INTERVENTION": intervention,
        "KNOWLEDGE_COMPARE_TABLE": knowledge_html,
        "ITEM_COMPARE_TABLE": item_html,
        "QUESTION_TYPE_COMPARE_TABLE": qtype_html,
        "SUMMARY": summary,
        "RECOMMENDATIONS": intervention,
        "_groups": sorted_groups,
        "_school_stats": school_stats,
    }
