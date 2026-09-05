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
from src.agent.education.report_types import ReportType, report_type_label
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
    from src.agent.education.query_parse import student_matches

    for r in by_exam.get(exam, []):
        if student_matches(str(r.get("student") or ""), name):
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


def _short_exam_label(exam: str) -> str:
    """长考试名压缩为表头短标签（摸底/调研/联考/一模等）。"""
    import re

    e = str(exam or "").strip()
    if not e:
        return "-"
    keys = ("一模", "二模", "三模", "摸底", "调研", "联考", "期末", "期中", "月考", "会考")
    hit = next((k for k in keys if k in e), None)
    m = re.search(r"(\d{1,2})[-./月](\d{1,2})", e)
    date = f"{int(m.group(1)):02d}-{int(m.group(2)):02d}" if m else ""
    if hit:
        return f"{date} {hit}".strip() if date else hit
    return e if len(e) <= 10 else e[:8] + "…"


_PLACEHOLDER_SUBJECTS = frozenset({"成绩", "总分", "score", "total", ""})


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
        return f"进步 ↑ (+{_fmt(delta)})"
    if delta < -5:
        return f"退步 ↓ ({_fmt(delta)})"
    return f"稳定 → ({_fmt(delta)})"


def _advice(
    strong: str,
    weak: str,
    delta: float,
    weak_avg: float | None,
    *,
    item_insight: dict[str, Any] | None = None,
    single_subject: bool = False,
    avg_score: float | None = None,
) -> str:
    """生成个性化建议；有小题/知识点时优先据此。

    ``single_subject=True``（科目诊断）：不按多科优劣势叙事，强制以知识点/小题得分驱动。
    """
    parts: list[str] = []
    insight = item_insight or {}
    weak_items = list(insight.get("weak_items") or [])
    weak_know = list(insight.get("weak_knowledge") or [])
    strong_know = list(insight.get("strong_knowledge") or [])

    if weak_items or weak_know:
        if weak_items:
            by_exam: dict[str, list[dict[str, Any]]] = {}
            for it in weak_items:
                lab = _short_exam_label(str(it.get("exam_name") or insight.get("exam_name") or "考试"))
                by_exam.setdefault(lab, []).append(it)
            for lab, items in by_exam.items():
                qbits = []
                for it in items[:4]:
                    qno = it.get("question_no")
                    kn = str(it.get("knowledge_name") or "").strip()
                    rate = it.get("score_rate")
                    bit = f"第{qno}题"
                    if kn and kn != "未关联知识点":
                        bit += f"（{kn}）"
                    if rate is not None:
                        try:
                            bit += f"得分率{_fmt(float(rate))}%"
                        except (TypeError, ValueError):
                            pass
                    qbits.append(bit)
                if qbits:
                    if single_subject and lab and lab not in ("-", "考试"):
                        prefix = f"【小题补漏·{lab}】"
                    elif single_subject:
                        prefix = "【小题补漏】"
                    else:
                        prefix = f"【小题补漏·{lab}】"
                    parts.append(f"{prefix}重点攻克：{'；'.join(qbits)}。")
        if weak_know:
            names = [str(k.get("knowledge_name") or k) for k in weak_know[:5]]
            names = [n for n in names if n and n != "未关联知识点"]
            if names:
                tag = "【知识点薄弱】" if single_subject else "【知识点薄弱·历次】"
                parts.append(
                    f"{tag}优先复习：{'、'.join(names)}。"
                    "建议回归课本例题与变式训练。"
                )
        if strong_know:
            names = [str(k.get("knowledge_name") or k) for k in strong_know[:3]]
            names = [n for n in names if n and n != "未关联知识点"]
            if names:
                parts.append(f"【掌握较好】{'、'.join(names)}得分率较高，可做拓展拔高题。")
        if single_subject:
            parts.append("【执行建议】本周先过关薄弱知识点对应基础题，下周用同类变式复测。")
    elif single_subject:
        if avg_score is not None:
            parts.append(
                f"【成绩定位】本次得分 {_fmt(avg_score)}，建议结合错题本复盘失分题，"
                "对照知识点清单逐项过关。"
            )
        else:
            parts.append(
                "【学习建议】暂未加载到该生小题/知识点得分明细，"
                "请核对 tb_score_detail；可先复盘课堂笔记与错题。"
            )
    else:
        subject_tips = {
            "语文": "加强文言文阅读和作文训练，积累素材。",
            "数学": "整理错题本，针对薄弱题型做变式练习。",
            "英语": "坚持词汇与阅读，强化听力与写作。",
        }
        if weak and weak not in _PLACEHOLDER_SUBJECTS:
            tip = subject_tips.get(weak, "回归基础，查漏补缺，针对性强化练习。")
            parts.append(f"【短板提升】{weak}相对薄弱，建议{tip}")
        if strong and strong not in _PLACEHOLDER_SUBJECTS and strong != weak:
            parts.append(f"【保持优势】{strong}表现较好，保持方法并适度拓展。")
        if not parts:
            parts.append("【学习建议】结合错题与课堂笔记复盘薄弱环节，制定每周专项训练。")

    if not single_subject:
        if delta > 5:
            parts.append("【趋势向好】总分呈进步趋势，请巩固有效学习节奏。")
        elif delta < -5:
            parts.append("【警惕下滑】总分呈退步趋势，建议复盘近期错题与时间分配。")
        else:
            parts.append("【维持稳定】成绩相对平稳，可在薄弱题型上寻求突破。")
        if weak_avg is not None and weak and weak not in _PLACEHOLDER_SUBJECTS and weak_avg < 90:
            parts.append(f"【{weak}补基】均分 {_fmt(weak_avg)}，需加强基础题正确率。")

    return (
        '<ul class="advice-list">'
        + "".join(f"<li>{p}</li>" for p in parts)
        + "</ul>"
    )


