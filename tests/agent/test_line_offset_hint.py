from src.agent.education.line_offset import (
    LINE_OFFSET_HINT,
    line_offset_hint,
    needs_line_offset_hint,
)
from src.agent.education.prompt_context import build_education_sql_hint_text
from src.agent.expand.planner import _track_xkkm_not_xkqk_hint
from src.templates.sql_gen_prompt import education_terminologies_block

_Q_WITHIN = "新华中学 2026届高三1月期末考试 特招线线上15分以内(包括15分)在 2026届高三3月 考试中有多少未达到特招线"
_Q_ABOVE = "新华中学1月特招线上15分在3月有多少未达特招线"
_Q_AVG = "新华中学高三1班语文均分"


def test_needs_line_offset_only_for_line_phrases() -> None:
    assert needs_line_offset_hint(_Q_WITHIN)
    assert needs_line_offset_hint(_Q_ABOVE)
    assert needs_line_offset_hint("线下10分以内多少人")
    assert needs_line_offset_hint("邗江本科达线人数")
    assert not needs_line_offset_hint(_Q_AVG)
    assert not needs_line_offset_hint("扬州中学数学全市排名")
    assert line_offset_hint(_Q_AVG) == ""
    assert line_offset_hint(_Q_WITHIN) == LINE_OFFSET_HINT


def test_hint_keeps_bare_above_and_opens_within() -> None:
    text = LINE_OFFSET_HINT
    assert "线上N分以内" in text
    assert "包括N分" in text
    assert "≥线+N" in text
    assert "≥511" in text
    assert "[496,511]" in text
    assert "禁止写成" not in text
    assert "那是压线" not in text
    assert "达线本身仍是" in text


def test_track_hint_stays_xkkm_only() -> None:
    track = _track_xkkm_not_xkqk_hint()
    assert "xkkm" in track
    assert "线上N分" not in track
    assert "禁止写成" not in track


def test_prompt_context_injects_table_on_within() -> None:
    within = build_education_sql_hint_text(_Q_WITHIN)
    assert "线上N分以内" in within
    assert "禁止写成" not in within
    avg = build_education_sql_hint_text(_Q_AVG)
    assert "线上N分以内" not in avg


def test_sql_gen_terminology_uses_shared_table() -> None:
    block = education_terminologies_block()
    assert LINE_OFFSET_HINT in block
    assert "禁止写成线到线+N的闭区间" not in block
    assert "SUM(reached_count)/SUM(candidates)" in block
