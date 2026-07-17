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


def _row_class_name(row: dict[str, Any]) -> str:
    return str(row.get("class_name") or row.get("class") or "").strip()


def collect_class_names(rows: list[dict[str, Any]]) -> list[str]:
    """按首次出现顺序收集班级名。"""
    names: list[str] = []
    seen: set[str] = set()
    for r in rows:
        name = _row_class_name(r)
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return names


_UNLINKED_KNOWLEDGE = "未关联知识点"


def _knowledge_label(name: Any) -> str:
    """规范知识点展示串：多知识点按顿号拆分后排序去重，与 SQL string_agg ORDER BY 一致。"""
    s = str(name or "").strip()
    if not s:
        return _UNLINKED_KNOWLEDGE
    parts = [p.strip() for p in s.replace(",", "、").split("、") if p.strip()]
    if not parts:
        return _UNLINKED_KNOWLEDGE
    if len(parts) == 1:
        return parts[0]
    return "、".join(sorted(set(parts)))


def normalize_link_weights(weights: list[float]) -> list[float]:
    """题内权重归一化：w_norm = weight / SUM(weight)。非正总和返回空列表。"""
    positive = [float(w) for w in weights if w is not None and float(w) > 0]
    total = sum(positive)
    if total <= 0:
        return []
    return [w / total for w in positive]


def weighted_score_contributions(
    score: float,
    full_score: float,
    weights: list[float],
) -> list[tuple[float, float]]:
    """按归一化权重拆分题得分/满分，返回 [(score_contrib, full_contrib), ...]。"""
    norms = normalize_link_weights(weights)
    return [(score * w, full_score * w) for w in norms]


def knowledge_names_subquery_join(db_type: str = "pg") -> str:
    """小题查询：按 question_id 聚合知识点名串，避免一题多知识点裂行。"""
    if db_type == "mysql":
        agg = (
            "GROUP_CONCAT(DISTINCT k.knowledge_name "
            "ORDER BY k.knowledge_name SEPARATOR '、')"
        )
    else:
        agg = "string_agg(DISTINCT k.knowledge_name, '、' ORDER BY k.knowledge_name)"
    return (
        "LEFT JOIN (\n"
        "  SELECT eqk.question_id,\n"
        f"         {agg} AS knowledge_name\n"
        "  FROM tb_exam_question_knowledge eqk\n"
        "  JOIN tb_knowledge k ON k.id = eqk.knowledge_id\n"
        "  GROUP BY eqk.question_id\n"
        ") kn ON kn.question_id = eq.id\n"
    )


def knowledge_weighted_join() -> str:
    """知识点掌握查询：关联表 + 题内 weight 归一化为 w_norm。"""
    return (
        "LEFT JOIN (\n"
        "  SELECT question_id, knowledge_id,\n"
        "         weight / NULLIF(SUM(weight) OVER (PARTITION BY question_id), 0) AS w_norm\n"
        "  FROM tb_exam_question_knowledge\n"
        ") eqk ON eqk.question_id = eq.id\n"
        "LEFT JOIN tb_knowledge k ON k.id = eqk.knowledge_id\n"
    )


def resolve_question_knowledge_map(item_rows: list[dict[str, Any]]) -> dict[Any, str]:
    """题号 → 全场统一知识点展示串（仅展示，不负责权重计分）。

    各班同一题号若关联串不一致，取多数票（优先非「未关联知识点」）。
    """
    from collections import Counter, defaultdict

    votes: dict[Any, Counter[str]] = defaultdict(Counter)
    for r in item_rows:
        qno = r.get("question_no")
        if qno is None:
            continue
        votes[qno][_knowledge_label(r.get("knowledge_name"))] += 1
    out: dict[Any, str] = {}
    for qno, counter in votes.items():
        linked = [(n, c) for n, c in counter.items() if n != _UNLINKED_KNOWLEDGE]
        pool = linked if linked else list(counter.items())
        pool.sort(key=lambda x: (-x[1], x[0]))
        out[qno] = pool[0][0]
    return out


