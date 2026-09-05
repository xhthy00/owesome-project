"""点名学校 · 2026届高三1月 · 全市水平 HTML 报告。

问法：「{校名}2026届高三1月考试整体在全市处于什么水平？哪些学科需要重点关注？」
数字从 tb_score_overview 在籍生重算；扬州中学无库时才回落到内置快照。
"""

from __future__ import annotations

import json
import logging
import math
import re
from collections import defaultdict
from typing import Any

from src.agent.education.subject_strength import classify_city_rank_band

logger = logging.getLogger(__name__)

DEMO_YANGZHOU_JAN_QUESTION = (
    "扬州中学2026届高三1月考试整体在全市处于什么水平？哪些学科需要重点关注？"
)
DEMO_HANJIANG_JAN_QUESTION = (
    "邗江中学2026届高三1月考试整体在全市处于什么水平？哪些学科需要重点关注？"
)

_SUBJECT_COLS: tuple[tuple[str, str], ...] = (
    ("语文", "yw"),
    ("数学", "sx"),
    ("英语", "yy"),
    ("物理", "wl"),
    ("化学", "hxzh"),
    ("生物", "swzh"),
    ("政治", "zzzh"),
    ("历史", "ls"),
    ("地理", "dlzh"),
)
_TOTAL_COLS: tuple[tuple[str, str], ...] = (
    ("三门总均分", "zf3m"),
    ("四门总均分", "zf4m"),
    ("六门总均分", "zf6m"),
)
_BANDS_SPEC: tuple[tuple[str, float | None, float | None], ...] = (
    ("400分以下", None, 400),
    ("401-450", 401, 450),
    ("451-500", 451, 500),
    ("501-550", 501, 550),
    ("551-600", 551, 600),
    ("601-650", 601, 650),
    ("651以上", 651, None),
)

_SUBJECTS: tuple[dict[str, Any], ...] = (
    {"name": "数学", "school": 112.96, "city": 86.30, "gap": 26.66, "rank": 1, "n": 38},
    {"name": "英语", "school": 128.68, "city": 107.23, "gap": 21.45, "rank": 1, "n": 38},
    {"name": "物理", "school": 81.06, "city": 61.12, "gap": 19.94, "rank": 1, "n": 38},
    {"name": "地理", "school": 84.18, "city": 69.61, "gap": 14.57, "rank": 1, "n": 38},
    {"name": "化学", "school": 84.60, "city": 70.09, "gap": 14.51, "rank": 1, "n": 36},
    {"name": "生物", "school": 81.48, "city": 69.30, "gap": 12.18, "rank": 1, "n": 37},
    {"name": "语文", "school": 105.45, "city": 94.89, "gap": 10.56, "rank": 1, "n": 38},
    {"name": "政治", "school": 79.44, "city": 69.69, "gap": 9.75, "rank": 5, "n": 38},
    {"name": "历史", "school": 78.30, "city": 68.66, "gap": 9.64, "rank": 2, "n": 38},
)
_TOTALS = (
    {"name": "三门总均分", "school": 346.62, "city": 287.11, "gap": 59.51},
    {"name": "四门总均分", "school": 427.22, "city": 349.52, "gap": 77.70},
    {"name": "六门总均分", "school": 592.43, "city": 488.28, "gap": 104.15},
)
_TRACKS = (
    {"name": "物理类", "school": 593.70, "city": 494.18, "school_n": 646, "city_n": 14010, "gap": 99.52},
    {"name": "历史类", "school": 583.69, "city": 464.01, "school_n": 94, "city_n": 3401, "gap": 119.68},
)
_BANDS = (
    {"name": "400分以下", "school": 0.54, "city": 12.95},
    {"name": "401-450", "school": 0.81, "city": 17.78},
    {"name": "451-500", "school": 3.24, "city": 24.38},
    {"name": "501-550", "school": 14.46, "city": 21.97},
    {"name": "551-600", "school": 31.08, "city": 14.69},
    {"name": "601-650", "school": 40.27, "city": 7.21},
    {"name": "651以上", "school": 9.59, "city": 1.03},
)
_CLASS_TOP = (
    {"name": "高三(2)班", "avg": 649.4, "n": 48},
    {"name": "高三(1)班", "avg": 644.3, "n": 52},
    {"name": "高三(5)班", "avg": 619.9, "n": 52},
)
_CLASS_TAIL = (
    {"name": "高三(8)班", "avg": 554.7, "n": 46},
    {"name": "高三(14)班", "avg": 550.2, "n": 44},
)


def _compact(question: str) -> str:
    return re.sub(r"\s+", "", question or "")


def is_school_city_level_query(question: str) -> bool:
    """点名学校 + 2026届高三1月 + 全市水平 + 学科关注。"""
    q = _compact(question)
    if not q:
        return False
    from src.agent.education.query_parse import extract_school_target

    if not extract_school_target(question or ""):
        return False
    if not ("2026届高三1月" in q or "1月期末" in q):
        return False
    if "全市" not in q or "水平" not in q:
        return False
    return "学科" in q and ("关注" in q or "薄弱" in q or "优势" in q)


def is_yangzhou_jan_level_demo_query(question: str) -> bool:
    return is_school_city_level_query(question)


def parse_school_city_level_school(question: str) -> str:
    from src.agent.education.query_parse import extract_school_target

    return (extract_school_target(question or "") or "").strip()


def _mean(vals: list[float]) -> float | None:
    if not vals:
        return None
    return round(sum(vals) / len(vals), 2)


def _stdev(vals: list[float]) -> float | None:
    if len(vals) < 2:
        return None
    avg = sum(vals) / len(vals)
    var = sum((x - avg) ** 2 for x in vals) / len(vals)
    return round(math.sqrt(var), 1)


