"""科目诊断报告——小题与知识点聚合、薄弱项识别（纯函数，不依赖 LLM）。"""

from __future__ import annotations

import ast
import json
from typing import Any

from src.agent.education.knowledge_tier import ABILITY_LABELS
from src.agent.education.stats import normalize_segments


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt(v: Any) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.2f}"
    return str(v)


def _is_html_fragment(value: str) -> bool:
    s = value.strip().lower()
    return s.startswith("<table") or s.startswith("<p ") or s.startswith("<div")


def _coerce_row_list(value: Any) -> list[dict[str, Any]] | None:
    """将 list / JSON 字符串 / Python repr 转为 dict 列表。"""
    if isinstance(value, list):
        if value and all(isinstance(x, dict) for x in value):
            return [dict(x) for x in value]
        return None
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not s.startswith("["):
        return None
    for loader in (json.loads, ast.literal_eval):
        try:
            parsed = loader(s)
        except Exception:
            continue
        if isinstance(parsed, list) and parsed and all(isinstance(x, dict) for x in parsed):
            return [dict(x) for x in parsed]
    return None


def normalize_item_row(row: dict[str, Any]) -> dict[str, Any]:
    """统一班级聚合行与学生逐题行的字段名。"""
    r = dict(row)
    if r.get("full_score") is None and r.get("question_score") is not None:
        r["full_score"] = r["question_score"]
    if r.get("avg_score") is None and r.get("score") is not None:
        r["avg_score"] = r["score"]
    if r.get("score_rate") is None:
        fs = _num(r.get("full_score"))
        av = _num(r.get("avg_score"))
        if fs and av is not None:
            r["score_rate"] = round(av / fs * 100, 2)
    return r


def normalize_knowledge_row(row: dict[str, Any]) -> dict[str, Any]:
    r = dict(row)
    if r.get("question_count") is None and r.get("question_nos") is not None:
        r["question_count"] = r["question_nos"]
    return r


def _wrap_table_html(table_html: str) -> str:
    if not table_html or "<table" not in table_html:
        return table_html
    return f'<div class="edu-table-wrap">{table_html}</div>'


