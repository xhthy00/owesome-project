"""PlannerAgent 单元测试。

覆盖：
- 复杂问题 → 多 sub_task 列表；
- 简单问题 → 1 个 sub_task（回落到原问题或 LLM 给出的 single-plan）；
- LLM 输出乱码 → 回落为 [原问题]，不抛；
- plans 超过 6 个 → 截断到 6；
- Profile desc 含 {{question}} 占位符，会被 prompt 渲染替换。
"""

from __future__ import annotations

import asyncio

from src.agent.core.agent import AgentMessage
from src.agent.expand.planner import PLANNER_DESC, PlannerAgent
from src.agent.expand.user_proxy import UserProxyAgent


class _ScriptedLlm:
    def __init__(self, reply: str) -> None:
        self._reply = reply
        self.calls: list[list[dict[str, str]]] = []

    async def chat(self, messages: list[dict[str, str]]) -> str:
        self.calls.append(messages)
        return self._reply


def _run(coro):
    return asyncio.run(coro)


def _ctx(question: str = "对比 Q2 / Q3 销售差异并分析原因"):
    return {"question": question}


def test_planner_splits_complex_question():
    llm = _ScriptedLlm(
        '{"thoughts":"需要对比两个季度","plans":['
        '"查 Q2 的总销售额",'
        '"查 Q3 的总销售额",'
        '"对比差异并定位主要变化品类"'
        "]}"
    )
    agent = PlannerAgent(llm_client=llm)

    reply = _run(
        agent.generate_reply(
            received_message=AgentMessage(
                content="对比 Q2 / Q3 销售差异并分析原因",
                role="user",
                context=_ctx(),
            ),
            sender=UserProxyAgent(),
        )
    )

    plans = reply.action_report.extra["plans"]
    assert len(plans) == 3
    assert "Q2" in plans[0]
    assert "Q3" in plans[1]


def test_planner_single_plan_for_simple_question():
    llm = _ScriptedLlm('{"plans":["用户有多少人"]}')
    agent = PlannerAgent(llm_client=llm)

    reply = _run(
        agent.generate_reply(
            received_message=AgentMessage(
                content="用户有多少人",
                role="user",
                context=_ctx("用户有多少人"),
            ),
            sender=UserProxyAgent(),
        )
    )
    assert reply.action_report.extra["plans"] == ["用户有多少人"]


def test_planner_garbage_falls_back_to_original_question():
    llm = _ScriptedLlm("this is not json")
    agent = PlannerAgent(llm_client=llm)

    reply = _run(
        agent.generate_reply(
            received_message=AgentMessage(
                content="销售额同比",
                role="user",
                context=_ctx("销售额同比"),
            ),
            sender=UserProxyAgent(),
        )
    )
    assert reply.action_report.extra["plans"] == ["销售额同比"]
    assert reply.action_report.is_exe_success is True  # fallback 视为成功


def test_planner_empty_plans_list_falls_back():
    llm = _ScriptedLlm('{"plans":[]}')
    agent = PlannerAgent(llm_client=llm)

    reply = _run(
        agent.generate_reply(
            received_message=AgentMessage(
                content="销售额同比",
                role="user",
                context=_ctx("销售额同比"),
            ),
            sender=UserProxyAgent(),
        )
    )
    assert reply.action_report.extra["plans"] == ["销售额同比"]


def test_planner_truncates_plans_over_limit():
    raw = '{"plans":[' + ",".join(f'"t{i}"' for i in range(10)) + "]}"
    llm = _ScriptedLlm(raw)
    agent = PlannerAgent(llm_client=llm)

    reply = _run(
        agent.generate_reply(
            received_message=AgentMessage(
                content="...",
                role="user",
                context=_ctx(),
            ),
            sender=UserProxyAgent(),
        )
    )
    assert len(reply.action_report.extra["plans"]) == 6


def test_planner_prompt_injects_question():
    llm = _ScriptedLlm('{"plans":["x"]}')
    agent = PlannerAgent(llm_client=llm)

    _run(
        agent.generate_reply(
            received_message=AgentMessage(
                content="某个特定问题 XYZ-123",
                role="user",
                context=_ctx("某个特定问题 XYZ-123"),
            ),
            sender=UserProxyAgent(),
        )
    )
    system_msg = llm.calls[0][0]["content"]
    assert "某个特定问题 XYZ-123" in system_msg
    assert "{{question}}" not in system_msg


