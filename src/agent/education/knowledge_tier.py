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
    from src.agent.education.subject_diagnosis import collect_class_names

    if len(collect_class_names(rows)) >= 2:
        return build_question_type_compare_table_html(rows)
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


def build_question_type_compare_table_html(item_rows: list[dict[str, Any]] | None = None) -> str:
    """班级横向对比：各班题型平均得分率。"""
    from collections import defaultdict

    from src.agent.education.subject_diagnosis import collect_class_names

    rows = list(item_rows or [])
    classes = collect_class_names(rows)
    if len(classes) < 2:
        return build_question_type_table_html(
            [{k: v for k, v in r.items() if k not in ("class_name", "class")} for r in rows]
        )
    # (question_type, class) → rates；题数按题型下去重 question_no
    rate_buckets: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    qnos: dict[str, set[Any]] = defaultdict(set)
    for r in rows:
        qt = str(r.get("question_type") or "").strip() or "未知"
        cls = str(r.get("class_name") or r.get("class") or "").strip()
        if not cls:
            continue
        try:
            rate = float(r["score_rate"]) if r.get("score_rate") is not None else None
        except (TypeError, ValueError):
            rate = None
        if rate is None:
            continue
        rate_buckets[qt][cls].append(rate)
        if r.get("question_no") is not None:
            qnos[qt].add(r.get("question_no"))
    if not rate_buckets:
        return ""
    class_heads = "".join(f"<th class='num'>{c}得分率</th>" for c in classes)
    body_parts: list[str] = []
    for qt in sorted(rate_buckets.keys()):
        qcount = len(qnos.get(qt) or ())
        if not qcount:
            qcount = max((len(rate_buckets[qt][c]) for c in classes), default=0)
        cells = f"<tr><td>{qt}</td><td class='num'>{qcount}</td>"
        for c in classes:
            rates = rate_buckets[qt].get(c) or []
            cells += (
                f"<td class='num'>{_fmt(sum(rates) / len(rates))}%</td>"
                if rates
                else "<td class='num'>-</td>"
            )
        body_parts.append(cells + "</tr>")
    table = (
        "<table class='edu-table edu-table--qtype-compare'><thead><tr>"
        f"<th>题型</th><th class='num'>题数</th>{class_heads}"
        f"</tr></thead><tbody>{''.join(body_parts)}</tbody></table>"
    )
    return f'<div class="edu-table-wrap">{table}</div>'


