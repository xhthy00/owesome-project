"""预测线达线：纯函数聚合（无 I/O）。

用 ``tb_fraction_bar`` 的分数线对 ``tb_score_overview`` 学生总分做达线判定，
再按区县（及可选学校）聚合人数/率。列名经别名探测，不写死单一物理列。
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from datasource.service.edu_permission import EduScope

__all__ = [
    "aggregate_district_line_reach",
    "attach_school_dimension",
    "build_line_reach_payload",
    "can_access_line_reach",
    "canon_line_name",
    "filter_fraction_bars",
    "filter_students_by_scope",
    "line_code_of",
    "normalize_fraction_bars",
    "normalize_overview_students",
    "payload_from_school_agg",
    "remap_agg_rows",
    "pick_col",
    "reached_lines",
    "rows_as_dicts",
    "select_known_columns",
    "student_total",
]

_LINE_NAME_KEYS = (
    "line_name",
    "line_type",
    "pc",
    "批次",
    "线种",
    "fsx",
    "bar_name",
    "name",
)
_THRESHOLD_KEYS = (
    "threshold",
    "line_score",
    "yxfs",
    "bar_score",
    "fs",
    "分数",
    "score",
)
_EXAM_KEYS = ("exam_name", "exam", "ksmc", "考试")
_TRACK_KEYS = ("track", "xkkm", "xkqk", "xkfx", "xk", "选科", "kslb", "lb")
_STUDENT_KEYS = ("anon_stu_id", "student_id", "sid")
_CLASS_KEYS = ("bj", "class", "class_name")
_SCHOOL_ID_KEYS = ("school_id", "xx", "xxid", "school")
_DISTRICT_KEYS = ("district", "dq", "qx", "区县")
# 达线只用六门/全科总分；禁止把 zf4m/zf3m 当总分（会远低于分数线导致全员不达线）
_TOTAL_KEYS = ("zf6m", "zf", "total", "total_score", "总分")
_SUBJECT_KEYS = (
    ("yw", "语文"),
    ("sx", "数学"),
    ("yy", "英语", "yingyu"),
    ("wl", "物理", "wuli"),
    ("hx", "化学", "huaxue"),
    ("sw", "生物", "shengwu"),
    ("zz", "政治", "zhengzhi"),
    ("ls", "历史", "lishi"),
    ("dl", "地理", "dili"),
)


def rows_as_dicts(columns: list[Any], rows: list[Any]) -> list[dict[str, Any]]:
    """把 execute_sql 的 columns+rows 转成 dict 行；同名列后者覆盖（JOIN 别名靠后）。"""
    cols = [str(c) for c in (columns or [])]
    out: list[dict[str, Any]] = []
    for row in rows or []:
        if isinstance(row, dict):
            out.append(dict(row))
            continue
        if not isinstance(row, (list, tuple)):
            continue
        item: dict[str, Any] = {}
        for i, col in enumerate(cols):
            if i < len(row):
                item[col] = row[i]
        out.append(item)
    return out


def pick_col(row: dict[str, Any], *keys: str) -> Any:
    """大小写不敏感取列；优先精确 key，再小写比对。"""
    if not row:
        return None
    for key in keys:
        if key in row:
            return row[key]
    lower = {str(k).lower(): v for k, v in row.items()}
    for key in keys:
        lk = key.lower()
        if lk in lower:
            return lower[lk]
    return None


def select_known_columns(available: list[str], *groups: tuple[str, ...]) -> list[str]:
    """从探到的列里挑达线需要的字段，避免 SELECT * 拉宽表。"""
    lower = {str(c).lower(): str(c) for c in (available or [])}
    out: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for key in group:
            actual = lower.get(key.lower())
            if actual and actual.lower() not in seen:
                seen.add(actual.lower())
                out.append(actual)
    return out


BAR_COL_GROUPS: tuple[tuple[str, ...], ...] = (
    _LINE_NAME_KEYS,
    _THRESHOLD_KEYS,
    _EXAM_KEYS,
    _TRACK_KEYS,
)
OVERVIEW_COL_GROUPS: tuple[tuple[str, ...], ...] = (
    _STUDENT_KEYS,
    _EXAM_KEYS,
    _TRACK_KEYS,
    _TOTAL_KEYS,
    _CLASS_KEYS,
    _SCHOOL_ID_KEYS,
    _DISTRICT_KEYS,
    *(tuple(keys) for keys in _SUBJECT_KEYS),
)


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def can_access_line_reach(scope: EduScope | None) -> bool:
    """学生不可见；其余角色可看（范围由 filter_students_by_scope 收敛）。"""
    role = (getattr(scope, "edu_role", None) or "").strip()
    return role != "student"


def filter_students_by_scope(
    students: list[dict[str, Any]],
    scope: EduScope | None,
) -> list[dict[str, Any]]:
    role = (getattr(scope, "edu_role", None) or "").strip()
    if role in ("", "bureau_admin"):
        return list(students)
    school_id = _str(getattr(scope, "school_id", "") or "")
    class_names = {
        str(c).strip()
        for c in (getattr(scope, "class_names", None) or [])
        if str(c).strip()
    }
    out: list[dict[str, Any]] = []
    for s in students:
        if school_id and _str(s.get("school_id")) != school_id:
            continue
        if role == "teacher" and class_names and _str(s.get("class_name")) not in class_names:
            continue
        out.append(s)
    return out


def student_total(row: dict[str, Any]) -> float | None:
    """优先总分列；否则语数英+选科求和。"""
    total = _num(pick_col(row, *_TOTAL_KEYS))
    if total is not None:
        return total
    parts: list[float] = []
    for keys in _SUBJECT_KEYS:
        n = _num(pick_col(row, *keys))
        if n is not None:
            parts.append(n)
    if not parts:
        return None
    return sum(parts)


def reached_lines(total: float, bars: list[dict[str, Any]]) -> list[str]:
    """返回该总分达到的线种名称（bars 为 normalize 后的结构）。"""
    names: list[str] = []
    for bar in bars or []:
        thr = _num(bar.get("threshold"))
        if thr is None:
            continue
        if total >= thr:
            name = _str(bar.get("line_name"))
            if name:
                names.append(name)
    return names


def infer_track(row: dict[str, Any]) -> str:
    explicit = _norm_track(_str(pick_col(row, *_TRACK_KEYS)))
    if explicit:
        return explicit
    wl = _num(pick_col(row, "wl", "物理", "wuli"))
    ls = _num(pick_col(row, "ls", "历史", "lishi"))
    wl_ok = wl is not None and wl > 0
    ls_ok = ls is not None and ls > 0
    if wl_ok and not ls_ok:
        return "物理类"
    if ls_ok and not wl_ok:
        return "历史类"
    return ""


def _norm_track(raw: str) -> str:
    t = _str(raw)
    if not t:
        return ""
    if "物理" in t or ("物" in t and "史" not in t and "历" not in t):
        return "物理类"
    if "历史" in t or "史" in t or "历" in t:
        return "历史类"
    return t


_WIDE_LINE_RE = re.compile(r"^(wl|ls)_(?:score|socre)_(.+)$", re.I)
_LINE_SUFFIX_LABEL = {
    "tz": "特控线",
    "bk": "本科线",
    "ty": "体育线",
    "ms": "美术线",
    "yy": "音乐线",
    "211": "211线",
    "985": "985线",
    "qb": "清北线",
    "nd": "南大线",
}


_LINE_NAME_TO_CODE = {label: code for code, label in _LINE_SUFFIX_LABEL.items()}


def canon_line_name(name: str) -> str:
    """特招线与特控线同义，统一成特控线。"""
    n = _str(name)
    if "特招" in n:
        return "特控线"
    return n


def line_code_of(bar: dict[str, Any]) -> str:
    code = _str(bar.get("line_code")).lower()
    if code:
        return code
    name = canon_line_name(_str(bar.get("line_name")))
    if name in _LINE_NAME_TO_CODE:
        return _LINE_NAME_TO_CODE[name]
    for label, c in _LINE_NAME_TO_CODE.items():
        if label and label in name:
            return c
    return ""


def _line_sort_key(bar: dict[str, Any]) -> tuple[int, float, str]:
    name = canon_line_name(str(bar.get("line_name") or ""))
    if "特控" in name or "强基" in name:
        pri = 0
    elif "本科" in name:
        pri = 1
    elif "清北" in name or "985" in name:
        pri = 2
    elif "211" in name or "南大" in name:
        pri = 3
    else:
        pri = 4
    thr = float(bar.get("threshold") or 0)
    track = str(bar.get("track") or "")
    return (pri, track, -thr, name)


def _unpivot_wide_fraction_bar(row: dict[str, Any]) -> list[dict[str, Any]]:
    """宽表分数线：wl_score_bk / ls_score_tz 等列拆成线种行。"""
    exam = _str(pick_col(row, *_EXAM_KEYS))
    out: list[dict[str, Any]] = []
    for col, val in row.items():
        m = _WIDE_LINE_RE.match(str(col).strip())
        if not m:
            continue
        thr = _num(val)
        if thr is None:
            continue
        track = "物理类" if m.group(1).lower() == "wl" else "历史类"
        suffix = m.group(2).lower()
        name = _LINE_SUFFIX_LABEL.get(suffix, suffix)
        out.append(
            {
                "line_name": name,
                "line_code": suffix,
                "threshold": thr,
                "exam_name": exam,
                "track": track,
            }
        )
    return out


def normalize_fraction_bars(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """分数线行 → {line_name, threshold, exam_name, track}。兼容长表与宽表。"""
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, float]] = set()

    def _add(item: dict[str, Any]) -> None:
        key = (
            str(item.get("exam_name") or ""),
            str(item.get("track") or ""),
            str(item.get("line_name") or ""),
            float(item.get("threshold") or 0),
        )
        if key in seen:
            return
        seen.add(key)
        out.append(item)

    for row in rows or []:
        if not isinstance(row, dict):
            continue
        name = _str(pick_col(row, *_LINE_NAME_KEYS))
        thr = _num(pick_col(row, *_THRESHOLD_KEYS))
        if name and thr is not None:
            _add(
                {
                    "line_name": canon_line_name(name),
                    "line_code": line_code_of({"line_name": name}),
                    "threshold": thr,
                    "exam_name": _str(pick_col(row, *_EXAM_KEYS)),
                    "track": _norm_track(_str(pick_col(row, *_TRACK_KEYS))),
                }
            )
            continue
        for item in _unpivot_wide_fraction_bar(row):
            _add(item)
    out.sort(key=_line_sort_key)
    return out


def attach_school_dimension(
    overview_rows: list[dict[str, Any]],
    score_scope_rows: list[dict[str, Any]] | None = None,
    school_rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """用权限内 tb_score 的 school_id/class 与 tb_school.district 补全 overview。"""
    by_stu: dict[str, dict[str, Any]] = {}
    for row in score_scope_rows or []:
        if not isinstance(row, dict):
            continue
        sid = _str(pick_col(row, *_STUDENT_KEYS))
        if sid:
            by_stu[sid] = row
    by_school: dict[str, dict[str, Any]] = {}
    for row in school_rows or []:
        if not isinstance(row, dict):
            continue
        kid = _str(pick_col(row, "id", "school_id"))
        if kid:
            by_school[kid] = row
    out: list[dict[str, Any]] = []
    for row in overview_rows or []:
        if not isinstance(row, dict):
            continue
        merged = dict(row)
        sid = _str(pick_col(row, *_STUDENT_KEYS))
        sc = by_stu.get(sid) or {}
        school_id = _str(pick_col(sc, "school_id")) or _str(pick_col(row, *_SCHOOL_ID_KEYS))
        cls = _str(pick_col(sc, "class", "bj")) or _str(pick_col(row, *_CLASS_KEYS))
        sch = by_school.get(school_id) or {}
        district = _str(pick_col(row, *_DISTRICT_KEYS)) or _str(pick_col(sch, "district", "qx"))
        merged["_school_id"] = school_id
        merged["_class"] = cls
        merged["_district"] = district
        merged["_school_name"] = _str(pick_col(sch, "name")) or school_id
        out.append(merged)
    return out


def normalize_overview_students(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """学生宽表行 → 达线用学生记录；按 (student_id, exam_name) 去重。"""
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        sid = _str(pick_col(row, *_STUDENT_KEYS))
        total = student_total(row)
        if not sid or total is None:
            continue
        exam = _str(pick_col(row, *_EXAM_KEYS))
        key = (sid, exam)
        if key in seen:
            continue
        seen.add(key)
        district = _str(row.get("_district")) or _str(pick_col(row, *_DISTRICT_KEYS)) or "未知区县"
        school_id = _str(row.get("_school_id")) or _str(pick_col(row, *_SCHOOL_ID_KEYS))
        out.append(
            {
                "student_id": sid,
                "district": district,
                "school_id": school_id,
                "school_name": _str(row.get("_school_name")) or school_id,
                "class_name": _str(row.get("_class")) or _str(pick_col(row, *_CLASS_KEYS)),
                "exam_name": exam,
                "track": infer_track(row),
                "total": total,
            }
        )
    return out


def _matches_exam(value: str, exam_name: str) -> bool:
    if not exam_name:
        return True
    v = _str(value)
    return (not v) or v == exam_name


def _matches_track(value: str, track: str) -> bool:
    if not track:
        return True
    v = _norm_track(value)
    return (not v) or v == track


def filter_fraction_bars(
    bars: list[dict[str, Any]],
    *,
    exam_name: str = "",
    track: str = "",
) -> list[dict[str, Any]]:
    exam_name = _str(exam_name)
    track = _norm_track(track)
    return [
        b
        for b in bars
        if _matches_exam(str(b.get("exam_name") or ""), exam_name)
        and _matches_track(str(b.get("track") or ""), track)
    ]


def _line_public_fields(bar: dict[str, Any], bars: list[dict[str, Any]]) -> dict[str, Any]:
    name = str(bar.get("line_name") or "")
    track = str(bar.get("track") or "")
    thr = float(bar.get("threshold") or 0)
    dup = sum(1 for b in bars if str(b.get("line_name") or "") == name) > 1
    label = f"{name}（{track}）" if dup and track else name
    out: dict[str, Any] = {
        "line_name": name,
        "label": label,
        "threshold": thr,
        "track": track,
        "line_key": f"{track}|{name}|{thr}",
    }
    note = str(bar.get("threshold_note") or "")
    if note:
        out["threshold_note"] = note
    return out


def _student_hits_bar(student: dict[str, Any], bar: dict[str, Any]) -> bool:
    bt = _norm_track(str(bar.get("track") or ""))
    st = _norm_track(str(student.get("track") or ""))
    if bt and st and bt != st:
        return False
    return float(student["total"]) >= float(bar["threshold"])


def _line_name_groups(bars: list[dict[str, Any]]) -> list[list[int]]:
    order: list[str] = []
    by_name: dict[str, list[int]] = {}
    for i, bar in enumerate(bars):
        name = str(bar.get("line_name") or "")
        if name not in by_name:
            order.append(name)
            by_name[name] = []
        by_name[name].append(i)
    return [by_name[name] for name in order]


def _merge_bar_group(items: list[dict[str, Any]]) -> dict[str, Any]:
    if len(items) == 1:
        return dict(items[0])
    thrs = [float(b.get("threshold") or 0) for b in items]
    notes: list[str] = []
    for b in items:
        t = str(b.get("track") or "").replace("类", "")
        thr = float(b.get("threshold") or 0)
        thr_s = str(int(thr)) if thr == int(thr) else str(thr)
        notes.append(f"{t}≥{thr_s}" if t else f"≥{thr_s}")
    return {
        "line_name": str(items[0].get("line_name") or ""),
        "threshold": min(thrs) if thrs else 0.0,
        "track": "",
        "threshold_note": " / ".join(notes),
    }


def _display_bars(
    bars: list[dict[str, Any]], *, collapse: bool
) -> tuple[list[dict[str, Any]], list[list[int]]]:
    if not collapse:
        groups = [[i] for i in range(len(bars))]
        return list(bars), groups
    groups = _line_name_groups(bars)
    return [_merge_bar_group([bars[i] for i in idxs]) for idxs in groups], groups


def _sum_groups(reached: list[int], groups: list[list[int]]) -> list[int]:
    return [sum(reached[i] for i in idxs if i < len(reached)) for idxs in groups]


def _stats_from_counts(
    bars: list[dict[str, Any]], candidates: int, reached: list[int]
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i, bar in enumerate(bars):
        hit = reached[i] if i < len(reached) else 0
        rate = round(100.0 * hit / candidates, 2) if candidates else 0.0
        out.append({**_line_public_fields(bar, bars), "reached": hit, "rate": rate})
    return out


def _line_stats_students(students: list[dict[str, Any]], bars: list[dict[str, Any]]) -> list[int]:
    return [sum(1 for s in students if _student_hits_bar(s, bar)) for bar in bars]


def aggregate_district_line_reach(
    students: list[dict[str, Any]],
    bars: list[dict[str, Any]],
    *,
    exam_name: str = "",
    track: str = "",
) -> dict[str, Any]:
    """按区县/学校聚合达线人数与率。"""
    exam_name = _str(exam_name)
    track = _norm_track(track)
    use_bars = [
        b
        for b in bars
        if _matches_exam(str(b.get("exam_name") or ""), exam_name)
        and _matches_track(str(b.get("track") or ""), track)
    ]
    use_students = [
        s
        for s in students
        if _matches_exam(str(s.get("exam_name") or ""), exam_name)
        and _matches_track(str(s.get("track") or ""), track)
    ]
    display_bars, groups = _display_bars(use_bars, collapse=not track)

    def _stats(group: list[dict[str, Any]]) -> list[dict[str, Any]]:
        raw = _line_stats_students(group, use_bars)
        return _stats_from_counts(display_bars, len(group), _sum_groups(raw, groups))

    lines_meta = [_line_public_fields(b, display_bars) for b in display_bars]
    kpis = {
        "candidates": len(use_students),
        "by_line": _stats(use_students),
    }

    by_district: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for s in use_students:
        by_district[str(s.get("district") or "未知区县")].append(s)

    districts: list[dict[str, Any]] = []
    for district in sorted(by_district):
        group = by_district[district]
        by_school: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for s in group:
            sk = _str(s.get("school_id")) or _str(s.get("school_name")) or "未知学校"
            by_school[sk].append(s)
        schools: list[dict[str, Any]] = []
        for sk in sorted(by_school):
            sg = by_school[sk]
            schools.append(
                {
                    "school_id": _str(sg[0].get("school_id")) or sk,
                    "school_name": _str(sg[0].get("school_name")) or sk,
                    "candidates": len(sg),
                    "by_line": _stats(sg),
                }
            )
        districts.append(
            {
                "district": district,
                "candidates": len(group),
                "by_line": _stats(group),
                "schools": schools,
            }
        )

    exams = sorted({_str(s.get("exam_name")) for s in students if _str(s.get("exam_name"))})
    if not exams:
        exams = sorted({_str(b.get("exam_name")) for b in bars if _str(b.get("exam_name"))})
    tracks = sorted(
        {
            _norm_track(_str(s.get("track")))
            for s in students
            if _norm_track(_str(s.get("track")))
        }
        | {
            _norm_track(_str(b.get("track")))
            for b in bars
            if _norm_track(_str(b.get("track")))
        }
    )
    return {
        "exam_name": exam_name,
        "track": track,
        "exams": exams,
        "tracks": tracks,
        "lines": lines_meta,
        "kpis": kpis,
        "districts": districts,
    }


def _bar_identity(bar: dict[str, Any]) -> tuple[str, str, float]:
    return (
        str(bar.get("track") or ""),
        str(bar.get("line_name") or ""),
        float(bar.get("threshold") or 0),
    )


def remap_agg_rows(
    rows: list[dict[str, Any]],
    src_bars: list[dict[str, Any]],
    dest_bars: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """把 r0.. 列从 src_bars 对齐到 dest_bars。"""
    if dest_bars == src_bars:
        return rows
    src_index = {_bar_identity(b): i for i, b in enumerate(src_bars)}
    keep = [src_index.get(_bar_identity(b), -1) for b in dest_bars]
    out: list[dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        nr = {
            "district": row.get("district"),
            "school_name": row.get("school_name") or row.get("school_id"),
            "track": row.get("track"),
            "candidates": row.get("candidates"),
        }
        for j, i in enumerate(keep):
            nr[f"r{j}"] = row.get(f"r{i}") if i >= 0 else 0
        out.append(nr)
    return out


def payload_from_school_agg(
    rows: list[dict[str, Any]],
    bars: list[dict[str, Any]],
    *,
    exam_name: str = "",
    track: str = "",
    exams: list[str] | None = None,
    tracks: list[str] | None = None,
) -> dict[str, Any]:
    """把 SQL 聚合行（district/school/track/candidates/r0..）组装成看板 payload。"""
    exam_name = _str(exam_name)
    track = _norm_track(track)
    n = len(bars)
    display_bars, groups = _display_bars(bars, collapse=not track)
    lines_meta = [_line_public_fields(b, display_bars) for b in display_bars]

    def _stats(candidates: int, reached: list[int]) -> list[dict[str, Any]]:
        return _stats_from_counts(display_bars, candidates, _sum_groups(reached, groups))

    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        st = _norm_track(str(row.get("track") or ""))
        if track and st and st != track:
            continue
        district = _str(row.get("district")) or "未知区县"
        school_name = _str(row.get("school_name")) or _str(row.get("school_id")) or "未知学校"
        candidates = int(_num(row.get("candidates")) or 0)
        reached: list[int] = []
        for i, bar in enumerate(bars):
            hit = int(_num(row.get(f"r{i}")) or 0)
            bt = _norm_track(str(bar.get("track") or ""))
            if bt and st and bt != st:
                hit = 0
            reached.append(hit)
        key = (district, school_name)
        if key in merged:
            merged[key]["candidates"] += candidates
            for i in range(n):
                merged[key]["reached"][i] += reached[i]
        else:
            merged[key] = {
                "district": district,
                "school_id": school_name,
                "school_name": school_name,
                "candidates": candidates,
                "reached": reached,
            }

    by_district: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for rec in merged.values():
        by_district[str(rec["district"])].append(rec)

    districts: list[dict[str, Any]] = []
    total_c = 0
    total_r = [0] * n
    for district in sorted(by_district):
        schools_raw = sorted(by_district[district], key=lambda s: str(s["school_name"]))
        dc = sum(int(s["candidates"]) for s in schools_raw)
        dr = [sum(int(s["reached"][i]) for s in schools_raw) for i in range(n)]
        total_c += dc
        for i in range(n):
            total_r[i] += dr[i]
        districts.append(
            {
                "district": district,
                "candidates": dc,
                "by_line": _stats(dc, dr),
                "schools": [
                    {
                        "school_id": s["school_id"],
                        "school_name": s["school_name"],
                        "candidates": s["candidates"],
                        "by_line": _stats(int(s["candidates"]), list(s["reached"])),
                    }
                    for s in schools_raw
                ],
            }
        )
    return {
        "accessible": True,
        "exam_name": exam_name,
        "track": track,
        "exams": list(exams or []),
        "tracks": list(tracks or []),
        "lines": lines_meta,
        "kpis": {"candidates": total_c, "by_line": _stats(total_c, total_r)},
        "districts": districts,
    }


def build_line_reach_payload(
    bar_rows: list[dict[str, Any]],
    overview_rows: list[dict[str, Any]],
    score_scope_rows: list[dict[str, Any]] | None = None,
    school_rows: list[dict[str, Any]] | None = None,
    *,
    exam_name: str = "",
    track: str = "",
    scope: EduScope | None = None,
) -> dict[str, Any]:
    """从原始查询行组装看板 payload。"""
    if not can_access_line_reach(scope):
        return {
            "accessible": False,
            "exam_name": exam_name,
            "track": track,
            "exams": [],
            "tracks": [],
            "lines": [],
            "kpis": {"candidates": 0, "by_line": []},
            "districts": [],
            "message": "学生账号不可查看达线看板",
        }
    merged = attach_school_dimension(overview_rows, score_scope_rows, school_rows)
    students = filter_students_by_scope(normalize_overview_students(merged), scope)
    bars = normalize_fraction_bars(bar_rows)
    payload = aggregate_district_line_reach(
        students, bars, exam_name=exam_name, track=track
    )
    payload["accessible"] = True
    return payload
