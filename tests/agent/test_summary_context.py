"""summary_context 单元测试。"""

from __future__ import annotations

from src.agent.education.summary_context import (
    collect_education_artifacts,
    format_education_pipeline_footer,
    format_tool_expert_sub_task_block,
)
from src.chat.service.agent_runner import _format_sub_tasks_block


def test_collect_education_artifacts_from_fetch_tool():
    agg = collect_education_artifacts(
        tool_calls=[
            {
                "tool": "fetch_subject_diagnosis_data_tool",
                "success": True,
                "data": {
                    "item_rows": [{"question_no": 1}] * 12,
                    "knowledge_rows": [{"knowledge_name": "函数"}] * 8,
                    "score_rows": [{"score": 90}] * 24,
                },
            }
        ],
        reports=[{"title": "数学诊断报告", "html": "<html></html>"}],
    )
    assert agg["item_count"] == 12
    assert agg["knowledge_count"] == 8
    assert agg["has_diagnosis_report"] is True
    assert "数学诊断报告" in agg["report_titles"]


def test_format_education_pipeline_footer_forbids_missing_data_claim():
    footer = format_education_pipeline_footer(
        {
            "sub_tasks": [
                {
                    "tool_calls": [
                        {
                            "tool": "fetch_subject_diagnosis_data_tool",
                            "success": True,
                            "data": {
                                "item_rows": [1, 2],
                                "knowledge_rows": [1, 2, 3],
                            },
                        }
                    ],
                    "reports": [{"title": "数学诊断报告"}],
                }
            ]
        }
    )
    assert "小题级诊断数据：**已获取**" in footer
    assert "知识点级诊断数据：**已获取**" in footer
    assert "禁止" in footer


def test_format_sub_tasks_block_includes_education_footer():
    class _FakeReply:
        content = "报告已渲染"

    class _FakeState:
        last_sql = ""
        last_exec_result = None
        tool_calls = [
            {
                "tool": "fetch_subject_diagnosis_data_tool",
                "success": True,
                "data": {
                    "item_rows": [{"question_no": i} for i in range(5)],
                    "knowledge_rows": [{"knowledge_name": "函数"}],
                },
            },
            {
                "tool": "build_subject_diagnosis_sections_tool",
                "success": True,
                "data": {"output_type": "html", "title": "数学诊断报告", "html": "<p>x</p>"},
            },
        ]
        reports = [{"title": "数学诊断报告"}]

    class _FakePhase:
        is_success = True
        fail_reason = ""
        reply = _FakeReply()
        state = _FakeState()

    block = _format_sub_tasks_block(
        [("fetch 并渲染报告", _FakePhase())],
        report_data={
            "sub_tasks": [
                {
                    "tool_calls": _FakeState().tool_calls,
                    "reports": _FakeState().reports,
                }
            ]
        },
    )
    assert "小题明细：5 题" in block
    assert "数学诊断报告" in block
    assert "教育学情产出摘要" in block
    assert "禁止" in block


def test_format_tool_expert_sub_task_block():
    text = format_tool_expert_sub_task_block(
        tool_calls=[
            {
                "tool": "build_subject_diagnosis_sections_tool",
                "success": True,
                "data": {"output_type": "html", "title": "数学诊断报告"},
            }
        ],
        reports=[],
        final_answer="完成",
    )
    assert "数学诊断报告" in text


def test_sql_offset_authority_notes_forbid_page_as_class_size():
    from src.agent.education.summary_context import (
        extract_stats_authority_block,
        format_sql_result_authority_notes,
        sql_looks_paginated,
    )

    assert sql_looks_paginated("SELECT * FROM t LIMIT 20 OFFSET 40")
    assert not sql_looks_paginated("SELECT * FROM t LIMIT 3")
    notes = format_sql_result_authority_notes(
        sql="SELECT * FROM tb_score ORDER BY id LIMIT 20 OFFSET 40",
        row_count=12,
        sample_shown=12,
    )
    assert "OFFSET" in notes
    assert "禁止" in notes
    assert "12" in notes

    stats = extract_stats_authority_block(
        [
            {
                "tool": "compute_score_stats_tool",
                "success": True,
                "content": "成绩统计完成：共 52 人，均分 108.5，满分 150，及格线 45，优秀线 75，及格率 100%，优秀率 96.15%，标准差 21.73。",
                "data": {
                    "count": 52,
                    "avg": 108.5,
                    "full_score": 150,
                    "pass_line": 45,
                    "excellent_line": 75,
                    "pass_rate": 100.0,
                    "excellent_rate": 96.15,
                },
            }
        ]
    )
    assert "count=52" in stats
    assert "及格线=45" in stats


def test_format_sub_tasks_block_flags_offset_sample():
    class _FakeReply:
        content = "全班参考人数 52 人，卷面满分 150，及格线 45，优秀线 75。"

    class _FakeState:
        last_sql = (
            "SELECT student_id, score FROM tb_score "
            "WHERE class='高三(10)班' ORDER BY student_id LIMIT 20 OFFSET 40"
        )
        last_exec_result = {
            "columns": ["student_id", "score"],
            "rows": [{"student_id": f"s{i}", "score": 80 + i} for i in range(12)],
            "row_count": 12,
        }
        tool_calls = [
            {
                "tool": "compute_score_stats_tool",
                "success": True,
                "content": "成绩统计完成：共 52 人",
                "data": {
                    "count": 52,
                    "avg": 108.5,
                    "full_score": 150.0,
                    "pass_line": 45.0,
                    "excellent_line": 75.0,
                    "pass_rate": 100.0,
                    "excellent_rate": 96.15,
                },
            }
        ]
        reports = []

    class _FakePhase:
        is_success = True
        fail_reason = ""
        reply = _FakeReply()
        state = _FakeState()

    block = _format_sub_tasks_block([("班级明细", _FakePhase())])
    assert "OFFSET" in block
    assert "禁止当班级总人数" in block or "禁止" in block
    assert "count=52" in block
    assert "参考人数 52" in block
    assert "样例（仅预览" in block


def test_truncate_keeping_kpi_lines_preserves_pass_line():
    from src.agent.education.summary_context import truncate_keeping_kpi_lines

    long = "前言\n" + ("x" * 1500) + "\n参考人数 52 人\n及格线 45\n优秀线 75\n"
    out = truncate_keeping_kpi_lines(long, limit=800)
    assert "参考人数 52" in out
    assert "及格线 45" in out
