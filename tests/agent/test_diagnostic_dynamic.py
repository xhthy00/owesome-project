"""全市结构化诊断：物理类/历史类达线、总分十分段、热力图、雷达图。"""

from __future__ import annotations

import json

from src.agent.education.diagnostic_report import build_diagnostic_data


def _ov(
    *,
    track_xkkm: str,
    dq: str,
    zf6m: float,
    yw: float = 110,
    sx: float = 100,
    yy: float = 105,
    wl: float | None = 70,
    hxzh: float | None = 65,
    swzh: float | None = 68,
    ls: float | None = None,
    zzzh: float | None = None,
    dlzh: float | None = None,
    xx: str = "GZ_A",
) -> dict:
    return {
        "xkkm": track_xkkm,
        "dq": dq,
        "xx": xx,
        "zf6m": zf6m,
        "yw": yw,
        "sx": sx,
        "yy": yy,
        "wl": wl,
        "hxzh": hxzh,
        "swzh": swzh,
        "ls": ls,
        "zzzh": zzzh,
        "dlzh": dlzh,
    }


def _ind(
    *,
    track: str,
    district: str,
    school_id: str,
    line_name: str,
    candidates: int,
    reached_count: int,
) -> dict:
    return {
        "exam_name": "2026届高三1月期末",
        "track": track,
        "district": district,
        "school_id": school_id,
        "school_name": school_id,
        "line_name": line_name,
        "candidates": candidates,
        "reached_count": reached_count,
        "reach_rate": round(100.0 * reached_count / candidates, 2) if candidates else 0,
    }


def _sample() -> tuple[list[dict], list[dict]]:
    overview = [
        _ov(track_xkkm="物理", dq="市直", zf6m=655, xx="GZ_1"),
        _ov(track_xkkm="物理", dq="市直", zf6m=612, xx="GZ_1"),
        _ov(track_xkkm="物理", dq="邗江区", zf6m=501, xx="GZ_2"),
        _ov(track_xkkm="历史", dq="邗江区", zf6m=548, xx="GZ_3", wl=None, hxzh=None, swzh=None, ls=72, zzzh=70, dlzh=68),
        _ov(track_xkkm="历史", dq="邗江区", zf6m=490, xx="GZ_3", wl=None, hxzh=None, swzh=None, ls=60, zzzh=58, dlzh=55),
    ]
    indicator = [
        _ind(track="物理类", district="市直", school_id="GZ_1", line_name="特控线", candidates=2, reached_count=2),
        _ind(track="物理类", district="邗江区", school_id="GZ_2", line_name="特控线", candidates=1, reached_count=0),
        _ind(track="物理类", district="市直", school_id="GZ_1", line_name="本科线", candidates=2, reached_count=2),
        _ind(track="物理类", district="邗江区", school_id="GZ_2", line_name="本科线", candidates=1, reached_count=1),
        _ind(track="历史类", district="邗江区", school_id="GZ_3", line_name="特控线", candidates=2, reached_count=1),
        _ind(track="历史类", district="邗江区", school_id="GZ_3", line_name="本科线", candidates=2, reached_count=2),
    ]
    return overview, indicator


def test_diagnostic_focuses_track_reach_and_total_bands():
    overview, indicator = _sample()
    data = build_diagnostic_data(
        overview_rows=overview,
        indicator_rows=indicator,
        exam_name="2026届高三1月期末",
        scope_label="全市",
    )
    kpi = data["KPI_GRID"]
    assert "物理类" in kpi
    assert "历史类" in kpi
    reach = data["REACH_TABLE"]
    assert "特控" in reach
    assert "本科" in reach
    assert "物理类" in reach
    bands = data["BAND_TABLE"]
    assert "物理方向" in bands or "物理类" in bands
    assert "历史方向" in bands or "历史类" in bands
    assert "分数段" in bands
    assert "edu-band-chart" in bands
    assert '"type": "bar"' in bands
    assert "退步生" not in (data.get("AT_RISK_SUMMARY") or "")
    assert "一般性 → 特殊性 → 动态性" not in (data.get("REPORT_SUBTITLE") or "")


def test_diagnostic_band_heatmap_is_district_by_shifen():
    overview, indicator = _sample()
    data = build_diagnostic_data(
        overview_rows=overview,
        indicator_rows=indicator,
        exam_name="2026届高三1月期末",
    )
    raw = data["BAND_HEATMAP"]
    assert raw
    opt = json.loads(raw)
    assert opt["series"][0]["type"] == "heatmap"
    y = opt["yAxis"]["data"]
    assert any("市直" in str(v) for v in y)
    x = opt["xAxis"]["data"]
    assert any("-" in str(v) or str(v).isdigit() for v in x)


def test_diagnostic_radar_has_physics_and_history_series():
    overview, indicator = _sample()
    data = build_diagnostic_data(
        overview_rows=overview,
        indicator_rows=indicator,
        exam_name="2026届高三1月期末",
    )
    raw = data["SUBJECT_RADAR"]
    assert raw
    opt = json.loads(raw)
    assert opt["series"][0]["type"] == "radar"
    names = [d.get("name") for d in opt["series"][0]["data"]]
    assert "物理类" in names
    assert "历史类" in names
    indicators = [i["name"] for i in opt["radar"]["indicator"]]
    assert "语文" in indicators
    assert "数学" in indicators
