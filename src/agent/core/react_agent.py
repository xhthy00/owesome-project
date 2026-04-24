"""ReActAgent：多轮 "thinking -> tool_call -> observation" 循环驱动的 Agent。

与 :class:`ConversableAgent` 的根本差异：

    ConversableAgent (基类)
        thinking -> review -> act -> verify -> 失败则 **重来同一轮**
        适合：单次对话生成 + 失败自修复

    ReActAgent (本类)
        第 N 轮:  thinking -> act(tool)
                  └─ terminate ? 结束 : observation 回灌 -> 进入第 N+1 轮
        适合：需要工具探查 / 多步推理 / 看结果再决定下一步

关键约定：
- LLM 每轮输出必须是 JSON `{"thoughts", "tool", "args"}`（由 ToolAction 解析）；
- **工具业务失败（如 SQL 报错）不触发重试，而是作为 observation 交给 LLM 下一轮
  自己修正**——这才是 ReAct 的精髓；
- 只有"工具不存在 / JSON 无法解析"这类框架错误才作为 observation 回灌并继续
  （LLM 下一轮应该会纠正格式）；
- ``terminate`` 工具的 ActionOutput.terminate=True 是唯一的正常退出信号。
"""

from __future__ import annotations

import logging
from typing import Any

from src.agent.core.action.base import Action, ActionOutput
from src.agent.core.action.tool_action import ToolAction
from src.agent.core.agent import AgentMessage
from src.agent.core.base_agent import ConversableAgent
from src.agent.resource.tool.pack import ToolPack

logger = logging.getLogger(__name__)

#: 同一工具连续调用次数达该阈值后，在下一轮 observation 前**追加**一段软警告，
#: 推动 LLM 换工具或 terminate。不强制终止——max_rounds 已经是硬上限兜底。
#: 3 是经验值：list_tables → describe_table → sample_rows 这类合法同源探查
#: 一般不会超过 2 次；连续 3 次同工具几乎都是"坏循环"信号。
_REPEAT_TOOL_WARN_THRESHOLD = 3


