"""Run DeepAgents inside FastAPI, reusing the existing domain tools and SSE payloads."""

import asyncio
import json
import logging
import re
from contextlib import asynccontextmanager, nullcontext
from time import monotonic
from typing import Annotated, Literal, NotRequired
from urllib.parse import urlsplit
from uuid import uuid4

from deepagents import create_deep_agent
from langchain.agents.middleware import (
    AgentState,
    ExtendedModelResponse,
    TodoListMiddleware,
    after_model,
    before_agent,
    wrap_model_call,
    wrap_tool_call,
)
from langchain.agents.middleware.types import PrivateStateAttr
from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, ToolMessage
from langchain_core.messages.utils import count_tokens_approximately
from langchain_core.tools import tool
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.errors import GraphRecursionError
from langgraph.managed.is_last_step import RemainingStepsManager
from langgraph.types import Command, interrupt
from psycopg import AsyncConnection
from sqlalchemy.engine import make_url

from datasource.service.edu_permission import edu_scope_dict_for_user_id
from src.agent.adapter.usage_sink import EmitFn, usage_tracking
from src.agent.audit.tool_call_log import log_tool_call_fire_and_forget
from src.agent.education.scope_guard import out_of_scope_question
from src.agent.education.tools import build_chart_option_tool
from src.chat.schemas import ChatRequest
from src.chat.service.semantic_query import (
    SemanticFieldError,
    SemanticQuery,
    SemanticQueryError,
    SemanticViewDescriptionRequiredError,
)
from src.chat.service.turn_records import persist_turn
from src.common.core.config import get_settings
from src.datasource.edu_connection import edu_db_connection
from src.llm.service import get_default_llm

logger = logging.getLogger(__name__)

_STEP_LIMIT_MESSAGE = "本轮分析已达到执行步数上限。"
# 支持用工具名强制 tool_choice 的 OpenAI 兼容网关。
_NAMED_TOOL_CHOICE_HOSTS = frozenset(
    {
        None,
        "api.openai.com",
        "api.deepseek.com",
        "api.minimaxi.com",
        "api.minimax.chat",
        "api.minimax.io",
    }
)
# 只接受 "required" 的网关（不接受具名对象）。
_REQUIRED_TOOL_CHOICE_HOSTS = frozenset({"api.moonshot.ai", "api.moonshot.cn"})
_MINIMAX_MARKER = "]<]minimax[>["
_TOOL_PROTOCOL_BLOCK = re.compile(
    rf"(?:{re.escape(_MINIMAX_MARKER)})?<(?:minimax:)?tool_call\b[^>]*>.*?"
    r"(?:</(?:minimax:)?tool_call\s*>|$)", re.IGNORECASE | re.DOTALL,
)
_PLAN_FAILURE = "当前模型未能发布步骤规划，请重试或更换支持工具调用的模型。"
_MODEL_REPLY_FAILURE = "本轮未能生成分析结论。"
# 先缓冲少量正文再开始下发：工具调用轮次常以一句短说明开头，
# 缓冲区可避免它先闪现再被 answer_discard 清掉。
_STREAM_PREFIX_CHARS = 24
_PROMPT = (
    "你是 Awesome-DB 的数据分析助手。用工具查询数据，按工具返回的范围和口径作答。"
    "业务事实和数值必须来自本轮 query_semantic 返回的数据。"
    "发布目录已在系统提示中给出。每个查询阶段按需选出相关对象，"
    "通过 describe_semantic_model 的 names 一次读取新对象的字段、说明和关联，复用本轮已读描述。"
    "首次使用视图建议先读取字段；直接查询时宿主会自动补读，以报错信息里的可用字段为准。"
    "先确认指标、筛选条件和关联键的来源，再提交 query_semantic。"
    "只使用发布目录中不带 schema 的逻辑名称和已确认的字段；禁止查询系统目录、物理表或用 SELECT * 探查。"
    "跨模型查询用来源别名限定字段。收到 FIELD_WRONG_SOURCE 或 FIELD_NOT_PUBLISHED 时，"
    "根据候选的字段说明和关联只修正出错的引用。避免原样重提。口径无法确认时请澄清。"
    "优先使用符合口径的已发布视图；视图缺少所需字段时，查阅已发布逻辑模型及关联。"
    "窗口函数的分组和排序使用来源别名限定字段。已有指标直接引用，加工后的表达式使用不同的输出别名。"
    "DataSemantic 负责将语义 SQL 规划为物理 SQL，宿主校验权限并执行。"
    "对发布物未定义的业务指标，说明口径缺失。禁止猜测字段、值和数值。"
    "条件缺失或有歧义时调用 ask_human。区分零值、无数据、截断与失败。"
    "任务计划和执行说明用业务语言描述正在做什么，省略 SQL、表名和字段名。"
    "用最少的查询取得回答所需的数据。对于跨考试趋势，优先查已发布的汇总视图，"
    "一次确认学校及考试范围，再按批次和所需维度合并统计。已有的标识、口径和结果直接复用。"
    "只补查影响结论的缺口，避免逐批次、逐科目查询或反复枚举班级、选科等无关维度。"
    "取得足够数据后直接作答。仅在能明确修正口径时重试；仍缺数据时说明范围。"
)


