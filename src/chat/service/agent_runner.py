"""Agent / Team 模式的 chat-stream 执行器。

两个公开入口：

- :func:`run_agent_stream` —— 单 Agent（DataAnalyst ReAct）模式。
- :func:`run_team_stream`  —— 四节点线性 team：
    DataAnalyst → Charter → Summarizer（Planner 留给 Phase C-2）。

两个入口共享前半段"跑 DataAnalyst + 累计 state"的逻辑（见
:func:`_run_data_analyst_phase`），差别仅在后处理和持久化字段。

设计约束：
- 全程 async，不开线程池；底层 DB 访问由 ``FunctionTool`` 自动
  ``asyncio.to_thread``，持久化环节显式放到线程池（SQLAlchemy session
  不跨线程）；
- 不抛异常到调用方：所有异常转成 ``error`` SSE 事件，record_id 返回 0；
- 不负责关闭 SSE 流（``done`` / SENTINEL 由调用方发）。
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from src.agent.adapter.llm_adapter import LangChainLlmClient
from src.agent.core.agent import AgentMessage
from src.agent.expand.charter import CharterAgent
from src.agent.expand.chat_awel_team import build_chat_team
from src.agent.expand.data_analyst import build_data_analyst
from src.agent.expand.planner import PlannerAgent
from src.agent.expand.summarizer import SummarizerAgent
from src.agent.expand.tool_agent import build_tool_agent
from src.agent.expand.user_proxy import UserProxyAgent
from src.chat.schemas import ChatRequest

logger = logging.getLogger(__name__)

EmitCallback = Callable[[str, dict[str, Any]], Awaitable[None]]

# Summarizer / Charter 上下文里塞给 LLM 的样例行数上限，避免 prompt 爆炸
_SAMPLE_ROWS_LIMIT = 20
_SQL_TABLE_RE = re.compile(r"""(?i)\b(?:from|join)\s+([`"]?[A-Za-z0-9_.]+[`"]?)""")


@dataclass
class _RunConstraints:
    """team 会话级约束（第 1 步：只做状态与传递，不做拦截）。"""

    locked_tables: list[str]
    required_keywords: list[str]
    source_sub_task_index: int | None = None

    def to_context(self) -> dict[str, Any]:
        return {
            "locked_tables": list(self.locked_tables),
            "required_keywords": list(self.required_keywords),
            "source_sub_task_index": self.source_sub_task_index,
        }


def _extract_required_keywords(question: str) -> list[str]:
    q = (question or "").strip()
    if not q:
        return []
    # 轻量关键词：中文连续段 + 英文 token，长度 >= 2。仅用于上下文提示，不作强校验。
    import re

    tokens = re.findall(r"[A-Za-z0-9_]{2,}|[\u4e00-\u9fff]{2,}", q)
    seen: set[str] = set()
    out: list[str] = []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out[:12]


def _normalize_ident(v: str) -> str:
    return str(v or "").strip().strip("`\"").lower()


def _extract_sql_tables(sql: str) -> list[str]:
    out: list[str] = []
    for m in _SQL_TABLE_RE.finditer(sql or ""):
        t = _normalize_ident(m.group(1))
        if t and t not in out:
            out.append(t)
    return out


def _sql_hits_locked_tables(sql: str, locked_tables: list[str]) -> bool:
    if not sql or not locked_tables:
        return True
    hit = _extract_sql_tables(sql)
    allowed = {_normalize_ident(x) for x in locked_tables if str(x or "").strip()}
    if not allowed:
        return True
    return any(t in allowed for t in hit)


# --------------------------------------------------------------------------- #
# 公开入口
# --------------------------------------------------------------------------- #


async def run_agent_stream(
    *,
    request: ChatRequest,
    current_user_id: int,
    emit: EmitCallback,
    llm_client: Any | None = None,
    persist: bool = True,
    enable_tool_agent: bool = True,
) -> int:
    """单 Agent 模式：跑 DataAnalyst ReAct 循环，全程 emit SSE。

    Returns:
        新建的 record_id；若无 conversation_id 或持久化失败则返回 0。
    """
    phase = await _run_data_analyst_phase(
        request=request,
        current_user_id=current_user_id,
        emit=emit,
        llm_client=llm_client,
    )
    if phase.fatal_error:
        return 0

    if not phase.terminated:
        await emit("error", {"error": phase.fail_reason or "agent did not reach a final answer"})

    if not persist:
        return 0

    return await asyncio.to_thread(
        _persist_sync,
        request=request,
        current_user_id=current_user_id,
        question=request.question,
        sql=phase.state.last_sql,
        sql_error=None if phase.is_success else (phase.fail_reason or ""),
        exec_result=phase.state.last_exec_result,
        is_success=phase.is_success,
        reasoning=phase.reply.content if phase.reply else "",
        steps=list(phase.state.steps),
        chart_type="table",
        chart_config=None,
        agent_mode="agent",
        tool_calls=list(phase.state.tool_calls),
        reports=list(phase.state.reports),
    )


async def run_team_stream(
    *,
    request: ChatRequest,
    current_user_id: int,
    emit: EmitCallback,
    llm_client: Any | None = None,
    persist: bool = True,
    enable_tool_agent: bool = True,
) -> int:
    """Team 模式：Planner → N × DataAnalyst → Charter → Summarizer。

    流水线语义：

    1. Planner 把用户问题拆成 N 个 sub_task（失败时 N=1，即原问题）；
    2. 串行跑 N 次 DataAnalyst，每次独立 ReAct 上下文；
    3. Chart 基于**最后一个成功**的 sub_task 推荐图表；
    4. Summarizer 综合**所有** sub_task 的 SQL+结果给出中文结论；
    5. 持久化：``sql`` / ``exec_result`` / ``chart_type`` 来自最后一个成功
       sub_task；``reasoning`` 来自 Summarizer 最终结论；``steps`` 里按
       sub_task 分组累计所有 DataAnalyst 回合。

    失败分治：
    - Planner 失败 → 回落为 1 个 sub_task（原问题），继续往下走；
    - 某个 DataAnalyst 失败 → emit plan_update(state=error)，继续下个 sub_task；
    - 全部 DataAnalyst 都失败 → 跳过 Chart/Summarizer，emit error；
    - Chart 失败 → chart_type=table；Summarizer 失败 → 回落 DataAnalyst 原文。
    """
    if llm_client is None:
        llm_client = LangChainLlmClient()
    team_cfg = build_chat_team(enable_tool_agent=enable_tool_agent)

    plan_items = await _run_planner_phase(
        request=request,
        llm_client=llm_client,
        emit=emit,
    )
    plans = [it["sub_task"] for it in plan_items]
    plan_agents = [team_cfg.resolve_sub_task_agent(it["sub_task_agent"]) for it in plan_items]
    await emit("plan", {"plans": plans, "sub_task_agents": plan_agents})

    shared_constraints = _RunConstraints(
        locked_tables=[],
        required_keywords=_extract_required_keywords(request.question),
        source_sub_task_index=None,
    )

    sub_phases: list[tuple[str, _DataAnalystPhase]] = []
    last_good_phase: _DataAnalystPhase | None = None
    all_steps: list[dict[str, Any]] = []

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
                llm_client=llm_client,
                question_override=sub_task,
                sub_task_index=idx,
                constraints=shared_constraints,
            )
        else:
            phase = await _run_data_analyst_phase(
                request=request,
                current_user_id=current_user_id,
                emit=emit,
                llm_client=llm_client,
                question_override=sub_task,
                sub_task_index=idx,
                constraints=shared_constraints,
            )
        # 把本 sub_task 的 steps 标上前缀后汇总，便于前端按子任务折叠
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
            # 框架级异常（如 LLM 不可达）一般是不可恢复的，直接中断后续 sub_task
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

    # 所有 sub_task 都失败时，跳过 Chart/Summarizer（省 LLM）
    if last_good_phase is None:
        overall_reason = _first_non_empty(
            [p.fail_reason for _, p in sub_phases]
        ) or "all sub tasks failed"
        await emit("error", {"error": overall_reason})
        if persist:
            return await asyncio.to_thread(
                _persist_sync,
                request=request,
                current_user_id=current_user_id,
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
            )
        return 0

    chart_type, chart_config = await _run_charter(
        question=request.question,
        state=last_good_phase.state,
        llm_client=llm_client,
        emit=emit,
    )
    await emit("chart", {"chart_type": chart_type, "chart_config": chart_config})

    default_summary = last_good_phase.reply.content if last_good_phase.reply else ""
    summary_text = await _run_summarizer_multi(
        question=request.question,
        sub_phases=sub_phases,
        llm_client=llm_client,
        emit=emit,
        fallback=default_summary,
    )
    await emit("summary", {"content": summary_text})

    if not persist:
        return 0

    return await asyncio.to_thread(
        _persist_sync,
        request=request,
        current_user_id=current_user_id,
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
    )


