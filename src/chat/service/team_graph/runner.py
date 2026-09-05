"""Team LangGraph 执行入口。"""

from __future__ import annotations

from typing import Any

from src.agent.adapter.llm_adapter import LangChainLlmClient
from src.chat.schemas import ChatRequest
from src.chat.service.agent_runner import EmitCallback
from src.chat.service.team_graph.graph import build_team_graph
from src.chat.service.team_graph.nodes import build_initial_team_state


async def run_team_stream_graph(
    *,
    request: ChatRequest,
    current_user_id: int,
    emit: EmitCallback,
    llm_client: Any | None = None,
    persist: bool = True,
    enable_tool_agent: bool = True,
    workspace_oid: int = 1,
    constraints: Any = None,
) -> int:
    """与 :func:`run_team_stream` 行为对齐，经 LangGraph 调度。"""
    graph = build_team_graph()
    llm = llm_client if llm_client is not None else LangChainLlmClient()
    constraints_ctx = constraints.to_context() if constraints is not None else None
    initial = build_initial_team_state(
        request=request,
        current_user_id=current_user_id,
        workspace_oid=workspace_oid,
        persist=persist,
        constraints_ctx=constraints_ctx,
    )
    config = {
        "configurable": {
            "emit": emit,
            "llm_client": llm,
            "enable_tool_agent": enable_tool_agent,
        }
    }
    final = await graph.ainvoke(initial, config)
    return int(final.get("record_id") or 0)
