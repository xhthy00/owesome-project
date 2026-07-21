"""SummarizerAgent 单元测试。

Summarizer 无 Action：thinking 的原文即结论。主要验证：
- LLM 输出被原样回填到 reply.content；
- Profile desc 含 runner 会注入的模板变量；
- prompt 里确实替换了 context 变量。
"""

from __future__ import annotations

import asyncio

from src.agent.core.agent import AgentMessage
from src.agent.expand.summarizer import SUMMARIZER_DESC, SummarizerAgent
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
        "question": "本月订单最多的三个用户",
        "sub_tasks_block": (
            "### 子任务 1：本月订单最多的三个用户\n"
            "SQL:\n```sql\nSELECT user_id, COUNT(*) AS n FROM orders GROUP BY 1 ORDER BY n DESC LIMIT 3\n```\n"
            "共 3 行，列：user_id, n\n"
            "样例：\n"
            "| user_id | n |\n"
            "| --- | --- |\n"
            "| 7 | 42 |\n"
            "| 3 | 30 |\n"
            "| 9 | 25 |"
        ),
    }
    base.update(overrides)
    return base


def test_summarizer_thinking_becomes_content():
    llm = _ScriptedLlm("本月订单最多的前三名用户是 7、3、9，分别下单 42、30、25 次。")
    agent = SummarizerAgent(llm_client=llm)

    reply = _run(
        agent.generate_reply(
            received_message=AgentMessage(content="...", role="user", context=_ctx()),
            sender=UserProxyAgent(),
        )
    )

    assert reply.content == "本月订单最多的前三名用户是 7、3、9，分别下单 42、30、25 次。"
    # 没有 Action 链时，基类 act 会产生一个 is_exe_success=True 的 ActionOutput
    assert reply.action_report is not None
    assert reply.action_report.is_exe_success is True


def test_summarizer_prompt_injects_context_variables():
    llm = _ScriptedLlm("结果正常。")
    agent = SummarizerAgent(llm_client=llm)
    ctx = _ctx(question="近 7 日 DAU")

    _run(
        agent.generate_reply(
            received_message=AgentMessage(content="any", role="user", context=ctx),
            sender=UserProxyAgent(),
        )
    )

    system_msg = llm.calls[0][0]["content"]
    assert "近 7 日 DAU" in system_msg
    # sub_tasks_block 里的内容也必须真正注入到 prompt
    assert "SELECT user_id, COUNT(*)" in system_msg
    assert "{{question}}" not in system_msg
    assert "{{sub_tasks_block}}" not in system_msg


def test_summarizer_desc_has_required_template_vars():
    for var in ("{{question}}", "{{sub_tasks_block}}"):
        assert var in SUMMARIZER_DESC, f"Summarizer desc missing {var}"


def test_summarizer_desc_forbids_offset_page_as_class_size():
    assert "OFFSET" in SUMMARIZER_DESC
    assert "样例" in SUMMARIZER_DESC
    assert "权威统计" in SUMMARIZER_DESC