def test_planner_accepts_object_plan_with_sub_task_agent():
    llm = _ScriptedLlm(
        '{"plans":['
        '{"task":"先查销量 top3","sub_task_agent":"DataAnalyst"},'
        '{"task":"根据 top3 计算同比百分比","sub_task_agent":"ToolExpert"}'
        "]}"
    )
    agent = PlannerAgent(llm_client=llm)

    reply = _run(
        agent.generate_reply(
            received_message=AgentMessage(
                content="查 top3 并计算同比",
                role="user",
                context=_ctx("查 top3 并计算同比"),
            ),
            sender=UserProxyAgent(),
        )
    )
    assert reply.action_report.extra["plans"] == ["先查销量 top3", "根据 top3 计算同比百分比"]
    assert reply.action_report.extra["plan_agents"] == ["DataAnalyst", "ToolExpert"]


def test_planner_desc_has_question_placeholder():
    assert "{{question}}" in PLANNER_DESC


def test_planner_desc_has_school_item_diagnosis_template():
    assert "学校/班级 + 科目 + 小题" in PLANNER_DESC
    assert "build_subject_diagnosis_sections_tool" in PLANNER_DESC
    assert "范围传递" in PLANNER_DESC
    assert "【XX学校】" in PLANNER_DESC


def test_build_citywide_team_plan_items_one_step():
    from src.agent.expand.planner import build_citywide_team_plan_items

    q = "帮我分析全市的江苏省高一上学期数学期末质量检测试卷的成绩分析，形成详细报告"
    items = build_citywide_team_plan_items(q)
    assert len(items) == 1
    assert items[0]["sub_task_agent"] == "ToolExpert"
    assert "build_diagnostic_report_data_tool" in items[0]["sub_task"]
    assert "fetch_subject_diagnosis_data_tool" not in items[0]["sub_task"]
    assert "build_comprehensive_report_data_tool" not in items[0]["sub_task"]


def test_run_planner_phase_citywide_skips_llm(monkeypatch):
    from src.chat.schemas import ChatRequest
    from src.chat.service.agent_runner import _run_planner_phase

    emitted: list[tuple[str, dict]] = []

    async def _emit(event: str, data: dict) -> None:
        emitted.append((event, data))

    class _FailLlm:
        async def chat(self, messages):
            raise RuntimeError("should not call llm for citywide")

    request = ChatRequest(
        question="帮我分析全市的江苏省高一上学期数学期末质量检测试卷的成绩分析，形成详细报告",
        datasource_id=1,
        agent_mode="team",
    )
    items = asyncio.run(
        _run_planner_phase(
            request=request,
            llm_client=_FailLlm(),
            emit=_emit,
        )
    )
    assert len(items) == 1
    assert any(evt == "agent_speak" and data.get("deterministic") for evt, data in emitted)


def test_individual_student_wrong_school_plan_is_rewritten():
    """个人知识点问法：Planner 误走班级 fetch+sections 时应改写为个人 2 步。"""
    from src.agent.expand.planner import coerce_plan_items_if_needed, should_replace_with_citywide_plan

    q = (
        "查询学生编号为：STU20240003，江苏省高一上学期数学期末质量检测成绩分析，"
        "哪些知识点需要加强，形成分析报告"
    )
    planner_output = [
        {"sub_task": "查询学生整体成绩", "sub_task_agent": "DataAnalyst"},
        {
            "sub_task": "调 fetch_subject_diagnosis_data_tool(subject_name=数学)",
            "sub_task_agent": "ToolExpert",
        },
        {
            "sub_task": "调 build_subject_diagnosis_sections_tool(render=true)",
            "sub_task_agent": "ToolExpert",
        },
    ]
    assert should_replace_with_citywide_plan(q, planner_output) is False
    fixed = coerce_plan_items_if_needed(q, planner_output)
    assert len(fixed) == 2
    assert "build_student_subject_diagnosis_tool" in fixed[1]["sub_task"]
    assert fixed[1]["sub_task"].strip().startswith("调 build_student_subject_diagnosis")


def test_school_report_query_coerced_to_three_steps():
    from src.agent.education.query_parse import is_school_exam_report_query
    from src.agent.expand.planner import (
        build_school_subject_report_plan_items,
        coerce_plan_items_if_needed,
    )

    q = (
        "帮我分析南京市第一中学在江苏省高一上学期数学期末质量检测中的成绩，"
        "进行多维分析形成分析报告"
    )
    assert is_school_exam_report_query(q) is True
    single = [{"sub_task": q, "sub_task_agent": "DataAnalyst"}]
    fixed = coerce_plan_items_if_needed(q, single)
    assert len(fixed) == 3
    assert fixed[0]["sub_task_agent"] == "DataAnalyst"
    assert "fetch_subject_diagnosis_data_tool" in fixed[1]["sub_task"]
    assert "build_subject_diagnosis_sections_tool" in fixed[2]["sub_task"]
    direct = build_school_subject_report_plan_items(q)
    assert "南京市第一中学" in direct[0]["sub_task"]
    assert "数学" in direct[0]["sub_task"]


