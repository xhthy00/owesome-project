"""tb_score_indicator 纯函数：宽表分数线压行、聚合行转指标行。"""

from src.agent.education.score_indicator import (
    agg_rows_to_indicator_rows,
    bars_to_wide_row,
    exam_batch_id_from_bars,
)


def test_bars_to_wide_row_uses_existing_columns_including_typo():
    cols = ["exam_name", "exam_batch_id", "wl_score_bk", "wl_score_tz", "ls_score_bk", "wl_socre_ms"]
    row = bars_to_wide_row(
        "5月模考",
        [
            {"track": "物理类", "line_code": "bk", "threshold": 461},
            {"track": "物理类", "line_code": "ms", "threshold": 360},
            {"track": "历史类", "line_code": "bk", "threshold": 453},
            {"track": "物理类", "line_code": "985", "threshold": 600},
        ],
        cols,
        exam_batch_id=12,
    )
    assert row["exam_name"] == "5月模考"
    assert row["exam_batch_id"] == 12
    assert row["wl_score_bk"] == 461
    assert row["wl_socre_ms"] == 360
    assert row["ls_score_bk"] == 453
    assert "wl_score_985" not in row


def test_bars_to_wide_row_skips_missing_batch_id_column():
    row = bars_to_wide_row("5月模考", [], ["exam_name", "wl_score_bk"], exam_batch_id=12)
    assert row["exam_name"] == "5月模考"
    assert "exam_batch_id" not in row


def test_agg_rows_to_indicator_rows_scopes_by_track():
    bars = [
        {"line_name": "本科线", "line_code": "bk", "threshold": 461, "track": "物理类"},
        {"line_name": "本科线", "line_code": "bk", "threshold": 453, "track": "历史类"},
    ]
    rows = agg_rows_to_indicator_rows(
        [
            {
                "district": "邗江区",
                "school_name": "SCHOOL_A",
                "track": "物理类",
                "candidates": 2,
                "r0": 1,
                "r1": 0,
            },
            {
                "district": "邗江区",
                "school_name": "SCHOOL_A",
                "track": "历史类",
                "candidates": 1,
                "r0": 0,
                "r1": 1,
            },
        ],
        bars,
        exam_name="5月模考",
        exam_batch_id=12,
    )
    assert len(rows) == 2
    phys = next(r for r in rows if r["track"] == "物理类")
    assert phys["exam_name"] == "5月模考"
    assert phys["exam_batch_id"] == 12
    assert phys["reached_count"] == 1
    assert phys["threshold"] == 461
    assert phys["reach_rate"] == 50.0
    assert phys["school_id"] == "SCHOOL_A"
    hist = next(r for r in rows if r["track"] == "历史类")
    assert hist["exam_batch_id"] == 12
    assert hist["reached_count"] == 1
    assert hist["threshold"] == 453
    assert hist["reach_rate"] == 100.0


def test_exam_batch_id_from_bars_matches_exam_name():
    bars = [
        {"exam_name": "2026届高三11月期中", "exam_batch_id": 3},
        {"exam_name": "2026届高三1月期末", "exam_batch_id": 4},
    ]
    assert exam_batch_id_from_bars("2026届高三1月期末", bars) == 4
    assert exam_batch_id_from_bars("不存在", bars) is None
