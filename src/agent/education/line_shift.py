"""临界生分析报告：两场考试、同一批在籍生的达线变化。

纯计算，不调用模型。达线只认六门总分（``zf6m``），不用四门或三门总分。

口径（与报告正文一致）：

- 巩固率、掉线率的基数：上次已达线，且这次有六门总分。两者人数之和等于基数。
- 转化率的基数：上次未达线，且上次总分在 ``[线分-15, 线分)``，且这次有六门总分。
- 达线：该场总分 ``>=`` 该场、该方向的这条线。
- 物理类、历史类分开；每条线分开。只考一场、缺总分、方向不一致的人不进分子也不进分母。
"""

from __future__ import annotations

import html
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from src.agent.education.line_reach import canon_line_name, infer_track, pick_col
from src.agent.education.subject_research import (
    base_school_name,
    campus_of,
    chapter_spec,
    strip_school_code,
)

__all__ = [
    "BELOW_MARGIN",
    "SPRINT_LINES",
    "build_line_shift_report",
    "line_shift_request_error",
    "exam_date_text",
    "order_two_exams",
    "overview_enrolled_sql",
    "parse_exam_pair",
    "row_in_schools",
    "select_line_shift_schools",
    "xx_literals",
]

BELOW_MARGIN = Decimal("15")
SPRINT_LINES = ("985线", "清北线", "南大线")
_TRACKS = ("物理类", "历史类")


def line_shift_request_error(
    role: str,
    school: str,
    class_name: str,
    exam_name: str,
) -> tuple[str, str] | None:
    """校验分析工具入参。通过返回 None；否则 ``(forbidden|bad, 文案)``。"""
    who = (role or "").strip()
    if who == "student":
        return ("forbidden", "学生账号不能生成临界生分析报告")
    try:
        parse_exam_pair(exam_name)
    except ValueError as exc:
        return ("bad", str(exc))
    if who == "teacher" and not (class_name or "").strip():
        return (
            "bad",
            "请先选择班级。老师生成临界生分析报告时只输出所带班级，不输出全校名单",
        )
    if who not in ("school_admin", "teacher") and not (school or "").strip():
        return (
            "bad",
            "请先选择学校。临界生分析报告按一所学校计算，不把多校合在一张表里",
        )
    return None


def parse_exam_pair(raw: str) -> tuple[str, str]:
    """分析工具把多场考试用 ``;;`` 拼成一个字符串。必须正好两场，且不能相同。"""
    parts = [p.strip() for p in str(raw or "").split(";;") if p.strip()]
    if len(parts) != 2 or parts[0] == parts[1]:
        raise ValueError("请选择两场不同的考试")
    return parts[0], parts[1]


def order_two_exams(
    first: str,
    second: str,
    batches: list[dict[str, Any]] | None,
) -> tuple[str, str, str]:
    """按考试时间区分上次、这次。没有可区分的时间时保持选择顺序，并写明。"""
    times = _exam_times(batches or [])
    left = times.get(first)
    right = times.get(second)
    if left is not None and right is not None and left != right:
        if left < right:
            return first, second, ""
        return second, first, ""
    return (
        first,
        second,
        "这两场考试没有可区分的考试时间，按选择顺序把第一场当作上次、第二场当作这次。",
    )


def exam_date_text(exam_name: str, batches: list[dict[str, Any]] | None) -> str:
    """考试日期。没有时间时返回空串，封面就不写日期。"""
    parsed = _exam_times(batches or []).get(str(exam_name or "").strip())
    if parsed is None:
        return ""
    if parsed.hour == 0 and parsed.minute == 0 and parsed.second == 0:
        return parsed.strftime("%Y-%m-%d")
    return parsed.strftime("%Y-%m-%d %H:%M")


def select_line_shift_schools(
    schools: list[dict[str, Any]],
    query: str,
) -> list[dict[str, Any]]:
    """只保留点名的那一所，不按校码把同名校区并进来。

    「扬大附中」对上 ``A02扬大附中``，不对上「扬大附中（淮海路）」。
    点了校区或学校 id 时，只留那一条。
    """
    target = str(query or "").strip()
    if not target:
        return []
    rows = [sch for sch in (schools or []) if isinstance(sch, dict)]
    by_id = [sch for sch in rows if str(sch.get("id") or "").strip() == target]
    if by_id:
        return by_id
    wanted = {target, strip_school_code(target)}
    wanted.discard("")
    exact: list[dict[str, Any]] = []
    for sch in rows:
        raw = str(sch.get("s_name") or "").strip()
        labels = {raw, strip_school_code(raw)}
        labels.discard("")
        if wanted & labels:
            exact.append(sch)
    if not exact:
        return []
    asked = campus_of(target)
    if asked:
        return [sch for sch in exact if asked in str(sch.get("s_name") or "")]
    plain = [sch for sch in exact if not campus_of(str(sch.get("s_name") or ""))]
    return plain or exact