def _first_non_empty(items: list[str]) -> str:
    for s in items:
        if s:
            return s
    return ""


# --------------------------------------------------------------------------- #
# DataAnalyst 阶段（两个入口共享）
# --------------------------------------------------------------------------- #


@dataclass
class _DataAnalystPhase:
    """DataAnalyst 阶段的汇总结果。"""

    reply: AgentMessage | None
    state: "_RunState"
    terminated: bool
    is_success: bool
    fail_reason: str
    fatal_error: bool  # True 表示框架级异常（已发 error 事件，调用方应提前退出）


async def _run_data_analyst_phase(
    *,
    request: ChatRequest,
    current_user_id: int,
    emit: EmitCallback,
    llm_client: Any | None,
    question_override: str | None = None,
    sub_task_index: int | None = None,
    constraints: _RunConstraints | None = None,
) -> _DataAnalystPhase:
    """跑一次独立的 DataAnalyst ReAct 循环。

    Args:
        question_override: 团队模式下用 Planner 拆分后的子任务替换原问题；
            为 ``None`` 则用 ``request.question``。每次调用都会 **新建** 一个
            DataAnalyst + 全新 _RunState，确保多 sub_task 之间上下文完全隔离。
        sub_task_index: 仅 team 模式下传入，forwarder 会把它注入到本 sub_task
            产生的 ``tool_call`` / ``tool_result`` / ``agent_thought`` /
            ``final_answer`` 事件 payload 里，前端据此按子任务折叠展示。
    """
    state = _RunState(sub_task_index=sub_task_index, constraints=constraints)

    if llm_client is None:
        llm_client = LangChainLlmClient()

    agent = build_data_analyst(
        llm_client=llm_client,
        datasource_id=request.datasource_id,
        user_id=current_user_id,
    )
    agent.stream_callback = _make_forwarder(state, emit)

    question = question_override if question_override is not None else request.question
    try:
        reply = await agent.generate_reply(
            received_message=AgentMessage(
                content=question,
                role="user",
                context={"constraints": constraints.to_context() if constraints else {}},
            ),
            sender=UserProxyAgent(),
            sub_task_index=sub_task_index,
            constraints=constraints.to_context() if constraints else {},
        )
    except Exception as e:  # noqa: BLE001 - 端点级兜底，禁止异常外溢
        logger.exception("agent run failed")
        await emit("error", {"error": f"agent run failed: {e}"})
        return _DataAnalystPhase(
            reply=None,
            state=state,
            terminated=False,
            is_success=False,
            fail_reason=str(e),
            fatal_error=True,
        )

    terminated = bool(reply.action_report and reply.action_report.terminate)
    # 成功判定：只要 Agent 主动 terminate 并给出了非空的 final_answer 即算成功。
    # 不强制要求调用 execute_sql——schema/元数据探索类问题（"有哪些表"、"XX 表字段"）
    # 通过 list_tables / describe_table / sample_rows 就能完整回答，此时
    # last_exec_result 为 None 但 reply.content 已包含可交付的结论，属于合法路径。
    has_content = bool((reply.content or "").strip())
    is_success = terminated and has_content
    fail_reason = ""
    if not is_success:
        fail_reason = (
            reply.action_report.content
            if reply.action_report and reply.action_report.content
            else "agent did not reach a final answer"
        )

    return _DataAnalystPhase(
        reply=reply,
        state=state,
        terminated=terminated,
        is_success=is_success,
        fail_reason=fail_reason,
        fatal_error=False,
    )


