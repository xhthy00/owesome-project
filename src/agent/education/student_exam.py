"""单个学生多次考试深度分析报告数据组装（对齐 Word 样例结构）。"""

from __future__ import annotations

from typing import Any

from src.agent.education.charts import build_chart_option
from src.agent.education.comprehensive import (
    _advice,
    _exam_has_valid_data,
    _find_student_row,
    _normalize_records,
    _record_effective_total,
    _resolve_exams,
    _short_exam_label,
)
from src.agent.education.query_parse import student_matches
from src.agent.education.subject_diagnosis import build_knowledge_table_html, enrich_knowledge_rows


def _fmt(v: Any) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.1f}" if v == int(v) else f"{v:.2f}"
    return str(v)


def _table(headers: list[str], rows: list[list[str]]) -> str:
    head = "<tr>" + "".join(f"<th>{h}</th>" for h in headers) + "</tr>"
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<div class="table-wrap"><table class="edu-table">{head}{body}</table></div>'


def _rank_label(rank: int | None, total: int) -> str:
    if rank is None or total <= 0:
        return "-"
    return f"第{rank}/{total}"


def _trend_tag(delta: float) -> str:
    if delta > 3:
        return f"📈 上升 (+{_fmt(delta)})"
    if delta < -3:
        return f"📉 下降 ({_fmt(delta)})"
    return f"📊 稳定 ({_fmt(delta)})"


def _resolve_student_name(records: list[dict[str, Any]], student_name: str) -> str:
    target = (student_name or "").strip()
    if not target:
        names = sorted({str(r.get("student") or "") for r in records if r.get("student")})
        return names[0] if names else "未知学生"
    for r in records:
        name = str(r.get("student") or "")
        if student_matches(name, target):
            return name
    return target


def _pick_item_insight(
    item_insight: dict[str, Any] | None,
    student_item_insights: dict[str, dict[str, Any]] | None,
    student_name: str,
) -> dict[str, Any]:
    if isinstance(item_insight, dict) and (
        item_insight.get("weak_knowledge")
        or item_insight.get("strong_knowledge")
        or item_insight.get("weak_items")
        or item_insight.get("knowledge_rows")
    ):
        return item_insight
    insights = student_item_insights or {}
    for key, val in insights.items():
        if student_matches(str(key), student_name) and isinstance(val, dict):
            return val
    return {}


def _knowledge_rows_from_insight(insight: dict[str, Any]) -> list[dict[str, Any]]:
    rows = list(insight.get("knowledge_rows") or [])
    if rows:
        return enrich_knowledge_rows(rows)
    all_k = list(insight.get("all_knowledge") or [])
    if all_k:
        return enrich_knowledge_rows(all_k)
    merged: dict[str, dict[str, Any]] = {}
    for bucket, default_level in (
        (insight.get("weak_knowledge") or [], "需加强"),
        (insight.get("strong_knowledge") or [], "掌握较好"),
    ):
        for k in bucket:
            if isinstance(k, dict):
                name = str(k.get("knowledge_name") or "").strip()
                if not name or name == "未关联知识点":
                    continue
                merged[name] = {
                    "knowledge_name": name,
                    "score_rate": k.get("score_rate"),
                    "question_count": k.get("question_count") or k.get("question_nos") or 1,
                    "level": default_level,
                }
            else:
                name = str(k).strip()
                if name and name != "未关联知识点":
                    merged[name] = {
                        "knowledge_name": name,
                        "score_rate": None,
                        "question_count": 1,
                        "level": default_level,
                    }
    return enrich_knowledge_rows(list(merged.values()))


def _build_weak_item_table(insight: dict[str, Any]) -> str:
    items = list(insight.get("weak_items") or [])
    if not items:
        return ""
    rows: list[list[str]] = []
    for it in items[:20]:
        rows.append([
            _short_exam_label(str(it.get("exam_name") or "")),
            str(it.get("question_no") or "-"),
            str(it.get("knowledge_name") or "-"),
            f"{_fmt(it.get('score_rate'))}%",
        ])
    return _table(["考试", "题号", "知识点", "得分率"], rows)


def _build_ability_radar_chart(
    *,
    student_name: str,
    subject_name: str,
    insight: dict[str, Any],
) -> str:
    """单科能力/知识点雷达：优先能力层级，否则取知识点得分率作维度。"""
    knowledge = _knowledge_rows_from_insight(insight)
    if not knowledge:
        return ""
    from src.agent.education.knowledge_tier import ABILITY_LABELS, build_ability_tier_summary

    tier = build_ability_tier_summary(knowledge)
    by_level = [
        s
        for s in (tier.get("by_ability_level") or [])
        if s.get("ability_level") not in (None, "", "unknown")
        and s.get("avg_score_rate") is not None
    ]
    title_prefix = f"{student_name}" + (f" {subject_name}" if subject_name else "")
    if len(by_level) >= 3:
        return build_chart_option(
            "ability_radar",
            {
                "levels": [
                    ABILITY_LABELS.get(str(s.get("ability_level")), str(s.get("ability_level")))
                    for s in by_level
                ],
                "values": [float(s.get("avg_score_rate") or 0) for s in by_level],
            },
            f"{title_prefix} 能力层级雷达",
        )
    # 知识点作雷达维度（至少 3 个才有可读性）
    usable = [
        r
        for r in knowledge
        if str(r.get("knowledge_name") or "") not in ("", "未关联知识点")
        and r.get("score_rate") is not None
    ]
    if len(usable) < 3:
        return ""
    # 弱项优先，再补强项，最多 8 维
    usable.sort(key=lambda r: float(r.get("score_rate") or 0))
    weak_first = usable[:5]
    strong = list(reversed(usable[-3:]))
    picked: list[dict[str, Any]] = []
    seen: set[str] = set()
    for r in weak_first + strong:
        name = str(r.get("knowledge_name") or "")
        if name in seen:
            continue
        seen.add(name)
        picked.append(r)
        if len(picked) >= 8:
            break
    if len(picked) < 3:
        return ""
    return build_chart_option(
        "ability_radar",
        {
            "levels": [str(r.get("knowledge_name") or "") for r in picked],
            "values": [float(r.get("score_rate") or 0) for r in picked],
        },
        f"{title_prefix} 知识点掌握雷达",
    )


