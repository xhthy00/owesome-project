"""全市结构化诊断：物理类 / 历史类达线 + 六门总分十分段。"""

from __future__ import annotations

from typing import Any

from src.agent.education.charts import build_chart_option
from src.agent.education.report_types import ReportType, report_type_label

_RADAR_SUBJECTS: tuple[tuple[str, str, float], ...] = (
    ("yw", "语文", 150),
    ("sx", "数学", 150),
    ("yy", "英语", 150),
    ("wl", "物理", 100),
    ("hxzh", "化学", 100),
    ("swzh", "生物", 100),
    ("ls", "历史", 100),
    ("zzzh", "政治", 100),
    ("dlzh", "地理", 100),
)
_TRACKS = ("物理类", "历史类")
_FOCUS_LINES = ("特控线", "本科线")


def _fmt(v: Any) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.2f}"
    return str(v)


def _kpi_card(label: str, value: str, hint: str = "") -> str:
    hint_html = (
        f'<div class="hint" style="margin-top:6px;font-size:11.5px;line-height:1.45;'
        f'color:rgba(0,0,0,0.45)">{hint}</div>'
        if hint
        else ""
    )
    return (
        f'<div class="edu-kpi"><div class="label">{label}</div>'
        f'<div class="value">{value}</div>{hint_html}</div>'
    )


def _avg(rows: list[dict[str, Any]], key: str) -> float:
    vals: list[float] = []
    for row in rows:
        v = row.get(key)
        if v is None:
            continue
        vals.append(float(v))
    if not vals:
        return 0.0
    return round(sum(vals) / len(vals), 1)


def _band_heatmap(rows: list[dict[str, Any]]) -> str:
    from src.agent.education.score_band import DISTRICT_ORDER, band_label, band_lo, match_district

    all_los: set[int] = set()
    districts: list[str] = []
    seen_d: set[str] = set()
    for name in DISTRICT_ORDER[1:]:
        if any(match_district(str(r.get("dq") or "")) == name for r in rows):
            districts.append(name)
            seen_d.add(name)
    extra = sorted(
        match_district(str(r.get("dq") or ""))
        for r in rows
        if match_district(str(r.get("dq") or "")) not in seen_d
        and match_district(str(r.get("dq") or ""))
        and match_district(str(r.get("dq") or "")) != "全市"
    )
    districts.extend(extra)
    for r in rows:
        if r.get("zf6m") is None:
            continue
        all_los.add(band_lo(float(r["zf6m"]), 10))
    if not all_los or not districts:
        return ""
    los = sorted(all_los, reverse=True)
    cols = [band_label(lo, 10) for lo in los]
    y_labels: list[str] = []
    matrix: list[list[float]] = []
    for track in _TRACKS:
        subset = [r for r in rows if r.get("track") == track]
        if not subset:
            continue
        short = track.replace("类", "")
        for district in districts:
            slice_rows = [
                r
                for r in subset
                if match_district(str(r.get("dq") or "")) == district and r.get("zf6m") is not None
            ]
            n = len(slice_rows)
            if n == 0:
                continue
            y_labels.append(f"{district}·{short}")
            row_vals: list[float] = []
            for lo in los:
                c = sum(1 for r in slice_rows if band_lo(float(r["zf6m"]), 10) == lo)
                row_vals.append(round(100.0 * c / n, 1))
            matrix.append(row_vals)
    if not matrix:
        return ""
    return build_chart_option(
        "heatmap",
        {
            "rows": y_labels,
            "cols": cols,
            "matrix": matrix,
            "max": 100,
            "series_name": "占比%",
        },
        title="各区县总分十分段占比（物理类 / 历史类）",
    )


def _subject_radar(rows: list[dict[str, Any]]) -> str:
    series: list[dict[str, Any]] = []
    for track in _TRACKS:
        subset = [r for r in rows if r.get("track") == track]
        if not subset:
            continue
        series.append(
            {
                "name": track,
                "values": [_avg(subset, key) for key, _name, _mx in _RADAR_SUBJECTS],
            }
        )
    if not series:
        return ""
    return build_chart_option(
        "subject_radar",
        {
            "subjects": [name for _k, name, _mx in _RADAR_SUBJECTS],
            "maxes": [mx for _k, _n, mx in _RADAR_SUBJECTS],
            "series": series,
        },
        title="文理各科均分",
    )