class ReActAgent(ConversableAgent):
    """多轮 ReAct Agent 基类。

    子类通常只需定义 ``profile``（含带 ``{{tools_prompt}}`` 占位符的 desc），
    ``tool_pack`` 由构造参数注入。
    """

    max_react_rounds: int = 10

    def __init__(
        self,
        *,
        tool_pack: ToolPack,
        actions: list[Action] | None = None,
        max_react_rounds: int | None = None,
        **kwargs: Any,
    ) -> None:
        if tool_pack is None:
            raise ValueError("ReActAgent requires a ToolPack")
        if actions is None:
            actions = [ToolAction(tool_pack=tool_pack)]
        super().__init__(actions=actions, **kwargs)
        self.tool_pack = tool_pack
        if max_react_rounds is not None:
            self.max_react_rounds = max_react_rounds

    def _build_prompt_variables(self, reply: AgentMessage) -> dict[str, Any]:
        base = super()._build_prompt_variables(reply)
        base.setdefault("tools_prompt", self.tool_pack.render_prompt())
        return base

    async def generate_reply(
        self,
        received_message: AgentMessage,
        sender: Any,
        reviewer: Any | None = None,
        rely_messages: list[AgentMessage] | None = None,
        **kwargs: Any,
    ) -> AgentMessage:
        import time

        reply = self._init_reply_message(received_message, rely_messages)
        running_rely: list[AgentMessage] = list(rely_messages or [])
        last_action_out: ActionOutput | None = None
        # E-guard：重复调用防护——仅追踪"成功解析到已知工具"的那个 tool_name，
        # 解析失败 / 未知工具会重置 streak（否则 LLM 稳定发错同一个错 name
        # 会被误报"重复调用"）。
        last_tool_name: str | None = None
        tool_streak: int = 0

        for round_idx in range(self.max_react_rounds):
            reply.rounds = round_idx + 1

            messages = await self._build_llm_messages(
                received_message=received_message,
                rely_messages=running_rely,
                reply=reply,
                fail_reason=None,
            )
            llm_text = await self.thinking(messages, sender)
            reply.content = llm_text

            await self._emit(
                "agent_thought",
                {"round": reply.rounds, "agent": self.name, "text": llm_text},
            )

            t0 = time.time()
            action_out = await self.act(
                reply,
                sender=sender,
                reviewer=reviewer,
                round_idx=reply.rounds,
                agent_name=self.name,
                **kwargs,
            )
            elapsed_ms = int((time.time() - t0) * 1000)
            reply.action_report = action_out
            last_action_out = action_out

            await self._emit_tool_events(reply.rounds, action_out, elapsed_ms)

            if action_out.terminate:
                if action_out.content:
                    reply.content = action_out.content
                await self._emit(
                    "final_answer",
                    {"agent": self.name, "text": reply.content or ""},
                )
                await self.write_memories(received_message, reply, action_out)
                return reply

            # 统计 streak：仅"成功 + 已知工具 + 非 terminate"的轮计入。
            current_tool = (
                action_out.action
                if action_out.is_exe_success and action_out.action
                and action_out.action != "tool_call"
                else None
            )
            if current_tool:
                if current_tool == last_tool_name:
                    tool_streak += 1
                else:
                    last_tool_name = current_tool
                    tool_streak = 1
            else:
                last_tool_name = None
                tool_streak = 0

            streak_warning: str | None = None
            if tool_streak >= _REPEAT_TOOL_WARN_THRESHOLD and last_tool_name:
                streak_warning = (
                    f"⚠️ 你已连续 {tool_streak} 次调用 `{last_tool_name}` 仍未收敛。"
                    "请**换一个工具**（例如 list_tables/describe_table 探查 → execute_sql 查询 → calculate 算数），"
                    "或在已有信息足够时直接调用 `terminate` 给出结论。"
                )
                logger.warning(
                    "[%s] tool streak=%d for %r at round %d — injecting soft warning",
                    self.name, tool_streak, last_tool_name, reply.rounds,
                )

            running_rely.extend(
                self._format_trace(
                    thought_text=llm_text,
                    action_out=action_out,
                    streak_warning=streak_warning,
                )
            )

            if not action_out.have_retry:
                logger.info(
                    "[%s] action declared have_retry=False at round %d, stopping loop",
                    self.name,
                    reply.rounds,
                )
                break

        if last_action_out is None:
            reply.action_report = ActionOutput(
                is_exe_success=False,
                content="ReAct loop produced no action",
                have_retry=False,
            )
        elif last_action_out.is_exe_success and (
            (last_action_out.observations and str(last_action_out.observations).strip())
            or (last_action_out.content and str(last_action_out.content).strip())
        ):
            # 兜底收敛：达到轮数上限但最后一次工具结果有效时，自动收敛成最终答复，
            # 避免前端把这类“已拿到结果但忘了 terminate”的场景误判为失败。
            final_text = str(last_action_out.observations or last_action_out.content or "").strip()
            reply.content = final_text
            reply.action_report = ActionOutput(
                is_exe_success=True,
                content=final_text,
                thoughts=last_action_out.thoughts,
                action=last_action_out.action,
                observations=last_action_out.observations,
                terminate=True,
                have_retry=False,
                extra=dict(last_action_out.extra or {}),
            )
            await self._emit(
                "final_answer",
                {"agent": self.name, "text": reply.content or ""},
            )
        else:
            reply.action_report = ActionOutput(
                is_exe_success=False,
                content=(
                    f"达到最大 ReAct 轮数 ({self.max_react_rounds}) 仍未调用 terminate。"
                    f" 最后一次观察：{last_action_out.observations or last_action_out.content}"
                ),
                thoughts=last_action_out.thoughts,
                action=last_action_out.action,
                observations=last_action_out.observations,
                have_retry=False,
            )
        await self.write_memories(received_message, reply, reply.action_report)
        return reply

    async def _emit_tool_events(
        self,
        round_idx: int,
        action_out: ActionOutput,
        elapsed_ms: int,
    ) -> None:
        """把一次 act 拆成 tool_call + tool_result 两条 SSE 事件。

        约定：
        - 成功：action_out.action 是 tool_name，extra['tool_args'] 是解析后的参数；
        - 失败（JSON 解析 / 未知工具 / 参数不匹配）：action_out.action 为 "tool_call"
          字面量，此时不发 tool_call 而只发 tool_result 记录失败原因，保持信号清晰。
        """
        tool_name = action_out.action
        is_known_tool = bool(action_out.is_exe_success) and tool_name and tool_name != "tool_call"
        is_terminate = bool(action_out.terminate)

        if is_known_tool and not is_terminate:
            await self._emit(
                "tool_call",
                {
                    "round": round_idx,
                    "agent": self.name,
                    "tool": tool_name,
                    "args": dict(action_out.extra.get("tool_args") or {}),
                    "thought": action_out.thoughts or "",
                },
            )

        await self._emit(
            "tool_result",
            {
                "round": round_idx,
                "agent": self.name,
                "tool": tool_name or "tool_call",
                "success": bool(action_out.is_exe_success),
                "content": action_out.observations or action_out.content or "",
                "data": action_out.extra.get("tool_data"),
                "elapsed_ms": elapsed_ms,
                "terminate": bool(action_out.terminate),
            },
        )

    def _format_trace(
        self,
        thought_text: str,
        action_out: ActionOutput,
        streak_warning: str | None = None,
    ) -> list[AgentMessage]:
        """把一轮 (thought, observation) 变成两条 AgentMessage 塞回 rely_messages。

        使用 user 角色表达 observation 是刻意为之：下一轮 LLM 看到"我上次说了 X，
        系统告诉我 Y"，自然会把 Y 当作新输入推进；若用 assistant 角色，会被
        ChatCompletion 视为"LLM 之前也这么说"，容易陷入 parroting。

        Args:
            streak_warning: 非 None 时会**追加**在 observation 前，作为软警告
                推动 LLM 跳出重复调用。注意**不污染** SSE tool_result payload——
                那里保留原始工具返回，前端需要干净信号；警告只进 ReAct 上下文。
        """
        marker = "✓" if action_out.is_exe_success else "✗"
        tool_name = action_out.action or "tool"
        observation = action_out.observations or action_out.content or "(no observation)"
        observation_block = f"[{marker} observation from {tool_name}]\n{observation}"
        if streak_warning:
            observation_block = f"{streak_warning}\n\n{observation_block}"

        return [
            AgentMessage(
                role="assistant",
                content=thought_text,
                sender=self.name,
            ),
            AgentMessage(
                role="user",
                content=observation_block,
                sender="tool",
            ),
        ]
