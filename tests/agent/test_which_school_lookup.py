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
    is_interrogative_school_name,
    is_which_school_lookup_query,
)

_Q_YANGZHOU = "扬州市哪几所学校本科达线人数一直在增长"
_Q_CITYWIDE = "全市 哪几所学校本科达线人数一直在增长"
_EDU = {"edu_role": "platform_admin"}


def test_extract_rejects_which_schools_phrase() -> None:
    assert extract_school_target(_Q_YANGZHOU) is None
    assert extract_school_target(_Q_CITYWIDE) is None
    assert extract_school_target("全市哪几所学校本科达线人数一直在增长") is None
    assert extract_school_target("扬州中学本科达线人数") == "扬州中学"
    assert extract_school_target("扬州市第一中学达线人数") == "扬州市第一中学"


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