def _build_knowledge_heatmap_chart(
    *,
    student_name: str,
    subject_name: str,
    insight: dict[str, Any],
    exams: list[str],
) -> str:
    """知识点×考试得分率热力图；无逐场知识点时退化为知识点×掌握度单列。"""
    items = [it for it in (insight.get("weak_items") or []) if isinstance(it, dict)]
    # 聚合 knowledge × exam → score_rate
    pivot: dict[str, dict[str, float]] = {}
    exam_labels: list[str] = []
    for it in items:
        kn = str(it.get("knowledge_name") or "").strip()
        if not kn or kn == "未关联知识点":
            continue
        exam = str(it.get("exam_name") or "").strip()
        if not exam:
            continue
        short = _short_exam_label(exam)
        if short not in exam_labels:
            exam_labels.append(short)
        rate = it.get("score_rate")
        if rate is None:
            continue
        try:
            pivot.setdefault(kn, {})[short] = float(rate)
        except (TypeError, ValueError):
            continue

    title_prefix = f"{student_name}" + (f" {subject_name}" if subject_name else "")
    if pivot and exam_labels:
        # 考试列优先按报告考试顺序
        preferred = [_short_exam_label(e) for e in exams]
        ordered_cols = [c for c in preferred if c in exam_labels]
        ordered_cols.extend([c for c in exam_labels if c not in ordered_cols])
        ranked = sorted(
            pivot.keys(),
            key=lambda k: sum(pivot[k].values()) / max(len(pivot[k]), 1),
        )[:10]
        matrix = [[pivot[k].get(c) for c in ordered_cols] for k in ranked]
        return build_chart_option(
            "heatmap",
            {
                "rows": ranked,
                "cols": ordered_cols,
                "matrix": matrix,
                "min": 0,
                "max": 100,
                "series_name": "得分率(%)",
            },
            f"{title_prefix} 知识点×考试得分率热力图",
        )

    # 退路：知识点列表作行，单列得分率
    knowledge = _knowledge_rows_from_insight(insight)
    usable = [
        r
        for r in knowledge
        if str(r.get("knowledge_name") or "") not in ("", "未关联知识点")
        and r.get("score_rate") is not None
    ]
    if len(usable) < 2:
        return ""
    usable.sort(key=lambda r: float(r.get("score_rate") or 0))
    top = usable[:12]
    return build_chart_option(
        "heatmap",
        {
            "rows": [str(r.get("knowledge_name") or "") for r in top],
            "cols": ["得分率(%)"],
            "matrix": [[round(float(r.get("score_rate") or 0), 1)] for r in top],
            "min": 0,
            "max": 100,
            "series_name": "得分率(%)",
        },
        f"{title_prefix} 知识点得分率热力图",
    )


def _build_exam_position_heatmap(
    *,
    student_name: str,
    subject_name: str,
    exams: list[str],
    stu_scores: dict[str, float | None],
    class_avgs: dict[str, float | None],
    class_maxes: dict[str, float | None],
) -> str:
    """无知识点时：考试×相对位置（该生/班均，归一到百分制）热力图。"""
    if len(exams) < 2:
        return ""
    cols = [_short_exam_label(e) for e in exams]
    stu_row: list[float | None] = []
    avg_row: list[float | None] = []
    rel_row: list[float | None] = []
    for e in exams:
        sc = stu_scores.get(e)
        ca = class_avgs.get(e)
        cm = class_maxes.get(e)
        full = cm if cm and cm > 0 else None
        if full is None and sc is not None:
            full = max(float(sc), float(ca or 0)) or None
        if sc is not None and full:
            stu_row.append(round(100.0 * float(sc) / float(full), 1))
        else:
            stu_row.append(None)
        if ca is not None and full:
            avg_row.append(round(100.0 * float(ca) / float(full), 1))
        else:
            avg_row.append(None)
        if sc is not None and ca is not None:
            # 50 为持平，+10 分 → 60；裁剪到 0–100
            rel_row.append(max(0.0, min(100.0, 50.0 + float(sc) - float(ca))))
        else:
            rel_row.append(None)
    if all(v is None for v in stu_row):
        return ""
    title_prefix = f"{student_name}" + (f" {subject_name}" if subject_name else "")
    return build_chart_option(
        "heatmap",
        {
            "rows": ["该生相对满分%", "班均相对满分%", "相对班均(50=持平)"],
            "cols": cols,
            "matrix": [stu_row, avg_row, rel_row],
            "min": 0,
            "max": 100,
            "series_name": "指数",
        },
        f"{title_prefix} 历次考试相对位置热力图",
    )


