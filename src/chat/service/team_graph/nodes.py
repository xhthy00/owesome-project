"""Team 图节点：内部调用 agent_runner 既有阶段函数，不复制业务逻辑。"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from langchain_core.runnables import RunnableConfig

from src.agent.adapter.llm_adapter import LangChainLlmClient
from src.agent.expand.chat_awel_team import build_chat_team
from src.chat.schemas import ChatRequest
from src.chat.service.agent_runner import (
    _DataAnalystPhase,
    _extract_required_keywords,
    _first_non_empty,
    _persist_sync,
    _run_charter,
    _run_data_analyst_phase,
    _run_planner_phase,
    _run_summarizer_multi,
    _run_tool_expert_phase,
    _RunConstraints,
)
from src.chat.service.team_graph.state import TeamState

logger = logging.getLogger(__name__)


def _build_plan_states_for_persist(
    sub_phases: list[tuple[str, _DataAnalystPhase]],
    plan_agents: list[str],
) -> list[dict[str, Any]]:
    plan_states_for_persist: list[dict[str, Any]] = []
    for idx, (sub_task, phase) in enumerate(sub_phases):
        state = "ok" if phase.is_success else "error"
        plan_states_for_persist.append(
            {
                "index": idx,
                "sub_task": sub_task,
                "sub_task_agent": plan_agents[idx] if idx < len(plan_agents) else "DataAnalyst",
                "state": state,
                "error": None if phase.is_success else (phase.fail_reason or ""),
                "sql": phase.state.last_sql if phase.is_success else None,
                "row_count": (
                    (phase.state.last_exec_result or {}).get("row_count")
                    if phase.is_success
                    else None
                ),
            }
        )
    return plan_states_for_persist


def _llm_from_config(config: RunnableConfig) -> Any:
    llm = config["configurable"].get("llm_client")
    if llm is None:
        return LangChainLlmClient()
    return llm


async def node_planner(state: TeamState, config: RunnableConfig) -> dict[str, Any]:
    emit = config["configurable"]["emit"]
    llm = _llm_from_config(config)
    request = state["request"]
    plan_items = await _run_planner_phase(
        request=request,
        llm_client=llm,
        emit=emit,
    )
    team_cfg = build_chat_team(
        enable_tool_agent=config["configurable"].get("enable_tool_agent", True),
    )
    plans = [it["sub_task"] for it in plan_items]
    plan_agents = [team_cfg.resolve_sub_task_agent(it["sub_task_agent"]) for it in plan_items]
    await emit("plan", {"plans": plans, "sub_task_agents": plan_agents})
    return {
        "plan_items": plan_items,
        "plans": plans,
        "plan_agents": plan_agents,
    }


async def node_sub_tasks_loop(state: TeamState, config: RunnableConfig) -> dict[str, Any]:
    """串行执行各 sub_task，语义对齐 legacy 主循环。"""
    emit = config["configurable"]["emit"]
    llm = _llm_from_config(config)
    request = state["request"]
    plan_items = state["plan_items"]
    team_cfg = build_chat_team(
        enable_tool_agent=config["configurable"].get("enable_tool_agent", True),
    )

    shared_constraints = _RunConstraints(
        locked_tables=[],
        required_keywords=_extract_required_keywords(request.question),
        source_sub_task_index=None,
    )

    sub_phases: list[tuple[str, _DataAnalystPhase]] = []
    last_good_phase: _DataAnalystPhase | None = None
    all_steps: list[dict[str, Any]] = []

    current_user_id = state["current_user_id"]
    workspace_oid = state["workspace_oid"]

    for idx, item in enumerate(plan_items):
        sub_task = item["sub_task"]
        sub_task_agent = team_cfg.resolve_sub_task_agent(item["sub_task_agent"])
        await emit(
            "plan_update",
            {
                "index": idx,
                "state": "running",
                "sub_task": sub_task,
                "sub_task_agent": sub_task_agent,
            },
        )
        if sub_task_agent == "ToolExpert":
            phase = await _run_tool_expert_phase(
                request=request,
                current_user_id=current_user_id,
                emit=emit,
                llm_client=llm,
                question_override=sub_task,
                sub_task_index=idx,
                constraints=shared_constraints,
                workspace_oid=workspace_oid,
            )
        else:
            phase = await _run_data_analyst_phase(
                request=request,
                current_user_id=current_user_id,
                emit=emit,
                llm_client=llm,
                question_override=sub_task,
                sub_task_index=idx,
                constraints=shared_constraints,
                workspace_oid=workspace_oid,
            )
        for step in phase.state.steps:
            tagged = dict(step)
            tagged["sub_task_index"] = idx
            all_steps.append(tagged)
        sub_phases.append((sub_task, phase))

        if phase.fatal_error:
            await emit(
                "plan_update",
                {"index": idx, "state": "error", "error": phase.fail_reason},
            )
            break

        if phase.is_success:
            last_good_phase = phase
            await emit(
                "plan_update",
                {
                    "index": idx,
                    "state": "ok",
                    "sub_task_agent": sub_task_agent,
                    "sql": phase.state.last_sql,
                    "row_count": (
                        (phase.state.last_exec_result or {}).get("row_count") or 0
                    ),
                },
            )
        else:
            await emit(
                "plan_update",
                {
                    "index": idx,
                    "state": "error",
                    "sub_task_agent": sub_task_agent,
                    "error": phase.fail_reason,
                },
            )

    return {
        "sub_phases": sub_phases,
        "last_good_phase": last_good_phase,
        "all_steps": all_steps,
    }


async def node_persist_failure(state: TeamState, config: RunnableConfig) -> dict[str, Any]:
    emit = config["configurable"]["emit"]
    request = state["request"]
    sub_phases: list[tuple[str, _DataAnalystPhase]] = state["sub_phases"]
    plans = state["plans"]
    plan_agents = state["plan_agents"]
    all_steps = state["all_steps"]

    overall_reason = _first_non_empty([p.fail_reason for _, p in sub_phases]) or "all sub tasks failed"
    await emit("error", {"error": overall_reason})

    plan_states_for_persist = _build_plan_states_for_persist(sub_phases, plan_agents)

    persist = state.get("persist", True)
    record_id = 0
    if persist:
        record_id = await asyncio.to_thread(
            _persist_sync,
            request=request,
            current_user_id=state["current_user_id"],
            question=request.question,
            sql="",
            sql_error=overall_reason,
            exec_result=None,
            is_success=False,
            reasoning="",
            steps=all_steps,
            chart_type="table",
            chart_config=None,
            agent_mode="team",
            plans=plans,
            sub_task_agents=plan_agents,
            plan_states=plan_states_for_persist,
            tool_calls=[tc for _, p in sub_phases for tc in p.state.tool_calls],
            reports=[rp for _, p in sub_phases for rp in p.state.reports],
            workspace_oid=state["workspace_oid"],
        )
    return {"record_id": record_id, "overall_error": overall_reason}


async def node_charter(state: TeamState, config: RunnableConfig) -> dict[str, Any]:
    emit = config["configurable"]["emit"]
    llm = _llm_from_config(config)
    request = state["request"]
    last_good_phase: _DataAnalystPhase = state["last_good_phase"]
    chart_type, chart_config = await _run_charter(
        question=request.question,
        state=last_good_phase.state,
        llm_client=llm,
        emit=emit,
    )
    await emit("chart", {"chart_type": chart_type, "chart_config": chart_config})
    return {"chart_type": chart_type, "chart_config": chart_config}


async def node_summarizer(state: TeamState, config: RunnableConfig) -> dict[str, Any]:
    emit = config["configurable"]["emit"]
    llm = _llm_from_config(config)
    request = state["request"]
    sub_phases: list[tuple[str, _DataAnalystPhase]] = state["sub_phases"]
    last_good_phase: _DataAnalystPhase = state["last_good_phase"]

    default_summary = last_good_phase.reply.content if last_good_phase.reply else ""
    summary_text = await _run_summarizer_multi(
        question=request.question,
        sub_phases=sub_phases,
        llm_client=llm,
        emit=emit,
        fallback=default_summary,
    )
    await emit("summary", {"content": summary_text})
    return {"summary_text": summary_text}


async def node_persist_success(state: TeamState, config: RunnableConfig) -> dict[str, Any]:
    request = state["request"]
    sub_phases: list[tuple[str, _DataAnalystPhase]] = state["sub_phases"]
    plans = state["plans"]
    plan_agents = state["plan_agents"]
    all_steps = state["all_steps"]
    last_good_phase: _DataAnalystPhase = state["last_good_phase"]
    chart_type = state.get("chart_type", "table")
    chart_config = state.get("chart_config") or {}
    summary_text = state.get("summary_text") or ""

    plan_states_for_persist = _build_plan_states_for_persist(sub_phases, plan_agents)

    persist = state.get("persist", True)
    record_id = 0
    if persist:
        record_id = await asyncio.to_thread(
            _persist_sync,
            request=request,
            current_user_id=state["current_user_id"],
            question=request.question,
            sql=last_good_phase.state.last_sql,
            sql_error=None,
            exec_result=last_good_phase.state.last_exec_result,
            is_success=True,
            reasoning=summary_text or "",
            steps=all_steps,
            chart_type=chart_type,
            chart_config=chart_config,
            agent_mode="team",
            plans=plans,
            sub_task_agents=plan_agents,
            plan_states=plan_states_for_persist,
            tool_calls=[tc for _, p in sub_phases for tc in p.state.tool_calls],
            summary=summary_text or None,
            reports=[rp for _, p in sub_phases for rp in p.state.reports],
            workspace_oid=state["workspace_oid"],
        )
    return {"record_id": record_id}


def build_initial_team_state(
    *,
    request: ChatRequest,
    current_user_id: int,
    workspace_oid: int,
    persist: bool,
) -> TeamState:
    return {
        "request": request,
        "current_user_id": current_user_id,
        "workspace_oid": workspace_oid,
        "persist": persist,
    }
