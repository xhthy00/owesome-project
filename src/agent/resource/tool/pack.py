"""ToolPack：一组工具的容器 + 运行时上下文绑定。

绑定（bindings）的用途：像 ``datasource_id`` / ``session`` 这类"每次调用都要
传但又不该由 LLM 决定"的参数，在构造 ToolPack 时一次性绑好，Tool.execute 时
自动注入。这样 LLM 只需要决定业务参数（SQL 文本、表名等）。
"""

from __future__ import annotations

from typing import Any, Iterable

from src.agent.resource.tool.base import BaseTool, ToolResult


class ToolNotFoundError(KeyError):
    pass


class ToolPack:
    def __init__(
        self,
        tools: Iterable[BaseTool] | None = None,
        bindings: dict[str, Any] | None = None,
    ) -> None:
        self._tools: dict[str, BaseTool] = {}
        self._bindings: dict[str, Any] = dict(bindings or {})
        if tools:
            for t in tools:
                self.register(t)

    def register(self, t: BaseTool) -> None:
        if not t.name:
            raise ValueError("Cannot register tool without name")
        if t.name in self._tools:
            raise ValueError(f"Tool already registered: {t.name}")
        self._tools[t.name] = t

    def get(self, name: str) -> BaseTool:
        if name not in self._tools:
            raise ToolNotFoundError(name)
        return self._tools[name]

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    def list_tools(self) -> list[BaseTool]:
        return list(self._tools.values())

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def bind(self, **bindings: Any) -> "ToolPack":
        """返回一个带了额外绑定的新 ToolPack（不修改原实例）。"""
        merged = {**self._bindings, **bindings}
        new_pack = ToolPack(bindings=merged)
        new_pack._tools = dict(self._tools)
        return new_pack

    @property
    def bindings(self) -> dict[str, Any]:
        return dict(self._bindings)

    async def invoke(self, name: str, args: dict[str, Any] | None = None) -> ToolResult:
        t = self.get(name)
        # bindings（datasource_id / report_data / tool_runtime_ctx 等）必须盖过 LLM args，
        # 否则模型手填 null/空对象会冲掉上游全量数据。
        merged = {**(args or {}), **self._bindings}
        return await t.execute(**merged)

    def render_prompt(self) -> str:
        """把所有工具渲染成 prompt 片段，供 ReAct system prompt 使用。

        bindings 中声明的参数会被隐藏——这些是运行时注入的上下文
        （如 datasource_id / user_id），LLM 不应也不需要填。
        """
        if not self._tools:
            return "（无可用工具）"
        hidden = set(self._bindings.keys())
        lines: list[str] = []
        for t in self._tools.values():
            visible = [p for p in t.parameters if p.name not in hidden]
            if not visible:
                lines.append(f"- {t.name}: {t.description}")
                continue
            params_desc = []
            for p in visible:
                required_flag = "必填" if p.required else "可选"
                params_desc.append(f"{p.name}({p.type}, {required_flag}): {p.description}")
            params_str = "\n    - ".join(params_desc)
            lines.append(f"- {t.name}: {t.description}\n    - {params_str}")
        return "\n".join(lines)