def _build_student_archive_html(
    *,
    students: list[str],
    exams: list[str],
    by_exam: dict[str, list[dict[str, Any]]],
    student_first_total: dict[str, float],
    student_last_total: dict[str, float],
    student_subject_avgs: dict[str, dict[str, list[float]]],
    imbalance: list[dict[str, Any]],
    level_full: float,
    student_item_insights: dict[str, dict[str, Any]] | None = None,
    single_subject: bool = False,
) -> str:
    """卡片式学生档案：短考试标签 + 小题驱动建议，避免宽表空白。"""
    insights = student_item_insights or {}
    exam_labels = [_short_exam_label(e) for e in exams]
    cards: list[str] = []
    for name in students:
        insight = insights.get(name) or insights.get(_normalize_student_key(name)) or {}
        # 兼容 student_id 与脱敏姓名键
        if not insight:
            for k, v in insights.items():
                if _normalize_student_key(k) == _normalize_student_key(name):
                    insight = v
                    break

        subs_avg = {
            sub: round(sum(v) / len(v), 1)
            for sub, v in student_subject_avgs.get(name, {}).items()
        }
        ordered = sorted(subs_avg.items(), key=lambda kv: float(kv[1]), reverse=True)
        # 单科占位名不作为优/劣势展示；有知识点则用知识点
        real_subs = [(s, a) for s, a in ordered if s not in _PLACEHOLDER_SUBJECTS]
        if single_subject:
            strong, weak, weak_avg = "", "", None
        elif real_subs:
            strong, weak = real_subs[0][0], real_subs[-1][0]
            weak_avg = real_subs[-1][1]
        elif insight.get("strong_knowledge") or insight.get("weak_knowledge"):
            sk = insight.get("strong_knowledge") or []
            wk = insight.get("weak_knowledge") or []
            strong = str((sk[0].get("knowledge_name") if sk and isinstance(sk[0], dict) else sk[0]) if sk else "")
            weak = str((wk[0].get("knowledge_name") if wk and isinstance(wk[0], dict) else wk[0]) if wk else "")
            weak_avg = None
        else:
            strong, weak, weak_avg = "", "", None

        delta = student_last_total.get(name, 0) - student_first_total.get(name, 0)
        score_chips: list[str] = []
        per_exam_vals: list[float] = []
        for e, lab in zip(exams, exam_labels):
            row = _find_student_row(by_exam, e, name)
            eff = _record_effective_total(row) if row else None
            if eff is not None:
                per_exam_vals.append(eff)
                score_chips.append(
                    f'<span class="score-chip"><em>{lab}</em><b>{_fmt(eff)}</b></span>'
                )
            else:
                score_chips.append(
                    f'<span class="score-chip muted"><em>{lab}</em><b>-</b></span>'
                )
        avg_total = (
            round(sum(per_exam_vals) / len(per_exam_vals), 1) if per_exam_vals else 0
        )
        level_label, level_color = _level_label(student_last_total.get(name, 0), level_full)
        imb = next((s for s in imbalance if s["name"] == name), None)
        degree = _fmt(imb["degree"]) if imb else "-"

        weak_item_tags = ""
        w_items = list(insight.get("weak_items") or [])
        if w_items:
            tags = []
            for it in w_items[:6]:
                qno = it.get("question_no")
                kn = str(it.get("knowledge_name") or "").strip()
                elab = _short_exam_label(str(it.get("exam_name") or ""))
                label = f"{elab}·第{qno}题" if elab and elab not in ("-", "") else f"第{qno}题"
                if kn and kn != "未关联知识点":
                    label += f"·{kn}"
                tags.append(f'<span class="weak-tag">{label}</span>')
            weak_item_tags = (
                '<div class="archive-tags"><span class="tags-label">薄弱小题</span>'
                + "".join(tags)
                + "</div>"
            )

        weak_know_tags = ""
        w_know = list(insight.get("weak_knowledge") or [])
        if single_subject and w_know:
            kn_tags = []
            for k in w_know[:5]:
                kn = str(k.get("knowledge_name") or k).strip()
                if not kn or kn == "未关联知识点":
                    continue
                rate = k.get("score_rate") if isinstance(k, dict) else None
                label = kn
                if rate is not None:
                    try:
                        label += f" {float(rate):.0f}%"
                    except (TypeError, ValueError):
                        pass
                kn_tags.append(f'<span class="weak-tag">{label}</span>')
            if kn_tags:
                weak_know_tags = (
                    '<div class="archive-tags"><span class="tags-label">薄弱知识点</span>'
                    + "".join(kn_tags)
                    + "</div>"
                )

        meta_bits = [
            f'<span>得分 <b>{_fmt(avg_total)}</b></span>',
            f'<span>{_trend_label(delta)}</span>',
        ]
        if not single_subject:
            strong_disp = strong or "—"
            weak_disp = weak or "—"
            meta_bits.extend(
                [
                    f'<span>优势 <b class="ok">{strong_disp}</b></span>',
                    f'<span>待提升 <b class="bad">{weak_disp}</b></span>',
                    f'<span>偏科度 {degree}</span>',
                ]
            )

        advice_html = _advice(
            strong,
            weak,
            delta,
            weak_avg,
            item_insight=insight or None,
            single_subject=single_subject,
            avg_score=avg_total if per_exam_vals else None,
        )
        cards.append(
            f'<article class="archive-card">'
            f'<header class="archive-head">'
            f'<div class="archive-name"><strong>{name}</strong>'
            f'<span class="level-pill" style="background:{level_color}">{level_label}</span></div>'
            f'<div class="archive-meta">{"".join(meta_bits)}</div></header>'
            f'<div class="score-row">{"".join(score_chips)}</div>'
            f'{weak_know_tags}'
            f'{weak_item_tags}'
            f'<div class="archive-advice">{advice_html}</div>'
            f"</article>"
        )

    if not cards:
        return (
            '<p class="insight warning">暂无学生明细：请确认 SQL 已返回 '
            "student_id/姓名、exam、score 等逐人成绩行（勿仅查班级 KPI 聚合）。</p>"
        )
    if any(insights.get(s) or insights.get(_normalize_student_key(s)) for s in students):
        if single_subject:
            note = (
                '<p class="archive-note">个性化建议已结合<strong>本场考试</strong>该科小题得分率与知识点掌握情况；'
                "得分率偏低的知识点与题目列入补漏清单。</p>"
            )
        else:
            note = (
                '<p class="archive-note">个性化建议已结合<strong>全部考试</strong>的小题得分率与知识点掌握情况；'
                "得分率低于阈值的题目按考试分别列入补漏清单。</p>"
            )
    elif single_subject:
        note = (
            '<p class="archive-note">未加载到逐人小题/知识点明细时，仅展示得分；'
            "系统将尝试从 tb_score_detail 拉取每位学生明细以生成知识点建议。</p>"
        )
    else:
        note = (
            '<p class="archive-note">未加载到小题明细时，建议基于总分趋势生成；'
            "导入 tb_score_detail 后可自动融入历次考试薄弱小题与知识点。</p>"
        )
    return note + f'<div class="archive-grid">{"".join(cards)}</div>'