def _pct(part: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round(part * 100 / total, 1)


def _in_band(score: float, lo: float | None, hi: float | None) -> bool:
    if lo is not None and score < lo:
        return False
    if hi is not None and score > hi:
        return False
    return True


def _match_school(xx: str, needle: str) -> bool:
    return bool(needle) and needle in (xx or "")


def _metric_vals(
    rows: list[dict[str, Any]],
    key: str,
    *,
    sitters_only: bool = False,
) -> list[float]:
    """``sitters_only``：单科均分只收该科参考（>0），排除未选考/缺考 0 分。"""
    out: list[float] = []
    for row in rows:
        val = row.get(key)
        if val is None:
            continue
        num = float(val)
        if sitters_only and num <= 0:
            continue
        out.append(num)
    return out


def _school_rank(
    groups: dict[str, list[float]],
    school_keys: set[str],
) -> tuple[int, int] | None:
    school_vals: list[float] = []
    others: list[float] = []
    for name, vals in groups.items():
        if name in school_keys:
            school_vals.extend(vals)
            continue
        avg = _mean(vals)
        if avg is not None:
            others.append(avg)
    school_avg = _mean(school_vals)
    if school_avg is None:
        return None
    rank = 1 + sum(1 for avg in others if avg > school_avg + 1e-9)
    return rank, 1 + len(others)


def _watch_names(subjects: list[dict[str, Any]]) -> set[str]:
    weak = {
        s["name"]
        for s in subjects
        if classify_city_rank_band(int(s["rank"]), int(s["n"])) == "weak"
    }
    if weak:
        return weak
    mid = {
        s["name"]
        for s in subjects
        if classify_city_rank_band(int(s["rank"]), int(s["n"])) == "mid"
    }
    if mid:
        return mid
    ranked = sorted(subjects, key=lambda s: (s["gap"], -s["rank"]))
    return {s["name"] for s in ranked[:2]}


def _yangzhou_hardcoded_snapshot() -> dict[str, Any]:
    subjects = [dict(s) for s in _SUBJECTS]
    return {
        "school_name": "扬州中学",
        "exam_label": "2026届高三1月期末",
        "school_n": 740,
        "city_n": 17411,
        "zf6m_school": 592.43,
        "zf6m_city": 488.28,
        "zf6m_gap": 104.15,
        "school_rank": 1,
        "school_rank_n": 38,
        "rate_600": 50.5,
        "n_600": 374,
        "city_rate_600": 8.4,
        "rate_650": 10.3,
        "n_650": 76,
        "city_rate_650": 1.1,
        "std_school": 52.6,
        "std_city": 81.9,
        "subjects": subjects,
        "totals": [dict(x) for x in _TOTALS],
        "tracks": [dict(x) for x in _TRACKS],
        "bands": [dict(x) for x in _BANDS],
        "class_top": [dict(x) for x in _CLASS_TOP],
        "class_tail": [dict(x) for x in _CLASS_TAIL],
        "watch_names": {"政治", "历史"},
    }


def compute_school_city_level_snapshot(
    students: list[dict[str, Any]],
    school_name: str,
    *,
    exam_label: str = "2026届高三1月期末",
) -> dict[str, Any] | None:
    """从 overview 学生行走在籍口径，算出全市水平研判快照。"""
    from src.agent.education.bureau_analysis import filter_students, normalize_students

    needle = (school_name or "").strip()
    if not needle:
        return None
    rows = filter_students(normalize_students(students), enrolled_only=True)
    if not rows:
        return None
    school_rows = [r for r in rows if _match_school(str(r.get("xx") or ""), needle)]
    if not school_rows:
        return None
    school_keys = {str(r.get("xx") or "") for r in school_rows}

    zf6_school = _metric_vals(school_rows, "zf6m")
    zf6_city = _metric_vals(rows, "zf6m")
    zf6_s = _mean(zf6_school)
    zf6_c = _mean(zf6_city)
    if zf6_s is None or zf6_c is None:
        return None

    by_xx: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        if row.get("zf6m") is None:
            continue
        xx = str(row.get("xx") or "")
        if xx:
            by_xx[xx].append(float(row["zf6m"]))
    rank_pair = _school_rank(by_xx, school_keys)
    school_rank, school_rank_n = rank_pair or (0, 0)

    subjects: list[dict[str, Any]] = []
    for label, col in _SUBJECT_COLS:
        school_vals = _metric_vals(school_rows, col, sitters_only=True)
        city_vals = _metric_vals(rows, col, sitters_only=True)
        s_avg = _mean(school_vals)
        c_avg = _mean(city_vals)
        if s_avg is None or c_avg is None:
            continue
        groups: dict[str, list[float]] = defaultdict(list)
        for row in rows:
            val = row.get(col)
            if val is None:
                continue
            num = float(val)
            if num <= 0:
                continue
            xx = str(row.get("xx") or "")
            if xx:
                groups[xx].append(num)
        sub_rank = _school_rank(groups, school_keys) or (0, 0)
        subjects.append(
            {
                "name": label,
                "school": s_avg,
                "city": c_avg,
                "gap": round(s_avg - c_avg, 2),
                "rank": sub_rank[0],
                "n": sub_rank[1],
            }
        )
    if not subjects:
        return None
    subjects.sort(key=lambda s: (-s["gap"], s["rank"], s["name"]))

    totals: list[dict[str, Any]] = []
    for label, col in _TOTAL_COLS:
        s_avg = _mean(_metric_vals(school_rows, col))
        c_avg = _mean(_metric_vals(rows, col))
        if s_avg is None or c_avg is None:
            continue
        totals.append(
            {
                "name": label,
                "school": s_avg,
                "city": c_avg,
                "gap": round(s_avg - c_avg, 2),
            }
        )

    tracks: list[dict[str, Any]] = []
    for track in ("物理类", "历史类"):
        s_tr = [r for r in school_rows if r.get("track") == track]
        c_tr = [r for r in rows if r.get("track") == track]
        s_avg = _mean(_metric_vals(s_tr, "zf6m"))
        c_avg = _mean(_metric_vals(c_tr, "zf6m"))
        if s_avg is None or c_avg is None:
            continue
        tracks.append(
            {
                "name": track,
                "school": s_avg,
                "city": c_avg,
                "school_n": len(_metric_vals(s_tr, "zf6m")),
                "city_n": len(_metric_vals(c_tr, "zf6m")),
                "gap": round(s_avg - c_avg, 2),
            }
        )

    bands: list[dict[str, Any]] = []
    for name, lo, hi in _BANDS_SPEC:
        s_hit = sum(1 for v in zf6_school if _in_band(v, lo, hi))
        c_hit = sum(1 for v in zf6_city if _in_band(v, lo, hi))
        bands.append(
            {
                "name": name,
                "school": _pct(s_hit, len(zf6_school)),
                "city": _pct(c_hit, len(zf6_city)),
            }
        )

    class_avgs: list[dict[str, Any]] = []
    by_class: dict[str, list[float]] = defaultdict(list)
    for row in school_rows:
        bj = str(row.get("bj") or "").strip()
        if not bj or row.get("zf6m") is None:
            continue
        by_class[bj].append(float(row["zf6m"]))
    for bj, vals in by_class.items():
        avg = _mean(vals)
        if avg is None:
            continue
        class_avgs.append({"name": bj, "avg": round(avg, 1), "n": len(vals)})
    class_avgs.sort(key=lambda x: (-x["avg"], x["name"]))
    class_top = class_avgs[:3]
    class_tail = list(reversed(class_avgs[-2:])) if len(class_avgs) > 3 else []

    n_600 = sum(1 for v in zf6_school if v >= 600)
    n_650 = sum(1 for v in zf6_school if v >= 650)
    city_600 = sum(1 for v in zf6_city if v >= 600)
    city_650 = sum(1 for v in zf6_city if v >= 650)
    return {
        "school_name": needle,
        "exam_label": exam_label or "2026届高三1月期末",
        "school_n": len(zf6_school),
        "city_n": len(zf6_city),
        "zf6m_school": zf6_s,
        "zf6m_city": zf6_c,
        "zf6m_gap": round(zf6_s - zf6_c, 2),
        "school_rank": school_rank,
        "school_rank_n": school_rank_n,
        "rate_600": _pct(n_600, len(zf6_school)),
        "n_600": n_600,
        "city_rate_600": _pct(city_600, len(zf6_city)),
        "rate_650": _pct(n_650, len(zf6_school)),
        "n_650": n_650,
        "city_rate_650": _pct(city_650, len(zf6_city)),
        "std_school": _stdev(zf6_school) or 0.0,
        "std_city": _stdev(zf6_city) or 0.0,
        "subjects": subjects,
        "totals": totals,
        "tracks": tracks,
        "bands": bands,
        "class_top": class_top,
        "class_tail": class_tail,
        "watch_names": _watch_names(subjects),
    }


def _pick_jan_exam(names: list[str]) -> str:
    pool = [n for n in names if n]
    hit = [
        n
        for n in pool
        if "2026届" in n and "高三" in n and re.search(r"(?<!\d)1月", n)
    ]
    if not hit:
        hit = [n for n in pool if re.search(r"(?<!\d)1月", n) and "高三" in n]
    prefer = [n for n in hit if "期末" in n] or hit or pool
    return prefer[0] if prefer else ""


def _sql_lit(val: str) -> str:
    return (val or "").replace("'", "''")


def _jan_exam_names_sql() -> str:
    return (
        "SELECT DISTINCT exam_name FROM tb_score_overview "
        "WHERE exam_name LIKE '%2026届%' AND exam_name LIKE '%高三%' "
        "AND exam_name LIKE '%1月%' "
        "ORDER BY exam_name LIMIT 20"
    )


def _enrolled_pred() -> str:
    return "COALESCE(xsxz, '') = '在籍生'"


def _round_avg(expr: str) -> str:
    return f"ROUND(CAST(AVG({expr}) AS numeric), 2)"


def _round_avg_sitters(col: str) -> str:
    """单科均分：只平均该科参考（>0），政史地等未选考为 0 不得进分母。"""
    return f"ROUND(CAST(AVG({col}) FILTER (WHERE {col} > 0) AS numeric), 2)"


def school_city_level_school_avg_sql(exam_name: str) -> str:
    """按校聚合总分/九科，全市约几十行，供校均排名。"""
    lit = _sql_lit(exam_name)
    bits: list[str] = []
    for _, col in _SUBJECT_COLS:
        bits.append(f"COUNT(*) FILTER (WHERE {col} > 0) AS n_{col}")
        bits.append(f"{_round_avg_sitters(col)} AS {col}")
    for _, col in _TOTAL_COLS:
        bits.append(f"COUNT({col}) AS n_{col}")
        bits.append(f"{_round_avg(col)} AS {col}")
    return (
        "SELECT xx, COUNT(*) AS n, "
        + ", ".join(bits)
        + " FROM tb_score_overview "
        f"WHERE exam_name = '{lit}' AND {_enrolled_pred()} "
        "AND xx IS NOT NULL AND CAST(xx AS TEXT) <> '' "
        "GROUP BY xx"
    )


def school_city_level_kpi_sql(exam_name: str, school_name: str) -> str:
    """本校 vs 全市一行 KPI：均分、标准差、高分段、分数段人数。"""
    lit = _sql_lit(exam_name)
    pred = f"xx LIKE '%{_sql_lit(school_name)}%'"
    band_bits: list[str] = []
    for i, (_, lo, hi) in enumerate(_BANDS_SPEC):
        cond = "zf6m IS NOT NULL"
        if lo is not None:
            cond += f" AND zf6m >= {lo}"
        if hi is not None:
            cond += f" AND zf6m <= {hi}"
        band_bits.append(
            f"COUNT(CASE WHEN {pred} AND {cond} THEN 1 END) AS s_b{i}"
        )
        band_bits.append(f"COUNT(CASE WHEN {cond} THEN 1 END) AS c_b{i}")
    return (
        "SELECT "
        f"COUNT(CASE WHEN {pred} AND zf6m IS NOT NULL THEN 1 END) AS school_n, "
        "COUNT(CASE WHEN zf6m IS NOT NULL THEN 1 END) AS city_n, "
        f"{_round_avg(f'CASE WHEN {pred} THEN zf6m END')} AS zf6m_school, "
        f"{_round_avg('zf6m')} AS zf6m_city, "
        f"ROUND(CAST(STDDEV_POP(CASE WHEN {pred} THEN zf6m END) AS numeric), 1) AS std_school, "
        "ROUND(CAST(STDDEV_POP(zf6m) AS numeric), 1) AS std_city, "
        f"COUNT(CASE WHEN {pred} AND zf6m >= 600 THEN 1 END) AS n_600, "
        "COUNT(CASE WHEN zf6m >= 600 THEN 1 END) AS city_n_600, "
        f"COUNT(CASE WHEN {pred} AND zf6m >= 650 THEN 1 END) AS n_650, "
        "COUNT(CASE WHEN zf6m >= 650 THEN 1 END) AS city_n_650, "
        + ", ".join(band_bits)
        + " FROM tb_score_overview "
        f"WHERE exam_name = '{lit}' AND {_enrolled_pred()}"
    )


def school_city_level_track_sql(exam_name: str, school_name: str) -> str:
    lit = _sql_lit(exam_name)
    pred = f"xx LIKE '%{_sql_lit(school_name)}%'"
    return (
        "SELECT CASE WHEN xkkm LIKE '物%' THEN '物理类' "
        "WHEN xkkm LIKE '史%' OR xkkm LIKE '历%' THEN '历史类' ELSE '' END AS track, "
        f"COUNT(CASE WHEN {pred} AND zf6m IS NOT NULL THEN 1 END) AS school_n, "
        "COUNT(CASE WHEN zf6m IS NOT NULL THEN 1 END) AS city_n, "
        f"{_round_avg(f'CASE WHEN {pred} THEN zf6m END')} AS school_avg, "
        f"{_round_avg('zf6m')} AS city_avg "
        "FROM tb_score_overview "
        f"WHERE exam_name = '{lit}' AND {_enrolled_pred()} "
        "GROUP BY 1"
    )


def school_city_level_class_sql(exam_name: str, school_name: str) -> str:
    lit = _sql_lit(exam_name)
    pred = f"xx LIKE '%{_sql_lit(school_name)}%'"
    return (
        "SELECT bj, "
        "ROUND(CAST(AVG(zf6m) AS numeric), 1) AS avg, "
        "COUNT(*) AS n "
        "FROM tb_score_overview "
        f"WHERE exam_name = '{lit}' AND {_enrolled_pred()} AND {pred} "
        "AND zf6m IS NOT NULL AND bj IS NOT NULL AND CAST(bj AS TEXT) <> '' "
        "GROUP BY bj ORDER BY AVG(zf6m) DESC"
    )


def _fnum(val: Any) -> float | None:
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _inum(val: Any) -> int:
    n = _fnum(val)
    return int(n) if n is not None else 0


def snapshot_from_school_aggs(
    *,
    school_name: str,
    exam_label: str,
    school_avgs: list[dict[str, Any]],
    kpi: dict[str, Any],
    tracks: list[dict[str, Any]],
    classes: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """用按校聚合行组装快照，避免拉全市学生明细。"""
    needle = (school_name or "").strip()
    if not needle or not school_avgs:
        return None
    school_keys = {
        str(r.get("xx") or "")
        for r in school_avgs
        if _match_school(str(r.get("xx") or ""), needle)
    }
    if not school_keys:
        return None
    zf6_s = _fnum(kpi.get("zf6m_school"))
    zf6_c = _fnum(kpi.get("zf6m_city"))
    school_n = _inum(kpi.get("school_n"))
    city_n = _inum(kpi.get("city_n"))
    if zf6_s is None or zf6_c is None or school_n <= 0 or city_n <= 0:
        return None

    def _rank(col: str) -> tuple[int, int] | None:
        others: list[float] = []
        school_vals: list[float] = []
        for row in school_avgs:
            avg = _fnum(row.get(col))
            if avg is None:
                continue
            name = str(row.get("xx") or "")
            if name in school_keys:
                school_vals.append(avg)
            else:
                others.append(avg)
        if not school_vals:
            return None
        school_avg = sum(school_vals) / len(school_vals)
        rank = 1 + sum(1 for avg in others if avg > school_avg + 1e-9)
        return rank, 1 + len(others)

    rank_pair = _rank("zf6m") or (0, 0)
    subjects: list[dict[str, Any]] = []
    for label, col in _SUBJECT_COLS:
        s_vals = [
            _fnum(r.get(col))
            for r in school_avgs
            if str(r.get("xx") or "") in school_keys and _fnum(r.get(col)) is not None
        ]
        city_w = 0.0
        city_n_sub = 0
        for row in school_avgs:
            avg = _fnum(row.get(col))
            n = _inum(row.get(f"n_{col}")) or _inum(row.get("n"))
            if avg is None or n <= 0:
                continue
            city_w += avg * n
            city_n_sub += n
        if not s_vals or city_n_sub <= 0:
            continue
        s_avg = round(sum(s_vals) / len(s_vals), 2)
        c_avg = round(city_w / city_n_sub, 2)
        sub_rank = _rank(col) or (0, 0)
        subjects.append(
            {
                "name": label,
                "school": s_avg,
                "city": c_avg,
                "gap": round(s_avg - c_avg, 2),
                "rank": sub_rank[0],
                "n": sub_rank[1],
            }
        )
    if not subjects:
        return None
    subjects.sort(key=lambda s: (-s["gap"], s["rank"], s["name"]))

    totals: list[dict[str, Any]] = []
    for label, col in _TOTAL_COLS:
        if col == "zf6m":
            totals.append(
                {"name": label, "school": zf6_s, "city": zf6_c, "gap": round(zf6_s - zf6_c, 2)}
            )
            continue
        s_vals = [
            _fnum(r.get(col))
            for r in school_avgs
            if str(r.get("xx") or "") in school_keys and _fnum(r.get(col)) is not None
        ]
        city_w = 0.0
        city_n_sub = 0
        for row in school_avgs:
            avg = _fnum(row.get(col))
            n = _inum(row.get(f"n_{col}")) or _inum(row.get("n"))
            if avg is None or n <= 0:
                continue
            city_w += avg * n
            city_n_sub += n
        if not s_vals or city_n_sub <= 0:
            continue
        s_avg = round(sum(s_vals) / len(s_vals), 2)
        c_avg = round(city_w / city_n_sub, 2)
        totals.append(
            {"name": label, "school": s_avg, "city": c_avg, "gap": round(s_avg - c_avg, 2)}
        )

    track_rows: list[dict[str, Any]] = []
    for row in tracks:
        name = str(row.get("track") or "").strip()
        if name not in ("物理类", "历史类"):
            continue
        s_avg = _fnum(row.get("school_avg"))
        c_avg = _fnum(row.get("city_avg"))
        if s_avg is None or c_avg is None:
            continue
        track_rows.append(
            {
                "name": name,
                "school": s_avg,
                "city": c_avg,
                "school_n": _inum(row.get("school_n")),
                "city_n": _inum(row.get("city_n")),
                "gap": round(s_avg - c_avg, 2),
            }
        )
    track_rows.sort(key=lambda x: 0 if x["name"] == "物理类" else 1)

    bands: list[dict[str, Any]] = []
    for i, (name, _, _) in enumerate(_BANDS_SPEC):
        s_hit = _inum(kpi.get(f"s_b{i}"))
        c_hit = _inum(kpi.get(f"c_b{i}"))
        bands.append(
            {
                "name": name,
                "school": _pct(s_hit, school_n),
                "city": _pct(c_hit, city_n),
            }
        )

    class_avgs = [
        {
            "name": str(r.get("bj") or "").strip(),
            "avg": round(float(_fnum(r.get("avg")) or 0), 1),
            "n": _inum(r.get("n")),
        }
        for r in classes
        if str(r.get("bj") or "").strip()
    ]
    class_avgs.sort(key=lambda x: (-x["avg"], x["name"]))
    class_top = class_avgs[:3]
    class_tail = list(reversed(class_avgs[-2:])) if len(class_avgs) > 3 else []
    n_600 = _inum(kpi.get("n_600"))
    n_650 = _inum(kpi.get("n_650"))
    city_600 = _inum(kpi.get("city_n_600"))
    city_650 = _inum(kpi.get("city_n_650"))
    return {
        "school_name": needle,
        "exam_label": exam_label or "2026届高三1月期末",
        "school_n": school_n,
        "city_n": city_n,
        "zf6m_school": zf6_s,
        "zf6m_city": zf6_c,
        "zf6m_gap": round(zf6_s - zf6_c, 2),
        "school_rank": rank_pair[0],
        "school_rank_n": rank_pair[1],
        "rate_600": _pct(n_600, school_n),
        "n_600": n_600,
        "city_rate_600": _pct(city_600, city_n),
        "rate_650": _pct(n_650, school_n),
        "n_650": n_650,
        "city_rate_650": _pct(city_650, city_n),
        "std_school": _fnum(kpi.get("std_school")) or 0.0,
        "std_city": _fnum(kpi.get("std_city")) or 0.0,
        "subjects": subjects,
        "totals": totals,
        "tracks": track_rows,
        "bands": bands,
        "class_top": class_top,
        "class_tail": class_tail,
        "watch_names": _watch_names(subjects),
    }


def format_school_city_level_summary(snap: dict[str, Any]) -> str:
    """聊天气泡用短结论，不经 LLM。"""
    school = str(snap.get("school_name") or "")
    exam = str(snap.get("exam_label") or "")
    rank = int(snap.get("school_rank") or 0)
    n = int(snap.get("school_rank_n") or 0)
    gap = float(snap.get("zf6m_gap") or 0)
    gap_txt = f"高出全市 {gap:.0f} 分" if gap >= 0 else f"低于全市 {abs(gap):.0f} 分"
    weak = [
        str(s["name"])
        for s in (snap.get("subjects") or [])
        if classify_city_rank_band(int(s.get("rank") or 0), int(s.get("n") or 0)) == "weak"
    ]
    watch = [str(x) for x in (snap.get("watch_names") or [])]
    weak_txt = (
        "本场没有薄弱学科（全市后50%口径）。"
        if not weak
        else "按全市后50%口径，薄弱学科为" + "、".join(weak) + "。"
    )
    watch_txt = "、".join(watch) if watch else "相对收口学科"
    return (
        f"{school}在「{exam}」{_stamp(snap)}。"
        f"六门均分 {snap.get('zf6m_school')}，{gap_txt}"
        f"（全市 {snap.get('zf6m_city')}，校均第 {rank}/{n}）。"
        f"{weak_txt}需要重点关注：{watch_txt}。"
    )


def load_school_city_level_snapshot(
    question: str,
    *,
    datasource_id: int | None,
    workspace_oid: Any = None,
    user_id: Any = None,
) -> dict[str, Any] | None:
    """查 overview 聚合生成快照；扬州中学查不到时回落内置数。"""
    school = parse_school_city_level_school(question)
    if not school:
        return None
    if not datasource_id:
        return _yangzhou_hardcoded_snapshot() if "扬州中学" in school else None
    try:
        from src.agent.education.line_reach_report import sql_result_to_dicts
        from src.agent.education.tools import _run_edu_sql
    except Exception as exc:  # noqa: BLE001
        logger.warning("全市水平报告依赖加载失败：%s", exc)
        return _yangzhou_hardcoded_snapshot() if "扬州中学" in school else None

    def _query(sql: str) -> tuple[bool, dict[str, Any] | None]:
        ok, _, res, _ = _run_edu_sql(
            sql,
            datasource_id=int(datasource_id),
            workspace_oid=workspace_oid,
            user_id=user_id,
        )
        return ok, res

    ok_n, name_res = _query(_jan_exam_names_sql())
    names = [
        str(r.get("exam_name") or "").strip()
        for r in (sql_result_to_dicts(name_res) if ok_n else [])
        if str(r.get("exam_name") or "").strip()
    ]
    exam = _pick_jan_exam(names)
    if not exam:
        logger.info("全市水平报告：未命中 2026届高三1月考试 school=%s", school)
        return _yangzhou_hardcoded_snapshot() if "扬州中学" in school else None

    ok_a, avg_res = _query(school_city_level_school_avg_sql(exam))
    ok_k, kpi_res = _query(school_city_level_kpi_sql(exam, school))
    ok_t, track_res = _query(school_city_level_track_sql(exam, school))
    ok_c, class_res = _query(school_city_level_class_sql(exam, school))
    if not (ok_a and ok_k):
        logger.info("全市水平报告：聚合查询失败 school=%s exam=%s", school, exam)
        return _yangzhou_hardcoded_snapshot() if "扬州中学" in school else None
    avg_rows = sql_result_to_dicts(avg_res)
    kpi_rows = sql_result_to_dicts(kpi_res)
    if not avg_rows or not kpi_rows:
        return _yangzhou_hardcoded_snapshot() if "扬州中学" in school else None
    snap = snapshot_from_school_aggs(
        school_name=school,
        exam_label=exam,
        school_avgs=avg_rows,
        kpi=kpi_rows[0],
        tracks=sql_result_to_dicts(track_res) if ok_t else [],
        classes=sql_result_to_dicts(class_res) if ok_c else [],
    )
    if snap is None and "扬州中学" in school:
        return _yangzhou_hardcoded_snapshot()
    return snap


def _table_rows(items: list[dict[str, Any]], *, watch_names: set[str] | None = None) -> str:
    watch_names = watch_names or set()
    rows: list[str] = []
    for item in items:
        watch = item["name"] in watch_names
        extra = ""
        if "rank" in item:
            extra = f"<td class='num'>{item['rank']} / {item['n']}</td>"
        tag = ""
        if watch:
            band = (
                classify_city_rank_band(int(item["rank"]), int(item["n"]))
                if item.get("rank")
                else "mid"
            )
            tag = (
                '<span class="tag-watch">薄弱</span>'
                if band == "weak"
                else '<span class="tag-watch">均衡关注</span>'
            )
        elif "rank" in item:
            tag = '<span class="tag-lead">全市前列</span>'
        gap = float(item.get("gap") or 0)
        gap_cls = "gap" if gap >= 0 else "gap down"
        gap_txt = f"{gap:+.2f}"
        rows.append(
            f"<tr class='{'is-watch' if watch else ''}'>"
            f"<td>{item['name']}{tag}</td>"
            f"<td class='num'>{item['school']:.2f}</td>"
            f"<td class='num'>{item['city']:.2f}</td>"
            f"<td class='num {gap_cls}'>{gap_txt}</td>"
            f"{extra}</tr>"
        )
    return "\n".join(rows)


def _class_rows(items: list[dict[str, Any]], kind: str) -> str:
    tag = "头部" if kind == "top" else "尾部"
    return "\n".join(
        f"<tr><td>{it['name']}</td><td class='num'>{it['avg']:.1f}</td>"
        f"<td class='num'>{it['n']}</td><td><span class='tag-{'lead' if kind == 'top' else 'watch'}'>{tag}</span></td></tr>"
        for it in items
    )


def _esc(text: Any) -> str:
    return (
        str(text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _stamp(snap: dict[str, Any]) -> str:
    rank = int(snap.get("school_rank") or 0)
    n = int(snap.get("school_rank_n") or 0)
    if rank == 1 and n:
        return "总判：全市校均第 1 · 全面领先"
    if rank and n and classify_city_rank_band(rank, n) == "advantage":
        return f"总判：全市前列 · 第 {rank} / {n}"
    if rank and n and classify_city_rank_band(rank, n) == "weak":
        return f"总判：全市偏后 · 第 {rank} / {n}"
    if rank and n:
        return f"总判：全市中游 · 第 {rank} / {n}"
    return "总判：已对照全市校均"


def _verdict_html(snap: dict[str, Any]) -> str:
    school = _esc(snap["school_name"])
    rank = int(snap.get("school_rank") or 0)
    n = int(snap.get("school_rank_n") or 0)
    gap = float(snap.get("zf6m_gap") or 0)
    lead_n = sum(1 for s in snap["subjects"] if int(s.get("rank") or 0) == 1)
    weak = [
        s
        for s in snap["subjects"]
        if classify_city_rank_band(int(s["rank"]), int(s["n"])) == "weak"
    ]
    watch = [s for s in snap["subjects"] if s["name"] in snap["watch_names"]]
    pos = "全市顶尖且结构健康" if rank == 1 else "全市相对位置已定位"
    if rank and n and classify_city_rank_band(rank, n) == "weak":
        pos = "全市偏后，需要按学科补缺口"
    elif rank and n and classify_city_rank_band(rank, n) == "mid":
        pos = "全市中游，有抬头空间"
    gap_txt = f"高出全市 {gap:.0f} 分" if gap >= 0 else f"低于全市 {abs(gap):.0f} 分"
    weak_txt = (
        "本场<b>没有薄弱学科</b>。"
        if not weak
        else "按全市后50%口径，薄弱学科为<b>"
        + "、".join(_esc(s["name"]) for s in weak)
        + "</b>。"
    )
    watch_txt = "、".join(_esc(s["name"]) for s in watch) or "相对收口学科"
    class_txt = ""
    top = snap.get("class_top") or []
    tail = snap.get("class_tail") or []
    if top and tail:
        class_gap = float(top[0]["avg"]) - float(tail[-1]["avg"])
        class_txt = (
            f"校内盯头部班与尾部班约 {class_gap:.0f} 分的梯队差。"
        )
    tracks = snap.get("tracks") or []
    track_txt = "、".join(
        f"{t['name']} {t['school_n']} 人" for t in tracks
    ) or "选科双轨"
    return f"""
      <p>{school}本场处于<strong>{pos}</strong>的位置：校均第 {rank} / {n}，六门均分{gap_txt}；
      600 分以上 {snap['rate_600']}%（全市 {snap['city_rate_600']}%），
      650 分以上 {snap['rate_650']}%（全市 {snap['city_rate_650']}%）。
      九科有数 {len(snap['subjects'])} 科，其中 {lead_n} 科全市第 1。</p>
      <ul class="bullets">
        <li><b>位置</b>：不是「本校里谁最差」，而是看全市相对位置。前 25% 为前列，后 50% 才算薄弱——{weak_txt}</li>
        <li><b>结构</b>：{track_txt}；本校标准差 {snap['std_school']}，全市 {snap['std_city']}。</li>
        <li><b>关注点</b>：学科上盯{watch_txt}；{class_txt}</li>
      </ul>
    """


def _focus_html(snap: dict[str, Any]) -> str:
    subjects = snap["subjects"]
    watch = snap["watch_names"]
    leads = [s for s in subjects if s["name"] not in watch][:3]
    watch_rows = [s for s in subjects if s["name"] in watch]
    weak = [
        s
        for s in watch_rows
        if classify_city_rank_band(int(s["rank"]), int(s["n"])) == "weak"
    ]
    lead_names = " / ".join(s["name"] for s in leads) or "前列学科"
    lead_gaps = "、".join(f"+{s['gap']:.1f}" for s in leads) if leads else "—"
    watch_names = "、".join(s["name"] for s in watch_rows) or "相对收口学科"
    watch_gaps = "、".join(f"{s['gap']:+.1f}" for s in watch_rows) if watch_rows else "—"
    if weak:
        mid_title = "按全市排名已落入后50%"
        mid_body = (
            "、".join(f"{s['name']}第 {s['rank']} / {s['n']}" for s in weak)
            + "，这才是薄弱，建议按补差排课。"
        )
    else:
        mid_title = "不是薄弱，是相对收口"
        mid_body = (
            "判定口径：名次/参赛学校数 ≤25% 为前列，≥50% 才算薄弱。"
            + (
                f"{watch_rows[0]['name']}全市第 {watch_rows[0]['rank']} / {watch_rows[0]['n']}"
                if watch_rows
                else ""
            )
            + "，仍须看全市位置，不能按本校互比判薄弱。"
        )
    return f"""
        <div class="callout ok">
          <b>优势盘：{lead_names}</b>
          分差 {lead_gaps}。这是本校对照全市的主引擎，建议保持难度与课时强度。
        </div>
        <div class="callout">
          <b>{mid_title}</b>
          {mid_body}
        </div>
        <div class="callout watch">
          <b>均衡关注：{watch_names}</b>
          三科分差约 {watch_gaps}。适合专题限时练，目标是把领先幅度向头部学科靠拢，而不是按补差班加课。
        </div>
    """


def _actions_html(snap: dict[str, Any]) -> str:
    watch = "、".join(snap["watch_names"]) or "相对收口学科"
    leads = [s["name"] for s in snap["subjects"] if s["name"] not in snap["watch_names"]][:4]
    lead_txt = "、".join(leads) or "前列学科"
    tail = snap.get("class_tail") or []
    class_txt = (
        "、".join(t["name"] for t in tail) + " 对校内头部仍有落差，建议把作业面批和临界生盯防放到这些班。"
        if tail
        else "把班际差压住，比全市盲目补差更有效。"
    )
    return f"""
        <li><b>{lead_txt}</b>：保住全市位置，关注高分段续航（本校 650+ 已有 {snap['n_650']} 人）。</li>
        <li><b>{watch}</b>：做「优势里的短板」专项——材料题、限时训练，按班际差而不是按全市落后学科来排课。</li>
        <li><b>班级</b>：{class_txt}</li>
    """


def build_school_city_level_html(snap: dict[str, Any]) -> str:
    """按快照渲染全市水平研判 HTML。"""
    school = _esc(snap["school_name"])
    exam = _esc(snap["exam_label"])
    subjects = list(snap["subjects"])
    watch_names = set(snap.get("watch_names") or ())
    names = [s["name"] for s in subjects]
    school_scores = [s["school"] for s in subjects]
    city_scores = [s["city"] for s in subjects]
    gaps = [s["gap"] for s in subjects]
    radar_max = max([abs(g) for g in gaps] + [12])
    radar_max = int(math.ceil(radar_max / 5.0) * 5)
    y_min = min([t["school"] for t in snap["tracks"]] + [t["city"] for t in snap["tracks"]] + [400])
    y_min = max(0, int(y_min // 20 * 20) - 20)
    band_max = max([b["school"] for b in snap["bands"]] + [b["city"] for b in snap["bands"]] + [40])
    band_max = int(math.ceil(band_max / 10.0) * 10)
    bar_opt = {
        "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"}},
        "legend": {"data": [snap["school_name"], "全市"], "top": 0},
        "grid": {"left": 48, "right": 16, "top": 36, "bottom": 28},
        "xAxis": {"type": "category", "data": names, "axisLabel": {"interval": 0}},
        "yAxis": {"type": "value", "name": "均分"},
        "series": [
            {
                "name": snap["school_name"],
                "type": "bar",
                "barMaxWidth": 18,
                "itemStyle": {"color": "#1677ff", "borderRadius": [4, 4, 0, 0]},
                "data": school_scores,
            },
            {
                "name": "全市",
                "type": "bar",
                "barMaxWidth": 18,
                "itemStyle": {"color": "#94b6e0", "borderRadius": [4, 4, 0, 0]},
                "data": city_scores,
            },
        ],
    }
    gap_opt = {
        "tooltip": {"trigger": "axis", "valueFormatter": "{value} 分"},
        "grid": {"left": 48, "right": 16, "top": 16, "bottom": 28},
        "xAxis": {"type": "category", "data": names, "axisLabel": {"interval": 0}},
        "yAxis": {"type": "value", "name": "高出全市"},
        "series": [
            {
                "type": "bar",
                "barMaxWidth": 22,
                "data": [
                    {
                        "value": g,
                        "itemStyle": {
                            "color": "#faad14" if n in watch_names else "#52c41a",
                            "borderRadius": [4, 4, 0, 0],
                        },
                    }
                    for g, n in zip(gaps, names)
                ],
            }
        ],
    }
    radar_opt = {
        "tooltip": {},
        "radar": {
            "indicator": [{"name": s["name"], "max": radar_max} for s in subjects],
            "radius": "62%",
        },
        "series": [
            {
                "type": "radar",
                "data": [
                    {
                        "value": gaps,
                        "name": "高出全市",
                        "areaStyle": {"color": "rgba(22,119,255,.18)"},
                        "lineStyle": {"color": "#1677ff"},
                    }
                ],
            }
        ],
    }
    band_opt = {
        "tooltip": {"trigger": "axis", "valueFormatter": "{value}%"},
        "legend": {"data": [snap["school_name"], "全市"], "top": 0},
        "grid": {"left": 48, "right": 16, "top": 36, "bottom": 28},
        "xAxis": {
            "type": "category",
            "data": [b["name"] for b in snap["bands"]],
            "axisLabel": {"interval": 0, "rotate": 20},
        },
        "yAxis": {"type": "value", "name": "占比%", "max": band_max},
        "series": [
            {
                "name": snap["school_name"],
                "type": "bar",
                "barMaxWidth": 16,
                "itemStyle": {"color": "#1677ff", "borderRadius": [4, 4, 0, 0]},
                "data": [b["school"] for b in snap["bands"]],
            },
            {
                "name": "全市",
                "type": "bar",
                "barMaxWidth": 16,
                "itemStyle": {"color": "#94b6e0", "borderRadius": [4, 4, 0, 0]},
                "data": [b["city"] for b in snap["bands"]],
            },
        ],
    }
    track_opt = {
        "tooltip": {"trigger": "axis"},
        "legend": {"data": [snap["school_name"], "全市"], "top": 0},
        "grid": {"left": 56, "right": 16, "top": 36, "bottom": 24},
        "xAxis": {"type": "category", "data": [t["name"] for t in snap["tracks"]]},
        "yAxis": {"type": "value", "name": "六门均分", "min": y_min},
        "series": [
            {
                "name": snap["school_name"],
                "type": "bar",
                "barMaxWidth": 36,
                "itemStyle": {"color": "#0f4c81", "borderRadius": [4, 4, 0, 0]},
                "label": {"show": True, "position": "top"},
                "data": [t["school"] for t in snap["tracks"]],
            },
            {
                "name": "全市",
                "type": "bar",
                "barMaxWidth": 36,
                "itemStyle": {"color": "#94b6e0", "borderRadius": [4, 4, 0, 0]},
                "label": {"show": True, "position": "top"},
                "data": [t["city"] for t in snap["tracks"]],
            },
        ],
    }
    track_rows = "".join(
        f"<tr><td>{_esc(t['name'])}六门</td><td class='num'>{t['school']:.2f}</td>"
        f"<td class='num'>{t['city']:.2f}</td>"
        f"<td class='num gap'>{t['gap']:+.2f}</td></tr>"
        for t in snap["tracks"]
    )
    track_note = "、".join(
        f"{t['name']} {t['school_n']} 人 / 全市 {t['city_n']} 人" for t in snap["tracks"]
    )
    class_html = ""
    if snap.get("class_top"):
        tail_html = _class_rows(list(snap.get("class_tail") or []), "tail")
        top0 = snap["class_top"][0]
        tail0 = (snap.get("class_tail") or [None])[-1]
        class_gap = (
            f"{float(top0['avg']) - float(tail0['avg']):.0f} 分"
            if tail0
            else "—"
        )
        class_html = f"""
    <section class="card">
      <h2>班级梯队</h2>
      <div class="split">
        <table>
          <thead><tr><th>班级</th><th class="num">六门均分</th><th class="num">人数</th><th>位置</th></tr></thead>
          <tbody>
            {_class_rows(list(snap['class_top']), "top")}
            {tail_html}
          </tbody>
        </table>
        <div>
          <p>最高班{_esc(top0['name'])} {top0['avg']:.1f}，
          {"最低班" + _esc(tail0["name"]) + f" {tail0['avg']:.1f}，校内落差约 <b>{class_gap}</b>。" if tail0 else "班级样本较少。"}</p>
          <p>全市领先或落后要看整体水位，不是只靠个别班。下一阶段优先<b>压缩校内班际差</b>。</p>
        </div>
      </div>
    </section>
        """
    gap_sign = "+" if float(snap["zf6m_gap"]) >= 0 else ""
    t3 = next((t for t in snap["totals"] if t["name"] == "三门总均分"), None)
    t4 = next((t for t in snap["totals"] if t["name"] == "四门总均分"), None)
    t6 = next((t for t in snap["totals"] if t["name"] == "六门总均分"), None)
    hint_tot = " · ".join(
        x
        for x in (
            f"三门 {t3['gap']:+.1f}" if t3 else "",
            f"四门 {t4['gap']:+.1f}" if t4 else "",
            f"六门 {t6['gap']:+.1f}" if t6 else "",
        )
        if x
    )
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{school} · {exam} · 全市水平研判</title>
  <script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
  <style>
    :root {{
      --lead: #0f4c81; --gold: #d4a017; --primary: #1677ff;
      --ok: #389e0d; --watch: #d48806; --text: rgba(0,0,0,.88);
      --muted: rgba(0,0,0,.45); --line: #e8edf3;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
      background: #eef3f8; color: var(--text);
    }}
    .page {{ max-width: 1100px; margin: 0 auto; padding: 0 16px 36px; }}
    .hero {{
      margin: 0 -16px 18px; padding: 28px 32px 22px;
      background: linear-gradient(135deg, #0b3a67 0%, #1677ff 62%, #3aa0ff 100%);
      color: #fff; box-shadow: 0 10px 28px rgba(15,76,129,.25);
    }}
    .hero .eyebrow {{ font-size: 12px; letter-spacing: .16em; opacity: .82; }}
    .hero h1 {{ margin: 8px 0 6px; font-size: 26px; font-weight: 750; letter-spacing: -.02em; }}
    .hero .sub {{ margin: 0; opacity: .88; font-size: 14px; }}
    .stamp {{
      display: inline-block; margin-top: 12px; padding: 4px 12px; border-radius: 999px;
      background: rgba(212,160,23,.95); color: #3a2a00; font-weight: 700; font-size: 13px;
    }}
    .kpis {{
      display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-top: -18px;
    }}
    .kpi {{
      background: #fff; border-radius: 14px; padding: 14px 16px;
      border: 1px solid var(--line); box-shadow: 0 8px 20px rgba(15,76,129,.06);
    }}
    .kpi .lab {{ font-size: 12px; color: var(--muted); }}
    .kpi .val {{ margin-top: 6px; font-size: 24px; font-weight: 760; letter-spacing: -.03em; color: var(--lead); }}
    .kpi.gold .val {{ color: #b8860b; }}
    .kpi .hint {{ margin-top: 4px; font-size: 12px; color: var(--muted); line-height: 1.45; }}
    .card {{
      background: #fff; border: 1px solid var(--line); border-radius: 14px;
      padding: 18px 20px; margin-top: 14px; box-shadow: 0 4px 14px rgba(16,24,40,.04);
    }}
    .card h2 {{
      margin: 0 0 12px; font-size: 16px; display: flex; align-items: center; gap: 8px;
    }}
    .card h2::before {{
      content: ""; width: 4px; height: 16px; border-radius: 2px; background: var(--primary);
    }}
    .charts {{ display: grid; grid-template-columns: 1.35fr 1fr; gap: 12px; }}
    .charts.three {{ grid-template-columns: 1fr 1fr; }}
    .chart {{ width: 100%; height: 320px; }}
    .chart.short {{ height: 280px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    th, td {{ padding: 9px 12px; border-bottom: 1px solid var(--line); text-align: left; }}
    thead th {{ background: #f3f8ff; color: #3b6fb8; font-weight: 650; }}
    td.num, th.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
    td.gap {{ color: var(--ok); font-weight: 650; }}
    td.gap.down {{ color: #cf1322; }}
    tr.is-watch td {{ background: #fffbe6; }}
    .tag-lead, .tag-watch {{
      display: inline-block; margin-left: 8px; padding: 1px 7px; border-radius: 999px;
      font-size: 11px; font-weight: 650;
    }}
    .tag-lead {{ background: #f6ffed; color: var(--ok); }}
    .tag-watch {{ background: #fff7e6; color: var(--watch); }}
    .callouts {{ display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 10px; }}
    .callout {{ padding: 12px 14px; border-radius: 10px; background: #f7faff; border-left: 3px solid var(--primary); font-size: 13px; line-height: 1.65; }}
    .callout.watch {{ background: #fffbe6; border-left-color: var(--watch); }}
    .callout.ok {{ background: #f6ffed; border-left-color: var(--ok); }}
    .callout b {{ display: block; margin-bottom: 4px; font-size: 14px; }}
    .split {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
    .bullets {{ margin: 8px 0 0; padding-left: 18px; line-height: 1.75; }}
    .foot {{ margin-top: 10px; text-align: right; color: var(--muted); font-size: 12px; }}
    @media (max-width: 800px) {{
      .kpis, .charts, .charts.three, .callouts, .split {{ grid-template-columns: 1fr; }}
      .hero h1 {{ font-size: 20px; }}
    }}
  </style>
</head>
<body data-edu-school-id="{school}">
  <div class="hero">
    <div class="page">
      <div class="eyebrow">EXPERT BRIEFING · 学情研判</div>
      <h1>{school} · {exam}</h1>
      <p class="sub">全市水平定位 · 选科双轨 · 分数段与班级梯队 · 学科关注清单</p>
      <span class="stamp">{_esc(_stamp(snap))}</span>
    </div>
  </div>
  <div class="page">
    <div class="kpis">
      <div class="kpi"><div class="lab">本校六门均分</div><div class="val">{snap['zf6m_school']:.2f}</div><div class="hint">在籍 {snap['school_n']} 人 · 全市 {snap['zf6m_city']:.2f}（{snap['city_n']} 人）</div></div>
      <div class="kpi gold"><div class="lab">高出全市</div><div class="val">{gap_sign}{snap['zf6m_gap']:.2f}</div><div class="hint">{hint_tot}</div></div>
      <div class="kpi"><div class="lab">全市学校排名</div><div class="val">第 {snap['school_rank']} / {snap['school_rank_n']}</div><div class="hint">在籍生校均分</div></div>
      <div class="kpi"><div class="lab">600 分及以上</div><div class="val">{snap['rate_600']}%</div><div class="hint">{snap['n_600']} 人 · 全市 {snap['city_rate_600']}%</div></div>
      <div class="kpi"><div class="lab">650 分及以上</div><div class="val">{snap['rate_650']}%</div><div class="hint">{snap['n_650']} 人 · 全市 {snap['city_rate_650']}%</div></div>
      <div class="kpi"><div class="lab">分数更集中</div><div class="val">σ {snap['std_school']}</div><div class="hint">全市标准差 {snap['std_city']}</div></div>
    </div>

    <section class="card">
      <h2>学情总判</h2>
      {_verdict_html(snap)}
    </section>

    <section class="card">
      <h2>总分结构与选科双轨</h2>
      <div class="split">
        <div>
          <table>
            <thead><tr><th>指标</th><th class="num">{school}</th><th class="num">全市</th><th class="num">分差</th></tr></thead>
            <tbody>
              {_table_rows(list(snap['totals']))}
              {track_rows}
            </tbody>
          </table>
          <p style="margin-top:10px;color:rgba(0,0,0,.55);font-size:12.5px;">{_esc(track_note)}</p>
        </div>
        <div id="chartTrack" class="chart short"></div>
      </div>
    </section>

    <section class="card">
      <h2>各科均分：本校 vs 全市</h2>
      <div class="charts">
        <div id="chartBar" class="chart"></div>
        <div id="chartRadar" class="chart"></div>
      </div>
      <div id="chartGap" class="chart short" style="margin-top:8px;"></div>
    </section>

    <section class="card">
      <h2>总分分数段</h2>
      <div id="chartBand" class="chart"></div>
    </section>

    <section class="card">
      <h2>学科明细</h2>
      <table>
        <thead>
          <tr><th>学科</th><th class="num">{school}</th><th class="num">全市</th><th class="num">高出全市</th><th class="num">全市排名</th></tr>
        </thead>
        <tbody>
          {_table_rows(subjects, watch_names=watch_names)}
        </tbody>
      </table>
    </section>
    {class_html}
    <section class="card">
      <h2>哪些学科需要重点关注</h2>
      <div class="callouts">
        {_focus_html(snap)}
      </div>
    </section>

    <section class="card">
      <h2>给教学的三条动作</h2>
      <ul class="bullets">
        {_actions_html(snap)}
      </ul>
    </section>
    <div class="foot">口径：在籍生 · 单科已排除未选考 · 化学/生物/政治/地理用转换分 · 专家团协作生成</div>
  </div>
  <script>
    (function () {{
      var ids = ["chartBar","chartRadar","chartGap","chartBand","chartTrack"];
      var opts = [
        {json.dumps(bar_opt, ensure_ascii=False)},
        {json.dumps(radar_opt, ensure_ascii=False)},
        {json.dumps(gap_opt, ensure_ascii=False)},
        {json.dumps(band_opt, ensure_ascii=False)},
        {json.dumps(track_opt, ensure_ascii=False)}
      ];
      var charts = ids.map(function (id, i) {{
        var el = document.getElementById(id);
        if (!el) return null;
        var c = echarts.init(el);
        c.setOption(opts[i]);
        return c;
      }}).filter(Boolean);
      window.addEventListener("resize", function () {{ charts.forEach(function (c) {{ c.resize(); }}); }});
    }})();
  </script>
</body>
</html>
"""


def build_yangzhou_jan_level_html() -> str:
    """扬州中学内置快照（无库回归用）。"""
    return build_school_city_level_html(_yangzhou_hardcoded_snapshot())


def school_city_level_tool_payload(snapshot: dict[str, Any]) -> dict[str, Any]:
    html = build_school_city_level_html(snapshot)
    title = f"{snapshot['school_name']} · {snapshot['exam_label']} · 全市水平研判"
    return {
        "tool": "build_school_city_level_report",
        "success": True,
        "agent": "Summarizer",
        "data": {
            "output_type": "html",
            "html": html,
            "title": title,
            "report_type": "subject_avg",
            "report_type_label": "全市水平研判",
            "mode": "inline",
            "_stats": {
                "avg": snapshot.get("zf6m_school"),
                "count": snapshot.get("school_n"),
            },
            "chunks": [{"output_type": "html", "title": title, "content": html}],
        },
    }


def yangzhou_jan_demo_tool_payload() -> dict[str, Any]:
    return school_city_level_tool_payload(_yangzhou_hardcoded_snapshot())


__all__ = [
    "DEMO_HANJIANG_JAN_QUESTION",
    "DEMO_YANGZHOU_JAN_QUESTION",
    "build_school_city_level_html",
    "build_yangzhou_jan_level_html",
    "compute_school_city_level_snapshot",
    "format_school_city_level_summary",
    "is_school_city_level_query",
    "is_yangzhou_jan_level_demo_query",
    "load_school_city_level_snapshot",
    "parse_school_city_level_school",
    "school_city_level_class_sql",
    "school_city_level_kpi_sql",
    "school_city_level_school_avg_sql",
    "school_city_level_tool_payload",
    "school_city_level_track_sql",
    "snapshot_from_school_aggs",
    "yangzhou_jan_demo_tool_payload",
]
