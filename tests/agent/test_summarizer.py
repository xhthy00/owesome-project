"""SummarizerAgent 单元测试。

Summarizer 无 Action：thinking 的原文即结论。主要验证：
- LLM 输出被原样回填到 reply.content；
- Profile desc 含 runner 会注入的模板变量；
- prompt 里确实替换了 context 变量；
- 事实问答 / 报告题使用不同写作 brief。
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
        "answer_mode": "report",
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
    assert "{{writing_brief}}" not in system_msg


def test_summarizer_desc_has_required_template_vars():
    for var in ("{{question}}", "{{sub_tasks_block}}", "{{writing_brief}}", "{{answer_mode}}"):
        assert var in SUMMARIZER_DESC, f"Summarizer desc missing {var}"


def test_summarizer_report_mode_has_edu_structure():
    llm = _ScriptedLlm("ok")
    agent = SummarizerAgent(llm_client=llm)
    _run(
        agent.generate_reply(
            received_message=AgentMessage(
                content="q", role="user", context=_ctx(answer_mode="report")
            ),
            sender=UserProxyAgent(),
        )
    )
    system_msg = llm.calls[0][0]["content"]
    assert "学情总判" in system_msg
    assert "关键指标" in system_msg
    assert "教学建议" in system_msg
    assert "OFFSET" in system_msg or "LIMIT" in system_msg


def test_summarizer_fact_mode_forbids_report_template():
    llm = _ScriptedLlm("最高分为 STU1，124 分。")
    agent = SummarizerAgent(llm_client=llm)
    _run(
        agent.generate_reply(
            received_message=AgentMessage(
                content="高二(6)班数学成绩最好的学生",
                role="user",
                context=_ctx(
                    question="高二(6)班数学成绩最好的学生",
                    answer_mode="fact",
                    sub_tasks_block="共 5 行（Top-N）",
                ),
            ),
            sender=UserProxyAgent(),
        )
    )
    system_msg = llm.calls[0][0]["content"]
    assert "事实问答" in system_msg
    assert "禁止**套用「学情总判" in system_msg or "禁止套用「学情总判" in system_msg
    assert "参考人数" in system_msg  # 出现在禁止项里
    assert "作答模式：fact" in system_msg
    # 报告题专属长结构不应出现在 fact brief
    assert "400~900" not in system_msg
