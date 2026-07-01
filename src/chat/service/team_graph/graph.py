"""编译 Team LangGraph。"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from src.chat.service.team_graph.nodes import (
    node_charter,
    node_persist_failure,
    node_persist_success,
    node_planner,
    node_sub_tasks_loop,
    node_summarizer,
)
from src.chat.service.team_graph.routing import route_after_sub_tasks
from src.chat.service.team_graph.state import TeamState


def build_team_graph():
    """构建并编译 Team 状态图（无 checkpoint，适用于单次 invoke）。"""
    graph = StateGraph(TeamState)
    graph.add_node("planner", node_planner)
    graph.add_node("sub_tasks_loop", node_sub_tasks_loop)
    graph.add_node("persist_failure", node_persist_failure)
    graph.add_node("charter", node_charter)
    graph.add_node("summarizer", node_summarizer)
    graph.add_node("persist_success", node_persist_success)

    graph.add_edge(START, "planner")
    graph.add_edge("planner", "sub_tasks_loop")
    graph.add_conditional_edges(
        "sub_tasks_loop",
        route_after_sub_tasks,
        {
            "persist_failure": "persist_failure",
            "charter": "charter",
        },
    )
    graph.add_edge("persist_failure", END)
    graph.add_edge("charter", "summarizer")
    graph.add_edge("summarizer", "persist_success")
    graph.add_edge("persist_success", END)

    return graph.compile()
