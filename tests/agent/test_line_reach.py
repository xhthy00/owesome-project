"""预测线达线：总分、达线判定、区县聚合纯函数。"""

from __future__ import annotations

from datasource.service.edu_permission import EduScope
from src.agent.education.line_reach import (
    aggregate_district_line_reach,
    build_line_reach_payload,
    can_access_line_reach,
    filter_students_by_scope,
    normalize_fraction_bars,
    reached_lines,
    student_total,
)


def _bars() -> list[dict]:
    return [
        {"line_name": "特控线", "yxfs": 480, "exam_name": "5月模考", "track": "物理类"},
        {"pc": "本科线", "fs": 400, "exam_name": "5月模考", "xkfx": "物理类"},
    ]


def _overview_rows() -> list[dict]:
    return [
        {"student_id": "s1", "exam_name": "5月模考", "zf": 500, "wl": 80},
        {"student_id": "s2", "exam_name": "5月模考", "zf": 410, "wl": 70},
        {"student_id": "s3", "exam_name": "5月模考", "zf": 390, "wl": 60},
        {"student_id": "s4", "exam_name": "5月模考", "zf": 480, "wl": 90},
    ]


def _score_scope() -> list[dict]:
    return [
        {"student_id": "s1", "school_id": "sch-a", "class": "1班"},
        {"student_id": "s2", "school_id": "sch-a", "class": "1班"},
        {"student_id": "s3", "school_id": "sch-b", "class": "2班"},
        {"student_id": "s4", "school_id": "sch-c", "class": "3班"},
    ]


def _schools() -> list[dict]:
    return [
        {"id": "sch-a", "district": "邗江区", "name": "SCHOOL_A"},
        {"id": "sch-b", "district": "广陵区", "name": "SCHOOL_B"},
        {"id": "sch-c", "district": "江都区", "name": "SCHOOL_C"},
    ]


def test_select_known_columns_picks_aliases():
    from src.agent.education.line_reach import BAR_COL_GROUPS, select_known_columns

    cols = select_known_columns(
        ["id", "xm", "exam_name", "pc", "yxfs", "extra"],
        *BAR_COL_GROUPS,
    )
    assert cols == ["pc", "yxfs", "exam_name"]


def test_student_total_prefers_zf_then_subjects():
    assert student_total({"zf": 500, "yw": 1}) == 500.0
    assert student_total({"zf6m": 368, "yw": 1}) == 368.0
    assert student_total({"yw": 100, "sx": 90, "yy": 80, "wl": 70}) == 340.0
    assert student_total({"总分": "410"}) == 410.0
    assert student_total({}) is None


def test_normalize_wide_fraction_bar_unpivot():
    bars = normalize_fraction_bars(
        [
            {
                "exam_name": "2026届高三5月模拟",
                "wl_score_bk": 461,
                "wl_score_tz": 518,
                "ls_score_bk": 453,
                "ls_score_tz": 521,
                "wl_socre_ms": 360,
            }
        ]
    )
    assert {b["exam_name"] for b in bars} == {"2026届高三5月模拟"}
    assert {b["track"] for b in bars} == {"物理类", "历史类"}
    phys_bk = next(b for b in bars if b["track"] == "物理类" and b["line_name"] == "本科线")
    assert phys_bk["threshold"] == 461
    assert phys_bk["line_code"] == "bk"
    assert any(b["line_name"] == "特控线" and b["track"] == "物理类" for b in bars)
    assert any(b["line_name"] == "美术线" and b["threshold"] == 360 for b in bars)


def test_normalize_fraction_bar_tezhao_alias():
    bars = normalize_fraction_bars(
        [{"line_name": "特招线", "threshold": 518, "track": "物理类"}]
    )
    assert bars[0]["line_name"] == "特控线"
    assert bars[0]["line_code"] == "tz"


def test_meta_from_wide_bars_lists_exam():
    payload = build_line_reach_payload(
        [
            {
                "exam_name": "2026届高三5月模拟",
                "wl_score_bk": 461,
                "wl_score_tz": 518,
                "ls_score_bk": 453,
                "ls_score_tz": 521,
            }
        ],
        [],
        scope=EduScope(edu_role="bureau_admin"),
    )
    assert payload["accessible"] is True
    assert "2026届高三5月模拟" in payload["exams"]
    assert set(payload["tracks"]) == {"物理类", "历史类"}
    assert {x["line_name"] for x in payload["lines"]} >= {"特控线", "本科线"}


