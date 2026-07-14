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

    summary_header = ["考试", *subjects]
    for sub in subjects:
        summary_header.extend([f"{sub}排名"])
    summary_header.extend(["总分", "总分排名"])
    summary_rows: list[list[str]] = []
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

    key_rows: list[list[str]] = []
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

    from src.agent.education.report_types import ReportType, report_type_label

    title_subject = subjects[0] if len(subjects) == 1 else ""
    report_title = (
        f"{resolved_name} {title_subject}学情分析报告"
        if title_subject
        else f"{resolved_name} 学情分析报告"
    )
    return {
        "REPORT_TITLE": report_title,
        "REPORT_TYPE": report_type_label(ReportType.STUDENT_PROFILE),
        "REPORT_SUBTITLE": f"{exam_label} · 趋势与对比",
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
        ),
        "STUDENT_NAME": resolved_name,
        "CLASS_NAME": class_name or "-",
        "EXAM_NAME": exam_label,
        "EXAM_COUNT": str(len(exams)),
        "MULTI_EXAM_AVG": _fmt(stu_avg) if stu_avg is not None else "-",
        "GAP_TO_FIRST": _fmt(last_gap) if last_gap is not None else "-",
        "OVERVIEW_INSIGHT": overview,
        "SCORE_SUMMARY_TABLE": score_summary_table,
        "KEY_METRICS_TABLE": key_metrics_table,
        "SUBJECT_ANALYSIS_HTML": subject_analysis_html,
        "TOTAL_ANALYSIS_TABLE": total_analysis_table,
        "TOTAL_TREND_INSIGHT": trend_narrative,
        "CONTRIBUTION_INSIGHT": contribution_insight,
        "CLASS_DIFF_TABLE": class_diff_table,
        "KNOWLEDGE_INSIGHT": kn_insight,
        "KNOWLEDGE_TABLE": kn_table,
        "WEAK_ITEM_TABLE": kn_weak_table,
        "KNOWLEDGE_CHART": kn_chart,
        "ASSESSMENT": assessment,
        "RECOMMENDATIONS": recommendations,
        "SUBJECT_RADAR_CHART": subject_radar,
        "TREND_LINE_CHART": trend_line,
        "TOTAL_TREND_CHART": total_trend,
        "TOTAL_SCORE": _fmt(last_total),
        "CLASS_RANK": _rank_label(last_rank, n_class or 0),
        "GRADE_RANK": _rank_label(last_rank, n_class or 0),
        "SUBJECT_TABLE": score_summary_table,
        "SUMMARY": f"<p>{assessment}</p><p>{contribution_insight}</p>",
    }


__all__ = ["build_student_exam_data"]
