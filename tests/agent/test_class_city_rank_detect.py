"""点名班级+排名未写全市时，默认全市班排并分文理。"""

from src.agent.education.query_parse import (
    class_city_rank_answer_mode,
    is_class_city_rank_query,
    is_class_rank_school_scope,
    is_class_subject_city_rank_query,
    is_school_city_rank_query,
)
from src.agent.education.sql_lint import lint_edu_sql_blocks

_Q1062 = "2026届高三1月期末 邗江中学高三(2)班 排名"
_MIXED_SQL = """
WITH class_stats AS (
  SELECT xx, bj, AVG(zf6m) AS avg_zf6m
  FROM tb_score_overview
  WHERE exam_name = '2026届高三1月期末'
    AND xsxz = '在籍生'
    AND zf6m > 0
  GROUP BY xx, bj
  HAVING COUNT(*) FILTER (WHERE zf6m > 0) >= 3
)
SELECT xx, bj, avg_zf6m,
       RANK() OVER (ORDER BY avg_zf6m DESC) AS city_rank,
       COUNT(*) OVER () AS n_class
FROM class_stats
WHERE xx LIKE '%邗江中学%' AND bj LIKE '%高三(2)班%'
"""


def test_named_class_rank_defaults_citywide_track():
    assert is_class_city_rank_query(_Q1062)
    assert class_city_rank_answer_mode(_Q1062) == "track"
    assert not is_class_rank_school_scope(_Q1062)
    assert not is_school_city_rank_query(_Q1062)


def test_explicit_city_and_school_scope_unchanged():
    city = "邗江中学高三(2)班全市排名"
    school = "邗江中学高三(2)班全校排名"
    assert is_class_city_rank_query(city)
    assert class_city_rank_answer_mode(city) == "track"
    assert not is_class_rank_school_scope(city)
    assert is_class_city_rank_query(school)
    assert is_class_rank_school_scope(school)


def test_subject_rank_without_city_word():
    q = "邗江中学高三(2)班数学排名"
    assert is_class_subject_city_rank_query(q)
    assert class_city_rank_answer_mode(q) == "track"


def test_school_or_student_rank_not_class_pool():
    assert not is_class_city_rank_query("邗江中学全市排名")
    assert not is_class_city_rank_query("高三(2)班学生张三的排名")
    assert not is_class_city_rank_query("高三(2)班最好的学生是谁")


def test_lint_blocks_mixed_track_on_bare_class_rank():
    blocks = lint_edu_sql_blocks(_MIXED_SQL, question=_Q1062)
    joined = " ".join(blocks)
    assert "区分文理" in joined
    assert "整班参考人数" in joined
