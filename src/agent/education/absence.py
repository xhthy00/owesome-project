"""缺考分析报告：一所学校、一场考试的缺考学生名单。

纯计算，不调用模型。只看在籍生。

判定口径（与报告正文一致）：

- 该考的科目：语文、数学、英语，加上首选科目（物理或历史），加上两门再选科目。
  再选与首选都从 ``xkkm`` 选科组合里读。组合里没有的科目不计缺考，
  所以未选考的 0 分不会算成缺考。
- 分数口径：语数英与首选（物理/历史）用原始分；再选科目（化学/生物/政治/地理）
  用赋分列 ``hxzh/swzh/zzzh/dlzh``（新高考转换分），与全系统对外展示口径一致，
  禁止用卷面列 hx/sw/zz/dl。
- 该考科目里有一门是 0 分或没有分，就算该门缺考。该门 0 分与缺考记法相同。
- 这场在本校没有成绩行、且全市任何学校都没有这个人的成绩行，算整场缺考。
  在别的学校有这场成绩行的，不算本校缺考。
- 上一场取考试时间早于这场、且最近的一场。取不到时不猜，报告写明无法识别整场缺考。
"""

from __future__ import annotations

import html
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from src.agent.education.line_reach import pick_col

__all__ = [
    "absence_request_error",
    "build_absence_report",
    "existing_sids_sql",
    "overview_subjects_sql",
    "pick_prev_exam_or_none",
    "subjects_of_combo",
]

#: 常见科目（语数英）。首选/再选由选科组合决定。
_COMMON_SUBJECTS: tuple[tuple[str, str], ...] = (
    ("语文", "yw"),
    ("数学", "sx"),
    ("英语", "yy"),
)

#: 选科组合里的单字 → (科目名, 列名, 是否首选)。再选科目用赋分列（转换分）。
_COMBO_CHARS: dict[str, tuple[str, str, bool]] = {
    "物": ("物理", "wl", True),
    "历": ("历史", "ls", True),
    "史": ("历史", "ls", True),
    "化": ("化学", "hxzh", False),
    "生": ("生物", "swzh", False),
    "政": ("政治", "zzzh", False),
    "地": ("地理", "dlzh", False),
}

_SUBJECT_COLS = ("yw", "sx", "yy", "wl", "hxzh", "swzh", "zzzh", "ls", "dlzh")


@dataclass
class _Stu:
    sid: str
    name: str
    xx: str
    bj: str
    xkkm: str
    scores: dict[str, Any] = field(default_factory=dict)
    combo_label: str = ""


def absence_request_error(
    role: str,
    school: str,
    class_name: str,
    exam_name: str,
) -> tuple[str, str] | None:
    """校验分析工具入参。通过返回 None；否则 ``(forbidden|bad, 文案)``。"""
    who = (role or "").strip()
    if who == "student":
        return ("forbidden", "学生账号不能生成缺考分析报告")
    if not (exam_name or "").strip():
        return ("bad", "请选择这次考试")
    if who == "teacher" and not (class_name or "").strip():
        return ("bad", "请先选择班级。老师生成缺考分析报告时只统计所带班级")
    if who not in ("school_admin", "teacher") and not (school or "").strip():
        return ("bad", "请先选择学校。缺考分析报告按一所学校统计")
    return None


def pick_prev_exam_or_none(
    batches: list[dict[str, Any]] | None,
    exam_name: str,
) -> str | None:
    """按考试时间取这次之前最近的一场。取不到返回 None，绝不猜。"""
    from src.agent.education.line_reach_report import ordered_exam_names, pick_previous_exam

    names = ordered_exam_names(batches or [])
    if not names:
        return None
    prev = pick_previous_exam(names, exam_name)
    return prev or None


def subjects_of_combo(xkkm: str) -> list[tuple[str, str]] | None:
    """选科组合 → 六门 (科目名, 列名)。组合不合法返回 None，不猜。

    合法组合：恰好一门首选（物理或历史），恰好两门再选（化学/生物/政治/地理）。
    """
    text = str(xkkm or "").strip()
    if not text:
        return None
    firsts: list[tuple[str, str]] = []
    reselects: list[tuple[str, str]] = []
    seen_labels: set[str] = set()
    for ch in text:
        hit = _COMBO_CHARS.get(ch)
        if hit is None:
            continue
        label, col, is_first = hit
        if label in seen_labels:
            return None
        seen_labels.add(label)
        if is_first:
            firsts.append((label, col))
        else:
            reselects.append((label, col))
    if len(firsts) != 1 or len(reselects) != 2:
        return None
    return list(_COMMON_SUBJECTS) + firsts + reselects


