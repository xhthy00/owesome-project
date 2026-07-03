"""综合分析报告数据组装——从原始记录一次性产出模板所需的全部字段。

设计动机：综合报告含 9 个维度、~25 个 data key。若让 LLM 逐个调统计/图表
工具再拼 data 字典，极易因 JSON 过大或漏 tool 外壳而失败（见 tool_action 兜底）。
故提供 ``build_comprehensive_data``：输入原始记录，输出可直接喂给
``render_html_report(data=...)`` 的完整字典——KPI 网格 / 图表 JSON / 表格 HTML /
洞察文本 / 学生档案表全部就绪，LLM 只需 query → 本函数 → render_html_report。
"""

from __future__ import annotations

from typing import Any

from src.agent.education.charts import build_chart_option
from src.agent.education.config import EducationConfig
from src.agent.education.stats import (
    compute_correlations,
    compute_imbalance_degree,
    compute_level_distribution,
    compute_subject_extremes,
    compute_top_progress_regress,
    compute_trend_distribution,
    pearson_r,
)

# 考试名别名组：exam_order 与 SQL 返回名不一致时（如「三模」vs「第三次考试」）做模糊匹配
_EXAM_ALIAS_GROUPS: list[list[str]] = [
    ["一模", "第一次", "第一次考试", "第1次", "第一次模拟", "第一次模拟考"],
    ["二模", "第二次", "第二次考试", "第2次", "第二次模拟", "第二次模拟考"],
    ["三模", "第三次", "第三次考试", "第3次", "第三次模拟", "第三次模拟考"],
]


def _normalize_student_key(name: str) -> str:
    return "".join(str(name or "").split())


def _record_subject_sum(record: dict[str, Any]) -> float:
    subs = record.get("subjects") or {}
    vals: list[float] = []
    for v in subs.values():
        if v is None:
            continue
        try:
            vals.append(float(v))
        except (TypeError, ValueError):
            continue
    return sum(vals)


def _record_effective_total(record: dict[str, Any]) -> float | None:
    """有效总分：优先各科之和；仅当无科目时用 total 字段。"""
    sub_sum = _record_subject_sum(record)
    if sub_sum > 0:
        return sub_sum
    total = record.get("total")
    if total is None:
        return None
    try:
        t = float(total)
    except (TypeError, ValueError):
        return None
    return t if t > 0 else None


def _normalize_records(records: list[dict[str, Any]]) -> None:
    for r in records:
        eff = _record_effective_total(r)
        r["total"] = eff if eff is not None else 0.0


def _match_exam_name(want: str, candidates: list[str], used: set[str]) -> str | None:
    want = str(want).strip()
    if want in candidates and want not in used:
        return want
    for group in _EXAM_ALIAS_GROUPS:
        if not any(want == a or want in a or a in want for a in group):
            continue
        for c in candidates:
            if c in used:
                continue
            if any(c == a or c in a or a in c for a in group):
                return c
    for c in candidates:
        if c in used:
            continue
        if want in c or c in want:
            return c
    return None


def _resolve_exams(record_exam_order: list[str], exam_order: list[str] | None) -> list[str]:
    """以 records 实际考试名为准；exam_order 仅用于排序/别名匹配，不引入无数据的考试。"""
    if not exam_order:
        return list(record_exam_order)
    used: set[str] = set()
    resolved: list[str] = []
    for want in exam_order:
        matched = _match_exam_name(want, record_exam_order, used)
        if matched:
            resolved.append(matched)
            used.add(matched)
    for e in record_exam_order:
        if e not in used:
            resolved.append(e)
    return resolved or list(record_exam_order)


def _find_student_row(
    by_exam: dict[str, list[dict[str, Any]]], exam: str, name: str
) -> dict[str, Any] | None:
    key = _normalize_student_key(name)
    for r in by_exam.get(exam, []):
        if _normalize_student_key(str(r.get("student") or "")) == key:
            return r
    return None


def _exam_has_valid_data(by_exam: dict[str, list[dict[str, Any]]], exam: str) -> bool:
    for r in by_exam.get(exam, []):
        if _record_effective_total(r) is not None:
            return True
    return False


