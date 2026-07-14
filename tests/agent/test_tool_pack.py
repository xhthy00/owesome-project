"""ToolPack 单元测试：注册、查找、绑定、渲染、调用。"""

from __future__ import annotations

import asyncio

import pytest

from src.agent.resource.tool.builtin import TerminateTool
from src.agent.resource.tool.function_tool import tool
from src.agent.resource.tool.pack import ToolNotFoundError, ToolPack


def _run(coro):
    return asyncio.run(coro)


@tool()
def add(a: int, b: int) -> int:
    """Add two ints."""
    return a + b


@tool()
def use_session(session: str, key: str) -> str:
    """Return 'session:key'."""
    return f"{session}:{key}"


def test_register_and_lookup_tools():
    pack = ToolPack(tools=[add, TerminateTool()])
    assert len(pack) == 2
    assert "add" in pack
    assert "terminate" in pack
    assert pack.get("add") is add


def test_invoke_tool_with_args():
    pack = ToolPack(tools=[add])
    result = _run(pack.invoke("add", {"a": 2, "b": 5}))
    assert result.data == 7


def test_unknown_tool_raises():
    pack = ToolPack(tools=[add])
    with pytest.raises(ToolNotFoundError):
        _run(pack.invoke("missing", {}))


def test_duplicate_registration_raises():
    pack = ToolPack(tools=[add])
    with pytest.raises(ValueError):
        pack.register(add)


def test_bindings_inject_into_tool_call():
    pack = ToolPack(tools=[use_session]).bind(session="s-42")
    result = _run(pack.invoke("use_session", {"key": "user"}))
    assert result.data == "s-42:user"


def test_bind_returns_new_instance_and_preserves_original():
    pack = ToolPack(tools=[use_session])
    bound = pack.bind(session="s-1")
    assert pack.bindings == {}
    assert bound.bindings == {"session": "s-1"}
    assert "use_session" in bound


def test_bindings_override_args():
    """运行时 bindings 必须盖过 LLM args，避免 null/错误上下文冲掉注入值。"""
    pack = ToolPack(tools=[use_session]).bind(session="bound")
    result = _run(pack.invoke("use_session", {"session": "explicit", "key": "k"}))
    assert result.data == "bound:k"


def test_render_prompt_contains_tools_and_params():
    pack = ToolPack(tools=[add, TerminateTool()])
    text = pack.render_prompt()
    assert "add:" in text
    assert "terminate:" in text
    assert "a(integer" in text
    assert "final_answer(string" in text


def test_render_prompt_empty():
    pack = ToolPack()
    assert "无可用工具" in pack.render_prompt()


def test_render_prompt_hides_bound_parameters():
    pack = ToolPack(tools=[use_session]).bind(session="s-1")
    text = pack.render_prompt()
    assert "session(" not in text
    assert "key(" in text


def test_render_prompt_tool_with_all_bound_params_shows_only_name():
    @tool()
    def full_bound(a: int, b: int) -> int:
        """Only used when all params bound."""
        return a + b

    pack = ToolPack(tools=[full_bound]).bind(a=1, b=2)
    text = pack.render_prompt()
    assert "full_bound:" in text
    assert "a(" not in text
    assert "b(" not in text
