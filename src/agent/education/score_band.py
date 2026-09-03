"""局端分数段：总分十分段 / 学科五分段，从 tb_score_overview 学生行重算。

累计从高分段往下：某段累计人数 = score >= 该段下限。
语数英物史用原始分；化生政地用转换分。市报生不并进四类校。
"""

from __future__ import annotations

import itertools
from collections import defaultdict
from typing import Any

__all__ = [
    "ALL_BLOCKS",
    "DISTRICT_ORDER",
    "SCHOOL_TYPE_ORDER",
    "SUBJECTS",
    "band_label",
    "band_lo",
    "build_score_band_tables",
    "match_district",
    "match_school_type",
    "parse_score_band_blocks",
    "parse_subject_filter",
]

ALL_BLOCKS = (
    "district_total",
    "district_subject",
    "schooltype_total",
    "schooltype_subject",
)
DISTRICT_ORDER = ("全市", "市直", "宝应", "高邮", "仪征", "江都", "邗江", "广陵")
SCHOOL_TYPE_ORDER = ("全市", "引领", "支撑", "发展", "其他", "市报生")
SUBJECTS: tuple[tuple[str, str], ...] = (
    ("yw", "语文"),
    ("sx", "数学"),
    ("yy", "英语"),
    ("wl", "物理"),
    ("hxzh", "化学"),
    ("ls", "历史"),
    ("dlzh", "地理"),
    ("zzzh", "政治"),
    ("swzh", "生物"),
)
_METRIC_HEADERS = ("人数", "比例(%)", "累计人数", "累计比例(%)")
_chart_ids = itertools.count(1)


