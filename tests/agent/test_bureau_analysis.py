"""局端基础分析：应届过滤、均分、ABCDE、位次并列、贡献分、组合达线、脱敏名单。"""

from __future__ import annotations

from src.agent.education.bureau_analysis import (
    aggregate_assign_grade,
    aggregate_combo_reach,
    aggregate_contribution,
    aggregate_rank_buckets,
    aggregate_subject_avg,
    build_bureau_report_data,
    build_elite_roster,
    compare_school_city_avg,
    filter_students,
    format_school_city_avg_content,
    normalize_students,
    parse_track,
    school_codes_from_lookup,
    wants_enrolled_only,
)
from src.agent.education.intent_router import classify_report_intent_sync
from src.agent.education.line_reach_report import parse_focus_lines
from src.agent.education.query_parse import (
    is_assign_grade_report_query,
    is_combo_reach_report_query,
    is_elite_roster_report_query,
    is_rank_bucket_report_query,
    is_score_band_report_query,
    is_subject_avg_report_query,
)
from src.agent.education.report_types import ReportType


def _stu(**kwargs):
    base = {
        "anon_stu_id": "S1",
        "xx": "A01",
        "dq": "市直",
        "bj": "01",
        "xkkm": "物化生",
        "xsxz": "在籍生",
        "zf3m": 300,
        "zf4m": 400,
        "zf6m": 500,
        "yw": 100,
        "sx": 100,
        "yy": 100,
        "wl": 70,
        "hx": 80,
        "hxdj": "A",
        "hxzh": 90,
        "sw": 70,
        "swdj": "B",
        "swzh": 80,
    }
    base.update(kwargs)
    return base


def test_enrolled_excludes_shibao():
    rows = normalize_students(
        [
            _stu(anon_stu_id="a", xsxz="在籍生"),
            _stu(anon_stu_id="b", xsxz="市报生"),
            _stu(anon_stu_id="c", xsxz=""),
        ]
    )
    assert len(filter_students(rows, enrolled_only=False)) == 3
    assert len(filter_students(rows, enrolled_only=True)) == 1
    assert wants_enrolled_only("应届生达线情况") is True


def test_subject_avg_city_and_rank():
    rows = normalize_students(
        [
            _stu(anon_stu_id="a", xx="A01", dq="市直", zf6m=600),
            _stu(anon_stu_id="b", xx="A02", dq="邗江", zf6m=400),
        ]
    )
    out = aggregate_subject_avg(rows, "school", with_rank=True)
    assert out[0]["name"] == "扬州市"
    assert out[0]["zf6m"] == 500
    assert out[1]["name"] == "A01"
    assert out[1]["rank"] == 1


def test_subject_avg_excludes_unselected_zero():
    rows = [
        {
            "xx": "A01",
            "dq": "市直",
            "bj": "01",
            "yw": 100,
            "ls": 0,
            "wl": 70,
            "zz": 0,
            "dl": 0,
            "zf6m": 500,
        },
        {
            "xx": "A01",
            "dq": "市直",
            "bj": "01",
            "yw": 110,
            "ls": 90,
            "wl": 0,
            "zz": 80,
            "dl": 70,
            "zf6m": 480,
        },
    ]
    city = aggregate_subject_avg(rows, "district")[0]
    assert city["ls"] == 90
    assert city["ls_n"] == 1
    assert city["wl"] == 70
    assert city["zz"] == 80
    assert city["dl"] == 70
    assert city["yw"] == 105


def test_assign_grade_a_rate():
    rows = normalize_students(
        [
            _stu(anon_stu_id="a", hxdj="A", hx=80),
            _stu(anon_stu_id="b", hxdj="B", hx=70),
            _stu(anon_stu_id="c", hxdj="A", hx=90),
            _stu(anon_stu_id="d", hxdj="C", hx=50),
        ]
    )
    city = aggregate_assign_grade(rows, "district")[0]
    assert city["化学"]["n"] == 4
    assert city["化学"]["A"] == 2
    assert city["化学"]["A_rate"] == 50.0


def test_rank_bucket_includes_ties():
    rows = normalize_students(
        [
            _stu(anon_stu_id="a", zf6m=100, xkkm="物化生"),
            _stu(anon_stu_id="b", zf6m=99, xkkm="物化生"),
            _stu(anon_stu_id="c", zf6m=98, xkkm="物化生"),
            _stu(anon_stu_id="d", zf6m=98, xkkm="物化生"),
            _stu(anon_stu_id="e", zf6m=90, xkkm="物化生"),
        ]
    )
    payload = aggregate_rank_buckets(rows, track="物理类")
    assert payload["cuts"][10] == 90
    assert payload["districts"][0][10] == 5


