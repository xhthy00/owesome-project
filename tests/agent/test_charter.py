"""CharterAgent 单元测试。

覆盖：
- happy path: JSON 正常 → 正确解析 chart_type / chart_config
- 未知 chart_type → fallback 到 table
- 非 dict / 非法 JSON → fallback 到 table
- chart_config 缺字段 → 补默认
- Profile desc 含 {{question}} 等模板变量（保证 runner 能注入）
"""

from __future__ import annotations

import asyncio

from src.agent.core.agent import AgentMessage
from src.agent.expand.charter import CHARTER_DESC, CharterAgent
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


def _ctx(**overrides):
    base = {
        "question": "每月销售额趋势",
        "sql": "SELECT month, amount FROM sales",
        "columns": "month, amount",
        "row_count": 12,
        "sample_rows": "| month | amount |\n| --- | --- |\n| 2025-01 | 100 |",
    }
    base.update(overrides)
    return base


def test_charter_happy_path_line_chart():
    llm = _ScriptedLlm(
        '{"thoughts":"时间序列","chart_type":"line",'
        '"chart_config":{"x":"month","y":["amount"],"title":"每月销售趋势"}}'
    )
    agent = CharterAgent(llm_client=llm)

    reply = _run(
        agent.generate_reply(
            received_message=AgentMessage(content="...", role="user", context=_ctx()),
            sender=UserProxyAgent(),
        )
    )

    ar = reply.action_report
    assert ar is not None and ar.is_exe_success is True
    assert ar.extra["chart_type"] == "line"
    assert ar.extra["chart_config"] == {
        "x": "month",
        "y": ["amount"],
        "title": "每月销售趋势",
    }


def test_charter_unknown_type_falls_back_to_table():
    llm = _ScriptedLlm('{"chart_type":"radar","chart_config":{}}')
    agent = CharterAgent(llm_client=llm)

    reply = _run(
        agent.generate_reply(
            received_message=AgentMessage(content="...", role="user", context=_ctx()),
            sender=UserProxyAgent(),
        )
    )

    assert reply.action_report.is_exe_success is True
    assert reply.action_report.extra["chart_type"] == "table"


def test_charter_invalid_json_falls_back():
    llm = _ScriptedLlm("not a json at all")
    agent = CharterAgent(llm_client=llm)

    reply = _run(
        agent.generate_reply(
            received_message=AgentMessage(content="...", role="user", context=_ctx()),
            sender=UserProxyAgent(),
        )
    )

    assert reply.action_report.is_exe_success is True
    assert reply.action_report.extra["chart_type"] == "table"
    assert reply.action_report.extra["chart_config"] == {
        "x": "",
        "y": [],
        "title": "",
    }


def test_charter_missing_chart_config_uses_defaults():
    llm = _ScriptedLlm('{"chart_type":"bar"}')
    agent = CharterAgent(llm_client=llm)

    reply = _run(
        agent.generate_reply(
            received_message=AgentMessage(content="...", role="user", context=_ctx()),
            sender=UserProxyAgent(),
        )
    )

    assert reply.action_report.extra["chart_type"] == "bar"
    assert reply.action_report.extra["chart_config"] == {
        "x": "",
        "y": [],
        "title": "",
    }


def test_charter_prompt_injects_context_variables():
    """系统 prompt 必须包含从 context 注入的 question / sql / columns。"""
    llm = _ScriptedLlm('{"chart_type":"table"}')
    agent = CharterAgent(llm_client=llm)

    ctx = _ctx(question="本月订单 TOP 5 用户", sql="SELECT u.name FROM orders")
    _run(
        agent.generate_reply(
            received_message=AgentMessage(content="any", role="user", context=ctx),
            sender=UserProxyAgent(),
        )
    )

    assert llm.calls, "LLM should have been called at least once"
    system_msg = llm.calls[0][0]["content"]
    assert "本月订单 TOP 5 用户" in system_msg
    assert "SELECT u.name FROM orders" in system_msg
    # 原始模板占位符必须全部被替换
    assert "{{question}}" not in system_msg
    assert "{{sql}}" not in system_msg


def test_charter_desc_has_required_template_vars():
    """防回归：Profile desc 必须含所有 runner 会注入的变量。"""
    for var in ("{{question}}", "{{sql}}", "{{columns}}", "{{row_count}}", "{{sample_rows}}"):
        assert var in CHARTER_DESC, f"Charter desc missing {var}"


def test_charter_desc_prefers_question_metric_over_headcount():
    """Y 轴须贴问题，禁止默认画参考人数，比率不与人数混轴。"""
    for phrase in (
        "用户问题关心的指标",
        "参考人数",
        "candidates",
        "达线率",
        "量纲不同",
    ):
        assert phrase in CHARTER_DESC, f"Charter desc missing {phrase!r}"
