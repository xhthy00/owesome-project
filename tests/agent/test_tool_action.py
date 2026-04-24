"""ToolAction 单元测试：LLM JSON -> 工具调用 -> ActionOutput。"""

from __future__ import annotations

import asyncio

import pytest

from src.agent.core.action import tool_action as tool_action_mod
from src.agent.core.action.tool_action import ToolAction
from src.agent.resource.tool.builtin import TerminateTool
from src.agent.resource.tool.function_tool import tool
from src.agent.resource.tool.pack import ToolPack


def _run(coro):
    return asyncio.run(coro)


@tool()
def add(a: int, b: int) -> int:
    """Add two ints."""
    return a + b


@tool()
def boom() -> str:
    """Always fails."""
    raise RuntimeError("kaboom")


@tool()
def find_related_datasources(question: str) -> str:
    """Find datasource by question."""
    return f"ds-for:{question}"


@tool()
def describe_table(table_name: str) -> str:
    """Describe table by name."""
    return f"describe:{table_name}"


@tool()
def execute_sql(sql: str) -> str:
    """Execute read-only sql."""
    return f"sql:{sql}"


@pytest.fixture()
def pack():
    return ToolPack(tools=[add, find_related_datasources, describe_table, execute_sql, TerminateTool(), boom])


@pytest.fixture()
def audit_spy(monkeypatch):
    calls = []

    def _spy(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(tool_action_mod, "log_tool_call_fire_and_forget", _spy)
    return calls


def test_happy_path_json_object(pack, audit_spy):
    action = ToolAction(tool_pack=pack)
    ai_msg = '{"thoughts": "simple add", "tool": "add", "args": {"a": 1, "b": 2}}'
    out = _run(action.run(ai_msg, agent_name="DataAnalyst", round_idx=2, sub_task_index=1))

    assert out.is_exe_success is True
    assert out.action == "add"
    assert out.thoughts == "simple add"
    assert out.observations == "3"
    assert out.terminate is False
    assert out.extra["tool_data"] == 3
    assert len(audit_spy) == 1
    assert audit_spy[0]["tool_name"] == "add"
    assert audit_spy[0]["success"] is True
    assert audit_spy[0]["agent_name"] == "DataAnalyst"
    assert audit_spy[0]["round_idx"] == 2
    assert audit_spy[0]["sub_task_index"] == 1


def test_happy_path_json_fenced(pack):
    action = ToolAction(tool_pack=pack)
    ai_msg = """思考中...
```json
{"tool": "add", "args": {"a": 10, "b": 5}}
```
"""
    out = _run(action.run(ai_msg))
    assert out.is_exe_success is True
    assert out.observations == "15"


def test_terminate_tool_sets_terminate_flag(pack):
    action = ToolAction(tool_pack=pack)
    ai_msg = '{"tool": "terminate", "args": {"final_answer": "all good"}}'
    out = _run(action.run(ai_msg))
    assert out.is_exe_success is True
    assert out.terminate is True
    assert out.content == "all good"


def test_unparsable_json_returns_fail(pack, audit_spy):
    action = ToolAction(tool_pack=pack)
    out = _run(action.run("definitely not json"))
    assert out.is_exe_success is False
    assert "JSON" in out.content
    assert len(audit_spy) == 1
    assert audit_spy[0]["tool_name"] == "tool_call"
    assert audit_spy[0]["success"] is False


def test_unparsable_json_with_report_text_fallbacks_to_terminate(pack, audit_spy):
    action = ToolAction(tool_pack=pack)
    ai_msg = (
        "<think>数据已齐全，准备给出报告</think>\n\n"
        "女生成绩统计分析已完成。\n\n"
        "平均分、最高分、最低分、及格率与标准差均已计算完成。"
    )
    out = _run(action.run(ai_msg))
    assert out.is_exe_success is True
    assert out.action == "terminate"
    assert out.terminate is True
    assert "女生成绩统计分析已完成" in out.content
    assert len(audit_spy) == 1
    assert audit_spy[0]["tool_name"] == "terminate"
    assert audit_spy[0]["success"] is True


def test_tool_call_pseudo_syntax_is_parsed_and_invoked(pack):
    action = ToolAction(tool_pack=pack)
    ai_msg = """
<think>先找数据源</think>
[TOOL_CALL]
{tool: "find_related_datasources", args: {
  --question "学生成绩分数分布 各班平均分"
}}
"""
    out = _run(action.run(ai_msg))
    assert out.is_exe_success is True
    assert out.action == "find_related_datasources"
    assert out.observations == "ds-for:学生成绩分数分布 各班平均分"


def test_guard_blocks_describe_table_on_non_locked_table(pack):
    action = ToolAction(tool_pack=pack)
    out = _run(
        action.run(
            '{"tool":"describe_table","args":{"table_name":"student_score"}}',
            constraints={"locked_tables": ["chusan_zhengzhi"]},
        )
    )
    assert out.is_exe_success is False
    assert "约束冲突" in out.content
    assert "chusan_zhengzhi" in out.content


def test_guard_allows_execute_sql_when_locked_table_is_used(pack):
    action = ToolAction(tool_pack=pack)
    out = _run(
        action.run(
            '{"tool":"execute_sql","args":{"sql":"SELECT * FROM chusan_zhengzhi LIMIT 10"}}',
            constraints={"locked_tables": ["chusan_zhengzhi"]},
        )
    )
    assert out.is_exe_success is True
    assert out.action == "execute_sql"


def test_missing_tool_field_returns_fail(pack):
    action = ToolAction(tool_pack=pack)
    out = _run(action.run('{"args": {"a": 1}}'))
    assert out.is_exe_success is False
    assert "tool" in out.content


def test_missing_tool_field_with_final_answer_fallbacks_to_terminate(pack):
    action = ToolAction(tool_pack=pack)
    out = _run(action.run('{"final_answer": "任务已完成"}'))
    assert out.is_exe_success is True
    assert out.action == "terminate"
    assert out.terminate is True
    assert out.content == "任务已完成"


def test_unknown_tool_returns_fail_with_available_list(pack):
    action = ToolAction(tool_pack=pack)
    out = _run(action.run('{"tool": "nope", "args": {}}'))
    assert out.is_exe_success is False
    assert "nope" in out.content
    assert "add" in out.content


def test_bad_args_type_returns_fail(pack):
    action = ToolAction(tool_pack=pack)
    out = _run(action.run('{"tool": "add", "args": "not a dict"}'))
    assert out.is_exe_success is False
    assert "args" in out.content


def test_tool_raises_is_caught(pack):
    action = ToolAction(tool_pack=pack)
    out = _run(action.run('{"tool": "boom", "args": {}}'))
    assert out.is_exe_success is False
    assert "kaboom" in out.content


def test_action_reads_alternative_field_names(pack):
    action = ToolAction(tool_pack=pack)
    ai_msg = '{"reasoning": "t", "action": "add", "arguments": {"a": 3, "b": 4}}'
    out = _run(action.run(ai_msg))
    assert out.is_exe_success is True
    assert out.observations == "7"
    assert out.thoughts == "t"


def test_tool_pack_cannot_be_none():
    with pytest.raises(ValueError):
        ToolAction(tool_pack=None)