def _build_knowledge_section(
    *,
    student_name: str,
    insight: dict[str, Any],
) -> tuple[str, str, str, str]:
    """返回 (insight_html, knowledge_table, weak_item_table, chart_json)。"""
    knowledge = _knowledge_rows_from_insight(insight)
    weak_know = [
        str(k.get("knowledge_name") or k)
        for k in (insight.get("weak_knowledge") or [])[:5]
    ]
    weak_know = [n for n in weak_know if n and n != "未关联知识点"]
    strong_know = [
        str(k.get("knowledge_name") or k)
        for k in (insight.get("strong_knowledge") or [])[:3]
    ]
    strong_know = [n for n in strong_know if n and n != "未关联知识点"]

    if not knowledge and not insight.get("weak_items"):
        empty = (
            f"<p class='edu-sub'>暂未加载到 {student_name} 的小题/知识点明细；"
            "导入 tb_score_detail 并关联知识点后可自动生成得分明细与提升建议。</p>"
        )
        return empty, "", "", ""

    bits: list[str] = []
    if weak_know:
        bits.append(f"薄弱知识点：{'、'.join(weak_know)}。")
    if strong_know:
        bits.append(f"掌握较好：{'、'.join(strong_know)}。")
    if insight.get("weak_items"):
        bits.append(
            f"低得分小题 {len(insight.get('weak_items') or [])} 道，建议按考试逐项补漏。"
        )
    insight_html = (
        f"<div class='insight'>"
        f"{''.join(bits) if bits else f'{student_name} 知识点掌握情况已汇总如下。'}"
        f"</div>"
    )
    table = build_knowledge_table_html(knowledge) if knowledge else ""
    weak_table = _build_weak_item_table(insight)
    chart = ""
    if knowledge:
        top = sorted(knowledge, key=lambda r: float(r.get("score_rate") or 0))[:12]
        chart = build_chart_option(
            "knowledge_bar",
            {
                "categories": [str(r.get("knowledge_name") or "") for r in top],
                "values": [float(r.get("score_rate") or 0) for r in top],
            },
            f"{student_name} 知识点得分率（由低到高）",
        )
    return insight_html, table, weak_table, chart