def overview_enrolled_sql(exam_name: str, xx_names: list[str]) -> str:
    """一所学校、一场考试的在籍生六门总分。不取四门、三门总分。"""
    names = [n for n in xx_names if str(n or "").strip()]
    if not names:
        raise ValueError("缺少学校名称，拒绝拉取全库成绩")
    lit = _esc(exam_name)
    inn = ", ".join(f"'{_esc(n)}'" for n in names)
    return (
        "SELECT exam_name, xm, anon_stu_id, xx, bj, xkkm, xsxz, zf6m "
        "FROM tb_score_overview "
        f"WHERE exam_name = '{lit}' "
        "AND xsxz = '在籍生' "
        f"AND xx IN ({inn}) "
        "LIMIT 200000"
    )


def xx_literals(schools: list[dict[str, Any]]) -> list[str]:
    """成绩表校名可能带校码，也可能不带。带校区的学校不把校区抹掉去匹配。"""
    out: list[str] = []
    for sch in schools or []:
        raw = str(sch.get("s_name") or "").strip()
        labels = [raw, strip_school_code(raw)]
        if raw and not campus_of(raw):
            labels.append(base_school_name(raw))
        for lab in labels:
            if lab and lab not in out:
                out.append(lab)
    return out


def row_in_schools(xx: str, schools: list[dict[str, Any]]) -> bool:
    """成绩行是否属于已选定的学校。点了校区时不把另一个校区算进来。"""
    raw = str(xx or "").strip()
    if not raw or not schools:
        return False
    candidates = {raw, strip_school_code(raw)}
    if not campus_of(raw):
        base = base_school_name(raw)
        if base:
            candidates.add(base)
    candidates.discard("")
    for sch in schools:
        s_name = str(sch.get("s_name") or "").strip()
        if not s_name:
            continue
        labels = {s_name, strip_school_code(s_name)}
        if not campus_of(s_name):
            labels.add(base_school_name(s_name))
        labels.discard("")
        if candidates & labels:
            return True
        campus = campus_of(s_name)
        if (
            campus
            and campus in raw
            and base_school_name(raw) == base_school_name(s_name)
        ):
            return True
    return False


def build_line_shift_report(
    *,
    prev_exam: str,
    curr_exam: str,
    prev_rows: list[dict[str, Any]],
    curr_rows: list[dict[str, Any]],
    bars: list[dict[str, Any]],
    schools: list[dict[str, Any]],
    class_name: str = "",
    order_note: str = "",
    prev_date: str = "",
    curr_date: str = "",
    anonymize: bool = True,
    report_time: str = "",
) -> dict[str, Any]:
    """组装模板数据。校类不明或校类不一致时抛 ``ValueError``，避免用错分数线。"""
    tier, main_lines, school_label = _tier_of(schools)
    prev_all, prev_dups = _index(prev_rows, schools)
    curr_all, curr_dups = _index(curr_rows, schools)
    curr_scope = _filter_class(curr_all, class_name)

    ready, notes = _pair(prev_all, curr_scope)
    notes["dup"] = prev_dups + curr_dups
    if class_name:
        # 选了班以后，其他班不在本次范围，不能把他们算成「这次没来考试」。
        notes["left"] = 0
        notes["hide_left"] = True
        if curr_all and not curr_scope:
            notes["class_miss"] = class_name

    show_sprint = tier == "引领校"
    lines = [
        _line_block(name, ready, bars, prev_exam, curr_exam, anonymize, with_names=True)
        for name in main_lines
    ]
    sprint = []
    if show_sprint:
        sprint = [
            _line_block(name, ready, bars, prev_exam, curr_exam, anonymize, with_names=False)
            for name in SPRINT_LINES
        ]

    scope = _esc_text(class_name) if class_name else "全校"
    title_scope = _esc_text(class_name) if class_name else ""
    return {
        "REPORT_TITLE": f"{school_label}{title_scope}临界生分析报告",
        "REPORT_TYPE": "临界生分析报告",
        "REPORT_TIME": _esc_text(report_time),
        "SCHOOL_LABEL": school_label,
        "CLASS_LABEL": scope,
        "TIER": tier,
        "LINE_NAMES": "、".join(main_lines),
        "PREV_EXAM": _esc_text(prev_exam),
        "CURR_EXAM": _esc_text(curr_exam),
        "PREV_DATE": _esc_text(prev_date),
        "CURR_DATE": _esc_text(curr_date),
        "ORDER_NOTE": _esc_text(order_note),
        "COHORT_NOTE": _cohort_note(school_label, scope, ready, notes),
        "LINES": lines,
        "SPRINT": sprint,
        "LINE_SCORES": _score_rows(lines + sprint),
        "_stats": {"count": len(ready)},
    }


