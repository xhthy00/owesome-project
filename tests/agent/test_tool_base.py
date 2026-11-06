"""BaseTool / FunctionTool / @tool / TerminateTool 单元测试。"""

from __future__ import annotations

import asyncio

import pytest

from src.agent.resource.tool.base import BaseTool, ToolParameter, ToolResult
from src.agent.resource.tool.function_tool import FunctionTool, tool


def _run(coro):
    return asyncio.run(coro)


def test_function_tool_from_sync_function_wraps_in_thread():
    def add(a: int, b: int) -> int:
        """Add two numbers.

        Args:
            a: left operand
            b: right operand
        """
        return a + b

    t = FunctionTool(fn=add)
    assert t.name == "add"
    assert t.description.startswith("Add two numbers")
    by_name = {p.name: p for p in t.parameters}
    assert by_name["a"].type == "integer" and by_name["a"].required is True

    result = _run(t.execute(a=2, b=3))
    assert isinstance(result, ToolResult)
    assert result.data == 5
    assert result.content == "5"
    assert result.is_final is False


def test_function_tool_from_async_function():
    async def async_hello(name: str) -> str:
        return f"hello {name}"

    t = FunctionTool(fn=async_hello, description="say hello")
    result = _run(t.execute(name="world"))
    assert result.content == "hello world"


def test_function_tool_filters_unknown_kwargs():
    @tool()
    def only_a(a: int) -> int:
        return a * 10

    result = _run(only_a.execute(a=3, bogus="x"))
    assert result.data == 30


def test_function_tool_returning_tool_result_passes_through():
    def echo(msg: str) -> ToolResult:
        return ToolResult(content="final: " + msg, data={"msg": msg}, is_final=True)

    t = FunctionTool(fn=echo)
    result = _run(t.execute(msg="bye"))
    assert result.is_final is True
    assert result.data == {"msg": "bye"}


def test_tool_decorator_overrides_name_and_description():
    @tool(name="renamed", description="overridden")
    def original(x: str) -> str:
        """orig doc"""
        return x

    assert isinstance(original, FunctionTool)
    assert original.name == "renamed"
    assert original.description == "overridden"


def test_tool_openai_schema():
    @tool()
    def square(n: int, double: bool = False) -> int:
        """Compute square.

        Args:
            n: base number
            double: whether to double the result
        """
        return n * n * (2 if double else 1)

    schema = square.to_openai_schema()
    assert schema["name"] == "square"
    props = schema["parameters"]["properties"]
    assert props["n"]["type"] == "integer"
    assert props["double"]["type"] == "boolean"
    assert schema["parameters"]["required"] == ["n"]


def test_base_tool_requires_name():
    class Bad(BaseTool):
        async def execute(self, **kwargs):
            return ToolResult()

    with pytest.raises(ValueError):
        Bad()


def test_explicit_parameters_override_inference():
    def fn(x: str) -> str:
        return x

    params = [ToolParameter(name="x", type="string", description="custom desc", required=False)]
    t = FunctionTool(fn=fn, parameters=params)
    assert t.parameters[0].description == "custom desc"
    assert t.parameters[0].required is False
