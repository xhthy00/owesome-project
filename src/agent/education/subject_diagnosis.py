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


def build_diagnosis_recommendations(
    knowledge_rows: list[dict[str, Any]] | None = None,
    item_rows: list[dict[str, Any]] | None = None,
    *,
    weak_threshold: float = 60.0,
    intervention_insights: dict[str, Any] | None = None,
) -> str:
    knowledge_rows = knowledge_rows or []
    item_rows = item_rows or []
    weak_k = identify_weak_knowledge(knowledge_rows, weak_threshold=weak_threshold)
    weak_i = identify_weak_items(item_rows, weak_threshold=weak_threshold)
    insights = intervention_insights or {}

    groups: list[tuple[str, str, list[dict[str, Any]]]] = []

    class_items: list[dict[str, Any]] = []
    for i, c in enumerate(insights.get("weak_classes") or []):
        class_items.append({
            "priority": 1 if i == 0 else 2,
            "title": str(c.get("class_name") or "该班"),
            "desc": "开展专项帮扶：巩固基础题、盯紧及格率与课堂过关",
            "metric": f"均分 {_fmt(c.get('avg'))} · 及格率 {_fmt(c.get('pass_rate'))}%",
        })
    if class_items:
        groups.append(("班级干预", class_items, "class"))

    seg_items: list[dict[str, Any]] = []
    for s in insights.get("concern_segments") or []:
        seg_items.append({
            "priority": 1,
            "title": str(s.get("label") or "低分段"),
            "desc": "组织分层练习与错题清零，推动临界生转化",
            "metric": f"占比 {_fmt(s.get('ratio'))}% · {s.get('count', 0)} 人",
        })
    if seg_items:
        groups.append(("分数段辅导", seg_items, "segment"))

    know_items: list[dict[str, Any]] = []
    for i, r in enumerate(weak_k):
        know_items.append({
            "priority": 1 if i < 2 else 2,
            "title": str(r.get("knowledge_name") or "未知知识点"),
            "desc": "安排专项练习、错题回顾与专题课",
            "metric": f"得分率 {_fmt(r.get('score_rate'))}%",
        })
    if know_items:
        groups.append(("知识点强化", know_items, "knowledge"))

    q_items: list[dict[str, Any]] = []
    for r in weak_i:
        kn = r.get("knowledge_name")
        q_items.append({
            "priority": 2,
            "title": f"第 {r.get('question_no')} 题",
            "desc": f"精讲典型错因并变式训练" + (f"（关联：{kn}）" if kn else ""),
            "metric": f"得分率 {_fmt(r.get('score_rate'))}%",
        })
    if q_items:
        groups.append(("小题精讲", q_items, "question"))

    ability_items: list[dict[str, Any]] = []
    for l in insights.get("weak_ability_levels") or []:
        label = ABILITY_LABELS.get(str(l.get("ability_level") or ""), l.get("ability_level") or "能力项")
        ability_items.append({
            "priority": 2,
            "title": label,
            "desc": "加强该能力层级训练，提升综合与应用题得分率",
            "metric": f"均分率 {_fmt(l.get('avg_score_rate'))}%",
        })
    if ability_items:
        groups.append(("能力层级", ability_items, "ability"))

    qtype_items: list[dict[str, Any]] = []
    for q in insights.get("weak_question_types") or []:
        qtype_items.append({
            "priority": 2,
            "title": str(q.get("question_type") or "题型"),
            "desc": "精讲典型错因并配套限时训练",
            "metric": f"均分率 {_fmt(q.get('avg_score_rate'))}%",
        })
    if qtype_items:
        groups.append(("题型突破", qtype_items, "qtype"))

    if not groups:
        return (
            '<div class="edu-rec edu-rec-empty">'
            '<div class="edu-rec-card edu-rec-priority-3">'
            '<div class="edu-rec-body">'
            '<div class="edu-rec-title">保持现有节奏</div>'
            '<div class="edu-rec-desc">整体掌握较好，建议以综合卷维持题感，并关注个别临界生的巩固。</div>'
            "</div></div></div>"
        )

    body = "".join(_rec_group(title, items, cat=cat) for title, items, cat in groups)
    return f'<div class="edu-rec">{body}</div>'


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
