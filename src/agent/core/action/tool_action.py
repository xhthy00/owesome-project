"""ToolAction：让 LLM 以 JSON 形式选择并调用一个工具。

约定的 LLM 输出 schema（可包在 ```json 代码块里）::

    {
      "thoughts": "为什么选这个工具 / 下一步计划",
      "tool": "tool_name",
      "args": { "arg1": "...", "arg2": 123 }
    }

解析失败、工具不存在、参数不匹配、工具抛异常——一律返回
``ActionOutput(is_exe_success=False, ...)``，由 ConversableAgent 主循环
决定是否回灌失败原因并重试。

本 Action 一次只执行一个工具；多步 ReAct 循环由 ReActAgent 上层驱动（Phase B-3）。
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from src.agent.audit.tool_call_log import log_tool_call_fire_and_forget
from src.agent.core.action.base import Action, ActionOutput
from src.agent.resource.tool.base import ToolResult
from src.agent.resource.tool.builtin import TERMINATE_TOOL_NAME
from src.agent.resource.tool.pack import ToolNotFoundError, ToolPack
from src.agent.util.json_parser import parse_json_tolerant

logger = logging.getLogger(__name__)
_THINK_BLOCK_RE = re.compile(r"<think\b[^>]*>.*?</think>", re.DOTALL | re.IGNORECASE)


class ToolAction(Action):
    name = "tool_call"

    def __init__(self, tool_pack: ToolPack) -> None:
        if tool_pack is None:
            raise ValueError("ToolAction requires a ToolPack")
        self.tool_pack = tool_pack

    async def run(self, ai_message: str, **kwargs: Any) -> ActionOutput:
        started = time.perf_counter()
        agent_name = str(kwargs.get("agent_name") or "")
        round_idx = kwargs.get("round_idx")
        sub_task_index = kwargs.get("sub_task_index")

        def _audit(
            *,
            tool_name: str,
            success: bool,
            args: dict[str, Any] | None,
            result_preview: str,
        ) -> None:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            log_tool_call_fire_and_forget(
                agent_name=agent_name,
                round_idx=round_idx if isinstance(round_idx, int) else None,
                sub_task_index=sub_task_index if isinstance(sub_task_index, int) else None,
                tool_name=tool_name or self.name,
                success=success,
                elapsed_ms=elapsed_ms,
                args=args,
                result_preview=result_preview,
            )

        def _extract_non_json_final_answer(raw_text: str) -> str | None:
            cleaned = _THINK_BLOCK_RE.sub("", str(raw_text or "")).strip()
            if not cleaned:
                return None
            # 有 JSON 结构痕迹时不要做 terminate 猜测，交给下一轮严格重试。
            if any(ch in cleaned for ch in "{}[]"):
                return None
            # 太短通常是噪声，不作为最终答案。
            if len(cleaned) < 24:
                return None
            return cleaned

        try:
            parsed = parse_json_tolerant(ai_message)
        except ValueError as e:
            fallback_answer = _extract_non_json_final_answer(ai_message)
            if fallback_answer and TERMINATE_TOOL_NAME in self.tool_pack:
                try:
                    result = await self.tool_pack.invoke(
                        TERMINATE_TOOL_NAME,
                        {"final_answer": fallback_answer},
                    )
                    _audit(
                        tool_name=TERMINATE_TOOL_NAME,
                        success=True,
                        args={"final_answer": fallback_answer},
                        result_preview=result.content,
                    )
                    return ActionOutput(
                        is_exe_success=True,
                        content=result.content,
                        action=TERMINATE_TOOL_NAME,
                        thoughts=None,
                        observations=result.content,
                        terminate=result.is_final,
                        extra={
                            "tool_args": {"final_answer": fallback_answer},
                            "tool_data": result.data,
                            "tool_extra": result.extra,
                        },
                    )
                except Exception:
                    logger.exception("fallback terminate failed")
            msg = f"无法从 LLM 输出解析 JSON：{e}. 请严格返回 {{\"tool\": ..., \"args\": ...}} 结构。"
            _audit(tool_name=self.name, success=False, args=None, result_preview=msg)
            return ActionOutput(
                is_exe_success=False,
                content=msg,
                action=self.name,
                thoughts=None,
            )

        if not isinstance(parsed, dict):
            msg = "LLM 输出必须是 JSON 对象，不能是数组或标量。"
            _audit(tool_name=self.name, success=False, args=None, result_preview=msg)
            return ActionOutput(
                is_exe_success=False,
                content=msg,
                action=self.name,
            )

        thoughts = parsed.get("thoughts") or parsed.get("reasoning")
        tool_name = parsed.get("tool") or parsed.get("action")
        args = parsed.get("args") or parsed.get("arguments") or {}

        if not tool_name:
            msg = "LLM 输出缺少 `tool` 字段。"
            _audit(tool_name=self.name, success=False, args=args if isinstance(args, dict) else None, result_preview=msg)
            return ActionOutput(
                is_exe_success=False,
                content=msg,
                action=self.name,
                thoughts=thoughts,
            )

        if not isinstance(args, dict):
            msg = "`args` 必须是 JSON 对象。"
            _audit(tool_name=str(tool_name), success=False, args=None, result_preview=msg)
            return ActionOutput(
                is_exe_success=False,
                content=msg,
                action=self.name,
                thoughts=thoughts,
            )

        try:
            result: ToolResult = await self.tool_pack.invoke(str(tool_name), args)
        except ToolNotFoundError:
            available = ", ".join(self.tool_pack.names()) or "（空）"
            msg = f"未知工具：{tool_name}。可用工具：{available}"
            _audit(tool_name=str(tool_name), success=False, args=args, result_preview=msg)
            return ActionOutput(
                is_exe_success=False,
                content=msg,
                action=self.name,
                thoughts=thoughts,
            )
        except TypeError as e:
            msg = f"调用工具 {tool_name} 参数不匹配：{e}"
            _audit(tool_name=str(tool_name), success=False, args=args, result_preview=msg)
            return ActionOutput(
                is_exe_success=False,
                content=msg,
                action=self.name,
                thoughts=thoughts,
            )
        except Exception as e:
            logger.exception("Tool %s raised", tool_name)
            msg = f"工具 {tool_name} 执行异常：{e}"
            _audit(tool_name=str(tool_name), success=False, args=args, result_preview=msg)
            return ActionOutput(
                is_exe_success=False,
                content=msg,
                action=self.name,
                thoughts=thoughts,
            )

        _audit(
            tool_name=str(tool_name),
            success=True,
            args=args,
            result_preview=result.content,
        )
        return ActionOutput(
            is_exe_success=True,
            content=result.content,
            action=str(tool_name),
            thoughts=thoughts,
            observations=result.content,
            terminate=result.is_final,
            extra={
                "tool_args": dict(args),
                "tool_data": result.data,
                "tool_extra": result.extra,
            },
        )
