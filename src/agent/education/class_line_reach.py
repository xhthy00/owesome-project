"""班级达线率与贡献分对照分析报告：一所学校、一场考试，各班达线率 + 贡献分对照。

纯计算，不调用模型。只看在籍生。达线只认六门总分（``zf6m``）。

口径（与报告正文一致）：

- 对标线按校类：引领校=211线、特控线；支撑校=特控线、本科线；发展校与其他校=本科线。
  校类未配置或范围混类时抛 ``ValueError``，不用错线。
- 达线率分母：该班在籍、方向明确、有六门总分的学生。分子：六门总分 ≥ 该场该方向
  该线的线分。年级对照按全校同口径合计后重算（用班级过滤前的数据），不做班级率的平均。
- 对比考试默认上一场（考试时间早于本场、最近的一场），可选不对比。线分逐场浮动，
  达线率已消解命题难度差异，跨场可比。对比场无线分或无此班时对比列显示「—」。
- 老师账号在 SQL 层只能拉到所带班级的成绩，算不出全校年级对照，年级对照列显示「—」。
- 贡献分对照按每条对标线各出一节（引领校=特控线+211线、支撑校=本科线+特控线、发展校=本科线）。
  差值 = 班级均分 − 贡献分；差值 ≤ −1.5 标红，与学校级薄弱学科工具 ``GAP_MIN`` 同口径；
  重点关注建议按最低对标线那节点名科目。每科只统计该班该方向选考该科（分数 > 0）的学生；
  再选科目用赋分列。
- 本报告只出班级汇总，不出学生名单，不涉及匿名开关。
"""

from __future__ import annotations

import html
import re
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from src.agent.education.contribution_weak import GAP_MIN, SUBJECT_LABELS, TRACK_ORDER
from src.agent.education.line_reach import canon_line_name, infer_track, pick_col
from src.agent.education.line_shift import _threshold, _tier_of, row_in_schools

__all__ = [
    "NO_COMPARE",
    "build_class_line_reach_report",
    "class_line_reach_request_error",
    "overview_full_sql",
    "pick_compare_exam_or_none",
]

NO_COMPARE = "不对比"
_SUBJECT_COLS = tuple(col for col, _label in SUBJECT_LABELS)


@dataclass
class _Stu:
    sid: str
    xx: str = ""
    bj: str = ""
    track: str = ""
    total: Decimal | None = None
    scores: dict[str, Any] = field(default_factory=dict)


def class_line_reach_request_error(
    role: str,
    school: str,
    class_name: str,
    exam_name: str,
    compare_exam: str = "",
) -> tuple[str, str] | None:
    """校验分析工具入参。通过返回 None；否则 ``(forbidden|bad, 文案)``。"""
    who = (role or "").strip()
    if who == "student":
        return ("forbidden", "学生账号不能生成班级达线率与贡献分对照分析报告")
    if not (exam_name or "").strip():
        return ("bad", "请选择这次考试")
    raw_compare = (compare_exam or "").strip()
    if raw_compare and raw_compare != NO_COMPARE and raw_compare == exam_name.strip():
        return ("bad", "对比考试必须与本次考试不同；只想看本场请选「不对比」")
    if who == "teacher" and not (class_name or "").strip():
        return (
            "bad",
            "请先选择班级。老师生成班级达线率与贡献分对照分析报告时只统计所带班级",
        )
    if who not in ("school_admin", "teacher") and not (school or "").strip():
        return (
            "bad",
            "请先选择学校。班级达线率与贡献分对照分析报告按一所学校计算，不把多校合在一张表里",
        )
    return None


def pick_compare_exam_or_none(
    batches: list[dict[str, Any]] | None,
    exam_name: str,
    compare_exam: str,
) -> tuple[str, str]:
    """对比考试：选了「不对比」→ 空；没选 → 自动上一场。返回 ``(名称, 说明)``。"""
    raw = str(compare_exam or "").strip()
    if raw == NO_COMPARE:
        return "", "已按选择不对比，只看本次考试。"
    if raw:
        return raw, ""
    from src.agent.education.absence import pick_prev_exam_or_none

    prev = pick_prev_exam_or_none(batches, exam_name)
    if prev:
        return prev, "未选对比考试，自动取考试时间早于本次、最近的一场。"
    return "", "没有考试时间早于本次的场次，未对比。"


def overview_full_sql(exam_name: str, xx_names: list[str]) -> str:
    """一所学校、一场考试的在籍生，带六门总分与九门科目分。

    再选科目（化学/生物/政治/地理）取赋分列 hxzh/swzh/zzzh/dlzh（转换分）。
    """
    names = [n for n in xx_names if str(n or "").strip()]
    if not names:
        raise ValueError("缺少学校名称，拒绝拉取全库成绩")
    lit = _esc(exam_name)
    inn = ", ".join(f"'{_esc(n)}'" for n in names)
    return (
        "SELECT exam_name, xm, anon_stu_id, xx, bj, xkkm, xsxz, zf6m, "
        "yw, sx, yy, wl, hxzh, swzh, zzzh, ls, dlzh "
        "FROM tb_score_overview "
        f"WHERE exam_name = '{lit}' "
        "AND xsxz = '在籍生' "
        f"AND xx IN ({inn}) "
        "LIMIT 200000"
    )