def build_student_exam_data(
    records: list[dict[str, Any]],
    student_name: str,
    exam_order: list[str] | None = None,
    class_name: str = "",
    class_size: int | None = None,
    item_insight: dict[str, Any] | None = None,
    student_item_insights: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """组装单个学生多次考试分析报告的全量 data 字典。

    ``item_insight`` / ``student_item_insights``：小题与知识点掌握情况，
    用于「知识点得分明细」与备考建议（与综合报告第九节同源）。
    """
    _normalize_records(records)
    resolved_name = _resolve_student_name(records, student_name)

    record_exam_order: list[str] = []
    seen_exam: set[str] = set()
    for r in records:
        e = str(r.get("exam") or "")
        if e and e not in seen_exam:
            seen_exam.add(e)
            record_exam_order.append(e)

    by_exam: dict[str, list[dict[str, Any]]] = {}
    for r in records:
        by_exam.setdefault(str(r.get("exam") or ""), []).append(r)

    exams = _resolve_exams(record_exam_order, exam_order or None)
    exams = [e for e in exams if _exam_has_valid_data(by_exam, e)] or list(record_exam_order)

    subjects: list[str] = []
    sub_seen: set[str] = set()
    for r in records:
        for sub in (r.get("subjects") or {}).keys():
            if sub not in sub_seen:
                sub_seen.add(sub)
                subjects.append(sub)

    n_class = class_size or max((len(by_exam.get(e, [])) for e in exams), default=0) or None

    def _ranks(exam: str, key_fn) -> dict[str, int]:
        pairs: list[tuple[str, float]] = []
        for r in by_exam.get(exam, []):
            v = key_fn(r)
            if v is not None:
                pairs.append((str(r.get("student") or ""), float(v)))
        pairs.sort(key=lambda x: x[1], reverse=True)
        return {name: i + 1 for i, (name, _) in enumerate(pairs)}

    def _class_avg(exam: str, sub: str | None = None) -> float | None:
        vals: list[float] = []
        for r in by_exam.get(exam, []):
            if sub:
                v = (r.get("subjects") or {}).get(sub)
            else:
                v = _record_effective_total(r)
            if v is not None:
                vals.append(float(v))
        return round(sum(vals) / len(vals), 1) if vals else None

    def _class_max(exam: str, sub: str | None = None) -> float | None:
        vals: list[float] = []
        for r in by_exam.get(exam, []):
            if sub:
                v = (r.get("subjects") or {}).get(sub)
            else:
                v = _record_effective_total(r)
            if v is not None:
                vals.append(float(v))
        return max(vals) if vals else None

    stu_totals: dict[str, float] = {}
    stu_sub_scores: dict[str, dict[str, float]] = {s: {} for s in subjects}
    for e in exams:
        row = _find_student_row(by_exam, e, resolved_name)
        if not row:
            continue
        eff = _record_effective_total(row)
        if eff is not None:
            stu_totals[e] = eff
        for sub in subjects:
            v = (row.get("subjects") or {}).get(sub)
            if v is not None:
                stu_sub_scores[sub][e] = float(v)

    exam_label = (
        f"共{len(exams)}次考试（{'、'.join(exams)}）" if exams else "历次考试"
    )

    stu_avg = (
        round(sum(stu_totals.values()) / len(stu_totals), 2) if stu_totals else None
    )
    last_exam = exams[-1] if exams else ""
    last_tv = stu_totals.get(last_exam) if last_exam else None
    last_cm = _class_max(last_exam) if last_exam else None
    last_gap = (
        (last_cm - last_tv) if last_cm is not None and last_tv is not None else None
    )
    avg_gap_parts: list[float] = []
    for e in exams:
        tv, cm = stu_totals.get(e), _class_max(e)
        if tv is not None and cm is not None:
            avg_gap_parts.append(cm - tv)
    avg_gap_to_first = (
        round(sum(avg_gap_parts) / len(avg_gap_parts), 2) if avg_gap_parts else None
    )

    single_subject = len(subjects) == 1
    sole_subject = subjects[0] if single_subject else ""

    if single_subject:
        # 单科：考试 | 得分 | 班级排名 | 班级均分 | 与均分差（不重复「总分」列）
        summary_header = ["考试", f"{sole_subject}得分", "班级排名", "班级均分", "与均分差"]
        summary_rows: list[list[str]] = []
        for e in exams:
            sc = stu_sub_scores[sole_subject].get(e)
            rk = _ranks(
                e, lambda r, s=sole_subject: (r.get("subjects") or {}).get(s)
            ).get(resolved_name)
            ca = _class_avg(e, sole_subject)
            gap = (sc - ca) if sc is not None and ca is not None else None
            summary_rows.append([
                e,
                _fmt(sc) if sc is not None else "-",
                _rank_label(rk, n_class or 0),
                _fmt(ca) if ca is not None else "-",
                _fmt(gap) if gap is not None else "-",
            ])
        score_summary_table = _table(summary_header, summary_rows)

        key_rows: list[list[str]] = []
        vals = [stu_sub_scores[sole_subject].get(e) for e in exams]
        if any(v is not None for v in vals):
            delta = (
                (vals[-1] or 0) - (vals[0] or 0)
                if vals[0] is not None and vals[-1] is not None
                else 0
            )
            key_rows.append([sole_subject, *[_fmt(v) for v in vals], _trend_tag(delta)])
            # 班级均分对照行
            avgs = [_class_avg(e, sole_subject) for e in exams]
            key_rows.append(["班级均分", *[_fmt(v) for v in avgs], "—"])
        key_metrics_table = _table(["指标", *exams, "趋势判断"], key_rows) if key_rows else ""
    else:
        summary_header = ["考试", *subjects]
        for sub in subjects:
            summary_header.extend([f"{sub}排名"])
        summary_header.extend(["总分", "总分排名"])
        summary_rows = []
        for e in exams:
            total_ranks = _ranks(e, lambda r: _record_effective_total(r))
            cells = [e]
            for sub in subjects:
                score = stu_sub_scores[sub].get(e)
                cells.append(_fmt(score) if score is not None else "-")
            for sub in subjects:
                sub_ranks = _ranks(e, lambda r, s=sub: (r.get("subjects") or {}).get(s))
                cells.append(_rank_label(sub_ranks.get(resolved_name), n_class or 0))
            total = stu_totals.get(e)
            cells.extend([
                _fmt(total) if total is not None else "-",
                _rank_label(total_ranks.get(resolved_name), n_class or 0),
            ])
            summary_rows.append(cells)
        score_summary_table = _table(summary_header, summary_rows)

        key_rows = []
        if len(exams) >= 1:
            for label in ["总分", *subjects]:
                if label == "总分":
                    vals = [stu_totals.get(e) for e in exams]
                else:
                    vals = [stu_sub_scores[label].get(e) for e in exams]
                if not any(v is not None for v in vals):
                    continue
                delta = (
                    (vals[-1] or 0) - (vals[0] or 0)
                    if vals[0] is not None and vals[-1] is not None
                    else 0
                )
                key_rows.append([label, *[_fmt(v) for v in vals], _trend_tag(delta)])
        key_metrics_table = _table(["指标", *exams, "趋势判断"], key_rows) if key_rows else ""

    subject_sections: list[str] = []
    strong_sub, weak_sub = "", ""
    strong_delta, weak_delta = -999.0, 999.0
    for sub in subjects:
        per_exam = [stu_sub_scores[sub].get(e) for e in exams]
        valid = [(e, v) for e, v in zip(exams, per_exam) if v is not None]
        if not valid:
            continue
        first_v, last_v = valid[0][1], valid[-1][1]
        delta = last_v - first_v
        if delta > strong_delta:
            strong_delta, strong_sub = delta, sub
        if delta < weak_delta:
            weak_delta, weak_sub = delta, sub

        detail_rows: list[list[str]] = []
        for metric in ("分数", "班级排名", "班级均分", "与均分差值"):
            cells = [metric]
            for e in exams:
                if metric == "分数":
                    cells.append(_fmt(stu_sub_scores[sub].get(e)))
                elif metric == "班级排名":
                    rk = _ranks(
                        e, lambda r, s=sub: (r.get("subjects") or {}).get(s)
                    ).get(resolved_name)
                    cells.append(_rank_label(rk, n_class or 0))
                elif metric == "班级均分":
                    cells.append(_fmt(_class_avg(e, sub)))
                else:
                    sc = stu_sub_scores[sub].get(e)
                    ca = _class_avg(e, sub)
                    cells.append(
                        _fmt(sc - ca) if sc is not None and ca is not None else "-"
                    )
            cells.append(
                _fmt(delta) if metric == "分数" and len(valid) >= 2 else "—"
            )
            detail_rows.append(cells)
        detail_table = _table(
            ["指标", *exams, f"变化（{exams[0]}→{exams[-1]}）"], detail_rows
        )

        avg_diffs = []
        for e in exams:
            sc = stu_sub_scores[sub].get(e)
            ca = _class_avg(e, sub)
            if sc is not None and ca is not None:
                avg_diffs.append(sc - ca)
        lead = sum(1 for d in avg_diffs if d > 5)
        lag = sum(1 for d in avg_diffs if d < -5)
        if lead >= lag and lead > 0:
            tag, analysis = (
                "优势学科",
                f"{sub}是{resolved_name}的优势科目，多次考试领先班级均分。",
            )
            conclusion = f"建议保持现有学习方法，巩固{sub}优势地位。"
        elif lag > 0:
            tag = "短板学科"
            analysis = f"{sub}是{resolved_name}的薄弱科目，与班级均分差距明显。"
            conclusion = (
                f"建议将{sub}作为重点攻坚科目，回归基础、整理错题、针对性强化。"
            )
        else:
            tag, analysis = "稳定学科", f"{sub}表现相对稳定。"
            conclusion = f"维持当前{sub}学习节奏，关注细节失分点。"

        subject_sections.append(
            f'<section class="edu-card"><h3>（{subjects.index(sub)+1}）{sub}：{tag}</h3>'
            f"<h4>1. 成绩走势</h4>{detail_table}"
            f"<h4>2. 分析</h4><p>{analysis}</p>"
            f"<h4>3. 结论</h4><p>{conclusion}</p></section>"
        )
    subject_analysis_html = "".join(subject_sections)

    total_rows: list[list[str]] = []
    total_ranks_map: dict[str, int | None] = {}
    for e in exams:
        tr = _ranks(e, lambda r: _record_effective_total(r)).get(resolved_name)
        total_ranks_map[e] = tr
        tv = stu_totals.get(e)
        ca, cm = _class_avg(e), _class_max(e)
        gap = (cm - tv) if cm is not None and tv is not None else None
        total_rows.append([
            e,
            _fmt(tv),
            _rank_label(tr, n_class or 0),
            _fmt(ca),
            _fmt(cm),
            _fmt(-gap) if gap is not None else "-",
        ])
    if single_subject:
        total_analysis_table = _table(
            ["考试", f"{sole_subject}得分", "班级排名", "班级均分", "班级最高分", "与第1名差距"],
            total_rows,
        )
    else:
        total_analysis_table = _table(
            ["考试", "总分", "总分排名", "班级均分", "班级最高分", "与第1名差距"],
            total_rows,
        )

    first_total = stu_totals.get(exams[0]) if exams else None
    last_total = stu_totals.get(exams[-1]) if exams else None
    total_delta = (
        (last_total - first_total)
        if first_total is not None and last_total is not None
        else 0
    )
    first_rank = total_ranks_map.get(exams[0]) if exams else None
    last_rank = total_ranks_map.get(exams[-1]) if exams else None
    rank_delta = (first_rank - last_rank) if first_rank and last_rank else 0

    if total_delta > 10 and rank_delta > 0:
        trend_narrative = (
            f"总体呈明显进步：总分从 {_fmt(first_total)} 升至 {_fmt(last_total)}"
            f"（+{_fmt(total_delta)}），排名提升约 {rank_delta} 位。"
        )
    elif total_delta < -10:
        trend_narrative = (
            f"总体呈下滑：总分从 {_fmt(first_total)} 降至 {_fmt(last_total)}"
            f"（{_fmt(total_delta)}），需警惕并调整学习策略。"
        )
    else:
        trend_narrative = (
            f"总分在 {_fmt(first_total)}–{_fmt(last_total)} 区间波动，整体相对稳定。"
        )

    contrib_parts: list[str] = []
    for sub in subjects:
        diffs = []
        for e in exams:
            sc, ca = stu_sub_scores[sub].get(e), _class_avg(e, sub)
            if sc is not None and ca is not None:
                diffs.append(sc - ca)
        if diffs:
            avg_d = sum(diffs) / len(diffs)
            role = "拉分项" if avg_d > 8 else ("扣分项" if avg_d < -8 else "均衡项")
            contrib_parts.append(f"{sub}（{role}，平均与均分差 {_fmt(avg_d)} 分）")
    contribution_insight = "；".join(contrib_parts) or "学科贡献度数据不足。"

    diff_rows: list[list[str]] = []
    for sub in subjects:
        diffs = []
        for e in exams:
            sc, ca = stu_sub_scores[sub].get(e), _class_avg(e, sub)
            diffs.append(
                _fmt(sc - ca) if sc is not None and ca is not None else "-"
            )
        if all(d == "-" for d in diffs):
            continue
        trend = (
            "领先扩大"
            if len(diffs) >= 2
            and diffs[-1] != "-"
            and diffs[0] != "-"
            and float(diffs[-1]) > float(diffs[0]) + 3
            else "基本持平"
        )
        diff_rows.append([sub, *diffs, trend])
    class_diff_table = _table(["科目", *[f"{e}差值" for e in exams], "趋势"], diff_rows)

    radar_vals = [
        round(
            sum(stu_sub_scores[s].get(e, 0) for e in exams)
            / max(len([e for e in exams if e in stu_sub_scores[s]]), 1),
            1,
        )
        for s in subjects
    ]
    if single_subject:
        # 单科不渲染「各科雷达」（仅 1 个顶点无意义）；主图用单科趋势
        subject_radar = ""
        trend_series = [
            {
                "name": sole_subject,
                "values": [stu_sub_scores[sole_subject].get(e, 0) for e in exams],
            },
            {
                "name": "班级均分",
                "values": [_class_avg(e, sole_subject) or 0 for e in exams],
            },
        ]
        trend_line = build_chart_option(
            "trend_line",
            {"x_labels": exams, "series": trend_series},
            f"{resolved_name} {sole_subject}成绩趋势（对照班均）",
        )
        total_trend = build_chart_option(
            "trend_line",
            {
                "x_labels": exams,
                "series": [
                    {
                        "name": sole_subject,
                        "values": [stu_sub_scores[sole_subject].get(e, 0) for e in exams],
                    }
                ],
            },
            f"{resolved_name} {sole_subject}成绩走势",
        )
    else:
        subject_radar = build_chart_option(
            "subject_radar",
            {"subjects": subjects, "values": radar_vals},
            f"{resolved_name} 各科均分雷达图",
        )
        trend_series = [{"name": "总分", "values": [stu_totals.get(e, 0) for e in exams]}]
        for sub in subjects:
            trend_series.append(
                {"name": sub, "values": [stu_sub_scores[sub].get(e, 0) for e in exams]}
            )
        trend_line = build_chart_option(
            "trend_line",
            {"x_labels": exams, "series": trend_series},
            f"{resolved_name} 成绩趋势",
        )
        total_trend = build_chart_option(
            "trend_line",
            {
                "x_labels": exams,
                "series": [{"name": "总分", "values": [stu_totals.get(e, 0) for e in exams]}],
            },
            f"{resolved_name} 总分走势",
        )

    insight = _pick_item_insight(item_insight, student_item_insights, resolved_name)
    if not insight:
        insight = _pick_item_insight(item_insight, student_item_insights, student_name)
    if insight and not insight.get("all_knowledge") and insight.get("knowledge_rows"):
        insight = {**insight, "all_knowledge": insight["knowledge_rows"]}

    kn_insight, kn_table, kn_weak_table, kn_chart = _build_knowledge_section(
        student_name=resolved_name, insight=insight
    )

    if single_subject:
        overview = (
            f"{resolved_name}本报告聚焦 <strong>{sole_subject}</strong>，共分析 "
            f"<strong>{len(exams)}</strong> 次考试（{'、'.join(exams)}）。"
            + (
                f"多次均分 <strong>{_fmt(stu_avg)}</strong> 分；"
                if stu_avg is not None
                else ""
            )
            + (
                f"最近一场（{last_exam}）得分 {_fmt(last_tv)}，"
                f"距班级第1名 {_fmt(last_gap)} 分；"
                if last_exam and last_tv is not None and last_gap is not None
                else ""
            )
            + f"{trend_narrative}"
        )
    else:
        overview = (
            f"{resolved_name}共分析 <strong>{len(exams)}</strong> 次考试"
            f"（{'、'.join(exams)}）。"
            + (
                f"多次均分 <strong>{_fmt(stu_avg)}</strong> 分；"
                if stu_avg is not None
                else ""
            )
            + (
                f"最近一场（{last_exam}）得分 {_fmt(last_tv)}，"
                f"距班级第1名 {_fmt(last_gap)} 分；"
                if last_exam and last_tv is not None and last_gap is not None
                else ""
            )
            + (
                f"各场相对班级第1名平均差距 {_fmt(avg_gap_to_first)} 分。"
                if avg_gap_to_first is not None
                else ""
            )
            + (
                f"{'优势科目为' + strong_sub + '，' if strong_sub else ''}"
                f"{'需重点提升' + weak_sub + '。' if weak_sub else ''}"
            )
            + f"{trend_narrative}"
        )
    weak_know_names = [
        str(k.get("knowledge_name") or k)
        for k in (insight.get("weak_knowledge") or [])[:3]
        if str(k.get("knowledge_name") or k) != "未关联知识点"
    ]
    assessment = (
        f"该生呈现"
        f"{'明显的偏科特征' if strong_sub and weak_sub else '相对均衡的学科结构'}。"
        f"{contribution_insight}"
    )
    if single_subject:
        assessment = (
            f"{resolved_name} 本报告聚焦 <strong>{sole_subject}</strong> 单科分析。"
            f"{trend_narrative}"
        )
    if weak_know_names:
        assessment += f" 知识点层面需优先补强：{'、'.join(weak_know_names)}。"

    if insight.get("weak_knowledge") or insight.get("weak_items") or insight.get(
        "strong_knowledge"
    ):
        weak_avg = None
        if weak_sub and weak_sub in stu_sub_scores:
            vals = [v for v in stu_sub_scores[weak_sub].values() if v is not None]
            weak_avg = round(sum(vals) / len(vals), 1) if vals else None
        recommendations = _advice(
            strong_sub,
            weak_sub,
            total_delta,
            weak_avg,
            item_insight=insight,
        )
    else:
        strategy_items = []
        if weak_sub:
            strategy_items.append(
                f"【{weak_sub}攻坚】作为最高优先级，回归基础、错题本、中档题训练。"
            )
        if strong_sub:
            strategy_items.append(
                f"【{strong_sub}保持】维持现有方法，适当拓展，避免优势回落。"
            )
        strategy_items.append("【总分策略】合理分配时间，短板提升优先于优势拓展。")
        recommendations = "<ul>" + "".join(f"<li>{s}</li>" for s in strategy_items) + "</ul>"

    from src.agent.education.config_store import get_config
    from src.agent.education.stats import compute_score_stats

    cfg = get_config()
    # 最近一场：班级总分分布
    score_dist_chart = ""
    if last_exam:
        class_totals = [
            t
            for r in by_exam.get(last_exam, [])
            if (t := _record_effective_total(r)) is not None
        ]
        if class_totals:
            full = None
            for r in by_exam.get(last_exam, []):
                for raw in (r.get("exam_score"), r.get("full_score")):
                    if raw is not None:
                        try:
                            full = float(raw)
                            break
                        except (TypeError, ValueError):
                            pass
                if full is not None:
                    break
            st = compute_score_stats(class_totals, cfg, full)
            score_dist_chart = build_chart_option(
                "score_distribution",
                {
                    "segments": st.get("segments") or [],
                    "pass_rate": st.get("pass_rate"),
                },
                (
                    f"{_short_exam_label(last_exam)} {sole_subject}班级得分分布"
                    if single_subject
                    else f"{_short_exam_label(last_exam)} 班级总分分布"
                )
                + (f"（{resolved_name} {_fmt(last_total)} 分）" if last_total is not None else ""),
            )

    # 散点：优先多场「班均 vs 该生」；单场用「成绩-名次」
    scatter_chart = ""
    if len(exams) >= 2:
        vs_avg: list[list[float]] = []
        for e in exams:
            stu = stu_totals.get(e)
            cavg = _class_avg(e)
            if stu is not None and cavg is not None:
                vs_avg.append([float(cavg), float(stu)])
        if vs_avg:
            scatter_chart = build_chart_option(
                "scatter",
                {
                    "x_name": "班级均分",
                    "y_name": "该生成绩",
                    "series": [{"name": "各场考试", "data": vs_avg, "symbolSize": 14}],
                },
                f"{resolved_name} 相对班级均分散点（多场）",
            )
    elif last_exam and last_total is not None:
        ranked = sorted(
            [
                (str(r.get("student") or ""), float(t))
                for r in by_exam.get(last_exam, [])
                if (t := _record_effective_total(r)) is not None
            ],
            key=lambda x: x[1],
            reverse=True,
        )
        peer_pts = [[score, float(i + 1)] for i, (_n, score) in enumerate(ranked)]
        stu_pt = [
            [score, float(i + 1)]
            for i, (n, score) in enumerate(ranked)
            if student_matches(n, resolved_name) or student_matches(n, student_name)
        ]
        if peer_pts:
            scatter_chart = build_chart_option(
                "scatter",
                {
                    "x_name": "成绩",
                    "y_name": "班内名次（1 最好）",
                    "series": [
                        {"name": "同班同学", "data": peer_pts, "symbolSize": 10},
                        {
                            "name": resolved_name,
                            "data": stu_pt
                            or [[float(last_total), float(last_rank or 1)]],
                            "symbolSize": 18,
                        },
                    ],
                },
                f"{_short_exam_label(last_exam)} 成绩-名次散点",
            )

    # 单科补充：能力雷达 + 热力图（知识点优先，否则相对位置）
    ability_radar_chart = ""
    heatmap_chart = ""
    if single_subject:
        ability_radar_chart = _build_ability_radar_chart(
            student_name=resolved_name,
            subject_name=sole_subject,
            insight=insight,
        )
        heatmap_chart = _build_knowledge_heatmap_chart(
            student_name=resolved_name,
            subject_name=sole_subject,
            insight=insight,
            exams=exams,
        )
        if not heatmap_chart:
            heatmap_chart = _build_exam_position_heatmap(
                student_name=resolved_name,
                subject_name=sole_subject,
                exams=exams,
                stu_scores={e: stu_totals.get(e) for e in exams},
                class_avgs={e: _class_avg(e) for e in exams},
                class_maxes={e: _class_max(e) for e in exams},
            )

    weak_list = "、".join(weak_know_names) if weak_know_names else ""
    if not weak_list:
        more = [
            str(k.get("knowledge_name") or k)
            for k in (insight.get("weak_knowledge") or [])[:8]
            if str(k.get("knowledge_name") or k) not in ("", "未关联知识点")
        ]
        weak_list = "、".join(more)

    from src.agent.education.report_types import ReportType, report_type_label

    title_subject = sole_subject if single_subject else ""
    report_title = (
        f"{resolved_name} {title_subject}学情分析报告"
        if title_subject
        else f"{resolved_name} 学情分析报告"
    )
    if single_subject:
        subject_section_title = f"{sole_subject}单科深度分析"
        subject_section_intro = (
            f"本报告仅分析 {sole_subject}。"
            "下方依次展示成绩趋势、能力/知识点雷达、班级分布与相对位置散点。"
        )
        overview_section_title = f"一、{sole_subject}成绩概览"
        trend_section_title = f"三、{sole_subject}走势与班级对比"
        parent_subject_title = f"{sole_subject}成绩表现"
        parent_subject_intro = (
            f"下图展示孩子在{sole_subject}科目的历次成绩走势，可对照班级均分看进退步；"
            "随后用雷达、热力图、分布与散点帮助直观定位强弱。"
        )
        contribution_insight_out = (
            f"{sole_subject}为本次分析科目；建议结合知识点薄弱清单与错题逐项过关。"
        )
    else:
        subject_section_title = "二、各科目深度分析"
        subject_section_intro = "下图为各科均分雷达图，越靠外圈表示该科表现越好。"
        overview_section_title = "一、总体成绩概览"
        trend_section_title = "三、总分与排名综合分析"
        parent_subject_title = "各科表现一览"
        parent_subject_intro = "下图展示孩子在各科目的得分情况，越靠外圈表示该科表现越好。"
        contribution_insight_out = contribution_insight

    return {
        "REPORT_TITLE": report_title,
        "REPORT_TYPE": report_type_label(ReportType.STUDENT_PROFILE),
        "REPORT_SUBTITLE": (
            f"{exam_label} · {sole_subject}单科"
            if single_subject
            else f"{exam_label} · 趋势与对比"
        ),
        "REPORT_TIME": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M"),
        "COVER_META": (
            f"分析对象：{resolved_name} | 考试范围：{'、'.join(exams)}（共{len(exams)}次）"
            + (f" | 多次均分：{_fmt(stu_avg)}" if stu_avg is not None else "")
            + (
                f" | 最近与第1名差距：{_fmt(last_gap)}分"
                if last_gap is not None
                else ""
            )
            + (f" | 班级：{class_name}" if class_name else "")
            + (f" | 班级人数：{n_class}人" if n_class else "")
            + (
                f" | 分析科目：{sole_subject}"
                if single_subject
                else (f" | 科目数：{len(subjects)}" if subjects else "")
            )
        ),
        "STUDENT_NAME": resolved_name,
        "CLASS_NAME": class_name or "-",
        "EXAM_NAME": exam_label,
        "SUBJECT_NAME": sole_subject if single_subject else ("、".join(subjects) if subjects else ""),
        "EXAM_COUNT": str(len(exams)),
        "MULTI_EXAM_AVG": _fmt(stu_avg) if stu_avg is not None else "-",
        "GAP_TO_FIRST": _fmt(last_gap) if last_gap is not None else "-",
        "OVERVIEW_INSIGHT": overview,
        "SCORE_SUMMARY_TABLE": score_summary_table,
        "KEY_METRICS_TABLE": key_metrics_table,
        "SUBJECT_ANALYSIS_HTML": subject_analysis_html,
        "TOTAL_ANALYSIS_TABLE": total_analysis_table,
        "TOTAL_TREND_INSIGHT": trend_narrative,
        "CONTRIBUTION_INSIGHT": contribution_insight_out,
        "CLASS_DIFF_TABLE": class_diff_table,
        "KNOWLEDGE_INSIGHT": kn_insight,
        "KNOWLEDGE_TABLE": kn_table,
        "WEAK_ITEM_TABLE": kn_weak_table,
        "KNOWLEDGE_CHART": kn_chart,
        "WEAK_KNOWLEDGE_LIST": weak_list or "暂无",
        "SCORE_DIST_CHART": score_dist_chart,
        "SCATTER_CHART": scatter_chart,
        "ABILITY_RADAR_CHART": ability_radar_chart,
        "HEATMAP_CHART": heatmap_chart,
        "ASSESSMENT": assessment,
        "RECOMMENDATIONS": recommendations,
        "SUBJECT_RADAR_CHART": subject_radar,
        "TREND_LINE_CHART": trend_line,
        "TOTAL_TREND_CHART": total_trend,
        "TOTAL_SCORE": _fmt(last_total),
        "CLASS_RANK": _rank_label(last_rank, n_class or 0),
        "GRADE_RANK": _rank_label(last_rank, n_class or 0),
        "SUBJECT_TABLE": score_summary_table,
        "SUMMARY": f"<p>{assessment}</p><p>{contribution_insight_out}</p>",
        # 单科 / 多科布局开关（模板用）
        "IS_SINGLE_SUBJECT": "1" if single_subject else "",
        "OVERVIEW_SECTION_TITLE": overview_section_title,
        "SUBJECT_SECTION_TITLE": subject_section_title,
        "SUBJECT_SECTION_INTRO": subject_section_intro,
        "TREND_SECTION_TITLE": trend_section_title,
        "PARENT_SUBJECT_TITLE": parent_subject_title,
        "PARENT_SUBJECT_INTRO": parent_subject_intro,
    }


__all__ = ["build_student_exam_data"]
