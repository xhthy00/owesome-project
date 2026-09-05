"""局端基础分析：均分 / ABCDE / 位次桶 / 贡献分 / 组合达线 / 脱敏高分名单 / 分段统计。

全部从 tb_score_overview 学生行重算。应届=xsxz 在籍生（排除市报生）。
特招线与特控线同义。脱敏开启时禁止输出 xm/ksh/sfzh；关闭后可展示姓名与学号。
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from src.agent.education.line_reach import canon_line_name, pick_col
from src.agent.education.report_types import ReportType, report_type_label

__all__ = [
    "ASSIGN_SUBJECTS",
    "BUREAU_KINDS",
    "COMBO_KEYS",
    "HISTORY_BUCKETS",
    "PHYSICS_BUCKETS",
    "aggregate_assign_grade",
    "aggregate_combo_reach",
    "aggregate_contribution",
    "aggregate_rank_buckets",
    "aggregate_subject_avg",
    "build_bureau_report_data",
    "build_elite_roster",
    "compare_school_city_avg",
    "elite_class_select_sql",
    "filter_students",
    "format_school_city_avg_content",
    "fraction_bar_select_sql",
    "normalize_students",
    "overview_exam_names_sql",
    "overview_select_sql",
    "parse_track",
    "school_city_avg_from_union_rows",
    "school_city_avg_sql",
    "school_codes_from_lookup",
    "school_lookup_sql",
    "wants_elite_class",
    "wants_enrolled_only",
]

BUREAU_KINDS: dict[str, ReportType] = {
    "subject_avg": ReportType.SUBJECT_AVG,
    "assign_grade": ReportType.ASSIGN_GRADE,
    "rank_bucket": ReportType.RANK_BUCKET,
    "contribution": ReportType.CONTRIBUTION,
    "combo_reach": ReportType.COMBO_REACH,
    "elite_roster": ReportType.ELITE_ROSTER,
    "score_band": ReportType.SCORE_BAND,
}

PHYSICS_BUCKETS = (10, 20, 50, 100, 200, 500, 1000, 2000)
HISTORY_BUCKETS = (10, 20, 50, 100, 200, 500)
COMBO_KEYS = ("物化生", "物化政", "物化地", "物生政", "物生地", "物政地")
ASSIGN_SUBJECTS = (
    ("hx", "hxdj", "化学"),
    ("sw", "swdj", "生物"),
    ("zz", "zzdj", "政治"),
    ("dl", "dldj", "地理"),
)
_GRADES = ("A", "B", "C", "D", "E")
_AVG_METRICS: tuple[tuple[str, str], ...] = (
    ("zf3m", "三门总均分"),
    ("zf4m", "四门总均分"),
    ("zf6m", "六门总均分"),
    ("yw", "语文均分"),
    ("ywzw", "语作文均分"),
    ("sx", "数学均分"),
    ("sxkg", "数客观均分"),
    ("yy", "英语均分"),
    ("yyzw", "英作文均分"),
    ("wl", "物理均分"),
    ("ls", "历史均分"),
    ("hx", "化学均分"),
    ("hxzh", "化转换均分"),
    ("sw", "生物均分"),
    ("swzh", "生转换均分"),
    ("zz", "政治均分"),
    ("zzzh", "政转换均分"),
    ("dl", "地理均分"),
    ("dlzh", "地转换均分"),
)
_CONTRIB_COLS: tuple[tuple[str, str], ...] = (
    ("zf6m", "ZF6"),
    ("yw", "语文"),
    ("sx", "数学"),
    ("yy", "英语"),
    ("wl", "物理"),
    ("ls", "历史"),
    ("hxzh", "化转换"),
    ("swzh", "生转换"),
    ("zzzh", "政转换"),
    ("dlzh", "地转换"),
)
_ROSTER_SUBJECTS: tuple[tuple[str, str], ...] = (
    ("yw", "语文"),
    ("sx", "数学"),
    ("yy", "英语"),
    ("wl", "物理"),
    ("ls", "历史"),
    ("hx", "化学"),
    ("sw", "生物"),
    ("zz", "政治"),
    ("dl", "地理"),
)
_FOCUS_LINES = ("特控线", "本科线", "211线")


def _str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    if n == 0:
        return None
    return n


def _mean(vals: list[float]) -> float | None:
    if not vals:
        return None
    return round(sum(vals) / len(vals), 2)


def _track_of(xkkm: str) -> str:
    t = _str(xkkm)
    if t.startswith("物"):
        return "物理类"
    if t.startswith("史") or t.startswith("历"):
        return "历史类"
    return ""


def parse_track(question: str) -> str:
    q = _str(question)
    if any(h in q for h in ("物理类", "物理方向", "理科")):
        return "物理类"
    if any(h in q for h in ("历史类", "历史方向", "文科")):
        return "历史类"
    return ""


def wants_enrolled_only(question: str) -> bool:
    return "应届" in _str(question)


def wants_elite_class(question: str) -> bool:
    q = _str(question)
    return "尖子生班" in q or "尖子班" in q


def normalize_students(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        xkkm = _str(pick_col(row, "xkkm", "xkqk", "xkfx"))
        item = {
            "anon_stu_id": _str(pick_col(row, "anon_stu_id", "student_id")),
            "xx": _str(pick_col(row, "xx", "school_id", "school_name")),
            "dq": _str(pick_col(row, "dq", "district")) or "未知区县",
            "bj": _str(pick_col(row, "bj", "class", "class_name")),
            "xkkm": xkkm,
            "track": _track_of(xkkm),
            "xsxz": _str(pick_col(row, "xsxz")),
            "xxlb": _str(pick_col(row, "xxlb")),
            "zf3m": _num(pick_col(row, "zf3m")),
            "zf4m": _num(pick_col(row, "zf4m")),
            "zf6m": _num(pick_col(row, "zf6m")),
        }
        from src.agent.education.privacy_mode import is_anonymize_display_enabled

        if not is_anonymize_display_enabled():
            item["xm"] = _str(pick_col(row, "xm", "姓名", "student_name"))
            item["xh"] = _str(pick_col(row, "xh", "学号", "student_id"))
        for key in (
            "yw", "ywzw", "sx", "sxkg", "yy", "yyzw",
            "wl", "hx", "sw", "zz", "ls", "dl",
            "hxzh", "swzh", "zzzh", "dlzh",
        ):
            item[key] = _num(pick_col(row, key))
        for key in ("hxdj", "swdj", "zzdj", "dldj"):
            item[key] = _str(pick_col(row, key)).upper()
        out.append(item)
    return out


def filter_students(
    rows: list[dict[str, Any]],
    *,
    track: str = "",
    enrolled_only: bool = False,
    elite_keys: set[tuple[str, str]] | None = None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        if track and row.get("track") != track:
            continue
        if enrolled_only and _str(row.get("xsxz")) != "在籍生":
            continue
        if elite_keys is not None:
            key = (_str(row.get("xx")), _str(row.get("bj")))
            if key not in elite_keys:
                continue
        out.append(row)
    return out


def school_lookup_sql(school_name: str) -> str:
    """按中文校名从 tb_school 取 id/name，供对齐 overview.xx。"""
    lit = _str(school_name).replace("'", "''")
    from src.agent.education.privacy_mode import is_anonymize_display_enabled

    if is_anonymize_display_enabled():
        return (
            "SELECT id, name FROM tb_school "
            f"WHERE name LIKE '%{lit}%' LIMIT 50"
        )
    return (
        "SELECT id, name, s_name FROM tb_school "
        f"WHERE s_name LIKE '%{lit}%' OR name LIKE '%{lit}%' LIMIT 50"
    )


def school_codes_from_lookup(rows: list[dict[str, Any]]) -> list[str]:
    codes: list[str] = []
    seen: set[str] = set()
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        for key in ("id", "name"):
            val = _str(row.get(key))
            if val and val not in seen:
                seen.add(val)
                codes.append(val)
    return codes


def compare_school_city_avg(
    students: list[dict[str, Any]],
    *,
    school_name: str,
    school_codes: list[str] | None = None,
    track: str = "",
    exam_name: str = "",
) -> dict[str, Any]:
    """点名学校 vs 全市：六门 zf6m 均分。不出 HTML。"""
    raw = normalize_students(students)
    rows = filter_students(raw, track=track) if track else raw
    codes = {_str(c) for c in (school_codes or []) if _str(c)}
    needle = _str(school_name)
    school_rows = [
        r
        for r in rows
        if _str(r.get("xx")) in codes or (needle and needle in _str(r.get("xx")))
    ]
    city_vals = [float(r["zf6m"]) for r in rows if r.get("zf6m") is not None]
    sch_vals = [float(r["zf6m"]) for r in school_rows if r.get("zf6m") is not None]
    city_avg = _mean(city_vals)
    school_avg = _mean(sch_vals)
    delta = None
    if school_avg is not None and city_avg is not None:
        delta = round(school_avg - city_avg, 2)
    if not rows:
        unmatched_reason = "track_empty" if raw else "no_students"
    elif not sch_vals:
        unmatched_reason = "school_not_aligned"
    else:
        unmatched_reason = ""
    table = []
    if sch_vals:
        table.append(
            {"scope": needle or "该校", "avg_zf6m": school_avg, "n": len(sch_vals)}
        )
    if city_vals:
        table.append({"scope": "全市", "avg_zf6m": city_avg, "n": len(city_vals)})
    return {
        "exam_name": _str(exam_name),
        "track": track or "全员",
        "school_name": needle,
        "school_matched": bool(sch_vals),
        "unmatched_reason": unmatched_reason,
        "school_avg": school_avg,
        "city_avg": city_avg,
        "delta": delta,
        "school_n": len(sch_vals),
        "city_n": len(city_vals),
        "table": table,
    }


def school_city_avg_sql(exam_name: str, school_name: str, track: str = "") -> str:
    """该校 vs 全市均分：聚合 UNION，不拉学生行。"""
    exam_lit = _str(exam_name).replace("'", "''")
    sch_lit = _str(school_name).replace("'", "''")
    track_pred = ""
    if track == "物理类":
        track_pred = " AND xkkm LIKE '物%'"
    elif track == "历史类":
        track_pred = " AND (xkkm LIKE '史%' OR xkkm LIKE '历%')"
    return (
        f"SELECT '{sch_lit}' AS scope, ROUND(AVG(zf6m), 2) AS avg_zf6m, COUNT(*) AS n "
        f"FROM tb_score_overview WHERE exam_name = '{exam_lit}'{track_pred} "
        f"AND xx LIKE '%{sch_lit}%' "
        "UNION ALL "
        f"SELECT '全市' AS scope, ROUND(AVG(zf6m), 2) AS avg_zf6m, COUNT(*) AS n "
        f"FROM tb_score_overview WHERE exam_name = '{exam_lit}'{track_pred}"
    )


def school_city_avg_from_union_rows(
    rows: list[dict[str, Any]],
    *,
    school_name: str,
    exam_name: str = "",
    track: str = "",
) -> dict[str, Any]:
    needle = _str(school_name)
    school_avg = None
    city_avg = None
    school_n = 0
    city_n = 0
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        scope = _str(r.get("scope"))
        n = int(r.get("n") or 0)
        avg = r.get("avg_zf6m")
        avg_n = float(avg) if avg is not None and avg != "" else None
        if scope == "全市":
            city_avg, city_n = avg_n, n
        else:
            school_avg, school_n = avg_n, n
    delta = None
    if school_avg is not None and city_avg is not None:
        delta = round(school_avg - city_avg, 2)
    if city_n == 0 and school_n == 0:
        unmatched_reason = "no_students"
    elif school_n == 0:
        unmatched_reason = "school_not_aligned"
    else:
        unmatched_reason = ""
    table = []
    if school_n:
        table.append({"scope": needle or "该校", "avg_zf6m": school_avg, "n": school_n})
    if city_n:
        table.append({"scope": "全市", "avg_zf6m": city_avg, "n": city_n})
    return {
        "exam_name": _str(exam_name),
        "track": track or "全员",
        "school_name": needle,
        "school_matched": school_n > 0,
        "unmatched_reason": unmatched_reason,
        "school_avg": school_avg,
        "city_avg": city_avg,
        "delta": delta,
        "school_n": school_n,
        "city_n": city_n,
        "table": table,
    }


def format_school_city_avg_content(
    cmp: dict[str, Any],
    *,
    overview_exam_names: list[str] | None = None,
) -> str:
    """给 Summarizer / 前端的结论。全市有数时禁止说考试名为空。"""
    exam = _str(cmp.get("exam_name")) or "该场考试"
    track = _str(cmp.get("track")) or "全员"
    school = _str(cmp.get("school_name")) or "该校"
    reason = _str(cmp.get("unmatched_reason"))
    city_avg = cmp.get("city_avg")
    city_n = int(cmp.get("city_n") or 0)
    school_avg = cmp.get("school_avg")
    school_n = int(cmp.get("school_n") or 0)
    delta = cmp.get("delta")
    if reason == "no_students":
        names = "、".join(
            _str(x) for x in (overview_exam_names or []) if _str(x)
        ) or "（未读到非空 exam_name）"
        return (
            f"按批次选中「{exam}」后，tb_score_overview 没有该场学生行。"
            f"库中已有考试名称：{names}。原因是批次未命中，不是校名问题。"
        )
    if reason == "track_empty":
        return f"「{exam}」有学生行，但选科方向「{track}」人数为 0。"
    if reason == "school_not_aligned" or not cmp.get("school_matched"):
        return (
            f"「{exam}」{track}全市六门均分 {city_avg}（{city_n}人）。"
            f"未能把校名「{school}」对齐到 overview.xx"
            f"（该列是校码，须经 tb_school.s_name 对齐）。"
        )
    sign = "高" if (delta or 0) >= 0 else "低"
    abs_delta = abs(delta) if delta is not None else 0
    return (
        f"「{exam}」{track}：{school}六门均分 {school_avg}（{school_n}人），"
        f"全市 {city_avg}（{city_n}人），该校{sign} {abs_delta} 分。"
    )


def overview_exam_names_sql() -> str:
    return (
        "SELECT DISTINCT exam_name FROM tb_score_overview "
        "WHERE exam_name IS NOT NULL AND exam_name <> '' "
        "ORDER BY exam_name LIMIT 40"
    )


def overview_select_sql(exam_name: str) -> str:
    lit = (exam_name or "").replace("'", "''")
    from src.agent.education.privacy_mode import is_anonymize_display_enabled

    extra = "" if is_anonymize_display_enabled() else "xm, xh, "
    return (
        f"SELECT exam_name, {extra}anon_stu_id, xx, dq, bj, xkkm, xsxz, xxlb, "
        "zf3m, zf4m, zf6m, yw, ywzw, sx, sxkg, yy, yyzw, "
        "wl, hx, sw, zz, ls, dl, hxzh, hxdj, swzh, swdj, zzzh, zzdj, dlzh, dldj "
        f"FROM tb_score_overview WHERE exam_name = '{lit}' LIMIT 50000"
    )


def elite_class_select_sql(exam_name: str) -> str:
    lit = (exam_name or "").replace("'", "''")
    return (
        "SELECT school_id, class_name, track FROM tb_elite_class "
        f"WHERE exam_name = '{lit}' LIMIT 500"
    )


def fraction_bar_select_sql(exam_name: str) -> str:
    lit = (exam_name or "").replace("'", "''")
    return (
        "SELECT * FROM tb_fraction_bar "
        f"WHERE exam_name = '{lit}' LIMIT 20"
    )


def _group_key(row: dict[str, Any], grain: str) -> str:
    if grain == "school":
        return _str(row.get("xx")) or "未知学校"
    if grain == "class":
        return f"{_str(row.get('xx'))}{_str(row.get('bj'))}".strip() or "未知班级"
    return _str(row.get("dq")) or "未知区县"


def aggregate_subject_avg(
    rows: list[dict[str, Any]],
    grain: str = "district",
    *,
    with_rank: bool = False,
) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        buckets[_group_key(row, grain)].append(row)
    out: list[dict[str, Any]] = []
    for name, group in buckets.items():
        item: dict[str, Any] = {"name": name, "count": len(group)}
        for key, _label in _AVG_METRICS:
            vals = [v for r in group if (v := _num(r.get(key))) is not None]
            item[key] = _mean(vals)
            item[f"{key}_n"] = len(vals)
        out.append(item)
    city = {
        "name": "扬州市" if grain != "class" else "合计",
        "count": len(rows),
    }
    for key, _label in _AVG_METRICS:
        vals = [v for r in rows if (v := _num(r.get(key))) is not None]
        city[key] = _mean(vals)
        city[f"{key}_n"] = len(vals)
    ranked = [r for r in out if r["name"] not in ("扬州市", "合计")]
    ranked.sort(key=lambda r: (-(r.get("zf6m") or 0), r["name"]))
    if with_rank:
        for i, row in enumerate(ranked, 1):
            row["rank"] = i
    return [city] + ranked


def aggregate_assign_grade(
    rows: list[dict[str, Any]],
    grain: str = "district",
) -> list[dict[str, Any]]:
    names = sorted({_group_key(r, grain) for r in rows})
    city_name = "扬州市"
    ordered = [city_name] + [n for n in names if n != city_name]

    def _stats(group: list[dict[str, Any]], raw_key: str, dj_key: str) -> dict[str, Any]:
        valid = [r for r in group if r.get(raw_key) is not None or r.get(dj_key)]
        counts = {g: 0 for g in _GRADES}
        for r in valid:
            g = _str(r.get(dj_key))
            if g in counts:
                counts[g] += 1
        n = len(valid)
        item: dict[str, Any] = {"n": n}
        for g in _GRADES:
            item[g] = counts[g]
            item[f"{g}_rate"] = round(counts[g] * 100.0 / n, 2) if n else 0.0
        return item

    out: list[dict[str, Any]] = []
    for name in ordered:
        group = rows if name == city_name else [r for r in rows if _group_key(r, grain) == name]
        if name != city_name and not group:
            continue
        row: dict[str, Any] = {"name": name}
        for raw_key, dj_key, label in ASSIGN_SUBJECTS:
            row[label] = _stats(group, raw_key, dj_key)
        out.append(row)
    return out


def aggregate_rank_buckets(
    rows: list[dict[str, Any]],
    *,
    track: str,
) -> dict[str, Any]:
    ns = PHYSICS_BUCKETS if track == "物理类" else HISTORY_BUCKETS
    ranked = sorted(
        [r for r in rows if r.get("zf6m") is not None],
        key=lambda r: (-float(r["zf6m"]), _str(r.get("anon_stu_id"))),
    )
    cuts: dict[int, float | None] = {}
    members: dict[int, list[dict[str, Any]]] = {}
    for n in ns:
        if not ranked:
            cuts[n] = None
            members[n] = []
            continue
        idx = min(n, len(ranked)) - 1
        cut = float(ranked[idx]["zf6m"])
        cuts[n] = cut
        members[n] = [r for r in ranked if float(r["zf6m"]) >= cut]

    def _unit_rows(grain: str) -> list[dict[str, Any]]:
        names = sorted({_group_key(r, grain) for r in ranked})
        out: list[dict[str, Any]] = []
        for name in names:
            item: dict[str, Any] = {"name": name}
            top20 = [
                i
                for i, r in enumerate(ranked[:20], 1)
                if _group_key(r, grain) == name
            ]
            item["top20_ranks"] = ",".join(str(i) for i in top20)
            for n in ns:
                item[n] = sum(1 for r in members[n] if _group_key(r, grain) == name)
            out.append(item)
        city = {"name": "扬州市", "top20_ranks": ""}
        for n in ns:
            city[n] = len(members[n])
        return [city] + out

    return {
        "track": track,
        "buckets": list(ns),
        "cuts": cuts,
        "districts": _unit_rows("district"),
        "schools": _unit_rows("school"),
    }


def aggregate_contribution(
    rows: list[dict[str, Any]],
    bars: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """切线贡献分：该选科 zf6m==threshold 的学生各科均值；无人则取达线末位一人。"""
    out: list[dict[str, Any]] = []
    by_track: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("track"):
            by_track[str(row["track"])].append(row)
    for bar in bars or []:
        if not isinstance(bar, dict):
            continue
        track = _str(bar.get("track"))
        line = canon_line_name(bar.get("line_name"))
        if line not in _FOCUS_LINES:
            continue
        try:
            thr = float(bar.get("threshold"))
        except (TypeError, ValueError):
            continue
        group = [
            r for r in by_track.get(track, [])
            if r.get("zf6m") is not None and float(r["zf6m"]) >= thr
        ]
        if not group:
            continue
        sitters = [r for r in group if abs(float(r["zf6m"]) - thr) < 1e-6]
        if not sitters:
            sitters = [min(group, key=lambda r: float(r["zf6m"]))]
        item: dict[str, Any] = {
            "track": track,
            "line_name": line,
            "threshold": thr,
            "reached": len(group),
        }
        for key, _label in _CONTRIB_COLS:
            vals = [float(r[key]) for r in sitters if r.get(key) is not None]
            item[key] = _mean(vals)
        out.append(item)
    order = {n: i for i, n in enumerate(_FOCUS_LINES)}
    out.sort(key=lambda r: (0 if r["track"] == "物理类" else 1, order.get(r["line_name"], 9)))
    return out


def aggregate_combo_reach(
    rows: list[dict[str, Any]],
    bars: list[dict[str, Any]],
    grain: str = "school",
) -> list[dict[str, Any]]:
    tz = bk = None
    for bar in bars or []:
        if not isinstance(bar, dict):
            continue
        if _str(bar.get("track")) != "物理类":
            continue
        name = canon_line_name(bar.get("line_name"))
        try:
            thr = float(bar.get("threshold"))
        except (TypeError, ValueError):
            continue
        if name == "特控线":
            tz = thr
        elif name == "本科线":
            bk = thr
    physics = [r for r in rows if r.get("track") == "物理类"]
    names = ["扬州市"] + sorted(
        {k for k in (_group_key(r, grain) for r in physics) if k != "扬州市"}
    )

    def _combo_stats(group: list[dict[str, Any]], combo: str) -> dict[str, Any]:
        g = [r for r in group if _str(r.get("xkkm")) == combo]
        n = len(g)
        tz_n = sum(
            1 for r in g if tz is not None and r.get("zf6m") is not None and float(r["zf6m"]) >= tz
        )
        bk_n = sum(
            1 for r in g if bk is not None and r.get("zf6m") is not None and float(r["zf6m"]) >= bk
        )
        return {
            "n": n,
            "tz": tz_n,
            "tz_rate": round(tz_n * 100.0 / n, 2) if n else 0.0,
            "bk": bk_n,
            "bk_rate": round(bk_n * 100.0 / n, 2) if n else 0.0,
        }

    out: list[dict[str, Any]] = []
    for name in names:
        group = physics if name == "扬州市" else [
            r for r in physics if _group_key(r, grain) == name
        ]
        if name != "扬州市" and not group:
            continue
        item: dict[str, Any] = {"name": name}
        for combo in COMBO_KEYS:
            item[combo] = _combo_stats(group, combo)
        out.append(item)
    return out


def build_elite_roster(
    rows: list[dict[str, Any]],
    *,
    track: str,
    top_n: int,
) -> list[dict[str, Any]]:
    pool = [
        r for r in rows
        if r.get("track") == track and r.get("zf6m") is not None
    ]
    pool.sort(key=lambda r: (-float(r["zf6m"]), _str(r.get("anon_stu_id"))))
    subject_ranks: dict[str, dict[str, int]] = {}
    for key, _label in _ROSTER_SUBJECTS:
        ordered = sorted(
            [r for r in pool if r.get(key) is not None],
            key=lambda r: (-float(r[key]), _str(r.get("anon_stu_id"))),
        )
        subject_ranks[key] = {
            _str(r.get("anon_stu_id")): i for i, r in enumerate(ordered, 1)
        }
    out: list[dict[str, Any]] = []
    for i, row in enumerate(pool[:top_n], 1):
        sid = _str(row.get("anon_stu_id"))
        item: dict[str, Any] = {
            "rank": i,
            "anon_stu_id": sid,
            "xx": _str(row.get("xx")),
            "zf6m": row.get("zf6m"),
        }
        from src.agent.education.privacy_mode import is_anonymize_display_enabled

        if not is_anonymize_display_enabled():
            item["xm"] = _str(row.get("xm"))
            item["xh"] = _str(row.get("xh")) or sid
        for key, _label in _ROSTER_SUBJECTS:
            item[key] = row.get(key)
            item[f"{key}_rank"] = subject_ranks[key].get(sid)
        out.append(item)
    return out


def _fmt(value: Any) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def _table(headers: list[str], rows: list[list[str]], *, numeric_from: int = 1) -> str:
    head = "<tr>" + "".join(
        f"<th class='{'num' if i >= numeric_from else ''}'>{h}</th>"
        for i, h in enumerate(headers)
    ) + "</tr>"
    body = "".join(
        "<tr>"
        + "".join(
            f"<td class='{'num' if i >= numeric_from else ''}'>{c}</td>"
            for i, c in enumerate(r)
        )
        + "</tr>"
        for r in rows
    )
    inner = f'<table class="edu-table"><thead>{head}</thead><tbody>{body}</tbody></table>'
    return f'<div class="edu-table-wrap">{inner}</div>'


def _kpi_card(label: str, value: str) -> str:
    return (
        f'<div class="edu-kpi"><div class="label">{label}</div>'
        f'<div class="value">{value}</div></div>'
    )


def _avg_table(rows: list[dict[str, Any]], *, with_rank: bool) -> str:
    headers = ["单位", "参考人数"]
    if with_rank:
        headers.append("六门排名")
    headers += [lab for _k, lab in _AVG_METRICS]
    body: list[list[str]] = []
    for row in rows:
        cells = [row["name"], str(row["count"])]
        if with_rank:
            cells.append(_fmt(row.get("rank")))
        for key, _lab in _AVG_METRICS:
            cells.append(_fmt(row.get(key)))
        body.append(cells)
    return _table(headers, body) if body else "<p class='edu-sub'>暂无均分数据。</p>"


def _grade_table(rows: list[dict[str, Any]], subjects: tuple[str, ...]) -> str:
    headers = ["单位"]
    for sub in subjects:
        headers += [f"{sub}人数"] + [f"{sub}{g}人数" for g in _GRADES] + [
            f"{sub}{g}率" for g in _GRADES
        ]
    body: list[list[str]] = []
    for row in rows:
        cells = [row["name"]]
        for sub in subjects:
            st = row.get(sub) or {}
            cells.append(str(st.get("n") or 0))
            cells += [str(st.get(g) or 0) for g in _GRADES]
            cells += [f"{st.get(f'{g}_rate') or 0:.2f}%" for g in _GRADES]
        body.append(cells)
    return _table(headers, body) if body else "<p class='edu-sub'>暂无等级数据。</p>"


def _bucket_table(payload: dict[str, Any], grain: str) -> str:
    ns: list[int] = list(payload.get("buckets") or [])
    headers = ["单位", "前20位次"] + [f"前{n}" for n in ns]
    body: list[list[str]] = []
    for row in payload.get(grain) or []:
        cells = [_str(row.get("name")), _str(row.get("top20_ranks")) or "—"]
        cells += [str(row.get(n) or 0) for n in ns]
        body.append(cells)
    cuts = payload.get("cuts") or {}
    cut_row = ["分数", "—"] + [_fmt(cuts.get(n)) for n in ns]
    body.append(cut_row)
    return _table(headers, body) if body else "<p class='edu-sub'>暂无位次数据。</p>"


def _contrib_table(rows: list[dict[str, Any]]) -> str:
    headers = ["选科", "线种", "切线分", "达线人数"] + [lab for _k, lab in _CONTRIB_COLS]
    body = [
        [
            r["track"],
            r["line_name"],
            _fmt(r.get("threshold")),
            str(r.get("reached") or 0),
        ]
        + [_fmt(r.get(k)) for k, _lab in _CONTRIB_COLS]
        for r in rows
    ]
    return _table(headers, body) if body else "<p class='edu-sub'>暂无贡献分（缺分数线或学生）。</p>"


def _combo_table(rows: list[dict[str, Any]]) -> str:
    headers = ["单位"]
    for combo in COMBO_KEYS:
        headers += [
            f"{combo}人数",
            f"{combo}特控",
            f"{combo}特率",
            f"{combo}本科",
            f"{combo}本率",
        ]
    body: list[list[str]] = []
    for row in rows:
        cells = [row["name"]]
        for combo in COMBO_KEYS:
            st = row.get(combo) or {}
            cells += [
                str(st.get("n") or 0),
                str(st.get("tz") or 0),
                f"{st.get('tz_rate') or 0:.2f}%",
                str(st.get("bk") or 0),
                f"{st.get('bk_rate') or 0:.2f}%",
            ]
        body.append(cells)
    return _table(headers, body) if body else "<p class='edu-sub'>暂无组合达线。</p>"


def _roster_table(rows: list[dict[str, Any]]) -> str:
    from src.agent.education.privacy_mode import is_anonymize_display_enabled

    reveal = not is_anonymize_display_enabled()
    headers = (
        ["全市名次", "姓名", "学号", "学校", "六门总分"]
        if reveal
        else ["全市名次", "匿名学号", "学校", "六门总分"]
    )
    for _k, lab in _ROSTER_SUBJECTS:
        headers += [lab, f"{lab}名次"]
    body: list[list[str]] = []
    for row in rows:
        if reveal:
            cells = [
                str(row.get("rank")),
                _str(row.get("xm")) or "—",
                _str(row.get("xh")) or _str(row.get("anon_stu_id")) or "—",
                _str(row.get("xx")),
                _fmt(row.get("zf6m")),
            ]
        else:
            cells = [
                str(row.get("rank")),
                _str(row.get("anon_stu_id")) or "—",
                _str(row.get("xx")),
                _fmt(row.get("zf6m")),
            ]
        for key, _lab in _ROSTER_SUBJECTS:
            cells += [_fmt(row.get(key)), _fmt(row.get(f"{key}_rank"))]
        body.append(cells)
    return _table(headers, body) if body else "<p class='edu-sub'>暂无高分名单。</p>"


def build_bureau_report_data(
    kind: str,
    students: list[dict[str, Any]],
    bars: list[dict[str, Any]] | None = None,
    *,
    exam_name: str = "",
    question: str = "",
    elite_keys: set[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    rt = BUREAU_KINDS.get(kind, ReportType.SUBJECT_AVG)
    q = _str(question)
    track = parse_track(q)
    enrolled = wants_enrolled_only(q)
    elite = elite_keys if wants_elite_class(q) else None
    rows = filter_students(
        normalize_students(students),
        track=track,
        enrolled_only=enrolled,
        elite_keys=elite,
    )
    exam = _str(exam_name) or "本次考试"
    scope = "应届" if enrolled else "全员"
    if track:
        scope += f"·{track}"
    if elite is not None:
        scope += "·尖子班"

    primary = "<p class='edu-sub'>暂无数据。</p>"
    secondary = ""
    primary_title = "区县" if kind != "elite_roster" else "名单"
    secondary_title = "学校/班级"
    insight = f"<p>{exam} {scope}共 {len(rows)} 人。</p>"
    recs = ["口径由学生行重算，不以离线汇总表为准。"]
    kpi = [_kpi_card("参考人数", str(len(rows)))]

    if kind == "subject_avg":
        ranked = True
        sch_grain = "class" if elite is not None else "school"
        slices = [track] if track else ["全员", "物理类", "历史类"]
        heading = {
            "全员": "全员",
            "物理类": "理科（物理类）",
            "历史类": "文科（历史类）",
        }

        def _avg_sections(grain: str) -> str:
            parts: list[str] = []
            for t in slices:
                subset = rows if t == "全员" else [r for r in rows if r.get("track") == t]
                parts.append(
                    f"<h3>{heading.get(t, t)}</h3>"
                    + _avg_table(
                        aggregate_subject_avg(subset, grain, with_rank=ranked),
                        with_rank=ranked,
                    )
                )
            return "".join(parts)

        primary = _avg_sections("district")
        secondary = _avg_sections(sch_grain)
        city = aggregate_subject_avg(rows, "district")[0] if rows else {}
        kpi.append(_kpi_card("六门总均分", _fmt(city.get("zf6m"))))
        kpi.append(_kpi_card("三门总均分", _fmt(city.get("zf3m"))))
        if not track:
            for t, lab in (("物理类", "理科六门均分"), ("历史类", "文科六门均分")):
                sub = [r for r in rows if r.get("track") == t]
                m = _mean([float(r["zf6m"]) for r in sub if r.get("zf6m") is not None])
                kpi.append(_kpi_card(lab, _fmt(m)))
        insight = (
            f"<p class='edu-insight-line'>{exam} {scope}六门总均分 "
            f"{_fmt(city.get('zf6m'))}（{len(rows)} 人）。</p>"
        )
        recs.append("三/四/六门均分用 AVG(zf3m/zf4m/zf6m)，禁止再除以 3。")
        recs.append("均分按全员、理科（物理类）、文科（历史类）分表，排名在各表内重算。")
    elif kind == "assign_grade":
        dist = aggregate_assign_grade(rows, "district")
        sch = aggregate_assign_grade(rows, "school")
        hx_sw, zz_dl = ("化学", "生物"), ("政治", "地理")
        primary = "<h3>化学、生物</h3>" + _grade_table(dist, hx_sw)
        primary += "<h3>政治、地理</h3>" + _grade_table(dist, zz_dl)
        secondary = "<h3>化学、生物</h3>" + _grade_table(sch, hx_sw)
        secondary += "<h3>政治、地理</h3>" + _grade_table(sch, zz_dl)
        city_hx = (dist[0].get("化学") if dist else {}) or {}
        kpi.append(_kpi_card("化学A率", f"{city_hx.get('A_rate') or 0:.2f}%"))
        recs.append("等级按 overview.*dj 聚合；无等级列则人数为 0。")
    elif kind == "rank_bucket":
        tracks = [track] if track else ["物理类", "历史类"]
        parts_d: list[str] = []
        parts_s: list[str] = []
        for t in tracks:
            payload = aggregate_rank_buckets(
                [r for r in rows if r.get("track") == t], track=t
            )
            parts_d.append(f"<h3>{t}</h3>" + _bucket_table(payload, "districts"))
            parts_s.append(f"<h3>{t}</h3>" + _bucket_table(payload, "schools"))
        primary = "".join(parts_d)
        secondary = "".join(parts_s)
        recs.append("前 N 含并列（zf6m≥第 N 名分数）；校前 20 位次为名次串，不出姓名。")
    elif kind == "contribution":
        contrib = aggregate_contribution(rows, bars or [])
        primary = _contrib_table(contrib)
        if contrib:
            kpi.append(_kpi_card("线种数", str(len(contrib))))
        recs.append("贡献分=达该线且 zf6m 等于切线分的学生各科均值；无人则取达线末位。")
    elif kind == "combo_reach":
        combo = aggregate_combo_reach(rows, bars or [])
        primary = _combo_table(combo)
        recs.append("仅物理类组合；特控/本科阈值来自 tb_fraction_bar。")
    elif kind == "elite_roster":
        from src.agent.education.privacy_mode import is_anonymize_display_enabled

        t = track or "物理类"
        top_n = 30 if t == "历史类" else 100
        roster = build_elite_roster(rows, track=t, top_n=top_n)
        primary = _roster_table(roster)
        kpi.append(_kpi_card("名单人数", str(len(roster))))
        recs.append(
            "可展示姓名、学号与校名。"
            if not is_anonymize_display_enabled()
            else "只展示匿名学号与校码，禁止姓名/考生号/身份证。"
        )
    elif kind == "score_band":
        from src.agent.education.score_band import build_score_band_tables

        payload = build_score_band_tables(rows, question=q, exam_name=exam)
        primary = payload["primary"]
        secondary = payload["secondary"]
        primary_title = payload["primary_title"]
        secondary_title = payload["secondary_title"]
        for label, value in payload.get("kpis") or []:
            kpi.append(_kpi_card(label, value))
        insight = payload.get("insight") or insight
        recs = list(payload.get("recs") or recs)

    title = f"{exam} · {report_type_label(rt)}"
    return {
        "REPORT_TITLE": title,
        "REPORT_TYPE": report_type_label(rt),
        "REPORT_SUBTITLE": f"{scope} · 学生行重算",
        "REPORT_TIME": "",
        "SCOPE": scope,
        "EXAM_NAME": exam,
        "SUBJECT_NAME": "全科",
        "KPI_GRID": f'<div class="edu-grid">{"".join(kpi)}</div>',
        "PRIMARY_TABLE": primary,
        "SECONDARY_TABLE": secondary,
        "PRIMARY_TITLE": primary_title,
        "SECONDARY_TITLE": secondary_title,
        "GENERAL_INSIGHT": insight,
        "SUMMARY": insight,
        "RECOMMENDATIONS": "<ul>" + "".join(f"<li>{x}</li>" for x in recs) + "</ul>",
        "_stats": {"count": len(rows)},
    }
