"""试卷库可见范围、归属和核对结果解析。"""

import json

from src.agent.education.exam_library import (
    EXAM_TYPE_LABELS,
    ExamActor,
    _batch_visibility_sql,
    _exam_name_is_this_batch,
    _exam_payload,
    _visibility_sql,
    batch_visible,
    batch_writable,
    can_create_batch,
    can_create_exam,
    exam_visible,
    exam_writable,
    new_batch_owner,
    normalize_batch_exam_type,
    normalize_batch_fields,
    normalize_batch_jc,
    normalize_ownership,
    normalize_question_rows,
    stamp_exam_from_batch,
)
from src.agent.education.exam_match import (
    _user_prompt,
    attach_match_context,
    limit_suggestions,
    links_after_adopt,
    match_summary,
    parse_match_reply,
)


def _actor(**kwargs) -> ExamActor:
    base = {"see_all": False, "school_id": "A01", "owner_id": "teacher1", "edu_role": "teacher"}
    base.update(kwargs)
    return ExamActor(**base)


def test_school_user_sees_city_papers_and_owns_what_they_create():
    actor = _actor(edu_role="school_admin", school_id="A01", owner_id="school1")
    city = {"exam_type": "city", "school_id": None, "owner_id": None}
    own = {"exam_type": "school", "school_id": "A01", "owner_id": None}
    other_school = {"exam_type": "school", "school_id": "B02", "owner_id": None}
    bureau = _actor(see_all=True, edu_role="bureau_admin")

    assert exam_visible(city, actor)
    assert not exam_writable(city, actor)
    assert exam_visible(own, actor)
    assert exam_writable(own, actor)
    assert not exam_visible(other_school, actor)
    leaked = {"exam_type": "school", "school_id": "B02", "owner_id": "school1"}
    assert not exam_visible(leaked, actor)
    assert not exam_writable(leaked, actor)
    assert exam_visible(other_school, bureau)
    assert exam_writable(city, bureau)
    assert exam_visible({"exam_type": None}, actor)


def test_school_user_cannot_assign_another_school_or_owner():
    school = _actor(edu_role="school_admin", school_id="A01", owner_id="school1")
    assert normalize_ownership("全市统考", "B02", "other", school) == ("city", None, None)
    assert normalize_ownership("school", "B02", "other", school) == ("school", "A01", None)
    bureau = _actor(see_all=True, edu_role="bureau_admin", school_id="A01", owner_id="admin")
    assert normalize_ownership("全市统考", "A01", "teacher1", bureau) == ("city", None, None)
    assert normalize_ownership("全市统考", "A01", "teacher1", bureau, "school1") == (
        "city",
        None,
        None,
    )
    assert normalize_ownership("school", "", "", bureau) == ("school", "A01", None)
    assert can_create_exam(bureau)
    assert can_create_exam(school)
    assert not can_create_exam(_actor(edu_role="teacher"))


def test_same_school_users_maintain_their_batch():
    school = "GZ_AADB4D1D"
    own = {"exam_type": "school", "school_id": school, "owner_id": "exvwji03"}
    city = {"exam_type": "city", "school_id": None, "owner_id": None}
    creator = _actor(edu_role="school_admin", school_id=school, owner_id="exvwji03")
    peer = _actor(edu_role="school_admin", school_id=school, owner_id="dvfort31")
    other = _actor(edu_role="school_admin", school_id="OTHER", owner_id="other")
    teacher = _actor(edu_role="teacher", school_id=school, owner_id="teacher1")
    bureau = _actor(see_all=True, edu_role="bureau_admin", owner_id="admin")

    assert batch_visible(city, creator)
    assert batch_visible(own, creator)
    assert batch_visible(own, peer)
    assert not batch_visible(own, other)
    assert batch_visible(own, bureau)
    assert batch_writable(own, creator)
    assert batch_writable(own, peer)
    assert not batch_writable(city, creator)
    assert not batch_writable(own, other)
    assert not batch_writable(own, teacher)
    assert batch_writable(city, bureau)
    assert can_create_batch(creator)
    assert can_create_batch(bureau)
    assert not can_create_batch(teacher)
    assert new_batch_owner(creator) == (school, "exvwji03")
    assert new_batch_owner(bureau) == (None, None)
    unbound = {"exam_type": "school", "school_id": None, "owner_id": "exvwji03"}
    assert not batch_writable(unbound, creator)
    foreign = {"exam_type": "school", "school_id": "OTHER", "owner_id": "exvwji03"}
    assert not batch_visible(foreign, creator)
    sql, params = _batch_visibility_sql(other)
    assert "b.school_id = %s" in sql
    assert "owner_id" not in sql
    assert params == ("OTHER",)
    paper_sql, paper_params = _visibility_sql(other)
    assert "school_id = %s" in paper_sql
    assert "owner_id" not in paper_sql
    assert paper_params == ("OTHER",)