def test_overview_aliases_zf6m_dq_xkkm():
    payload = build_line_reach_payload(
        [
            {
                "exam_name": "2026届高三5月模拟",
                "wl_score_bk": 400,
                "wl_score_tz": 500,
            }
        ],
        [
            {
                "anon_stu_id": "GZ1",
                "exam_name": "2026届高三5月模拟",
                "zf6m": 510,
                "xkkm": "物化生",
                "dq": "邗江",
                "xx": "C12",
                "bj": "高三(1)班",
                "wl": 80,
                "ls": 0,
            }
        ],
        scope=EduScope(edu_role="bureau_admin"),
    )
    assert payload["kpis"]["candidates"] == 1
    assert payload["districts"][0]["district"] == "邗江"
    by_line = {x["line_name"]: x for x in payload["kpis"]["by_line"]}
    assert by_line["特控线"]["reached"] == 1
    assert by_line["本科线"]["reached"] == 1


def test_payload_from_school_agg():
    from src.agent.education.line_reach import payload_from_school_agg

    bars = [
        {"line_name": "特控线", "threshold": 480},
        {"line_name": "本科线", "threshold": 400},
    ]
    payload = payload_from_school_agg(
        [
            {"district": "邗江区", "school_name": "SCHOOL_A", "candidates": 2, "r0": 1, "r1": 2},
            {"district": "广陵区", "school_name": "SCHOOL_B", "candidates": 1, "r0": 0, "r1": 0},
        ],
        bars,
        exam_name="5月模考",
        track="物理类",
        exams=["5月模考"],
        tracks=["物理类"],
    )
    assert payload["kpis"]["candidates"] == 3
    by_line = {x["line_name"]: x for x in payload["kpis"]["by_line"]}
    assert by_line["特控线"]["reached"] == 1
    assert by_line["本科线"]["reached"] == 2
    assert payload["districts"][0]["district"] == "广陵区"


def test_payload_from_school_agg_merges_track_rows():
    from src.agent.education.line_reach import payload_from_school_agg

    bars = [
        {"line_name": "本科线", "threshold": 461, "track": "物理类"},
        {"line_name": "本科线", "threshold": 453, "track": "历史类"},
    ]
    payload = payload_from_school_agg(
        [
            {
                "district": "邗江区",
                "school_name": "A",
                "track": "物理类",
                "candidates": 6,
                "r0": 4,
                "r1": 6,
            },
            {
                "district": "邗江区",
                "school_name": "A",
                "track": "历史类",
                "candidates": 4,
                "r0": 3,
                "r1": 2,
            },
        ],
        bars,
        exam_name="5月模考",
        track="",
    )
    assert payload["kpis"]["candidates"] == 10
    by_line = payload["kpis"]["by_line"]
    assert len(by_line) == 1
    assert by_line[0]["reached"] == 6
    assert payload["districts"][0]["schools"][0]["school_name"] == "A"
    assert payload["districts"][0]["schools"][0]["candidates"] == 10


def test_reached_lines_threshold():
    bars = normalize_fraction_bars(_bars())
    assert reached_lines(500, bars) == ["特控线", "本科线"]
    assert reached_lines(410, bars) == ["本科线"]
    assert reached_lines(390, bars) == []
    assert reached_lines(480, bars) == ["特控线", "本科线"]


def test_can_access_line_reach_hides_student():
    assert can_access_line_reach(EduScope(edu_role="student")) is False
    assert can_access_line_reach(EduScope(edu_role="bureau_admin")) is True
    assert can_access_line_reach(EduScope(edu_role="school_admin")) is True
    assert can_access_line_reach(EduScope(edu_role="teacher")) is True
    assert can_access_line_reach(EduScope(edu_role="")) is True


def test_filter_students_by_scope_school_and_class():
    students = [
        {"student_id": "s1", "school_id": "sch-a", "class_name": "1班"},
        {"student_id": "s2", "school_id": "sch-a", "class_name": "2班"},
        {"student_id": "s3", "school_id": "sch-b", "class_name": "1班"},
    ]
    school = filter_students_by_scope(
        students, EduScope(edu_role="school_admin", school_id="sch-a")
    )
    assert [s["student_id"] for s in school] == ["s1", "s2"]
    teacher = filter_students_by_scope(
        students,
        EduScope(edu_role="teacher", school_id="sch-a", class_names=["1班"]),
    )
    assert [s["student_id"] for s in teacher] == ["s1"]


