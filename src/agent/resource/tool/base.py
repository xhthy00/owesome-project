"""Shared types for business and education tools."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field


class ToolParameter(BaseModel):
    """单个工具入参声明。字段对齐 OpenAI function calling 的 JSON Schema。"""

    name: str
    type: str = "string"
    description: str = ""
    required: bool = True
    default: Any | None = None


class ToolResult(BaseModel):
    """工具执行结果。

    ``content`` 是渲染回 prompt 的文本（observation）；
    ``data`` 是结构化数据，供 Agent / 前端后续消费（例如执行结果表格）；
    ``is_final`` 为 True 表示工具本身已给出最终答案，Agent 应终止 ReAct 循环。
    """

    content: str = ""
    data: Any = None
    is_final: bool = False
    extra: dict[str, Any] = Field(default_factory=dict)


class BaseTool(ABC):
    """所有 Tool 的基类。

    子类至少要提供 ``name`` / ``description`` / ``parameters`` / ``execute``。
    ``execute`` 必须是 async——即便内部是同步逻辑，也请用 ``asyncio.to_thread``
    包起来，避免阻塞事件循环。
    """

    name: str = ""
    description: str = ""
    parameters: list[ToolParameter] = []

    def __init__(
        self,
        name: str | None = None,
        description: str | None = None,
        parameters: list[ToolParameter] | None = None,
    ) -> None:
        if name is not None:
            self.name = name
        if description is not None:
            self.description = description
        if parameters is not None:
            self.parameters = list(parameters)
        if not self.name:
            raise ValueError(f"{type(self).__name__}: tool name is required")

    @abstractmethod
    async def execute(self, **kwargs: Any) -> ToolResult:
        raise NotImplementedError

    def to_openai_schema(self) -> dict[str, Any]:
        """导出 OpenAI function calling 风格 schema，便于拼 prompt。"""
        props: dict[str, dict[str, Any]] = {}
        required: list[str] = []
        for p in self.parameters:
            props[p.name] = {"type": p.type, "description": p.description}
            if p.default is not None:
                props[p.name]["default"] = p.default
            if p.required:
                required.append(p.name)
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": props,
                "required": required,
            },
        }

    def __repr__(self) -> str:
        return f"<Tool {self.name}>"
