"""kpi_sql 与 compute_score_stats 口径对齐。"""

from __future__ import annotations

from src.agent.education.config import EducationConfig
from src.agent.education.kpi_sql import (
    append_exam_id_predicate,
    build_kpi_aggregate_sql,
    build_primary_exam_id_sql,
    build_score_count_sql,
    kpi_row_to_stats,
)
from src.agent.education.stats import compute_score_stats


def test_kpi_row_to_stats_matches_compute_score_stats():
    cfg = EducationConfig()
    scores = [40.0, 60.0, 75.0, 90.0, 100.0, 55.0, 88.0]
    full = 100.0
    expected = compute_score_stats(scores, cfg, full)

    # 模拟聚合行（与 SQL 输出字段一致）
    seg_counts = [s["count"] for s in expected["segments"]]
    row = {
        "count": expected["count"],
        "full_score": full,
        "avg": expected["avg"],
        "median": expected["median"],
        "stdev": expected["stdev"],
        "min": expected["min"],
        "max": expected["max"],
        "pass_rate": expected["pass_rate"],
        "excellent_rate": expected["excellent_rate"],
        "good_rate": expected["good_rate"],
        "low_score_rate": expected["low_score_rate"],
        "fail_rate": expected["fail_rate"],
    }
    for i, c in enumerate(seg_counts):
        row[f"seg_{i}_count"] = c

    got = kpi_row_to_stats(row, cfg)
    assert got["count"] == expected["count"]
    assert got["avg"] == expected["avg"]
    assert got["pass_rate"] == expected["pass_rate"]
    assert got["excellent_rate"] == expected["excellent_rate"]
    assert got["segments"] == expected["segments"]
    assert got["full_score"] == expected["full_score"]


def test_build_kpi_aggregate_sql_has_no_limit_and_has_rates():
    cfg = EducationConfig()
    sql = build_kpi_aggregate_sql("WHERE sc.class LIKE '%高一(1)%'", cfg)
    assert "LIMIT" not in sql.upper().replace("WITHIN GROUP", "")
    assert "pass_rate" in sql
    assert "PERCENTILE_CONT" in sql
    assert "STDDEV_SAMP" in sql
    assert "seg_0_count" in sql


def test_build_primary_exam_and_count_sql():
    p = build_primary_exam_id_sql("WHERE sc.subject_name LIKE '%数学%'")
    assert "ORDER BY COUNT(*) DESC" in p
    assert "LIMIT 1" in p
    assert "tb_exam_batch" in p
    c = build_score_count_sql("WHERE sc.class LIKE '%1%'")
    assert "COUNT(*) AS cnt" in c
    assert "sc.score IS NOT NULL" in c


def test_append_exam_id_predicate():
    assert append_exam_id_predicate("", "42") == "WHERE sc.exam_id = '42'"
    assert "AND sc.exam_id = '7'" in append_exam_id_predicate(
        "WHERE sc.class LIKE '%1%'", "7"
    )


def test_stats_from_fetch_bundle_prefers_kpi_stats():
    from src.agent.education.config import EducationConfig
    from src.agent.education.tools import _stats_from_fetch_bundle

    cfg = EducationConfig()
    # 截断行会算出错误人数；权威 KPI 应来自 kpi_stats
    got = _stats_from_fetch_bundle(
        {
            "kpi_stats": {
                "count": 1500,
                "avg": 72.5,
                "pass_rate": 80.0,
                "full_score": 100,
            },
            "score_rows": [{"score": 99, "exam_score": 100}] * 50,
            "score_rows_incomplete": True,
        },
        cfg,
    )
    assert got is not None
    assert got["count"] == 1500
    assert got["pass_rate"] == 80.0

    # 无聚合且声明截断时拒绝用行级结果冒充 KPI
    assert (
        _stats_from_fetch_bundle(
            {
                "score_rows": [{"score": 99, "exam_score": 100}] * 50,
                "score_rows_incomplete": True,
            },
            cfg,
        )
        is None
    )
