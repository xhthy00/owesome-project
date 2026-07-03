"""ToolExpert Agent prompt 注入与上游数据复用测试。"""

from __future__ import annotations

import asyncio

from src.agent.core.agent import AgentMessage
from src.agent.expand.tool_agent import ToolAgent, build_tool_agent, _format_upstream_report_data
from src.agent.expand.user_proxy import UserProxyAgent
from src.agent.resource.tool.business import build_default_toolpack


class _ScriptedLlm:
    def __init__(self, replies: list[str]) -> None:
        self._q = list(replies)
        self.calls: list[list[dict[str, str]]] = []

    async def chat(self, messages: list[dict[str, str]]) -> str:
        self.calls.append(messages)
        return self._q.pop(0)


def _run(coro):
    return asyncio.run(coro)


def test_tool_agent_profile_has_scope_and_upstream_placeholders():
    assert "{{scope_constraints}}" in ToolAgent.profile.desc
    assert "{{upstream_report_data}}" in ToolAgent.profile.desc
    assert "禁止" in ToolAgent.profile.desc and "execute_sql" in ToolAgent.profile.desc


def test_format_upstream_report_data_includes_exec_result():
    text = _format_upstream_report_data({
        "sub_tasks": [
            {
                "sub_task_index": 0,
                "sub_task": "查询南京市第一中学数学成绩",
                "sub_task_agent": "DataAnalyst",
                "sql": "SELECT school, score FROM t WHERE school='南京市第一中学'",
                "exec_result": {
                    "columns": ["school", "score"],
                    "rows": [{"school": "南京市第一中学", "score": 88}],
                    "row_count": 24,
                },
                "final_answer": "共 24 人，均分 78.5",
            }
        ]
    })
    assert "南京市第一中学" in text
    assert "行数：24" in text
    assert "共 24 人" in text


def test_tool_agent_injects_scope_and_upstream_in_system_prompt():
    llm = _ScriptedLlm(['{"tool": "terminate", "args": {"final_answer": "ok"}}'])
    agent = build_tool_agent(
        llm_client=llm,
        tool_pack=build_default_toolpack(datasource_id=1),
    )

    _run(
        agent.generate_reply(
            received_message=AgentMessage(
                content="用 subject_diagnosis 模板组装 HTML 报告",
                role="user",
                context={
                    "constraints": {
                        "target_school": "南京市第一中学",
                        "required_keywords": ["数学", "期末"],
                        "report_data": {
                            "sub_tasks": [
                                {
                                    "sub_task_index": 0,
                                    "sub_task": "查小题得分",
                                    "sub_task_agent": "DataAnalyst",
                                    "exec_result": {
                                        "columns": ["item", "avg"],
                                        "rows": [{"item": "1", "avg": 3.2}],
                                        "row_count": 24,
                                    },
                                    "final_answer": "共 24 人",
                                }
                            ]
                        },
                    }
                },
            ),
            sender=UserProxyAgent(),
        )
    )

    system_msg = llm.calls[0][0]["content"]
    assert "南京市第一中学" in system_msg
    assert "共 24 人" in system_msg
    assert "禁止" in system_msg
    assert "{{scope_constraints}}" not in system_msg
    assert "{{upstream_report_data}}" not in system_msg
