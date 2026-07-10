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