def overview_subjects_sql(exam_name: str, xx_names: list[str]) -> str:
    """一所学校、一场考试的在籍生，带九门科目分。

    再选科目（化学/生物/政治/地理）取赋分列 hxzh/swzh/zzzh/dlzh（转换分）。
    """
    names = [n for n in xx_names if str(n or "").strip()]
    if not names:
        raise ValueError("缺少学校名称，拒绝拉取全库成绩")
    lit = _esc(exam_name)
    inn = ", ".join(f"'{_esc(n)}'" for n in names)
    return (
        "SELECT exam_name, xm, anon_stu_id, xx, bj, xkkm, xsxz, "
        "yw, sx, yy, wl, hxzh, swzh, zzzh, ls, dlzh "
        "FROM tb_score_overview "
        f"WHERE exam_name = '{lit}' "
        "AND xsxz = '在籍生' "
        f"AND xx IN ({inn}) "
        "LIMIT 200000"
    )


def existing_sids_sql(exam_name: str, sids: list[str]) -> str:
    """某个学号列表在这场考试里、任意学校是否有成绩行。不限 xsxz：有行就算参考过。"""
    wanted = [s for s in sids if str(s or "").strip()]
    if not wanted:
        raise ValueError("学号列表为空")
    lit = _esc(exam_name)
    inn = ", ".join(f"'{_esc(s)}'" for s in wanted)
    return (
        "SELECT DISTINCT anon_stu_id FROM tb_score_overview "
        f"WHERE exam_name = '{lit}' "
        f"AND anon_stu_id IN ({inn})"
    )


def build_absence_report(
    *,
    exam_name: str,
    prev_exam: str,
    curr_rows: list[dict[str, Any]],
    prev_rows: list[dict[str, Any]],
    existing_sids: set[str],
    schools: list[dict[str, Any]],
    class_name: str = "",
    anonymize: bool = True,
    report_time: str = "",
) -> dict[str, Any]:
    """组装模板数据。整场缺考的人用上一场的行补，科目按上一场选科组合。"""
    school_label = _school_label(schools)
    curr_all, curr_dups = _index(curr_rows, schools)
    prev_all, prev_dups = _index(prev_rows, schools)

    want_class = _norm_class(class_name)
    curr_scope = {
        sid: stu
        for sid, stu in curr_all.items()
        if not want_class or _norm_class(stu.bj) == want_class
    }

    partial: list[dict[str, str]] = []
    unknown: list[dict[str, str]] = []
    for sid, stu in curr_scope.items():
        subjects = subjects_of_combo(stu.xkkm)
        if subjects is None:
            prev_stu = prev_all.get(sid)
            if prev_stu is not None:
                subjects = subjects_of_combo(prev_stu.xkkm)
        if subjects is None:
            unknown.append(_person(stu, stu.xkkm, anonymize, taken="", missing=""))
            continue
        missing = [label for label, col in subjects if _subject_missing(stu.scores.get(col))]
        if not missing:
            continue
        taken = _taken_text(stu, subjects)
        partial.append(_person(stu, stu.combo_label, anonymize, taken=taken, missing=missing))

    full: list[dict[str, str]] = []
    if prev_exam:
        for sid, prev_stu in prev_all.items():
            if sid in curr_all or sid in existing_sids:
                continue
            if want_class and _norm_class(prev_stu.bj) != want_class:
                continue
            subjects = subjects_of_combo(prev_stu.xkkm)
            if subjects is None:
                unknown.append(_person(prev_stu, prev_stu.xkkm, anonymize, taken="", missing=""))
                continue
            missing = [label for label, _col in subjects]
            full.append(
                _person(prev_stu, prev_stu.combo_label, anonymize, taken="", missing=missing)
            )

    for lst in (full, partial, unknown):
        lst.sort(key=lambda p: (p["class"], p["name"]))

    note_bits: list[str] = []
    if not prev_exam:
        note_bits.append(
            "没有可用的上一场考试，整场缺考的学生无法识别。"
            "整场缺考名单为空不代表没有人整场缺考。"
        )
    else:
        note_bits.append(f"上一场（用于识别整场缺考）：{_esc_text(prev_exam)}。")
    if want_class:
        note_bits.append(
            f"只统计班级「{_esc_text(class_name)}」：这次有成绩的按这次班级，"
            "整场缺考的按上一场班级。"
        )
    scope = _esc_text(class_name) if class_name else "全校"
    return {
        "REPORT_TITLE": f"{school_label}{_esc_text(class_name)}缺考分析报告",
        "REPORT_TYPE": "缺考分析报告",
        "REPORT_TIME": _esc_text(report_time),
        "SCHOOL_LABEL": school_label,
        "CLASS_LABEL": scope,
        "EXAM_NAME": _esc_text(exam_name),
        "PREV_EXAM": _esc_text(prev_exam),
        "NOTE": " ".join(note_bits),
        "CURR_COUNT": len(curr_scope),
        "PREV_COUNT": len(prev_all),
        "FULL_COUNT": len(full),
        "PARTIAL_COUNT": len(partial),
        "UNKNOWN_COUNT": len(unknown),
        "DUP_COUNT": curr_dups + prev_dups,
        "FULL_LIST": full,
        "PARTIAL_LIST": partial,
        "UNKNOWN_LIST": unknown,
        "_stats": {"count": len(curr_scope) + len(prev_all)},
    }