def test_paper_type_follows_the_batch_not_the_form():
    school = _actor(edu_role="school_admin", school_id="A01", owner_id="school1")
    base = {
        "exam_name": "练习卷",
        "subject": "语文",
        "exam_score": 150,
        "exam_type": "school",
        "school_id": "B02",
        "exam_batch_id": 3,
    }
    payload = _exam_payload(base, school)
    stamp_exam_from_batch(payload, "city", None, school)
    assert payload["exam_type"] == "city"
    assert payload["school_id"] is None
    assert payload["owner_id"] is None

    bureau = _actor(see_all=True, edu_role="bureau_admin", school_id="A01", owner_id="admin")
    city_form = _exam_payload({**base, "exam_type": "city"}, bureau)
    stamp_exam_from_batch(city_form, "school", "B02", bureau)
    assert city_form["exam_type"] == "school"
    assert city_form["school_id"] == "B02"
    assert normalize_batch_exam_type("全市统考") == "city"
    assert normalize_batch_exam_type("全市联考") == "city"
    assert normalize_batch_exam_type("模拟考试") == "city"
    assert EXAM_TYPE_LABELS["city"] == "模拟考试"
    assert normalize_batch_jc("2027届高三10月月考") == "2027"
    assert normalize_batch_jc(2027) == "2027"
    try:
        normalize_batch_jc("")
        raise AssertionError("empty cohort should be rejected")
    except ValueError as exc:
        assert "届次" in str(exc)
    try:
        normalize_batch_exam_type("教师自建")
    except ValueError as exc:
        assert "考试类型" in str(exc)
    else:
        raise AssertionError("teacher type is not a batch type")


def test_batch_fields_require_name_and_date():
    assert normalize_batch_fields("  2027届高二6月期末  ", "2026-06-18") == (
        "2027届高二6月期末",
        "2026-06-18",
    )
    try:
        normalize_batch_fields("", "2026-06-18")
    except ValueError as exc:
        assert "批次名称" in str(exc)
    else:
        raise AssertionError("empty batch name should fail")
    try:
        normalize_batch_fields("期末", "")
    except ValueError as exc:
        assert "考试时间" in str(exc)
    else:
        raise AssertionError("empty exam time should fail")


def test_mismatched_score_line_name_is_not_this_batch():
    batch = "新华中学2026~2027学年度第一学期第一阶段自主练习"
    assert not _exam_name_is_this_batch("2026届高三5月模拟", batch)
    assert _exam_name_is_this_batch(batch, batch)
    assert _exam_name_is_this_batch("  ", batch)
    assert _exam_name_is_this_batch(None, batch)


def test_every_exam_requires_a_batch():
    actor = _actor(edu_role="school_admin", school_id="A01", owner_id="school1")
    base = {"exam_name": "练习卷", "subject": "语文", "exam_score": 150, "exam_type": "school"}
    owned = _exam_payload({**base, "exam_batch_id": "3"}, actor)
    assert owned["exam_batch_id"] == 3
    assert owned["owner_id"] is None
    assert owned["school_id"] == "A01"
    city = _exam_payload({**base, "exam_type": "city", "exam_batch_id": 3}, actor)
    assert city["exam_batch_id"] == 3
    assert city["owner_id"] is None
    assert city["school_id"] is None

    try:
        _exam_payload(base, actor)
    except ValueError as exc:
        assert "考试批次" in str(exc)
    else:
        raise AssertionError("exam without batch should fail")

    try:
        _exam_payload({**base, "exam_batch_id": 0}, actor)
    except ValueError as exc:
        assert "考试批次" in str(exc)
    else:
        raise AssertionError("invalid batch id should fail")


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


def test_full_suggestion_replaces_existing_links():
    chosen = links_after_adopt([4343, 4344, 4368], set(), [4349, 4350, 4383])
    assert chosen == [4349, 4350, 4383]


def test_partial_suggestion_still_requires_room():
    try:
        links_after_adopt([1, 2, 3], set(), [4])
    except ValueError as exc:
        assert "超过 3 个" in str(exc)
    else:
        raise AssertionError("one extra link should not fit")
    assert links_after_adopt([1, 2, 3], {1}, [4]) == [2, 3, 4]


