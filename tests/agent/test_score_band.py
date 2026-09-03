"""局端分数段：分箱、累计、市报生隔离、区县分母、问句裁剪。"""

from __future__ import annotations

from src.agent.education.bureau_analysis import normalize_students
from src.agent.education.score_band import (
    band_lo,
    build_score_band_tables,
    match_school_type,
    parse_score_band_blocks,
)


def _stu(**kwargs):
    base = {
        "anon_stu_id": "S1",
        "xx": "A01",
        "dq": "市直",
        "bj": "01",
        "xkkm": "物化生",
        "xsxz": "在籍生",
        "xxlb": "发展",
        "zf6m": 500,
        "yw": 100,
        "hxzh": 90,
    }
    base.update(kwargs)
    return base


def test_band_lo_matches_excel():
    assert band_lo(690, 10) == 681
    assert band_lo(4, 10) == 1
    assert band_lo(130, 5) == 126
    assert band_lo(100, 5) == 96


def test_cumulative_from_high():
    rows = normalize_students(
        [
            _stu(anon_stu_id="a", zf6m=685, dq="市直"),
            _stu(anon_stu_id="b", zf6m=685, dq="市直"),
            _stu(anon_stu_id="c", zf6m=675, dq="邗江区"),
        ]
    )
    data = build_score_band_tables(
        rows, question="各区县总分十分段情况", exam_name="1月"
    )
    html = data["primary"]
    assert "681 - 690" in html
    assert "671 - 680" in html
    # 全市 681 段：2 人，累计 2；671 段：1 人，累计 3
    assert "colspan='4'>全市" in html
    assert "累计人数" in html
    assert data["secondary"] == ""


def test_shibao_not_in_lingyin():
    rows = normalize_students(
        [
            _stu(
                anon_stu_id="a",
                zf6m=685,
                xxlb="引领校",
                xsxz="市报生",
                dq="市直",
            ),
            _stu(
                anon_stu_id="b",
                zf6m=685,
                xxlb="引领校",
                xsxz="在籍生",
                dq="市直",
            ),
        ]
    )
    assert match_school_type(rows[0]) == "市报生"
    assert match_school_type(rows[1]) == "引领"
    data = build_score_band_tables(
        rows, question="各类校总分十分段情况", exam_name="1月"
    )
    html = data["primary"]
    # 引领列人数 1，市报生列人数 1；全市 2
    assert "引领" in html
    assert "市报生" in html
    assert "681 - 690" in html


def test_district_denominator_not_citywide():
    rows = normalize_students(
        [
            _stu(anon_stu_id="a", zf6m=600, dq="市直"),
            _stu(anon_stu_id="b", zf6m=600, dq="市直"),
            _stu(anon_stu_id="c", zf6m=400, dq="邗江区"),
        ]
    )
    data = build_score_band_tables(
        rows, question="各区县总分十分段情况", exam_name="1月"
    )
    html = data["primary"]
    # 市直两人同段，该列比例 100.00，不是全市 66.67
    assert "100.00" in html
    assert "591 - 600" in html


def test_chemistry_uses_hxzh():
    rows = normalize_students(
        [
            _stu(anon_stu_id="a", hx=80, hxzh=90, yw=100, xkkm="物化生"),
        ]
    )
    data = build_score_band_tables(
        rows, question="各区县化学五分段分析", exam_name="1月"
    )
    html = data["primary"]
    assert "86 - 90" in html
    assert "76 - 80" not in html
    assert "化学" in html
    assert "语文" not in html


def test_parse_blocks_crop():
    assert parse_score_band_blocks("各区县总分十分段情况") == {"district_total"}
    assert parse_score_band_blocks("各类校学科五分段分析") == {"schooltype_subject"}
    assert parse_score_band_blocks("分段统计情况分析") == {
        "district_total",
        "district_subject",
        "schooltype_total",
        "schooltype_subject",
    }
    both = parse_score_band_blocks("物理方向十分段情况")
    assert "district_total" in both
    assert "schooltype_total" in both
    assert "district_subject" in both
    q10 = "2026届高三1月各区县总分10分段统计"
    assert parse_score_band_blocks(q10) == {"district_total"}
    data = build_score_band_tables(
        normalize_students([_stu(anon_stu_id="a", zf6m=685, yw=100, yy=90)]),
        question=q10,
        exam_name="1月",
    )
    assert "各区县总分十分段" in data["primary"]
    assert "学科五分段" not in data["primary"]
    assert "英语" not in data["primary"]
    assert "语数英" not in data["primary"]


def test_shifen_and_wufen_render_bar_charts():
    rows = normalize_students([_stu(anon_stu_id="a", zf6m=685, yw=100, hxzh=90)])
    shifen = build_score_band_tables(
        rows, question="各区县总分十分段情况", exam_name="1月"
    )
    assert "edu-band-chart" in shifen["primary"]
    assert '"type": "bar"' in shifen["primary"]
    assert "681 - 690" in shifen["primary"]
    wufen = build_score_band_tables(
        rows, question="各区县化学五分段分析", exam_name="1月"
    )
    assert "edu-band-chart" in wufen["primary"]
    assert '"type": "bar"' in wufen["primary"]
    assert "86 - 90" in wufen["primary"]