def test_contribution_uses_cutoff_sitters():
    rows = normalize_students(
        [
            _stu(anon_stu_id="a", zf6m=600, yw=120, xkkm="物化生"),
            _stu(anon_stu_id="b", zf6m=500, yw=90, xkkm="物化生"),
            _stu(anon_stu_id="c", zf6m=500, yw=110, xkkm="物化生"),
        ]
    )
    bars = [{"track": "物理类", "line_name": "特控线", "threshold": 500}]
    out = aggregate_contribution(rows, bars)
    assert out[0]["reached"] == 3
    assert out[0]["yw"] == 100


def test_combo_reach_wuhs():
    rows = normalize_students(
        [
            _stu(anon_stu_id="a", xkkm="物化生", zf6m=520),
            _stu(anon_stu_id="b", xkkm="物化生", zf6m=400),
            _stu(anon_stu_id="c", xkkm="物化地", zf6m=520),
        ]
    )
    bars = [
        {"track": "物理类", "line_name": "特控线", "threshold": 500},
        {"track": "物理类", "line_name": "本科线", "threshold": 430},
    ]
    city = aggregate_combo_reach(rows, bars)[0]
    assert city["物化生"]["n"] == 2
    assert city["物化生"]["tz"] == 1
    assert city["物化生"]["bk"] == 1


def test_elite_roster_no_pii():
    rows = normalize_students(
        [
            _stu(anon_stu_id="a", zf6m=680, yw=120),
            _stu(anon_stu_id="b", zf6m=670, yw=110),
        ]
    )
    roster = build_elite_roster(rows, track="物理类", top_n=100)
    blob = str(roster)
    assert "xm" not in blob.lower()
    assert roster[0]["anon_stu_id"] == "a"
    assert roster[0]["yw_rank"] == 1


def test_elite_roster_reveals_name_when_anonymize_off():
    from src.agent.education.bureau_analysis import overview_select_sql
    from src.agent.education.privacy_mode import (
        clear_anonymize_display_cache,
        set_anonymize_display_cached,
    )

    set_anonymize_display_cached(False)
    try:
        rows = normalize_students(
            [_stu(anon_stu_id="a", zf6m=680, yw=120, xm="张三", xh="2024001")]
        )
        roster = build_elite_roster(rows, track="物理类", top_n=100)
        assert roster[0]["xm"] == "张三"
        assert roster[0]["xh"] == "2024001"
        data = build_bureau_report_data(
            "elite_roster",
            [_stu(anon_stu_id="a", zf6m=680, yw=120, xm="张三", xh="2024001")],
            question="理前100名单情况",
        )
        html = data["PRIMARY_TABLE"]
        assert "张三" in html
        assert "2024001" in html
        assert "姓名" in html
        sql = overview_select_sql("1月期末")
        assert "xm" in sql
        assert "xh" in sql
    finally:
        clear_anonymize_display_cache()


def test_build_report_html_tables():
    data = build_bureau_report_data(
        "subject_avg",
        [
            _stu(anon_stu_id="a", xkkm="物化生", zf6m=600, dq="市直"),
            _stu(anon_stu_id="b", xx="A02", dq="邗江", xkkm="史政地", zf6m=400, ls=70),
        ],
        exam_name="1月期末",
        question="各地区均分情况分析",
    )
    html = data["PRIMARY_TABLE"]
    assert "三门总均分" in html
    assert "六门排名" in html
    assert "全员" in html
    assert "理科（物理类）" in html
    assert "文科（历史类）" in html
    assert data["REPORT_TYPE"] == "均分情况分析"
    assert data["_stats"]["count"] == 2
    only_wl = build_bureau_report_data(
        "subject_avg",
        [
            _stu(anon_stu_id="a", xkkm="物化生", zf6m=600),
            _stu(anon_stu_id="b", xkkm="史政地", zf6m=400, ls=70),
        ],
        exam_name="1月期末",
        question="物理方向均分情况分析",
    )
    assert "理科（物理类）" in only_wl["PRIMARY_TABLE"]
    assert "文科（历史类）" not in only_wl["PRIMARY_TABLE"]
    assert only_wl["_stats"]["count"] == 1