def build_question_type_compare_chart_payload(
    item_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """供 group_compare_bar：各组=题型，各系列=班级得分率。"""
    from collections import defaultdict

    from src.agent.education.subject_diagnosis import collect_class_names

    rows = list(item_rows or [])
    classes = collect_class_names(rows)
    if len(classes) < 2:
        return None
    buckets: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        qt = str(r.get("question_type") or "").strip()
        cls = str(r.get("class_name") or r.get("class") or "").strip()
        if not qt or not cls:
            continue
        try:
            rate = float(r["score_rate"]) if r.get("score_rate") is not None else None
        except (TypeError, ValueError):
            rate = None
        if rate is None:
            continue
        buckets[qt][cls].append(rate)
    if not buckets:
        return None
    cats = sorted(buckets.keys())
    metrics = []
    for c in classes:
        vals = []
        for qt in cats:
            rates = buckets[qt].get(c) or []
            vals.append(round(sum(rates) / len(rates), 2) if rates else 0)
        metrics.append({"name": c, "values": vals})
    return {
        "groups": cats,
        "metrics": metrics,
        "y_name": "得分率(%)",
        "y_max": 100,
    }


def _qtype_averages(item_rows: list[dict[str, Any]]) -> list[tuple[str, float, int]]:
    from collections import defaultdict

    buckets: dict[str, list[float]] = defaultdict(list)
    for r in item_rows:
        qt = str(r.get("question_type") or "").strip()
        if not qt:
            continue
        try:
            rate = float(r["score_rate"]) if r.get("score_rate") is not None else None
        except (TypeError, ValueError):
            rate = None
        if rate is None:
            continue
        buckets[qt].append(rate)
    return [
        (qt, sum(rates) / len(rates), len(rates))
        for qt, rates in buckets.items()
        if rates
    ]


def _knowledge_sorted(knowledge_rows: list[dict[str, Any]]) -> list[tuple[str, float]]:
    out: list[tuple[str, float]] = []
    for r in knowledge_rows:
        name = str(r.get("knowledge_name") or "").strip()
        if not name:
            continue
        try:
            rate = float(r["score_rate"]) if r.get("score_rate") is not None else None
        except (TypeError, ValueError):
            rate = None
        if rate is None:
            continue
        out.append((name, rate))
    out.sort(key=lambda x: x[1])
    return out


def build_ability_tier_insight(
    summary: dict[str, Any] | None = None,
    *,
    knowledge_rows: list[dict[str, Any]] | None = None,
    item_rows: list[dict[str, Any]] | None = None,
    weak_threshold: float = 60.0,
) -> str:
    """生成能力画像文字：必须点出优点与问题，禁止空泛「整体平稳」。"""
    summary = summary or {}
    knowledge_rows = list(knowledge_rows or [])
    item_rows = list(item_rows or [])
    parts: list[str] = []

    # 1) 能力层级（有真实分级时才写；忽略 unknown 空壳）
    level_rows = [
        s for s in (summary.get("by_ability_level") or [])
        if str(s.get("ability_level") or "") not in ("", "unknown", "None")
        and s.get("avg_score_rate") is not None
    ]
    if level_rows:
        weak_lv = sorted(
            [s for s in level_rows if float(s["avg_score_rate"]) < weak_threshold],
            key=lambda s: float(s["avg_score_rate"]),
        )
        strong_lv = sorted(
            [s for s in level_rows if float(s["avg_score_rate"]) >= weak_threshold],
            key=lambda s: float(s["avg_score_rate"]),
            reverse=True,
        )
        if strong_lv:
            top = strong_lv[0]
            parts.append(
                f"能力层级上，「{_label(str(top.get('ability_level')))}」相对扎实"
                f"（均分率 {_fmt(top.get('avg_score_rate'))}%）"
            )
        if weak_lv:
            bits = [
                f"「{_label(str(s.get('ability_level')))}」仅 {_fmt(s.get('avg_score_rate'))}%"
                for s in weak_lv[:3]
            ]
            parts.append(
                "短板清晰：" + "、".join(bits) + "，必须优先突破，不能再按「均衡」自我安慰"
            )
        elif len(level_rows) >= 2:
            rates = [float(s["avg_score_rate"]) for s in level_rows]
            gap = max(rates) - min(rates)
            if gap >= 12:
                lo = min(level_rows, key=lambda s: float(s["avg_score_rate"]))
                hi = max(level_rows, key=lambda s: float(s["avg_score_rate"]))
                parts.append(
                    f"层级间落差达 {_fmt(gap)} 个百分点："
                    f"「{_label(str(hi.get('ability_level')))}」{_fmt(hi.get('avg_score_rate'))}% "
                    f"明显强于「{_label(str(lo.get('ability_level')))}」"
                    f"{_fmt(lo.get('avg_score_rate'))}%，优势不均衡"
                )

    # 2) 题型表现——截图场景的主信号
    qtypes = _qtype_averages(item_rows)
    if qtypes:
        qtypes_sorted = sorted(qtypes, key=lambda x: x[1])
        weak_qt = [x for x in qtypes_sorted if x[1] < weak_threshold]
        strong_qt = [x for x in sorted(qtypes, key=lambda x: -x[1]) if x[1] >= weak_threshold]
        if len(qtypes_sorted) >= 2:
            lo_name, lo_rate, _ = qtypes_sorted[0]
            hi_name, hi_rate, _ = qtypes_sorted[-1]
            gap = hi_rate - lo_rate
            if gap >= 8:
                parts.append(
                    f"题型分化明显：强项「{hi_name}」{_fmt(hi_rate)}%，"
                    f"弱项「{lo_name}」仅 {_fmt(lo_rate)}%，落差 {_fmt(gap)} 个百分点——"
                    f"说明并非「全面平稳」，而是局部能力拖后腿"
                )
        if strong_qt and not any("强项" in p or "相对扎实" in p for p in parts):
            s_bits = [f"「{n}」{_fmt(r)}%" for n, r, _ in strong_qt[:2]]
            parts.append("优势题型：" + "、".join(s_bits) + "，可作保分盘")
        if weak_qt:
            w_bits = [f"「{n}」{_fmt(r)}%（{c}题）" for n, r, c in weak_qt[:3]]
            parts.append(
                "问题题型：" + "、".join(w_bits)
                + "——客观题/基础题失分往往比大题更伤总分，必须专项纠错"
            )
        elif qtypes_sorted and qtypes_sorted[0][1] < weak_threshold + 5:
            n, r, c = qtypes_sorted[0]
            parts.append(
                f"相对最弱仍是「{n}」（{_fmt(r)}%，{c}题），虽未全面崩盘，但已接近警戒线，不宜掉以轻心"
            )

    # 3) 知识点——点名最差与最好
    kn = _knowledge_sorted(knowledge_rows)
    if kn:
        weak_kn = [x for x in kn if x[1] < weak_threshold]
        strong_kn = [x for x in reversed(kn) if x[1] >= max(weak_threshold, 80)][:2]
        if strong_kn:
            parts.append(
                "知识点亮点："
                + "、".join(f"「{n}」{_fmt(r)}%" for n, r in strong_kn)
                + "，说明并非基础全面塌方"
            )
        if weak_kn:
            worst = weak_kn[:3]
            names = "、".join(f"「{n}」仅 {_fmt(r)}%" for n, r in worst)
            verdict = (
                "属于严重失分点，必须本周清零"
                if worst[0][1] < 40
                else "已跌破及格线，拖累整卷表现"
            )
            parts.append(f"知识点硬伤：{names}，{verdict}")
        elif kn and kn[0][1] < 75:
            n, r = kn[0]
            parts.append(f"相对薄弱知识点是「{n}」（{_fmt(r)}%），建议作为下一轮专练入口")

    if not parts:
        # 真无数据时才保守表述；仍避免假「平稳」
        return (
            "<p>暂无足够的能力层级/题型/知识点明细，无法给出精准画像；"
            "请核对小题与知识点数据后再诊断。</p>"
        )

    # 组装为可读段落
    lead = parts[0]
    rest = parts[1:]
    html = [f"<p><strong>诊断结论：</strong>{lead}。</p>"]
    if rest:
        html.append("<ul class='edu-insight-list'>")
        for p in rest:
            html.append(f"<li>{p}。</li>")
        html.append("</ul>")
    return "".join(html)


__all__ = [
    "ABILITY_LABELS",
    "build_ability_tier_insight",
    "build_ability_tier_matrix",
    "build_ability_tier_summary",
    "build_ability_tier_table_html",
    "build_question_type_compare_chart_payload",
    "build_question_type_compare_table_html",
    "build_question_type_table_html",
]
