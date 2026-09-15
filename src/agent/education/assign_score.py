"""化生政地对外展示用赋分后分数。

``tb_score.score`` 对化学/生物/政治/地理存的是卷面分；
``tb_score_overview.hxzh/swzh/zzzh/dlzh`` 才是新高考赋分。
问答与报告统一走本模块的 SQL 表达式。
"""

from __future__ import annotations

from src.agent.education.schema_mapping import EXAM_NAME_SQL

ELECTIVE_CONV_COL: dict[str, str] = {
    "化学": "hxzh",
    "生物": "swzh",
    "政治": "zzzh",
    "地理": "dlzh",
}

OVERVIEW_SCORE_JOIN = (
    "LEFT JOIN tb_score_overview ov ON ov.anon_stu_id = sc.student_id "
    f"AND ov.exam_name = {EXAM_NAME_SQL}"
)


def display_score_sql(
    *,
    subject_expr: str = "sc.subject_name",
    raw_expr: str = "sc.score",
    ov_alias: str = "ov",
) -> str:
    cases = " ".join(
        f"WHEN '{name}' THEN COALESCE({ov_alias}.{col}, {raw_expr})"
        for name, col in ELECTIVE_CONV_COL.items()
    )
    return f"CASE {subject_expr} {cases} ELSE {raw_expr} END"


DISPLAY_SCORE_SQL = display_score_sql()

__all__ = [
    "DISPLAY_SCORE_SQL",
    "ELECTIVE_CONV_COL",
    "OVERVIEW_SCORE_JOIN",
    "display_score_sql",
]
