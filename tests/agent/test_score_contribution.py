"""tb_score_contribution 粒度：全市×选科×线种，无学校列。"""

from src.agent.education.score_contribution import (
    ENSURE_TABLE_SQL,
    SUBJECT_COLS,
    UNIQUE_KEYS,
    attach_focus_lines,
    folder_exam_hint,
    parse_contribution_sheet,
    resolve_exam_name,
)


def test_contribution_unique_is_exam_track_line() -> None:
    sql = " ".join(ENSURE_TABLE_SQL.split())
    assert f"UNIQUE ({', '.join(UNIQUE_KEYS)})" in sql
    assert "school_id" not in sql
    assert "district" not in sql
    assert "reach_rate" not in sql


def test_contribution_subject_cols_match_excel() -> None:
    assert SUBJECT_COLS == (
        "yw",
        "sx",
        "yy",
        "wl",
        "ls",
        "hxzh",
        "swzh",
        "zzzh",
        "dlzh",
    )
    collapsed = " ".join(ENSURE_TABLE_SQL.split())
    for col in SUBJECT_COLS:
        assert f"{col} NUMERIC(8, 2)" in collapsed


def test_folder_hint_prefers_november_over_january() -> None:
    assert folder_exam_hint("11月分析基础表") == "11月"
    assert folder_exam_hint("1月分析基础表") == "1月"
    names = ["2026届高三11月期中", "2026届高三1月期末", "2026届高三3月"]
    assert resolve_exam_name("1月", names) == "2026届高三1月期末"
    assert resolve_exam_name("11月", names) == "2026届高三11月期中"


def test_parse_contribution_sheet_skips_dash_and_labels_track() -> None:
    rows = parse_contribution_sheet(
        [
            ["理"],
            [1997, 564, 114, 118, 110, 67, "——", 70, 68, 74, 72],
            ["文"],
            [298, 564, 116, 120, 111, "——", 65, 71, 69, 73, 70],
        ]
    )
    assert len(rows) == 2
    assert rows[0]["track"] == "物理类"
    assert rows[0]["reached_count"] == 1997
    assert rows[0]["threshold"] == 564.0
    assert rows[0]["wl"] == 67.0
    assert rows[0]["ls"] is None
    assert rows[1]["track"] == "历史类"
    assert rows[1]["wl"] is None
    assert rows[1]["ls"] == 65.0


def test_attach_focus_lines_matches_threshold_then_fallback() -> None:
    parsed = [
        {"track": "物理类", "threshold": 564, "reached_count": 1997},
        {"track": "物理类", "threshold": 479, "reached_count": 7006},
        {"track": "物理类", "threshold": 419, "reached_count": 11125},
    ]
    labeled = attach_focus_lines(
        parsed,
        [
            {"track": "物理类", "line_code": "211", "line_name": "211线", "threshold": 564},
            {"track": "物理类", "line_code": "tz", "line_name": "特控线", "threshold": 479},
            {"track": "物理类", "line_code": "bk", "line_name": "本科线", "threshold": 419},
        ],
    )
    assert [(r["line_code"], r["line_name"]) for r in labeled] == [
        ("211", "211线"),
        ("tz", "特控线"),
        ("bk", "本科线"),
    ]
    fallback = attach_focus_lines(parsed, [])
    assert [r["line_code"] for r in fallback] == ["211", "tz", "bk"]
