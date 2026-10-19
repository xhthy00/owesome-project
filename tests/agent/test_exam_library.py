"""试卷库可见范围、归属和核对结果解析。"""

import json

from src.agent.education.exam_library import (
    ExamActor,
    exam_visible,
    normalize_ownership,
    normalize_question_rows,
)
from src.agent.education.exam_match import parse_match_reply


def _actor(**kwargs) -> ExamActor:
    base = {"see_all": False, "school_id": "A01", "owner_id": "teacher1", "edu_role": "teacher"}
    base.update(kwargs)
    return ExamActor(**base)


def test_visibility_follows_city_school_and_owner():
    city = {"exam_type": "city", "school_id": None, "owner_id": None}
    school = {"exam_type": "school", "school_id": "A01", "owner_id": None}
    other_school = {"exam_type": "school", "school_id": "B02", "owner_id": None}
    own = {"exam_type": "teacher", "school_id": "A01", "owner_id": "teacher1"}
    peer = {"exam_type": "teacher", "school_id": "A01", "owner_id": "teacher2"}
    actor = _actor()

    assert exam_visible(city, actor)
    assert exam_visible(school, actor)
    assert not exam_visible(other_school, actor)
    assert exam_visible(own, actor)
    assert not exam_visible(peer, actor)
    assert exam_visible(peer, _actor(see_all=True, edu_role="bureau_admin"))
    assert exam_visible({"exam_type": None}, actor)


def test_city_exam_clears_school_and_owner():
    actor = _actor()
    assert normalize_ownership("全市统考", "A01", "teacher1", actor) == ("city", None, None)
    assert normalize_ownership("school", "", "", actor) == ("school", "A01", None)
    assert normalize_ownership("teacher", None, None, actor) == ("teacher", "A01", "teacher1")


def test_question_rows_reject_duplicate_numbers_and_normalize_weights():
    rows = normalize_question_rows(
        [
            {
                "question_no": " 1 ",
                "question_score": 5,
                "knowledge_links": [{"knowledge_id": 3}, {"knowledge_id": 4}],
            },
            {"id": 9, "question_no": "17-1", "question_score": 2, "content": "题干"},
        ]
    )
    assert rows[0]["question_no"] == "1"
    assert rows[0]["id"] is None
    assert rows[0]["knowledge_links"] == [
        {"knowledge_id": 3, "weight": 0.5},
        {"knowledge_id": 4, "weight": 0.5},
    ]
    assert rows[1]["id"] == 9

    try:
        normalize_question_rows([{"question_no": "1"}, {"question_no": "1"}])
    except ValueError as exc:
        assert "题号重复" in str(exc)
    else:
        raise AssertionError("duplicate question number should fail")


def test_parse_match_reply_keeps_only_known_names():
    raw = """<think>skip</think>
```json
{"ok": false, "add": [{"name": "函数", "confidence": 0.9, "reason": "题干"}],
 "remove": [{"name": "不存在"}, {"name": "集合", "reason": "偏了"}]}
```"""
    parsed = parse_match_reply(
        raw,
        [{"id": 1, "knowledge_name": "函数"}, {"id": 2, "knowledge_name": "集合"}],
        [{"knowledge_id": 2, "knowledge_name": "集合"}],
    )
    assert parsed["verdict"] == "needs_review"
    assert parsed["add"] == [
        {"knowledge_id": 1, "knowledge_name": "函数", "confidence": 0.9, "reason": "题干"}
    ]
    assert parsed["remove"][0]["knowledge_id"] == 2
    assert json.loads(json.dumps(parsed))["verdict"] == "needs_review"