def apply_canonical_knowledge_to_item_rows(
    item_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """将各班小题行的知识点统一为「按题号」的规范名称串。"""
    mapping = resolve_question_knowledge_map(item_rows)
    if not mapping:
        return [dict(r) for r in item_rows]
    out: list[dict[str, Any]] = []
    for r in item_rows:
        nr = dict(r)
        qno = nr.get("question_no")
        if qno in mapping:
            nr["knowledge_name"] = mapping[qno]
        out.append(nr)
    return out


def build_knowledge_class_rows_from_items(
    item_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """由小题各班行聚合知识点对比行（仅展示兜底，不可用于权重拆分计分）。

    诊断主路径应使用带 weight 归一化的知识点 SQL；本函数不拆分权重。
    """
    from collections import defaultdict

    rows = [
        normalize_item_row(r)
        for r in apply_canonical_knowledge_to_item_rows(item_rows)
    ]
    classes = collect_class_names(rows)
    buckets: dict[tuple[str, str], list[tuple[float | None, float | None, float | None]]] = (
        defaultdict(list)
    )
    qnos_by_kn: dict[str, set[Any]] = defaultdict(set)
    for r in rows:
        cls = _row_class_name(r)
        kn = _knowledge_label(r.get("knowledge_name"))
        if not cls:
            continue
        qno = r.get("question_no")
        if qno is not None:
            qnos_by_kn[kn].add(qno)
        buckets[(cls, kn)].append(
            (_num(r.get("avg_score")), _num(r.get("full_score")), _num(r.get("score_rate")))
        )
    kn_order = sorted(qnos_by_kn.keys(), key=lambda k: (k == _UNLINKED_KNOWLEDGE, k))
    result: list[dict[str, Any]] = []
    for kn in kn_order:
        qc = len(qnos_by_kn[kn])
        for cls in classes:
            vals = buckets.get((cls, kn))
            if not vals:
                continue
            rate: float | None = None
            pairs = [(a, f) for a, f, _ in vals if a is not None and f is not None and f > 0]
            if pairs:
                sa = sum(a for a, _ in pairs)
                sf = sum(f for _, f in pairs)
                if sf > 0:
                    rate = round(sa / sf * 100, 2)
            if rate is None:
                rates_only = [rr for _, _, rr in vals if rr is not None]
                if rates_only:
                    rate = round(sum(rates_only) / len(rates_only), 2)
            if rate is None:
                continue
            result.append(
                {
                    "class_name": cls,
                    "knowledge_name": kn,
                    "question_count": qc,
                    "score_rate": rate,
                }
            )
    return result


def build_item_table_html(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "<p class='edu-sub'>暂无小题数据</p>"
    # 含多班 class_name 时走横向对比表（各班均分）
    if len(collect_class_names(rows)) >= 2:
        return build_item_compare_table_html(rows)
    norm = [normalize_item_row(r) for r in rows]
    score_label = "得分" if any("score" in r for r in rows) else "均分"
    extra_cols = any(
        r.get(k) is not None
        for r in norm
        for k in ("question_type", "difficulty", "discrimination")
    )
    extra_head = ""
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


def build_item_compare_table_html(rows: list[dict[str, Any]]) -> str:
    """班级横向对比：各班每题均分（不含得分率、区分度）。"""
    if not rows:
        return "<p class='edu-sub'>暂无小题数据</p>"
    classes = collect_class_names(rows)
    if not classes:
        return build_item_table_html(
            [{k: v for k, v in r.items() if k not in ("class_name", "class")} for r in rows]
        )
    kn_map = resolve_question_knowledge_map(rows)
    norm = [normalize_item_row(r) for r in rows]
    # question_no → meta + class → avg
    order: list[Any] = []
    meta: dict[Any, dict[str, Any]] = {}
    scores: dict[Any, dict[str, float | None]] = {}
    for r in norm:
        qno = r.get("question_no")
        if qno not in meta:
            order.append(qno)
            meta[qno] = {
                "knowledge_name": kn_map.get(qno) or r.get("knowledge_name") or "-",
                "full_score": r.get("full_score"),
                "question_type": r.get("question_type") or "-",
            }
            scores[qno] = {}
        cls = _row_class_name(r)
        if cls:
            scores[qno][cls] = _num(r.get("avg_score"))
    has_qtype = any(str(meta[q].get("question_type") or "") not in ("", "-") for q in order)
    class_heads = "".join(f"<th class='num'>{c}均分</th>" for c in classes)
    qtype_head = "<th>题型</th>" if has_qtype else ""
    body_parts: list[str] = []
    for qno in order:
        m = meta[qno]
        cells = (
            f"<tr><td>{qno}</td>"
            f"<td class='edu-cell-text'>{m.get('knowledge_name') or '-'}</td>"
            f"<td class='num'>{_fmt(m.get('full_score'))}</td>"
        )
        if has_qtype:
            cells += f"<td>{m.get('question_type') or '-'}</td>"
        for c in classes:
            cells += f"<td class='num'>{_fmt(scores[qno].get(c))}</td>"
        body_parts.append(cells + "</tr>")
    return _wrap_table_html(
        "<table class='edu-table edu-table--item-compare'><thead><tr>"
        f"<th>题号</th><th>知识点</th><th class='num'>满分</th>{qtype_head}{class_heads}"
        f"</tr></thead><tbody>{''.join(body_parts)}</tbody></table>"
    )


def build_knowledge_table_html(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "<p class='edu-sub'>暂无知识点数据</p>"
    if len(collect_class_names(rows)) >= 2:
        return build_knowledge_compare_table_html(rows)
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


def build_knowledge_compare_table_html(rows: list[dict[str, Any]]) -> str:
    """班级横向对比：各班知识点得分率。"""
    if not rows:
        return "<p class='edu-sub'>暂无知识点数据</p>"
    classes = collect_class_names(rows)
    if not classes:
        return build_knowledge_table_html(
            [{k: v for k, v in r.items() if k not in ("class_name", "class")} for r in rows]
        )
    norm = [normalize_knowledge_row(r) for r in rows]
    order: list[str] = []
    meta: dict[str, Any] = {}
    rates: dict[str, dict[str, float | None]] = {}
    for r in norm:
        name = str(r.get("knowledge_name") or "").strip() or "未关联知识点"
        if name not in meta:
            order.append(name)
            meta[name] = r.get("question_count", r.get("question_nos", "-"))
            rates[name] = {}
        cls = _row_class_name(r)
        if cls:
            rates[name][cls] = _num(r.get("score_rate"))
            # 涉及题数取各班最大值（题目集合一致时相同）
            qc = r.get("question_count", r.get("question_nos"))
            try:
                qc_n = int(qc) if qc is not None else None
            except (TypeError, ValueError):
                qc_n = None
            if qc_n is not None:
                try:
                    cur = int(meta[name]) if meta[name] not in (None, "-") else 0
                except (TypeError, ValueError):
                    cur = 0
                meta[name] = max(cur, qc_n)
    class_heads = "".join(f"<th class='num'>{c}得分率</th>" for c in classes)
    body_parts: list[str] = []
    for name in order:
        cells = (
            f"<tr><td class='edu-cell-text'>{name}</td>"
            f"<td class='num'>{meta.get(name, '-')}</td>"
        )
        for c in classes:
            rate = rates[name].get(c)
            cells += f"<td class='num'>{_fmt(rate)}%</td>" if rate is not None else "<td class='num'>-</td>"
        body_parts.append(cells + "</tr>")
    return _wrap_table_html(
        "<table class='edu-table edu-table--knowledge-compare'><thead><tr>"
        f"<th>知识点</th><th class='num'>涉及题数</th>{class_heads}"
        f"</tr></thead><tbody>{''.join(body_parts)}</tbody></table>"
    )


def build_knowledge_compare_chart_payload(
    rows: list[dict[str, Any]],
    *,
    max_items: int = 12,
) -> dict[str, Any] | None:
    """供 group_compare_bar：各组=知识点，各系列=班级得分率。"""
    classes = collect_class_names(rows)
    if len(classes) < 2:
        return None
    # 按全校均分率升序取前 max_items（薄弱优先）
    by_kn: dict[str, list[float]] = {}
    rate_map: dict[str, dict[str, float]] = {}
    for r in rows:
        name = str(r.get("knowledge_name") or "").strip() or "未关联知识点"
        cls = _row_class_name(r)
        rate = _num(r.get("score_rate"))
        if not cls or rate is None:
            continue
        rate_map.setdefault(name, {})[cls] = rate
        by_kn.setdefault(name, []).append(rate)
    if not by_kn:
        return None
    ranked = sorted(by_kn.keys(), key=lambda k: sum(by_kn[k]) / len(by_kn[k]))[:max_items]
    metrics = []
    for c in classes:
        metrics.append({
            "name": c,
            "values": [round(rate_map.get(kn, {}).get(c, 0), 2) for kn in ranked],
        })
    return {
        "groups": ranked,
        "metrics": metrics,
        "y_name": "得分率(%)",
        "y_max": 100,
    }


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


def _unique_student_and_exam_counts(
    score_rows: list[dict[str, Any]] | None,
) -> tuple[int, int]:
    """从 score_rows 估计去重学生数与考试场次数。"""
    students: set[str] = set()
    exams: set[str] = set()
    for r in score_rows or []:
        if not isinstance(r, dict):
            continue
        for k in ("student_id", "student_name", "student", "name", "学号"):
            v = r.get(k)
            if v is not None and str(v).strip():
                students.add(str(v).strip())
                break
        for k in ("exam_name", "exam_id", "exam"):
            v = r.get(k)
            if v is not None and str(v).strip():
                exams.add(str(v).strip())
                break
    return len(students), len(exams)


def build_diagnosis_summary(
    *,
    school_name: str = "",
    exam_name: str = "",
    subject_name: str = "",
    stats: dict[str, Any] | None = None,
    item_rows: list[dict[str, Any]] | None = None,
    knowledge_rows: list[dict[str, Any]] | None = None,
    score_rows: list[dict[str, Any]] | None = None,
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
        raw_count = int(stats.get("count") or 0)
        uniq_students, exam_n = _unique_student_and_exam_counts(score_rows)
        # 多场考试混算时 len(score_rows) 是人次；参考人数改为去重学生数
        show_count = raw_count
        multi_exam_note = ""
        if exam_n >= 2 and uniq_students > 0 and uniq_students < raw_count:
            show_count = uniq_students
            multi_exam_note = (
                f"<p class='edu-diag-muted'>跨 {exam_n} 场考试共 {raw_count} 条成绩；"
                f"参考人数按去重学生计为 {uniq_students}。"
                "多场考试对比请改用综合分析报告。</p>"
            )
        parts.append('<div class="edu-diag-overview">')
        parts.append(_diag_stat_card("参考人数", str(show_count), tone="primary"))
        parts.append(_diag_stat_card("平均分", _fmt(stats.get("avg")), tone="primary"))
        parts.append(_diag_stat_card("及格率", f"{_fmt(stats.get('pass_rate'))}%", tone=pass_tone))
        parts.append(_diag_stat_card("优秀率", f"{_fmt(stats.get('excellent_rate'))}%", tone="neutral"))
        parts.append("</div>")
        if multi_exam_note:
            parts.append(multi_exam_note)

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


def build_class_overview_summary(
    *,
    class_name: str = "",
    subject_name: str = "",
    exam_name: str = "",
    stats: dict[str, Any] | None = None,
    stdev_level: str = "",
    rank_summary: str = "",
) -> str:
    """根据班级 KPI / 分数段 / 离散度生成「总体分析」正文（HTML 段落）。"""
    stats = stats or {}
    count = int(stats.get("count") or 0)
    avg = _num(stats.get("avg"))
    pass_rate = _num(stats.get("pass_rate"))
    excellent_rate = _num(stats.get("excellent_rate"))
    good_rate = _num(stats.get("good_rate"))
    low_rate = _num(stats.get("low_score_rate"))
    max_s = _num(stats.get("max"))
    min_s = _num(stats.get("min"))
    full = _num(stats.get("full_score")) or 100.0
    stdev = _num(stats.get("stdev"))
    segments = stats.get("segments") or []

    scope_bits = [p for p in (class_name, subject_name, exam_name) if p]
    scope = " · ".join(scope_bits) if scope_bits else "本班"

    if not count and avg is None and pass_rate is None:
        return f"<p>{scope}暂无足够成绩明细，无法生成量化总体分析。</p>"

    parts: list[str] = []
    head = f"<p><strong>{scope}</strong>共 <strong>{count}</strong> 人参考"
    if avg is not None:
        head += f"，均分 <strong>{_fmt(avg)}</strong>（满分 {_fmt(full)}）"
    if pass_rate is not None:
        head += f"，及格率 <strong>{_fmt(pass_rate)}%</strong>"
    if excellent_rate is not None:
        head += f"，优秀率 <strong>{_fmt(excellent_rate)}%</strong>"
    if good_rate is not None:
        head += f"，良好率 {_fmt(good_rate)}%"
    head += "。</p>"
    parts.append(head)

    if max_s is not None or min_s is not None or stdev is not None:
        range_bits: list[str] = []
        if max_s is not None and min_s is not None:
            range_bits.append(f"最高分 {_fmt(max_s)}、最低分 {_fmt(min_s)}")
        elif max_s is not None:
            range_bits.append(f"最高分 {_fmt(max_s)}")
        elif min_s is not None:
            range_bits.append(f"最低分 {_fmt(min_s)}")
        if stdev is not None:
            level = stdev_level or "适中"
            range_bits.append(f"标准差 {_fmt(stdev)}（{level}）")
        parts.append(f"<p>{'，'.join(range_bits)}。</p>")

    # 分数段：点出占比最高段与低分段
    top_seg = None
    low_segs: list[dict[str, Any]] = []
    for s in segments:
        if not isinstance(s, dict):
            continue
        ratio = _num(s.get("ratio")) or 0
        label = str(s.get("label") or "")
        if top_seg is None or ratio > (_num(top_seg.get("ratio")) or 0):
            top_seg = s
        lowish = (
            "低" in label
            or "不及格" in label
            or label.startswith("0-")
            or (("-" in label) and "60" in label.split("-", 1)[0])
        )
        if ratio >= 10 and lowish:
            low_segs.append(s)
    seg_bits: list[str] = []
    if top_seg and (_num(top_seg.get("ratio")) or 0) > 0:
        seg_bits.append(
            f"人数最多的分数段为「{top_seg.get('label')}」"
            f"（{top_seg.get('count', 0)} 人，占比 {_fmt(top_seg.get('ratio'))}%）"
        )
    if low_segs:
        low = low_segs[0]
        seg_bits.append(
            f"低分段「{low.get('label')}」仍有 {low.get('count', 0)} 人"
            f"（{_fmt(low.get('ratio'))}%）需关注"
        )
    elif low_rate is not None and low_rate >= 10:
        seg_bits.append(f"低分率 {_fmt(low_rate)}%，需安排分层巩固")
    if seg_bits:
        parts.append(f"<p>{'；'.join(seg_bits)}。</p>")

    if rank_summary:
        rs = rank_summary.strip()
        if rs and not rs.startswith("<") and "{" not in rs:
            parts.append(f"<p>年级位置：{rs}</p>")

    # 一句定性判断
    verdict = ""
    if pass_rate is not None and pass_rate < 60:
        verdict = "整体过关压力较大，应优先稳住及格带。"
    elif pass_rate is not None and pass_rate < 80 and (
        excellent_rate is None or excellent_rate < 20
    ):
        verdict = "过关基本面尚可，但优秀层偏薄，提质与培优需并进。"
    elif excellent_rate is not None and excellent_rate >= 30 and (
        pass_rate is None or pass_rate >= 80
    ):
        verdict = "班级高分段表现较好，可在巩固基础上拉开优秀层厚度。"
    elif pass_rate is not None and pass_rate >= 85:
        verdict = "整体达标情况较好，重点转为防回落与临界生盯防。"
    if verdict:
        parts.append(f"<p>{verdict}</p>")

    return "".join(parts)


def build_class_overview_recommendations(
    *,
    stats: dict[str, Any] | None = None,
    dispersion_tip: str = "",
) -> str:
    """根据班级 KPI / 分数段生成「改进建议」有序列表。"""
    items = _build_class_kpi_items(stats)
    if not items and dispersion_tip:
        tip = dispersion_tip.strip()
        if tip and "分化" in tip:
            items = [{
                "priority": 2,
                "title": "缩小成绩分化",
                "desc": "针对两端学生分层辅导：低分段基础通关，高分段限时综合；两周后复查标准差变化。",
                "metric": "",
            }]
    if not items:
        return (
            "<ol>"
            "<li><strong>巩固与防回落</strong>："
            "结合分数段盯防临界生，每周保留 1 次综合过关练 + 1 次错题回流。</li>"
            "</ol>"
        )
    lis = "".join(
        f"<li><strong>{it.get('title') or '建议'}</strong>：{it.get('desc') or ''}"
        + (f"（{it['metric']}）" if it.get("metric") else "")
        + "</li>"
        for it in items
    )
    return f"<ol>{lis}</ol>"


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
    "apply_canonical_knowledge_to_item_rows",
    "build_class_overview_recommendations",
    "build_class_overview_summary",
    "build_diagnosis_recommendations",
    "build_diagnosis_summary",
    "build_item_compare_table_html",
    "build_item_table_html",
    "build_knowledge_class_rows_from_items",
    "build_knowledge_compare_chart_payload",
    "build_knowledge_compare_table_html",
    "build_knowledge_table_html",
    "build_segment_table_html",
    "coerce_report_table_fields",
    "collect_class_names",
    "enrich_knowledge_rows",
    "identify_weak_items",
    "identify_weak_knowledge",
    "knowledge_names_subquery_join",
    "knowledge_weighted_join",
    "normalize_item_row",
    "normalize_link_weights",
    "resolve_question_knowledge_map",
    "weighted_score_contributions",
]