def band_lo(score: float, width: int) -> int:
    """十分段/五分段下限：690→681，4→1，130→126。"""
    n = int(score)
    if n < 1:
        n = 1
    return ((n - 1) // width) * width + 1


def band_label(lo: int, width: int) -> str:
    return f"{lo} - {lo + width - 1}"


def match_district(dq: str) -> str:
    s = str(dq or "").strip()
    for name in DISTRICT_ORDER[1:]:
        if name in s:
            return name
    return s or "未知"


def match_school_type(row: dict[str, Any]) -> str:
    xsxz = str(row.get("xsxz") or "")
    if "市报" in xsxz:
        return "市报生"
    xxlb = str(row.get("xxlb") or "")
    for name in ("引领", "支撑", "发展"):
        if name in xxlb:
            return name
    return "其他"


def parse_score_band_blocks(question: str) -> set[str]:
    """问句点名则裁剪；默认四块全出。"""
    q = str(question or "").strip()
    named_district = any(h in q for h in ("各区县", "各区", "各地区", "区县"))
    named_school = any(h in q for h in ("各类校", "校类", "引领校", "学校类别"))
    want_district = named_district or not named_school
    want_school = named_school or not named_district

    from src.agent.education.query_parse import SCORE_BAND_SHIFEN_HINTS, SCORE_BAND_WUFEN_HINTS

    mention_shifen = any(h in q for h in SCORE_BAND_SHIFEN_HINTS)
    mention_wufen = any(h in q for h in SCORE_BAND_WUFEN_HINTS)
    mention_total = "总分" in q
    mention_subject = "学科" in q or mention_wufen
    if mention_total and not mention_subject:
        want_total, want_subj = True, False
    elif mention_wufen and not mention_shifen and not mention_total:
        want_total, want_subj = False, True
    elif mention_subject and not mention_shifen and not mention_total:
        want_total, want_subj = False, True
    else:
        want_total, want_subj = True, True

    out: set[str] = set()
    if want_district and want_total:
        out.add("district_total")
    if want_district and want_subj:
        out.add("district_subject")
    if want_school and want_total:
        out.add("schooltype_total")
    if want_school and want_subj:
        out.add("schooltype_subject")
    return out or set(ALL_BLOCKS)


def parse_subject_filter(question: str) -> str | None:
    q = str(question or "")
    for token in ("物理方向", "物理类", "理科", "历史方向", "历史类", "文科"):
        q = q.replace(token, "")
    hits = [name for _key, name in SUBJECTS if name in q]
    if len(hits) == 1:
        return hits[0]
    return None


def _scores(rows: list[dict[str, Any]], key: str) -> list[float]:
    out: list[float] = []
    for row in rows:
        v = row.get(key)
        if v is None:
            continue
        out.append(float(v))
    return out


def _hist(scores: list[float], width: int) -> dict[int, tuple[int, float, int, float]]:
    n = len(scores)
    if n == 0:
        return {}
    by_lo: dict[int, int] = defaultdict(int)
    for s in scores:
        by_lo[band_lo(s, width)] += 1
    out: dict[int, tuple[int, float, int, float]] = {}
    lo = max(by_lo)
    min_lo = min(by_lo)
    while lo >= min_lo:
        count = by_lo.get(lo, 0)
        cum = sum(1 for s in scores if s >= lo)
        out[lo] = (
            count,
            round(100.0 * count / n, 2),
            cum,
            round(100.0 * cum / n, 2),
        )
        lo -= width
    return out


def _column_order(
    rows: list[dict[str, Any]],
    *,
    kind: str,
) -> list[str]:
    if kind == "district":
        seen = {match_district(str(r.get("dq") or "")) for r in rows}
        extra = sorted(s for s in seen if s not in DISTRICT_ORDER)
        return list(DISTRICT_ORDER) + extra
    return list(SCHOOL_TYPE_ORDER)


def _group_key(row: dict[str, Any], kind: str) -> str:
    if kind == "district":
        return match_district(str(row.get("dq") or ""))
    return match_school_type(row)


def _empty_metrics() -> tuple[int, float, int, float]:
    return (0, 0.0, 0, 0.0)


def _band_frame(
    columns: dict[str, list[float]],
    col_order: list[str],
    width: int,
) -> tuple[list[int], dict[str, dict[int, tuple[int, float, int, float]]]]:
    hists = {name: _hist(columns.get(name) or [], width) for name in col_order}
    all_los: set[int] = set()
    for h in hists.values():
        all_los.update(h)
    if not all_los:
        return [], hists
    los = sorted(all_los, reverse=True)
    kept: list[int] = []
    for lo in los:
        if any((hists[name].get(lo) or _empty_metrics())[0] > 0 for name in col_order):
            kept.append(lo)
    return kept, hists


def _band_chart_html(
    columns: dict[str, list[float]],
    col_order: list[str],
    width: int,
    title: str,
) -> str:
    from src.agent.education.charts import build_chart_option

    kept, hists = _band_frame(columns, col_order, width)
    if not kept:
        return ""
    labels = [band_label(lo, width) for lo in sorted(kept)]
    metrics: list[dict[str, Any]] = []
    for name in col_order:
        vals = [(hists[name].get(lo) or _empty_metrics())[0] for lo in sorted(kept)]
        if any(v > 0 for v in vals):
            metrics.append({"name": name, "values": vals})
    if not metrics:
        return ""
    option = build_chart_option(
        "group_compare_bar",
        {
            "groups": labels,
            "metrics": metrics,
            "y_name": "人数",
            "x_rotate": 45 if len(labels) > 8 else 0,
            "show_label": len(labels) <= 12,
        },
        title=title,
    )
    if not option:
        return ""
    cid = f"bandBar{next(_chart_ids)}"
    return (
        f"<div id='{cid}' class='edu-chart edu-band-chart'></div>"
        f"<script type='application/json' id='{cid}Data'>{option}</script>"
    )


def _band_table(
    columns: dict[str, list[float]],
    col_order: list[str],
    width: int,
) -> str:
    kept, hists = _band_frame(columns, col_order, width)
    if not kept:
        return "<p class='edu-sub'>暂无分段数据。</p>"

    top = ["<th rowspan='2'>分数段</th>"]
    sub: list[str] = []
    for name in col_order:
        top.append(f"<th colspan='4'>{name}</th>")
        sub.extend(f"<th class='num'>{h}</th>" for h in _METRIC_HEADERS)
    head = (
        "<tr>" + "".join(top) + "</tr><tr>" + "".join(sub) + "</tr>"
    )
    body_rows: list[str] = []
    for lo in kept:
        cells = [f"<td>{band_label(lo, width)}</td>"]
        for name in col_order:
            count, pct, cum, cum_pct = hists[name].get(lo) or _empty_metrics()
            cells.append(f"<td class='num'>{count}</td>")
            cells.append(f"<td class='num'>{pct:.2f}</td>")
            cells.append(f"<td class='num'>{cum}</td>")
            cells.append(f"<td class='num'>{cum_pct:.2f}</td>")
        body_rows.append("<tr>" + "".join(cells) + "</tr>")
    inner = (
        f"<table class='edu-table'><thead>{head}</thead>"
        f"<tbody>{''.join(body_rows)}</tbody></table>"
    )
    return f"<div class='edu-table-wrap'>{inner}</div>"


def _slice_rows(
    rows: list[dict[str, Any]],
    col_order: list[str],
    kind: str,
    score_key: str,
) -> dict[str, list[float]]:
    columns: dict[str, list[float]] = {name: [] for name in col_order}
    for row in rows:
        v = row.get(score_key)
        if v is None:
            continue
        val = float(v)
        columns["全市"].append(val)
        g = _group_key(row, kind)
        if g in columns:
            columns[g].append(val)
        elif g:
            columns.setdefault(g, []).append(val)
    return columns


def _total_section(
    rows: list[dict[str, Any]],
    *,
    kind: str,
    track: str,
) -> str:
    tracks = [track] if track else ["物理类", "历史类"]
    col_order = _column_order(rows, kind=kind)
    parts: list[str] = []
    for t in tracks:
        subset = [r for r in rows if r.get("track") == t]
        cols = _slice_rows(subset, col_order, kind, "zf6m")
        heading = f"{t.replace('类', '方向')}"
        parts.append(
            f"<h3>{heading}</h3>"
            + _band_chart_html(cols, col_order, 10, f"{heading}总分十分段人数")
            + _band_table(cols, col_order, 10)
        )
    return "".join(parts) if parts else "<p class='edu-sub'>暂无总分分段。</p>"


def _subject_section(
    rows: list[dict[str, Any]],
    *,
    kind: str,
    subject_name: str | None,
) -> str:
    col_order = _column_order(rows, kind=kind)
    wanted = [s for s in SUBJECTS if subject_name is None or s[1] == subject_name]
    parts: list[str] = []
    for key, name in wanted:
        cols = _slice_rows(rows, col_order, kind, key)
        parts.append(
            f"<h3>{name}</h3>"
            + _band_chart_html(cols, col_order, 5, f"{name}五分段人数")
            + _band_table(cols, col_order, 5)
        )
    return "".join(parts) if parts else "<p class='edu-sub'>暂无学科分段。</p>"


def build_score_band_tables(
    rows: list[dict[str, Any]],
    *,
    question: str = "",
    exam_name: str = "",
) -> dict[str, Any]:
    from src.agent.education.bureau_analysis import parse_track

    q = str(question or "")
    blocks = parse_score_band_blocks(q)
    track = parse_track(q)
    subject_name = parse_subject_filter(q)
    exam = str(exam_name or "").strip() or "本次考试"

    district_html = ""
    school_html = ""
    if "district_total" in blocks:
        district_html += "<h3>各区县总分十分段</h3>" + _total_section(
            rows, kind="district", track=track
        )
    if "district_subject" in blocks:
        district_html += "<h3>各区县学科五分段</h3>" + _subject_section(
            rows, kind="district", subject_name=subject_name
        )
    if "schooltype_total" in blocks:
        school_html += "<h3>各类校总分十分段</h3>" + _total_section(
            rows, kind="schooltype", track=track
        )
    if "schooltype_subject" in blocks:
        school_html += "<h3>各类校学科五分段</h3>" + _subject_section(
            rows, kind="schooltype", subject_name=subject_name
        )

    has_d = bool(district_html)
    has_s = bool(school_html)
    if has_d and has_s:
        primary, secondary = district_html, school_html
        p_title, s_title = "各区县分段", "各类校分段"
    elif has_s:
        primary, secondary = school_html, ""
        p_title, s_title = "各类校分段", ""
    else:
        primary, secondary = district_html or "<p class='edu-sub'>暂无分段数据。</p>", ""
        p_title, s_title = "各区县分段", ""

    n = len(rows)
    n_wl = sum(1 for r in rows if r.get("track") == "物理类")
    n_ls = sum(1 for r in rows if r.get("track") == "历史类")
    recs = [
        "口径由学生行重算，不以离线汇总表为准。",
        "十分段下限 ((分-1)//10)*10+1；五分段同理宽为 5。",
        "累计从高分往下：累计人数 = 分数≥该段下限。",
        "化生政地用转换分；市报生单独成列，不并进引领/支撑/发展/其他。",
    ]
    insight = (
        f"<p class='edu-insight-line'>{exam} 参考 {n} 人"
        f"（物理类 {n_wl}、历史类 {n_ls}）。"
        "比例分母为该列切片有效人数。</p>"
    )
    return {
        "primary": primary,
        "secondary": secondary,
        "primary_title": p_title,
        "secondary_title": s_title,
        "insight": insight,
        "recs": recs,
        "count": n,
        "kpis": [
            ("参考人数", str(n)),
            ("物理类", str(n_wl)),
            ("历史类", str(n_ls)),
        ],
    }
