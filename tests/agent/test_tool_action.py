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


@tool()
def render_html_report(template_name: str = "", data: dict | None = None, title: str = "Report", html: str = "") -> dict:
    """Render an HTML report (test stub)."""
    return {"template_name": template_name, "data": data or {}, "title": title, "html": html or "<html>ok</html>"}


@pytest.fixture()
def pack():
    return ToolPack(tools=[add, find_related_datasources, describe_table, execute_sql, render_html_report, TerminateTool(), boom])


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


def test_locked_tables_constraint_kwarg_is_ignored_describe_any_table(pack):
    """已不再根据 constraints.locked_tables 拦截工具调用。"""
    action = ToolAction(tool_pack=pack)
    out = _run(
        action.run(
            '{"tool":"describe_table","args":{"table_name":"student_score"}}',
            constraints={"locked_tables": ["chusan_zhengzhi"]},
        )
    )
    assert out.is_exe_success is True
    assert out.observations == "describe:student_score"


def test_execute_sql_succeeds_even_when_constraints_list_other_locked_tables(pack):
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


def test_missing_tool_field_with_report_key_fallbacks_to_terminate(pack):
    """模型把报告正文塞进 report 字段（未走 tool 协议）时优雅 terminate。"""
    action = ToolAction(tool_pack=pack)
    out = _run(action.run('{"report": "学情报告：均分 78，及格率 90%"}'))
    assert out.is_exe_success is True
    assert out.action == "terminate"
    assert out.terminate is True
    assert "均分 78" in out.content


def test_missing_tool_field_with_render_html_report_args_rescues_to_render(pack, audit_spy):
    """模型漏掉 tool 外壳、直接返回 render_html_report 的 args 对象时自动补调。"""
    action = ToolAction(tool_pack=pack)
    ai_msg = '{"template_name": "education/student_profile.html", "data": {"REPORT_TITLE": "张三学情"}, "title": "张三学情报告"}'
    out = _run(action.run(ai_msg))
    assert out.is_exe_success is True
    assert out.action == "render_html_report"
    assert out.terminate is False
    assert out.extra["tool_args"]["template_name"] == "education/student_profile.html"
    assert out.extra["tool_data"]["template_name"] == "education/student_profile.html"
    assert audit_spy[0]["tool_name"] == "render_html_report"
    assert audit_spy[0]["success"] is True


def test_missing_tool_field_with_inline_html_rescues_to_render(pack):
    """模型直接返回 {"html": "..."} 时也走 render_html_report 而非 terminate。"""
    action = ToolAction(tool_pack=pack)
    out = _run(action.run('{"html": "<html>inline</html>", "title": "R"}'))
    assert out.is_exe_success is True
    assert out.action == "render_html_report"
    assert out.extra["tool_data"]["html"] == "<html>inline</html>"


def test_scalar_string_fallbacks_to_terminate(pack):
    """模型直接返回一段自然语言（标量字符串）时作为最终答案 terminate。"""
    action = ToolAction(tool_pack=pack)
    out = _run(action.run('"分析完成：班级总体表现良好"'))
    assert out.is_exe_success is True
    assert out.action == "terminate"
    assert out.terminate is True
    assert "班级总体表现良好" in out.content


def test_array_output_still_fails_gracefully(pack):
    """数组输出无法兜底为最终答案，仍按失败返回。"""
    action = ToolAction(tool_pack=pack)
    out = _run(action.run('["a", "b"]'))
    assert out.is_exe_success is False
    assert "JSON 对象" in out.content


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


def test_sanitize_strips_hand_filled_records():
    from src.agent.core.action.tool_action import _sanitize_report_tool_args

    out = _sanitize_report_tool_args(
        "build_comprehensive_report_data_tool",
        {
            "class_name": "高三（10）班",
            "records": [{"exam": "摸底", "student": "s1", "total": 90}] * 50,
            "report_data": None,
            "tool_runtime_ctx": {},
        },
        constraints={"target_classes": ["高三（10）班"]},
        sub_task="用 education/comprehensive.html 组装报告",
    )
    assert out == {"class_name": "高三（10）班"}
    assert "records" not in out
    assert "report_data" not in out


def test_sanitize_fills_class_name_from_sub_task():
    from src.agent.core.action.tool_action import _sanitize_report_tool_args

    out = _sanitize_report_tool_args(
        "build_comprehensive_report_data_tool",
        {},
        sub_task="调 build_comprehensive_report_data_tool 为高三（10）班生成综合分析 HTML",
    )
    assert out.get("class_name") == "高三（10）班"
    assert "records" not in out