def _fmt(v: Any) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.1f}" if v == int(v) else f"{v:.2f}"
    return str(v)


def _stat_card(value: str, label: str, accent: str = "") -> str:
    cls = f"stat-card {accent}" if accent else "stat-card"
    return f'<div class="{cls}"><div class="value">{value}</div><div class="label">{label}</div></div>'


def _kpi_grid(cards: list[str]) -> str:
    return f'<div class="stat-grid">{"".join(cards)}</div>'


def _insight(text: str, kind: str = "") -> str:
    cls = f"insight {kind}" if kind else "insight"
    return f'<div class="{cls}">{text}</div>'


def _table(headers: list[str], rows: list[list[str]]) -> str:
    head = "<tr>" + "".join(f"<th>{h}</th>" for h in headers) + "</tr>"
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<div class="table-wrap"><table>{head}{body}</table></div>'


def _level_label(t: float, upper: float) -> tuple[str, str]:
    if t >= upper * 0.85:
        return "A (优秀)", "#27ae60"
    if t >= upper * 0.70:
        return "B (良好)", "#2980b9"
    if t >= upper * 0.60:
        return "C (中等)", "#f39c12"
    return "D (待提升)", "#e74c3c"


def _trend_label(delta: float) -> str:
    if delta > 5:
        return f"📈 进步 ↑ (+{_fmt(delta)})"
    if delta < -5:
        return f"📉 退步 ↓ ({_fmt(delta)})"
    return f"📊 稳定 → ({_fmt(delta)})"


def _advice(strong: str, weak: str, delta: float, weak_avg: float | None) -> str:
    parts: list[str] = []
    subject_tips = {
        "语文": "加强文言文阅读和作文训练，多读经典文学作品，积累素材。",
        "数学": "多做综合题型练习，整理错题本，强化薄弱章节的理解。",
        "英语": "每天坚持背单词和阅读英文文章，加强听力和写作练习。",
    }
    weak_tip = subject_tips.get(weak, "回归基础，查漏补缺，针对性强化练习。")
    parts.append(f"【短板提升】{weak}是相对薄弱科目，建议{weak_tip}")
    parts.append(f"【保持优势】{strong}是优势科目，建议保持当前学习方法，适度拓展深度。")
    if delta > 5:
        parts.append("【趋势向好】总体呈进步趋势，说明学习方法有效，请继续坚持。")
    elif delta < -5:
        parts.append("【警惕下滑】总体呈退步趋势，建议认真复盘原因，调整学习计划和时间分配。")
    else:
        parts.append("【维持稳定】成绩稳定，建议在保持现状的基础上寻求突破。")
    if weak_avg is not None and weak_avg < 100:
        parts.append(f"【{weak}补基】{weak}均分低于100，需回归基础，查漏补缺。")
    return "；".join(parts)