def build_student_archive_from_score_rows(
    rows: list[dict[str, Any]],
    *,
    exam_name: str = "",
    full_score: float | None = None,
    student_item_insights: dict[str, dict[str, Any]] | None = None,
    single_subject: bool = False,
) -> str:
    """从成绩明细行组装「每位学生详细档案与个性化建议」HTML。

    供 ``class_overview`` / 综合报告 / ``subject_diagnosis``（``single_subject=True``）共用。
    行字段兼容：``student`` / ``student_id`` / ``name``、``score``、
    ``subject`` / ``subject_name``、``exam`` / ``exam_name``。
    """
    if not rows:
        return (
            '<p class="edu-sub">暂无学生明细：请确认成绩查询已返回学号/姓名与分数'
            "（勿仅查班级 KPI 聚合）。</p>"
        )

    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        sid = str(
            r.get("student_name")
            or r.get("name")
            or r.get("xm")
            or r.get("student")
            or r.get("student_id")
            or ""
        ).strip()
        if not sid:
            continue
        exam = str(
            r.get("exam") or r.get("exam_name") or exam_name or "本次考试"
        ).strip() or "本次考试"
        subj = str(
            r.get("subject") or r.get("subject_name") or "全科"
        ).strip() or "全科"
        score = r.get("score")
        try:
            score_f = float(score) if score is not None else None
        except (TypeError, ValueError):
            score_f = None
        key = (sid, exam)
        rec = grouped.setdefault(
            key,
            {"student": sid, "exam": exam, "subjects": {}, "total": None},
        )
        if score_f is not None:
            # 同行已有同分科目时累加，兼容科目分+总分混排
            prev = rec["subjects"].get(subj)
            if prev is None:
                rec["subjects"][subj] = score_f
            else:
                rec["subjects"][subj] = max(float(prev), score_f)

    records = list(grouped.values())
    for rec in records:
        subs = rec.get("subjects") or {}
        if not subs:
            continue
        # 仅一科或含「总分」占位时用科目和作为总分
        if len(subs) == 1:
            rec["total"] = float(next(iter(subs.values())))
        else:
            rec["total"] = float(sum(float(v) for v in subs.values()))

    if not records:
        return (
            '<p class="edu-sub">暂无学生明细：成绩行缺少学号/姓名字段。</p>'
        )

    _normalize_records(records)
    by_exam: dict[str, list[dict[str, Any]]] = {}
    for r in records:
        by_exam.setdefault(str(r.get("exam") or ""), []).append(r)
    exams = [e for e in by_exam if _exam_has_valid_data(by_exam, e)]
    if not exams:
        exams = list(by_exam.keys())

    student_first_total: dict[str, float] = {}
    student_last_total: dict[str, float] = {}
    student_subject_avgs: dict[str, dict[str, list[float]]] = {}
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
            for sub, val in (r.get("subjects") or {}).items():
                try:
                    student_subject_avgs.setdefault(name, {}).setdefault(str(sub), []).append(
                        float(val)
                    )
                except (TypeError, ValueError):
                    continue

    imbalance: list[dict[str, Any]] = []
    for name, subs in student_subject_avgs.items():
        avgs = [sum(v) / len(v) for v in subs.values() if v]
        if len(avgs) >= 2:
            imbalance.append({"name": name, "degree": _stdev(avgs)})

    all_students = sorted(
        {str(r.get("student") or "") for r in records if str(r.get("student") or "").strip()}
    )
    level_full = float(full_score) if full_score else 100.0
    if full_score is None:
        all_scores = [
            float(v)
            for r in records
            for v in (r.get("subjects") or {}).values()
            if v is not None
        ]
        if all_scores:
            level_full = max(max(all_scores), 100.0)

    return _build_student_archive_html(
        students=all_students,
        exams=exams,
        by_exam=by_exam,
        student_first_total=student_first_total,
        student_last_total=student_last_total,
        student_subject_avgs=student_subject_avgs,
        imbalance=imbalance,
        level_full=level_full,
        student_item_insights=student_item_insights,
        single_subject=single_subject,
    )


