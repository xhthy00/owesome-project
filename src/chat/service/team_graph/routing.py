"""Team 图条件路由。"""

from __future__ import annotations

from typing import Literal

from src.chat.service.team_graph.state import TeamState


def route_after_sub_tasks(
    state: TeamState,
) -> Literal["persist_failure", "charter"]:
    """与 legacy ``run_team_stream`` 一致：仅当无任何成功 sub_task 时走失败持久化。"""
    if state.get("last_good_phase") is None:
        return "persist_failure"
    return "charter"
