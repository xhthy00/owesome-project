from src.agent.education.clarification import (
    SLOT_CLASS,
    SLOT_EXAM,
    SLOT_SCHOOL,
    candidate_missing_slots,
    extract_filled_slots,
    sanitize_llm_slots,
)
from src.agent.education.entity_resolve import resolve_entities
from src.agent.education.query_parse import (
    extract_school_target,
    extract_school_targets,
    is_interrogative_school_name,
    is_vague_exam_name,
    is_which_school_lookup_query,
)
from src.agent.education.slot_merge import merge_turn_slots

_Q_YANGZHOU = "扬州市哪几所学校本科达线人数一直在增长"
_Q_CITYWIDE = "全市 哪几所学校本科达线人数一直在增长"
_EDU = {"edu_role": "platform_admin"}


def test_extract_rejects_which_schools_phrase() -> None:
    assert extract_school_target(_Q_YANGZHOU) is None
    assert extract_school_target(_Q_CITYWIDE) is None
    assert extract_school_target("全市哪几所学校本科达线人数一直在增长") is None
    assert extract_school_target("扬州中学本科达线人数") == "扬州中学"
    assert extract_school_target("扬州市第一中学达线人数") == "扬州市第一中学"
    assert extract_school_target("请帮我分析扬州大学附属中学") == "扬州大学附属中学"
    assert extract_school_target("实验学校均分") == "实验学校"


def test_extract_rejects_descriptive_school_phrase() -> None:
    q = (
        "对比2026届高三3月和2026届高三5月模拟各学校语文均分，"
        "列出5月比3月下降的学校，给出两场均分和分差"
    )
    assert extract_school_target(q) is None
    assert extract_school_targets(q) == []
    assert extract_school_target("找出退步的学校") is None
    assert extract_school_target("薄弱学校语文均分") is None
    filled = extract_filled_slots(q, _EDU)
    assert SLOT_SCHOOL not in filled
    missing = candidate_missing_slots(None, q, filled, _EDU)
    assert SLOT_SCHOOL not in missing
    assert is_which_school_lookup_query(q)
    assert sanitize_llm_slots({SLOT_SCHOOL: "下降的学校"}) == {}
    catalog = {
        "exams": ["2026届高三3月", "2026届高三5月模拟"],
        "schools": ["A01扬州中学", "B01新华中学"],
    }
    dropped = resolve_entities(
        {SLOT_SCHOOL: "下降的学校", SLOT_EXAM: "2026届高三5月模拟"},
        catalog,
        edu_scope=_EDU,
    )
    assert dropped.clarify_slot is None
    assert SLOT_SCHOOL not in dropped.bound


def test_which_school_lookup_skips_named_school() -> None:
    assert is_which_school_lookup_query(_Q_YANGZHOU)
    assert is_which_school_lookup_query(_Q_CITYWIDE)
    assert not is_which_school_lookup_query("扬州中学本科达线人数")
    assert not is_which_school_lookup_query("扬州中学哪几所学校达线更好")
    assert is_interrogative_school_name("扬州市哪几所学校")
    assert is_interrogative_school_name("哪几所学校")
    assert not is_interrogative_school_name("扬州中学")


def test_line_reach_which_schools_does_not_ask_school() -> None:
    for q in (_Q_YANGZHOU, _Q_CITYWIDE):
        filled = extract_filled_slots(q, _EDU)
        assert SLOT_SCHOOL not in filled
        missing = candidate_missing_slots(None, q, filled, _EDU)
        assert SLOT_SCHOOL not in missing
        assert SLOT_CLASS not in missing
        assert SLOT_EXAM in missing

        filled_exam = {**filled, SLOT_EXAM: "2026届高三1月期末"}
        assert candidate_missing_slots(None, q, filled_exam, _EDU) == []


def test_named_school_line_reach_still_fills_school() -> None:
    q = "扬州中学本科达线人数"
    filled = extract_filled_slots(q, _EDU)
    assert filled.get(SLOT_SCHOOL) == "扬州中学"
    missing = candidate_missing_slots(None, q, filled, _EDU)
    assert SLOT_SCHOOL not in missing