def test_multi_exam_class_query_coerced_to_comprehensive():
    from src.agent.education.query_parse import (
        is_multi_exam_class_analysis_query,
        is_school_exam_report_query,
    )
    from src.agent.expand.planner import (
        coerce_plan_items_if_needed,
        should_replace_with_comprehensive_plan,
    )

    q = "扬州中学高三(11)班所有数学考试成绩分析"
    assert is_multi_exam_class_analysis_query(q) is True
    assert is_school_exam_report_query(q) is False
    wrong_plan = [
        {"sub_task": "查询 KPI", "sub_task_agent": "DataAnalyst"},
        {
            "sub_task": "调 fetch_subject_diagnosis_data_tool(subject_name=数学)",
            "sub_task_agent": "ToolExpert",
        },
        {
            "sub_task": "调 build_subject_diagnosis_sections_tool(render=true)",
            "sub_task_agent": "ToolExpert",
        },
    ]
    assert should_replace_with_comprehensive_plan(q, wrong_plan) is True
    fixed = coerce_plan_items_if_needed(q, wrong_plan)
    assert len(fixed) == 2
    assert "历次" in fixed[0]["sub_task"] or "考试" in fixed[0]["sub_task"]
    assert "build_comprehensive_report_data_tool" in fixed[1]["sub_task"]
    assert fixed[1]["sub_task_agent"] == "ToolExpert"
    assert "禁止" in fixed[1]["sub_task"] and "build_subject_diagnosis_sections_tool" in fixed[1]["sub_task"]
    assert not fixed[1]["sub_task"].strip().startswith("调 build_subject_diagnosis")


def test_school_class_comparison_query_coerced_to_school_wide():
    from src.agent.education.query_parse import (
        is_school_class_comparison_query,
        is_school_exam_report_query,
    )
    from src.agent.expand.planner import (
        coerce_plan_items_if_needed,
        should_replace_with_school_class_comparison_plan,
    )

    q = "扬州中学在连淮扬镇数学考试中各个班级的横向多维对比分析"
    assert is_school_class_comparison_query(q) is True
    assert is_school_exam_report_query(q) is True
    wrong_plan = [
        {
            "sub_task": "调 fetch_subject_diagnosis_data_tool(school_name=扬州中学, class_name=高三(9)班)",
            "sub_task_agent": "ToolExpert",
        },
        {
            "sub_task": "调 build_subject_diagnosis_sections_tool(class_name=高三(9)班, render=true)",
            "sub_task_agent": "ToolExpert",
        },
    ]
    assert should_replace_with_school_class_comparison_plan(q, wrong_plan) is True
    fixed = coerce_plan_items_if_needed(q, wrong_plan)
    assert len(fixed) == 3
    assert "扬州中学" in fixed[0]["sub_task"]
    assert "连淮扬镇" in fixed[1]["sub_task"] or "连淮扬镇" in fixed[0]["sub_task"]
    blob = " ".join(it["sub_task"] for it in fixed)
    assert "禁止传 class_name" in blob
    assert "class_name=高三" not in blob


def test_planner_infers_tool_expert_for_html_report_task():
    llm = _ScriptedLlm('{"plans":["输出一份 HTML 可视化报告"]}')
    agent = PlannerAgent(llm_client=llm)

    reply = _run(
        agent.generate_reply(
            received_message=AgentMessage(
                content="请输出一份 HTML 可视化报告",
                role="user",
                context=_ctx("请输出一份 HTML 可视化报告"),
            ),
            sender=UserProxyAgent(),
        )
    )
    assert reply.action_report.extra["plans"] == ["输出一份 HTML 可视化报告"]
    assert reply.action_report.extra["plan_agents"] == ["ToolExpert"]


def test_planner_infers_tool_expert_for_visual_analysis_report_task():
    llm = _ScriptedLlm('{"plans":["帮我生成学生成绩可视化分析报告"]}')
    agent = PlannerAgent(llm_client=llm)

    reply = _run(
        agent.generate_reply(
            received_message=AgentMessage(
                content="帮我生成学生成绩可视化分析报告",
                role="user",
                context=_ctx("帮我生成学生成绩可视化分析报告"),
            ),
            sender=UserProxyAgent(),
        )
    )
    assert reply.action_report.extra["plans"] == ["帮我生成学生成绩可视化分析报告"]
    assert reply.action_report.extra["plan_agents"] == ["ToolExpert"]