async def _run_tool_expert_phase(
    *,
    request: ChatRequest,
    current_user_id: int,
    emit: EmitCallback,
    llm_client: Any | None,
    question_override: str | None = None,
    sub_task_index: int | None = None,
    constraints: _RunConstraints | None = None,
) -> _DataAnalystPhase:
    state = _RunState(sub_task_index=sub_task_index, constraints=constraints)

    if llm_client is None:
        llm_client = LangChainLlmClient()

    agent = build_tool_agent(
        llm_client=llm_client,
        datasource_id=request.datasource_id,
        user_id=current_user_id,
    )
    agent.stream_callback = _make_forwarder(state, emit)

    question = question_override if question_override is not None else request.question
    try:
        reply = await agent.generate_reply(
            received_message=AgentMessage(
                content=question,
                role="user",
                context={"constraints": constraints.to_context() if constraints else {}},
            ),
            sender=UserProxyAgent(),
            sub_task_index=sub_task_index,
            constraints=constraints.to_context() if constraints else {},
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("tool expert run failed")
        await emit("error", {"error": f"tool expert run failed: {e}"})
        return _DataAnalystPhase(
            reply=None,
            state=state,
            terminated=False,
            is_success=False,
            fail_reason=str(e),
            fatal_error=True,
        )

    terminated = bool(reply.action_report and reply.action_report.terminate)
    has_content = bool((reply.content or "").strip())
    is_success = terminated and has_content
    fail_reason = ""
    if not is_success:
        fail_reason = (
            reply.action_report.content
            if reply.action_report and reply.action_report.content
            else "tool expert did not reach a final answer"
        )

    return _DataAnalystPhase(
        reply=reply,
        state=state,
        terminated=terminated,
        is_success=is_success,
        fail_reason=fail_reason,
        fatal_error=False,
    )


# --------------------------------------------------------------------------- #
# Planner 阶段
# --------------------------------------------------------------------------- #


async def _run_planner_phase(
    *,
    request: ChatRequest,
    llm_client: Any,
    emit: EmitCallback,
) -> list[dict[str, str]]:
    """跑 Planner 得到 sub_task 列表。失败一律回落 [原问题]，不抛。"""
    await emit("agent_speak", {"agent": "Planner", "status": "start"})
    try:
        planner = PlannerAgent(llm_client=llm_client)
        reply = await planner.generate_reply(
            received_message=AgentMessage(
                content=request.question,
                role="user",
                context={"question": request.question},
            ),
            sender=UserProxyAgent(),
        )
    except Exception as e:  # noqa: BLE001 - Planner 失败不能拖垮 team
        logger.warning("planner failed: %s", e)
        await emit("agent_speak", {"agent": "Planner", "status": "error", "error": str(e)})
        return [{"sub_task": request.question, "sub_task_agent": "DataAnalyst"}]

    ar = reply.action_report
    extra = dict(ar.extra) if ar and ar.extra else {}
    plans = extra.get("plans") or []
    plan_agents = extra.get("plan_agents") or []
    if not isinstance(plans, list) or not plans:
        plans = [request.question]
    plans = [str(p) for p in plans if p]
    if not isinstance(plan_agents, list):
        plan_agents = []
    plan_items: list[dict[str, str]] = []
    for idx, p in enumerate(plans):
        raw_agent = str(plan_agents[idx]) if idx < len(plan_agents) else "DataAnalyst"
        sub_task_agent = "ToolExpert" if raw_agent == "ToolExpert" else "DataAnalyst"
        plan_items.append({"sub_task": p, "sub_task_agent": sub_task_agent})

    await emit(
        "agent_speak",
        {"agent": "Planner", "status": "end", "plan_count": len(plan_items)},
    )
    return plan_items


# --------------------------------------------------------------------------- #
# Charter / Summarizer 运行
# --------------------------------------------------------------------------- #


async def _run_charter(
    *,
    question: str,
    state: "_RunState",
    llm_client: Any,
    emit: EmitCallback,
) -> tuple[str, dict[str, Any]]:
    """跑 Charter；失败一律回落 table + 空 config，不抛。"""
    context = _build_single_task_context(question, state)
    await emit("agent_speak", {"agent": "Charter", "status": "start"})
    try:
        charter = CharterAgent(llm_client=llm_client)
        reply = await charter.generate_reply(
            received_message=AgentMessage(
                content=question,
                role="user",
                context=context,
            ),
            sender=UserProxyAgent(),
        )
    except Exception as e:  # noqa: BLE001 - 后处理失败不中断主流程
        logger.warning("charter failed: %s", e)
        await emit("agent_speak", {"agent": "Charter", "status": "error", "error": str(e)})
        return "table", {}

    ar = reply.action_report
    extra = dict(ar.extra) if ar and ar.extra else {}
    chart_type = str(extra.get("chart_type") or "table")
    chart_config = extra.get("chart_config") or {}
    if not isinstance(chart_config, dict):
        chart_config = {}
    await emit("agent_speak", {"agent": "Charter", "status": "end", "chart_type": chart_type})
    return chart_type, chart_config


async def _run_summarizer_multi(
    *,
    question: str,
    sub_phases: list[tuple[str, "_DataAnalystPhase"]],
    llm_client: Any,
    emit: EmitCallback,
    fallback: str,
) -> str:
    """把 N 个 sub_task 的结果综合成一段中文结论。失败回落 ``fallback``。"""
    sub_tasks_block = _format_sub_tasks_block(sub_phases)
    context = {
        "question": question,
        "sub_tasks_block": sub_tasks_block,
    }
    await emit("agent_speak", {"agent": "Summarizer", "status": "start"})
    try:
        summarizer = SummarizerAgent(llm_client=llm_client)
        reply = await summarizer.generate_reply(
            received_message=AgentMessage(
                content=question,
                role="user",
                context=context,
            ),
            sender=UserProxyAgent(),
        )
    except Exception as e:  # noqa: BLE001 - 后处理失败不中断主流程
        logger.warning("summarizer failed: %s", e)
        await emit("agent_speak", {"agent": "Summarizer", "status": "error", "error": str(e)})
        return fallback

    await emit("agent_speak", {"agent": "Summarizer", "status": "end"})
    content = (reply.content or "").strip()
    return content or fallback


def _build_single_task_context(question: str, state: "_RunState") -> dict[str, Any]:
    """Charter 用：DataAnalyst 单任务的 prompt 变量。"""
    exec_result = state.last_exec_result or {}
    columns = list(exec_result.get("columns") or [])
    rows = list(exec_result.get("rows") or [])
    row_count = int(exec_result.get("row_count") or len(rows))
    return {
        "question": question,
        "sql": state.last_sql or "",
        "columns": ", ".join(columns) if columns else "(无)",
        "row_count": row_count,
        "sample_rows": _format_sample_rows(columns, rows[:_SAMPLE_ROWS_LIMIT]),
    }


def _format_sub_tasks_block(
    sub_phases: list[tuple[str, "_DataAnalystPhase"]],
) -> str:
    """把 N 个 sub_task 执行详情拼成 Summarizer 的 {{sub_tasks_block}} 变量。

    每个 sub_task 是一小段 markdown：标题 + SQL（成功才有）+ 结果样例。失败
    的 sub_task 也列出来并附上失败原因，让 Summarizer 知道哪些维度没拿到数据。
    """
    if not sub_phases:
        return "（无子任务结果）"
    blocks: list[str] = []
    for idx, (sub_task, phase) in enumerate(sub_phases):
        header = f"### 子任务 {idx + 1}：{sub_task}"
        if not phase.is_success:
            blocks.append(f"{header}\n状态：失败\n原因：{phase.fail_reason or '未知'}")
            continue
        exec_result = phase.state.last_exec_result or {}
        columns = list(exec_result.get("columns") or [])
        rows = list(exec_result.get("rows") or [])
        row_count = int(exec_result.get("row_count") or len(rows))
        block = (
            f"{header}\n"
            f"SQL:\n```sql\n{phase.state.last_sql}\n```\n"
            f"共 {row_count} 行，列：{', '.join(columns) if columns else '(无)'}\n"
            f"样例：\n{_format_sample_rows(columns, rows[:_SAMPLE_ROWS_LIMIT])}"
        )
        blocks.append(block)
    return "\n\n".join(blocks)


def _format_sample_rows(columns: list[str], rows: list[Any]) -> str:
    """把前若干行拼成 Markdown 表格，便于 LLM 稳定解析。"""
    if not rows or not columns:
        return "(无数据)"
    header = "| " + " | ".join(columns) + " |"
    sep = "| " + " | ".join(["---"] * len(columns)) + " |"
    body_lines: list[str] = []
    for row in rows:
        if isinstance(row, dict):
            cells = [str(row.get(c, "")) for c in columns]
        elif isinstance(row, (list, tuple)):
            cells = [str(v) for v in row[: len(columns)]]
            cells += [""] * (len(columns) - len(cells))
        else:
            cells = [str(row)] + [""] * (len(columns) - 1)
        body_lines.append("| " + " | ".join(cells) + " |")
    return "\n".join([header, sep, *body_lines])


# --------------------------------------------------------------------------- #
# SSE 转发 + 状态累积
# --------------------------------------------------------------------------- #


class _RunState:
    """在 emit 回调里累积的跨事件状态。"""

    def __init__(
        self,
        sub_task_index: int | None = None,
        constraints: _RunConstraints | None = None,
    ) -> None:
        self.last_sql: str = ""
        self.last_exec_result: dict[str, Any] | None = None
        self.steps: list[dict[str, Any]] = []
        self.tool_calls: list[dict[str, Any]] = []
        self._open_step_round: int | None = None
        # 仅 team 模式下非 None——让 forwarder 把 sub_task_index 也注入到
        # tool_call / tool_result / agent_thought 等 payload 里，前端才能按子任务归组。
        self.sub_task_index: int | None = sub_task_index
        self.reports: list[dict[str, Any]] = []
        self.constraints = constraints


#: "sub_task 内部产生"的事件——在 team 模式下给它们统一贴 sub_task_index。
#: step 已在 run_team_stream 主协程里显式 tag，此处避免重复。
_SUB_TASK_SCOPED_EVENTS: tuple[str, ...] = (
    "tool_call", "tool_result", "agent_thought", "final_answer", "report",
)


def _make_forwarder(state: _RunState, emit: EmitCallback) -> EmitCallback:
    """生成一个 stream_callback：先做状态累积 / 老事件回灌，再转发给 emit。"""

    async def forward(event: str, payload: dict[str, Any]) -> None:
        if event == "tool_call":
            _on_tool_call(state, payload)
        elif event == "tool_result":
            _on_tool_result(state, payload)

        if state.sub_task_index is not None and event in _SUB_TASK_SCOPED_EVENTS:
            # 不破坏上游原有字段——只加一个前端可选消费的 tag。
            payload = {**payload, "sub_task_index": state.sub_task_index}

        await emit(event, payload)

        if event == "tool_result":
            await _maybe_emit_legacy_sql_result(state, payload, emit)
            await _maybe_emit_report(payload, emit, state)

    return forward


def _on_tool_call(state: _RunState, payload: dict[str, Any]) -> None:
    round_idx = int(payload.get("round") or 0)
    tool = str(payload.get("tool") or "tool")
    thought = str(payload.get("thought") or "")
    step = {
        "name": f"agent_round_{round_idx}",
        "label": f"调用工具 {tool}",
        "status": "running",
        "elapsed_ms": 0,
        "detail": thought[:200],
    }
    state.steps.append(step)
    state._open_step_round = round_idx
    rec = next(
        (
            it
            for it in state.tool_calls
            if int(it.get("round") or 0) == round_idx
            and int(it.get("sub_task_index") or -1) == int(state.sub_task_index or -1)
        ),
        None,
    )
    if rec is None:
        rec = {
            "round": round_idx,
            "sub_task_index": state.sub_task_index,
            "tool": tool,
            "args": payload.get("args") or {},
            "thought": thought,
        }
        state.tool_calls.append(rec)
    else:
        rec["tool"] = tool
        rec["args"] = payload.get("args") or rec.get("args") or {}
        rec["thought"] = thought or rec.get("thought", "")


def _on_tool_result(state: _RunState, payload: dict[str, Any]) -> None:
    round_idx = int(payload.get("round") or 0)
    success = bool(payload.get("success"))
    content = str(payload.get("content") or "")
    elapsed = int(payload.get("elapsed_ms") or 0)

    if state.steps and state._open_step_round == round_idx:
        step = state.steps[-1]
        step["status"] = "ok" if success else "error"
        step["elapsed_ms"] = elapsed
        if content:
            step["detail"] = content[:300]
    rec = next(
        (
            it
            for it in state.tool_calls
            if int(it.get("round") or 0) == round_idx
            and int(it.get("sub_task_index") or -1) == int(state.sub_task_index or -1)
        ),
        None,
    )
    if rec is None:
        rec = {
            "round": round_idx,
            "sub_task_index": state.sub_task_index,
            "tool": str(payload.get("tool") or ""),
        }
        state.tool_calls.append(rec)
    rec["success"] = success
    rec["content"] = content
    rec["data"] = payload.get("data")
    rec["elapsed_ms"] = elapsed

    data = payload.get("data") or {}
    # 第 1 步：仅记录并传递锁表线索（describe_table 成功后锁定该表），后续步骤再做守卫拦截。
    if payload.get("tool") == "describe_table" and success and isinstance(data, dict):
        table_name = str(data.get("name") or "").strip()
        if table_name and state.constraints is not None and table_name not in state.constraints.locked_tables:
            state.constraints.locked_tables.append(table_name)
            if state.sub_task_index is not None:
                state.constraints.source_sub_task_index = state.sub_task_index
            logger.info(
                "constraints_locked_table_added sub_task=%s table=%s locked_tables=%s",
                state.sub_task_index,
                table_name,
                state.constraints.locked_tables,
            )

    if (
        payload.get("tool") == "execute_sql"
        and success
        and isinstance(data, dict)
    ):
        sql_text = str(data.get("sql") or "")
        if sql_text:
            state.last_sql = sql_text
        if "columns" in data and "rows" in data:
            rows = data.get("rows") or []
            state.last_exec_result = {
                "columns": list(data.get("columns") or []),
                "rows": list(rows),
                "row_count": int(data.get("row_count") or len(rows)),
            }


async def _maybe_emit_legacy_sql_result(
    state: _RunState,
    payload: dict[str, Any],
    emit: EmitCallback,
) -> None:
    if payload.get("tool") != "execute_sql" or not payload.get("success"):
        return
    if state.last_exec_result is None or not state.last_sql:
        return
    await emit(
        "sql",
        {
            "sql": state.last_sql,
            "formatted_sql": state.last_sql,
            "tables": [],
            "chart_type": "table",
        },
    )
    await emit("result", state.last_exec_result)


async def _maybe_emit_report(
    payload: dict[str, Any],
    emit: EmitCallback,
    state: "_RunState | None" = None,
) -> None:
    data = payload.get("data")
    if not isinstance(data, dict):
        return
    if data.get("output_type") != "html":
        return
    html = str(data.get("html") or "")
    if not html.strip():
        return
    if state is not None and state.constraints is not None:
        locked = list(state.constraints.locked_tables or [])
        if locked and not _sql_hits_locked_tables(state.last_sql or "", locked):
            warn = (
                f"报告已拦截：当前报告来源 SQL 未命中锁定表 {locked}。"
                f" 当前 SQL={state.last_sql or '(空)'}"
            )
            logger.warning(warn)
            await emit("error", {"error": warn})
            return
    report_payload: dict[str, Any] = {
        "title": str(data.get("title") or "Report"),
        "html": html,
        "mode": str(data.get("mode") or "inline"),
        "agent": payload.get("agent"),
    }
    if payload.get("sub_task_index") is not None:
        report_payload["sub_task_index"] = payload.get("sub_task_index")
    if state is not None:
        state.reports.append(dict(report_payload))
    await emit("report", report_payload)


# --------------------------------------------------------------------------- #
# 持久化
# --------------------------------------------------------------------------- #


def _persist_sync(
    *,
    request: ChatRequest,
    current_user_id: int,
    question: str,
    sql: str,
    sql_error: str | None,
    exec_result: dict[str, Any] | None,
    is_success: bool,
    reasoning: str,
    steps: list[dict[str, Any]],
    chart_type: str = "table",
    chart_config: dict[str, Any] | None = None,
    agent_mode: str | None = None,
    plans: list[str] | None = None,
    sub_task_agents: list[str] | None = None,
    plan_states: list[dict[str, Any]] | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
    summary: str | None = None,
    reports: list[dict[str, Any]] | None = None,
) -> int:
    """在工作线程里开短事务并写 record。失败吞掉返回 0。"""
    if not request.conversation_id:
        return 0
    try:
        from src.chat.crud import chat as chat_crud
        from src.common.core.database import get_db_session

        with get_db_session() as session:
            record = chat_crud.create_conversation_record(
                session=session,
                conversation_id=request.conversation_id,
                user_id=current_user_id,
                question=question,
                sql=sql or None,
                sql_error=sql_error,
                exec_result=exec_result,
                chart_type=chart_type,
                chart_config=chart_config,
                is_success=is_success,
                reasoning=reasoning or None,
                steps=steps or None,
                agent_mode=agent_mode,
                plans=plans,
                sub_task_agents=sub_task_agents,
                plan_states=plan_states,
                tool_calls=tool_calls,
                summary=summary,
                reports=reports,
            )
            return record.id or 0
    except Exception as e:  # noqa: BLE001
        logger.warning("persist agent record failed: %s", e)
        return 0


__all__ = ["run_agent_stream", "run_team_stream", "EmitCallback"]