def _reach_rate(
    indicator_rows: list[dict[str, Any]],
    *,
    track: str,
    line_name: str,
) -> tuple[int, int, float]:
    from src.agent.education.line_reach_report import _filter_rows, sum_reach

    return sum_reach(_filter_rows(indicator_rows, track=track, line_name=line_name))


def build_diagnostic_data(
    overview_rows: list[dict[str, Any]] | None = None,
    indicator_rows: list[dict[str, Any]] | None = None,
    *,
    prev_indicator_rows: list[dict[str, Any]] | None = None,
    exam_name: str = "",
    prev_exam_name: str = "",
    scope_label: str = "全市",
    question: str = "",
    subject_name: str = "",
) -> dict[str, Any]:
    """组装全市文理达线 + 总分十分段诊断模板 data。"""
    from src.agent.education.bureau_analysis import normalize_students
    from src.agent.education.line_reach_report import build_line_reach_report_data
    from src.agent.education.score_band import build_score_band_tables

    students = normalize_students(overview_rows or [])
    n_wl = sum(1 for r in students if r.get("track") == "物理类")
    n_ls = sum(1 for r in students if r.get("track") == "历史类")
    exam = (exam_name or "").strip() or "本次考试"
    scope = (scope_label or "").strip() or "全市"

    reach = build_line_reach_report_data(
        indicator_rows or [],
        prev_indicator_rows,
        exam_name=exam,
        prev_exam_name=prev_exam_name,
        scope_label=scope,
        question=question or "特控 本科",
    )
    band = build_score_band_tables(
        students,
        question="各区县总分十分段",
        exam_name=exam,
    )

    kpi_cards = [
        _kpi_card("参考人数", str(len(students))),
        _kpi_card("物理类", str(n_wl)),
        _kpi_card("历史类", str(n_ls)),
    ]
    for track in _TRACKS:
        for line_name in _FOCUS_LINES:
            _n, hit, rate = _reach_rate(indicator_rows or [], track=track, line_name=line_name)
            kpi_cards.append(_kpi_card(f"{track}{line_name}率", f"{rate:.1f}%", f"{hit} 人"))
    kpi_grid = f'<div class="edu-grid">{"".join(kpi_cards)}</div>'

    wl_avg = _avg([r for r in students if r.get("track") == "物理类"], "zf6m")
    ls_avg = _avg([r for r in students if r.get("track") == "历史类"], "zf6m")
    insight = (
        f'<p class="edu-insight-line">{exam} 全市参考 <strong>{len(students)}</strong> 人'
        f'（物理类 {n_wl}、历史类 {n_ls}）；'
        f'六门均分物理类 <strong>{_fmt(wl_avg)}</strong>、历史类 <strong>{_fmt(ls_avg)}</strong>。</p>'
    )

    recs = (
        "<ul class='edu-list'>"
        "<li>对照物理类 / 历史类特控、本科达线率，定位薄弱区县。</li>"
        "<li>结合总分十分段热力图关注高分堆积与低分段过宽的区县。</li>"
        "<li>雷达图短板学科优先安排全市教研与补差。</li>"
        "</ul>"
    )

    return {
        "REPORT_TITLE": f"{scope}{exam}考试分析".replace("全市全市", "全市"),
        "REPORT_TYPE": report_type_label(ReportType.DIAGNOSTIC_REPORT),
        "REPORT_SUBTITLE": "物理类 / 历史类 · 达线与总分十分段",
        "REPORT_TIME": "",
        "SCOPE": scope,
        "EXAM_NAME": exam,
        "SUBJECT_NAME": subject_name or "文理全科",
        "KPI_GRID": kpi_grid,
        "GENERAL_INSIGHT": insight,
        "REACH_TABLE": reach.get("DELTA_TABLE") or "<p class='edu-sub'>暂无达线指标。</p>",
        "REACH_CHART": reach.get("COMPARE_CHART") or "",
        "DISTRICT_SUMMARY": reach.get("DISTRICT_TABLE") or "",
        "BAND_TABLE": band.get("primary") or "<p class='edu-sub'>暂无总分分段。</p>",
        "BAND_HEATMAP": _band_heatmap(students),
        "SUBJECT_RADAR": _subject_radar(students),
        "SUMMARY": insight,
        "RECOMMENDATIONS": recs,
    }


__all__ = ["build_diagnostic_data"]
