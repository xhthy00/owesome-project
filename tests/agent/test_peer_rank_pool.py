"""校长/教师跨校排名默认同层次；问全市仍是该类。"""

from __future__ import annotations

from src.agent.education.query_frame import push_constraints, reset_constraints
from src.agent.education.query_parse import (
    bound_school_type_from_edu,
    format_scope_constraints,
    is_school_city_rank_query,
    peer_rank_type_required,
    rank_pool_label,
    rank_pool_school_type,
    wants_unrestricted_school_type_rank,
)
from src.agent.education.sql_lint import lint_edu_sql_blocks
from src.agent.education.subject_strength import format_subject_strength_verdict

_PRINCIPAL = {"edu_role": "school_admin", "school_name": "扬州中学", "school_type": "引领"}
_TEACHER = {"edu_role": "teacher", "school_name": "新华中学", "xxlb": "支撑"}
_BUREAU = {"edu_role": "bureau_admin", "school_name": ""}
_CLASS_Q = "扬州中学高三(1)班全市排名"
_STRENGTH_Q = "扬州中学优势学科"
_SCHOOL_Q = "扬州中学全市排名"

_CLASS_RANK_SQL = """
SELECT xx, bj, AVG(zf6m) AS avg_zf6m,
       RANK() OVER (ORDER BY AVG(zf6m) DESC) AS rk,
       COUNT(*) OVER() AS n
FROM tb_score_overview
WHERE exam_name='2026届高三1月期末' AND xsxz='在籍生' AND xxlb NOT LIKE '%其他%'
GROUP BY xx, bj
HAVING COUNT(*) >= 10
"""

_STRENGTH_SQL = """
SELECT xx, AVG(yw) FILTER (WHERE yw > 0) AS avg_yw,
       RANK() OVER (ORDER BY AVG(yw) FILTER (WHERE yw > 0) DESC) AS rk,
       COUNT(*) OVER() AS n
FROM tb_score_overview
WHERE exam_name='2026届高三1月期末' AND xsxz='在籍生' AND xxlb NOT LIKE '%其他%'
GROUP BY xx
"""


def _type_lint(sql: str, question: str) -> list[str]:
    return [
        b
        for b in lint_edu_sql_blocks(sql, question=question)
        if "默认同层次" in b or "引领/支撑/发展" in b
    ]


def test_citywide_wording_still_peer_tier_for_principal():
    assert rank_pool_school_type(_CLASS_Q, _PRINCIPAL) == "引领"
    assert rank_pool_school_type(_SCHOOL_Q, _PRINCIPAL) == "引领"
    assert rank_pool_school_type(_STRENGTH_Q, _PRINCIPAL) == "引领"
    assert peer_rank_type_required(_CLASS_Q, _PRINCIPAL)
    assert peer_rank_type_required("扬州中学全市排名", _PRINCIPAL)
    assert rank_pool_label(_CLASS_Q, _PRINCIPAL) == "引领校"


def test_teacher_uses_bound_type():
    q = "新华中学高三(1)班全市排名"
    assert rank_pool_school_type(q, _TEACHER) == "支撑"
    assert peer_rank_type_required(q, _TEACHER)


def test_bureau_and_admin_stay_citywide():
    assert rank_pool_school_type(_CLASS_Q, _BUREAU) is None
    assert not peer_rank_type_required(_CLASS_Q, _BUREAU)
    assert rank_pool_school_type(_CLASS_Q, {"edu_role": "admin"}) is None
    assert rank_pool_label(_CLASS_Q, _BUREAU) == "全市"


def test_named_other_type_keeps_bound_pool():
    q = "扬州中学高三(1)班在发展校中的全市排名"
    assert rank_pool_school_type(q, _PRINCIPAL) == "引领"
    assert rank_pool_school_type("高三(1)班在引领校中的全市排名", _PRINCIPAL) == "引领"


def test_unrestricted_and_school_scope_escape():
    assert wants_unrestricted_school_type_rank("扬州中学高三(1)班不限校类全市排名")
    assert rank_pool_school_type("扬州中学高三(1)班不限校类全市排名", _PRINCIPAL) is None
    assert rank_pool_school_type("扬州中学高三(1)班全市普通高中排名", _PRINCIPAL) is None
    assert not peer_rank_type_required("扬州中学高三(1)班全校排名", _PRINCIPAL)
    assert rank_pool_school_type("扬州中学高三(1)班全校排名", _PRINCIPAL) is None
    assert rank_pool_label("扬州中学高三(1)班全校排名", _PRINCIPAL) == "全校"


