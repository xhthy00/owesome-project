"""ReActAgent 主循环测试。

核心断言：
- 多轮 (thinking -> tool_call -> observation) 的轨迹正确；
- observation 被回灌到下一轮 LLM 的 messages 里；
- terminate 工具终结循环并把 final_answer 写入 reply.content；
- 工具业务失败 != 框架失败：失败时仍走回灌 + 下一轮，而不是 ConversableAgent
  基类的"重来同一轮"。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from src.agent.core.agent import AgentMessage
from src.agent.core.profile import ProfileConfig
from src.agent.core.react_agent import ReActAgent
from src.agent.expand.user_proxy import UserProxyAgent
from src.agent.resource.tool.base import ToolResult
from src.agent.resource.tool.builtin import TerminateTool
from src.agent.resource.tool.function_tool import tool
from src.agent.resource.tool.pack import ToolPack


class FakeLlmClient:
    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)
        self.calls: list[list[dict[str, str]]] = []

    async def chat(self, messages: list[dict[str, str]]) -> str:
        self.calls.append(messages)
        if not self._replies:
            return ""
        if len(self._replies) == 1:
            return self._replies[0]
        return self._replies.pop(0)


def _run(coro):
    return asyncio.run(coro)


class _TrivialReActAgent(ReActAgent):
    profile = ProfileConfig(
        name="Tester",
        role="Tester",
        goal="测试 ReAct 主循环。",
        desc="[可用工具]\n{{tools_prompt}}\n\n严格 JSON 输出。",
    )


@tool()
def list_things() -> ToolResult:
    """List things."""
    return ToolResult(content="[alpha, beta]", data=["alpha", "beta"])


@tool()
def flaky_lookup(name: str) -> ToolResult:
    """Fail first, succeed on 'beta'."""
    if name == "alpha":
        return ToolResult(content="ERROR: alpha unavailable", data={"error": "alpha unavailable"})
    return ToolResult(content=f"info-of-{name}", data={"name": name})


def _pack_with(*tools: Any) -> ToolPack:
    return ToolPack(tools=[*tools, TerminateTool()])


def test_react_terminates_in_single_round_when_llm_calls_terminate():
    llm = FakeLlmClient(
        ['{"thoughts": "直接结束", "tool": "terminate", "args": {"final_answer": "done"}}']
    )
    agent = _TrivialReActAgent(llm_client=llm, tool_pack=_pack_with())

    reply = _run(
        agent.generate_reply(
            received_message=AgentMessage(content="hi", role="user"),
            sender=UserProxyAgent(),
        )
    )

    assert reply.content == "done"
    assert reply.rounds == 1
    assert reply.action_report.terminate is True
    assert reply.action_report.is_exe_success is True
    assert len(llm.calls) == 1


def test_react_multi_round_feeds_observation_into_next_prompt():
    llm = FakeLlmClient(
        [
            '{"thoughts": "先列一下", "tool": "list_things", "args": {}}',
            '{"thoughts": "拿到后结束", "tool": "terminate", "args": {"final_answer": "have alpha/beta"}}',
        ]
    )
    agent = _TrivialReActAgent(llm_client=llm, tool_pack=_pack_with(list_things))

    reply = _run(
        agent.generate_reply(
            received_message=AgentMessage(content="列出所有", role="user"),
            sender=UserProxyAgent(),
        )
    )

    assert reply.content == "have alpha/beta"
    assert reply.rounds == 2
    assert len(llm.calls) == 2

    second_call_messages = llm.calls[1]
    roles_and_contents = [(m["role"], m["content"]) for m in second_call_messages]
    assert any(
        role == "user" and "observation from list_things" in content and "[alpha, beta]" in content
        for role, content in roles_and_contents
    )
    assert any(
        role == "assistant" and "list_things" in content
        for role, content in roles_and_contents
    )


def test_react_tool_business_failure_is_fed_as_observation_not_retried_in_place():
    llm = FakeLlmClient(
        [
            '{"tool": "flaky_lookup", "args": {"name": "alpha"}}',
            '{"tool": "flaky_lookup", "args": {"name": "beta"}}',
            '{"tool": "terminate", "args": {"final_answer": "got beta"}}',
        ]
    )
    agent = _TrivialReActAgent(llm_client=llm, tool_pack=_pack_with(flaky_lookup))

    reply = _run(
        agent.generate_reply(
            received_message=AgentMessage(content="lookup", role="user"),
            sender=UserProxyAgent(),
        )
    )

    assert reply.content == "got beta"
    assert reply.rounds == 3
    round_2_msgs = llm.calls[1]
    assert any(
        "observation from flaky_lookup" in m["content"] and "ERROR: alpha unavailable" in m["content"]
        for m in round_2_msgs
    )


def test_react_framework_error_feeds_observation_and_recovers():
    llm = FakeLlmClient(
        [
            "this is not JSON at all",
            '{"tool": "terminate", "args": {"final_answer": "recovered"}}',
        ]
    )
    agent = _TrivialReActAgent(llm_client=llm, tool_pack=_pack_with())

    reply = _run(
        agent.generate_reply(
            received_message=AgentMessage(content="q", role="user"),
            sender=UserProxyAgent(),
        )
    )

    assert reply.content == "recovered"
    assert reply.rounds == 2
    round_2 = llm.calls[1]
    assert any("observation from" in m["content"] and "JSON" in m["content"] for m in round_2)


def test_react_exceeds_max_rounds_auto_terminates_when_last_observation_is_valid():
    llm = FakeLlmClient(['{"tool": "list_things", "args": {}}'])
    agent = _TrivialReActAgent(
        llm_client=llm,
        tool_pack=_pack_with(list_things),
        max_react_rounds=3,
    )

    reply = _run(
        agent.generate_reply(
            received_message=AgentMessage(content="hmm", role="user"),
            sender=UserProxyAgent(),
        )
    )

    assert reply.action_report.is_exe_success is True
    assert reply.action_report.terminate is True
    assert reply.action_report.have_retry is False
    assert "[alpha, beta]" in reply.action_report.content
    assert reply.rounds == 3
    assert len(llm.calls) == 3


def test_react_exceeds_max_rounds_still_fails_when_last_action_failed():
    llm = FakeLlmClient(["this is not JSON at all"])
    agent = _TrivialReActAgent(
        llm_client=llm,
        tool_pack=_pack_with(list_things),
        max_react_rounds=2,
    )

    reply = _run(
        agent.generate_reply(
            received_message=AgentMessage(content="hmm", role="user"),
            sender=UserProxyAgent(),
        )
    )

    assert reply.action_report.is_exe_success is False
    assert reply.action_report.have_retry is False
    assert "未调用 terminate" in reply.action_report.content


def test_react_requires_tool_pack():
    with pytest.raises(ValueError):
        _TrivialReActAgent(llm_client=FakeLlmClient([]), tool_pack=None)  # type: ignore[arg-type]


def test_react_repeat_tool_soft_warning_injected_after_third_call():
    """连续 3 次同工具 → 第 3 轮 observation 前应追加软警告；
    但 SSE tool_result / action_out.observations 必须保持干净（前端需原始信号）。
    """
    from src.agent.core.react_agent import _REPEAT_TOOL_WARN_THRESHOLD

    assert _REPEAT_TOOL_WARN_THRESHOLD == 3, "本测试依赖阈值为 3，若改动需同步调"

    llm = FakeLlmClient(
        [
            '{"tool": "list_things", "args": {}}',
            '{"tool": "list_things", "args": {}}',
            '{"tool": "list_things", "args": {}}',
            '{"tool": "terminate", "args": {"final_answer": "ok"}}',
        ]
    )
    agent = _TrivialReActAgent(llm_client=llm, tool_pack=_pack_with(list_things))

    captured_tool_results: list[dict] = []

    async def capture(event: str, payload: dict) -> None:
        if event == "tool_result":
            captured_tool_results.append(payload)

    agent.stream_callback = capture

    _run(
        agent.generate_reply(
            received_message=AgentMessage(content="q", role="user"),
            sender=UserProxyAgent(),
        )
    )

    # round 1/2 的下一轮上下文不应含警告；round 3 产生的 observation（喂到
    # round 4 LLM input）才应带警告。
    round2_input = llm.calls[1]  # round 2 LLM 看到的 messages（含 round 1 的 trace）
    round3_input = llm.calls[2]
    round4_input = llm.calls[3]

    def _has_warning(messages: list[dict]) -> bool:
        return any("连续" in m.get("content", "") and "list_things" in m.get("content", "") for m in messages)

    assert not _has_warning(round2_input), "第 1 次调用后不应触发警告"
    assert not _has_warning(round3_input), "第 2 次调用后还不应触发警告（阈值是 3）"
    assert _has_warning(round4_input), (
        "第 3 次同工具调用后，下一轮 LLM input 应包含软警告"
    )

    # SSE payload 保持干净——前端不应看到"警告"污染 tool 返回信号。
    assert len(captured_tool_results) >= 3
    for payload in captured_tool_results[:3]:
        assert "连续" not in (payload.get("content") or ""), (
            f"tool_result SSE payload 被警告污染：{payload.get('content')!r}"
        )


def test_react_different_tools_do_not_trigger_warning():
    """交替调不同工具不应触发 streak 警告——streak 应只计连续同名。"""
    llm = FakeLlmClient(
        [
            '{"tool": "list_things", "args": {}}',
            '{"tool": "flaky_lookup", "args": {"name": "beta"}}',
            '{"tool": "list_things", "args": {}}',
            '{"tool": "flaky_lookup", "args": {"name": "beta"}}',
            '{"tool": "terminate", "args": {"final_answer": "ok"}}',
        ]
    )
    agent = _TrivialReActAgent(
        llm_client=llm,
        tool_pack=_pack_with(list_things, flaky_lookup),
    )

    _run(
        agent.generate_reply(
            received_message=AgentMessage(content="q", role="user"),
            sender=UserProxyAgent(),
        )
    )

    for call_msgs in llm.calls:
        for m in call_msgs:
            assert "连续" not in m.get("content", ""), (
                "交替调工具不应产生连续警告，但警告出现在："
                f"{m.get('content')[:100]!r}"
            )


def test_react_injects_tools_prompt_into_system_message():
    llm = FakeLlmClient(['{"tool": "terminate", "args": {"final_answer": "x"}}'])
    agent = _TrivialReActAgent(llm_client=llm, tool_pack=_pack_with(list_things))

    _run(
        agent.generate_reply(
            received_message=AgentMessage(content="q", role="user"),
            sender=UserProxyAgent(),
        )
    )

    system = llm.calls[0][0]
    assert system["role"] == "system"
    assert "list_things" in system["content"]
    assert "terminate" in system["content"]
    assert "{{tools_prompt}}" not in system["content"]
