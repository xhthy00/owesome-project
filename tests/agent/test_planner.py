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