@dataclass
class _Stu:
    sid: str
    name: str
    xx: str
    bj: str
    track: str
    total: Decimal | None


def _tier_of(schools: list[dict[str, Any]]) -> tuple[str, tuple[str, ...], str]:
    if not schools:
        raise ValueError("未找到学校，请从列表里选择库内学校名称")
    specs = [chapter_spec(str(sch.get("type") or "")) for sch in schools]
    tiers = {str(item.get("tier") or "") for item in specs}
    if len(tiers) != 1:
        raise ValueError("所选范围包含不同校类，请指定到同一校类的学校或校区")
    if any(item.get("unspecified") for item in specs):
        raise ValueError("该校未配置校类（引领校、支撑校或发展校），无法确定应对比的分数线")
    tier = next(iter(tiers))
    lines = tuple(specs[0].get("lines") or ())
    if not lines:
        raise ValueError("该校类没有配置分数线")
    labels: list[str] = []
    for sch in schools:
        raw = str(sch.get("s_name") or "").strip()
        label = strip_school_code(raw) or raw
        if label and label not in labels:
            labels.append(label)
    if not labels:
        raise ValueError("未找到学校，请从列表里选择库内学校名称")
    return tier, lines, _esc_text("、".join(labels))


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
    xsxz = str(pick_col(row, "xsxz", "学生性质") or "").strip()
    if xsxz != "在籍生":
        return None
    sid = str(pick_col(row, "anon_stu_id", "student_id") or "").strip()
    if not sid:
        return None
    total = _decimal(pick_col(row, "zf6m"))
    track = infer_track(row)
    if track not in _TRACKS:
        track = ""
    return _Stu(
        sid=sid,
        name=str(pick_col(row, "xm", "name") or "").strip(),
        xx=str(pick_col(row, "xx", "school_name") or "").strip(),
        bj=str(pick_col(row, "bj", "class_name") or "").strip(),
        track=track,
        total=total,
    )


def _filter_class(students: dict[str, _Stu], class_name: str) -> dict[str, _Stu]:
    want = _norm_class(class_name)
    if not want:
        return dict(students)
    return {sid: stu for sid, stu in students.items() if _norm_class(stu.bj) == want}


def _pair(
    prev_all: dict[str, _Stu],
    curr_scope: dict[str, _Stu],
) -> tuple[list[tuple[_Stu, _Stu]], dict[str, Any]]:
    ready: list[tuple[_Stu, _Stu]] = []
    only_curr = 0
    missing_total = 0
    bad_track = 0
    for sid, curr in curr_scope.items():
        prev = prev_all.get(sid)
        if prev is None:
            only_curr += 1
            continue
        if prev.total is None or curr.total is None:
            missing_total += 1
            continue
        if not prev.track or prev.track != curr.track:
            bad_track += 1
            continue
        ready.append((prev, curr))
    left = 0
    for sid, prev in prev_all.items():
        if sid in curr_scope or prev.total is None:
            continue
        # 全校口径下，这次不在本校的人。选了班时，curr_scope 只含该班，
        # 同校其他班也会计入这里，文案按「不在本次范围」表述。
        left += 1
    ready.sort(key=lambda pair: pair[0].sid)
    return ready, {
        "only_curr": only_curr,
        "left": left,
        "missing_total": missing_total,
        "bad_track": bad_track,
        "dup": 0,
        "class_miss": "",
        "hide_left": False,
    }


def _line_block(
    line_name: str,
    ready: list[tuple[_Stu, _Stu]],
    bars: list[dict[str, Any]],
    prev_exam: str,
    curr_exam: str,
    anonymize: bool,
    *,
    with_names: bool,
) -> dict[str, Any]:
    tracks = [
        _track_block(
            line_name,
            track,
            ready,
            bars,
            prev_exam,
            curr_exam,
            anonymize,
            with_names=with_names,
        )
        for track in _TRACKS
    ]
    return {"line_name": line_name, "tracks": tracks}