def test_parse_match_reply_moves_overflow_into_remove():
    parsed = parse_match_reply(
        '{"ok": false, "add": [{"name": "甲", "confidence": 0.9, "reason": "a"},'
        '{"name": "乙", "confidence": 0.8, "reason": "b"},'
        '{"name": "丙", "confidence": 0.7, "reason": "c"}], "remove": []}',
        [
            {"id": 11, "knowledge_name": "甲"},
            {"id": 12, "knowledge_name": "乙"},
            {"id": 13, "knowledge_name": "丙"},
        ],
        [
            {"knowledge_id": 1, "knowledge_name": "旧一", "weight": 0.5},
            {"knowledge_id": 2, "knowledge_name": "旧二", "weight": 0.2},
            {"knowledge_id": 3, "knowledge_name": "旧三", "weight": 0.3},
        ],
    )
    assert [item["knowledge_id"] for item in parsed["add"]] == [11, 12, 13]
    assert {item["knowledge_id"] for item in parsed["remove"]} == {1, 2, 3}


def test_sub_question_match_uses_the_shared_stem():
    questions = [
        {"question_no": "16-1", "content": "阅读材料：循吏是古代良吏。\n（1）概括特点。"},
        {"question_no": "16-2", "content": "（2）分析取得成效的原因。"},
        {"question_no": "17", "content": "阅读材料，完成下列要求。材料 中国现代化。"},
        {"question_no": "单选1", "content": "西周封卫国以统治殷商遗民。"},
    ]
    attach_match_context(questions)
    assert "lead_in" not in questions[0]
    assert "循吏是古代良吏" in questions[1]["lead_in"]
    assert "lead_in" not in questions[2]
    assert "lead_in" not in questions[3]
    prompt = _user_prompt(questions[1], [])
    assert "大题材料与前文" in prompt
    assert "当前小题" in prompt
    assert "分析取得成效的原因" in prompt


def test_later_sub_question_keeps_opening_and_previous_part():
    questions = [
        {"question_no": "14（1）①", "content": "从氧化锌烟尘中回收锌。①写出原因。"},
        {"question_no": "14（1）②", "content": "②浸出率变化的原因是。"},
        {"question_no": "14（3）②", "content": "②电解后的溶液可循环用于哪一步。"},
    ]
    attach_match_context(questions)
    lead = questions[2]["lead_in"]
    assert "氧化锌烟尘" in lead
    assert "浸出率变化的原因" in lead
    assert "电解后的溶液" not in lead


def test_only_sub_question_uses_parent_row_stem():
    questions = [
        {"question_no": "15", "content": "阅读材料：科茨沃尔兹是英国乡村城镇化的代表。"},
        {"question_no": "15-1", "content": "（1）说明城镇化起步的条件。"},
    ]
    attach_match_context(questions)
    assert "科茨沃尔兹" in questions[1]["lead_in"]
    assert "lead_in" not in questions[0]


def test_limit_suggestions_keeps_the_three_most_confident():
    kept = limit_suggestions(
        [
            {"knowledge_id": 1, "confidence": 0.4},
            {"knowledge_id": 2, "confidence": 0.95},
            {"knowledge_id": 3, "confidence": 0.8},
            {"knowledge_id": 4, "confidence": 0.7},
        ]
    )
    assert [item["knowledge_id"] for item in kept] == [2, 3, 4]


def test_link_weights_stay_on_one_decimal():
    from src.agent.education.exam_library import normalize_links

    even = normalize_links([{"knowledge_id": 1}, {"knowledge_id": 2}, {"knowledge_id": 3}])
    assert [item["weight"] for item in even] == [0.4, 0.3, 0.3]
    tuned = normalize_links(
        [
            {"knowledge_id": 1, "weight": 0.7},
            {"knowledge_id": 2, "weight": 0.2},
            {"knowledge_id": 3, "weight": 0.1},
        ]
    )
    assert [item["weight"] for item in tuned] == [0.7, 0.2, 0.1]


def test_match_summary_distinguishes_unlinked_papers_from_adjustments():
    assert match_summary(24, 0, 24, 0, 24) == (
        "共 24 题目前都还没有关联知识点，本次给出关联建议 24 题，核对失败 0 题"
    )
    assert match_summary(10, 7, 2, 1, 3) == "共 10 题：关联正确 7 题，建议调整 2 题，核对失败 1 题"
