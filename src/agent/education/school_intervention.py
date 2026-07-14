"""学校科目诊断——重点干预提示（班级 / 分数段 / 知识点 / 学科薄弱项）。"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from src.agent.education.aggregation import aggregate_by, prepare_score_rows_for_kpi
from src.agent.education.config import EducationConfig
from src.agent.education.cross_analysis import compare_groups
from src.agent.education.knowledge_tier import ABILITY_LABELS, build_ability_tier_summary
from src.agent.education.stats import compute_score_stats, normalize_segments
from src.agent.education.subject_diagnosis import identify_weak_items, identify_weak_knowledge


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt(value: Any) -> str:
    v = _num(value)
    if v is None:
        return "-"
    return f"{v:.2f}"


def identify_weak_classes(
    score_rows: list[dict[str, Any]],
    stats: dict[str, Any] | None = None,
    *,
    config: EducationConfig | None = None,
    min_students: int = 3,
) -> list[dict[str, Any]]:
    """识别相对校级基准需重点干预的班级。"""
    if not score_rows:
        return []
    if config is None:
        config = EducationConfig()

    class_agg = aggregate_by("class", score_rows, config)
    class_agg = [g for g in class_agg if int(g.get("count") or 0) >= min_students]
    if not class_agg:
        return []

    stats = stats or {}
    baseline = {
        "dimension_value": "校级",
        "avg": stats.get("avg"),
        "pass_rate": stats.get("pass_rate"),
        "stdev": stats.get("stdev"),
    }
    school_avg = _num(baseline.get("avg"))
    school_pass = _num(baseline.get("pass_rate"))
    comparisons = compare_groups(baseline, class_agg, ["avg", "pass_rate"])

    weak: list[dict[str, Any]] = []
    for comp, agg in zip(comparisons, class_agg):
        name = str(comp.get("name") or agg.get("dimension_value") or "")
        if not name:
            continue
        deltas = comp.get("deltas") or {}
        avg_delta = _num(deltas.get("avg"))
        pass_delta = _num(deltas.get("pass_rate"))
        cls_avg = _num(agg.get("avg"))
        cls_pass = _num(agg.get("pass_rate"))
        reasons: list[str] = []
        if avg_delta is not None and avg_delta < -5:
            reasons.append(f"均分低于校级 {abs(avg_delta):.1f} 分")
        elif comp.get("notable") and avg_delta is not None and avg_delta < 0:
            reasons.append(f"均分低于校级 {abs(avg_delta):.1f} 分")
        if pass_delta is not None and pass_delta < -8:
            reasons.append(f"及格率低于校级 {abs(pass_delta):.1f}%")
        if cls_pass is not None and school_pass is not None and cls_pass < school_pass - 5:
            if f"及格率低于校级" not in "".join(reasons):
                reasons.append(f"及格率 {cls_pass:.1f}%（校级 {school_pass:.1f}%）")
        if cls_pass is not None and cls_pass < 60:
            reasons.append(f"及格率仅 {cls_pass:.1f}%，未达 60%")
        if not reasons:
            continue
        weak.append({
            "class_name": name,
            "avg": cls_avg,
            "pass_rate": cls_pass,
            "count": agg.get("count"),
            "avg_delta": avg_delta,
            "pass_delta": pass_delta,
            "reasons": reasons,
            "priority": (
                (abs(avg_delta or 0) * 2)
                + (abs(pass_delta or 0) if pass_delta and pass_delta < 0 else 0)
                + (10 if cls_pass is not None and cls_pass < 60 else 0)
            ),
        })
    weak.sort(key=lambda x: float(x.get("priority") or 0), reverse=True)
    return weak


def identify_concern_segments(
    segments: list[dict[str, Any]] | None = None,
    stats: dict[str, Any] | None = None,
    *,
    config: EducationConfig | None = None,
    ratio_threshold: float = 12.0,
) -> list[dict[str, Any]]:
    """识别占比偏高、需重点关注的低分段。"""
    stats = stats or {}
    raw = segments if segments is not None else stats.get("segments") or []
    if not raw:
        return []
    if config is None:
        config = EducationConfig()

    full_score = _num(stats.get("full_score"))
    normalized = normalize_segments(raw, config=config, full_score=full_score)
    if not normalized:
        return []

    pass_line = None
    if full_score:
        pass_line = full_score * config.pass_ratio
    else:
        pass_line = config.pass_threshold

    concerns: list[dict[str, Any]] = []
    for idx, seg in enumerate(normalized):
        ratio = _num(seg.get("ratio")) or 0.0
        label = str(seg.get("label") or f"段{idx + 1}")
        lo_hi = label.split("-")
        seg_hi = _num(lo_hi[-1]) if len(lo_hi) >= 2 else None
        is_low_band = seg_hi is not None and seg_hi <= (pass_line or 60) + 1
        is_bottom = idx == 0
        if ratio < ratio_threshold and not (is_bottom and ratio >= 8):
            continue
        if not is_low_band and not is_bottom:
            continue
        reason = f"占比 {ratio:.1f}%"
        if is_bottom:
            reason += "，处于最低分数段"
        if seg_hi is not None and pass_line and seg_hi <= pass_line:
            reason += f"（未达及格线 {pass_line:.0f} 分）"
        concerns.append({
            "label": label,
            "count": seg.get("count"),
            "ratio": ratio,
            "reason": reason,
        })
    concerns.sort(key=lambda x: float(x.get("ratio") or 0), reverse=True)
    return concerns


def identify_weak_question_types(
    item_rows: list[dict[str, Any]] | None = None,
    *,
    weak_threshold: float = 60.0,
    max_items: int = 5,
) -> list[dict[str, Any]]:
    """按题型聚合，返回平均得分率偏低的题型。"""
    buckets: dict[str, list[float]] = defaultdict(list)
    for row in item_rows or []:
        qt = str(row.get("question_type") or "").strip()
        rate = _num(row.get("score_rate"))
        if not qt or rate is None:
            continue
        buckets[qt].append(rate)
    weak: list[dict[str, Any]] = []
    for qt, rates in buckets.items():
        avg = sum(rates) / len(rates)
        if avg < weak_threshold:
            weak.append({
                "question_type": qt,
                "question_count": len(rates),
                "avg_score_rate": round(avg, 2),
            })
    weak.sort(key=lambda x: float(x.get("avg_score_rate") or 0))
    return weak[:max_items]


def build_school_intervention_insights(
    *,
    score_rows: list[dict[str, Any]] | None = None,
    stats: dict[str, Any] | None = None,
    knowledge_rows: list[dict[str, Any]] | None = None,
    item_rows: list[dict[str, Any]] | None = None,
    config: EducationConfig | None = None,
    weak_threshold: float = 60.0,
) -> dict[str, Any]:
    """汇总学校级重点干预四类信号。"""
    if config is None:
        config = EducationConfig()

    knowledge = list(knowledge_rows or [])
    items = list(item_rows or [])
    raw_rows = list(score_rows or [])
    rows = prepare_score_rows_for_kpi(raw_rows)
    st = dict(stats or {})
    # 多场/重复行会导致人数膨胀：按清洗后成绩重算 KPI 与分数段
    if rows and (
        not st.get("count")
        or int(st.get("count") or 0) != len(rows)
        or len(rows) < len(raw_rows)
    ):
        scores: list[float] = []
        fs: float | None = None
        for r in rows:
            try:
                if r.get("score") is not None and r.get("score") != "":
                    scores.append(float(r["score"]))
            except (TypeError, ValueError):
                continue
            if fs is None and r.get("exam_score") is not None:
                try:
                    fs = float(r["exam_score"])
                except (TypeError, ValueError):
                    pass
        if scores:
            st = compute_score_stats(scores, config, fs)

    weak_classes = identify_weak_classes(rows, st, config=config)
    concern_segments = identify_concern_segments(st.get("segments"), st, config=config)
    weak_knowledge = identify_weak_knowledge(knowledge, weak_threshold=weak_threshold)
    weak_items = identify_weak_items(items, weak_threshold=weak_threshold)
    tier = build_ability_tier_summary(knowledge, weak_threshold=weak_threshold)
    weak_levels = list(tier.get("weak_levels") or [])
    weak_qtypes = identify_weak_question_types(items, weak_threshold=weak_threshold)

    class_agg = aggregate_by("class", rows, config) if rows else []

    return {
        "weak_classes": weak_classes,
        "concern_segments": concern_segments,
        "weak_knowledge": weak_knowledge,
        "weak_items": weak_items,
        "weak_ability_levels": weak_levels,
        "weak_question_types": weak_qtypes,
        "class_compare": class_agg,
        "stats": st,
        "has_intervention": bool(
            weak_classes or concern_segments or weak_knowledge
            or weak_levels or weak_qtypes
        ),
    }


def build_class_compare_table_html(
    class_agg: list[dict[str, Any]],
    weak_classes: list[dict[str, Any]] | None = None,
    *,
    school_stats: dict[str, Any] | None = None,
) -> str:
    """班级 KPI 对比表，薄弱班级高亮。"""
    if not class_agg:
        return ""
    weak_names = {str(c.get("class_name") or "") for c in (weak_classes or [])}
    school_avg = _num((school_stats or {}).get("avg"))
    school_pass = _num((school_stats or {}).get("pass_rate"))
    rows_html: list[str] = []
    for g in sorted(class_agg, key=lambda x: _num(x.get("avg")) or 0):
        name = str(g.get("dimension_value") or "")
        flagged = name in weak_names
        style = ' style="background:#fff2f0"' if flagged else ""
        tag = ' <span class="edu-badge" style="background:#fff2f0;color:#ff4d4f">需干预</span>' if flagged else ""
        rows_html.append(
            f"<tr{style}><td class='edu-cell-text'>{name}{tag}</td>"
            f"<td class='num'>{g.get('count', '-')}</td>"
            f"<td class='num'>{_fmt(g.get('avg'))}</td>"
            f"<td class='num'>{_fmt(g.get('pass_rate'))}%</td>"
            f"<td class='num'>{_fmt(g.get('excellent_rate'))}%</td></tr>"
        )
    school_row = ""
    if school_avg is not None or school_pass is not None:
        school_row = (
            "<tr style='background:#e6f4ff;font-weight:600'>"
            "<td>校级基准</td><td class='num'>-</td>"
            f"<td class='num'>{_fmt(school_avg)}</td>"
            f"<td class='num'>{_fmt(school_pass)}%</td>"
            "<td class='num'>-</td></tr>"
        )
    inner = (
        "<table class='edu-table'><thead><tr>"
        "<th>班级</th><th class='num'>人数</th><th class='num'>均分</th>"
        "<th class='num'>及格率</th><th class='num'>优秀率</th></tr></thead>"
        f"<tbody>{school_row}{''.join(rows_html)}</tbody></table>"
    )
    return f'<div class="edu-table-wrap">{inner}</div>'


def build_intervention_section_html(insights: dict[str, Any]) -> str:
    """渲染「重点干预提示」HTML 区块。"""
    if not insights.get("has_intervention"):
        return (
            "<p class='edu-sub'>本次考试各班级与知识点整体处于可控区间，"
            "建议按常规教学节奏巩固，并持续关注临界生。</p>"
        )

    parts: list[str] = []
    wc = insights.get("weak_classes") or []
    cs = insights.get("concern_segments") or []
    wk = insights.get("weak_knowledge") or []
    wl = insights.get("weak_ability_levels") or []
    wq = insights.get("weak_question_types") or []

    parts.append(
        '<div class="edu-grid" style="margin-bottom:16px">'
        f'<div class="edu-kpi"><div class="label">需干预班级</div>'
        f'<div class="value" style="color:var(--edu-error)">{len(wc)}</div></div>'
        f'<div class="edu-kpi"><div class="label">关注分数段</div>'
        f'<div class="value" style="color:var(--edu-warning)">{len(cs)}</div></div>'
        f'<div class="edu-kpi"><div class="label">薄弱知识点</div>'
        f'<div class="value" style="color:var(--edu-warning)">{len(wk)}</div></div>'
        f'<div class="edu-kpi"><div class="label">学科薄弱项</div>'
        f'<div class="value" style="color:var(--edu-warning)">{len(wl) + len(wq)}</div></div>'
        "</div>"
    )

    if wc:
        lis = "".join(
            f"<li><strong>{c.get('class_name')}</strong>："
            f"均分 {_fmt(c.get('avg'))}，及格率 {_fmt(c.get('pass_rate'))}%——"
            f"{'；'.join(c.get('reasons') or [])}</li>"
            for c in wc[:8]
        )
        parts.append(f"<h3>需重点干预的班级</h3><ul>{lis}</ul>")

    if cs:
        lis = "".join(
            f"<li><strong>{s.get('label')}</strong>（{s.get('count', 0)} 人）：{s.get('reason')}</li>"
            for s in cs[:5]
        )
        parts.append(
            f"<h3>需关注的分数段</h3><ul>{lis}</ul>"
            "<p class='edu-sub'>低分段占比较高时，建议开展分层辅导与临界生转化。</p>"
        )

    if wk:
        names = "、".join(
            f"{r.get('knowledge_name')}（{_fmt(r.get('score_rate'))}%）"
            for r in wk[:8]
        )
        parts.append(f"<h3>需加强的知识点</h3><p>{names}</p>")

    weak_subject_parts: list[str] = []
    if wl:
        level_text = "、".join(
            f"{ABILITY_LABELS.get(str(l.get('ability_level') or ''), l.get('ability_level') or '未分级')}"
            f"（均分率 {_fmt(l.get('avg_score_rate'))}%）"
            for l in wl[:4]
        )
        weak_subject_parts.append(f"能力层级薄弱：<strong>{level_text}</strong>")
    if wq:
        qtext = "、".join(
            f"{q.get('question_type')}（{_fmt(q.get('avg_score_rate'))}%）"
            for q in wq[:5]
        )
        weak_subject_parts.append(f"题型薄弱：<strong>{qtext}</strong>")
    if weak_subject_parts:
        parts.append(
            "<h3>学科薄弱环节</h3>"
            + "".join(f"<p>{x}</p>" for x in weak_subject_parts)
        )

    return "\n".join(parts)


def build_intervention_recommendations(insights: dict[str, Any]) -> list[str]:
    """从干预洞察生成可执行教学建议条目。"""
    items: list[str] = []
    for c in insights.get("weak_classes") or []:
        name = c.get("class_name") or "该班"
        items.append(
            f"对 <strong>{name}</strong> 开展专项帮扶：巩固基础题、盯紧及格率与课堂过关"
        )
    for s in insights.get("concern_segments") or []:
        items.append(
            f"针对 <strong>{s.get('label')}</strong> 分数段（占比 {_fmt(s.get('ratio'))}%）"
            "组织分层练习与错题清零"
        )
    for r in insights.get("weak_knowledge") or []:
        name = r.get("knowledge_name") or "薄弱知识点"
        items.append(
            f"优先补强「{name}」（得分率 {_fmt(r.get('score_rate'))}%），安排专题课与变式训练"
        )
    for l in insights.get("weak_ability_levels") or []:
        label = ABILITY_LABELS.get(str(l.get("ability_level") or ""), l.get("ability_level") or "能力项")
        items.append(f"加强「{label}」层级训练，提升综合与应用题得分率")
    for q in insights.get("weak_question_types") or []:
        items.append(
            f"针对「{q.get('question_type')}」题型（均分率 {_fmt(q.get('avg_score_rate'))}%）"
            "精讲典型错因并配套限时训练"
        )
    return items[:12]


def append_intervention_to_summary(summary_html: str, insights: dict[str, Any]) -> str:
    """在学校诊断摘要中补充干预要点。"""
    if not insights.get("has_intervention"):
        return summary_html
    bullets: list[str] = []
    wc = insights.get("weak_classes") or []
    if wc:
        names = "、".join(str(c.get("class_name") or "") for c in wc[:5])
        bullets.append(f"<strong>重点干预班级：</strong>{names}")
    cs = insights.get("concern_segments") or []
    if cs:
        seg = "、".join(str(s.get("label") or "") for s in cs[:3])
        bullets.append(f"<strong>关注分数段：</strong>{seg}")
    wk = insights.get("weak_knowledge") or []
    if wk:
        kn = "、".join(str(r.get("knowledge_name") or "") for r in wk[:5])
        bullets.append(f"<strong>薄弱知识点：</strong>{kn}")
    if not bullets:
        return summary_html
    extra = "<p>" + "；".join(bullets) + "。</p>"
    return summary_html + extra


__all__ = [
    "append_intervention_to_summary",
    "build_class_compare_table_html",
    "build_intervention_recommendations",
    "build_intervention_section_html",
    "build_school_intervention_insights",
    "identify_concern_segments",
    "identify_weak_classes",
    "identify_weak_question_types",
]