def build_comprehensive_data(
    records: list[dict[str, Any]],
    exam_order: list[str],
    class_name: str = "",
    full_score: float | None = None,
    config: EducationConfig | None = None,
    student_item_insights: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """从原始记录组装综合报告全量 data 字典。

    Args:
        records: 每条 ``{exam, student, subjects: {科目: 分数}, total}``。
        exam_order: 考试顺序（最早→最近），用于趋势/进退步。
        class_name: 班级名（用于封面）。
        full_score: 单科满分；总分满分按 full_score × 科目数推算，并自适应
            回退到数据中的最大总分，避免等级判定全员 D。
        config: 阈值配置；None 时取 config_store 默认。
        student_item_insights: 可选，按学生键提供小题/知识点薄弱项，用于个性化建议。

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
        overview_cards.append(_stat_card(_fmt(avg), f"{_short_exam_label(e)} 班级总分均分"))
    if len(exam_avgs) >= 2:
        delta = exam_avgs[-1] - exam_avgs[0]
        overview_cards.append(_stat_card(f"{'+' if delta >= 0 else ''}{_fmt(delta)}", "首→末 总分变化", "accent2"))
    for e, sd in zip(exams, exam_stdevs):
        overview_cards.append(_stat_card(_fmt(sd), f"{_short_exam_label(e)} 总分标准差"))
    if len(exam_stdevs) >= 2:
        sd_delta = exam_stdevs[-1] - exam_stdevs[0]
        overview_cards.append(
            _stat_card(f"{'+' if sd_delta >= 0 else ''}{_fmt(sd_delta)}", "标准差变化 (分化趋势)", "accent3")
        )
    overview_insight = (
        f"历次考试班级总分均分在 {_fmt(exam_avgs[0])}–{_fmt(exam_avgs[-1])} 之间。"
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
        "trend_line",
        {"x_labels": [_short_exam_label(e) for e in exams], "series": subject_trend_series},
        "历次考试班级各科平均分趋势",
    )
    subject_compare_chart = build_chart_option(
        "subject_bar", {"subjects": subjects, "metrics": subject_compare_metrics}, "历次考试班级各科平均分对比"
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
        idx = corr["subjects"].index(sub) if sub in corr["subjects"] else -1
        rs = [s["values"][idx] for s in corr["series"] if idx < len(s["values"])]
        valid_rs = [r for r in rs if r is not None]
        if valid_rs:
            avg_r = sum(valid_rs) / len(valid_rs)
            tag = "对总分排名影响最大" if avg_r > 0.5 else ("呈负相关，值得教学团队高度重视" if avg_r < 0 else "相关性较弱")
            corr_insight_parts.append(f"{sub}与总分平均相关性 r≈{_fmt(avg_r)}，{tag}")
    correlation_insight = "；".join(corr_insight_parts) or "样本不足，相关性分析结果仅供参考。"

    # ---- S4 趋势分布与水平分布 ----
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
    if not progress_rows:
        progress_rows = [["—", "—", "—", "暂无总分进步学生"]]
    if not regress_rows:
        regress_rows = [["—", "—", "—", "暂无总分退步学生"]]
    progress_table = _table(["学生", "首次考试", "末次考试", "变化"], progress_rows)
    regress_table = _table(["学生", "首次考试", "末次考试", "变化"], regress_rows)
    progress_insight = (f"进步之星 {top['progress'][0].get('name', '')} 总分提升 "
                        f"{_fmt(top['progress'][0].get('delta', 0))} 分。" if top["progress"] else
                        "各生总分相对稳定，未出现显著进步个体。")
    regress_insight = (f"退步预警 {top['regress'][0].get('name', '')} 总分下降 "
                       f"{_fmt(top['regress'][0].get('delta', 0))} 分，需立即干预。"
                       if top["regress"] else "未出现显著退步个体。")

    # ---- S6 偏科生诊断 ----
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
        f"单科退步 TOP5 集中在 {', '.join(set(str(s) for s in regress_subs if s))}，需重点关注。"
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
        "trajectory_line",
        {"x_labels": [_short_exam_label(e) for e in exams], "series": trajectory_series},
        "全体学生总分变化轨迹",
    )
    trajectory_note = "点击图例可切换显示学生；头部学生保持在高分区间，中部学生波动较大。"

    # ---- S9 每位学生详细档案与建议 ----
    all_students = sorted(
        {
            str(r.get("student") or "")
            for r in records
            if str(r.get("student") or "").strip()
        }
    )
    # 给 insight 打上短考试标签（多场考试时 exam_label 为「历次考试」）
    enriched_insights: dict[str, dict[str, Any]] = {}
    for k, v in (student_item_insights or {}).items():
        vv = dict(v)
        exam_names = list(vv.get("exam_names") or [])
        if not exam_names and vv.get("exam_name"):
            exam_names = [str(vv["exam_name"])]
        if len(exam_names) > 1:
            vv["exam_label"] = "历次考试"
        else:
            vv.setdefault(
                "exam_label",
                _short_exam_label(str(exam_names[0] if exam_names else (exams[-1] if exams else ""))),
            )
        enriched_insights[k] = vv
    student_archive_table = _build_student_archive_html(
        students=all_students,
        exams=exams,
        by_exam=by_exam,
        student_first_total=student_first_total,
        student_last_total=student_last_total,
        student_subject_avgs=student_subject_avgs,
        imbalance=imbalance,
        level_full=level_full,
        student_item_insights=enriched_insights,
    )

    cover_title = f"{class_name}综合分析报告" if class_name else "综合分析报告"
    cover_subtitle = f"基于 {('、'.join(_short_exam_label(e) for e in exams))} 数据的深度学业诊断"
    cover_meta = (
        f"班级: {class_name or '未指定'} | 科目: {' / '.join(subjects)} | "
        f"分析维度: 总分水平 · 趋势变化 · 优势劣势 · 偏科诊断 · 相关性 · 个性化建议"
    )

    return {
        "COVER_TITLE": cover_title,
        "COVER_SUBTITLE": cover_subtitle,
        "COVER_META": cover_meta,
        "REPORT_TYPE": report_type_label(ReportType.COMPREHENSIVE),
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


def aggregate_student_item_insights(
    detail_rows: list[dict[str, Any]],
    *,
    weak_threshold: float = 60.0,
    exam_name: str = "",
) -> dict[str, dict[str, Any]]:
    """将逐人小题行聚合为 ``{student_id: insight}``（支持多场考试）。

    每行需含 ``student_id``（或 student）、``question_no``、``score_rate``、
    可选 ``knowledge_name`` / ``exam_name`` / ``subject_name``。
    有科目时按 (科目, 知识点) 聚合，避免多学科混在同一行；多场考试时薄弱小题按考试分别保留。
    """
    by_stu: dict[str, list[dict[str, Any]]] = {}
    for row in detail_rows:
        sid = str(row.get("student_id") or row.get("student") or "").strip()
        if not sid:
            continue
        by_stu.setdefault(sid, []).append(row)

    out: dict[str, dict[str, Any]] = {}
    for sid, rows in by_stu.items():
        weak_items: list[dict[str, Any]] = []
        # key: (subject_name, knowledge_name)
        know_rates: dict[tuple[str, str], list[float]] = {}
        exam_names_seen: list[str] = []
        exam_set: set[str] = set()
        for r in rows:
            ename = str(r.get("exam_name") or exam_name or "").strip()
            if ename and ename not in exam_set:
                exam_set.add(ename)
                exam_names_seen.append(ename)
            rate = r.get("score_rate")
            try:
                rate_f = float(rate) if rate is not None else None
            except (TypeError, ValueError):
                rate_f = None
            kn = str(r.get("knowledge_name") or "").strip() or "未关联知识点"
            sub = str(r.get("subject_name") or r.get("subject") or "").strip()
            if rate_f is not None:
                know_rates.setdefault((sub, kn), []).append(rate_f)
                if rate_f < weak_threshold:
                    weak_items.append({
                        "question_no": r.get("question_no"),
                        "knowledge_name": kn,
                        "subject_name": sub,
                        "score_rate": rate_f,
                        "exam_name": ename,
                    })
        # 各场考试各保留若干最弱题，再按得分率排序，避免只剩一场考试
        per_exam_cap = 4
        capped: list[dict[str, Any]] = []
        by_exam: dict[str, list[dict[str, Any]]] = {}
        for it in weak_items:
            by_exam.setdefault(str(it.get("exam_name") or ""), []).append(it)
        for _ename, items in by_exam.items():
            items.sort(key=lambda x: float(x.get("score_rate") or 0))
            capped.extend(items[:per_exam_cap])
        capped.sort(key=lambda x: float(x.get("score_rate") or 0))

        know_avg = [
            {
                "knowledge_name": kn,
                "subject_name": sub,
                "score_rate": round(sum(v) / len(v), 1),
                "question_count": len(v),
            }
            for (sub, kn), v in know_rates.items()
            if v
        ]
        # 先按科目再按得分率，便于报告按学科分块
        know_avg.sort(key=lambda x: (str(x.get("subject_name") or ""), float(x["score_rate"])))
        weak_knowledge = [k for k in know_avg if float(k["score_rate"]) < weak_threshold][:8]
        strong_knowledge = [
            k for k in sorted(know_avg, key=lambda x: float(x["score_rate"]), reverse=True)
            if float(k["score_rate"]) >= 80
        ][:5]
        # 按科目分组（供模板分科展示；无科目时仅一组）
        by_subject: dict[str, list[dict[str, Any]]] = {}
        subject_order: list[str] = []
        for row in know_avg:
            sub = str(row.get("subject_name") or "").strip() or "未分科"
            if sub not in by_subject:
                by_subject[sub] = []
                subject_order.append(sub)
            by_subject[sub].append(row)
        out[sid] = {
            "exam_name": "、".join(exam_names_seen) if exam_names_seen else exam_name,
            "exam_names": exam_names_seen,
            "weak_items": capped[:16],
            "weak_knowledge": weak_knowledge,
            "strong_knowledge": strong_knowledge,
            "knowledge_rows": know_avg,
            "all_knowledge": know_avg,
            "knowledge_by_subject": {s: by_subject[s] for s in subject_order},
        }
    return out


__all__ = [
    "aggregate_student_item_insights",
    "build_comprehensive_data",
    "build_student_archive_from_score_rows",
]