def build_comprehensive_data(
    records: list[dict[str, Any]],
    exam_order: list[str],
    class_name: str = "",
    full_score: float | None = None,
    config: EducationConfig | None = None,
) -> dict[str, Any]:
    """从原始记录组装综合报告全量 data 字典。

    Args:
        records: 每条 ``{exam, student, subjects: {科目: 分数}, total}``。
        exam_order: 考试顺序（最早→最近），用于趋势/进退步。
        class_name: 班级名（用于封面）。
        full_score: 单科满分；总分满分按 full_score × 科目数推算，并自适应
            回退到数据中的最大总分，避免等级判定全员 D。
        config: 阈值配置；None 时取 config_store 默认。

    Returns:
        可直接作为 ``render_html_report`` 的 ``data`` 参数的字典；含模板全部 key。
    """
    if config is None:
        from src.agent.education.config_store import get_config
        config = get_config()
    _normalize_records(records)
    # 单科满分基准：优先用传入的 full_score；缺省时从数据推断单科最大分，
    # 避免用 config 默认 100 而数据实际单科满分是 150 时等级判定偏松。
    if full_score is not None:
        upper = float(full_score)
    else:
        _all_subject_scores = [
            float(v) for r in records for v in (r.get("subjects") or {}).values() if v is not None
        ]
        inferred = max(_all_subject_scores) if _all_subject_scores else config.default_full_score
        upper = max(inferred, config.default_full_score)
    # 考试顺序：以 records 实际考试名为准；exam_order 仅排序/别名匹配，不引入无数据考试。
    record_exam_order: list[str] = []
    _seen_exam: set[str] = set()
    for r in records:
        e = str(r.get("exam") or "")
        if e and e not in _seen_exam:
            _seen_exam.add(e)
            record_exam_order.append(e)
    subjects: list[str] = []
    seen: set[str] = set()
    for r in records:
        for sub in (r.get("subjects") or {}).keys():
            if sub not in seen:
                seen.add(sub)
                subjects.append(sub)

    # 按考试分组
    by_exam: dict[str, list[dict[str, Any]]] = {}
    for r in records:
        by_exam.setdefault(str(r.get("exam") or ""), []).append(r)
    exams = _resolve_exams(record_exam_order, exam_order or None)
    exams = [e for e in exams if _exam_has_valid_data(by_exam, e)] or list(record_exam_order)

    # ---- S1 班级整体概览 ----
    overview_cards: list[str] = []
    exam_avgs: list[float] = []
    exam_stdevs: list[float] = []
    for e in exams:
        rows = by_exam.get(e, [])
        totals = [t for r in rows if (t := _record_effective_total(r)) is not None]
        avg = sum(totals) / len(totals) if totals else 0
        stdev = _stdev(totals)
        exam_avgs.append(avg)
        exam_stdevs.append(stdev)
        overview_cards.append(_stat_card(_fmt(avg), f"{e} 班级总分均分"))
    if len(exam_avgs) >= 2:
        delta = exam_avgs[-1] - exam_avgs[0]
        overview_cards.append(_stat_card(f"{'+' if delta >= 0 else ''}{_fmt(delta)}", "首→末 总分变化", "accent2"))
    for e, sd in zip(exams, exam_stdevs):
        overview_cards.append(_stat_card(_fmt(sd), f"{e} 总分标准差"))
    if len(exam_stdevs) >= 2:
        sd_delta = exam_stdevs[-1] - exam_stdevs[0]
        overview_cards.append(
            _stat_card(f"{'+' if sd_delta >= 0 else ''}{_fmt(sd_delta)}", "标准差变化 (分化趋势)", "accent3")
        )
    overview_insight = (
        f"三次考试班级总分均分在 {_fmt(exam_avgs[0])}–{_fmt(exam_avgs[-1])} 之间。"
        + (f"标准差从 {_fmt(exam_stdevs[0])} 变化到 {_fmt(exam_stdevs[-1])}，"
           f"{'班级内部分化在加剧，需重点关注后进生' if exam_stdevs[-1] > exam_stdevs[0] else '班级分化有所收敛'}。"
           if len(exam_stdevs) >= 2 else "")
    )

    # ---- S2 各科成绩趋势 ----
    subject_trend_series = []
    subject_compare_metrics = []
    subject_kpi_cards: list[str] = []
    for sub in subjects:
        per_exam_avg: list[float] = []
        for e in exams:
            rows = by_exam.get(e, [])
            vals = [float((r.get("subjects") or {}).get(sub)) for r in rows
                    if (r.get("subjects") or {}).get(sub) is not None]
            per_exam_avg.append(round(sum(vals) / len(vals), 1) if vals else 0)
        subject_trend_series.append({"name": sub, "values": per_exam_avg})
        subject_compare_metrics.append({"name": sub, "values": per_exam_avg})
        if len(per_exam_avg) >= 2:
            d = per_exam_avg[-1] - per_exam_avg[0]
            accent = "accent1" if d < 0 else "accent2"
            subject_kpi_cards.append(
                _stat_card(f"{_fmt(per_exam_avg[0])}→{_fmt(per_exam_avg[-1])}", f"{sub}均分变化 ({'+' if d >= 0 else ''}{_fmt(d)})", accent)
            )
    # 总分趋势线（虚线）
    subject_trend_series.append({"name": "总分", "values": [round(a, 1) for a in exam_avgs]})
    subject_trend_chart = build_chart_option(
        "trend_line", {"x_labels": exams, "series": subject_trend_series}, "三次考试班级各科平均分趋势"
    )
    subject_compare_chart = build_chart_option(
        "subject_bar", {"subjects": subjects, "metrics": subject_compare_metrics}, "三次考试班级各科平均分对比"
    )
    # 洞察：找下降最多和上升最多的科目
    subject_deltas = [(sub, per[-1] - per[0]) for sub, per in
                      [(s["name"], s["values"]) for s in subject_trend_series if s["name"] != "总分"]
                      if len(per) >= 2]
    warning_insight = "各科均分相对稳定，无明显下滑。"
    success_insight = "各科均分相对稳定。"
    if subject_deltas:
        subject_deltas.sort(key=lambda x: x[1])
        worst_sub, worst_d = subject_deltas[0]
        best_sub, best_d = subject_deltas[-1]
        if worst_d < 0:
            warning_insight = f"{worst_sub}平均分下降 {_fmt(abs(worst_d))} 分，是全科中降幅最大的科目，需探究试卷难度及教学效果。"
        if best_d > 0:
            success_insight = f"{best_sub}平均分提升 {_fmt(best_d)} 分，班级整体{best_sub}水平进步明显。"

    # ---- S3 相关性 ----
    corr = compute_correlations(records)
    correlation_chart = build_chart_option(
        "correlation_bar",
        {"subjects": corr["subjects"], "series": corr["series"]},
        "各科与总分的相关性 (Pearson r)",
    )
    corr_insight_parts: list[str] = []
    for sub in corr["subjects"]:
        rs = [s["values"][i] for i, s in enumerate(corr["series"]) for _ in [None]
              if i < len(corr["subjects"]) and corr["subjects"][i] == sub]
        # 简化：取该科目在 series 中的索引
        idx = corr["subjects"].index(sub) if sub in corr["subjects"] else -1
        rs = [s["values"][idx] for s in corr["series"] if idx < len(s["values"])]
        valid_rs = [r for r in rs if r is not None]
        if valid_rs:
            avg_r = sum(valid_rs) / len(valid_rs)
            tag = "对总分排名影响最大" if avg_r > 0.5 else ("呈负相关，值得教学团队高度重视" if avg_r < 0 else "相关性较弱")
            corr_insight_parts.append(f"{sub}与总分平均相关性 r≈{_fmt(avg_r)}，{tag}")
    correlation_insight = "；".join(corr_insight_parts) or "样本不足，相关性分析结果仅供参考。"

    # ---- S4 趋势分布与水平分布 ----
    # 每位学生首末总分 delta
    student_first_total: dict[str, float] = {}
    student_last_total: dict[str, float] = {}
    if exams:
        first_exam, last_exam = exams[0], exams[-1]
        for r in records:
            name = str(r.get("student") or "")
            e = str(r.get("exam") or "")
            eff = _record_effective_total(r)
            if eff is None:
                continue
            if e == first_exam:
                student_first_total[name] = eff
            if e == last_exam:
                student_last_total[name] = eff
    deltas = [
        {"name": n, "delta": student_last_total.get(n, 0) - student_first_total.get(n, 0)}
        for n in set(student_first_total) | set(student_last_total)
    ]
    trend_dist = compute_trend_distribution(deltas)
    trend_dist_chart = build_chart_option(
        "pie", {"items": trend_dist["items"]}, "学生成绩趋势分布 (进步/退步/稳定)"
    )
    last_totals = list(student_last_total.values())
    # 水平判定满分：full_score 按单科满分理解，总分满分 = full_score × 科目数。
    # 但 LLM 可能误把总分满分当 full_score 传入（再乘科目数会得到远超实际总分
    # 的"满分"，导致所有人 < 60% 全判 D）。故加自适应回退：当候选满分明显超过
    # 数据中的最大总分时，改用最大总分作为满分基准（相对等级），保证不会全员 D。
    all_totals = [t for r in records if (t := _record_effective_total(r)) is not None]
    max_total = max(all_totals) if all_totals else upper
    candidate_full = upper * len(subjects) if subjects else upper
    if candidate_full > 0 and candidate_full <= max_total * 1.3:
        level_full = candidate_full
    else:
        level_full = max_total if max_total > 0 else candidate_full
    level_items = compute_level_distribution(last_totals, config, full_score=level_full)
    level_dist_chart = build_chart_option("pie", {"items": level_items}, "学生总分水平分布")
    trend_dist_kpi = _kpi_grid([
        _stat_card(str(len(trend_dist["progress"])), "进步学生数", "accent2"),
        _stat_card(str(len(trend_dist["regress"])), "退步学生数", "accent1"),
        _stat_card(str(len(trend_dist["stable"])), "稳定学生数"),
    ])
    trend_dist_insight = (
        f"进步({len(trend_dist['progress'])}人)、退步({len(trend_dist['regress'])}人)、"
        f"稳定({len(trend_dist['stable'])}人)分布情况如上。"
    )

    # ---- S5 进步最快 & 退步最快 ----
    top = compute_top_progress_regress(deltas, top_n=5)
    progress_regress_chart = build_chart_option(
        "progress_regress_bar", {"items": top["chart_items"]}, "总分变化最大学生 (首次→末次)"
    )
    progress_rows = [[d.get("name", ""), _fmt(student_first_total.get(d.get("name", ""), 0)),
                      _fmt(student_last_total.get(d.get("name", ""), 0)),
                      f'<span style="color:#27ae60;font-weight:bold;">+{_fmt(d.get("delta", 0))}</span>']
                     for d in top["progress"]]
    regress_rows = [[d.get("name", ""), _fmt(student_first_total.get(d.get("name", ""), 0)),
                     _fmt(student_last_total.get(d.get("name", ""), 0)),
                     f'<span style="color:#e74c3c;font-weight:bold;">{_fmt(d.get("delta", 0))}</span>']
                    for d in top["regress"]]
    progress_table = _table(["学生", "第一次", "末次", "变化"], progress_rows)
    regress_table = _table(["学生", "第一次", "末次", "变化"], regress_rows)
    progress_insight = (f"进步之星 {top['progress'][0].get('name', '')} 总分提升 "
                        f"{_fmt(top['progress'][0].get('delta', 0))} 分。" if top["progress"] else "")
    regress_insight = (f"退步预警 {top['regress'][0].get('name', '')} 总分下降 "
                       f"{_fmt(top['regress'][0].get('delta', 0))} 分，需立即干预。"
                       if top["regress"] else "")

    # ---- S6 偏科生诊断 ----
    # 每位学生各科平均分
    student_subject_avgs: dict[str, dict[str, list[float]]] = {}
    for r in records:
        name = str(r.get("student") or "")
        student_subject_avgs.setdefault(name, {})
        for sub, val in (r.get("subjects") or {}).items():
            if val is None:
                continue
            student_subject_avgs[name].setdefault(sub, []).append(float(val))
    imbalance_input = [
        {"name": n, "subjects": {sub: round(sum(v) / len(v), 1) for sub, v in subs.items()}}
        for n, subs in student_subject_avgs.items()
    ]
    imbalance = compute_imbalance_degree(imbalance_input, top_n=10, min_degree=7.0)
    imbalance_chart_series = [{"name": sub, "values": [s["subjects"].get(sub, 0) for s in imbalance]}
                              for sub in subjects]
    imbalance_chart = build_chart_option(
        "subject_bar",
        {"subjects": [f'{s["name"]}\n({s["strong_subject"]}强/{s["weak_subject"]}弱)' for s in imbalance],
         "metrics": imbalance_chart_series},
        "偏科生三科均分对比 (偏科度>7)",
    ) if imbalance else ""
    imbalance_rows = [
        [f'<strong>{s["name"]}</strong>',
         f'<span style="color:#e74c3c;font-weight:bold;">{_fmt(s["degree"])}</span>',
         f'<span style="color:#27ae60;">{s["strong_subject"]}</span>',
         f'<span style="color:#e74c3c;">{s["weak_subject"]}</span>',
         _fmt(s["subjects"].get(s["strong_subject"], 0)),
         _fmt(s["subjects"].get(s["weak_subject"], 0)),
         f'{s["strong_subject"]}强{s["weak_subject"]}弱']
        for s in imbalance
    ]
    imbalance_table = _table(["学生", "偏科度", "优势科目", "劣势科目", "优势均分", "劣势均分", "诊断"], imbalance_rows)
    imbalance_insight = (
        f"偏科度最高的是 {imbalance[0]['name']}（偏科度 {imbalance[0]['degree']}），"
        f"{imbalance[0]['strong_subject']}强而{imbalance[0]['weak_subject']}弱。"
        if imbalance else "未发现严重偏科学生。"
    )

    # ---- S7 单科进步/退步之最 ----
    # 每位学生每科首末 delta
    sub_deltas: list[dict[str, Any]] = []
    for name, subs in student_subject_avgs.items():
        for sub in subs:
            first_vals: list[float] = []
            last_vals: list[float] = []
            for r in records:
                if str(r.get("student") or "") != name:
                    continue
                if str(r.get("exam") or "") == exams[0]:
                    v = (r.get("subjects") or {}).get(sub)
                    if v is not None:
                        first_vals.append(float(v))
                if str(r.get("exam") or "") == exams[-1]:
                    v = (r.get("subjects") or {}).get(sub)
                    if v is not None:
                        last_vals.append(float(v))
            if first_vals and last_vals:
                sub_deltas.append({
                    "name": name, "subject": sub,
                    "delta": (sum(last_vals) / len(last_vals)) - (sum(first_vals) / len(first_vals)),
                })
    ext = compute_subject_extremes(sub_deltas, top_n=5)
    subject_extreme_chart = build_chart_option(
        "subject_extreme_bar", {"items": ext["chart_items"]}, "单科进步/退步最大 (首次→末次)"
    ) if ext["chart_items"] else ""
    sp_rows = [[d.get("name", ""), d.get("subject", ""), _fmt(d.get("delta", 0))]
               for d in ext["progress"]]
    sr_rows = [[d.get("name", ""), d.get("subject", ""), _fmt(d.get("delta", 0))]
               for d in ext["regress"]]
    subject_progress_table = _table(["学生", "科目", "变化"], sp_rows)
    subject_regress_table = _table(["学生", "科目", "变化"], sr_rows)
    regress_subs = [d.get("subject") for d in ext["regress"]]
    subject_extreme_insight = (
        f"单科退步 TOP5 集中在 {', '.join(set(regress_subs))}，需重点关注。"
        if regress_subs else "单科变化不明显。"
    )

    # ---- S8 全体学生总分轨迹 ----
    trajectory_series = []
    for name in sorted(set(str(r.get("student") or "") for r in records)):
        vals: list[float] = []
        for e in exams:
            row = _find_student_row(by_exam, e, name)
            eff = _record_effective_total(row) if row else None
            vals.append(eff if eff is not None else 0)
        trajectory_series.append({"name": name, "values": vals})
    trajectory_chart = build_chart_option(
        "trajectory_line", {"x_labels": exams, "series": trajectory_series}, "全体学生总分变化轨迹"
    )
    trajectory_note = "点击图例可切换显示学生；头部学生保持在高分区间，中部学生波动较大。"

    # ---- S9 每位学生详细档案与建议 ----
    archive_rows: list[list[str]] = []
    for name in sorted(student_first_total.keys() | student_last_total.keys()):
        subs_avg = {sub: round(sum(v) / len(v), 1) for sub, v in student_subject_avgs.get(name, {}).items()}
        ordered = sorted(subs_avg.items(), key=lambda kv: float(kv[1]), reverse=True)
        strong = ordered[0][0] if ordered else ""
        weak = ordered[-1][0] if ordered else ""
        weak_avg = ordered[-1][1] if ordered else None
        delta = student_last_total.get(name, 0) - student_first_total.get(name, 0)
        # 均分取该学生所有考试总分的平均（早期实现仅用首末两次，会与各次总分对不上）
        per_exam_total_vals: list[float] = []
        per_exam_totals = []
        for e in exams:
            row = _find_student_row(by_exam, e, name)
            eff = _record_effective_total(row) if row else None
            if eff is not None:
                per_exam_total_vals.append(eff)
                per_exam_totals.append(_fmt(eff))
            else:
                per_exam_totals.append("-")
        avg_total = (
            round(sum(per_exam_total_vals) / len(per_exam_total_vals), 1)
            if per_exam_total_vals else 0
        )
        level_label, level_color = _level_label(student_last_total.get(name, 0), level_full)
        imb = next((s for s in imbalance if s["name"] == name), None)
        degree = imb["degree"] if imb else "-"
        archive_rows.append([
            f"<strong>{name}</strong>",
            f'<span style="background:{level_color};color:white;padding:2px 10px;border-radius:12px;font-size:12px;">{level_label}</span>',
            *per_exam_totals,
            _fmt(avg_total),
            f'<span style="color:#7f8c8d;font-weight:bold;">{_trend_label(delta)}</span>',
            f'<span style="color:#27ae60;font-weight:bold;">{strong}</span>',
            f'<span style="color:#e74c3c;font-weight:bold;">{weak}</span>',
            _fmt(degree),
            _advice(strong, weak, delta, weak_avg),
        ])
    student_archive_table = _table(
        ["姓名", "水平", *exams, "均分", "趋势", "优势", "劣势", "偏科度", "个性化建议"],
        archive_rows,
    )

    cover_title = f"{class_name}综合分析报告" if class_name else "综合分析报告"
    cover_subtitle = f"基于 {('、'.join(exams))} 数据的深度学业诊断"
    cover_meta = (
        f"班级: {class_name or '未指定'} | 科目: {' / '.join(subjects)} | "
        f"分析维度: 总分水平 · 趋势变化 · 优势劣势 · 偏科诊断 · 相关性 · 个性化建议"
    )

    return {
        "COVER_TITLE": cover_title,
        "COVER_SUBTITLE": cover_subtitle,
        "COVER_META": cover_meta,
        "REPORT_TIME": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M"),
        "OVERVIEW_KPI_GRID": _kpi_grid(overview_cards),
        "OVERVIEW_INSIGHT": overview_insight,
        "SUBJECT_TREND_CHART": subject_trend_chart,
        "SUBJECT_COMPARE_CHART": subject_compare_chart,
        "SUBJECT_KPI_GRID": _kpi_grid(subject_kpi_cards),
        "SUBJECT_WARNING_INSIGHT": warning_insight,
        "SUBJECT_SUCCESS_INSIGHT": success_insight,
        "CORRELATION_CHART": correlation_chart,
        "CORRELATION_INSIGHT": correlation_insight,
        "TREND_DIST_CHART": trend_dist_chart,
        "LEVEL_DIST_CHART": level_dist_chart,
        "TREND_DIST_KPI_GRID": trend_dist_kpi,
        "TREND_DIST_INSIGHT": trend_dist_insight,
        "PROGRESS_REGRESS_CHART": progress_regress_chart,
        "PROGRESS_TABLE": progress_table,
        "REGRESS_TABLE": regress_table,
        "PROGRESS_INSIGHT": progress_insight,
        "REGRESS_INSIGHT": regress_insight,
        "IMBALANCE_CHART": imbalance_chart,
        "IMBALANCE_TABLE": imbalance_table,
        "IMBALANCE_INSIGHT": imbalance_insight,
        "SUBJECT_EXTREME_CHART": subject_extreme_chart,
        "SUBJECT_PROGRESS_TABLE": subject_progress_table,
        "SUBJECT_REGRESS_TABLE": subject_regress_table,
        "SUBJECT_EXTREME_INSIGHT": subject_extreme_insight,
        "TRAJECTORY_CHART": trajectory_chart,
        "TRAJECTORY_NOTE": trajectory_note,
        "STUDENT_ARCHIVE_TABLE": student_archive_table,
    }


def _stdev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    import statistics
    return statistics.pstdev(values)


__all__ = ["build_comprehensive_data"]