def test_sanitize_and_resolve_drop_interrogative_school() -> None:
    assert sanitize_llm_slots({SLOT_SCHOOL: "哪几所学校"}) == {}
    catalog = {
        "exams": ["2026届高三1月期末"],
        "schools": ["A01扬州中学", "B01新华中学"],
    }
    dropped = resolve_entities(
        {SLOT_SCHOOL: "哪几所学校", SLOT_EXAM: "2026届高三1月期末"},
        catalog,
        edu_scope=_EDU,
    )
    assert dropped.clarify_slot is None
    assert SLOT_SCHOOL not in dropped.bound

    typo = resolve_entities(
        {SLOT_SCHOOL: "瓜州中学", SLOT_EXAM: "2026届高三1月期末"},
        catalog,
        edu_scope=_EDU,
    )
    assert typo.clarify_slot == SLOT_SCHOOL


def test_conjoined_schools_are_split_not_forced_pick_one() -> None:
    q = "丁沟中学和大桥中学 在1月考试中 各自第一名的成绩对比"
    assert extract_school_targets(q) == ["丁沟中学", "大桥中学"]
    assert extract_school_target(q) == "丁沟中学"
    assert is_vague_exam_name("1月")
    assert is_vague_exam_name("1月考试")

    filled = extract_filled_slots(q, _EDU)
    assert SLOT_SCHOOL not in filled
    assert SLOT_EXAM not in filled
    missing = candidate_missing_slots(None, q, filled, _EDU)
    assert SLOT_SCHOOL not in missing
    assert SLOT_CLASS not in missing
    assert SLOT_EXAM in missing

    catalog = {
        "exams": ["2026届高三1月期末", "2026届高三11月期中", "2026届高三3月"],
        "schools": ["丁沟中学", "大桥中学", "扬州中学"],
    }
    hit = resolve_entities({SLOT_SCHOOL: "丁沟中学和大桥中学"}, catalog, edu_scope=_EDU)
    assert hit.clarify_slot is None
    assert SLOT_SCHOOL not in hit.bound

    merged = merge_turn_slots(
        q,
        rule_slots=filled,
        llm_mentions=[
            {
                "kind": "school",
                "value": "丁沟中学",
                "role": "filter",
                "evidence": "丁沟中学",
                "confidence": 0.95,
            },
            {
                "kind": "school",
                "value": "大桥中学",
                "role": "filter",
                "evidence": "大桥中学",
                "confidence": 0.95,
            },
        ],
    )
    assert "school:conflict" not in merged.frame.diagnostics


def test_single_named_school_still_fills() -> None:
    q = "丁沟中学在2026届高三1月期末的均分"
    filled = extract_filled_slots(q, _EDU)
    assert filled.get(SLOT_SCHOOL) == "丁沟中学"


def test_which_school_am_i_principal_is_not_named_school() -> None:
    q = "我是哪个学校的校长"
    assert extract_school_target(q) is None
    assert is_which_school_lookup_query(q)
    assert is_interrogative_school_name("哪个学校")


def test_principal_which_school_is_not_out_of_scope() -> None:
    from src.agent.education.scope_guard import out_of_scope_question

    edu = {
        "edu_role": "school_admin",
        "school_name": "扬州中学",
        "school_id": "GZ_19D9D68D",
    }
    assert out_of_scope_question(edu, "我是哪个学校的校长") is None
    assert out_of_scope_question(edu, "新华中学均分") == "当前数据无权限访问"


def test_baonan_zhongxue_links_to_baonan_xuexiao() -> None:
    """口语「宝楠中学」应对齐目录「宝楠学校」，不再当成没指定学校。"""
    catalog = {
        "exams": ["2026届高三1月期末"],
        "schools": ["扬州中学", "宝应中学", "B05宝楠学校", "新华中学"],
    }
    hit = resolve_entities(
        {SLOT_SCHOOL: "宝楠中学"},
        catalog,
        edu_scope=_EDU,
    )
    assert hit.clarify_slot is None
    assert hit.bound[SLOT_SCHOOL] in {"宝楠学校", "B05宝楠学校"}

    still_typo = resolve_entities(
        {SLOT_SCHOOL: "瓜州中学"},
        catalog,
        edu_scope=_EDU,
    )
    assert still_typo.clarify_slot == SLOT_SCHOOL