def test_student_list_is_not_school_city_rank():
    assert is_school_city_rank_query("扬州中学全市排名")
    assert not is_school_city_rank_query("扬州中学在籍生总分前10名")
    assert not is_school_city_rank_query("扬州中学高三(1)班全市排名")


def test_lint_principal_city_rank_requires_xxlb_type():
    token = push_constraints({"edu_scope": _PRINCIPAL})
    try:
        assert _type_lint(_CLASS_RANK_SQL, _CLASS_Q)
        ok = _CLASS_RANK_SQL.replace(
            "xxlb NOT LIKE '%其他%'",
            "xxlb NOT LIKE '%其他%' AND xxlb LIKE '%引领%'",
        )
        assert not _type_lint(ok, _CLASS_Q)
        free = "扬州中学高三(1)班不限校类全市排名"
        assert not _type_lint(_CLASS_RANK_SQL, free)
        assert not _type_lint(_CLASS_RANK_SQL, "扬州中学高三(1)班全校排名")
    finally:
        reset_constraints(token)


def test_lint_strength_same_pool_as_rank():
    token = push_constraints({"edu_scope": _PRINCIPAL})
    try:
        assert _type_lint(_STRENGTH_SQL, _STRENGTH_Q)
        ok = _STRENGTH_SQL.replace(
            "xxlb NOT LIKE '%其他%'",
            "xxlb NOT LIKE '%其他%' AND xxlb LIKE '%引领%'",
        )
        assert not _type_lint(ok, _STRENGTH_Q)
    finally:
        reset_constraints(token)


def test_lint_bureau_citywide_unless_named_type():
    token = push_constraints({"edu_scope": _BUREAU})
    try:
        assert not _type_lint(_CLASS_RANK_SQL, _CLASS_Q)
        named = "扬州中学高三(1)班支撑校全市排名"
        assert _type_lint(_CLASS_RANK_SQL, named)
        ok = _CLASS_RANK_SQL.replace(
            "xxlb NOT LIKE '%其他%'",
            "xxlb NOT LIKE '%其他%' AND xxlb LIKE '%支撑%'",
        )
        assert not _type_lint(ok, named)
    finally:
        reset_constraints(token)


def test_scope_constraints_principal_not_bureau():
    principal = format_scope_constraints({"edu_scope": _PRINCIPAL})
    assert "同层次" in principal
    assert "问句写全市仍是同层次" in principal
    bureau = format_scope_constraints({"edu_scope": _BUREAU})
    assert "默认同层次" not in bureau


def test_strength_verdict_uses_peer_label():
    rows = [{"学科": "语文", "全市排名": 7, "参赛学校数": 37}]
    cols = ["学科", "全市排名", "参赛学校数"]
    token = push_constraints({"edu_scope": _PRINCIPAL})
    try:
        text = format_subject_strength_verdict(rows, cols, "扬州中学优势学科")
        assert "引领校" in text
        assert "全市前25%" not in text
    finally:
        reset_constraints(token)


def test_bound_school_type_ignores_other():
    assert bound_school_type_from_edu({"school_type": "其他校"}) is None
    assert bound_school_type_from_edu({"xxlb": "引领高中"}) == "引领"


def test_principal_school_strength_does_not_ask_class():
    from src.agent.education.clarification import (
        SLOT_CLASS,
        SLOT_EXAM,
        candidate_missing_slots,
        extract_filled_slots,
    )

    edu = {"edu_role": "school_admin", "school_name": "安宜中学"}
    q = "2026届高三1月期末 学校的薄弱学科"
    filled = extract_filled_slots(q, edu)
    missing = candidate_missing_slots(None, q, filled, edu)
    assert SLOT_CLASS not in missing
    assert SLOT_EXAM not in missing
    assert extract_filled_slots("我校的薄弱学科", edu).get("school_name") == "安宜中学"
    named = "2026届高三1月期末 安宜中学的薄弱学科"
    assert SLOT_CLASS not in candidate_missing_slots(
        None, named, extract_filled_slots(named, edu), edu
    )
    class_q = "2026届高三1月期末 高三(1)班薄弱学科"
    class_filled = extract_filled_slots(class_q, edu)
    assert class_filled.get(SLOT_CLASS)
