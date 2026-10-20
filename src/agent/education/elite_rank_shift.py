"""尖子生排名变化分析报告：一届学生、多场考试的全市排名追踪。

纯计算，不调用模型。排名只认六门总分（``zf6m``），全市在籍生池，``RANK()`` 含并列。

口径（与报告正文一致）：

- 池按角色：教育局（bureau）= 全市前 N；校长或教育局下钻一所学校 = 本校前 N
  （本校学生按全市排名取前 N，含并列）；老师 = 本班前 N。N 档位 10/30/50/100，默认 30。
- 主名单锚定对比场（上一场）的池：看上一场的尖子生本次表现——上升/下降/掉出；
  新进 = 本次在池、上一场不在池（含上一场缺考后回归）。
- 历场走势 = 本次池 ∪ 上一场池学生在历场的全市排名折线（第 1 名画在最上），
  缺数据场次断线；超过 10 人只画本次排名最靠前的 10 人，其余进表。
- 退步预警：较上一场全市排名下降 ≥ 30 名，或最近三场连降两场。
- 学科归因：总排名下滑的学生给出单科排名降幅最大的前 2 科（单科排名 =
  该科全市选考在籍生中的名次，``RANK`` 含并列）；再选科目（化学/生物/政治/
  地理）按赋分列排名，与全系统对外展示口径一致；只归因该生选科组合内的六门。
- 老师账号在 SQL 层只能拉到所带班级的成绩（权限谓词注入主查询）；
  排名窗口保持全市口径，由 ``query_permission`` 的学生级排名池豁免保证。
- 脱敏开启时名单与走势线只显示学生编码；关闭后显示姓名。
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from typing import Any

from src.agent.education.class_line_reach import NO_COMPARE
from src.agent.education.line_reach import pick_col
from src.agent.education.line_shift import row_in_schools

__all__ = [
    "DEFAULT_TOP_N",
    "SUBJECT_RANK_COLS",
    "TOP_N_CHOICES",
    "WARN_DROP_RANK",
    "build_elite_rank_shift_report",
    "elite_rank_shift_request_error",
    "parse_top_n",
    "ranked_pool_sql",
]

#: N 档位（表单下拉）；默认 30
TOP_N_CHOICES = (10, 30, 50, 100)
DEFAULT_TOP_N = 30
#: 单场全市排名下降达到该名次即进退步预警
WARN_DROP_RANK = 30
#: 走势图最多画的线数；超出只画本次排名最靠前的这些
_TREND_MAX_LINES = 10
#: 单场最多拉回的行数（全市前 400 名 + 本校学生，带全市排名）
_POOL_FETCH_LIMIT = 400
#: 主表/新进表每行最多列出的退步/拉动科目数
_ATTR_SUBJECTS_TOP = 2

#: 单科排名列（科目名, 分数列, 排名列）。再选科目（化学/生物/政治/地理）
#: 按赋分列（转换分）排名，与 ``absence`` 等模块的对外展示口径一致。
SUBJECT_RANK_COLS: tuple[tuple[str, str, str], ...] = (
    ("语文", "yw", "rk_yw"),
    ("数学", "sx", "rk_sx"),
    ("英语", "yy", "rk_yy"),
    ("物理", "wl", "rk_wl"),
    ("历史", "ls", "rk_ls"),
    ("化学", "hxzh", "rk_hx"),
    ("生物", "swzh", "rk_sw"),
    ("政治", "zzzh", "rk_zz"),
    ("地理", "dlzh", "rk_dl"),
)


@dataclass
class _Stu:
    sid: str
    name: str = ""
    xx: str = ""
    bj: str = ""
    total: Any = None
    rk: int | None = None
    xkkm: str = ""
    subject_ranks: dict[str, int] = field(default_factory=dict)


def elite_rank_shift_request_error(
    role: str,
    school: str,
    class_name: str,
    exam_name: str,
    compare_exam: str = "",
) -> tuple[str, str] | None:
    """校验分析工具入参。通过返回 None；否则 ``(forbidden|bad, 文案)``。"""
    who = (role or "").strip()
    if who == "student":
        return ("forbidden", "学生账号不能生成尖子生排名变化分析报告")
    if not (exam_name or "").strip():
        return ("bad", "请选择这次考试")
    raw_compare = (compare_exam or "").strip()
    if raw_compare and raw_compare != NO_COMPARE and raw_compare == exam_name.strip():
        return ("bad", "对比考试必须与本次考试不同；只想看本场请选「不对比」")
    if who == "teacher" and not (class_name or "").strip():
        return (
            "bad",
            "请先选择班级。老师生成尖子生排名变化分析报告时只统计所带班级",
        )
    if who not in ("bureau_admin", "school_admin", "teacher") and not (school or "").strip():
        return (
            "bad",
            "请先选择学校。教育局账号可不选学校看全市；校长/老师自动统计本校/本班",
        )
    return None


def parse_top_n(raw: Any, default: int = DEFAULT_TOP_N) -> int:
    """N 档位解析：10/30/50/100 之外的值回默认。"""
    try:
        n = int(str(raw or "").strip())
    except (TypeError, ValueError):
        return default
    return n if n in TOP_N_CHOICES else default


def _clamp_n(raw: Any) -> int:
    """计算层收任意正整数（档位约束在表单层 ``parse_top_n``）。"""
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_TOP_N
    return n if n > 0 else DEFAULT_TOP_N


def ranked_pool_sql(exam_name: str, xx_names: list[str]) -> str:
    """一场考试的全市在籍生排名池行（总分 + 九门单科全市排名）。

    外层直接 ``FROM tb_score_overview``（行级权限谓词注入外层，老师/校长
    只拉回本班/本校）；``RANK()`` 窗口放 JOIN 子查询——全市口径，由
    ``query_permission::_select_is_student_rank_pool`` 豁免，不收窄。
    拉回范围 = 全市前 400 名 ∪ 点名学校学生（带各自的全市排名）。
    单科排名 = 该科全市选考在籍生中的名次（``NULLS LAST`` 使未选科沉底，
    Python 层按选科组合只取该生选的科目，未选科不参与归因）。
    """
    lit = _esc(exam_name)
    names = [n for n in xx_names if str(n or "").strip()]
    cond = "r.rk <= 400"
    if names:
        inn = ", ".join(f"'{_esc(n)}'" for n in names)
        cond = f"(r.rk <= 400 OR o.xx IN ({inn}))"
    windows = ", ".join(
        f"RANK() OVER (ORDER BY {score_col} DESC NULLS LAST) AS {rank_col}"
        for _label, score_col, rank_col in SUBJECT_RANK_COLS
    )
    outer_ranks = ", ".join(
        f"r.{rank_col}" for _label, _score, rank_col in SUBJECT_RANK_COLS
    )
    return (
        "SELECT o.exam_name, o.xm, o.anon_stu_id, o.xx, o.bj, o.xkkm, o.xsxz, o.zf6m, "
        f"r.rk, {outer_ranks} "
        "FROM tb_score_overview o "
        "JOIN ("
        "SELECT anon_stu_id, "
        "RANK() OVER (ORDER BY zf6m DESC NULLS LAST) AS rk, "
        f"{windows} "
        "FROM tb_score_overview "
        f"WHERE exam_name = '{lit}' AND xsxz = '在籍生'"
        ") r ON r.anon_stu_id = o.anon_stu_id "
        f"WHERE o.exam_name = '{lit}' AND o.xsxz = '在籍生' AND {cond} "
        "ORDER BY r.rk "
        "LIMIT 5000"
    )


def build_elite_rank_shift_report(
    *,
    role: str,
    exam_name: str,
    compare_exam: str,
    top_n: int,
    rows_by_exam: dict[str, list[dict[str, Any]]],
    schools: list[dict[str, Any]] | None = None,
    class_name: str = "",
    compare_note: str = "",
    curr_date: str = "",
    compare_date: str = "",
    report_time: str = "",
    trend_exams: list[str] | None = None,
) -> dict[str, Any]:
    """组装模板数据。

    ``rows_by_exam``：历场各场拉回的排名池行（``ranked_pool_sql`` 产出，
    含全市排名列 ``rk``）。视角过滤（本校/本班）在 Python 层再保险一次。
    """
    from src.agent.education.privacy_mode import is_anonymize_display_enabled

    anonymize = is_anonymize_display_enabled()
    who = (role or "").strip()
    n = _clamp_n(top_n)
    want_class = _norm_class(class_name)
    school_list = [s for s in (schools or []) if isinstance(s, dict)]

    def _scoped(exam: str) -> dict[str, _Stu]:
        out: dict[str, _Stu] = {}
        for row in rows_by_exam.get(exam) or []:
            stu = _parse_row(row)
            if stu is None:
                continue
            if want_class and _norm_class(stu.bj) != want_class:
                continue
            if school_list and not row_in_schools(stu.xx, school_list):
                continue
            if stu.sid not in out:
                out[stu.sid] = stu
        return out

    curr = _scoped(exam_name)
    prev = _scoped(compare_exam) if compare_exam else {}
    if not curr:
        raise ValueError(
            f"本次考试「{exam_name}」没有可统计的在籍生排名数据，请核对考试名称。"
        )

    curr_pool = _pool_of(curr, n)
    prev_pool = _pool_of(prev, n) if prev else {}
    curr_pool_ids = {s.sid for s in curr_pool}
    prev_pool_ids = {s.sid for s in prev_pool}

    # 教育局全市视角名单跨多校，主表/新进表要显示学校；下钻一校或校长/老师不必。
    show_school = who == "bureau_admin" and not school_list
    if show_school:
        pool_desc = f"全市前 {n} 名"
        scope_label = "全市"
    elif want_class:
        pool_desc = f"本班前 {n} 名"
        scope_label = _esc_text(class_name)
    else:
        pool_desc = f"本校前 {n} 名"
        scope_label = _school_label(school_list)

    main_rows, attributions = _main_rows(
        compare_exam=compare_exam,
        curr=curr,
        prev=prev,
        prev_pool=prev_pool,
        curr_pool_ids=curr_pool_ids,
        prev_pool_ids=prev_pool_ids,
        anonymize=anonymize,
    )
    new_rows = _new_rows(
        compare_exam=compare_exam,
        curr_pool=curr_pool,
        prev=prev,
        prev_pool_ids=prev_pool_ids,
        anonymize=anonymize,
    )
    subject_rows = _subject_summary(main_rows, new_rows)
    subject_chart = _subject_chart(subject_rows)
    trend_chart, trend_note = _trend_chart(
        trend_exams=trend_exams or [],
        rows_by_exam=rows_by_exam,
        want_class=want_class,
        schools=school_list,
        curr=curr,
        curr_pool=curr_pool,
        prev_pool=prev_pool,
        exam_name=exam_name,
        pool_desc=pool_desc,
        anonymize=anonymize,
    )
    recommendations = _recommendations(
        compare_exam=compare_exam,
        curr=curr,
        prev_pool_ids=prev_pool_ids,
        curr_pool=curr_pool,
        trend_exams=trend_exams or [],
        rows_by_exam=rows_by_exam,
        want_class=want_class,
        schools=school_list,
        anonymize=anonymize,
        pool_desc=pool_desc,
        attributions=attributions,
    )
    overview = _overview_text(
        compare_exam=compare_exam,
        pool_desc=pool_desc,
        prev_pool_ids=prev_pool_ids,
        curr_pool=curr_pool,
        curr_pool_ids=curr_pool_ids,
        main_rows=main_rows,
        curr=curr,
        anonymize=anonymize,
    )

    compare_on = bool(compare_exam)
    data = {
        "REPORT_TITLE": f"{scope_label}尖子生排名变化分析报告",
        "REPORT_TYPE": "尖子生排名变化分析报告",
        "REPORT_TIME": _esc_text(report_time),
        "SCOPE": scope_label,
        "POOL_DESC": pool_desc,
        "CURR_EXAM": _esc_text(exam_name),
        "CURR_DATE": _esc_text(curr_date),
        "COMPARE_EXAM": _esc_text(compare_exam),
        "COMPARE_DATE": _esc_text(compare_date),
        "COMPARE_NOTE": _esc_text(compare_note),
        "COMPARE_ON": compare_on,
        "SHOW_SCHOOL": show_school,
        "OVERVIEW": overview,
        "MAIN_ROWS": main_rows,
        "NEW_ROWS": new_rows,
        "SUBJECT_ROWS": subject_rows,
        "SUBJECT_CHART": subject_chart,
        "TREND_CHART": trend_chart,
        "TREND_NOTE": trend_note,
        "RECOMMENDATIONS": recommendations,
        "_stats": {
            "count": len(curr),
            "curr_pool": len(curr_pool),
            "prev_pool": len(prev_pool),
            "new_in": len(new_rows),
        },
    }
    return data


def _parse_row(row: dict[str, Any]) -> _Stu | None:
    if not isinstance(row, dict):
        return None
    sid = str(pick_col(row, "anon_stu_id", "student_id") or "").strip()
    if not sid:
        return None
    try:
        rk = int(pick_col(row, "rk", "rank") or 0)
    except (TypeError, ValueError):
        rk = None
    subject_ranks: dict[str, int] = {}
    for label, _score_col, rank_col in SUBJECT_RANK_COLS:
        try:
            v = int(pick_col(row, rank_col) or 0)
        except (TypeError, ValueError):
            continue
        if v > 0:
            subject_ranks[label] = v
    return _Stu(
        sid=sid,
        name=str(pick_col(row, "xm", "name") or "").strip(),
        xx=str(pick_col(row, "xx", "school_name") or "").strip(),
        bj=str(pick_col(row, "bj", "class_name") or "").strip(),
        total=pick_col(row, "zf6m"),
        rk=rk if rk and rk > 0 else None,
        xkkm=str(pick_col(row, "xkkm") or "").strip(),
        subject_ranks=subject_ranks,
    )


def _pool_of(students: dict[str, _Stu], n: int) -> list[_Stu]:
    """视角内学生按全市排名取前 N（含并列：第 N 名同分的都带）。"""
    rows = sorted(
        (s for s in students.values() if s.rk),
        key=lambda s: (s.rk, s.sid),
    )
    if not rows:
        return []
    cut = min(n, len(rows)) - 1
    cut_rk = rows[cut].rk
    return [s for s in rows if s.rk <= cut_rk]


def _subject_shifts(
    prev_stu: _Stu | None, curr_stu: _Stu | None
) -> list[tuple[str, int, int, int]]:
    """单科排名变化：``[(科目, 上一场排名, 本次排名, 变化)]``，变化正为上升。

    只算该生选科组合内的六门（``subjects_of_combo``），两场都有单科排名才进。
    """
    from src.agent.education.absence import subjects_of_combo

    if prev_stu is None or curr_stu is None:
        return []
    subjects = None
    for stu in (prev_stu, curr_stu):
        subjects = subjects_of_combo(stu.xkkm)
        if subjects:
            break
    if not subjects:
        return []
    out: list[tuple[str, int, int, int]] = []
    for label, _col in subjects:
        a = prev_stu.subject_ranks.get(label)
        b = curr_stu.subject_ranks.get(label)
        if a and b:
            out.append((label, a, b, a - b))
    return out


def _fmt_shifts(
    shifts: list[tuple[str, int, int, int]], *, drop: bool, top: int = _ATTR_SUBJECTS_TOP
) -> str:
    """拖分/拉动科目文案：「物理 156→689、数学 312→645」。

    ``drop`` 取降幅最大的前 ``top`` 科；否则取升幅最大的前 ``top`` 科。
    """
    picked = [s for s in shifts if (s[3] < 0 if drop else s[3] > 0)]
    # drop：降幅最大在前 = 变化值最小在前；lift：升幅最大在前 = 变化值最大在前
    picked.sort(key=lambda s: s[3], reverse=not drop)
    picked = picked[:top]
    if not picked:
        return ""
    return "、".join(f"{label} {a}→{b}" for label, a, b, _d in picked)


def _is_drop_row(row: dict[str, Any]) -> bool:
    """真实排名下滑的行：变化文案以「下降」开头（掉出但并列挤出的不算）。"""
    return str(row.get("delta") or "").startswith("下降")


def _display(stu: _Stu | None, idx: int, anonymize: bool) -> str:
    """名单显示名：脱敏开=学生编码（走势线用序号），关=姓名。"""
    if stu is None:
        return "—"
    if anonymize:
        return f"学生{idx}"
    return _esc_text(stu.name or stu.sid)


def _main_rows(
    *,
    compare_exam: str,
    curr: dict[str, _Stu],
    prev: dict[str, _Stu],
    prev_pool: list[_Stu],
    curr_pool_ids: set[str],
    prev_pool_ids: set[str],
    anonymize: bool,
) -> tuple[list[dict[str, str]], dict[str, str]]:
    """主表：锚定上一场池看本次表现；不对比时 = 本次池名单。

    返回 ``(行列表, 学号→主要退步科目文案)``，后者供预警文案带学科。
    """
    if not compare_exam:
        pool = sorted(
            (s for s in curr.values() if s.sid in curr_pool_ids),
            key=lambda s: (s.rk or 10**9, s.sid),
        )
        rows = []
        for i, stu in enumerate(pool, 1):
            rows.append(
                {
                    "idx": str(i),
                    "name": _display(stu, i, anonymize),
                    "school": _school_cell(stu.xx),
                    "class": _esc_text(stu.bj) or "—",
                    "prev_rank": "—",
                    "curr_rank": str(stu.rk or "—"),
                    "delta": "—",
                    "status": "在池",
                    "total": _fmt_total(stu.total),
                    "subjects": "",
                    "_shifts": [],
                }
            )
        return rows, {}

    rows: list[dict[str, Any]] = []
    for stu in prev_pool:
        now = curr.get(stu.sid)
        prev_rk = stu.rk
        if now is None or now.rk is None:
            rows.append(
                {
                    "idx": "",
                    "name": "",
                    "_sid": stu.sid,
                    "school": _school_cell(stu.xx),
                    "class": _esc_text(stu.bj) or "—",
                    "prev_rank": str(prev_rk or "—"),
                    "curr_rank": "缺考/无成绩",
                    "delta": "—",
                    "status": "掉出（本次无成绩）",
                    "total": "—",
                    "subjects": "—",
                    "_shifts": [],
                }
            )
            continue
        delta = (prev_rk or 0) - now.rk
        in_pool = stu.sid in curr_pool_ids
        shifts = _subject_shifts(stu, now)
        # 只有总排名真实下滑才标拖分科目（升/持平/并列挤出不算退步）
        subjects = _fmt_shifts(shifts, drop=True) if delta < 0 else ""
        rows.append(
            {
                "idx": "",
                "name": "",
                "_sid": stu.sid,
                "school": _school_cell(now.xx or stu.xx),
                "class": _esc_text(now.bj) or "—",
                "prev_rank": str(prev_rk or "—"),
                "curr_rank": str(now.rk),
                "delta": _fmt_delta(delta),
                "status": "稳住" if in_pool else f"掉出（现排第 {now.rk} 名）",
                "total": _fmt_total(now.total),
                "subjects": _esc_text(subjects) or "—",
                "_shifts": shifts,
            }
        )
    # 退步最狠的排最前：缺考（无本次排名）最前，然后降幅降序。
    rows.sort(
        key=lambda r: (
            0 if r["curr_rank"] == "缺考/无成绩" else 1,
            _delta_value(r["delta"]),
            _rank_key(r["curr_rank"]),
        )
    )
    out: list[dict[str, str]] = []
    attributions: dict[str, str] = {}
    for i, r in enumerate(rows, 1):
        sid = str(r.pop("_sid", "") or "")
        stu = next((s for s in prev_pool if s.sid == sid), None)
        subjects = str(r.get("subjects") or "")
        if sid and subjects not in ("", "—"):
            attributions[sid] = subjects
        out.append({**r, "idx": str(i), "name": _display(stu, i, anonymize)})
    return out, attributions


def _new_rows(
    *,
    compare_exam: str,
    curr_pool: list[_Stu],
    prev: dict[str, _Stu],
    prev_pool_ids: set[str],
    anonymize: bool,
) -> list[dict[str, Any]]:
    """本次新进池名单（上一场不在池，含缺考后回归），带拉动学科。"""
    if not compare_exam:
        return []
    fresh = [s for s in curr_pool if s.sid not in prev_pool_ids]
    fresh.sort(key=lambda s: (s.rk or 0, s.sid))
    out: list[dict[str, Any]] = []
    for i, s in enumerate(fresh, 1):
        shifts = _subject_shifts(prev.get(s.sid), s)
        lift = _esc_text(_fmt_shifts(shifts, drop=False)) or "—"
        out.append(
            {
                "idx": str(i),
                "name": _display(s, i, anonymize),
                "school": _school_cell(s.xx),
                "class": _esc_text(s.bj) or "—",
                "curr_rank": str(s.rk or "—"),
                "total": _fmt_total(s.total),
                "lift_subjects": lift,
                "_lifts": shifts,
            }
        )
    return out


def _subject_summary(
    main_rows: list[dict[str, Any]], new_rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """学科归因汇总：池内下滑学生的单科退步 + 新进学生的单科拉动。

    只统计总排名真实下滑的学生（``delta`` 文案以「下降」开头）的单科降幅；
    排序 = 平均单科降幅降序 → 退步人数降序 → 科目名。
    """
    drop_amts: dict[str, list[int]] = {}
    for row in main_rows:
        if not _is_drop_row(row):
            continue
        for label, _a, _b, d in row.get("_shifts") or []:
            if d < 0:
                drop_amts.setdefault(label, []).append(-d)
    lift_cnt: dict[str, int] = {}
    for row in new_rows:
        for label, _a, _b, d in row.get("_lifts") or []:
            if d > 0:
                lift_cnt[label] = lift_cnt.get(label, 0) + 1

    stats: list[tuple[str, int, int, int]] = []
    for label in sorted(set(drop_amts) | set(lift_cnt)):
        drops = drop_amts.get(label) or []
        avg = round(sum(drops) / len(drops)) if drops else 0
        stats.append((label, len(drops), avg, lift_cnt.get(label, 0)))
    stats.sort(key=lambda t: (-t[2], -t[1], t[0]))
    return [
        {
            "subject": label,
            "drop_n": str(drop_n),
            "avg_drop": str(avg) if drop_n else "—",
            "lift_n": str(lift_n),
            "_avg_drop": avg,
        }
        for label, drop_n, avg, lift_n in stats
    ]


def _subject_chart(subject_rows: list[dict[str, Any]]) -> str:
    """学科归因汇总柱图：各科平均单科降幅。"""
    if not subject_rows:
        return ""
    from src.agent.education.charts import build_chart_option

    return build_chart_option(
        "subject_bar",
        {
            "subjects": [r["subject"] for r in subject_rows],
            "metrics": [
                {
                    "name": "平均单科降幅（名）",
                    "values": [r.get("_avg_drop") or 0 for r in subject_rows],
                }
            ],
        },
        title="各科平均单科降幅（池内下滑学生）",
    )


def _trend_chart(
    *,
    trend_exams: list[str],
    rows_by_exam: dict[str, list[dict[str, Any]]],
    want_class: str,
    schools: list[dict[str, Any]],
    curr: dict[str, _Stu],
    curr_pool: list[_Stu],
    prev_pool: list[_Stu],
    exam_name: str,
    pool_desc: str,
    anonymize: bool,
) -> tuple[str, str]:
    """历场全市排名走势：池并集各一条线，第 1 名画在最上。"""
    from src.agent.education.charts import build_chart_option

    exams = [e for e in trend_exams if str(e or "").strip()]
    if len(exams) < 2:
        return "", ""

    union: dict[str, _Stu] = {}
    for stu in (*curr_pool, *prev_pool):
        union[stu.sid] = stu
    if not union:
        return "", ""

    # 排名轨迹：每场拉回行 -> 视角过滤 -> sid -> rk
    rk_by_exam: dict[str, dict[str, int]] = {}
    for exam in exams:
        scoped: dict[str, _Stu] = {}
        for row in rows_by_exam.get(exam) or []:
            stu = _parse_row(row)
            if stu is None:
                continue
            if want_class and _norm_class(stu.bj) != want_class:
                continue
            if schools and not row_in_schools(stu.xx, schools):
                continue
            if stu.sid not in scoped:
                scoped[stu.sid] = stu
        rk_by_exam[exam] = {sid: s.rk for sid, s in scoped.items() if s.rk}

    ordered = sorted(
        union.values(),
        key=lambda s: (
            (s.rk if s.rk else 10**9),
            s.sid,
        ),
    )[:_TREND_MAX_LINES]

    x_labels = [
        _short_exam(e) + ("·本次" if e == exam_name else "") for e in exams
    ]
    series = []
    for i, stu in enumerate(ordered, 1):
        values = [rk_by_exam.get(e, {}).get(stu.sid) for e in exams]
        if not any(v is not None for v in values):
            continue
        series.append({"name": _display(stu, i, anonymize), "values": values})

    if not series:
        return "", ""
    chart = build_chart_option(
        "trend_line",
        {
            "x_labels": x_labels,
            "series": series,
            "y_name": "全市排名",
            "y_inverse": True,
        },
        title=f"{pool_desc}历场全市排名走势",
    )
    note = (
        "本届历次考试同口径全市排名（含并列），第 1 名画在最上，线往下即排名下滑；"
        "某场缺数据则该点断线；标「·本次」为本次考试。"
        f"超过 {_TREND_MAX_LINES} 人只画本次排名最靠前的 {_TREND_MAX_LINES} 人，其余见上表。"
    )
    return chart, note


def _recommendations(
    *,
    compare_exam: str,
    curr: dict[str, _Stu],
    prev_pool_ids: set[str],
    curr_pool: list[_Stu],
    trend_exams: list[str],
    rows_by_exam: dict[str, list[dict[str, Any]]],
    want_class: str,
    schools: list[dict[str, Any]],
    anonymize: bool,
    pool_desc: str,
    attributions: dict[str, str],
) -> list[str]:
    """退步预警：较上一场下降 ≥ 30 名，或最近三场连降两场（带主要拖分科目）。"""
    union: dict[str, _Stu] = {}
    for stu in (*curr_pool, *(curr[sid] for sid in prev_pool_ids if sid in curr)):
        union[stu.sid] = stu
    if not union:
        return []

    # 历场排名轨迹（走势图同口径）
    exams = [e for e in trend_exams if str(e or "").strip()]
    rk_by_exam: dict[str, dict[str, int]] = {}
    for exam in exams:
        scoped: dict[str, _Stu] = {}
        for row in rows_by_exam.get(exam) or []:
            stu = _parse_row(row)
            if stu is None:
                continue
            if want_class and _norm_class(stu.bj) != want_class:
                continue
            if schools and not row_in_schools(stu.xx, schools):
                continue
            if stu.sid not in scoped:
                scoped[stu.sid] = stu
        rk_by_exam[exam] = {sid: s.rk for sid, s in scoped.items() if s.rk}

    ordered = sorted(union.values(), key=lambda s: (s.rk if s.rk else 10**9, s.sid))
    recs: list[str] = []
    for i, stu in enumerate(ordered, 1):
        name = _display(stu, i, anonymize)
        seq = [rk_by_exam.get(e, {}).get(stu.sid) for e in exams]
        # 归因后缀：主要拖分科目（该生在主表归因里且非空）
        attr = attributions.get(stu.sid) or ""
        attr_part = f"，主要拖分：{attr}" if attr else ""
        # 最近三场连降两场（三场都有排名且逐场下滑）
        dropped2 = False
        if len(seq) >= 3:
            a, b, c = seq[-3], seq[-2], seq[-1]
            if a and b and c and a < b < c:
                dropped2 = True
        # 较上一场下降 ≥ 30 名
        big_drop = False
        if compare_exam and len(seq) >= 2:
            a, b = seq[-2], seq[-1]
            if a and b and (b - a) >= WARN_DROP_RANK:
                big_drop = True
        if dropped2:
            a, b, c = seq[-3], seq[-2], seq[-1]
            recs.append(
                f"退步预警：{name}最近三场全市排名连降两场"
                f"（第 {a}→{b}→{c} 名）{attr_part}，建议重点干预。"
            )
        elif big_drop:
            a, b = seq[-2], seq[-1]
            recs.append(
                f"退步预警：{name}较上一场全市排名下降 {b - a} 名"
                f"（第 {a}→{b} 名）{attr_part}，{pool_desc}需重点关注。"
            )
        if len(recs) >= 5:
            break
    return [_esc_text(r) for r in recs]


def _overview_text(
    *,
    compare_exam: str,
    pool_desc: str,
    prev_pool_ids: set[str],
    curr_pool: list[_Stu],
    curr_pool_ids: set[str],
    main_rows: list[dict[str, str]],
    curr: dict[str, _Stu],
    anonymize: bool,
) -> str:
    """总览一句话：池去向 → 升降之最 → 整体判断。"""
    if not compare_exam:
        top = ", ".join(
            f"{r['name']}（第 {r['curr_rank']} 名）" for r in main_rows[:3]
        )
        bits = [f"本次考试{pool_desc}共 {len(curr_pool)} 人"]
        if top:
            bits.append(f"前 3 名：{top}")
        return _esc_text("；".join(bits) + "。")

    prev_n = len(prev_pool_ids)
    hold = len(prev_pool_ids & curr_pool_ids)
    out_rows = [r for r in main_rows if r["status"].startswith("掉出")]
    out_n = len(out_rows)
    new_n = sum(1 for r in (curr_pool or []) if r.sid not in prev_pool_ids)
    bits = [
        f"上一场{pool_desc}共 {prev_n} 人，本次稳住 {hold} 人、掉出 {out_n} 人；"
        f"本次新进 {new_n} 人"
    ]
    up = _extreme(main_rows, "上升")
    down = _extreme(main_rows, "下降")
    if up:
        bits.append(f"升幅最大：{up}")
    if down:
        bits.append(f"降幅最大：{down}")
    turnover = out_n + new_n
    if prev_n and turnover * 3 > prev_n:
        bits.append(f"掉出与新进合计 {turnover} 人，超过池的三分之一，头部换血明显")
    else:
        bits.append("头部基本稳定，按表内降幅逐人跟进即可")
    return _esc_text("；".join(bits) + "。")


def _extreme(rows: list[dict[str, str]], word: str) -> str:
    """升降之最：变化文案里含「上升/下降」且幅度最大的那行。"""
    best: tuple[int, str] | None = None
    for r in rows:
        m = re.match(rf"^{word} (\d+) 名$", str(r.get("delta") or ""))
        if not m:
            continue
        v = int(m.group(1))
        if best is None or v > best[0]:
            best = (v, f"{r['name']}（第 {r['prev_rank']}→{r['curr_rank']} 名，{word} {v} 名）")
    return best[1] if best else ""


def _fmt_delta(delta: int) -> str:
    if delta > 0:
        return f"上升 {delta} 名"
    if delta < 0:
        return f"下降 {-delta} 名"
    return "持平"


def _delta_value(delta_text: str) -> int:
    m = re.match(r"^(?:上升|下降) (\d+) 名$", str(delta_text or ""))
    neg = 1 if str(delta_text or "").startswith("上升") else -1
    return neg * int(m.group(1)) if m else 0


def _rank_key(rank_text: str) -> int:
    try:
        return int(str(rank_text))
    except (TypeError, ValueError):
        return 10**9


def _fmt_total(total: Any) -> str:
    try:
        v = float(total)
    except (TypeError, ValueError):
        return "—"
    if v == int(v):
        return str(int(v))
    return f"{v:g}"


def _norm_class(value: str) -> str:
    text = str(value or "").strip().replace("（", "(").replace("）", ")")
    return "".join(text.split())


def _school_cell(xx: str) -> str:
    """名单学校列：去掉校码前缀（A02扬大附中 → 扬大附中）。"""
    from src.agent.education.subject_research import base_school_name

    raw = str(xx or "").strip()
    if not raw:
        return "—"
    return _esc_text(base_school_name(raw) or raw)


def _school_label(schools: list[dict[str, Any]]) -> str:
    from src.agent.education.subject_research import base_school_name

    labels: list[str] = []
    for sch in schools or []:
        raw = str(sch.get("s_name") or "").strip()
        label = base_school_name(raw) or raw
        if label and label not in labels:
            labels.append(label)
    return _esc_text("、".join(labels)) if labels else "本校"


def _esc(value: str) -> str:
    return str(value or "").replace("'", "''")


def _esc_text(value: str) -> str:
    return html.escape(str(value or ""), quote=True)


def _short_exam(exam: str) -> str:
    """考试名压短：去掉开头的「届次+年级」前缀（2026届高三1月期末 → 1月期末）。"""
    text = str(exam or "").strip()
    short = re.sub(r"^\d{4}届\s*(高三|高二|高一)?", "", text)
    return short or text
