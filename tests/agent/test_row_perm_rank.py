from src.datasource.service.query_permission import merge_row_predicates_into_sql

_SCHOOL_PRED = ['tb_score_overview."xx" = \'GZ_19D9D68D\'']

_RANK_SQL = """
WITH base AS (
  SELECT xx, bj, xkkm, zf6m FROM tb_score_overview
  WHERE exam_name='2026届高三1月期末' AND xsxz='在籍生' AND xxlb NOT LIKE '%其他%' AND zf6m > 0
),
sz AS (
  SELECT xx, bj, COUNT(*) AS n FROM base GROUP BY xx, bj HAVING COUNT(*) >= 10
),
pool AS (
  SELECT b.xx, b.bj, AVG(b.zf6m) AS avg_zf6m
  FROM base b JOIN sz s ON b.xx=s.xx AND b.bj=s.bj
  GROUP BY b.xx, b.bj
),
rk AS (
  SELECT xx, bj, avg_zf6m, RANK() OVER (ORDER BY avg_zf6m DESC) AS rk,
         COUNT(*) OVER() AS total FROM pool
)
SELECT * FROM rk
WHERE rk = 1 OR rk BETWEEN (
  (SELECT rk FROM rk WHERE xx LIKE '%扬州中学%' AND bj='高三(1)班') - 3
) AND (
  (SELECT rk FROM rk WHERE xx LIKE '%扬州中学%' AND bj='高三(1)班') + 3
)
"""


def test_citywide_class_rank_does_not_hang_overview_on_outer() -> None:
    merged = merge_row_predicates_into_sql(_RANK_SQL, "pg", _SCHOOL_PRED)
    compact = " ".join(merged.split())
    assert 'FROM rk WHERE tb_score_overview' not in compact
    assert 'FROM "rk" WHERE tb_score_overview' not in compact
    assert "GZ_19D9D68D" not in merged


def test_student_list_still_gets_school_pred() -> None:
    sql = (
        "SELECT xm, bj FROM tb_score_overview "
        "WHERE exam_name='2026届高三1月期末' AND xsxz='在籍生'"
    )
    merged = merge_row_predicates_into_sql(sql, "pg", _SCHOOL_PRED)
    assert "GZ_19D9D68D" in merged
    assert "tb_score_overview" in merged


def test_student_list_cte_injects_on_fact_leaf_not_outer() -> None:
    sql = """
    WITH s AS (
      SELECT xm, bj FROM tb_score_overview
      WHERE exam_name='2026届高三1月期末'
    )
    SELECT * FROM s
    """
    merged = merge_row_predicates_into_sql(sql, "pg", _SCHOOL_PRED)
    assert "GZ_19D9D68D" in merged
    compact = " ".join(merged.split())
    assert 'FROM s WHERE tb_score_overview' not in compact
    assert 'FROM "s" WHERE tb_score_overview' not in compact
