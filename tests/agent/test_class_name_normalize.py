from src.agent.education.clarification import extract_filled_slots
from src.agent.education.orchestrator import _extract_class_name
from src.agent.education.query_parse import extract_class_target, normalize_fullwidth_parentheses
from src.agent.expand.planner import _subject_strength_fact_hint

_CID930 = "扬州中学高三 (5) 班的薄弱学科是哪个"


def test_normalize_collapses_spaces_around_class_parens():
    assert "高三(5)班" in normalize_fullwidth_parentheses(_CID930)
    assert normalize_fullwidth_parentheses("高三（5）班") == "高三(5)班"
    assert normalize_fullwidth_parentheses("高三  (5)  班") == "高三(5)班"
    assert normalize_fullwidth_parentheses("高三2班") == "高三(2)班"


def test_normalize_does_not_turn_exam_month_into_class():
    text = normalize_fullwidth_parentheses("扬州中学2026届高三1月考试")
    assert "高三(1)班" not in text
    assert "高三1月" in text


def test_extract_class_from_spaced_cid930_question():
    assert extract_class_target(_CID930) == "高三(5)班"
    assert _extract_class_name(_CID930) == "高三(5)班"
    filled = extract_filled_slots(_CID930)
    assert filled.get("class_name") == "高三(5)班"


def test_subject_strength_hint_uses_class_rank_when_class_present():
    cls = _extract_class_name(_CID930) or ""
    hint = _subject_strength_fact_hint(cls)
    assert "对照池班级排名" in hint
    assert "GROUP BY xx,bj" in hint
    assert "GROUP BY xx 后" not in hint


def test_admin_also_extracts_spaced_class_and_named_school():
    admin = extract_filled_slots(_CID930, {"edu_role": "admin"})
    assert admin.get("class_name") == "高三(5)班"
    assert "扬州中学" in (admin.get("school_name") or "")

    school_admin = extract_filled_slots(
        _CID930,
        {"edu_role": "school_admin", "school_id": "GZ_19D9D68D"},
    )
    assert school_admin.get("class_name") == "高三(5)班"