def _tool_choice_for(model, forced_tool: str) -> str:
    """按网关能力选择 tool_choice：具名强制 / required / auto。"""
    hostname = urlsplit(str(getattr(model, "openai_api_base", None) or "")).hostname
    if hostname in _NAMED_TOOL_CHOICE_HOSTS:
        return forced_tool
    if hostname in _REQUIRED_TOOL_CHOICE_HOSTS:
        return "required"
    return "auto"


def _visible_model_text(text: str) -> str:
    text = re.sub(r"<think\b[^>]*>.*?(?:</think>|$)", "", text, flags=re.I | re.S)
    return _TOOL_PROTOCOL_BLOCK.sub("", text).split(_MINIMAX_MARKER, 1)[0].strip()


def _chunk_text(chunk) -> str:
    """提取流式分片里的正文；思考内容和工具参数分片不计入答案。"""
    content = getattr(chunk, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") in {"text", "text_delta"}:
                parts.append(str(block.get("text") or ""))
        return "".join(parts)
    return ""


def _chunk_reasoning(chunk) -> str:
    """服务商单独下发的思考分片（MiniMax/DeepSeek 的 reasoning_content）。"""
    kwargs = getattr(chunk, "additional_kwargs", None) or {}
    for key in ("reasoning_content", "reasoning"):
        value = kwargs.get(key)
        if isinstance(value, str) and value:
            return value
    content = getattr(chunk, "content", None)
    if isinstance(content, list):
        return "".join(
            str(block.get("text") or "")
            for block in content
            if isinstance(block, dict) and block.get("type") in {"reasoning", "reasoning_content"}
        )
    return ""


_THINK_TAG = "<think"
_THINK_END_TAG = "</think"


def _drain_think(buf: str, in_think: bool) -> tuple[str, str, str, bool]:
    """把正文流切成 (思考, 正文)，并保留可能是半个标签的尾巴。

    兼容 ``<think>`` / ``</think>`` 被分片切开的情况；未闭合时余下内容都算思考。
    """
    reasoning: list[str] = []
    answer: list[str] = []
    while buf:
        tag = _THINK_END_TAG if in_think else _THINK_TAG
        index = buf.lower().find(tag)
        if index < 0:
            head = buf.lower()
            keep = 0
            for size in range(1, len(tag)):
                if head.endswith(tag[:size]):
                    keep = size
            segment = buf[:-keep] if keep else buf
            (reasoning if in_think else answer).append(segment)
            buf = buf[-keep:] if keep else ""
            break
        (reasoning if in_think else answer).append(buf[:index])
        close = buf.find(">", index + len(tag))
        if close < 0:
            buf = buf[index:]
            break
        buf = buf[close + 1:]
        in_think = not in_think
    return "".join(reasoning), "".join(answer), buf, in_think


def _planning_required(text: str) -> bool:
    """只有多步骤/多维度的问题才强制发布计划，简单查询直接作答。"""
    value = text.strip()
    if re.fullmatch(r"(?:你好|您好|早上好|晚上好|谢谢|感谢|再见|hi|hello|thanks)[！!。.?？\s]*", value, re.I):
        return False
    if len(value) <= 200 and re.match(r"^(?:解释|介绍|什么是|为什么|what is\b|explain\b)", value, re.I):
        if not re.search(r"附件|文件|生成|创建|修改|保存|搜索|检索|最新|查询|运行|执行|删除|发送|报告|file|create|write|search|run|delete|send", value, re.I):
            return False
    if value.count("？") + value.count("?") >= 2:
        return True
    if len(value) >= 40:
        return True
    return bool(
        re.search(
            r"分别|逐个|对比|比较|趋势|排名|排序|报告|归因|各科|各校|各班|各年级|"
            r"多个|以及|同时|并且|然后再|分阶段",
            value,
        )
    )


class _ChatState(AgentState):
    remaining_steps: Annotated[int, PrivateStateAttr, RemainingStepsManager]
    chat_model_calls: NotRequired[Annotated[int, PrivateStateAttr]]
    chat_model_tokens: NotRequired[Annotated[int, PrivateStateAttr]]


class _ModelCallLimitError(RuntimeError):
    pass


class _ModelCallBudget(AsyncCallbackHandler):
    raise_error = True

    def __init__(self, limit: int | None, token_limit: int | None):
        self.limit = limit
        self.calls = 0
        self.token_limit = token_limit
        self.tokens = 0
        self.prompts = {}
        self.message = f"本轮分析已达到模型调用上限（{limit} 次）。"

    async def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
        if (serialized or {}).get("name") == "FallbackChatModel":
            return
        if self.limit is not None and self.calls >= self.limit:
            raise _ModelCallLimitError(self.message)
        if self.token_limit is not None and self.tokens >= self.token_limit:
            raise _ModelCallLimitError("本轮分析已达到 Token 上限。")
        self.calls += 1
        if self.token_limit is not None:
            prompt = count_tokens_approximately(
                [message for group in messages for message in group],
                tools=(kwargs.get("invocation_params") or {}).get("tools"),
            )
            if self.tokens + sum(self.prompts.values()) + prompt > self.token_limit:
                raise _ModelCallLimitError("本轮分析已达到 Token 上限。")
            self.prompts[run_id] = prompt

    async def on_llm_end(self, response, *, run_id, **kwargs):
        prompt = self.prompts.pop(run_id, None)
        if prompt is None:
            return
        usage = {}
        for group in response.generations:
            for generation in group:
                usage = getattr(getattr(generation, "message", None), "usage_metadata", None) or usage
        output = sum(
            max(1, len(generation.text) // 3)
            for group in response.generations for generation in group
        )
        self.tokens += int(usage.get("total_tokens") or prompt + output)
        if self.token_limit is not None and self.tokens > self.token_limit:
            raise _ModelCallLimitError("本轮分析已达到 Token 上限。")

    async def on_llm_error(self, error, *, run_id, **kwargs):
        self.prompts.pop(run_id, None)


@tool
def ask_human(question: str) -> str:
    """Ask the user for a missing detail and wait for their reply."""
    return interrupt({"kind": "ask_human", "question": question})


def _conninfo(database_url: str) -> str:
    url = make_url(database_url)
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


@asynccontextmanager
async def open_chat_checkpointer(database_url: str):
    async with AsyncPostgresSaver.from_conn_string(_conninfo(database_url)) as saver:
        # Serializes the checkpoint schema migrations across FastAPI workers.
        async with _thread_lock(database_url, "awesome:deepagents:setup", wait=True):
            await saver.setup()
        yield saver


@asynccontextmanager
async def _thread_lock(database_url: str, key: str, *, wait: bool = False):
    async with await AsyncConnection.connect(_conninfo(database_url), autocommit=True) as conn:
        if wait:
            await conn.execute("SELECT pg_advisory_lock(hashtextextended(%s, 0))", (key,))
        else:
            cursor = await conn.execute(
                "SELECT pg_try_advisory_lock(hashtextextended(%s, 0))", (key,)
            )
            if not (await cursor.fetchone())[0]:
                raise ValueError("当前会话已有查询正在执行，请稍后重试。")
        yield


async def run_chat_stream(
    *,
    request: ChatRequest,
    current_user_id: int,
    workspace_oid: int,
    emit: EmitFn,
    checkpointer,
    model=None,
    execute: bool = True,
) -> int:
    """Serialize each conversation's updates and use the same thread to resume an interrupt."""
    settings = get_settings()
    conversation_key = request.conversation_id or uuid4().hex
    thread = f"awesome:deepagents:v1:{workspace_oid}:{current_user_id}:{request.datasource_id}:{conversation_key}"
    lock = _thread_lock(settings.database_url, thread) if request.conversation_id else nullcontext()
    async with lock:
        async with usage_tracking(emit) as usage:
            try:
                return await _run_turn(
                    request,
                    current_user_id,
                    workspace_oid,
                    emit,
                    checkpointer,
                    model,
                    thread,
                    usage,
                    execute,
                )
            finally:
                if not request.conversation_id:
                    await checkpointer.adelete_thread(thread)


async def _run_turn(
    request,
    user_id,
    workspace_oid,
    emit,
    checkpointer,
    model,
    thread,
    usage,
    execute,
):
    settings = get_settings()
    datasource_id = request.datasource_id
    semantic = SemanticQuery(settings, datasource_id, workspace_oid, user_id)
    try:
        context = await asyncio.to_thread(semantic.context)
    except SemanticQueryError as exc:
        await emit("error", {"error": str(exc)})
        return 0
    directory = {key: context[key] for key in ("bundle_id", "manifest_hash", "dialect") if key in context}
    for kind in ("models", "views"):
        directory[kind] = [
            {key: item[key] for key in ("name", "description") if key in item}
            for item in context.get(kind, [])
        ]
    context_json = json.dumps(directory, ensure_ascii=False, sort_keys=True)
    preparation_error = ""
    protocol_retried = False
    model_reply_error = ""
    scope = await asyncio.to_thread(edu_scope_dict_for_user_id, user_id)
    last_sql = ""
    last_result = None
    steps = []
    tool_calls = []
    query_error = ""
    evidence = []
    todos = []
    planning_attempts = 0
    needs_plan = execute and _planning_required(request.question)
    budget = (
        _ModelCallBudget(settings.chat_max_steps, settings.chat_max_total_tokens)
        if settings.chat_max_steps is not None or settings.chat_max_total_tokens is not None
        else None
    )
    limit_message = ""
    chart_type, chart_config = "table", None

    def active_stage_index():
        return next((i for i, todo in enumerate(todos) if todo["status"] == "in_progress"), None)

    @tool
    async def get_analysis_context() -> str:
        """重新读取当前发布的模型和视图目录。目录通常已随系统提示给出，仅在需要核对时调用。"""
        return context_json

    @tool
    async def describe_semantic_model(name: str | None = None, names: list[str] | None = None) -> str:
        """读取语义对象的字段、口径和关联。用 name 读取单个对象，或用 names 批量读取至多 12 个对象。"""
        if (name is None) == (names is None):
            return "请提供 name 或 names 中的一项。"
        try:
            item = await (
                asyncio.to_thread(semantic.describe_many, names) if names is not None
                else asyncio.to_thread(semantic.describe, name)
            )
        except SemanticQueryError as exc:
            return str(exc)
        return json.dumps(item, ensure_ascii=False)

    @tool
    async def query_semantic(sql: str) -> str:
        """提交一条只读语义 SQL，显式列出已确认的字段。DataSemantic 负责物理规划，宿主按权限执行。"""
        nonlocal query_error, last_sql, last_result
        round_number = len(tool_calls) + 1
        meta = {"tool": "query_semantic", "round": round_number, "agent": "DeepAgents"}
        if (stage_index := active_stage_index()) is not None:
            meta["sub_task_index"] = stage_index
            meta["sub_task"] = todos[stage_index]["content"]
        call = {**meta, "args": {"sql": sql}}
        step = {
            "name": f"agent_round_{round_number}",
            "label": "调用工具 query_semantic",
            "status": "running",
            "elapsed_ms": 0,
            "detail": "",
        }
        tool_calls.append(call)
        steps.append(step)
        await emit("tool_call", dict(call))
        started = monotonic()
        try:
            try:
                result = await asyncio.to_thread(semantic.run, sql, execute=execute)
            except SemanticViewDescriptionRequiredError as exc:
                # 同批 describe+query 或直接查询视图：宿主补读字段后重试一次，省掉一轮模型往返。
                unread_views = list(exc.names)
                if not unread_views:
                    raise
                await asyncio.to_thread(
                    lambda: [semantic.describe(name) for name in unread_views]
                )
                result = await asyncio.to_thread(semantic.run, sql, execute=execute)
        except SemanticQueryError as exc:
            query_error = str(exc)
            result = {"error": query_error}
            if isinstance(exc, SemanticFieldError):
                result["error_detail"] = exc.detail
        else:
            last_sql = result["sql"]
            last_result = result if execute else None
            await emit(
                "sql",
                {**result, "chart_type": "table"}
                if not execute
                else {
                    "sql": result["sql"],
                    "tables": result["tables"],
                    "chart_type": "table",
                    "bundle_id": result["bundle_id"],
                    "manifest_hash": result["manifest_hash"],
                    "plan_request_id": result.get("plan_request_id"),
                },
            )
            if execute:
                await emit("result", result)
            evidence.append(result)
        elapsed_ms = int((monotonic() - started) * 1000)
        success = "error" not in result
        content = result.get("error", "")
        call.update(success=success, content=content, data=result, elapsed_ms=elapsed_ms)
        step.update(
            status="ok" if success else "error", detail=content[:300], elapsed_ms=elapsed_ms
        )
        await emit("tool_result", {key: value for key, value in call.items() if key != "args"})
        log_tool_call_fire_and_forget(
            agent_name="DeepAgents",
            round_idx=meta["round"],
            sub_task_index=None,
            tool_name=meta["tool"],
            success=success,
            elapsed_ms=elapsed_ms,
            args={"sql": sql},
            result_preview=result.get("error", ""),
        )
        if "error" in result:
            return json.dumps(result, ensure_ascii=False) if "error_detail" in result else content
        if not execute:
            return "DataSemantic 已成功生成并校验计划。"
        preview = {
            "columns": result["columns"],
            "rows": result["rows"][:50],
            "preview_truncated": len(result["rows"]) > 50 or result.get("rows_truncated", False),
        }
        return json.dumps(preview, ensure_ascii=False)

    @tool
    async def chart_semantic_result(
        x_column: str,
        y_columns: list[str],
        kind: Literal["line", "bar"],
        title: str = "",
    ) -> str:
        """使用最近一次语义查询结果的列生成折线图或柱状图。"""
        nonlocal chart_type, chart_config
        if not last_result or not last_result["rows"]:
            return "请先查询可用于绘图的数据。"
        rows = last_result["rows"]
        columns = last_result["columns"]
        if not y_columns or not {x_column, *y_columns} <= set(columns):
            return "请选择结果中已有的维度列和数值列。"
        labels = [str(row[columns.index(x_column)]) for row in rows]
        try:
            series = [
                {
                    "name": column,
                    "values": [
                        float(row[columns.index(column)])
                        if row[columns.index(column)] is not None
                        else None
                        for row in rows
                    ],
                }
                for column in y_columns
            ]
        except (TypeError, ValueError):
            return "纵轴列必须为数值。"
        if kind == "line":
            chart = await build_chart_option_tool.execute(
                chart_type="trend_line",
                data={"x_labels": labels, "series": series},
                title=title,
            )
        else:
            chart = await build_chart_option_tool.execute(
                chart_type="subject_bar",
                data={"subjects": labels, "metrics": series},
                title=title,
            )
        if chart.data.get("error"):
            return chart.content
        chart_type, chart_config = chart.data["chart_type"], json.loads(chart.data["option"])
        await emit("chart", {"chart_type": chart_type, "chart_config": chart_config})
        return chart.content

    tools = [get_analysis_context, describe_semantic_model, query_semantic]
    if execute:
        tools.append(chart_semantic_result)
    if request.conversation_id and execute:
        tools.append(ask_human)
    # Restrict both model binding and tool execution, including DeepAgents' built-in task delegation.
    allowed = {item.name for item in tools}
    if execute:
        allowed.add("write_todos")

    @before_agent
    async def reset_task_plan(state, runtime):
        # A new turn starts at entry. Command(resume=...) continues the existing graph.
        return {"todos": []}

    @after_model(can_jump_to=["model", "end"])
    async def require_plan_or_repair(state, runtime):
        """只在需要计划时补一次规划；不再强制先读上下文。"""
        nonlocal preparation_error
        nonlocal model_reply_error, protocol_retried
        message = state["messages"][-1]
        if limit_message or preparation_error or not isinstance(message, AIMessage):
            return None
        if not message.tool_calls and (
            message.invalid_tool_calls
            or _MINIMAX_MARKER in message.text
            or _TOOL_PROTOCOL_BLOCK.search(message.text)
        ):
            update = {"messages": [RemoveMessage(id=message.id)]}
            if protocol_retried:
                model_reply_error = _MODEL_REPLY_FAILURE
                return {**update, "jump_to": "end"}
            protocol_retried = True
            return {**update, "jump_to": "model"}
        if needs_plan and not state.get("todos") and (
            not message.tool_calls or any(
                call["name"] not in {"write_todos", "ask_human"} for call in message.tool_calls
            )
        ):
            update = {"messages": [RemoveMessage(id=message.id)]}
            if planning_attempts >= 3:
                preparation_error = _PLAN_FAILURE
                return {**update, "jump_to": "end"}
            return {**update, "jump_to": "model"}
        return None

    @wrap_model_call(state_schema=_ChatState)
    async def restrict_model_tools(model_request, handler):
        nonlocal limit_message, preparation_error, planning_attempts
        if budget is not None and budget.limit is not None and budget.calls >= budget.limit:
            limit_message = budget.message
            return AIMessage(content=limit_message)
        # Reserve graph steps for the final model message and DeepAgents' after-agent hooks.
        if model_request.state["remaining_steps"] <= 3:
            limit_message = _STEP_LIMIT_MESSAGE
            return AIMessage(content=limit_message)
        model_tools = [t for t in model_request.tools if t.name in allowed]
        if needs_plan and not model_request.state.get("todos"):
            planning_attempts += 1
            if planning_attempts > 3:
                preparation_error = _PLAN_FAILURE
                return AIMessage(content=preparation_error)
            choice = _tool_choice_for(model_request.model, "write_todos")
            plan_tools = (
                {"write_todos"} if choice == "write_todos" else {"write_todos", "ask_human"}
            )
            model_request = model_request.override(
                tools=[t for t in model_tools if t.name in plan_tools],
                tool_choice=choice,
                system_prompt=(
                    f"{model_request.system_prompt}\n请先调用 write_todos 发布至少一个计划步骤。"
                    "如果当前工具列表包含 ask_human，缺少关键条件时可先向用户澄清。"
                ),
            )
        else:
            planning_attempts = 0
            model_request = model_request.override(tools=model_tools)
        if protocol_retried:
            model_request = model_request.override(system_prompt=(
                f"{model_request.system_prompt}\n请用正常文字作答。若还需要且当前允许调用工具，"
                "请通过结构化工具接口调用，禁止在正文中写出工具调用标签和参数。"
            ))
        if execute:
            await emit("step", {"name": "deepagents_model", "label": "正在分析", "status": "running"})
        response = await handler(model_request)
        if budget is None and not (
            model_request.state.get("chat_model_calls") or model_request.state.get("chat_model_tokens")
        ):
            return response
        # Save the count with the model response so clarification can resume the same budget.
        return ExtendedModelResponse(
            model_response=response, command=Command(update={
                "chat_model_calls": budget.calls if budget else 0,
                "chat_model_tokens": budget.tokens if budget else 0,
            })
        )

    @wrap_tool_call
    async def restrict_tool_call(tool_request, handler):
        name = tool_request.tool_call["name"]
        last_message = tool_request.state["messages"][-1]
        calls = last_message.tool_calls if isinstance(last_message, AIMessage) else []
        asking = any(call["name"] == "ask_human" for call in calls)
        if name not in allowed or (
            asking and (len(calls) != 1 or any(c["tool"] == "query_semantic" for c in tool_calls))
        ):
            return ToolMessage(
                content="此操作不可用。若已查询且仍缺信息，请直接询问用户。",
                tool_call_id=tool_request.tool_call["id"],
                status="error",
            )
        if name == "query_semantic":
            if any(call["name"] == "write_todos" for call in calls):
                return ToolMessage(
                    content="请等待任务计划更新后，再为当前阶段提交查询。",
                    tool_call_id=tool_request.tool_call["id"],
                    status="error",
                )
            return await handler(tool_request)
        if not execute:
            return await handler(tool_request)
        call = {"tool": name, "round": len(tool_calls) + 1, "agent": "DeepAgents"}
        if name != "write_todos" and (stage_index := active_stage_index()) is not None:
            call["sub_task_index"] = stage_index
            call["sub_task"] = todos[stage_index]["content"]
        tool_calls.append(call)
        await emit("tool_call", dict(call))
        started = monotonic()
        result = await handler(tool_request)
        failed = isinstance(result, ToolMessage) and result.status == "error"
        call.update(
            success=not failed,
            content="工具执行未完成" if failed else "",
            elapsed_ms=int((monotonic() - started) * 1000),
        )
        await emit("tool_result", dict(call))
        return result

    prompt = (
        f"{_PROMPT}\n当前授权范围参考：{scope}。\n"
        f"已发布目录（bundle_id / manifest_hash / dialect / models / views）：{context_json}"
    )
    middleware = [require_plan_or_repair, restrict_model_tools, restrict_tool_call]
    if execute:
        prompt += (
            "\n复杂问题先用 write_todos 发布具体计划，首步设为 in_progress，"
            "后续步骤设为 pending。每完成一个阶段就更新清单，并开始下一阶段；只把已核实的阶段设为 completed，"
            "完成最后一次更新后再发最终答复。简单问题不必发布计划，直接查询作答。"
        )
        middleware = [TodoListMiddleware(), reset_task_plan, *middleware]
    if request.report_audience:
        prompt += f"\n报告受众：{request.report_audience}。"
    if model is None:
        model = get_default_llm()._llm
        # ChatOpenAI does not round-trip DeepSeek's reasoning_content during tool calls.
        if (
            urlsplit(str(getattr(model, "openai_api_base", None) or "")).hostname
            == "api.deepseek.com"
        ):
            model = model.model_copy(
                update={
                    "use_responses_api": False,
                    "extra_body": {**(model.extra_body or {}), "thinking": {"type": "disabled"}},
                }
            )
    graph = create_deep_agent(
        model=model,
        tools=tools,
        system_prompt=prompt,
        middleware=middleware,
        checkpointer=checkpointer,
    )
    config = {"configurable": {"thread_id": thread}}
    if budget is not None:
        config["callbacks"] = [budget]
        if budget.limit is not None:
            config["recursion_limit"] = 4 * budget.limit + 20
    snapshot = await graph.aget_state(config)
    history = snapshot.values.get("messages", [])
    original_question = request.question
    pending_interrupt = snapshot.interrupts[0] if snapshot.interrupts else None
    new_question = request.question.strip().startswith(
        ("换个问题", "换一个问题", "新问题", "另外问", "重新提问", "忽略上面")
    )
    if (
        request.resume_interrupt_id
        and not new_question
        and (pending_interrupt is None or request.resume_interrupt_id != pending_interrupt.id)
    ):
        await emit("error", {"error": "这条追问已经处理或失效，请重新提问。"})
        return 0
    if pending_interrupt and not new_question:
        original_question = next(
            (message.content for message in reversed(history) if isinstance(message, HumanMessage)),
            request.question,
        )
        denial = await asyncio.to_thread(
            out_of_scope_question,
            scope,
            original_question,
            datasource_id=datasource_id,
            workspace_oid=workspace_oid,
            connection_factory=edu_db_connection,
        )
        if denial:
            await emit("error", {"error": denial})
            return 0
        needs_plan = execute and _planning_required(original_question)
        if budget is not None:
            budget.calls = snapshot.values.get("chat_model_calls", 0)
            budget.tokens = snapshot.values.get("chat_model_tokens", 0)
        payload = Command(resume={pending_interrupt.id: request.question})
        todos = snapshot.values.get("todos") or []
        if todos:
            await emit("task_plan", {"stages": todos})
    else:
        if snapshot.next:
            await checkpointer.adelete_thread(thread)
            history = []
        # Earlier result rows are re-queried under current permissions when needed.
        remove = [
            RemoveMessage(id=m.id) for m in history if m.id and not isinstance(m, HumanMessage)
        ]
        replies = [
            HumanMessage(content=m.content)
            for m in history
            if isinstance(m, ToolMessage) and m.name == "ask_human"
        ]
        payload = {"messages": [*remove, *replies, HumanMessage(content=request.question)]}

    preparation_step = {"name": "prepare_agent", "label": "准备智能体", "status": "ok", "round": 0}
    steps.append(preparation_step)
    await emit("step", preparation_step)
    answer = ""
    pending = None
    # 已下发给前端的草稿正文；工具调用轮次结束后用 answer_discard 清掉。
    streamed_text = ""
    buffered_text = ""
    # <think> 标签可能被分片切开，carry 保存待续的半截标签。
    think_carry = ""
    in_think = False
    reasoning_started = False
    reasoning_in_call = False
    usage_reported = False

    def answer_streamable() -> bool:
        """已有查询结果、计划就绪后的正文才下发。

        先有 evidence 再流式，可避免把寒暄类答复先刷出来、再被“未取得查询结果”覆盖。
        """
        return bool(
            execute
            and evidence
            and not limit_message
            and not preparation_error
            and (not needs_plan or todos)
        )

    try:
        async for mode, update in graph.astream(
            payload, config, stream_mode=["updates", "messages"]
        ):
            if mode == "messages":
                chunk, meta = update
                if (meta or {}).get("langgraph_node") != "model":
                    continue
                # 流式回复的 usage 由末尾分片携带；拿不到时退回 updates 里的整条消息。
                if usage_info := getattr(chunk, "usage_metadata", None):
                    usage_reported = True
                    await usage.record(
                        usage_info.get("input_tokens") or 0,
                        usage_info.get("output_tokens") or 0,
                        usage_info.get("total_tokens") or 0,
                    )
                if getattr(chunk, "tool_call_chunks", None):
                    # 工具调用参数不属于答案正文，丢弃尚未下发的缓冲。
                    buffered_text = ""
                    continue
                reasoning = _chunk_reasoning(chunk) if execute else ""
                text = _chunk_text(chunk)
                if text:
                    inline_think, text, think_carry, in_think = _drain_think(
                        think_carry + text, in_think
                    )
                    reasoning = f"{reasoning}{inline_think}"
                if reasoning:
                    # 新一轮模型调用的思考另起一段，便于阅读整轮推理轨迹。
                    separator = "\n\n" if reasoning_started and not reasoning_in_call else ""
                    await emit("reasoning_delta", {"text": f"{separator}{reasoning}"})
                    reasoning_started = True
                    reasoning_in_call = True
                if not text or not answer_streamable():
                    continue
                buffered_text += text
                if streamed_text or len(buffered_text) >= _STREAM_PREFIX_CHARS:
                    await emit("answer_delta", {"text": buffered_text})
                    streamed_text += buffered_text
                    buffered_text = ""
                continue
            if update.get("__interrupt__"):
                pending = update["__interrupt__"][0]
                continue
            for node in update.values():
                if not isinstance(node, dict):
                    continue
                if "todos" in node and node["todos"] != todos:
                    todos = node["todos"]
                    await emit("task_plan", {"stages": todos})
                for message in node.get("messages", []):
                    if not isinstance(message, AIMessage):
                        continue
                    if message.usage_metadata and not usage_reported:
                        usage_info = message.usage_metadata
                        await usage.record(
                            usage_info["input_tokens"],
                            usage_info["output_tokens"],
                            usage_info["total_tokens"],
                        )
                    usage_reported = False
                    if not message.tool_calls:
                        answer = _visible_model_text(message.text)
                    elif execute:
                        note = _visible_model_text(message.text)
                        if note:
                            step = {
                                "name": "analysis_update", "label": "分析思路", "status": "ok",
                                "detail": note[:1000], "round": len(tool_calls) + 1,
                            }
                            steps.append(step)
                            await emit("step", step)
                    # 这一次模型调用结束：草稿要么成为结论，要么作废，不能带到下一次调用。
                    buffered_text = ""
                    think_carry = ""
                    in_think = False
                    reasoning_in_call = False
                    if streamed_text and (
                        message.tool_calls
                        or message.invalid_tool_calls
                        or _MINIMAX_MARKER in message.text
                        or _TOOL_PROTOCOL_BLOCK.search(message.text)
                    ):
                        await emit("answer_discard", {})
                        streamed_text = ""
    except _ModelCallLimitError as exc:
        limit_message = str(exc)
    except GraphRecursionError:
        logger.warning("DeepAgents exhausted the LangGraph step limit")
        limit_message = _STEP_LIMIT_MESSAGE

    if pending:
        if tool_calls and tool_calls[-1]["tool"] == "ask_human" and "success" not in tool_calls[-1]:
            tool_calls[-1].update(success=True, content="等待用户补充信息")
            await emit("tool_result", dict(tool_calls[-1]))
        answer = str(pending.value["question"])
        last_result = {
            "clarify": True,
            "prompt": answer,
            "missing": ["scope"],
            "options": {},
            "filled": {},
            "original_question": original_question,
            "interrupt_id": pending.id,
        }
        await emit("clarify", last_result)
    if not pending and not evidence:
        stop_reason = limit_message
        answer = (
            preparation_error
            or query_error
            or model_reply_error
            or (
                stop_reason + "未取得 DataSemantic 查询结果，请缩小查询范围后重试。"
                if stop_reason
                else "本轮未取得 DataSemantic 查询结果，请补充查询条件或检查语义模型覆盖范围。"
            )
        )
        await emit("error", {"error": answer})
    elif execute and not pending and limit_message:
        answer = limit_message + "请查看本轮已返回的数据。"
    elif execute and not pending and not any(result["rows"] for result in evidence):
        answer = "DataSemantic 在当前条件和数据权限范围内未返回记录。"
    elif execute and not pending and model_reply_error:
        answer = model_reply_error + "请查看本轮已返回的查询结果。"
    elif execute and not answer:
        model_reply_error = _MODEL_REPLY_FAILURE
        answer = model_reply_error + "请查看本轮已返回的查询结果。"
    if execute and any(result.get("rows_truncated") for result in evidence):
        answer += "\n查询结果已截断，以上结论仅覆盖返回的数据。"
    if not execute:
        return 0
    succeeded = bool(pending or (evidence and not (limit_message or model_reply_error)))
    await emit(
        "step",
        {
            "name": "deepagents",
            "label": "分析问题",
            "status": "ok" if succeeded else "error",
        },
    )
    await emit("summary", {"content": answer})
    return await persist_turn(
        request=request,
        current_user_id=user_id,
        workspace_oid=workspace_oid,
        question=request.question,
        resolved_question=original_question,
        turn_type="retry" if isinstance(payload, Command) else "standalone",
        sql=last_sql,
        sql_error=None if succeeded else answer,
        exec_result=last_result,
        is_success=succeeded,
        reasoning="",
        steps=steps,
        chart_type=chart_type,
        chart_config=chart_config,
        agent_mode=request.agent_mode,
        plans=[todo["content"] for todo in todos] or None,
        plan_states=[
            {"index": index, "state": {"completed": "ok", "in_progress": "running", "pending": "pending", "failed": "error"}[todo["status"]]}
            for index, todo in enumerate(todos)
        ] or None,
        tool_calls=tool_calls,
        summary=answer,
    )