def build_class_line_reach_report(
    *,
    exam_name: str,
    compare_exam: str,
    curr_rows: list[dict[str, Any]],
    prev_rows: list[dict[str, Any]],
    bars: list[dict[str, Any]],
    contribution_rows: list[dict[str, Any]],
    schools: list[dict[str, Any]],
    prev_contribution_rows: list[dict[str, Any]] | None = None,
    class_name: str = "",
    compare_note: str = "",
    curr_date: str = "",
    compare_date: str = "",
    report_time: str = "",
    grade_rates_hidden: bool = False,
    trend_exams: list[str] | None = None,
    trend_rows: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """组装模板数据。校类不明或混类时抛 ``ValueError``，避免用错分数线。

    ``grade_rates_hidden``：老师账号的行级权限把成绩限制在所带班级，算不出真正的
    全校年级对照——置 True 时年级对照列显示「—」，不用所带班级冒充全校。
    ``trend_exams`` / ``trend_rows``：本届历次考试（时间序）与其成绩行，画
    「历场达线率走势」折线图；不传则不出图。
    """
    tier, main_lines, school_label = _tier_of(schools)
    curr_all, curr_dups = _index(curr_rows, schools)
    prev_all, prev_dups = _index(prev_rows, schools)
    want = _norm_class(class_name)
    # 年级对照必须用班级过滤前的全校口径重算；否则选了单班后
    # 年级对照会退化成该班自己的达线率。
    school_groups, _school_excluded = _groups(curr_all)
    if want:
        curr_all = {sid: s for sid, s in curr_all.items() if _norm_class(s.bj) == want}
        prev_all = {sid: s for sid, s in prev_all.items() if _norm_class(s.bj) == want}
    grade_groups = {} if grade_rates_hidden else school_groups

    curr_groups, curr_excluded = _groups(curr_all)
    prev_groups, _prev_excluded = _groups(prev_all)
    if not curr_groups:
        raise ValueError(
            "本次考试没有方向明确且有六门总分的在籍生，无法计算班级达线率。"
        )

    thresholds: dict[tuple[str, str, str], tuple[Decimal | None, str]] = {}
    for line in main_lines:
        for track in TRACK_ORDER:
            thresholds[(exam_name, line, track)] = _threshold(bars, exam_name, line, track)
            if compare_exam:
                thresholds[(compare_exam, line, track)] = (
                    _threshold(bars, compare_exam, line, track)[0],
                    "",
                )

    class_ids = sorted(
        {cid for cid, _track in curr_groups},
        key=lambda cid: _class_sort_key(_class_label(curr_groups, cid)),
    )
    reach_rows = []
    rate_by: dict[tuple[str, str], tuple[float | None, float | None, float | None]] = {}
    for cid in class_ids:
        label = _class_label(curr_groups, cid)
        for line in main_lines:
            row = _reach_row(
                cid,
                label,
                line,
                curr_groups,
                prev_groups,
                grade_groups,
                thresholds,
                exam_name,
                compare_exam,
            )
            reach_rows.append(row)
            rate_by[(cid, line)] = (row["_rate"], row["_grade_rate"], row["_prev_rate"])

    grade_rates = _grade_rates(main_lines, grade_groups, thresholds, exam_name)
    gap_sections = _gap_sections(
        curr_groups=curr_groups,
        prev_groups=prev_groups,
        contribution_rows=contribution_rows,
        prev_contribution_rows=prev_contribution_rows or [],
        main_lines=main_lines,
        compare_exam=compare_exam,
        class_ids=class_ids,
    )

    trend_chart, trend_note = _trend_chart(
        trend_exams=trend_exams or [],
        trend_rows=trend_rows or {},
        schools=schools,
        class_name=class_name,
        main_lines=main_lines,
        bars=bars,
        exam_name=exam_name,
        grade_rates_hidden=grade_rates_hidden,
    )

    # 薄弱学科总览：先结论后明细，放在贡献分明细之前；单班视图即本班标红科目排行。
    weak_sections = _weak_sections(
        gap_sections=gap_sections,
        school_groups=school_groups,
        contribution_rows=contribution_rows,
        class_name=class_name,
    )
    school_recs = [] if want else _school_weak_recs(weak_sections)

    scope = _esc_text(class_name) if class_name else "全校"
    compare_on = bool(compare_exam)
    data = {
        "REPORT_TITLE": f"{school_label}{_esc_text(class_name)}班级达线率与贡献分对照分析报告",
        "REPORT_TYPE": "班级达线率与贡献分对照分析报告",
        "REPORT_TIME": _esc_text(report_time),
        "SCHOOL_LABEL": school_label,
        "CLASS_LABEL": scope,
        "TIER": _esc_text(tier),
        "LINE_NAMES": _esc_text("、".join(main_lines)),
        "EDGE_LINE": _esc_text(main_lines[-1]),
        "EXAM_NAME": _esc_text(exam_name),
        "EXAM_DATE": _esc_text(curr_date),
        "COMPARE_EXAM": _esc_text(compare_exam),
        "COMPARE_DATE": _esc_text(compare_date),
        "COMPARE_ON": compare_on,
        "COMPARE_NOTE": _esc_text(compare_note),
        "COHORT_NOTE": _cohort_note(
            school_label,
            scope,
            curr_groups,
            curr_excluded,
            curr_dups + prev_dups,
            prev_groups,
            compare_on,
        ),
        "LINE_SCORES": _line_scores(main_lines, TRACK_ORDER, thresholds, exam_name, compare_exam),
        "REACH_ROWS": reach_rows,
        "REACH_CHART": _reach_chart(reach_rows),
        "TREND_CHART": trend_chart,
        "TREND_NOTE": trend_note,
        "WEAK_SECTIONS": weak_sections,
        "GAP_SECTIONS": gap_sections,
        # 单班视图贡献分表全量展示；全校视图只列标红项，避免上百行长表
        "GAP_TABLE_SHOW_ALL": bool(want),
        "GAP_MIN": f"{GAP_MIN}",
        "GRADE_NOTE": (
            "老师账号只统计所带班级，拉不到全校成绩，无法计算年级对照，该列显示「—」。"
            if grade_rates_hidden
            else ""
        ),
        "RECOMMENDATIONS": school_recs
        + _recommendations(
            reach_rows,
            # 点名科目用最低对标线（贴近生源层次）那节的差值
            gap_sections[-1]["rows"],
            rate_by,
            grade_rates,
            {cid: _class_label(curr_groups, cid) for cid in class_ids},
            main_lines,
            compare_exam,
        ),
        "_stats": {"count": sum(len(v) for v in curr_groups.values())},
    }
    return data


def _reach_row(
    cid: str,
    label: str,
    line: str,
    curr_groups: dict[tuple[str, str], list[_Stu]],
    prev_groups: dict[tuple[str, str], list[_Stu]],
    grade_groups: dict[tuple[str, str], list[_Stu]],
    thresholds: dict[tuple[str, str, str], tuple[Decimal | None, str]],
    exam_name: str,
    compare_exam: str,
) -> dict[str, Any]:
    count, reached = _count_reached(curr_groups, cid, thresholds, exam_name, line)
    prev_count, prev_reached = (0, 0)
    if compare_exam:
        prev_count, prev_reached = _count_reached(
            prev_groups, cid, thresholds, compare_exam, line
        )

    grade_count, grade_reached = _count_reached(
        grade_groups, _ALL, thresholds, exam_name, line
    )
    rate = _pct(reached, count)
    grade_rate = _pct(grade_reached, grade_count)
    prev_rate = _pct(prev_reached, prev_count) if compare_exam else None
    delta = None
    if rate is not None and prev_rate is not None:
        delta = round(rate - prev_rate, 1)

    return {
        "class": _esc_text(label),
        "line_name": _esc_text(line),
        "count": str(count),
        "reached": str(reached),
        "rate": _fmt_pct(rate),
        "grade_rate": _fmt_pct(grade_rate),
        "below": rate is not None and grade_rate is not None and rate < grade_rate,
        "prev_count": str(prev_count) if compare_exam else "",
        "prev_rate": _fmt_pct(prev_rate),
        "delta": _fmt_pp(delta),
        "_rate": rate,
        "_grade_rate": grade_rate,
        "_prev_rate": prev_rate,
        "_delta": delta,
    }


#: 全校合计的哨兵班级键（年级对照列用）
_ALL = "__all__"


def _count_reached(
    groups: dict[tuple[str, str], list[_Stu]],
    cid: str,
    thresholds: dict[tuple[str, str, str], tuple[Decimal | None, str]],
    exam_name: str,
    line: str,
) -> tuple[int, int]:
    """一个班级（或 ``_ALL`` 全校合计）在一条线上的参考人数与达线人数。

    某方向无线分时，该方向学生不进分母。
    """
    count = 0
    reached = 0
    for track in TRACK_ORDER:
        thr, _err = thresholds.get((exam_name, line, track), (None, ""))
        if cid == _ALL:
            stus = [s for (c, t), lst in groups.items() if t == track for s in lst]
        else:
            stus = groups.get((cid, track)) or []
        if thr is None:
            continue
        count += len(stus)
        reached += sum(1 for s in stus if s.total is not None and s.total >= thr)
    return count, reached


def _grade_rates(
    main_lines: list[str],
    grade_groups: dict[tuple[str, str], list[_Stu]],
    thresholds: dict[tuple[str, str, str], tuple[Decimal | None, str]],
    exam_name: str,
) -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    for line in main_lines:
        count, reached = _count_reached(grade_groups, _ALL, thresholds, exam_name, line)
        out[line] = _pct(reached, count)
    return out


def _gap_sections(
    *,
    curr_groups: dict[tuple[str, str], list[_Stu]],
    prev_groups: dict[tuple[str, str], list[_Stu]],
    contribution_rows: list[dict[str, Any]],
    prev_contribution_rows: list[dict[str, Any]],
    main_lines: list[str],
    compare_exam: str,
    class_ids: list[str],
) -> list[dict[str, Any]]:
    """每条对标线各出一节贡献分对照；数据缺某条线时该节留空由模板写明。"""
    sections: list[dict[str, Any]] = []
    for line in main_lines:
        canon = canon_line_name(line)
        contrib = _contrib_map(contribution_rows, canon)
        prev_contrib = _contrib_map(prev_contribution_rows, canon) if compare_exam else {}
        rows, chart_rows = _gap_rows_for_line(
            curr_groups=curr_groups,
            prev_groups=prev_groups,
            contrib=contrib,
            prev_contrib=prev_contrib,
            compare_exam=compare_exam,
            class_ids=class_ids,
        )
        sections.append(
            {
                "line_name": _esc_text(line),
                "rows": rows,
                "flagged": sum(1 for r in rows if r["flag"]),
                "chart": _gap_chart(chart_rows, line),
            }
        )
    return sections


def _gap_rows_for_line(
    *,
    curr_groups: dict[tuple[str, str], list[_Stu]],
    prev_groups: dict[tuple[str, str], list[_Stu]],
    contrib: dict[tuple[str, str], Decimal | None],
    prev_contrib: dict[tuple[str, str], Decimal | None],
    compare_exam: str,
    class_ids: list[str],
) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    rows: list[dict[str, str]] = []
    chart_rows: list[dict[str, Any]] = []
    for cid in class_ids:
        label = _class_label(curr_groups, cid)
        for track in TRACK_ORDER:
            stus = curr_groups.get((cid, track)) or []
            if not stus:
                continue
            for col, subject in SUBJECT_LABELS:
                c = contrib.get((track, col))
                if c is None:
                    continue
                avg = _subject_avg(stus, col)
                if avg is None:
                    continue
                gap = (avg - c).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
                flag = gap <= -GAP_MIN
                prev_gap = None
                if compare_exam:
                    prev_stus = prev_groups.get((cid, track)) or []
                    pc = prev_contrib.get((track, col))
                    prev_avg = _subject_avg(prev_stus, col) if prev_stus else None
                    if pc is not None and prev_avg is not None:
                        prev_gap = (prev_avg - pc).quantize(
                            Decimal("0.1"), rounding=ROUND_HALF_UP
                        )
                delta = None
                if prev_gap is not None:
                    delta = (gap - prev_gap).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
                # 贡献分达线率：选该科的学生中该科 ≥ 贡献分 的人数占比。
                # 差值看平均水平，达线率看分布——均分达标但达线率低即两极分化。
                rate_hit = 0
                rate_n = 0
                for s in stus:
                    d = _decimal(s.scores.get(col))
                    if d is not None and d > 0:
                        rate_n += 1
                        if d >= c:
                            rate_hit += 1
                rate = _pct(rate_hit, rate_n)
                rows.append(
                    {
                        "class": _esc_text(label),
                        "track": _esc_text(track),
                        "subject": _esc_text(subject),
                        "avg": _fmt_num(avg),
                        "contrib": _fmt_num(c),
                        "gap": _fmt_signed(gap),
                        "flag": flag,
                        "rate": (
                            f"{_fmt_pct(rate)}（{rate_hit}/{rate_n}）"
                            if rate is not None
                            else "—"
                        ),
                        "prev_gap": _fmt_signed(prev_gap) if prev_gap is not None else "—",
                        "gap_delta": _fmt_signed(delta) if delta is not None else "—",
                        "_rate": rate,
                        "_rate_hit": rate_hit,
                        "_rate_n": rate_n,
                        "_col": col,
                    }
                )
                if flag:
                    chart_rows.append(
                        {
                            "class": label,
                            "subject": subject,
                            "gap": float(gap),
                        }
                    )
    return rows, chart_rows


def _contrib_map(
    contribution_rows: list[dict[str, Any]],
    edge_line: str,
) -> dict[tuple[str, str], Decimal | None]:
    out: dict[tuple[str, str], Decimal | None] = {}
    for row in contribution_rows or []:
        if not isinstance(row, dict):
            continue
        track = str(pick_col(row, "track") or "").strip()
        if canon_line_name(str(pick_col(row, "line_name") or "")) != edge_line:
            continue
        for col, _subject in SUBJECT_LABELS:
            out[(track, col)] = _decimal(row.get(col))
    return out


def _subject_avg(stus: list[_Stu], col: str) -> Decimal | None:
    vals = [s.scores.get(col) for s in stus]
    nums = []
    for v in vals:
        d = _decimal(v)
        if d is not None and d > 0:
            nums.append(d)
    if not nums:
        return None
    total = Decimal("0")
    for n in nums:
        total += n
    return (total / Decimal(len(nums))).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def _reach_chart(reach_rows: list[dict[str, Any]]) -> str:
    from src.agent.education.charts import build_chart_option

    classes = _ordered_unique([str(r["class"]) for r in reach_rows])
    lines = _ordered_unique([str(r["line_name"]) for r in reach_rows])
    metrics = []
    for line in lines:
        values = []
        for row in reach_rows:
            if str(row["line_name"]) == line:
                values.append(row["_rate"])
        metrics.append({"name": line, "values": [None if v is None else round(v, 1) for v in values]})
    if not classes or not lines:
        return ""
    return build_chart_option(
        "group_compare_bar",
        {
            "groups": classes,
            "metrics": metrics,
            "y_name": "达线率%",
            "y_max": 100,
            "x_rotate": 30,
        },
        title="各班对标线达线率",
    )


def _gap_chart(chart_rows: list[dict[str, Any]], line_name: str) -> str:
    from src.agent.education.charts import build_chart_option

    if not chart_rows:
        return ""
    classes = _ordered_unique([str(r["class"]) for r in chart_rows])
    subjects = _ordered_unique([str(r["subject"]) for r in chart_rows])
    metrics = []
    for subject in subjects:
        values = []
        for cls in classes:
            hit = next(
                (r for r in chart_rows if str(r["class"]) == cls and str(r["subject"]) == subject),
                None,
            )
            values.append(round(float(hit["gap"]), 1) if hit else None)
        metrics.append({"name": subject, "values": values})
    return build_chart_option(
        "group_compare_bar",
        {
            "groups": classes,
            "metrics": metrics,
            "y_name": "差值（均分−贡献分）",
            "x_rotate": 30,
        },
        title=f"拖后腿科目·{line_name}（差值 ≤ −{GAP_MIN}）",
    )


def _trend_chart(
    *,
    trend_exams: list[str],
    trend_rows: dict[str, list[dict[str, Any]]],
    schools: list[dict[str, Any]],
    class_name: str,
    main_lines: list[str],
    bars: list[dict[str, Any]],
    exam_name: str,
    grade_rates_hidden: bool,
) -> tuple[str, str]:
    """历场达线率走势折线图：本届历次考试同口径，高亮本次，缺数据场次断线。"""
    from src.agent.education.charts import build_chart_option

    exams = [e for e in trend_exams if str(e or "").strip()]
    if len(exams) < 2:
        return "", ""

    points: list[dict[str, Any]] = []
    for exam in exams:
        allmap, _dups = _index(trend_rows.get(exam) or [], schools)
        school_groups, _ex = _groups(allmap)
        grade_groups = {} if grade_rates_hidden else school_groups
        want = _norm_class(class_name)
        if want:
            allmap = {
                sid: s for sid, s in allmap.items() if _norm_class(s.bj) == want
            }
        scope_groups, _ex2 = _groups(allmap)
        exam_thresholds: dict[tuple[str, str, str], tuple[Decimal | None, str]] = {
            (exam, line, track): _threshold(bars, exam, line, track)
            for line in main_lines
            for track in TRACK_ORDER
        }
        scope_rates: dict[str, float | None] = {}
        grade_rates: dict[str, float | None] = {}
        for line in main_lines:
            c_count, c_reached = _count_reached(scope_groups, _ALL, exam_thresholds, exam, line)
            scope_rates[line] = _pct(c_reached, c_count)
            g_count, g_reached = _count_reached(grade_groups, _ALL, exam_thresholds, exam, line)
            grade_rates[line] = _pct(g_reached, g_count)
        points.append({"exam": exam, "scope": scope_rates, "grade": grade_rates})

    x_labels = [
        _short_exam(p["exam"]) + ("·本次" if p["exam"] == exam_name else "")
        for p in points
    ]
    series: list[dict[str, Any]] = []
    if class_name:
        # 单班视图：该班一条线 + 年级对照一条线（老师账号没有全校数据，不画年级）。
        for line in main_lines:
            series.append(
                {
                    "name": f"{line}·{class_name}",
                    "values": [p["scope"].get(line) for p in points],
                }
            )
        if not grade_rates_hidden:
            for line in main_lines:
                series.append(
                    {
                        "name": f"{line}·年级对照",
                        "values": [p["grade"].get(line) for p in points],
                    }
                )
    else:
        # 全校视图：34 个班画不下，只画全校合计。
        for line in main_lines:
            series.append(
                {
                    "name": f"{line}·全校",
                    "values": [p["grade"].get(line) for p in points],
                }
            )
    if not any(v is not None for s in series for v in s["values"]):
        return "", ""

    title = f"{class_name or '全校'}历场达线率走势"
    chart = build_chart_option(
        "trend_line",
        {
            "x_labels": x_labels,
            "series": series,
            "y_name": "达线率%",
            "y_max": 100,
        },
        title=title,
    )
    note = (
        "本届历次考试同口径计算，各场按各自线分判定达线，跨场可比；"
        "某场缺该校数据则该点断线；标「·本次」为本次考试。"
    )
    if class_name:
        note += (
            "老师账号无全校数据，只画本班。" if grade_rates_hidden else "本班与年级对照各一组线。"
        )
    else:
        note += "全校视图只画全校合计达线率，班级明细见上表。"
    return chart, note


def _short_exam(exam: str) -> str:
    """考试名压短：去掉开头的「届次+年级」前缀（2026届高三1月期末 → 1月期末）。"""
    text = str(exam or "").strip()
    short = re.sub(r"^\d{4}届\s*(高三|高二|高一)?", "", text)
    return short or text


def _weak_sections(
    *,
    gap_sections: list[dict[str, Any]],
    school_groups: dict[tuple[str, str], list[_Stu]],
    contribution_rows: list[dict[str, Any]],
    class_name: str,
) -> list[dict[str, Any]]:
    """薄弱学科总览：先结论后明细。

    全校视图按「标红班级数」汇总（多少个班该科差值 ≤ GAP_MIN），
    附校均差值与最低班贡献分达线率；单班视图列本班标红科目（差值升序）。
    每节附一句话总结，结论先于表格。
    """
    want = _norm_class(class_name)
    sections: list[dict[str, Any]] = []
    for sec in gap_sections:
        line = str(sec["line_name"])
        rows = list(sec["rows"])
        if want:
            items = _weak_items_for_class(rows)
            chart = ""
            summary = _weak_summary_class(line, items)
        else:
            items = _weak_items_for_school(rows, school_groups, contribution_rows, line)
            chart = _weak_chart(items, line)
            summary = _weak_summary_school(line, items)
        sections.append(
            {
                "line_name": _esc_text(line),
                "class_view": bool(want),
                "rows": items,
                "chart": chart,
                "summary": summary,
            }
        )
    return sections


#: 总结里逐科列举的上限，超出用「等」收尾
_WEAK_LIST_CAP = 4


def _weak_summary_school(line: str, items: list[dict[str, str]]) -> str:
    """全校总结：哪些科目薄弱 → 共性还是个别 → 整体偏弱还是局部掉队。

    科目按科目名合并计数（物理类数学与历史类数学算一门「数学」），
    班级数用大白话表述（「16 个班中 12 个班薄弱」），不用「x/y 班标红」。
    """
    if not items:
        return _esc_text(
            f"各班各科均分距{line}画像的差值均未低于 −{GAP_MIN}，无薄弱学科。"
        )
    subjects = _ordered_unique([str(x["subject"]) for x in items])
    shown = items[:_WEAK_LIST_CAP]
    listed = "、".join(
        f"{x['track']}{x['subject']}（{_flagged_plain(x['flagged'])}）" for x in shown
    )
    more = "等" if len(items) > len(shown) else ""
    bits = [
        f"{line}画像下，{len(subjects)} 门科目存在薄弱班级"
        f"（班级均分低于该科贡献分 {GAP_MIN} 分以上）：{listed}{more}。"
    ]
    majority = _majority_item(items)
    if majority is not None:
        flagged, total = _parse_flagged(str(majority["flagged"]))
        bits.append(
            f"{majority['track']}{majority['subject']}在 {total} 个班中 {flagged} 个班"
            "均分低于画像，过半数，属年级共性问题，建议年级统筹补强。"
        )
    else:
        bits.append("各科薄弱班级均未过半，属个别班级问题，按班针对性补强即可。")
    gap_text = str(items[0]["school_gap"] or "")
    if gap_text and gap_text != "—":
        if _dec_of(gap_text) < 0:
            bits.append(
                f"校均差值最深为{items[0]['subject']}（{gap_text} 分），整体亦偏弱。"
            )
        else:
            bits.append(f"校均差值为正（{gap_text}），整体达标、局部掉队。")
    return _esc_text("".join(bits))


def _weak_summary_class(line: str, items: list[dict[str, str]]) -> str:
    """单班总结：本班哪些科薄弱、差多少、达线率多少，并点名优先补强科目。"""
    if not items:
        return _esc_text(
            f"本班各科均分距{line}画像的差值均未低于 −{GAP_MIN}，无薄弱学科。"
        )
    shown = items[:_WEAK_LIST_CAP]
    listed = "、".join(
        f"{x['subject']}（差 {_fmt_num(-_dec_of(x['gap']))} 分，贡献分达线率 {x['rate']}）"
        for x in shown
    )
    more = "等" if len(items) > len(shown) else ""
    top = items[0]
    return _esc_text(
        f"本班{line}画像下 {len(items)} 科薄弱：{listed}{more}。"
        f"建议优先补强{top['subject']}。"
    )


def _majority_item(items: list[dict[str, str]]) -> dict[str, str] | None:
    """第一项过半班级标红的科目（items 已按标红班级数降序）。"""
    for x in items:
        flagged, total = _parse_flagged(str(x.get("flagged") or ""))
        if total >= 2 and flagged * 2 >= total:
            return x
    return None


def _parse_flagged(text: str) -> tuple[int, int]:
    """「3/15」→ (3, 15)；解析失败返回 (0, 0)。"""
    try:
        a, b = str(text).split("/")
        return int(a), int(b)
    except (ValueError, AttributeError):
        return 0, 0


def _flagged_plain(flagged: str) -> str:
    """「12/16」→「16 个班中 12 个班薄弱」；解析不了原样返回。"""
    a, b = _parse_flagged(str(flagged or ""))
    if b > 0:
        return f"{b} 个班中 {a} 个班薄弱"
    return str(flagged or "")


def _weak_items_for_class(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """单班视图：本班标红科目按差值升序（最弱在前）。"""
    items = [r for r in rows if r.get("flag")]
    items.sort(key=lambda r: (_dec_of(r["gap"]), str(r["subject"])))
    return [
        {
            "track": r["track"],
            "subject": r["subject"],
            "avg": r["avg"],
            "contrib": r["contrib"],
            "gap": r["gap"],
            "rate": r["rate"],
        }
        for r in items
    ]


def _weak_items_for_school(
    rows: list[dict[str, str]],
    school_groups: dict[tuple[str, str], list[_Stu]],
    contribution_rows: list[dict[str, Any]],
    line: str,
) -> list[dict[str, str]]:
    """全校视图：按（方向 × 科目）汇总标红班级数，附校均差值与最低班达线率。"""
    contrib = _contrib_map(contribution_rows, canon_line_name(line))
    stus_by_track: dict[str, list[_Stu]] = {track: [] for track in TRACK_ORDER}
    for (_cid, track), stus in school_groups.items():
        stus_by_track.setdefault(track, []).extend(stus)
    grouped: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for r in rows:
        grouped[(str(r["track"]), str(r["subject"]))].append(r)
    items: list[dict[str, Any]] = []
    for (track, subject), rrows in grouped.items():
        flagged = sum(1 for r in rrows if r.get("flag"))
        if flagged < 1:
            continue
        total = len({str(r["class"]) for r in rrows})
        col = str(rrows[0].get("_col") or "")
        c = contrib.get((track, col))
        school_gap: Decimal | None = None
        if c is not None:
            avg = _subject_avg(stus_by_track.get(track) or [], col)
            if avg is not None:
                school_gap = (avg - c).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        rate_rows = [r for r in rrows if r.get("_rate") is not None]
        min_rate = ""
        if rate_rows:
            low = min(rate_rows, key=lambda r: (float(r["_rate"]), str(r["class"])))
            min_rate = f"{_fmt_pct(low['_rate'])}（{low['class']}）"
        items.append(
            {
                "track": _esc_text(track),
                "subject": _esc_text(subject),
                "flagged": f"{flagged}/{total}",
                "_flagged": flagged,
                "school_gap": _fmt_signed(school_gap),
                "min_rate": min_rate or "—",
                "_gap_val": float(school_gap) if school_gap is not None else 0.0,
            }
        )
    items.sort(key=lambda x: (-int(x["_flagged"]), float(x["_gap_val"]), str(x["subject"])))
    return [
        {
            "track": x["track"],
            "subject": x["subject"],
            "flagged": x["flagged"],
            "school_gap": x["school_gap"],
            "min_rate": x["min_rate"],
        }
        for x in items
    ]


def _weak_chart(items: list[dict[str, str]], line: str) -> str:
    from src.agent.education.charts import build_chart_option

    if not items:
        return ""
    labels = [f"{x['track']}·{x['subject']}" for x in items]
    values = [int(str(x["flagged"]).split("/")[0] or 0) for x in items]
    return build_chart_option(
        "group_compare_bar",
        {
            "groups": labels,
            "metrics": [{"name": "薄弱班级数", "values": values}],
            "y_name": "薄弱班级数",
            "x_rotate": 30,
        },
        title=f"薄弱班级数·{line}（班级均分低于画像 {GAP_MIN} 分以上）",
    )


def _school_weak_recs(weak_sections: list[dict[str, Any]]) -> list[str]:
    """全校性薄弱学科（过半班级标红）给年级层面的统筹建议，排在班级建议之前。"""
    recs: list[str] = []
    for sec in weak_sections:
        line = str(sec["line_name"])
        for item in list(sec.get("rows") or []):
            flagged, total = _parse_flagged(str(item.get("flagged") or "0/0"))
            if total < 2 or flagged * 2 < total:
                continue
            min_rate = str(item.get("min_rate") or "—")
            recs.append(
                f"全校性薄弱：{line}·{item['track']}{item['subject']}，"
                f"{total} 个班中 {flagged} 个班均分低于画像，建议年级层面统筹专题补强，"
                f"而非各班单打独斗；最低班贡献分达线率 {min_rate}。"
            )
            break  # 每条线只点最重的科目（items 已按标红班级数降序）
    return [_esc_text(r) for r in recs]


#: 与年级对照差距达到该百分点数时建议前缀「重点关注」
_FOCUS_GAP = 15.0
#: 班级在最低对标线贡献分对照里标红科目数达到该值时提示两极分化
_POLAR_FLAGGED = 3


def _recommendations(
    reach_rows: list[dict[str, Any]],
    gap_rows: list[dict[str, str]],
    rate_by: dict[tuple[str, str], tuple[float | None, float | None, float | None]],
    grade_rates: dict[str, float | None],
    labels: dict[str, str],
    main_lines: list[str],
    compare_exam: str,
) -> list[str]:
    gap_by_class: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in gap_rows:
        gap_by_class[str(row["class"])].append(row)
    edge_line = main_lines[-1]

    # 问题班级：与年级对照差距最大的线，按差距从小到大排（最严重的在前）。
    problems: list[tuple[float, str]] = []
    for cid, label in labels.items():
        worst_line = ""
        worst_diff = None
        for line in main_lines:
            rate, _prev, _delta = rate_by.get((cid, line), (None, None, None))
            grade = grade_rates.get(line)
            if rate is None or grade is None:
                continue
            diff = round(rate - grade, 1)
            if diff < 0 and (worst_diff is None or diff < worst_diff):
                worst_diff = diff
                worst_line = line
        if not worst_line:
            continue
        rate, _prev, _delta = rate_by[(cid, worst_line)]
        grade = grade_rates.get(worst_line) or 0.0
        bits = [
            f"{label}{worst_line}达线率 {_fmt_pct(rate)}，低于年级对照 {_fmt_pct(grade)}"
        ]
        rows = gap_by_class.get(label) or []
        if rows:
            worst = min(rows, key=lambda r: _dec_of(r["gap"]))
            # 差值全为正的班级不点名科目——没有拖后腿科目可说。
            if _dec_of(worst["gap"]) < 0:
                n = _fmt_num(-_dec_of(worst["gap"]))
                bits.append(
                    f"距{edge_line}画像差距最大的科目是{worst['subject']}（差 {n} 分），"
                    "建议安排专题补强"
                )
            # 贡献分达线率横向比最低的科目：差值看均分，达线率看分布——
            # 均分达标但达线率低即两极分化，这是差值照不出的证据。
            rate_rows = [r for r in rows if r.get("_rate") is not None]
            if rate_rows:
                low = min(rate_rows, key=lambda r: (float(r["_rate"]), str(r["subject"])))
                bits.append(
                    f"贡献分达线率最低的科目是{low['subject']}"
                    f"（{low['track']} {_fmt_pct(low['_rate'])}，"
                    f"选该科的 {low['_rate_n']} 人中 {low['_rate_hit']} 人达到贡献分要求）"
                )
        flagged = [r for r in rows if r["flag"]]
        if len(flagged) >= _POLAR_FLAGGED:
            subs = "、".join(r["subject"] for r in flagged)
            bits.append(
                f"全班 {len(flagged)} 科（{subs}）均分低于{edge_line}边缘画像，"
                "两极分化明显，需重点抓后进生"
            )
        prefix = "重点关注：" if (worst_diff or 0) <= -_FOCUS_GAP else ""
        text = prefix + "；".join(bits) + "。"
        problems.append((worst_diff or 0.0, text))
    problems.sort(key=lambda pair: pair[0])
    recs = [text for _diff, text in problems]

    # 表扬：各线达线率最高且高于年级对照的班级（并列都列）。
    for line in main_lines:
        best_rate = None
        best: list[str] = []
        grade = grade_rates.get(line)
        for cid, label in labels.items():
            rate, _prev, _delta = rate_by.get((cid, line), (None, None, None))
            if rate is None or grade is None or rate <= grade:
                continue
            if best_rate is None or rate > best_rate:
                best_rate, best = rate, [label]
            elif rate == best_rate:
                best.append(label)
        if best:
            names = "、".join(best)
            recs.append(
                f"表现突出：{names}{line}达线率 {_fmt_pct(best_rate)}，"
                f"高于年级对照 {_fmt_pct(grade)}，居全校前列。"
            )

    if compare_exam:
        deltas = [
            (row["_delta"], row) for row in reach_rows if row["_delta"] is not None
        ]
        if deltas:
            best = max(deltas, key=lambda pair: pair[0])
            if best[0] > 0:
                row = best[1]
                recs.append(
                    f"对比{_esc_text(compare_exam)}：{row['class']}{row['line_name']}"
                    f"达线率进步 {_fmt_pp_abs(row['_delta'])} 个百分点，值得肯定。"
                )
            worst = min(deltas, key=lambda pair: pair[0])
            if worst[0] < 0:
                row = worst[1]
                recs.append(
                    f"对比{_esc_text(compare_exam)}：{row['class']}{row['line_name']}"
                    f"达线率退步 {_fmt_pp_abs(row['_delta'])} 个百分点，需要重点关注。"
                )
    if not recs:
        recs.append("各班各线达线率均不低于年级对照，且无退步线；暂无重点提示班级。")
    return [_esc_text(r) for r in recs]


def _fmt_pp_abs(delta: float) -> str:
    q = Decimal(str(abs(delta))).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    if q == q.to_integral():
        return str(int(q))
    return f"{q}"


def _line_scores(
    main_lines: list[str],
    tracks: tuple[str, ...],
    thresholds: dict[tuple[str, str, str], tuple[Decimal | None, str]],
    exam_name: str,
    compare_exam: str,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for line in main_lines:
        for track in tracks:
            curr_thr, _err = thresholds.get((exam_name, line, track), (None, ""))
            prev_thr = None
            if compare_exam:
                prev_thr = thresholds.get((compare_exam, line, track), (None, ""))[0]
            rows.append(
                {
                    "line_name": _esc_text(line),
                    "track": _esc_text(track),
                    "curr_line": _fmt_score(curr_thr),
                    "prev_line": _fmt_score(prev_thr) if compare_exam else "",
                }
            )
    return rows


def _cohort_note(
    school: str,
    scope: str,
    curr_groups: dict[tuple[str, str], list[_Stu]],
    excluded: dict[str, int],
    dups: int,
    prev_groups: dict[tuple[str, str], list[_Stu]],
    compare_on: bool,
) -> str:
    phy = sum(len(v) for (cid, track), v in curr_groups.items() if track == "物理类")
    his = sum(len(v) for (cid, track), v in curr_groups.items() if track == "历史类")
    bits = [
        f"{school}{scope}：本次考试在籍、方向明确且有六门总分的 {phy + his} 人"
        f"（物理类 {phy} 人，历史类 {his} 人）。"
    ]
    excluded_bits = [
        f"缺六门总分 {excluded.get('no_total', 0)} 人",
        f"方向无法识别 {excluded.get('no_track', 0)} 人",
        f"同一场学号重复因而整人排除 {dups} 人",
    ]
    bits.append("未进入分子和分母的人数：" + "，".join(excluded_bits) + "。")
    if compare_on:
        prev_n = sum(len(v) for v in prev_groups.values())
        bits.append(f"对比场有同口径成绩的 {prev_n} 人；对比场没有成绩的班级对比列显示「—」。")
    return "".join(bits)


def _index(
    rows: list[dict[str, Any]],
    schools: list[dict[str, Any]],
) -> tuple[dict[str, _Stu], int]:
    grouped: dict[str, list[_Stu]] = defaultdict(list)
    for row in rows or []:
        stu = _parse_row(row)
        if stu is None or not row_in_schools(stu.xx, schools):
            continue
        grouped[stu.sid].append(stu)
    out: dict[str, _Stu] = {}
    dups = 0
    for sid, items in grouped.items():
        if len(items) != 1:
            dups += 1
            continue
        out[sid] = items[0]
    return out, dups


def _parse_row(row: dict[str, Any]) -> _Stu | None:
    if not isinstance(row, dict):
        return None
    xsxz = str(pick_col(row, "xsxz") or "").strip()
    if xsxz != "在籍生":
        return None
    sid = str(pick_col(row, "anon_stu_id", "student_id") or "").strip()
    if not sid:
        return None
    return _Stu(
        sid=sid,
        xx=str(pick_col(row, "xx") or "").strip(),
        bj=str(pick_col(row, "bj") or "").strip(),
        track=infer_track(row),
        total=_decimal(pick_col(row, "zf6m")),
        scores={col: row.get(col) for col in _SUBJECT_COLS},
    )


def _groups(
    students: dict[str, _Stu],
) -> tuple[dict[tuple[str, str], list[_Stu]], dict[str, int]]:
    out: dict[tuple[str, str], list[_Stu]] = defaultdict(list)
    excluded = {"no_total": 0, "no_track": 0}
    for stu in students.values():
        if stu.total is None:
            excluded["no_total"] += 1
            continue
        if stu.track not in TRACK_ORDER:
            excluded["no_track"] += 1
            continue
        out[(_norm_class(stu.bj), stu.track)].append(stu)
    return dict(out), excluded


def _class_label(groups: dict[tuple[str, str], list[_Stu]], cid: str) -> str:
    for (c, _track), stus in groups.items():
        if c == cid and stus:
            for s in stus:
                if s.bj.strip():
                    return s.bj
    return cid


def _class_sort_key(label: str) -> tuple[str, int]:
    text = str(label or "")
    m = re.search(r"(\d+)", text)
    num = int(m.group(1)) if m else 9999
    prefix = re.sub(r"\d+", "", text)
    return (prefix, num)


def _ordered_unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _pct(num: int, den: int) -> float | None:
    if den <= 0:
        return None
    pct = (Decimal(num) * Decimal(100) / Decimal(den)).quantize(
        Decimal("0.1"), rounding=ROUND_HALF_UP
    )
    return float(pct)


def _fmt_pct(rate: float | None) -> str:
    if rate is None:
        return "—"
    q = Decimal(str(rate)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    return f"{q}%"


def _fmt_pp(delta: float | None) -> str:
    if delta is None:
        return "—"
    q = Decimal(str(delta)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    if q == 0:
        return "±0.0"
    if q > 0:
        return f"+{q}"
    return f"{q}"


def _fmt_num(value: Decimal | None) -> str:
    if value is None:
        return "—"
    q = value.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    if q == q.to_integral():
        return str(int(q))
    return f"{q}"


def _fmt_signed(value: Decimal | None) -> str:
    if value is None:
        return "—"
    q = value.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    if q == q.to_integral():
        n = int(q)
        return f"+{n}" if n > 0 else str(n)
    text = f"{q}"
    return text if text.startswith("-") else f"+{text}"


def _fmt_score(value: Decimal | None) -> str:
    if value is None:
        return "—"
    q = value.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    if q == q.to_integral():
        return str(int(q))
    return f"{q}"


def _dec_of(text: str) -> Decimal:
    try:
        return Decimal(str(text))
    except Exception:
        return Decimal("0")


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    try:
        num = Decimal(str(value))
    except Exception:
        return None
    if not num.is_finite():
        return None
    return num


def _norm_class(value: str) -> str:
    text = str(value or "").strip().replace("（", "(").replace("）", ")")
    return "".join(text.split())


def _esc(value: str) -> str:
    return str(value or "").replace("'", "''")


def _esc_text(value: str) -> str:
    return html.escape(str(value or ""), quote=True)
