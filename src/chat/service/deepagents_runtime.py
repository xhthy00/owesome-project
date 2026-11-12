"""Run DeepAgents inside FastAPI, reusing the existing domain tools and SSE payloads."""

import asyncio
import json
import logging
import posixpath
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
    before_model,
    wrap_model_call,
    wrap_tool_call,
)
from langchain.agents.middleware.types import PrivateStateAttr
from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.messages.utils import count_tokens_approximately
from langchain_core.tools import tool
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.errors import GraphRecursionError
from langgraph.graph import END
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langgraph.managed.is_last_step import RemainingStepsManager
from langgraph.types import Command, interrupt
from psycopg import AsyncConnection
from sqlalchemy.engine import make_url

from datasource.service.edu_permission import edu_scope_dict_for_user_id
from src.agent.adapter.usage_sink import EmitFn, usage_tracking
from src.agent.audit.tool_call_log import log_tool_call_fire_and_forget
from src.agent.education.scope_guard import out_of_scope_question
from src.chat.schemas import ChatRequest
from src.chat.service.chart_spec import (
    build_tool_chart_spec,
    recommend_chart_spec,
    spec_to_echarts_option,
    validate_chart_spec,
)
from src.chat.service.context_usage import ContextUsageCallback
from src.chat.service.conversation_state import (
    authorization_fingerprint,
    fingerprint,
    load_conversation_history,
)
from src.chat.service.evidence_answer import (
    evidence_table,
    revalidate_evidence,
    summarize_verified_evidence,
)
from src.chat.service.human_questions import (
    CLARIFICATION_INSTRUCTIONS,
    HumanQuestionField,
    HumanQuestionInput,
    clarification_payload,
)
from src.chat.service.question_assessment import (
    ASSESSMENT_FAILURE,
    LOOKUP_INSTRUCTIONS,
    MAX_CONTEXT_LOOKUPS,
    QuestionAssessmentError,
    review_question,
    validate_context_lookup,
)
from src.chat.service.semantic_query import (
    SemanticFieldError,
    SemanticQuery,
    SemanticQueryError,
    SemanticViewDescriptionRequiredError,
)
from src.chat.service.turn_records import persist_turn
from src.common.core.config import get_settings
from src.datasource.edu_connection import edu_db_connection
from src.datasource.service.semantic_permission_contract import (
    PermissionRecovery,
)
from src.llm.service import get_default_llm

logger = logging.getLogger(__name__)