def _track_block(
    line_name: str,
    track: str,
    ready: list[tuple[_Stu, _Stu]],
    bars: list[dict[str, Any]],
    prev_exam: str,
    curr_exam: str,
    anonymize: bool,
    *,
    with_names: bool,
) -> dict[str, Any]:
    prev_thr, prev_err = _threshold(bars, prev_exam, line_name, track)
    curr_thr, curr_err = _threshold(bars, curr_exam, line_name, track)
    base = {
        "track": track,
        "available": False,
        "missing_reason": "",
        "headcount": 0,
        "prev_line": "—",
        "curr_line": "—",
        "prev_band": "—",
        "curr_band": "—",
        "keep_folded": False,
        "keep_text": "—",
        "drop_text": "—",
        "conv_text": "—",
        "drop_list": [],
        "new_list": [],
        "keep_list": [],
    }
    base["headcount"] = sum(
        1 for prev, curr in ready if prev.track == track and curr.track == track
    )
    if prev_thr is not None:
        base["prev_line"] = _fmt_score(prev_thr)
        base["prev_band"] = _band_text(prev_thr)
    if curr_thr is not None:
        base["curr_line"] = _fmt_score(curr_thr)
        base["curr_band"] = _band_text(curr_thr)
    if prev_thr is None or curr_thr is None:
        base["missing_reason"] = " ".join(x for x in (prev_err, curr_err) if x)
        return base

    cohort = [(p, c) for p, c in ready if p.track == track and c.track == track]
    keep_den_rows = [(p, c) for p, c in cohort if _reached(p.total, prev_thr)]
    conv_rows = [
        (p, c)
        for p, c in cohort
        if not _reached(p.total, prev_thr) and _in_below_band(p.total, prev_thr)
    ]
    keep_rows = [(p, c) for p, c in keep_den_rows if _reached(c.total, curr_thr)]
    drop_rows = [(p, c) for p, c in keep_den_rows if not _reached(c.total, curr_thr)]
    new_rows = [(p, c) for p, c in conv_rows if _reached(c.total, curr_thr)]
    if len(keep_rows) + len(drop_rows) != len(keep_den_rows):
        raise RuntimeError("巩固人数与掉线人数之和必须等于上次达线基数")
    if len(new_rows) > len(conv_rows):
        raise RuntimeError("新上线人数不能大于转化基数")

    drop_rows.sort(key=lambda pair: (_gap(pair[1].total, curr_thr), pair[0].sid))
    new_rows.sort(key=lambda pair: (_gap(pair[0].total, prev_thr), pair[0].sid), reverse=True)
    keep_rows.sort(key=lambda pair: (_gap(pair[1].total, curr_thr), pair[0].sid), reverse=True)

    base.update(
        {
            "available": True,
            "keep_text": _fmt_rate(len(keep_rows), len(keep_den_rows)),
            "drop_text": _fmt_rate(len(drop_rows), len(keep_den_rows)),
            "conv_text": _fmt_rate(len(new_rows), len(conv_rows)),
            "keep_folded": len(keep_rows) > 20,
        }
    )
    if with_names:
        base["drop_list"] = [
            _person(p, c, prev_thr, curr_thr, anonymize) for p, c in drop_rows
        ]
        base["new_list"] = [
            _person(p, c, prev_thr, curr_thr, anonymize) for p, c in new_rows
        ]
        base["keep_list"] = [
            _person(p, c, prev_thr, curr_thr, anonymize) for p, c in keep_rows
        ]
    return base


def _threshold(
    bars: list[dict[str, Any]],
    exam: str,
    line_name: str,
    track: str,
) -> tuple[Decimal | None, str]:
    found: list[Decimal] = []
    for bar in bars or []:
        if str(bar.get("exam_name") or "").strip() != exam:
            continue
        if canon_line_name(str(bar.get("line_name") or "")) != line_name:
            continue
        if str(bar.get("track") or "").strip() != track:
            continue
        thr = _decimal(bar.get("threshold"))
        if thr is None:
            continue
        if all(thr != old for old in found):
            found.append(thr)
    if len(found) == 1:
        return found[0], ""
    if not found:
        return None, f"「{exam}」没有配置{track}{line_name}，本方向不计算。"
    return None, f"「{exam}」的{track}{line_name}有多条不同分数，本方向不计算。"