def test_aggregate_district_line_reach_counts_and_rates():
    payload = build_line_reach_payload(
        _bars(),
        _overview_rows(),
        _score_scope(),
        _schools(),
        exam_name="5月模考",
        track="物理类",
        scope=EduScope(edu_role="bureau_admin"),
    )
    assert payload["accessible"] is True
    assert payload["kpis"]["candidates"] == 4
    by_line = {x["line_name"]: x for x in payload["kpis"]["by_line"]}
    assert by_line["特控线"]["reached"] == 2
    assert by_line["特控线"]["rate"] == 50.0
    assert by_line["本科线"]["reached"] == 3
    assert by_line["本科线"]["rate"] == 75.0

    districts = {d["district"]: d for d in payload["districts"]}
    hanjiang = districts["邗江区"]
    assert hanjiang["candidates"] == 2
    hj = {x["line_name"]: x for x in hanjiang["by_line"]}
    assert hj["特控线"]["reached"] == 1
    assert hj["特控线"]["rate"] == 50.0
    assert hj["本科线"]["reached"] == 2
    assert hj["本科线"]["rate"] == 100.0
    assert districts["广陵区"]["candidates"] == 1
    gl = {x["line_name"]: x for x in districts["广陵区"]["by_line"]}
    assert gl["特控线"]["reached"] == 0
    assert gl["本科线"]["reached"] == 0
    assert districts["江都区"]["candidates"] == 1
    assert hanjiang["schools"][0]["school_name"] == "SCHOOL_A"


def test_build_payload_student_inaccessible():
    payload = build_line_reach_payload(
        _bars(),
        _overview_rows(),
        _score_scope(),
        _schools(),
        scope=EduScope(edu_role="student"),
    )
    assert payload["accessible"] is False
    assert payload["kpis"]["candidates"] == 0
    assert payload["districts"] == []


def test_aggregate_empty_students():
    got = aggregate_district_line_reach([], normalize_fraction_bars(_bars()))
    assert got["kpis"]["candidates"] == 0
    assert got["districts"] == []
    assert got["kpis"]["by_line"][0]["rate"] == 0.0


def test_citywide_merges_physics_and_history_lines():
    payload = build_line_reach_payload(
        [
            {
                "exam_name": "5月模考",
                "wl_score_bk": 461,
                "wl_score_tz": 518,
                "ls_score_bk": 453,
                "ls_score_tz": 521,
            }
        ],
        [
            {
                "anon_stu_id": "p1",
                "exam_name": "5月模考",
                "zf6m": 470,
                "xkkm": "物化生",
                "dq": "邗江",
                "xx": "A",
            },
            {
                "anon_stu_id": "h1",
                "exam_name": "5月模考",
                "zf6m": 460,
                "xkkm": "史政地",
                "dq": "邗江",
                "xx": "A",
            },
        ],
        scope=EduScope(edu_role="bureau_admin"),
        exam_name="5月模考",
    )
    assert payload["kpis"]["candidates"] == 2
    by_line = {x["line_name"]: x for x in payload["kpis"]["by_line"]}
    assert set(by_line) == {"特控线", "本科线"}
    assert by_line["本科线"]["reached"] == 2
    assert by_line["特控线"]["reached"] == 0
    assert by_line["本科线"]["rate"] == 100.0
    note = by_line["本科线"].get("threshold_note") or ""
    assert "物理" in note and "历史" in note


def test_payload_from_school_agg_citywide_sums_tracks():
    from src.agent.education.line_reach import payload_from_school_agg

    payload = payload_from_school_agg(
        [
            {
                "district": "邗江区",
                "school_name": "A",
                "candidates": 10,
                "r0": 6,
                "r1": 3,
            }
        ],
        [
            {"line_name": "本科线", "threshold": 461, "track": "物理类"},
            {"line_name": "本科线", "threshold": 453, "track": "历史类"},
        ],
        exam_name="5月模考",
        track="",
    )
    by_line = payload["kpis"]["by_line"]
    assert len(by_line) == 1
    assert by_line[0]["line_name"] == "本科线"
    assert by_line[0]["reached"] == 9
    assert by_line[0]["rate"] == 90.0


def test_filter_track_keeps_short_line_label():
    from src.agent.education.line_reach import filter_fraction_bars

    bars = filter_fraction_bars(
        normalize_fraction_bars(
            [
                {
                    "exam_name": "5月模考",
                    "wl_score_bk": 461,
                    "ls_score_bk": 453,
                }
            ]
        ),
        exam_name="5月模考",
        track="历史类",
    )
    payload = aggregate_district_line_reach([], bars, exam_name="5月模考", track="历史类")
    assert len(payload["lines"]) == 1
    assert payload["lines"][0]["line_name"] == "本科线"
    assert payload["lines"][0]["label"] == "本科线"
    assert payload["lines"][0]["track"] == "历史类"
    assert payload["lines"][0]["threshold"] == 453