_STEP_LIMIT_MESSAGE = "本轮分析已达到执行步数上限。"
# 支持用工具名强制 tool_choice 的 OpenAI 兼容网关。
_NAMED_TOOL_CHOICE_HOSTS = frozenset(
    {
        None,
        "api.openai.com",
        "api.deepseek.com",
    }
)
# 只接受 "required" 的网关（不接受具名对象）。
_REQUIRED_TOOL_CHOICE_HOSTS = frozenset({"api.moonshot.ai", "api.moonshot.cn"})
_MINIMAX_MARKER = "]<]minimax[>["
_TOOL_PROTOCOL_BLOCK = re.compile(
    rf"(?:{re.escape(_MINIMAX_MARKER)})?<(?:minimax:)?tool_call\b[^>]*>.*?"
    r"(?:</(?:minimax:)?tool_call\s*>|$)", re.IGNORECASE | re.DOTALL,
)
_PLAN_FAILURE = "本轮未能生成可执行的步骤计划，请重试。"
_MODEL_REPLY_FAILURE = "本轮未能生成分析结论。"
_MODEL_OUTPUT_LIMIT = "本轮模型输出达到长度上限，未能完整生成分析。请缩小分析范围或调整模型输出额度。"
_RUNTIME_FAILURE = "本轮分析执行中断，请重试。"
# 先缓冲少量正文再开始下发：工具调用轮次常以一句短说明开头，
# 缓冲区可避免它先闪现再被 answer_discard 清掉。
_STREAM_PREFIX_CHARS = 24
_PROMPT = (
    "你是 Awesome-DB 的数据分析助手。用工具查询数据，按工具返回的范围和口径作答。"
    "业务事实和数值必须来自本轮 query_semantic 返回的数据。"
    "即使历史或上一轮已有同口径结果，本轮回答业务数值前仍须调用 query_semantic；"
    "历史结果不能作为本轮证据。"
    "续问沿用历史中已确认的对象、条件和口径，用户本轮明确修改的条件覆盖旧条件。"
    "历史答复和查询结果仅用于理解上下文，数据权限和发布版本以当前宿主为准。"
    "系统自动压缩较早的消息；需要原始细节时，用 read_file 读取提示中给出的历史归档路径。"
    "发布目录已在系统提示中给出。每个查询阶段按需选出相关对象，"
    "通过 describe_semantic_model 的 names 一次读取新对象的字段、说明和关联，复用本轮已读描述。"
    "首次使用视图建议先读取字段；直接查询时宿主会自动补读，以报错信息里的可用字段为准。"
    "先确认指标、筛选条件和关联键的来源，再提交 query_semantic。"
    "只使用发布目录中不带 schema 的逻辑名称和已确认的字段；禁止查询系统目录、物理表或用 SELECT * 探查。"
    "跨模型查询用来源别名限定字段。收到 FIELD_WRONG_SOURCE 或 FIELD_NOT_PUBLISHED 时，"
    "根据候选的字段说明和关联只修正出错的引用。避免原样重提。口径无法确认时请澄清。"
    "优先使用符合口径的已发布视图；视图缺少所需字段时，查阅已发布逻辑模型及关联。"
    "窗口函数的分组和排序使用来源别名限定字段。已有指标直接引用，加工后的表达式使用不同的输出别名。"
    "输出列必须用可读的业务别名（如 化学均分、及格率、参考人数），禁止 c1/c2/col1 这类占位别名："
    "这类别名会让报表和图表无法区分列含义。"
    "不同量纲的指标（人数、均分、分差、名次）不要放进同一列或同一张图，按指标分列或分图。"
    "DataSemantic 负责将语义 SQL 规划为物理 SQL，宿主校验权限并执行。"
    "对发布物未定义的业务指标，说明口径缺失。禁止猜测字段、值和数值。"
    "条件缺失或有歧义时请求澄清。区分零值、无数据、截断与失败。"
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


def _planning_tool_choice(model, *, can_clarify: bool) -> str:
    # A named write_todos choice hides clarification even if ask_human is bound.
    # Keep both choices available; require_plan_or_repair enforces stage order.
    return "auto" if can_clarify else _tool_choice_for(model, "write_todos")


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


def _query_free_question(text: str) -> bool:
    """Only greetings and explicit generic metric definitions need no data evidence."""
    value = text.strip().rstrip("！!。.?？ ")
    if re.fullmatch(r"你好|您好|早上好|晚上好|谢谢|感谢|再见|hi|hello|thanks", value, re.I):
        return True
    return bool(re.fullmatch(
        r"(?:什么是|解释(?:一下)?(?:什么是)?|介绍(?:一下)?)(?:平均分|均分|中位数|标准差|"
        r"方差|及格率|优秀率|达线率|百分位|排名|相关系数)(?:的(?:含义|定义|计算方法))?", value,
    ))


class _ChatState(AgentState):
    remaining_steps: Annotated[int, PrivateStateAttr, RemainingStepsManager]
    chat_model_calls: NotRequired[Annotated[int, PrivateStateAttr]]
    chat_model_tokens: NotRequired[Annotated[int, PrivateStateAttr]]
    chat_context_key: NotRequired[Annotated[str, PrivateStateAttr]]
    chat_question_assessment: NotRequired[Annotated[dict | None, PrivateStateAttr]]


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


@tool(args_schema=HumanQuestionInput)
def ask_human(
    question: str, options: list[str] | None = None,
    fields: list[HumanQuestionField] | None = None,
) -> str:
    """Ask for essential missing information using a form, then resume this task.

    Use fields for single/multiple choices or text. Only essential fields are
    required. Combine necessary questions in one call; do not ask for facts
    available through tools. The UI supports custom answers and optional skips.
    """
    form = HumanQuestionInput(question=question, options=options, fields=fields)
    value = interrupt({
        "kind": "human", "question": form.question,
        "options": form.options or [],
        "fields": [field.model_dump() for field in form.fields or []],
    })
    # New replies follow my-cowork's {answer: ...}; accept older raw-string resumes.
    return str(value["answer"]) if isinstance(value, dict) else str(value)


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


async def _recover_checkpoint(graph, config, stable):
    """Abandon pending work while preserving the previous conversation state.

    Use public graph updates instead of deleting the entire thread. When the
    stable state is a waiting clarification, close its outstanding tool calls
    so the next user turn has a valid message sequence.
    """
    current = await graph.aget_state(config)
    values = dict(stable.values)
    messages = list(values.get("messages", []))
    answered = {m.tool_call_id for m in messages if isinstance(m, ToolMessage)}
    completed = {m.tool_call_id: m for m in current.values.get("messages", [])
                 if isinstance(m, ToolMessage)}
    messages += [completed.get(call["id"]) or ToolMessage(
        content="本次等待已结束，请根据用户下一条消息继续。",
        tool_call_id=call["id"], status="error",
    ) for message in messages if isinstance(message, AIMessage)
        for call in message.tool_calls if call["id"] not in answered]
    values.update(
        messages=[RemoveMessage(id=REMOVE_ALL_MESSAGES), *messages],
        todos=values.get("todos", []),
        chat_model_calls=0, chat_model_tokens=0,
        _summarization_event=values.get("_summarization_event"),
        files={**{path: None for path in current.values.get("files", {})},
               **values.get("files", {})},
    )
    await graph.aupdate_state(config, values, as_node="model")
    await graph.aupdate_state(config, None, as_node=END)


async def run_chat_stream(
    *,
    request: ChatRequest,
    current_user_id: int,
    workspace_oid: int,
    emit: EmitFn,
    checkpointer,
    model=None,
    execute: bool = True,
    max_model_calls: int | None = None,
) -> int:
    """Serialize each conversation's updates and use the same thread to resume an interrupt."""
    settings = get_settings()
    if max_model_calls is not None and (
        not isinstance(max_model_calls, int) or isinstance(max_model_calls, bool) or max_model_calls < 1
    ):
        raise ValueError("max_model_calls 必须是正整数")
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
                    max_model_calls,
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
    max_model_calls=None,
):
    settings = get_settings()
    datasource_id = request.datasource_id
    semantic = SemanticQuery(settings, datasource_id, workspace_oid, user_id)
    try:
        explicit_authorization = await asyncio.to_thread(semantic.authorization_context)
        context = await asyncio.to_thread(semantic.context)
    except SemanticQueryError as exc:
        detail = getattr(exc, "detail", None)
        await emit("error", {"error": detail.get("user_message", str(exc)) if detail else str(exc),
                             "preserve_interrupt": True,
                             **({"error_detail": detail} if detail else {})})
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
    scope = ({"edu_role": "v2", **explicit_authorization} if explicit_authorization is not None
             else await asyncio.to_thread(edu_scope_dict_for_user_id, user_id))
    # Keep the prompt readable: examples and the capability catalog are rendered
    # as dedicated sections below instead of being repeated inside the JSON blob.
    scope_for_prompt = (
        {key: value for key, value in scope.items()
         if key not in {"reference_examples", "capability_catalog"}}
        if isinstance(scope, dict) else scope
    )
    context_key = fingerprint({
        "authorization": await asyncio.to_thread(
            authorization_fingerprint, user_id, workspace_oid, datasource_id,
        ),
        "scope": scope,
        "publication": {key: context.get(key) for key in ("bundle_id", "manifest_hash", "dialect")},
        "binding": settings.datasemantic_bindings.get(datasource_id),
        "endpoint": settings.datasemantic_url,
    })
    last_sql = ""
    last_result = None
    last_result_index: int | None = None
    final_ai_message_id = None
    evidence_answer = None
    steps = []
    tool_calls = []
    query_error = ""
    permission_recovery = PermissionRecovery()
    permission_query_lock = asyncio.Lock()
    evidence = []
    todos = []
    planning_attempts = 0
    completion_attempts = 0
    lookup_attempts = 0
    context_lookups = 0
    question_assessment = None
    needs_plan = execute and _planning_required(request.question)
    budget = (
        _ModelCallBudget(max_model_calls or settings.chat_max_steps, settings.chat_max_total_tokens)
        if max_model_calls is not None or settings.chat_max_steps is not None or settings.chat_max_total_tokens is not None
        else None
    )
    limit_message = ""
    chart_type, chart_config, chart_spec = "table", None, None
    chart_emitted = False
    chart_emitted_index: int | None = None

    def active_stage_index():
        return next((i for i, todo in enumerate(todos) if todo["status"] == "in_progress"), None)

    # 同一子任务（无计划时按整轮）的查询次数，达到上限后收敛作答。
    stage_query_count: dict[int | None, int] = {}
    # 已就“预算用尽”提醒过推进计划的阶段，避免模型不理会时反复空转。
    advance_requested: set[int | None] = set()
    query_limit = settings.chat_max_queries_per_stage

    def stage_query_exhausted() -> bool:
        if query_limit is None:
            return False
        return stage_query_count.get(active_stage_index(), 0) >= query_limit

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
    async def lookup_question_context(sql: str) -> str:
        """只读查询真实候选。用 SELECT DISTINCT 读取至多6个标识、名称或时间字段，不加LIMIT/OFFSET，不汇总分析。"""
        nonlocal context_lookups
        if not execute:
            return json.dumps({"error": "当前入口仅生成SQL，不执行候选查询；请补充明确的查询范围。"}, ensure_ascii=False)
        if context_lookups >= MAX_CONTEXT_LOOKUPS:
            return json.dumps({"error": "已达到本轮候选探查上限，请根据已有候选确认范围。"}, ensure_ascii=False)
        context_lookups += 1
        try:
            validate_context_lookup(sql, context.get("dialect") or "postgres")
            try:
                result = await asyncio.to_thread(semantic.run, sql, execute=True)
            except SemanticViewDescriptionRequiredError as exc:
                if not exc.names:
                    raise
                unread_names = list(exc.names)
                await asyncio.to_thread(lambda: [semantic.describe(name) for name in unread_names])
                result = await asyncio.to_thread(semantic.run, sql, execute=True)
        except (QuestionAssessmentError, SemanticQueryError) as exc:
            result = {"error": str(exc)}
            if getattr(exc, "detail", None) and exc.detail.get("code") == "AUTH_SCOPE_MISSING":
                # A probe that cannot prove the full tuple is usually an attempt
                # to discover an unavailable capability (other classes, school rank).
                exc.detail.setdefault(
                    "repair_hint",
                    "该探查未落在完整授权元组内。若目标是其他班级明细或全校个人名次，"
                    "该能力未开通；可改用 grade_baseline 做班级与年级基准对照。",
                )
            if hasattr(exc, "detail") and (
                str(exc.detail.get("code", "")).startswith("AUTH_")
                or exc.detail.get("recovery_action")
                or explicit_authorization is not None and isinstance(exc, SemanticFieldError)
            ):
                permission_recovery.reject(
                    {"message": str(exc), **exc.detail},
                    stage=active_stage_index(),
                    tool="lookup",
                )
                result["error_detail"] = permission_recovery.last_error
            detail = getattr(exc, "detail", None) or {}
            log_tool_call_fire_and_forget(
                agent_name="DeepAgents",
                round_idx=None,
                sub_task_index=active_stage_index(),
                tool_name="lookup_question_context",
                success=False,
                elapsed_ms=None,
                args={"sql": sql},
                result_preview=str(exc),
                error_code=detail.get("code"),
                error_detail=detail or None,
                failure_domain=detail.get("failure_domain"),
                rule=detail.get("reason"),
            )
            return json.dumps(result, ensure_ascii=False)
        permission_recovery.success()
        log_tool_call_fire_and_forget(
            agent_name="DeepAgents",
            round_idx=None,
            sub_task_index=active_stage_index(),
            tool_name="lookup_question_context",
            success=True,
            elapsed_ms=None,
            args={"sql": sql},
            result_preview=f"{len(result.get('rows', []))} rows",
        )
        return json.dumps({
            "columns": result["columns"], "rows": result["rows"],
            "row_count": result.get("row_count", len(result["rows"])),
            "rows_truncated": result.get("rows_truncated", False),
        }, ensure_ascii=False, default=str)

    @tool
    async def query_semantic(sql: str) -> str:
        """提交一条只读语义 SQL，显式列出已确认的字段。DataSemantic 负责物理规划，宿主按权限执行。"""
        nonlocal query_error, last_sql, last_result, last_result_index
        stage_index = active_stage_index()
        if permission_recovery.blocked or permission_recovery.stage_blocked(stage_index):
            return json.dumps({
                "error": permission_recovery.public_error() or "本阶段数据查询已停止。",
                "error_detail": permission_recovery.last_error,
            }, ensure_ascii=False)
        used = stage_query_count.get(stage_index, 0)
        if query_limit is not None and used >= query_limit:
            return (
                f"当前子任务已提交 {query_limit} 次查询，达到收敛上限。"
                "请基于已返回的数据作答；仍缺关键数据时说明缺口，或先更新任务计划进入下一阶段。"
            )
        stage_query_count[stage_index] = used + 1
        round_number = len(tool_calls) + 1
        meta = {"tool": "query_semantic", "round": round_number, "agent": "DeepAgents"}
        if stage_index is not None:
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
            if isinstance(exc, SemanticFieldError) or hasattr(exc, "detail"):
                result["error_detail"] = exc.detail
                if (str(exc.detail.get("code", "")).startswith("AUTH_")
                        or exc.detail.get("recovery_action")
                        or explicit_authorization is not None and isinstance(exc, SemanticFieldError)):
                    permission_recovery.reject(
                        {"message": str(exc), **exc.detail},
                        stage=stage_index,
                        tool="query_semantic",
                    )
                    result["error_detail"] = permission_recovery.last_error
        else:
            query_error = ""
            permission_recovery.success()
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
            if execute:
                last_result_index = len(evidence) - 1
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
            sub_task_index=meta.get("sub_task_index"),
            tool_name=meta["tool"],
            success=success,
            elapsed_ms=elapsed_ms,
            args={"sql": sql},
            result_preview=result.get("error", ""),
            error_code=(result.get("error_detail") or {}).get("code"),
            error_detail=result.get("error_detail"),
            failure_domain=(result.get("error_detail") or {}).get("failure_domain"),
            rule=(result.get("error_detail") or {}).get("reason"),
        )
        if "error" in result:
            if explicit_authorization is not None and evidence:
                result["available_results"] = [{"columns": e.get("columns", []), "row_count": e.get("row_count")}
                                               for e in evidence[-3:] if e.get("rows")]
                result["answer_guidance"] = "此前已有成功查询。若已覆盖原问题应据此作答，宿主发布前会重新核验授权；额外查询失败不能抹去已有数值。"
            return json.dumps(result, ensure_ascii=False) if "error_detail" in result else content
        if not execute:
            return "DataSemantic 已成功生成并校验计划。"
        preview = {
            **({"answer_guidance": "如果当前结果已覆盖原问题，更新计划并直接作答；本校与全市整体比较无需另查各校明细或排名。"}
               if explicit_authorization is not None else {}),
            "columns": result["columns"],
            "rows": result["rows"][:50],
            "preview_truncated": len(result["rows"]) > 50 or result.get("rows_truncated", False),
        }
        return json.dumps(preview, ensure_ascii=False)

    @tool
    async def chart_semantic_result(
        x_column: str,
        y_columns: list[str],
        kind: Literal[
            "line",
            "area",
            "bar",
            "column",
            "groupedColumn",
            "stackedColumn",
            "donut",
            "pie",
        ],
        title: str = "",
        unit: str = "",
        top_n: int | None = None,
    ) -> str:
        """用最近一次语义查询结果生成图表。x 为维度列，y_columns 为数值列。

        kind: line/area 趋势；bar 横向排名；column 纵向对比；groupedColumn 分组对比；
        stackedColumn 堆叠；donut/pie 占比。title/unit 可选。
        """
        nonlocal chart_type, chart_config, chart_spec, chart_emitted, chart_emitted_index
        if not last_result or not last_result["rows"]:
            return "请先查询可用于绘图的数据。"
        rows = last_result["rows"]
        columns = last_result["columns"]
        spec, error = build_tool_chart_spec(
            kind=kind,
            x_column=x_column,
            y_columns=y_columns,
            columns=columns,
            rows=rows,
            result_index=last_result_index or 0,
            title=title,
            unit=unit,
            top_n=top_n,
        )
        if spec is None:
            return error
        option = spec_to_echarts_option(spec, rows, columns)
        chart_type, chart_spec, chart_config = spec["type"], spec, spec
        chart_emitted = True
        chart_emitted_index = last_result_index
        await emit(
            "chart",
            {
                "chart_type": chart_type,
                "chart_spec": chart_spec,
                "chart_config": option or None,
            },
        )
        return f"已生成{chart_type}图表。"

    tools = [get_analysis_context, describe_semantic_model, lookup_question_context, query_semantic]
    if execute:
        tools.append(chart_semantic_result)
    if request.conversation_id and execute:
        tools.append(ask_human)
    # Restrict both model binding and tool execution, including DeepAgents' built-in task delegation.
    allowed = {item.name for item in tools}
    # DeepAgents archives and large tool results live in the thread's StateBackend.
    # Reading them is needed after compaction; no host filesystem is mounted.
    allowed.add("read_file")
    if execute:
        allowed.add("write_todos")

    @before_agent
    async def initialize_turn(state, runtime):
        # A new turn starts at entry. Command(resume=...) continues the existing graph.
        return {
            "chat_context_key": context_key,
            "chat_question_assessment": None,
            "messages": [RemoveMessage(id=message.id) for message in state.get("messages", [])
                         if isinstance(message, SystemMessage) and message.id
                         and (message.additional_kwargs.get("chat_runtime_instruction") or (
                             isinstance(message.content, str) and message.content.startswith((
                                 "本轮尚未发布步骤计划。下一次调用", "本轮计划仍有 pending 阶段",
                             ))
                         ))],
            **({"todos": []} if execute else {}),
        }

    @before_model(state_schema=_ChatState, can_jump_to=["end"])
    async def assess_question(state, runtime):
        nonlocal question_assessment, preparation_error, last_result
        previous = state.get("chat_question_assessment") or {}
        if _query_free_question(original_question):
            question_assessment = {"decision": "ready", "reason": "通用问候或指标定义，无需数据范围确认。", "skipped": True}
            return {"chat_question_assessment": question_assessment}
        messages = state.get("messages", [])
        latest_tool = next((m for m in reversed(messages) if isinstance(m, ToolMessage)), None)
        after_tool = latest_tool.id if latest_tool else None
        if previous.get("decision") == "ready":
            question_assessment = previous
            changed = after_tool != previous.get("after_tool") and latest_tool and latest_tool.name in {
                "describe_semantic_model", "lookup_question_context", "query_semantic", "read_file", "ask_human",
            }
            if changed and latest_tool.name == "query_semantic":
                candidate_call = next((c for m in reversed(messages) if isinstance(m, AIMessage)
                                       for c in m.tool_calls if c["id"] == latest_tool.tool_call_id), None)
                try:
                    validate_context_lookup((candidate_call or {}).get("args", {}).get("sql", ""), context.get("dialect") or "postgres")
                except QuestionAssessmentError:
                    changed = False
            if not changed:
                return None
        if previous.get("decision") == "clarify" and not any(
            isinstance(m, ToolMessage) and m.tool_call_id == previous.get("call_id") for m in messages
        ):
            return None
        if previous.get("decision") == "lookup" and previous.get("after_tool") == after_tool:
            return None
        revision = previous.get("revision", 0) + 1
        step = {"name": f"assess_question_{revision}", "label": "评估查询条件", "status": "running"}
        steps.append(step)
        await emit("step", dict(step))
        try:
            assessment = await review_question(
                model=model, question=original_question, messages=messages,
                directory=directory, authorization_scope=scope,
                tool_choice=_tool_choice_for(model, "submit_question_assessment"),
            )
            lookup_rounds = previous.get("lookup_rounds", 0) + (previous.get("decision") == "lookup")
            if assessment.decision == "lookup" and lookup_rounds >= MAX_CONTEXT_LOOKUPS:
                raise QuestionAssessmentError("查询条件核验达到探查上限，请补充具体分析条件后重试。")
        except QuestionAssessmentError as exc:
            preparation_error = str(exc)
            question_assessment = {"decision": "error", "reason": preparation_error, "revision": revision}
            step.update(status="error", detail=preparation_error)
            await emit("step", dict(step))
            return {"chat_question_assessment": question_assessment, "jump_to": "end"}
        question_assessment = {
            **assessment.model_dump(exclude_none=True), "revision": revision,
            "after_tool": after_tool, "lookup_rounds": lookup_rounds,
        }
        if assessment.decision == "clarify":
            question_assessment["call_id"] = f"assessment-ask-{uuid4().hex}"
            if "ask_human" not in allowed:
                form = assessment.clarification.model_dump(exclude_none=True)
                last_result = {"clarify": True, "prompt": form.pop("question"), **form, "original_question": original_question}
                await emit("clarify", last_result)
                preparation_error = f"需要补充查询条件：{last_result['prompt']}"
                step.update(status="ok", detail=assessment.reason)
                await emit("step", dict(step))
                return {"chat_question_assessment": question_assessment, "jump_to": "end"}
        step.update(status="ok", detail=assessment.reason)
        await emit("step", dict(step))
        return {
            "chat_question_assessment": question_assessment,
            "chat_model_calls": budget.calls if budget else 0,
            "chat_model_tokens": budget.tokens if budget else 0,
        }

    @before_model(can_jump_to=["tools"])
    async def request_assessed_clarification(state, runtime):
        assessment = state.get("chat_question_assessment") or {}
        if assessment.get("decision") == "clarify":
            return {"messages": [AIMessage(content="", additional_kwargs={"reasoning_content": ""}, tool_calls=[{
                "name": "ask_human", "id": assessment["call_id"],
                "args": assessment["clarification"], "type": "tool_call",
            }])], "jump_to": "tools"}
        return None

    @after_model(can_jump_to=["model", "end"])
    async def require_plan_or_repair(state, runtime):
        """校验阶段顺序和输出完整性，对可恢复响应做有界修复。"""
        nonlocal preparation_error
        nonlocal model_reply_error, protocol_retried
        nonlocal completion_attempts
        message = state["messages"][-1]
        if limit_message or preparation_error or not isinstance(message, AIMessage):
            return None
        if message.response_metadata.get("finish_reason") == "length":
            model_reply_error = _MODEL_OUTPUT_LIMIT
            return {"messages": [ToolMessage(
                content="模型输出被截断，本次工具未执行。", tool_call_id=call["id"], status="error",
            ) for call in message.tool_calls], "jump_to": "end"}
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
        if (state.get("chat_question_assessment") or {}).get("decision") != "ready":
            if not message.tool_calls:
                preparation_error = ASSESSMENT_FAILURE
                return {"messages": [RemoveMessage(id=message.id)], "jump_to": "end"}
            return None
        if needs_plan and not state.get("todos"):
            # Some gateways return a valid plan together with the next tool even
            # when only write_todos was bound. Keep the plan; the tool wrapper
            # defers its companion calls until the plan has actually executed.
            if any(call["name"] == "write_todos" for call in message.tool_calls):
                return None
            if "ask_human" in allowed and any(call["name"] == "ask_human" for call in message.tool_calls):
                return None
            logger.warning(
                "Planning response missing write_todos: attempt=%s tools=%s finish_reason=%s",
                planning_attempts, [call["name"] for call in message.tool_calls],
                message.response_metadata.get("finish_reason"),
            )
            feedback = [ToolMessage(
                content="此工具尚未执行。请先调用 write_todos 发布本轮步骤计划。",
                tool_call_id=call["id"], status="error",
            ) for call in message.tool_calls]
            update = {"messages": feedback}
            if planning_attempts >= 3:
                preparation_error = _PLAN_FAILURE
                return {**update, "jump_to": "end"}
            feedback.append(SystemMessage(content=(
                "本轮尚未发布步骤计划。缺少关键条件且工具可用时先调用 ask_human，"
                "否则用 write_todos 发布至少一个步骤；等待工具成功返回后，再调用查询或描述工具。"
                "不要直接输出结论，不要只在正文里追问或声称正在等待用户。"
            ), additional_kwargs={"chat_runtime_instruction": True}))
            return {**update, "jump_to": "model"}
        if not permission_recovery.blocked and not message.tool_calls and any(todo["status"] == "pending" for todo in state.get("todos", [])):
            completion_attempts += 1
            if completion_attempts > 2:
                preparation_error = "本轮计划仍有未执行阶段，分析尚未完成。"
                return {"messages": [RemoveMessage(id=message.id)], "jump_to": "end"}
            return {"messages": [RemoveMessage(id=message.id), SystemMessage(content=(
                "本轮计划仍有 pending 阶段，请继续执行剩余阶段并据实际进展更新 write_todos，"
                "不要提前输出完成结论；不能执行的阶段应说明具体缺口，不能直接标为已完成。"
            ), additional_kwargs={"chat_runtime_instruction": True})], "jump_to": "model"}
        return None

    @wrap_model_call(state_schema=_ChatState)
    async def restrict_model_tools(model_request, handler):
        nonlocal limit_message, preparation_error, planning_attempts
        nonlocal lookup_attempts
        if budget is not None and budget.limit is not None and budget.calls >= budget.limit:
            limit_message = budget.message
            return AIMessage(content=limit_message)
        # Reserve graph steps for the final model message and DeepAgents' after-agent hooks.
        if model_request.state["remaining_steps"] <= 3:
            limit_message = _STEP_LIMIT_MESSAGE
            return AIMessage(content=limit_message)
        model_tools = [t for t in model_request.tools if t.name in allowed]
        assessment = model_request.state.get("chat_question_assessment") or {}
        if explicit_authorization is not None and any(item.get("rows") for item in evidence):
            model_request = model_request.override(system_prompt=(
                f"{model_request.system_prompt}\n本轮已取得成功查询结果。先检查这些结果是否已覆盖原问题。"
                "本校均分、全市整体均分和分差均已返回时，更新计划并直接作答。"
                "不要为了核验整体对比再查询其他学校的明细或排名；仅补查用户明确需要且尚未返回的信息。"
            ))
        if assessment.get("decision") == "ready":
            model_tools = [t for t in model_tools if t.name != "lookup_question_context"]
        if permission_recovery.human_retry_blocked:
            model_tools = [t for t in model_tools if t.name != "ask_human"]
            model_request = model_request.override(system_prompt=(
                f"{model_request.system_prompt}\n最近一次查询有宿主判定的技术失败。"
                "按error_detail.recovery_action修正或停止，不能要求用户确认继续或重新授权。"
            ))
        if permission_recovery.blocked:
            model_request = model_request.override(
                tools=[t for t in model_tools if t.name not in {
                    "query_semantic", "lookup_question_context", "ask_human",
                }],
                tool_choice="auto",
                system_prompt=(
                    f"{model_request.system_prompt}\n本轮数据查询已停止，不能继续读取或让用户选择重试。"
                    f"宿主错误类别为{permission_recovery.last_error['category']}，"
                    f"恢复动作为{permission_recovery.last_error['recovery_action']}。"
                    "按宿主分类说明未完成部分，不能猜测账号未获授权。"
                    "未执行的阶段保持未完成，不得虚报完成；已有可信数据保留在结果中。"
                ),
            )
        elif permission_recovery.stage_blocked(active_stage_index()):
            model_request = model_request.override(
                tools=[t for t in model_tools if t.name != "query_semantic"],
                tool_choice="auto",
                system_prompt=(
                    f"{model_request.system_prompt}\n当前子任务已用完 SQL 修正次数，不能再提交查询。"
                    "可以更新计划进入下一阶段，或基于已有数据说明本阶段缺口；"
                    "不要用用户确认或重授权来绕过技术修复。"
                ),
            )
        elif assessment.get("decision") != "ready":
            lookup_attempts += 1
            if lookup_attempts > MAX_CONTEXT_LOOKUPS:
                preparation_error = ASSESSMENT_FAILURE
                return AIMessage(content=preparation_error)
            model_request = model_request.override(
                tools=[t for t in model_tools if t.name in {
                    "get_analysis_context", "describe_semantic_model", "lookup_question_context", "read_file", "ask_human",
                }],
                tool_choice="auto", system_prompt=f"{model_request.system_prompt}{LOOKUP_INSTRUCTIONS}\n评估依据：{assessment.get('reason', '')}",
            )
        elif needs_plan and not model_request.state.get("todos"):
            planning_attempts += 1
            if planning_attempts > 3:
                preparation_error = _PLAN_FAILURE
                return AIMessage(content=preparation_error)
            choice = _planning_tool_choice(
                model_request.model, can_clarify=any(t.name == "ask_human" for t in model_tools),
            )
            plan_tools = {"write_todos", "ask_human"}
            model_request = model_request.override(
                tools=[t for t in model_tools if t.name in plan_tools],
                tool_choice=choice,
                system_prompt=(
                    f"{model_request.system_prompt}\n先核对任务所需的对象、范围和口径。"
                    "缺少会影响执行的关键条件时，优先调用当前工具列表中的 ask_human，收到回复后再规划。"
                    "已有信息足够时调用 write_todos 发布至少一个计划步骤。不要为了先发布计划而猜测关键条件。"
                ),
            )
        else:
            planning_attempts = 0
            if stage_query_exhausted():
                stage_key = active_stage_index()
                pending_stage = next(
                    (i for i, todo in enumerate(todos) if todo["status"] == "pending"), None
                )
                if pending_stage is not None and stage_key not in advance_requested:
                    # 还有后续阶段：先推进计划继续执行，不要提前收尾跳过剩余阶段。
                    advance_requested.add(stage_key)
                    model_request = model_request.override(
                        tools=[t for t in model_tools if t.name in {"write_todos", "ask_human"}],
                        tool_choice=_planning_tool_choice(
                            model_request.model, can_clarify=any(t.name == "ask_human" for t in model_tools),
                        ),
                        system_prompt=(
                            f"{model_request.system_prompt}\n当前子任务已提交 {query_limit} 次查询，"
                            "达到收敛上限。缺少关键条件时可先调用 ask_human 等待回复；否则请更新任务计划："
                            "把当前阶段标记为 completed 并写明缺口，"
                            "立刻把下一阶段置为 in_progress；不要跳过或提前把后续阶段标记为 completed。"
                        ),
                    )
                else:
                    model_tools = [t for t in model_tools if t.name != "query_semantic"]
                    model_request = model_request.override(
                        tools=model_tools,
                        system_prompt=(
                            f"{model_request.system_prompt}\n当前子任务已提交 {query_limit} 次查询，"
                            "请基于已返回数据作答；仍缺数据时说明缺口。"
                        ),
                    )
            else:
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

    async def _restricted_tool_call(tool_request, handler):
        name = tool_request.tool_call["name"]
        if name == "ask_human" and permission_recovery.human_retry_blocked:
            return ToolMessage(
                content="技术校验失败不能通过用户确认继续；请按宿主给出的recovery_action修正或停止。",
                tool_call_id=tool_request.tool_call["id"], status="error",
            )
        if permission_recovery.blocked and name in {"query_semantic", "lookup_question_context", "ask_human"}:
            return ToolMessage(
                content="本轮数据查询已停止，不能通过追问用户继续尝试。请按宿主错误分类说明未完成部分。",
                tool_call_id=tool_request.tool_call["id"], status="error",
            )
        if name == "query_semantic" and permission_recovery.stage_blocked(active_stage_index()):
            return ToolMessage(
                content="当前子任务已用完 SQL 修正次数，不能再提交查询。"
                        "请更新计划进入下一阶段，或基于已有数据说明本阶段缺口。",
                tool_call_id=tool_request.tool_call["id"], status="error",
            )
        if name in {"query_semantic", "chart_semantic_result", "write_todos"} and (
            tool_request.state.get("chat_question_assessment") or {}
        ).get("decision") != "ready":
            return ToolMessage(content="本次操作尚未执行。请先完成查询条件评估，按需探查候选或等待用户澄清。",
                               tool_call_id=tool_request.tool_call["id"], status="error")
        if name == "read_file":
            path = posixpath.normpath(str(tool_request.tool_call["args"].get("file_path", "")))
            if not path.startswith(("/conversation_history/", "/large_tool_results/")):
                return ToolMessage(
                    content="只可读取当前会话的历史归档和工具结果。",
                    tool_call_id=tool_request.tool_call["id"], status="error",
                )
        # A resumed parallel ToolNode may already have sibling ToolMessages in
        # state. Find this call's originating AI batch instead of the last row.
        calls = next((message.tool_calls for message in reversed(tool_request.state["messages"])
                      if isinstance(message, AIMessage) and any(
                          call["id"] == tool_request.tool_call["id"] for call in message.tool_calls
                      )), [tool_request.tool_call])
        asking = "ask_human" in allowed and any(call["name"] == "ask_human" for call in calls)
        if name == "ask_human" and tool_request.tool_call["id"] != next(
            call["id"] for call in calls if call["name"] == "ask_human"
        ):
            return ToolMessage(
                content="本次额外追问未执行。每轮只等待一个追问，请收到用户答复后再继续澄清。",
                tool_call_id=tool_request.tool_call["id"], status="error",
            )
        if asking and name != "ask_human":
            return ToolMessage(
                content="本次工具尚未执行。请先等待用户澄清，再继续操作。",
                tool_call_id=tool_request.tool_call["id"], status="error",
            )
        publishing_plan = any(call["name"] == "write_todos" for call in calls)
        if publishing_plan and not asking and name != "write_todos":
            # ToolNode can execute a batch concurrently. A plan in the same AI
            # message is not an executed plan yet; do not race business tools.
            return ToolMessage(
                content="本次工具尚未执行。请等待 write_todos 发布计划成功后，再单独调用本工具。",
                tool_call_id=tool_request.tool_call["id"], status="error",
            )
        if name == "chart_semantic_result" and any(c["name"] == "query_semantic" for c in calls):
            return ToolMessage(
                content="本次绘图未执行。请等待本批查询全部返回后，再调用绘图工具。",
                tool_call_id=tool_request.tool_call["id"], status="error",
            )
        if name not in allowed:
            return ToolMessage(
                content="此操作不可用。若已查询且仍缺信息，请直接询问用户。",
                tool_call_id=tool_request.tool_call["id"],
                status="error",
            )
        if name == "query_semantic":
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
        if name == "lookup_question_context" and isinstance(result, ToolMessage):
            try:
                data = json.loads(result.content)
            except (ValueError, TypeError):
                data = {"error": "候选探查返回格式无效"}
            failed = "error" in data
            call["data"] = data
        call.update(
            success=not failed,
            content="工具执行未完成" if failed else "",
            elapsed_ms=int((monotonic() - started) * 1000),
        )
        await emit("tool_result", dict(call))
        return result

    @wrap_tool_call
    async def restrict_tool_call(tool_request, handler):
        if explicit_authorization is not None and tool_request.tool_call["name"] in {
            "query_semantic", "lookup_question_context",
        }:
            # A ToolNode can run sibling calls concurrently. Serialize v2 data
            # reads so queued calls recheck terminal failures before execution.
            async with permission_query_lock:
                return await _restricted_tool_call(tool_request, handler)
        return await _restricted_tool_call(tool_request, handler)

    prompt = (
        f"{_PROMPT}{CLARIFICATION_INSTRUCTIONS if execute and request.conversation_id else ''}\n当前授权范围参考：{scope_for_prompt}。\n"
        f"已发布目录（bundle_id / manifest_hash / dialect / models / views）：{context_json}"
    )
    if explicit_authorization is not None:
        capabilities = explicit_authorization.get("capability_catalog") or []
        examples = explicit_authorization.get("reference_examples") or {}
        scope_answers = explicit_authorization.get("scope_answers") or []
        scope_not_answers = explicit_authorization.get("scope_not_answers") or []
        prompt += (
            "\n严格遵守scope_grants和query_contract。可执行规则以relations的reference_query_contract为准。"
            "错误分类、修正次数和停止决定以宿主error_detail为准，不能猜测授权失效或让用户确认技术重试。"
            "\n授权范围内可直接查询（scope_answers，不需要 capability；本班学生成绩明细、"
            "班级内排名/分数分布/均分/及格率都属于这一类）："
            + json.dumps(scope_answers, ensure_ascii=False)
            + "\n只有以下口径才需要说明未开通（scope_not_answers）："
            + json.dumps(scope_not_answers, ensure_ascii=False)
            + "\ncapability_catalog 只约束参照池/跨单位对照，不限制 scope_answers 中的普通查询。"
            "“未开通全校名次/跨校排名”不等于不能查本班学生明细；本班明细按 scope_answers 正常查询。"
            "\n能力边界（granted=true 才可用；granted=false 的能力当前未开通；"
            "问题要求 not_answers 中的口径时直接说明未开通并给出替代口径；"
            "不得用 lookup_question_context 或 query_semantic 试探）："
            + json.dumps(capabilities, ensure_ascii=False)
            + "\n规范形态示例（占位符替换为已确认值；这是形态示例，不是可直接执行的模板）：\n"
            + "\n\n".join(
                f"[{capability}]\n{sql}" for capability, sql in examples.items()
            )
        )
    middleware = [initialize_turn, assess_question, request_assessed_clarification,
                  require_plan_or_repair, restrict_model_tools, restrict_tool_call]
    if execute:
        prompt += (
            "\n复杂问题先用 write_todos 发布具体计划，首步设为 in_progress，"
            "后续步骤设为 pending。每完成一个阶段就更新清单，并开始下一阶段；只把已核实的阶段设为 completed，"
            "完成最后一次更新后再发最终答复。简单问题不必发布计划，但同样必须先通过查询条件评估。"
        )
        if query_limit is not None:
            prompt += (
                f"\n同一个子任务最多提交 {query_limit} 次 query_semantic；"
                "达到上限后请基于已有数据作答并说明缺口。"
            )
        middleware = [TodoListMiddleware(), *middleware]
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
    config = {
        "configurable": {"thread_id": thread},
        "callbacks": [ContextUsageCallback(usage)],
    }
    if budget is not None:
        config["callbacks"].append(budget)
        if budget.limit is not None:
            # Graph steps include assessment, routing and other SDK hooks;
            # the callback still enforces the exact number of model calls.
            config["recursion_limit"] = 16 * budget.limit + 32
    snapshot = await graph.aget_state(config)
    boundary_changed = bool(snapshot.values and snapshot.values.get("chat_context_key") != context_key)
    if boundary_changed:
        # Old checkpoints predate this boundary. Reset them as well: their rows
        # cannot be assumed to match the current user's permissions.
        await checkpointer.adelete_thread(thread)
        snapshot = await graph.aget_state(config)
        if request.resume_interrupt_id:
            await emit("human.cancelled", {"interrupt_id": request.resume_interrupt_id})
            await emit("error", {"error": "会话授权或语义发布版本已变化，请重新提问。"})
            return 0
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
        await emit("human.cancelled", {"interrupt_id": request.resume_interrupt_id})
        await emit("error", {"error": "这条追问已经处理或失效，请重新提问。"})
        return 0
    if pending_interrupt and not new_question and not request.resume_interrupt_id:
        await emit("clarify", clarification_payload(pending_interrupt, next(
            (m.content for m in reversed(history) if isinstance(m, HumanMessage)), request.question,
        )))
        await emit("error", {"error": "当前会话有待回复的追问，请通过追问卡片回复，或明确输入“换个问题”开始新提问。"})
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
        payload = Command(resume={pending_interrupt.id: {"answer": request.question}})
        todos = snapshot.values.get("todos") or []
        if todos:
            await emit("task_plan", {"stages": todos})
    else:
        bootstrap = []
        if not history and not boundary_changed and not new_question and request.conversation_id:
            bootstrap = await asyncio.to_thread(
                load_conversation_history, request.conversation_id, user_id,
                workspace_oid, datasource_id, context_key,
            )
        if snapshot.next:
            await _recover_checkpoint(graph, config, snapshot)
            snapshot = await graph.aget_state(config)
        # Like my-cowork, append only the new user turn. DeepAgents owns message
        # retention, tool-call pairing, offloading and automatic summarization.
        payload = {
            "messages": [*bootstrap, HumanMessage(content=request.question)],
        }

    preparation_step = {"name": "prepare_agent", "label": "准备智能体", "status": "ok", "round": 0}
    steps.append(preparation_step)
    await emit("step", preparation_step)
    answer = ""
    query_free = execute and _query_free_question(original_question)
    pending = None
    # 已下发给前端的草稿正文；工具调用轮次结束后用 answer_discard 清掉。
    streamed_text = ""
    buffered_text = ""
    # <think> 标签可能被分片切开，carry 保存待续的半截标签。
    think_carry = ""
    in_think = False
    reasoning_started = False
    reasoning_in_call = False

    def answer_streamable() -> bool:
        """已有查询结果、计划就绪后的正文才下发。

        先有 evidence 再流式，可避免把寒暄类答复先刷出来、再被“未取得查询结果”覆盖。
        """
        return bool(
            execute
            and (evidence or query_free)
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
                if (meta or {}).get("lc_source") == "summarization":
                    continue
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
            for node_name, node in update.items():
                if not isinstance(node, dict):
                    continue
                if streamed_text and any(isinstance(m, RemoveMessage) for m in node.get("messages", [])):
                    await emit("answer_discard", {})
                    streamed_text = ""
                if "todos" in node and node["todos"] != todos:
                    todos = node["todos"]
                    await emit("task_plan", {"stages": todos})
                for message in node.get("messages", []):
                    if node_name != "model" or not isinstance(message, AIMessage):
                        continue
                    if not message.tool_calls:
                        final_ai_message_id = message.id
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
    except asyncio.CancelledError:
        await _recover_checkpoint(graph, config, snapshot)
        raise
    except Exception:
        logger.exception("DeepAgents turn failed: thread=%s", thread)
        model_reply_error = _RUNTIME_FAILURE

    if not pending and (limit_message or model_reply_error or preparation_error):
        await _recover_checkpoint(graph, config, snapshot)
        for call in tool_calls:
            if "success" not in call:
                call.update(success=False, content=model_reply_error or limit_message or preparation_error)
                await emit("tool_result", dict(call))
        for step in steps:
            if step.get("status") == "running":
                step.update(status="error", detail=model_reply_error or limit_message or preparation_error)

    if pending:
        if tool_calls and tool_calls[-1]["tool"] == "ask_human" and "success" not in tool_calls[-1]:
            tool_calls[-1].update(success=True, content="等待用户补充信息")
            await emit("tool_result", dict(tool_calls[-1]))
        answer = str(pending.value["question"])
        last_result = clarification_payload(pending, original_question)
        await emit("clarify", last_result)
    if not pending and not evidence and not (
        query_free and answer and not (limit_message or model_reply_error or preparation_error)
    ):
        stop_reason = limit_message
        answer = (
            preparation_error
            or permission_recovery.public_error()
            or query_error
            or model_reply_error
            or (
                stop_reason + "未取得 DataSemantic 查询结果，请缩小查询范围后重试。"
                if stop_reason
                else "本轮未执行数据查询，无法给出业务数值；如问题涉及数据，请确认查询条件或重新发起。"
            )
        )
        await emit("error", {"error": answer, **(
            {"error_detail": permission_recovery.last_error} if permission_recovery.last_error else {}
        )})
    elif execute and not pending and limit_message:
        answer = limit_message + "请查看本轮已返回的数据。"
    elif execute and not pending and evidence and not any(result["rows"] for result in evidence):
        answer = "DataSemantic 在当前条件和数据权限范围内未返回记录。"
    elif execute and not pending and model_reply_error:
        answer = model_reply_error + "请查看本轮已返回的查询结果。"
    elif execute and not pending and preparation_error:
        answer = preparation_error + "请查看本轮已返回的查询结果。"
    elif execute and not answer:
        model_reply_error = _MODEL_REPLY_FAILURE
        answer = model_reply_error + "请查看本轮已返回的查询结果。"
    if execute and any(result.get("rows_truncated") for result in evidence):
        answer += "\n查询结果已截断，以上结论仅覆盖返回的数据。"
    if execute and not pending and evidence and permission_recovery.last_error and explicit_authorization is not None:
        try:
            # A failed extra query cannot erase successful evidence. Synthesize the
            # business answer in an isolated, query-free call; host owns error text.
            current_evidence = await revalidate_evidence(semantic.authorization_context, evidence)
            verified = current_evidence.results
            finalization_error = current_evidence.error
            if verified:
                evidence_answer = evidence_table(verified)
                if not budget or ((budget.limit is None or budget.calls < budget.limit)
                                  and (budget.token_limit is None or budget.tokens < budget.token_limit)):
                    evidence_answer = await summarize_verified_evidence(
                        model=model, question=original_question, followup=request.question,
                        results=verified, tool_choice=_tool_choice_for(model, "submit_verified_answer"),
                        callbacks=config.get("callbacks", ()),
                    )
                after = await revalidate_evidence(semantic.authorization_context, evidence,
                                                  expected=current_evidence.fingerprint)
                finalization_error = after.error
            if finalization_error:
                evidence_answer = None
                answer = finalization_error.get("user_message", "成功结果需要重新验证，请重新查询。")
                query_error = answer
            elif evidence_answer:
                answer = evidence_answer.text
                if evidence_answer.coverage == "complete":
                    query_error = ""
                    answer += "\n\n补充查询已被当前规则拦截，不影响上述已验证的结果。"
                else:
                    answer += "\n\n查询说明：" + permission_recovery.public_error()
                if final_ai_message_id and not (limit_message or model_reply_error or preparation_error):
                    await graph.aupdate_state(config, {"messages": [AIMessage(content=answer, id=final_ai_message_id)],
                        "chat_model_calls": budget.calls if budget else 0,
                        "chat_model_tokens": budget.tokens if budget else 0}, as_node="model")
                    await graph.aupdate_state(config, None, as_node=END)
            else:
                answer = permission_recovery.public_error()
            if streamed_text:
                await emit("answer_discard", {})
                streamed_text = ""
        except asyncio.CancelledError:
            await _recover_checkpoint(graph, config, snapshot)
            raise
    elif execute and not pending and evidence and permission_recovery.last_error:
        answer = permission_recovery.public_error() + "\n已完成的数据见本轮查询结果；未完成部分暂时无法给出结论。"
    elif execute and not pending and evidence and query_error:
        answer += "\n部分查询未完成，本轮结论仅覆盖已成功返回的数据。"
    if not execute:
        return 0
    if (
        execute
        and not pending
        and last_result
        and (not chart_emitted or chart_emitted_index != last_result_index)
    ):
        result_rows = last_result.get("rows") or []
        result_columns = last_result.get("columns") or []
        if result_rows and result_columns:
            try:
                fallback_spec = recommend_chart_spec(
                    question=request.question,
                    columns=result_columns,
                    rows=result_rows,
                    result_index=last_result_index or 0,
                )
                if fallback_spec and not validate_chart_spec(
                    fallback_spec, result_columns, result_rows
                ):
                    fallback_option = spec_to_echarts_option(
                        fallback_spec, result_rows, result_columns
                    )
                    chart_type, chart_spec, chart_config = (
                        fallback_spec["type"],
                        fallback_spec,
                        fallback_spec,
                    )
                    chart_emitted = True
                    chart_emitted_index = last_result_index
                    await emit(
                        "chart",
                        {
                            "chart_type": chart_type,
                            "chart_spec": chart_spec,
                            "chart_config": fallback_option or None,
                        },
                    )
            except Exception:
                logger.exception("chart spec fallback failed")
    succeeded = bool(pending or ((evidence or (query_free and answer)) and not (
        limit_message or model_reply_error or preparation_error or query_error
    )))
    await emit(
        "step",
        {
            "name": "deepagents",
            "label": "分析问题",
            "status": "ok" if succeeded else "error",
        },
    )
    await emit("summary", {"content": answer})
    # The graph has accepted the reply (or ended the old wait). Acknowledge it
    # independently of record persistence, as my-cowork's human lifecycle does.
    if isinstance(payload, Command):
        await emit("human.answered", {"interrupt_id": request.resume_interrupt_id, "answer": request.question})
    elif pending_interrupt and new_question:
        await emit("human.cancelled", {"interrupt_id": pending_interrupt.id})
    record_id = await persist_turn(
        request=request,
        current_user_id=user_id,
        workspace_oid=workspace_oid,
        question=request.question,
        resolved_question=original_question,
        turn_type="retry" if isinstance(payload, Command) else "standalone",
        sql=last_sql,
        sql_error=None if succeeded else answer,
        exec_result={
            **(last_result or {}), "context_boundary": context_key,
            **({"answer_coverage": {"coverage": evidence_answer.coverage,
                                   "evidence_ids": list(evidence_answer.evidence_ids),
                                   "model_generated": evidence_answer.model_generated}}
               if evidence_answer else {}),
            **({"question_assessment": {key: value for key, value in question_assessment.items()
                                       if key not in {"after_tool", "call_id"}}} if question_assessment else {}),
            **({"human_reply_to": request.resume_interrupt_id, "human_answer": request.question}
               if isinstance(payload, Command) else {}),
            **({"human_cancelled_to": pending_interrupt.id}
               if pending_interrupt and new_question else {}),
        },
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
    if request.conversation_id and not record_id:
        await emit("step", {
            "name": "persist_turn", "label": "保存问答记录", "status": "error",
            "detail": "本轮结果已返回，但问答记录保存失败，请先保留当前结果。",
        })
    return record_id