def test_json_truncate_rescues_comprehensive_report(monkeypatch, audit_spy):
    """LLM 手填 208 条 records 导致 JSON 截断时，自动空参调综合报告工具。"""

    calls: list[dict] = []

    @tool()
    def build_comprehensive_report_data_tool(
        class_name: str = "",
        records: list | None = None,
        report_data: dict | None = None,
        tool_runtime_ctx: dict | None = None,
    ) -> dict:
        """Build comprehensive report."""
        calls.append(
            {
                "class_name": class_name,
                "records": records,
                "has_report_data": bool(report_data),
                "has_ctx": bool(tool_runtime_ctx),
            }
        )
        assert records is None  # 救援路径不得再接手填表
        assert report_data and tool_runtime_ctx
        return {"ok": True, "html": "<html>report</html>"}

    report_data = {
        "sub_tasks": [
            {
                "sub_task_agent": "DataAnalyst",
                "exec_result": {
                    "columns": ["exam_name", "student_id", "score"],
                    "rows": [[f"考{e}", f"S{s}", 90] for e in range(2) for s in range(3)],
                    "row_count": 6,
                },
            }
        ]
    }
    ctx = {"last_exec_result": report_data["sub_tasks"][0]["exec_result"], "report_data": report_data}
    pack = ToolPack(
        tools=[build_comprehensive_report_data_tool, TerminateTool()],
        bindings={"report_data": report_data, "tool_runtime_ctx": ctx},
    )
    action = ToolAction(tool_pack=pack)
    # 模拟截断：以未闭合引号开头，parse 失败
    ai_msg = (
        '<think>I have the data. Now call build_comprehensive_report_data_tool with records...</think>\n'
        '{"tool": "build_comprehensive_report_data_tool", "args": {"records": [{"exam": "摸底"'
    )
    out = _run(
        action.run(
            ai_msg,
            sub_task="用 education/comprehensive.html 模板组装 HTML 报告",
            constraints={"target_classes": ["高三（10）班"]},
        )
    )
    assert out.is_exe_success is True
    assert out.action == "build_comprehensive_report_data_tool"
    assert out.extra.get("rescued_report") is True
    assert len(calls) == 1
    assert calls[0]["class_name"] == "高三（10）班"


def test_comprehensive_strips_records_before_invoke():
    """正常解析成功时也剥离 records，改走 bindings 全量数据。"""

    seen: dict = {}

    @tool()
    def build_comprehensive_report_data_tool(
        class_name: str = "",
        records: list | None = None,
        report_data: dict | None = None,
        tool_runtime_ctx: dict | None = None,
    ) -> dict:
        """Build comprehensive report."""
        seen["class_name"] = class_name
        seen["records"] = records
        seen["report_data"] = report_data
        return {"ok": True}

    report_data = {"sub_tasks": [{"sub_task_agent": "DataAnalyst", "exec_result": {
        "columns": ["exam_name", "student_id", "score"],
        "rows": [["摸底", "S1", 90]],
        "row_count": 1,
    }}]}
    pack = ToolPack(
        tools=[build_comprehensive_report_data_tool, TerminateTool()],
        bindings={
            "report_data": report_data,
            "tool_runtime_ctx": {"report_data": report_data, "last_exec_result": report_data["sub_tasks"][0]["exec_result"]},
        },
    )
    action = ToolAction(tool_pack=pack)
    ai_msg = (
        '{"tool": "build_comprehensive_report_data_tool", '
        '"args": {"class_name": "高三（10）班", "records": [{"exam": "摸底", "student": "x", "total": 1}]}}'
    )
    out = _run(action.run(ai_msg, sub_task="综合分析报告"))
    assert out.is_exe_success is True
    assert seen["records"] is None
    assert seen["class_name"] == "高三（10）班"
    assert seen["report_data"] is report_data


def test_bindings_win_over_llm_args():
    from src.agent.resource.tool.pack import ToolPack

    @tool()
    def echo_ctx(report_data: dict | None = None, x: int = 0) -> dict:
        """Echo."""
        return {"report_data": report_data, "x": x}

    pack = ToolPack(tools=[echo_ctx], bindings={"report_data": {"sub_tasks": [1]}})
    result = _run(pack.invoke("echo_ctx", {"report_data": None, "x": 7}))
    assert result.data["x"] == 7
    assert result.data["report_data"] == {"sub_tasks": [1]}