def _subject_missing(score: Any) -> bool:
    """该考科目 0 分或没有分，算缺考。该科 0 分与缺考记法相同。"""
    if score is None or score == "":
        return True
    try:
        return Decimal(str(score)) <= 0
    except Exception:
        return True


def _taken_text(stu: _Stu, subjects: list[tuple[str, str]]) -> str:
    bits: list[str] = []
    for label, col in subjects:
        raw = stu.scores.get(col)
        if _subject_missing(raw):
            continue
        bits.append(f"{label} {_fmt_num(raw)}")
    return "、".join(bits)


def _person(
    stu: _Stu,
    combo_label: str,
    anonymize: bool,
    *,
    taken: str,
    missing: list[str] | None = None,
) -> dict[str, str]:
    shown = stu.sid if anonymize else (stu.name or stu.sid)
    return {
        "name": _esc_text(shown),
        "class": _esc_text(stu.bj),
        "combo": _esc_text(combo_label),
        "missing": _esc_text("、".join(missing or [])),
        "taken": _esc_text(taken),
    }


def _index(
    rows: list[dict[str, Any]],
    schools: list[dict[str, Any]],
) -> tuple[dict[str, _Stu], int]:
    from src.agent.education.line_shift import row_in_schools

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
    scores = {col: row.get(col) for col in _SUBJECT_COLS}
    combo = str(pick_col(row, "xkkm") or "").strip()
    return _Stu(
        sid=sid,
        name=str(pick_col(row, "xm") or "").strip(),
        xx=str(pick_col(row, "xx") or "").strip(),
        bj=str(pick_col(row, "bj") or "").strip(),
        xkkm=combo,
        scores=scores,
        combo_label=_combo_label(combo),
    )


def _combo_label(combo: str) -> str:
    parts: list[str] = []
    for ch in str(combo or "").strip():
        hit = _COMBO_CHARS.get(ch)
        if hit is not None and hit[0] not in parts:
            parts.append(hit[0])
    return "".join(parts)


def _school_label(schools: list[dict[str, Any]]) -> str:
    from src.agent.education.subject_research import strip_school_code

    labels: list[str] = []
    for sch in schools or []:
        raw = str(sch.get("s_name") or "").strip()
        label = strip_school_code(raw) or raw
        if label and label not in labels:
            labels.append(label)
    if not labels:
        raise ValueError("未找到学校，请从列表里选择库内学校名称")
    return _esc_text("、".join(labels))


def _norm_class(value: str) -> str:
    text = str(value or "").strip().replace("（", "(").replace("）", ")")
    return "".join(text.split())


def _fmt_num(value: Any) -> str:
    try:
        num = Decimal(str(value))
    except Exception:
        return str(value)
    q = num.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    if q == q.to_integral():
        return str(int(q))
    return f"{q}"


def _esc(value: str) -> str:
    return str(value or "").replace("'", "''")


def _esc_text(value: str) -> str:
    return html.escape(str(value or ""), quote=True)