def test_routing_bureau_reports():
    assert is_subject_avg_report_query("各地区均分情况分析")
    assert is_assign_grade_report_query("各学校选考学科ABCDE情况")
    assert is_rank_bucket_report_query("高三物理方向位次情况")
    assert is_combo_reach_report_query("理科各选择组合达线情况")
    assert is_elite_roster_report_query("理前100名单情况")
    assert is_score_band_report_query("各地区总分十分段情况分析")
    assert is_score_band_report_query("请生成各区县与各类校总分十分段、学科五分段统计")
    assert is_score_band_report_query("2026届高三1月各区县总分10分段统计")
    assert not is_score_band_report_query("高三(1)班分数段情况")
    assert classify_report_intent_sync("各地区均分情况分析").report_type == ReportType.SUBJECT_AVG
    assert classify_report_intent_sync("各地区总分十分段情况分析").report_type == ReportType.SCORE_BAND
    assert classify_report_intent_sync("2026届高三1月各区县总分10分段统计").report_type == ReportType.SCORE_BAND
    assert classify_report_intent_sync("2026届高三1月扬州中学总分10分段分布情况").needs_report is False
    assert classify_report_intent_sync(
        "2026届高三1月扬州中学物理类均分与全市的比较分析"
    ).needs_report is False
    assert classify_report_intent_sync("邗江物理类本科线达线人数").needs_report is False
    assert classify_report_intent_sync("邗江物理类600分以上多少人").needs_report is False


def test_score_band_report_html():
    data = build_bureau_report_data(
        "score_band",
        [
            _stu(anon_stu_id="a", xkkm="物化生", zf6m=685, dq="市直", xxlb="引领"),
            _stu(
                anon_stu_id="b",
                xx="A02",
                dq="邗江区",
                xkkm="史政地",
                zf6m=400,
                ls=70,
            ),
        ],
        exam_name="1月期末",
        question="各地区总分十分段情况分析",
    )
    html = data["PRIMARY_TABLE"]
    assert "物理方向" in html
    assert "681 - 690" in html
    assert "edu-band-chart" in html
    assert '"type": "bar"' in html
    assert data["REPORT_TYPE"] == "分段统计"
    assert data["SECONDARY_TABLE"] == ""
    assert "各区县分段" in data["PRIMARY_TITLE"]


def test_tezhao_alias_and_parse_track():
    assert parse_focus_lines("特招线达线情况") == ["特控线"]
    assert parse_track("物理方向均分情况") == "物理类"
    bars = [{"track": "物理类", "line_name": "特招线", "threshold": 500}]
    rows = normalize_students([_stu(anon_stu_id="a", zf6m=500, yw=90, xkkm="物化生")])
    out = aggregate_contribution(rows, bars)
    assert out[0]["line_name"] == "特控线"
    assert out[0]["reached"] == 1


def test_compare_school_city_avg_physics_track():
    students = [
        _stu(anon_stu_id="a", xx="A01", xkkm="物化生", zf6m=600),
        _stu(anon_stu_id="b", xx="A01", xkkm="史政地", zf6m=400, ls=70),
        _stu(anon_stu_id="c", xx="A02", xkkm="物化生", zf6m=500),
    ]
    out = compare_school_city_avg(
        students,
        school_name="扬州中学",
        school_codes=["A01"],
        track="物理类",
        exam_name="2026届高三1月期末",
    )
    assert out["school_matched"] is True
    assert out["school_n"] == 1
    assert out["city_n"] == 2
    assert out["school_avg"] == 600
    assert out["city_avg"] == 550
    assert out["delta"] == 50
    assert [r["scope"] for r in out["table"]] == ["扬州中学", "全市"]
    text = format_school_city_avg_content(out)
    assert "600" in text and "550" in text
    assert "考试名称字段为空" not in text


def test_compare_school_city_avg_unmatched_school_not_empty_exam():
    students = [
        _stu(anon_stu_id="a", xx="A02", xkkm="物化生", zf6m=500),
        _stu(anon_stu_id="b", xx="A03", xkkm="物化生", zf6m=400),
    ]
    out = compare_school_city_avg(
        students,
        school_name="扬州中学",
        school_codes=["A01"],
        track="物理类",
        exam_name="2026届高三1月期末",
    )
    assert out["school_matched"] is False
    assert out["unmatched_reason"] == "school_not_aligned"
    assert out["city_n"] == 2
    assert out["city_avg"] == 450
    text = format_school_city_avg_content(out)
    assert "对齐" in text
    assert "全市" in text
    assert "考试名称字段为空" not in text
    assert "未包含该考试批次" not in text
    assert school_codes_from_lookup([{"id": "A01", "name": "A01", "s_name": "扬州中学"}]) == ["A01"]