def build_item_table_html(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "<p class='edu-sub'>暂无小题数据</p>"
    norm = [normalize_item_row(r) for r in rows]
    score_label = "得分" if any("score" in r for r in rows) else "均分"
    extra_cols = any(
        r.get(k) is not None
        for r in norm
        for k in ("question_type", "difficulty", "discrimination")
    )
    extra_head = ""
    extra_cells = ""
    if extra_cols:
        extra_head = "<th>题型</th><th class='num'>难度</th><th class='num'>区分度</th>"
    body_parts: list[str] = []
    for r in norm:
        cells = (
            f"<tr><td>{r.get('question_no', '')}</td>"
            f"<td class='edu-cell-text'>{r.get('knowledge_name') or '-'}</td>"
            f"<td class='num'>{_fmt(r.get('full_score'))}</td>"
            f"<td class='num'>{_fmt(r.get('avg_score'))}</td>"
            f"<td class='num'>{_fmt(r.get('score_rate'))}%</td>"
        )
        if extra_cols:
            cells += (
                f"<td>{r.get('question_type') or '-'}</td>"
                f"<td class='num'>{_fmt(r.get('difficulty'))}</td>"
                f"<td class='num'>{_fmt(r.get('discrimination'))}</td>"
            )
        body_parts.append(cells + "</tr>")
    body = "".join(body_parts)
    return _wrap_table_html(
        "<table class='edu-table edu-table--item'><thead><tr>"
        f"<th>题号</th><th>知识点</th><th class='num'>满分</th><th class='num'>{score_label}</th><th class='num'>得分率</th>"
        f"{extra_head}</tr></thead>"
        f"<tbody>{body}</tbody></table>"
    )


def build_knowledge_table_html(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "<p class='edu-sub'>暂无知识点数据</p>"
    norm = [normalize_knowledge_row(r) for r in rows]
    has_level_col = any(r.get("ability_level") for r in norm)
    level_head = "<th>能力层级</th>" if has_level_col else ""
    body_parts: list[str] = []
    for r in norm:
        level_cell = ""
        if has_level_col:
            al = str(r.get("ability_level") or "")
            level_cell = f"<td>{ABILITY_LABELS.get(al, al or '-')}</td>"
        body_parts.append(
            f"<tr><td class='edu-cell-text'>{r.get('knowledge_name', '')}</td>"
            f"<td class='num'>{r.get('question_count', r.get('question_nos', '-'))}</td>"
            f"<td class='num'>{_fmt(r.get('score_rate'))}%</td>"
            f"<td>{r.get('level') or _knowledge_level(_num(r.get('score_rate')))}</td>"
            f"{level_cell}</tr>"
        )
    return _wrap_table_html(
        "<table class='edu-table'><thead><tr>"
        "<th>知识点</th><th class='num'>涉及题数</th><th class='num'>得分率</th><th>掌握水平</th>"
        f"{level_head}</tr></thead>"
        f"<tbody>{''.join(body_parts)}</tbody></table>"
    )


def _knowledge_level(rate: float | None) -> str:
    if rate is None:
        return "未知"
    if rate >= 85:
        return "优秀"
    if rate >= 70:
        return "良好"
    if rate >= 60:
        return "及格"
    return "需加强"


def identify_weak_knowledge(
    rows: list[dict[str, Any]],
    *,
    weak_threshold: float = 60.0,
    max_items: int = 8,
) -> list[dict[str, Any]]:
    """按得分率升序返回薄弱知识点（得分率 < weak_threshold）。"""
    scored: list[tuple[float, dict[str, Any]]] = []
    for r in rows:
        rate = _num(r.get("score_rate"))
        if rate is None:
            continue
        scored.append((rate, r))
    scored.sort(key=lambda x: x[0])
    weak = [r for rate, r in scored if rate < weak_threshold]
    return weak[:max_items]


def identify_weak_items(
    rows: list[dict[str, Any]],
    *,
    weak_threshold: float = 60.0,
    max_items: int = 5,
) -> list[dict[str, Any]]:
    scored: list[tuple[float, dict[str, Any]]] = []
    for r in rows:
        rate = _num(r.get("score_rate"))
        if rate is None:
            continue
        scored.append((rate, r))
    scored.sort(key=lambda x: x[0])
    return [r for rate, r in scored if rate < weak_threshold][:max_items]


def _rate_level(rate: float | None, *, weak_threshold: float = 60.0) -> str:
    v = _num(rate)
    if v is None:
        return "neutral"
    if v < weak_threshold:
        return "error"
    if v < 70:
        return "warn"
    return "ok"


def _rate_bar_html(rate: float | None, *, weak_threshold: float = 60.0) -> str:
    v = _num(rate) or 0
    level = _rate_level(rate, weak_threshold=weak_threshold)
    width = max(0, min(100, v))
    return (
        f'<div class="edu-rate-track edu-rate-{level}">'
        f'<div class="edu-rate-fill" style="width:{width:.0f}%"></div></div>'
        f'<span class="edu-rate-val edu-rate-{level}">{_fmt(rate)}%</span>'
    )


def _diag_stat_card(label: str, value: str, *, tone: str = "primary") -> str:
    return (
        f'<div class="edu-diag-stat edu-diag-stat-{tone}">'
        f'<div class="edu-diag-stat-val">{value}</div>'
        f'<div class="edu-diag-stat-label">{label}</div></div>'
    )


def _diag_chip(name: str, rate: float | None, *, weak_threshold: float = 60.0) -> str:
    level = _rate_level(rate, weak_threshold=weak_threshold)
    return (
        f'<div class="edu-diag-chip edu-diag-chip-{level}">'
        f'<span class="edu-diag-chip-name">{name}</span>'
        f'{_rate_bar_html(rate, weak_threshold=weak_threshold)}</div>'
    )


def _diag_item_card(qno: Any, knowledge: str, rate: float | None, *, weak_threshold: float = 60.0) -> str:
    level = _rate_level(rate, weak_threshold=weak_threshold)
    kn = f'<span class="edu-diag-item-kn">{knowledge}</span>' if knowledge else ""
    return (
        f'<div class="edu-diag-item edu-diag-item-{level}">'
        f'<span class="edu-diag-item-qno">第{qno}题</span>{kn}'
        f'<span class="edu-diag-item-rate">{_fmt(rate)}%</span></div>'
    )


def _rec_group(title: str, items: list[dict[str, Any]], *, cat: str) -> str:
    if not items:
        return ""
    cards = "".join(
        f'<div class="edu-rec-card edu-rec-priority-{it.get("priority", 2)}">'
        f'<span class="edu-rec-priority">P{it.get("priority", 2)}</span>'
        f'<div class="edu-rec-body">'
        f'<div class="edu-rec-title">{it.get("title", "")}</div>'
        f'<div class="edu-rec-desc">{it.get("desc", "")}</div>'
        + (f'<span class="edu-rec-metric">{it.get("metric")}</span>' if it.get("metric") else "")
        + "</div></div>"
        for it in items
    )
    return (
        f'<div class="edu-rec-group edu-rec-cat-{cat}">'
        f'<div class="edu-rec-group-head">'
        f'<span class="edu-rec-group-title">{title}</span>'
        f'<span class="edu-rec-badge">{len(items)} 项</span></div>'
        f'<div class="edu-rec-list">{cards}</div></div>'
    )


def build_diagnosis_summary(
    *,
    school_name: str = "",
    exam_name: str = "",
    subject_name: str = "",
    stats: dict[str, Any] | None = None,
    item_rows: list[dict[str, Any]] | None = None,
    knowledge_rows: list[dict[str, Any]] | None = None,
    weak_threshold: float = 60.0,
    intervention_insights: dict[str, Any] | None = None,
) -> str:
    stats = stats or {}
    item_rows = item_rows or []
    knowledge_rows = knowledge_rows or []
    weak_k = identify_weak_knowledge(knowledge_rows, weak_threshold=weak_threshold)
    weak_i = identify_weak_items(item_rows, weak_threshold=weak_threshold)

    scope_parts = [p for p in (school_name, exam_name, subject_name) if p]
    scope = " · ".join(scope_parts) if scope_parts else "本次考试"

    parts: list[str] = [
        '<div class="edu-diag">',
        '<div class="edu-diag-header">',
        f'<span class="edu-diag-scope">{scope}</span>',
        '<span class="edu-diag-tag">科目诊断</span></div>',
    ]

    if stats.get("count"):
        pass_tone = "ok" if (_num(stats.get("pass_rate")) or 0) >= 60 else "warn"
        parts.append('<div class="edu-diag-overview">')
        parts.append(_diag_stat_card("参考人数", str(stats.get("count")), tone="primary"))
        parts.append(_diag_stat_card("平均分", _fmt(stats.get("avg")), tone="primary"))
        parts.append(_diag_stat_card("及格率", f"{_fmt(stats.get('pass_rate'))}%", tone=pass_tone))
        parts.append(_diag_stat_card("优秀率", f"{_fmt(stats.get('excellent_rate'))}%", tone="neutral"))
        parts.append("</div>")

    if knowledge_rows:
        parts.append('<div class="edu-diag-section">')
        parts.append(
            f'<div class="edu-diag-section-title">'
            f'<span class="edu-diag-dot warn"></span>知识点掌握'
            f'<span class="edu-diag-count">{len(knowledge_rows)} 个 / 薄弱 {len(weak_k)} 个</span></div>'
        )
        if weak_k:
            chips = "".join(
                _diag_chip(str(r.get("knowledge_name") or ""), _num(r.get("score_rate")), weak_threshold=weak_threshold)
                for r in weak_k[:8]
            )
            parts.append(f'<div class="edu-diag-chips">{chips}</div>')
        else:
            parts.append('<p class="edu-diag-muted">各知识点得分率整体达标，保持复习节奏即可。</p>')
        parts.append("</div>")

    if weak_i:
        parts.append('<div class="edu-diag-section">')
        parts.append(
            '<div class="edu-diag-section-title">'
            '<span class="edu-diag-dot error"></span>薄弱小题</div>'
        )
        items_html = "".join(
            _diag_item_card(
                r.get("question_no"),
                str(r.get("knowledge_name") or ""),
                _num(r.get("score_rate")),
                weak_threshold=weak_threshold,
            )
            for r in weak_i[:6]
        )
        parts.append(f'<div class="edu-diag-items">{items_html}</div></div>')

    try:
        from src.agent.education.knowledge_tier import build_ability_tier_summary

        tier = build_ability_tier_summary(knowledge_rows, weak_threshold=weak_threshold)
        weak_levels = tier.get("weak_levels") or []
        if weak_levels:
            parts.append('<div class="edu-diag-section">')
            parts.append(
                '<div class="edu-diag-section-title">'
                '<span class="edu-diag-dot warn"></span>能力层级薄弱</div>'
            )
            chips = "".join(
                _diag_chip(
                    ABILITY_LABELS.get(str(l.get("ability_level") or ""), str(l.get("ability_level") or "未分级")),
                    _num(l.get("avg_score_rate")),
                    weak_threshold=weak_threshold,
                )
                for l in weak_levels[:4]
            )
            parts.append(f'<div class="edu-diag-chips">{chips}</div></div>')
    except Exception:
        pass

    insights = intervention_insights or {}
    if insights.get("has_intervention"):
        alert_items: list[str] = []
        for c in (insights.get("weak_classes") or [])[:3]:
            alert_items.append(
                f'<span class="edu-diag-alert-item">'
                f'<span class="edu-diag-alert-label">班级</span>{c.get("class_name")}</span>'
            )
        for s in (insights.get("concern_segments") or [])[:2]:
            alert_items.append(
                f'<span class="edu-diag-alert-item">'
                f'<span class="edu-diag-alert-label">分数段</span>{s.get("label")}</span>'
            )
        for r in (insights.get("weak_knowledge") or [])[:3]:
            alert_items.append(
                f'<span class="edu-diag-alert-item">'
                f'<span class="edu-diag-alert-label">知识点</span>{r.get("knowledge_name")}</span>'
            )
        if alert_items:
            parts.append(
                '<div class="edu-diag-alert">'
                '<div class="edu-diag-alert-title">重点干预方向</div>'
                f'<div class="edu-diag-alert-items">{"".join(alert_items)}</div></div>'
            )

    if not weak_k and not weak_i and knowledge_rows:
        parts.append(
            '<p class="edu-diag-muted">整体掌握较好，建议以综合卷维持题感，并关注个别临界题。</p>'
        )

    parts.append("</div>")
    return "\n".join(parts)


def _personal_knowledge_advice(name: str, rate: float | None) -> str:
    """按得分率给出差异化的个人复习建议。"""
    n = name or "该知识点"
    if rate is None:
        return f"针对「{n}」梳理概念与典型例题，完成错题订正后再做一组变式。"
    if rate < 30:
        return (
            f"「{n}」得分率仅 {_fmt(rate)}%，属于严重薄弱项："
            f"先回归课本定义与基础例题，每天 15–20 分钟专项，再过渡到中档题。"
        )
    if rate < 50:
        return (
            f"「{n}」掌握不稳（{_fmt(rate)}%）："
            f"整理该点错题本，按题型限时做 8–10 道针对性练习，隔日复测一次。"
        )
    return (
        f"「{n}」接近及格线（{_fmt(rate)}%）："
        f"查漏补缺后做一套限时模拟，重点盯易错步骤与审题。"
    )


def _personal_item_advice(qno: Any, kn: Any, rate: float | None) -> str:
    base = f"第 {qno} 题"
    kn_s = f"（关联：{kn}）" if kn else ""
    if rate is not None and rate < 30:
        return f"{base}失分较重{kn_s}：对照标准答案定位概念缺口，先做同类基础题 5 道再回看原题。"
    if rate is not None and rate < 50:
        return f"{base}需重点订正{kn_s}：标注错因后完成 2–3 道同知识点变式，并限时重做原题。"
    return f"{base}查漏补缺{kn_s}：精讲典型错因后做 1–2 道变式巩固。"


def _class_knowledge_advice(name: str, rate: float | None, *, relative: bool = False) -> str:
    """班级维度：可落地的知识点教学动作。"""
    n = name or "该知识点"
    prefix = "相对偏弱" if relative else "薄弱"
    if rate is None:
        return f"针对「{n}」梳理通法与易错点，安排一次专题小测并过关跟踪。"
    if rate < 40:
        return (
            f"「{n}」{prefix}（{_fmt(rate)}%）：本周开专题课，"
            f"课前摸底 5 题 → 精讲通法 → 课后变式 8–10 题，三日后复测。"
        )
    if rate < 60:
        return (
            f"「{n}」{prefix}（{_fmt(rate)}%）：错题归类后限时专练两次，"
            f"并指定临界生当面订正，过关标准为同类题正确率≥80%。"
        )
    return (
        f"「{n}」仍有提升空间（{_fmt(rate)}%）：用 15–20 分钟微专题巩固，"
        f"搭配 1 组易错变式；关注班级均分附近学生是否稳固。"
    )


def _class_item_advice(qno: Any, kn: Any, rate: float | None, *, relative: bool = False) -> str:
    kn_s = f"，关联「{kn}」" if kn else ""
    tag = "相对低分题" if relative else "薄弱题"
    if rate is not None and rate < 40:
        return (
            f"第 {qno} 题为{tag}（{_fmt(rate)}%{kn_s}）：课堂上拆步骤精讲，"
            f"布置同构题 2 道限时完成并抽查板演。"
        )
    if rate is not None and rate < 60:
        return (
            f"第 {qno} 题需重点讲评（{_fmt(rate)}%{kn_s}）：先展示典型错因，"
            f"再完成 1–2 道变式，要求错题学生二次过关。"
        )
    return (
        f"第 {qno} 题仍可挖分（{_fmt(rate)}%{kn_s}）：对照标准解法订正步骤，"
        f"班内组织互批，巩固得分细节。"
    )


def _qtype_buckets_from_items(
    item_rows: list[dict[str, Any]],
) -> list[tuple[str, float, int]]:
    from collections import defaultdict

    buckets: dict[str, list[float]] = defaultdict(list)
    for r in item_rows:
        qt = str(r.get("question_type") or "").strip()
        rate = _num(r.get("score_rate"))
        if qt and rate is not None:
            buckets[qt].append(rate)
    out: list[tuple[str, float, int]] = []
    for qt, rates in buckets.items():
        out.append((qt, sum(rates) / len(rates), len(rates)))
    out.sort(key=lambda x: x[1])
    return out


def _pick_relative_rows(
    rows: list[dict[str, Any]],
    *,
    max_n: int = 3,
    soft_ceiling: float = 80.0,
) -> list[dict[str, Any]]:
    """选取相对靠后的行：优先得分率低于 soft_ceiling 的最低若干项。"""
    scored: list[tuple[float, dict[str, Any]]] = []
    for r in rows:
        rate = _num(r.get("score_rate"))
        if rate is None:
            continue
        scored.append((rate, r))
    if not scored:
        return []
    scored.sort(key=lambda x: x[0])
    below = [r for rate, r in scored if rate < soft_ceiling]
    if below:
        return below[:max_n]
    # 整体偏高时仍给出最低项作为巩固重点
    return [r for _, r in scored[:max_n]]


def _build_class_kpi_items(stats: dict[str, Any] | None) -> list[dict[str, Any]]:
    """根据班级 KPI 生成提质/分层建议。"""
    if not stats or not stats.get("count"):
        return []
    items: list[dict[str, Any]] = []
    pass_rate = _num(stats.get("pass_rate"))
    excellent_rate = _num(stats.get("excellent_rate"))
    avg = _num(stats.get("avg"))
    full = _num(stats.get("full_score")) or 100.0
    count = int(stats.get("count") or 0)

    if pass_rate is not None and pass_rate < 70:
        fail_est = max(0, round(count * (100 - pass_rate) / 100))
        items.append({
            "priority": 1,
            "title": "及格临界生过关",
            "desc": (
                f"及格率 {_fmt(pass_rate)}%"
                + (f"（约 {fail_est} 人未及格）" if fail_est else "")
                + "：锁定满分的 55%–60% 分数带学生，"
                "本周开展「基础题组 + 面批」两次，目标下周复测过关。"
            ),
            "metric": f"及格率 {_fmt(pass_rate)}%",
        })
    elif pass_rate is not None and pass_rate < 85:
        items.append({
            "priority": 2,
            "title": "稳住及格带、减少失分",
            "desc": (
                f"及格率 {_fmt(pass_rate)}%，仍需盯防反复掉队："
                "对刚过线学生每周回收错题订正单，防止回落到不及格。"
            ),
            "metric": f"及格率 {_fmt(pass_rate)}%",
        })

    if excellent_rate is not None and excellent_rate < 25:
        items.append({
            "priority": 1 if (pass_rate is None or pass_rate >= 70) else 2,
            "title": "优秀率冲刺培优",
            "desc": (
                f"优秀率 {_fmt(excellent_rate)}% 偏低：选拔均分附近及上游学生，"
                "安排每周 1 次中高档综合与压轴思维训练（40–50 分钟）。"
            ),
            "metric": f"优秀率 {_fmt(excellent_rate)}%",
        })
    elif excellent_rate is not None and pass_rate is not None and (pass_rate - excellent_rate) >= 40:
        items.append({
            "priority": 2,
            "title": "拉开优秀层厚度",
            "desc": (
                f"及格与优秀差距大（{_fmt(pass_rate)}% vs {_fmt(excellent_rate)}%）："
                "在保障过关同时，为前 30% 学生加一层限时综合卷。"
            ),
            "metric": f"优分差 {_fmt(pass_rate - excellent_rate)} pt",
        })

    segments = stats.get("segments") or []
    low_segs = [
        s for s in segments
        if isinstance(s, dict) and (_num(s.get("ratio")) or 0) >= 15
        and ("低" in str(s.get("label") or "") or "不及格" in str(s.get("label") or "")
             or "0-" in str(s.get("label") or "") or "60" in str(s.get("label") or ""))
    ]
    for s in low_segs[:2]:
        items.append({
            "priority": 1,
            "title": f"分数段：{s.get('label')}",
            "desc": (
                f"该段占比 {_fmt(s.get('ratio'))}%（{s.get('count', 0)} 人），"
                "组织分层小灶：基础通关卷 + 一对一订正清单。"
            ),
            "metric": f"{s.get('count', 0)} 人 · {_fmt(s.get('ratio'))}%",
        })

    if avg is not None and full and avg / full < 0.65 and not any(
        it["title"].startswith("及格") for it in items
    ):
        items.append({
            "priority": 1,
            "title": "提分主线：中低档得分率",
            "desc": (
                f"均分 {_fmt(avg)}/{_fmt(full)}，先抓选择题/填空通法正确率，"
                "再放开解答题；每课设置课堂过关 3 题。"
            ),
            "metric": f"均分 {_fmt(avg)}",
        })

    return items[:4]


def build_diagnosis_recommendations(
    knowledge_rows: list[dict[str, Any]] | None = None,
    item_rows: list[dict[str, Any]] | None = None,
    *,
    weak_threshold: float = 60.0,
    intervention_insights: dict[str, Any] | None = None,
    audience: str = "class",
    stats: dict[str, Any] | None = None,
) -> str:
    knowledge_rows = knowledge_rows or []
    item_rows = item_rows or []
    weak_k = identify_weak_knowledge(knowledge_rows, weak_threshold=weak_threshold)
    weak_i = identify_weak_items(item_rows, weak_threshold=weak_threshold)
    insights = intervention_insights or {}
    personal = (audience or "").lower() in {"student", "personal", "individual"}

    groups: list[tuple[str, list[dict[str, Any]], str]] = []

    if not personal:
        kpi_items = _build_class_kpi_items(stats)
        if kpi_items:
            groups.append(("班级提质目标", kpi_items, "kpi"))

        class_items: list[dict[str, Any]] = []
        for i, c in enumerate(insights.get("weak_classes") or []):
            class_items.append({
                "priority": 1 if i == 0 else 2,
                "title": str(c.get("class_name") or "该班"),
                "desc": (
                    f"对「{c.get('class_name') or '该班'}」开展专项帮扶："
                    "基础题组过关 + 临界生面批，两周内复查及格率变化。"
                ),
                "metric": f"均分 {_fmt(c.get('avg'))} · 及格率 {_fmt(c.get('pass_rate'))}%",
            })
        if class_items:
            groups.append(("班级干预", class_items, "class"))

        seg_items: list[dict[str, Any]] = []
        for s in insights.get("concern_segments") or []:
            seg_items.append({
                "priority": 1,
                "title": str(s.get("label") or "低分段"),
                "desc": "组织分层练习与错题清零，推动临界生转化；每周回收订正记录。",
                "metric": f"占比 {_fmt(s.get('ratio'))}% · {s.get('count', 0)} 人",
            })
        if seg_items:
            groups.append(("分数段辅导", seg_items, "segment"))

    know_items: list[dict[str, Any]] = []
    know_source = weak_k
    know_relative = False
    if not know_source and not personal:
        know_source = _pick_relative_rows(knowledge_rows, max_n=3, soft_ceiling=max(weak_threshold + 15, 75))
        know_relative = bool(know_source)
    for i, r in enumerate(know_source):
        rate = _num(r.get("score_rate"))
        title = str(r.get("knowledge_name") or "未知知识点")
        if personal:
            desc = _personal_knowledge_advice(title, rate)
        else:
            desc = _class_knowledge_advice(title, rate, relative=know_relative)
        know_items.append({
            "priority": 1 if (rate is not None and rate < 40) or i < 2 else 2,
            "title": title,
            "desc": desc,
            "metric": f"得分率 {_fmt(r.get('score_rate'))}%",
        })
    if know_items:
        title = (
            "薄弱知识点专项" if personal
            else ("相对巩固知识点" if know_relative else "知识点强化")
        )
        groups.append((title, know_items, "knowledge"))

    q_items: list[dict[str, Any]] = []
    item_source = weak_i
    item_relative = False
    if not item_source and not personal:
        item_source = _pick_relative_rows(item_rows, max_n=4, soft_ceiling=max(weak_threshold + 15, 75))
        item_relative = bool(item_source)
    for r in item_source:
        kn = r.get("knowledge_name")
        rate = _num(r.get("score_rate"))
        qno = r.get("question_no")
        if personal:
            desc = _personal_item_advice(qno, kn, rate)
        else:
            desc = _class_item_advice(qno, kn, rate, relative=item_relative)
        q_items.append({
            "priority": 1 if rate is not None and rate < 40 else 2,
            "title": f"第 {qno} 题",
            "desc": desc,
            "metric": f"得分率 {_fmt(r.get('score_rate'))}%",
        })
    if q_items:
        title = (
            "薄弱小题精练" if personal
            else ("相对挖分小题" if item_relative else "小题精讲")
        )
        groups.append((title, q_items, "question"))

    if not personal:
        ability_items: list[dict[str, Any]] = []
        for l in insights.get("weak_ability_levels") or []:
            label = ABILITY_LABELS.get(str(l.get("ability_level") or ""), l.get("ability_level") or "能力项")
            ability_items.append({
                "priority": 2,
                "title": label,
                "desc": (
                    f"针对「{label}」设计阶梯题组（基础→综合→应用），"
                    "课堂限时训练并抽查过程书写。"
                ),
                "metric": f"均分率 {_fmt(l.get('avg_score_rate'))}%",
            })
        if ability_items:
            groups.append(("能力层级", ability_items, "ability"))

        qtype_items: list[dict[str, Any]] = []
        insight_qtypes = list(insights.get("weak_question_types") or [])
        if insight_qtypes:
            for q in insight_qtypes:
                avg = _num(q.get("avg_score_rate"))
                qt = str(q.get("question_type") or "题型")
                if avg is not None and avg < 40:
                    desc = f"「{qt}」整体偏弱：本周安排 2 次限时专练（各 20–25 分钟），先通法再冲中档。"
                else:
                    desc = f"「{qt}」需强化：错因归类后配套限时训练，周末用综合卷复测。"
                qtype_items.append({
                    "priority": 1 if avg is not None and avg < 40 else 2,
                    "title": qt,
                    "desc": desc,
                    "metric": f"均分率 {_fmt(avg)}%",
                })
        else:
            # 班级报告常无 school intervention：直接从小题行聚合相对弱题型
            for qt, avg, n in _qtype_buckets_from_items(item_rows)[:3]:
                if avg >= max(weak_threshold + 20, 85) and len(qtype_items) >= 1:
                    continue
                if avg < 40:
                    desc = (
                        f"「{qt}」均分率仅 {_fmt(avg)}%：拆通法步骤精讲，"
                        f"本周限时专练 2 次，覆盖该题型全部易错点。"
                    )
                    pri = 1
                elif avg < weak_threshold + 10:
                    desc = (
                        f"「{qt}」相对偏低（{_fmt(avg)}%）："
                        f"精选 {min(n, 6)} 道典例限时完成，课上互批典型错因。"
                    )
                    pri = 2
                else:
                    desc = (
                        f"「{qt}」仍可挖分（{_fmt(avg)}%）："
                        "保留每周一组巩固练，防止得分率回落。"
                    )
                    pri = 3
                qtype_items.append({
                    "priority": pri,
                    "title": qt,
                    "desc": desc,
                    "metric": f"均分率 {_fmt(avg)}% · {n} 题",
                })
        if qtype_items:
            groups.append(("题型突破", qtype_items[:4], "qtype"))
    else:
        # 个人报告：按题型汇总薄弱表现，给出可执行节奏
        qtype_items = []
        for qt, avg, n in _qtype_buckets_from_items(item_rows):
            if avg >= weak_threshold:
                continue
            if avg < 40:
                desc = f"「{qt}」整体偏弱，本周安排 2 次限时题型专练（每次 20 分钟），先保基础再冲中档。"
            else:
                desc = f"「{qt}」略低于及格线，错题归类后每天练 3–5 道，周末做一套综合巩固。"
            qtype_items.append({
                "priority": 1 if avg < 40 else 2,
                "title": qt,
                "desc": desc,
                "metric": f"均分率 {_fmt(avg)}% · {n} 题",
            })
        if qtype_items:
            groups.append(("题型突破计划", qtype_items[:4], "qtype"))

    if not groups:
        keep = (
            "各知识点掌握较稳，建议每周保留 1 套综合卷维持题感，并定期回看错题本。"
            if personal
            else (
                "本班整体达标。请结合分数段盯防临界生，"
                "每周保留 1 次综合过关练 + 1 次错题回流，避免回落。"
            )
        )
        return (
            '<div class="edu-rec edu-rec-empty">'
            '<div class="edu-rec-card edu-rec-priority-3">'
            '<div class="edu-rec-body">'
            '<div class="edu-rec-title">巩固与防回落</div>'
            f'<div class="edu-rec-desc">{keep}</div>'
            "</div></div></div>"
        )

    intro = (
        '<div class="edu-rec-intro">'
        + (
            "以下建议按薄弱程度排序，优先完成 P1 项；每项建议尽量在本周内落地并复测。"
            if personal
            else "建议按 P1→P2 落地：先过关/提质目标，再盯相对薄弱知识点、小题与题型；每项尽量本周执行并下周复测。"
        )
        + "</div>"
    )
    body = "".join(_rec_group(title, items, cat=cat) for title, items, cat in groups)
    return f'<div class="edu-rec">{intro}{body}</div>'


def build_segment_table_html(
    segments: list[dict[str, Any]],
    *,
    full_score: float | None = None,
) -> str:
    if not segments:
        return "<p class='edu-sub'>暂无分数段数据</p>"
    normalized = normalize_segments(segments, full_score=full_score)
    rows = "".join(
        f"<tr><td>{s.get('label', '')}</td><td class='num'>{s.get('count', 0)}</td>"
        f"<td class='num'>{_fmt(s.get('ratio'))}%</td></tr>"
        for s in normalized
    )
    return _wrap_table_html(
        "<table class='edu-table'><thead><tr>"
        "<th>分数段</th><th class='num'>人数</th><th class='num'>占比</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
    )


def _coerce_table_field(value: Any, builder: Any) -> Any:
    if isinstance(value, str) and _is_html_fragment(value):
        return value
    rows = _coerce_row_list(value)
    if rows is not None:
        return builder(rows)
    return value


def coerce_report_table_fields(data: dict[str, Any]) -> dict[str, Any]:
    """渲染前兜底：LLM 误将 list/JSON 填入 *_TABLE 占位符时自动转 HTML 表格。"""
    if not data:
        return {}
    out = dict(data)
    if "ITEM_TABLE" in out:
        out["ITEM_TABLE"] = _coerce_table_field(out["ITEM_TABLE"], build_item_table_html)
    if "KNOWLEDGE_TABLE" in out:
        val = out["KNOWLEDGE_TABLE"]
        if not (isinstance(val, str) and _is_html_fragment(val)):
            kn = _coerce_row_list(val)
            if kn is not None:
                out["KNOWLEDGE_TABLE"] = build_knowledge_table_html(enrich_knowledge_rows(kn))
    if "SEGMENT_TABLE" in out:
        seg_val = out["SEGMENT_TABLE"]
        if not (isinstance(seg_val, str) and _is_html_fragment(seg_val)):
            seg_rows = _coerce_row_list(seg_val)
            if seg_rows is not None:
                full_score = out.get("full_score")
                try:
                    full_score = float(full_score) if full_score is not None else None
                except (TypeError, ValueError):
                    full_score = None
                out["SEGMENT_TABLE"] = build_segment_table_html(seg_rows, full_score=full_score)
            else:
                out["SEGMENT_TABLE"] = _coerce_table_field(seg_val, build_segment_table_html)
        else:
            out["SEGMENT_TABLE"] = seg_val
    weak = out.get("WEAK_KNOWLEDGE_LIST")
    if isinstance(weak, list):
        out["WEAK_KNOWLEDGE_LIST"] = "、".join(str(x.get("knowledge_name") if isinstance(x, dict) else x) for x in weak)[:500]
    return out


def enrich_knowledge_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """为知识点行补充掌握水平标签。"""
    out: list[dict[str, Any]] = []
    for r in rows:
        row = dict(r)
        rate = _num(row.get("score_rate"))
        row["level"] = _knowledge_level(rate)
        out.append(row)
    return out


__all__ = [
    "build_diagnosis_recommendations",
    "build_diagnosis_summary",
    "build_item_table_html",
    "build_knowledge_table_html",
    "build_segment_table_html",
    "coerce_report_table_fields",
    "enrich_knowledge_rows",
    "identify_weak_items",
    "identify_weak_knowledge",
    "normalize_item_row",
]
