"""知识点能力层级分析。"""

from __future__ import annotations

from typing import Any

from src.agent.education.stats import compute_knowledge_mastery

ABILITY_LABELS: dict[str, str] = {
    "basic": "基础知识",
    "applied": "综合应用",
    "advanced": "高阶能力",
    "unknown": "未分级",
}


def _label(level: str) -> str:
    return ABILITY_LABELS.get(level, level or "未分级")


def _fmt(v: Any) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.2f}"
    return str(v)


def build_ability_tier_summary(
    knowledge_rows: list[dict[str, Any]] | None = None,
    *,
    weak_threshold: float = 60.0,
) -> dict[str, Any]:
    """按 ability_level 聚合得分率，识别各层级强弱项。"""
    mastery = compute_knowledge_mastery(
        list(knowledge_rows or []),
        weak_threshold=weak_threshold,
    )
    weak_levels = [
        s for s in mastery.get("by_ability_level") or []
        if s.get("weak")
    ]
    strong_levels = [
        s for s in mastery.get("by_ability_level") or []
        if s.get("avg_score_rate") is not None and not s.get("weak")
    ]
    return {
        "by_ability_level": mastery.get("by_ability_level") or [],
        "weak_levels": weak_levels,
        "strong_levels": strong_levels,
    }


def build_ability_tier_matrix(
    knowledge_rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """知识点列表含能力层级与掌握度。"""
    rows = list(knowledge_rows or [])
    out: list[dict[str, Any]] = []
    for r in rows:
        level = str(r.get("ability_level") or "unknown")
        rate = r.get("score_rate")
        out.append({
            "knowledge_name": r.get("knowledge_name"),
            "ability_level": level,
            "ability_label": _label(level),
            "score_rate": rate,
            "weak": rate is not None and float(rate) < 60,
        })
    return out


def build_ability_tier_table_html(rows: list[dict[str, Any]] | None = None) -> str:
    matrix = build_ability_tier_matrix(rows)
    if not matrix or not any(r.get("ability_level") not in ("", "unknown", None) for r in matrix):
        return ""
    body = "".join(
        f"<tr><td>{r.get('ability_label')}</td>"
        f"<td>{r.get('knowledge_name') or '-'}</td>"
        f"<td>{_fmt(r.get('score_rate'))}%</td>"
        f"<td>{'需加强' if r.get('weak') else '达标'}</td></tr>"
        for r in matrix
    )
    return (
        "<table class='edu-table'><thead><tr>"
        "<th>能力层级</th><th>知识点</th><th>得分率</th><th>状态</th></tr></thead>"
        f"<tbody>{body}</tbody></table>"
    )


def build_question_type_table_html(item_rows: list[dict[str, Any]] | None = None) -> str:
    rows = list(item_rows or [])
    if not rows or not any(r.get("question_type") for r in rows):
        return ""
    from collections import defaultdict

    buckets: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        qt = str(r.get("question_type") or "未知")
        rate = r.get("score_rate")
        if rate is not None:
            buckets[qt].append(float(rate))
    if not buckets:
        return ""
    body = "".join(
        f"<tr><td>{qt}</td><td>{len(rates)}</td>"
        f"<td>{_fmt(sum(rates) / len(rates))}%</td></tr>"
        for qt, rates in sorted(buckets.items())
    )
    return (
        "<table class='edu-table'><thead><tr>"
        "<th>题型</th><th>题数</th><th>平均得分率</th></tr></thead>"
        f"<tbody>{body}</tbody></table>"
    )


def build_ability_tier_insight(summary: dict[str, Any]) -> str:
    weak = summary.get("weak_levels") or []
    if not weak:
        return "<p>各能力层级掌握情况整体平稳。</p>"
    parts = []
    for s in weak[:3]:
        label = _label(str(s.get("ability_level") or ""))
        rate = _fmt(s.get("avg_score_rate"))
        parts.append(f"「{label}」层级平均得分率 {rate}%，需重点突破")
    return f"<p>{'；'.join(parts)}。</p>"


__all__ = [
    "ABILITY_LABELS",
    "build_ability_tier_insight",
    "build_ability_tier_matrix",
    "build_ability_tier_summary",
    "build_ability_tier_table_html",
    "build_question_type_table_html",
]
