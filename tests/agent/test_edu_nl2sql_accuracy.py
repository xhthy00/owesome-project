"""教育问数准确率：peek / lint / 意图裁剪 extras 回归。"""

from __future__ import annotations

from src.agent.education.filter_peek import (
    empty_result_protocol_note,
    extract_filter_literals,
    format_peek_payload,
    peek_edu_filter_values,
    touches_edu_table,
)
from src.agent.education.prompt_context import (
    build_education_prompt_extras,
    build_education_sql_hint_text,
)
from src.agent.education.query_parse import extract_district_target, extract_exam_name_hint
from src.agent.education.sql_lint import format_lint_warnings, lint_edu_sql
from src.templates.sql_gen_prompt import resolve_edu_sql_intent


def test_district_after_month_not_swallowed():
    q = "扬州市2026届高三3月广陵区本科线达线人数和达线率"
    assert extract_district_target(q) == "广陵区"
    assert extract_exam_name_hint(q) == "2026届高三3月"


def test_resolve_edu_sql_intent_line_reach():
    assert resolve_edu_sql_intent("邗江区本科线达线人数") == "line_reach"
    assert resolve_edu_sql_intent("高三(1)班南大达线") == "class_line_reach"
    assert resolve_edu_sql_intent("语数外三门均分") == "overview_avg"


def test_build_education_prompt_extras_trims_for_line_reach():
    term, training = build_education_prompt_extras(
        "扬州市2026届高三3月广陵区本科线达线人数和达线率"
    )
    assert "<terminologies>" in term
    assert "达线" in term or "本科线" in term
    assert "tb_score_indicator" in training
    assert "广陵" in training or "邗江" in training
    # 不应整包灌入无关知识点长示例
    assert "知识点薄弱诊断" not in training
    assert "每一小题得分率" not in training


def test_build_education_prompt_extras_default_compatible():
    term, training = build_education_prompt_extras()
    assert "<terminologies>" in term
    assert "<sql-examples>" in training


def test_build_education_sql_hint_text():
    hint = build_education_sql_hint_text("广陵区本科线达线人数")
    assert "intent=line_reach" in hint
    assert "peek" in hint.lower() or "indicator" in hint.lower()


def test_peek_edu_filter_values_parses_distinct():
    def fake_exec(sql: str):
        assert "tb_score_indicator" in sql
        return (
            True,
            "ok",
            {
                "columns": ["exam_name", "district", "line_name", "track"],
                "rows": [
                    ["2026届高三3月", "广陵区", "本科线", "物理类"],
                    ["2026届高三3月", "邗江区", "本科线", "物理类"],
                ],
            },
        )

    payload = peek_edu_filter_values(fake_exec, exam_hint="2026届高三3月")
    assert payload["districts"] == ["广陵区", "邗江区"]
    assert "2026届高三3月" in payload["exam_names"]
    text = format_peek_payload(payload)
    assert "广陵区" in text


def test_touches_edu_table_and_empty_protocol():
    sql = "SELECT * FROM tb_score_indicator WHERE district = '月广陵区'"
    assert touches_edu_table(sql) is True
    assert "月广陵区" in extract_filter_literals(sql)
    note = empty_result_protocol_note(sql)
    assert "空结果协议" in note
    assert "peek_edu_filter_values" in note
    assert not touches_edu_table("SELECT 1 FROM dual")


def test_lint_avg_reach_rate_and_month_district():
    warns = lint_edu_sql(
        "SELECT AVG(reach_rate) FROM tb_score_indicator WHERE district = '月广陵区'"
    )
    assert any("AVG(reach_rate)" in w for w in warns)
    assert any("月" in w and "区" in w for w in warns)
    blob = format_lint_warnings(warns)
    assert "SQL lint" in blob
