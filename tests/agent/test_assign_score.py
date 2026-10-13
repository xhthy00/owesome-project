from src.agent.education.assign_score import DISPLAY_SCORE_SQL, OVERVIEW_SCORE_JOIN
from src.agent.education.bureau_analysis import _AVG_METRICS, _ROSTER_SUBJECTS
from src.agent.education.config import EducationConfig
from src.agent.education.kpi_sql import SCORE_JOIN_FROM, build_kpi_aggregate_sql
from src.agent.education.metric_catalog import (
    METRIC_SUBJECT_STRENGTH,
    MetricSpec,
    compile_metric_sql,
)
from src.agent.education.subject_research import score_school_avg_select_sql, score_select_sql


def test_display_score_sql_uses_zh_for_electives():
    assert "WHEN '化学' THEN COALESCE(ov.hxzh, sc.score)" in DISPLAY_SCORE_SQL
    assert "WHEN '生物' THEN COALESCE(ov.swzh, sc.score)" in DISPLAY_SCORE_SQL
    assert "WHEN '政治' THEN COALESCE(ov.zzzh, sc.score)" in DISPLAY_SCORE_SQL
    assert "WHEN '地理' THEN COALESCE(ov.dlzh, sc.score)" in DISPLAY_SCORE_SQL
    assert "ELSE sc.score" in DISPLAY_SCORE_SQL
    assert "tb_score_overview" in OVERVIEW_SCORE_JOIN
    assert "tb_score_overview" in SCORE_JOIN_FROM


def test_kpi_sql_selects_assigned_score():
    sql = build_kpi_aggregate_sql("WHERE sc.subject_name = '化学'", EducationConfig())
    assert "ov.hxzh" in sql
    assert "tb_score_overview" in sql
    assert "sc.score AS score" not in sql


def test_subject_research_score_sql_uses_assigned():
    sql = score_select_sql("1月期末", "化学")
    assert "ov.hxzh" in sql
    avg = score_school_avg_select_sql("1月期末", "化学")
    assert "ov.hxzh" in avg
    assert "AVG(sc.score)" not in avg


def test_subject_strength_metric_uses_assigned():
    spec = MetricSpec(METRIC_SUBJECT_STRENGTH, ("优势学科",), ())
    sql = compile_metric_sql(spec, {"exam_name": "1月期末"})
    assert "ov.hxzh" in sql
    assert "AVG(sc.score)" not in sql


def test_bureau_display_metrics_use_assigned():
    assert ("hxzh", "化学均分") in _AVG_METRICS
    assert ("hx", "化学均分") not in _AVG_METRICS
    assert ("hxzh", "化转换均分") not in _AVG_METRICS
    assert ("hxzh", "化学") in _ROSTER_SUBJECTS
    assert ("hx", "化学") not in _ROSTER_SUBJECTS


def _raw_elective_lint(sql: str) -> list[str]:
    from src.agent.education.sql_lint import lint_edu_sql_blocks

    return [b for b in lint_edu_sql_blocks(sql) if "转换分" in b]


def test_lint_blocks_overview_raw_elective_select_and_order_by():
    blocked = _raw_elective_lint(
        "SELECT xm, hx FROM tb_score_overview "
        "WHERE xsxz='在籍生' ORDER BY hx DESC LIMIT 1"
    )
    assert blocked
    assert "hx" in blocked[0]
    assert "hxzh" in blocked[0]


def test_lint_blocks_overview_raw_elective_max():
    blocked = _raw_elective_lint(
        "SELECT MAX(hx) FROM tb_score_overview WHERE xsxz='在籍生'"
    )
    assert blocked
    assert "hx" in blocked[0]


def test_lint_allows_overview_assigned_elective():
    blocked = _raw_elective_lint(
        "SELECT xm, hxzh FROM tb_score_overview "
        "WHERE xsxz='在籍生' AND hxzh > 0 ORDER BY hxzh DESC LIMIT 1"
    )
    assert blocked == []


def test_lint_allows_rank_alias_suffix_not_raw_elective():
    """cid954：AS rk_hx / CTE hx_rk / JOIN 别名 ssw 不是卷面列 hx/sw。"""
    sql = (
        "WITH hx_rk AS ("
        " SELECT anon_stu_id, RANK() OVER (ORDER BY hxzh DESC) AS rk_hx "
        " FROM tb_score_overview WHERE xsxz='在籍生' AND hxzh > 0"
        ") "
        "SELECT t.xm, t.hxzh, sh.rk_hx "
        "FROM tb_score_overview t "
        "LEFT JOIN hx_rk sh ON t.anon_stu_id = sh.anon_stu_id "
        "LEFT JOIN hx_rk ssw ON t.anon_stu_id = ssw.anon_stu_id "
        "WHERE t.xsxz='在籍生'"
    )
    assert _raw_elective_lint(sql) == []


def test_lint_still_blocks_qualified_raw_elective():
    blocked = _raw_elective_lint(
        "SELECT t.hx FROM tb_score_overview t WHERE t.xsxz='在籍生'"
    )
    assert blocked
    assert "hx" in blocked[0]