def _person(
    prev: _Stu,
    curr: _Stu,
    prev_thr: Decimal,
    curr_thr: Decimal,
    anonymize: bool,
) -> dict[str, str]:
    shown = prev.sid if anonymize else (prev.name or prev.sid)
    assert prev.total is not None and curr.total is not None
    return {
        "name": _esc_text(shown),
        "track": curr.track,
        "prev_score": _fmt_score(prev.total),
        "curr_score": _fmt_score(curr.total),
        "prev_gap": _fmt_gap(prev.total, prev_thr),
        "gap": _fmt_gap(curr.total, curr_thr),
    }


def _score_rows(blocks: list[dict[str, Any]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for line in blocks:
        for track in line["tracks"]:
            rows.append(
                {
                    "line_name": str(line["line_name"]),
                    "track": str(track["track"]),
                    "prev_line": str(track["prev_line"]),
                    "curr_line": str(track["curr_line"]),
                    "prev_band": str(track["prev_band"]),
                    "curr_band": str(track["curr_band"]),
                }
            )
    return rows


def _cohort_note(
    school: str,
    scope: str,
    ready: list[tuple[_Stu, _Stu]],
    notes: dict[str, Any],
) -> str:
    phy = sum(1 for prev, _curr in ready if prev.track == "物理类")
    his = sum(1 for prev, _curr in ready if prev.track == "历史类")
    bits = [
        f"{school}{scope}：两次都有六门总分、且方向一致的在籍生 {len(ready)} 人"
        f"（物理类 {phy} 人，历史类 {his} 人）。"
    ]
    if notes.get("class_miss"):
        bits.append(f"这次考试没有班级「{_esc_text(str(notes['class_miss']))}」的在籍生。")
    excluded = [
        f"这次在范围内但上次没有成绩 {notes['only_curr']} 人",
        f"两场都在但缺少六门总分 {notes['missing_total']} 人",
        f"两次方向不一致或方向无法识别 {notes['bad_track']} 人",
        f"同一场学号重复因而整人排除 {notes['dup']} 人",
    ]
    if not notes.get("hide_left"):
        excluded.insert(1, f"上次有成绩但这次不在本校 {notes['left']} 人")
    bits.append("未进入分子和分母的人数：" + "，".join(excluded) + "。")
    return "".join(bits)


def _band_text(threshold: Decimal) -> str:
    """转化看的区间：含正好低 15 分，不含刚好达线。"""
    low = threshold - BELOW_MARGIN
    return f"{_fmt_score(low)}（含）到不到 {_fmt_score(threshold)}"


def _reached(score: Decimal | None, threshold: Decimal) -> bool:
    return score is not None and score >= threshold


def _in_below_band(score: Decimal | None, threshold: Decimal) -> bool:
    """线下 15 分以内，含正好低 15 分，不含正好达线。"""
    if score is None:
        return False
    return (threshold - BELOW_MARGIN) <= score < threshold


def _gap(score: Decimal | None, threshold: Decimal) -> Decimal:
    if score is None:
        return Decimal(0)
    return score - threshold


def _fmt_rate(num: int, den: int) -> str:
    if den <= 0:
        return "0/0（—）"
    pct = (Decimal(num) * Decimal(100) / Decimal(den)).quantize(
        Decimal("0.1"),
        rounding=ROUND_HALF_UP,
    )
    return f"{num}/{den}（{pct}%）"


def _fmt_score(value: Decimal) -> str:
    q = value.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    if q == q.to_integral():
        return str(int(q))
    return f"{q}"


def _fmt_gap(score: Decimal, threshold: Decimal) -> str:
    q = (score - threshold).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    if q == q.to_integral():
        n = int(q)
        return f"+{n}" if n > 0 else str(n)
    text = f"{q}"
    return text if text.startswith("-") else f"+{text}"


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


def _exam_times(batches: list[dict[str, Any]]) -> dict[str, datetime]:
    from src.agent.education.line_reach_report import _parse_exam_time

    out: dict[str, datetime] = {}
    for row in batches:
        if not isinstance(row, dict):
            continue
        name = str(row.get("batch_name") or row.get("exam_name") or "").strip()
        if not name:
            continue
        parsed = _parse_exam_time(row.get("exam_time"))
        if isinstance(parsed, datetime):
            out[name] = parsed
        elif isinstance(parsed, date):
            out[name] = datetime(parsed.year, parsed.month, parsed.day)
    return out
