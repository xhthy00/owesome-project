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


def test_lint_subject_avg_requires_exclude_zero():
    bad = lint_edu_sql(
        "SELECT ROUND(AVG(yw), 1) AS 语文, ROUND(AVG(ls), 1) AS 历史 "
        "FROM tb_score_overview WHERE xx LIKE '%扬州中学%'"
    )
    assert any("FILTER" in w and "ls" in w for w in bad)
    good = lint_edu_sql(
        "SELECT ROUND(AVG(yw) FILTER (WHERE yw > 0), 1) AS 语文, "
        "ROUND(AVG(ls) FILTER (WHERE ls > 0), 1) AS 历史, "
        "COUNT(*) FILTER (WHERE ls > 0) AS 历史人数 "
        "FROM tb_score_overview WHERE xx LIKE '%扬州中学%'"
    )
    assert good == []
    single = lint_edu_sql(
        "SELECT ROUND(AVG(yw), 2) AS avg_score, COUNT(*) AS n "
        "FROM tb_score_overview WHERE yw > 0 AND xx LIKE '%扬州中学%'"
    )
    assert single == []


def test_lint_subject_stdev_requires_exclude_zero():
    bad = lint_edu_sql(
        "SELECT STDDEV_SAMP(ls) AS stdev, AVG(ls) AS avg_score "
        "FROM tb_score_overview"
    )
    assert any("ls" in w and "0" in w for w in bad)
    good = lint_edu_sql(
        "SELECT STDDEV_SAMP(ls) FILTER (WHERE ls > 0) AS stdev, "
        "AVG(ls) FILTER (WHERE ls > 0) AS avg_score "
        "FROM tb_score_overview"
    )
    assert good == []


def test_lint_citywide_class_rank_requires_enrolled():
    bad = lint_edu_sql(
        "SELECT xx, bj, AVG(sx) FILTER (WHERE sx > 0) AS avg_sx, "
        "RANK() OVER (ORDER BY AVG(sx) FILTER (WHERE sx > 0) DESC) "
        "FROM tb_score_overview GROUP BY xx, bj"
    )
    assert any("市报" in w or "在籍" in w for w in bad)
    good = lint_edu_sql(
        "SELECT xx, bj, AVG(sx) FILTER (WHERE sx > 0) AS avg_sx, "
        "RANK() OVER (ORDER BY AVG(sx) FILTER (WHERE sx > 0) DESC) "
        "FROM tb_score_overview WHERE xsxz='在籍生' GROUP BY xx, bj"
    )
    assert good == []


def test_citywide_class_rank_plan_excludes_shibao():
    from src.agent.expand.planner import build_fact_query_plan_items

    q = "2026届高三1月扬州中学高三(1)班数学成绩全市排名"
    assert resolve_edu_sql_intent(q) == "overview_avg"
    blob = build_fact_query_plan_items(q)[0]["sub_task"]
    assert "在籍" in blob or "市报" in blob
    hint = build_education_sql_hint_text(q)
    assert "在籍" in hint or "市报" in hint


def test_resolve_edu_sql_intent_balance_uses_overview_avg():
    q = "全市均衡性最好的学科"
    assert resolve_edu_sql_intent(q) == "overview_avg"
    term, training = build_education_prompt_extras(q)
    assert "FILTER" in term or "未选考" in term
    assert "STDDEV" in training or "均衡" in training
    hint = build_education_sql_hint_text(q)
    assert "FILTER" in hint
    from src.agent.expand.planner import build_fact_query_plan_items

    blob = build_fact_query_plan_items(q)[0]["sub_task"]
    assert "FILTER" in blob
    assert "未选考" in blob


def test_resolve_edu_sql_intent_subject_avg_overall():
    assert resolve_edu_sql_intent("分析一下3月考试的整体情况") == "overview_avg"
    term, training = build_education_prompt_extras("扬州中学1月期末各科均分")
    assert "FILTER" in term or "未选考" in term
    assert "FILTER" in training
    hint = build_education_sql_hint_text("扬州中学1月期末各科均分")
    assert "FILTER" in hint
    from src.agent.expand.planner import build_fact_query_plan_items

    blob = build_fact_query_plan_items("扬州中学1月期末各科均分")[0]["sub_task"]
    assert "FILTER" in blob
    assert "未选考" in blob
