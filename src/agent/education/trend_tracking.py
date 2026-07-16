"""成绩趋势报告数据组装——从多场学生明细确定性产出折线与明细表。

避免 LLM 手填 ``TREND_CHART`` / ``TREND_TABLE`` 导致图表空白或半成品表格。
"""

from __future__ import annotations

from typing import Any

from src.agent.education.charts import build_chart_option
from src.agent.education.comprehensive import (
    _fmt,
    _normalize_records,
    _record_effective_total,
    _resolve_exams,
    _short_exam_label,
)
from src.agent.education.config import EducationConfig
from src.agent.education.report_types import ReportType, report_type_label
from src.agent.education.stats import compute_score_stats


def _now_str() -> str:
    from datetime import datetime

    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _score_for_record(record: dict[str, Any], subject_name: str = "") -> float | None:
    """取单条记录用于趋势的分数：指定科目用该科，否则用有效总分。"""
    sub = (subject_name or "").strip()
    if sub:
        subjects = record.get("subjects") or {}
        if sub in subjects and subjects[sub] is not None:
            try:
                return float(subjects[sub])
            except (TypeError, ValueError):
                return None
        # 科目名模糊匹配
        for k, v in subjects.items():
            if sub in str(k) or str(k) in sub:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    continue
        return None
    return _record_effective_total(record)


def _infer_full_score(
    records: list[dict[str, Any]],
    *,
    subject_name: str = "",
    full_score: float | None = None,
    default: float = 100.0,
) -> float:
    if full_score is not None:
        try:
            return float(full_score)
        except (TypeError, ValueError):
            pass
    vals: list[float] = []
    for r in records:
        v = _score_for_record(r, subject_name)
        if v is not None:
            vals.append(v)
        for raw in (r.get("exam_score"), r.get("full_score")):
            if raw is None:
                continue
            try:
                vals.append(float(raw))
            except (TypeError, ValueError):
                continue
    if not vals:
        return default
    return max(max(vals), default)


def _table_html(headers: list[str], rows: list[list[str]]) -> str:
    head = "<tr>" + "".join(f"<th>{h}</th>" for h in headers) + "</tr>"
    body = "".join(
        "<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows
    )
    return f'<table class="edu-table"><thead>{head}</thead><tbody>{body}</tbody></table>'


