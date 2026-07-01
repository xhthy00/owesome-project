"""Team LangGraph 编排的状态定义。"""

from __future__ import annotations

from typing import Any, NotRequired, TypedDict

from src.chat.schemas import ChatRequest


class TeamState(TypedDict, total=False):
    """跨节点传递的快照。复杂对象（如 _DataAnalystPhase）仅存在于内存执行路径，勿依赖 checkpoint 序列化。"""

    request: ChatRequest
    current_user_id: int
    workspace_oid: int
    persist: bool

    plan_items: list[dict[str, str]]
    plans: list[str]
    plan_agents: list[str]

    sub_phases: list[Any]
    last_good_phase: Any
    all_steps: list[dict[str, Any]]

    chart_type: str
    chart_config: dict[str, Any]
    summary_text: NotRequired[str | None]

    overall_error: NotRequired[str | None]
    record_id: NotRequired[int]