def build_trend_tracking_data(
    records: list[dict[str, Any]],
    exam_order: list[str] | None = None,
    *,
    class_name: str = "",
    school_name: str = "",
    subject_name: str = "",
    target_name: str = "",
    full_score: float | None = None,
    config: EducationConfig | None = None,
) -> dict[str, Any]:
    """从多场成绩 records 组装趋势报告模板字段。

    Args:
        records: 每条 ``{exam, student, subjects: {科目: 分数}, total}``。
        exam_order: 考试顺序（最早→最近）；为空则按出现顺序。
    """
    if config is None:
        from src.agent.education.config_store import get_config

        config = get_config()

    records = [dict(r) for r in (records or [])]
    _normalize_records(records)

    record_exam_order: list[str] = []
    seen_exam: set[str] = set()
    for r in records:
        e = str(r.get("exam") or "").strip()
        if e and e not in seen_exam:
            seen_exam.add(e)
            record_exam_order.append(e)

    by_exam: dict[str, list[dict[str, Any]]] = {}
    for r in records:
        e = str(r.get("exam") or "").strip()
        if e:
            by_exam.setdefault(e, []).append(r)

    exams = _resolve_exams(record_exam_order, exam_order or None)
    exams = [
        e
        for e in exams
        if any(_score_for_record(r, subject_name) is not None for r in by_exam.get(e, []))
    ] or list(record_exam_order)

    upper = _infer_full_score(
        records, subject_name=subject_name, full_score=full_score, default=config.default_full_score
    )

    exam_stats: list[dict[str, Any]] = []
    avgs: list[float] = []
    for e in exams:
        scores = [
            s
            for r in by_exam.get(e, [])
            if (s := _score_for_record(r, subject_name)) is not None
        ]
        st = compute_score_stats(scores, config, full_score=upper)
        exam_stats.append({"exam": e, "stats": st, "scores": scores})
        avgs.append(float(st.get("avg") or 0))

    x_labels = [_short_exam_label(e) for e in exams]
    series_name = (subject_name or "").strip() or "均分"
    chart = ""
    if len(exams) >= 1:
        chart = build_chart_option(
            "trend_line",
            {
                "x_labels": x_labels,
                "series": [
                    {"name": f"{series_name}均分", "values": [round(a, 1) for a in avgs]},
                ],
                "pass_line": round(upper * config.pass_ratio, 1) if upper else None,
            },
            title=f"{(class_name or target_name or '本班')}{series_name}历次均分趋势",
        )

    table_rows: list[list[str]] = []
    for item in exam_stats:
        st = item["stats"]
        table_rows.append(
            [
                str(item["exam"]),
                str(st.get("count") or 0),
                _fmt(st.get("avg")),
                _fmt(st.get("max")),
                _fmt(st.get("min")),
                _fmt(st.get("stdev")),
                f"{_fmt(st.get('pass_rate'))}%",
                f"{_fmt(st.get('excellent_rate'))}%",
            ]
        )
    trend_table = (
        _table_html(
            ["考试", "参考人数", "均分", "最高", "最低", "标准差", "及格率", "优秀率"],
            table_rows,
        )
        if table_rows
        else "<p>暂无历次成绩明细。</p>"
    )

    change_parts: list[str] = []
    if len(avgs) >= 2:
        delta = avgs[-1] - avgs[0]
        direction = "上升" if delta > 0.5 else ("下降" if delta < -0.5 else "基本持平")
        change_parts.append(
            f"<p>从「{exams[0]}」到「{exams[-1]}」，班级{series_name}均分"
            f"由 {_fmt(avgs[0])} 变为 {_fmt(avgs[-1])}（{direction} {_fmt(abs(delta))} 分）。</p>"
        )
        # 相邻场次最大波动
        max_jump = 0.0
        jump_from = jump_to = ""
        for i in range(1, len(avgs)):
            d = abs(avgs[i] - avgs[i - 1])
            if d >= max_jump:
                max_jump = d
                jump_from, jump_to = exams[i - 1], exams[i]
        if max_jump >= 1:
            change_parts.append(
                f"<p>相邻场次波动最大：{_short_exam_label(jump_from)} → "
                f"{_short_exam_label(jump_to)}，均分变动 {_fmt(max_jump)} 分。</p>"
            )
    elif len(avgs) == 1:
        change_parts.append(
            f"<p>当前仅有 1 场有效考试「{exams[0]}」，均分 {_fmt(avgs[0])}；"
            "需至少 2 场才能判断进退步趋势。</p>"
        )
    else:
        change_parts.append("<p>未找到可用的多场成绩，无法分析变化。</p>")
    change_info = "\n".join(change_parts)

    n_exams = len(exams)
    target = (target_name or class_name or "本班").strip()
    sub_label = (subject_name or "全科").strip()
    if n_exams >= 2:
        summary = (
            f"<p>{school_name + ' · ' if school_name else ''}{target} 共跟踪 "
            f"{n_exams} 场{sub_label}考试；首场均分 {_fmt(avgs[0])}，"
            f"最近一场 {_fmt(avgs[-1])}。</p>"
        )
        if avgs[-1] >= avgs[0]:
            rec = (
                "<ul class=\"edu-rec-list\">"
                "<li>保持当前节奏，对波动较大场次做错题复盘。</li>"
                "<li>关注均分仍低于班级目标的分数段学生，安排分层巩固。</li>"
                "</ul>"
            )
        else:
            rec = (
                "<ul class=\"edu-rec-list\">"
                "<li>针对下滑场次的薄弱知识点安排回炉训练。</li>"
                "<li>对比进退幅度较大的学生，一对一分析失分原因。</li>"
                "</ul>"
            )
    elif n_exams == 1:
        summary = f"<p>{target} 目前仅 1 场{sub_label}数据（均分 {_fmt(avgs[0])}）。</p>"
        rec = "<p>请补充更多场次考试成绩后再生成趋势解读。</p>"
    else:
        summary = "<p>未检索到可用于趋势分析的成绩明细。</p>"
        rec = "<p>请确认班级、科目与考试范围后重试。</p>"

    title_bits = [p for p in (school_name, class_name or target_name, subject_name) if p]
    title = (
        f"{' '.join(title_bits)} 历次成绩趋势报告"
        if title_bits
        else "历次成绩趋势报告"
    )
    subtitle_bits = [p for p in (school_name, class_name or target_name, subject_name) if p]
    subtitle = " · ".join(subtitle_bits)
    if n_exams:
        subtitle = (subtitle + " · " if subtitle else "") + f"{n_exams} 场考试跟踪分析"

    return {
        "REPORT_TITLE": title,
        "REPORT_SUBTITLE": subtitle or "历次成绩跟踪",
        "REPORT_TIME": _now_str(),
        "REPORT_TYPE": report_type_label(ReportType.TREND_TRACKING),
        "TARGET_NAME": target,
        "SUBJECT_NAME": sub_label,
        "TREND_CHART": chart,
        "TREND_TABLE": trend_table,
        "CHANGE_INFO": change_info,
        "SUMMARY": summary,
        "RECOMMENDATIONS": rec,
        "EXAM_COUNT": n_exams,
        "EXAM_NAMES": list(exams),
    }


__all__ = ["build_trend_tracking_data"]
