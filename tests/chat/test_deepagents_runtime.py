import asyncio
import json
from contextlib import asynccontextmanager, nullcontext

import httpx
import pytest
from deepagents import create_deep_agent
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.messages.utils import count_tokens_approximately
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from src.chat.schemas import ChatRequest
from src.chat.service import deepagents_runtime, semantic_query
from src.chat.service.deepagents_runtime import ask_human
from src.common.core.config import Settings

_RAW_MINIMAX_CALL = (
    "]<]minimax[>[<tool_call> ]<]minimax[>[<invoke name=\"query_semantic\">"
    "]<]minimax[>[<sql>SELECT private_field FROM exam_facts]<]minimax[>[</sql>"
    "]<]minimax[>[</invoke> ]<]minimax[>[</tool_call>"
)


class ToolCallingModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def context_call(id_):
    return AIMessage(
        content="", tool_calls=[{"name": "get_analysis_context", "args": {}, "id": id_}]
    )


def _planning_call():
    return AIMessage(content="", tool_calls=[{
        "name": "write_todos", "id": "initial-plan",
        "args": {"todos": [{"content": "核对考试指标", "status": "in_progress"}]},
    }])


def test_context_is_preloaded_without_a_forced_model_round(monkeypatch):
    """发布目录随系统提示下发：直接作答只花一次调用，不再空转三轮读上下文。"""
    events = []
    model = ToolCallingModel(responses=[AIMessage(content="直接回答") for _ in range(4)])
    monkeypatch.setattr(semantic_query.SemanticQuery, "context", lambda _self: {})
    monkeypatch.setattr(
        semantic_query.SemanticQuery,
        "run",
        lambda *_a, **_kw: pytest.fail("prompt-free answer should not query"),
    )
    monkeypatch.setattr(deepagents_runtime, "edu_scope_dict_for_user_id", lambda _uid: {})
    settings = Settings(_env_file=None, chat_max_steps=None)
    monkeypatch.setattr(deepagents_runtime, "get_settings", lambda: settings)

    async def emit(event, payload):
        events.append((event, payload))

    result = asyncio.run(
        deepagents_runtime.run_chat_stream(
            request=ChatRequest(datasource_id=4, question="总人数是多少"),
            current_user_id=7,
            workspace_oid=1,
            emit=emit,
            checkpointer=InMemorySaver(),
            model=model,
            execute=False,
        )
    )

    assert result == 0 and model.i == 1
    assert any(
        event == "error" and "未取得 DataSemantic" in data["error"] for event, data in events
    )


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("总人数是多少", False),
        ("解释一下什么是均分", False),
        ("最近一次考试各年级的总分平均分和及格率是多少？", True),
        ("对比各校语文均分排名", True),
        ("语文和数学谁更高？英语呢？", True),
    ],
)
def test_planning_only_for_complex_questions(question, expected):
    """简单查询不再强制发布计划，只有多步骤/多维度问题才要求 write_todos。"""
    assert deepagents_runtime._planning_required(question) is expected


def test_tool_choice_prefers_named_function_when_gateway_supports_it():
    class Model:
        def __init__(self, base_url):
            self.openai_api_base = base_url

    minimax = Model("https://api.minimaxi.com/v1")
    moonshot = Model("https://api.moonshot.cn/v1")
    other = Model("https://llm.example.com/v1")
    assert deepagents_runtime._tool_choice_for(minimax, "write_todos") == "write_todos"
    assert deepagents_runtime._tool_choice_for(moonshot, "write_todos") == "required"
    assert deepagents_runtime._tool_choice_for(other, "write_todos") == "auto"


def test_view_query_can_be_resubmitted_after_reading_fields(monkeypatch):
    sql = "SELECT average_score FROM exam_summary"
    bad_sql = "SELECT avg_score FROM exam_summary"
    error_detail = {
        "code": "FIELD_NOT_PUBLISHED",
        "source": {"alias": "exam_summary", "relation": "exam_summary"},
        "field": "avg_score",
        "candidates": [{"name": "average_score"}],
    }
    calls = []
    events = []

    def describe(_self, name):
        assert name == "exam_summary"
        calls.append("describe")
        return {"name": name, "columns": [{"name": "average_score"}]}

    def run_query(_self, submitted_sql, *, execute):
        assert not execute
        calls.append("query")
        if "describe" not in calls:
            raise semantic_query.SemanticViewDescriptionRequiredError("请先读取视图字段。")
        if submitted_sql == bad_sql:
            raise semantic_query.SemanticFieldError("字段未发布", error_detail)
        assert submitted_sql == sql
        return {"sql": sql, "tables": ["tb_score"]}

    class RepairModel(ToolCallingModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            if self.i == 1:
                assert json.loads(messages[-1].content)["views"] == [{"name": "exam_summary"}]
            if self.i == 4:
                assert json.loads(messages[-1].content)["descriptions"][0]["columns"] == [
                    {"name": "average_score"}
                ]
            if self.i == 5:
                assert isinstance(messages[-1], ToolMessage)
                assert json.loads(messages[-1].content)["error_detail"] == error_detail
            return super()._generate(messages, stop, run_manager, **kwargs)

    model = RepairModel(
        responses=[
            context_call("context"),
            AIMessage(content=_RAW_MINIMAX_CALL),
            AIMessage(
                content="",
                tool_calls=[{"name": "query_semantic", "args": {"sql": sql}, "id": "first"}],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "describe_semantic_model",
                        "args": {"names": ["exam_summary"]},
                        "id": "describe",
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[{"name": "query_semantic", "args": {"sql": bad_sql}, "id": "bad-field"}],
            ),
            AIMessage(
                content="",
                tool_calls=[{"name": "query_semantic", "args": {"sql": sql}, "id": "retry"}],
            ),
            AIMessage(content="完成"),
        ]
    )
    monkeypatch.setattr(semantic_query.SemanticQuery, "context", lambda _self: {
        "views": [{"name": "exam_summary", "columns": [{"name": "average_score"}]}],
    })
    monkeypatch.setattr(semantic_query.SemanticQuery, "describe", describe)
    monkeypatch.setattr(semantic_query.SemanticQuery, "run", run_query)
    monkeypatch.setattr(deepagents_runtime, "log_tool_call_fire_and_forget", lambda **_kw: None)
    monkeypatch.setattr(deepagents_runtime, "edu_scope_dict_for_user_id", lambda _uid: {})
    settings = Settings(_env_file=None, chat_max_steps=None)
    monkeypatch.setattr(deepagents_runtime, "get_settings", lambda: settings)

    async def emit(event, payload):
        events.append((event, payload))

    asyncio.run(
        deepagents_runtime.run_chat_stream(
            request=ChatRequest(datasource_id=4, question="均分是多少"),
            current_user_id=7,
            workspace_oid=1,
            emit=emit,
            checkpointer=InMemorySaver(),
            model=model,
            execute=False,
        )
    )
    assert calls == ["query", "describe", "query", "query"]
    assert any(
        event == "tool_result" and payload["data"].get("error_detail") == error_detail
        for event, payload in events
    )
    assert any(event == "sql" and payload["sql"] == sql for event, payload in events)


def test_view_query_auto_reads_fields_without_another_model_round(monkeypatch):
    """直接查询视图时宿主自动补读字段并重试，模型不必再单独跑一轮 describe。"""
    sql = "SELECT average_score FROM exam_summary"
    described, events = [], []

    def describe(_self, name):
        described.append(name)
        return {"name": name, "columns": [{"name": "average_score"}]}

    def run_query(_self, submitted_sql, *, execute):
        if not described:
            raise semantic_query.SemanticViewDescriptionRequiredError(
                "请先用 describe_semantic_model 读取视图字段。", ["exam_summary"]
            )
        return {"sql": submitted_sql, "tables": ["tb_score"]}

    model = ToolCallingModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[{"name": "query_semantic", "args": {"sql": sql}, "id": "q"}],
            ),
            AIMessage(content="完成"),
        ]
    )
    monkeypatch.setattr(semantic_query.SemanticQuery, "context", lambda _self: {})
    monkeypatch.setattr(semantic_query.SemanticQuery, "describe", describe)
    monkeypatch.setattr(semantic_query.SemanticQuery, "run", run_query)
    monkeypatch.setattr(deepagents_runtime, "edu_scope_dict_for_user_id", lambda _uid: {})
    monkeypatch.setattr(deepagents_runtime, "log_tool_call_fire_and_forget", lambda **_kw: None)
    settings = Settings(_env_file=None, chat_max_steps=None)
    monkeypatch.setattr(deepagents_runtime, "get_settings", lambda: settings)

    async def emit(event, payload):
        events.append((event, payload))

    asyncio.run(
        deepagents_runtime.run_chat_stream(
            request=ChatRequest(datasource_id=4, question="语文均分是多少"),
            current_user_id=7,
            workspace_oid=1,
            emit=emit,
            checkpointer=InMemorySaver(),
            model=model,
            execute=False,
        )
    )

    assert described == ["exam_summary"]
    assert sum(
        event == "tool_call" and payload["tool"] == "query_semantic" for event, payload in events
    ) == 1
    assert any(event == "sql" and payload["sql"] == sql for event, payload in events)
    assert not any(
        event == "tool_result" and payload.get("success") is False for event, payload in events
    )


def test_deepagent_streams_question_and_resumes_same_thread():
    async def run():
        model = ToolCallingModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "ask_human",
                            "args": {"question": "请选择考试"},
                            "id": "ask-exam",
                        }
                    ],
                ),
                AIMessage(content="已确认考试"),
                AIMessage(content="继续分析数学成绩"),
            ],
        )
        saver = InMemorySaver()
        agent = create_deep_agent(model=model, tools=[ask_human], checkpointer=saver)
        config = {"configurable": {"thread_id": "exam-chat"}}
        events = [
            event
            async for event in agent.astream(
                {"messages": [HumanMessage(content="本班数学均分")]},
                config,
                stream_mode="updates",
            )
        ]
        pending = events[-1]["__interrupt__"][0]
        assert pending.value == {"kind": "ask_human", "question": "请选择考试"}

        resumed = await agent.ainvoke(Command(resume={pending.id: "期中考试"}), config)
        answer = next(msg for msg in resumed["messages"] if isinstance(msg, ToolMessage))
        assert answer.tool_call_id == "ask-exam"
        assert answer.content == "期中考试"
        assert resumed["messages"][-1].content == "已确认考试"

        followup = await agent.ainvoke({"messages": [HumanMessage(content="那及格率呢")]}, config)
        questions = [msg.content for msg in followup["messages"] if isinstance(msg, HumanMessage)]
        assert questions == ["本班数学均分", "那及格率呢"]
        assert followup["messages"][-1].content == "继续分析数学成绩"

    asyncio.run(run())


def _query_turn(responses):
    """一次完整的问数轮：读上下文、发布计划、查一次、给出结论。"""
    return [
        context_call("context"),
        _planning_call(),
        AIMessage(
            content="",
            tool_calls=[{"name": "query_semantic", "args": {"sql": "SELECT metric FROM exam_facts"}, "id": "q"}],
        ),
        *responses,
    ]


def _install_query_mocks(monkeypatch):
    async def persist(**_record):
        return 0

    monkeypatch.setattr(
        semantic_query.SemanticQuery,
        "run",
        lambda _self, sql, *, execute: {
            "sql": sql,
            "tables": ["exam_facts"],
            "bundle_id": "edu",
            "manifest_hash": "a" * 64,
            "columns": ["value"],
            "rows": [[12]],
        },
    )
    monkeypatch.setattr(semantic_query.SemanticQuery, "context", lambda _self: {})
    monkeypatch.setattr(deepagents_runtime, "edu_scope_dict_for_user_id", lambda _uid: {})
    monkeypatch.setattr(deepagents_runtime, "log_tool_call_fire_and_forget", lambda **_kw: None)
    monkeypatch.setattr(deepagents_runtime, "persist_turn", persist)
    settings = Settings(_env_file=None, chat_max_steps=None, chat_max_total_tokens=None)
    monkeypatch.setattr(deepagents_runtime, "get_settings", lambda: settings)


def test_final_answer_streams_as_answer_deltas(monkeypatch):
    """结论正文边生成边下发，summary 仍作为最终全文。"""
    answer = "本次考试全年级数学平均分为 82.5 分，及格率为 91%。"
    _install_query_mocks(monkeypatch)
    model = ToolCallingModel(responses=_query_turn([AIMessage(content=answer)]))
    events = []

    async def emit(event, payload):
        events.append((event, payload))

    asyncio.run(
        deepagents_runtime.run_chat_stream(
            request=ChatRequest(datasource_id=4, question="本次考试数学均分和及格率是多少"),
            current_user_id=7,
            workspace_oid=1,
            emit=emit,
            checkpointer=InMemorySaver(),
            model=model,
        )
    )

    deltas = "".join(payload["text"] for event, payload in events if event == "answer_delta")
    assert deltas == answer
    assert not any(event == "answer_discard" for event, _ in events)
    assert ("summary", {"content": answer}) in events


def test_tool_turn_preamble_discards_streamed_draft(monkeypatch):
    """已有结果后的追加查询会先说一句思路：先下发，随后作废，再流式给结论。"""
    preamble = "我先查一次各班的平均分，然后再核对及格率口径是否一致。"
    answer = "本次考试全年级数学平均分为 82.5 分，及格率为 91%。"
    _install_query_mocks(monkeypatch)
    model = ToolCallingModel(
        responses=[
            context_call("context"),
            _planning_call(),
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "query_semantic", "args": {"sql": "SELECT metric FROM exam_facts"}, "id": "q1"}
                ],
            ),
            AIMessage(
                content=preamble,
                tool_calls=[
                    {"name": "query_semantic", "args": {"sql": "SELECT rate FROM exam_facts"}, "id": "q2"}
                ],
            ),
            AIMessage(content=answer),
        ]
    )
    events = []

    async def emit(event, payload):
        events.append((event, payload))

    asyncio.run(
        deepagents_runtime.run_chat_stream(
            request=ChatRequest(datasource_id=4, question="本次考试数学均分和及格率是多少"),
            current_user_id=7,
            workspace_oid=1,
            emit=emit,
            checkpointer=InMemorySaver(),
            model=model,
        )
    )

    kinds = [event for event, _ in events]
    assert "answer_delta" in kinds and "answer_discard" in kinds
    assert kinds.index("answer_discard") > kinds.index("answer_delta")
    assert ("summary", {"content": answer}) in events


def test_inline_think_streams_into_reasoning(monkeypatch):
    """正文里的 <think> 块走思考流，正文只保留结论。"""
    answer = "本次考试全年级数学平均分为 82.5 分，及格率为 91%。"
    _install_query_mocks(monkeypatch)
    model = ToolCallingModel(
        responses=_query_turn(
            [AIMessage(content=f"<think>先核对总分口径，再看及格线</think>{answer}")]
        )
    )
    events = []

    async def emit(event, payload):
        events.append((event, payload))

    asyncio.run(
        deepagents_runtime.run_chat_stream(
            request=ChatRequest(datasource_id=4, question="本次考试数学均分和及格率是多少"),
            current_user_id=7,
            workspace_oid=1,
            emit=emit,
            checkpointer=InMemorySaver(),
            model=model,
        )
    )

    reasoning = "".join(p["text"] for event, p in events if event == "reasoning_delta")
    deltas = "".join(p["text"] for event, p in events if event == "answer_delta")
    assert "先核对总分口径" in reasoning
    assert "think" not in deltas.lower()
    assert deltas == answer
    assert ("summary", {"content": answer}) in events


def test_drain_think_handles_tags_split_across_chunks():
    reasoning, answer = [], []
    carry, in_think = "", False
    for char in "<think>先看口径</think>最终结论":
        think_part, answer_part, carry, in_think = deepagents_runtime._drain_think(
            carry + char, in_think
        )
        reasoning.append(think_part)
        answer.append(answer_part)
    assert "".join(reasoning) == "先看口径"
    assert "".join(answer) == "最终结论"
    assert carry == ""


def test_provider_reasoning_content_streams(monkeypatch):
    """服务商单独下发的 reasoning_content 同样进入思考流。"""
    _install_query_mocks(monkeypatch)
    model = ToolCallingModel(
        responses=_query_turn(
            [AIMessage(content="", additional_kwargs={"reasoning_content": "先确认口径再汇总"})]
        )
    )
    events = []

    async def emit(event, payload):
        events.append((event, payload))

    asyncio.run(
        deepagents_runtime.run_chat_stream(
            request=ChatRequest(datasource_id=4, question="本次考试数学均分是多少"),
            current_user_id=7,
            workspace_oid=1,
            emit=emit,
            checkpointer=InMemorySaver(),
            model=model,
        )
    )

    reasoning = "".join(p["text"] for event, p in events if event == "reasoning_delta")
    assert reasoning == "先确认口径再汇总"


@pytest.mark.parametrize("max_steps", [None, 4])
def test_chat_sse_resumes_deepagents_across_requests(monkeypatch, max_steps):
    from src.chat.api import chat

    @asynccontextmanager
    async def lock(*_args, **_kwargs):
        yield

    async def persist(**record):
        records.append(record)
        return len(records)

    def query_call(name):
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "query_semantic",
                    "args": {"sql": f"SELECT {name} FROM exam_facts"},
                    "id": name,
                }
            ],
        )

    def plan_call(status, note=""):
        return AIMessage(content=note, tool_calls=[{
            "name": "write_todos", "id": f"plan-{status}",
            "args": {"todos": [{"content": "查询缺考人数", "status": status}]},
        }])

    def semantic_result(self, sql, *, execute):
        assert self.datasource_id == 4 and self.user_id == 7 and execute
        return {
            "sql": sql,
            "tables": ["tb_score"],
            "bundle_id": "edu",
            "manifest_hash": "a" * 64,
            "columns": ["value"],
            "rows": [[12]],
        }

    records = []
    bound_tools = []

    class SingleAgentModel(ToolCallingModel):
        def bind_tools(self, tools, **kwargs):
            bound_tools.extend(tool.name for tool in tools)
            return self

    settings = Settings(_env_file=None, chat_max_steps=max_steps)
    monkeypatch.setattr(deepagents_runtime, "get_settings", lambda: settings)
    model = SingleAgentModel(
        responses=[
            context_call("first-context"),
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "ask_human", "args": {"question": "请选择考试"}, "id": "ask-exam"},
                ],
            ),
            plan_call("in_progress"),
            query_call("student_count"),
            *([AIMessage(content="已确认期中考试")] if max_steps is None else []),
            context_call("followup-context"),
            plan_call("in_progress", "<think>internal</think>先核对缺考人数。"),
            query_call("absent_count"),
            *([plan_call("completed")] if max_steps is None else []),
            AIMessage(content="继续查询期中考试"),
            context_call("third-context"),
            plan_call("in_progress"),
            AIMessage(content="我猜答案是 999 人"),
        ]
    )
    monkeypatch.setattr(
        semantic_query.SemanticQuery,
        "context",
        lambda self: {"models": [{"name": "exam_facts"}]},
    )
    monkeypatch.setattr(semantic_query.SemanticQuery, "run", semantic_result)
    monkeypatch.setattr(deepagents_runtime, "log_tool_call_fire_and_forget", lambda **_kw: None)
    monkeypatch.setattr(deepagents_runtime, "_thread_lock", lock)
    monkeypatch.setattr(deepagents_runtime, "persist_turn", persist)
    monkeypatch.setattr(
        deepagents_runtime, "get_default_llm", lambda: type("LLM", (), {"_llm": model})()
    )
    monkeypatch.setattr(deepagents_runtime, "out_of_scope_question", lambda *_a, **_kw: None)
    monkeypatch.setattr(deepagents_runtime, "edu_scope_dict_for_user_id", lambda _uid: {})
    monkeypatch.setattr(chat, "assert_datasource_accessible", lambda *_a: None)
    monkeypatch.setattr(chat, "_edu_out_of_scope_message", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        chat.chat_crud,
        "get_conversation_by_id",
        lambda *_a: type("Conversation", (), {"datasource_id": 4})(),
    )

    app = FastAPI()
    app.state.chat_checkpointer = InMemorySaver()
    app.include_router(chat.router)
    app.dependency_overrides[chat.get_session] = lambda: None
    app.dependency_overrides[chat.get_workspace_oid] = lambda: 1
    app.dependency_overrides[chat.get_current_user] = lambda: type(
        "User", (), {"id": 7, "account": "test"}
    )()

    with TestClient(app) as client:
        base = {"datasource_id": 4, "conversation_id": 9}
        first = client.post("/chat/chat-stream", json={**base, "question": "数学均分"})
        interrupt_id = records[0]["exec_result"]["interrupt_id"]
        reply = client.post(
            "/chat/chat-stream",
            json={
                **base,
                "question": "期中考试",
                "agent_mode": "team",
                "resume_interrupt_id": interrupt_id,
            },
        )
        duplicate = client.post(
            "/chat/chat-stream",
            json={**base, "question": "期中考试", "resume_interrupt_id": interrupt_id},
        )
        followup = client.post(
            "/chat/chat-stream", json={**base, "question": "及格率呢", "agent_mode": "legacy"}
        )

        no_query = client.post("/chat/chat-stream", json={**base, "question": "总人数是多少"})

    assert all(response.status_code == 200 for response in (first, reply, followup))
    assert "event: clarify" in first.text and "请选择考试" in first.text
    assert "event: done" in first.text and "event: error" not in first.text
    if max_steps is None:
        assert "已确认期中考试" in reply.text
    else:
        assert "模型调用上限（4 次）" in reply.text
    assert "event: error" in duplicate.text and "已经处理或失效" in duplicate.text
    assert "继续查询期中考试" in followup.text
    assert records[1]["turn_type"] == "retry"
    assert all(record["agent_mode"] == "agent" for record in records)
    assert set(bound_tools) == {
        "get_analysis_context",
        "describe_semantic_model",
        "query_semantic",
        "chart_semantic_result",
        "ask_human",
        "write_todos",
    }
    if max_steps is None:
        assert followup.text.count("event: task_plan") == 2
        assert "先核对缺考人数" in followup.text
        # 思考内容只走 reasoning_delta，不进入最终结论
        assert "event: reasoning_delta" in followup.text
        assert "internal" not in followup.text.split("event: summary", 1)[1]
        assert records[2]["plans"] == ["查询缺考人数"]
        assert records[2]["plan_states"] == [{"index": 0, "state": "ok"}]
        assert next(c for c in records[2]["tool_calls"] if c["tool"] == "query_semantic")["sub_task_index"] == 0

    assert "event: result" in reply.text and "event: result" in followup.text
    assert "本轮未取得 DataSemantic" in no_query.text and "999 人" not in no_query.text


def test_token_budget_estimates_usage_when_provider_omits_it():
    messages = [HumanMessage(content="查询考试成绩")]
    prompt_tokens = count_tokens_approximately(messages)
    budget = deepagents_runtime._ModelCallBudget(None, prompt_tokens + 1)
    model = ToolCallingModel(responses=[AIMessage(content="分析结论需要更多 Token")])

    with pytest.raises(deepagents_runtime._ModelCallLimitError, match="Token 上限"):
        asyncio.run(model.ainvoke(messages, config={"callbacks": [budget]}))

    assert budget.tokens > prompt_tokens + 1


@pytest.mark.parametrize(("max_steps", "query_succeeds"), [(None, True), (4, True), (4, False)])
def test_deepagent_model_call_budget(monkeypatch, caplog, max_steps, query_succeeds):
    events, records, queries = [], [], []

    async def emit(event, payload):
        events.append((event, payload))

    async def persist(**record):
        records.append(record)
        return 42

    def run_query(_self, sql, *, execute):
        assert execute
        queries.append(sql)
        if not query_succeeds:
            raise semantic_query.SemanticQueryError("当前口径未发布")
        return {
            "sql": sql,
            "tables": ["tb_score"],
            "bundle_id": "edu",
            "manifest_hash": "a" * 64,
            "columns": ["value"],
            "rows": [[12]],
        }

    monkeypatch.setattr(semantic_query.SemanticQuery, "context", lambda _self: {})
    monkeypatch.setattr(semantic_query.SemanticQuery, "run", run_query)
    monkeypatch.setattr(deepagents_runtime, "edu_scope_dict_for_user_id", lambda _uid: {})
    monkeypatch.setattr(deepagents_runtime, "log_tool_call_fire_and_forget", lambda **_kw: None)
    monkeypatch.setattr(deepagents_runtime, "_thread_lock", lambda *_a: nullcontext())
    monkeypatch.setattr(deepagents_runtime, "persist_turn", persist)
    monkeypatch.setenv("CHAT_MAX_STEPS", str(max_steps) if max_steps is not None else "")
    settings = Settings(_env_file=None)
    monkeypatch.setattr(deepagents_runtime, "get_settings", lambda: settings)

    async def run():
        model = ToolCallingModel(
            responses=[context_call("context"), _planning_call()]
            + [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "query_semantic",
                            "args": {
                                "sql": f"SELECT metric_{0 if query_succeeds else i} FROM exam_facts"
                            },
                            "id": f"query-{i}",
                        }
                    ],
                )
                for i in range(30)
            ]
            + [AIMessage(content="分析完成"), AIMessage(content="额外调用")],
        )
        record_id = await deepagents_runtime.run_chat_stream(
            request=ChatRequest(datasource_id=4, conversation_id=9, question="总人数是多少"),
            current_user_id=7,
            workspace_oid=1,
            emit=emit,
            checkpointer=InMemorySaver(),
            model=model,
        )
        assert record_id == 42
        assert model.i == (33 if max_steps is None else max_steps)

    asyncio.run(run())

    assert "DeepAgents exhausted the LangGraph step limit" not in caplog.text
    assert records[0]["is_success"] is (query_succeeds and max_steps is None)
    if query_succeeds:
        assert len(queries) == (30 if max_steps is None else max_steps - 2)
        assert records[0]["exec_result"]["rows"] == [[12]]
        if max_steps is None:
            assert records[0]["summary"] == "分析完成"
        else:
            assert "模型调用上限（4 次）" in records[0]["summary"]
        assert not any(event == "error" for event, _ in events)
    else:
        assert len(queries) == max_steps - 2
        assert ("error", {"error": "当前口径未发布"}) in events


@pytest.mark.parametrize("final_answer", ["已核对两个指标，其他指标无法验证。", "", _RAW_MINIMAX_CALL])
def test_planned_parallel_queries_have_no_fixed_cap(monkeypatch, final_answer):
    events, records, queries = [], [], []

    async def emit(event, payload):
        events.append((event, payload))

    async def persist(**record):
        records.append(record)
        return 42

    def run_query(_self, sql, *, execute):
        assert execute
        queries.append(sql)
        return {
            "sql": sql, "tables": ["tb_score"], "bundle_id": "edu", "manifest_hash": "a" * 64,
            "columns": ["value"], "rows": [[12]],
        }

    def query_call(sql, index):
        return {"name": "query_semantic", "args": {"sql": sql}, "id": f"query-{index}"}

    statements = [f"SELECT metric_{index} FROM exam_facts" for index in range(64)]
    model = ToolCallingModel(responses=[
        context_call("context"),
        AIMessage(content="", tool_calls=[query_call("SELECT bypass FROM exam_facts", -1)]),
        _planning_call(),
        AIMessage(content="", tool_calls=[query_call(sql, i) for i, sql in enumerate(statements)]),
        AIMessage(content=final_answer),
        *([AIMessage(content=_RAW_MINIMAX_CALL)] if final_answer == _RAW_MINIMAX_CALL else []),
        AIMessage(content="不应继续执行"),
    ])
    settings = Settings(_env_file=None, chat_max_steps=None)
    monkeypatch.setattr(deepagents_runtime, "get_settings", lambda: settings)
    monkeypatch.setattr(semantic_query.SemanticQuery, "context", lambda _self: {"dialect": "postgres"})
    monkeypatch.setattr(semantic_query.SemanticQuery, "run", run_query)
    monkeypatch.setattr(deepagents_runtime, "edu_scope_dict_for_user_id", lambda _uid: {})
    monkeypatch.setattr(deepagents_runtime, "log_tool_call_fire_and_forget", lambda **_kw: None)
    monkeypatch.setattr(deepagents_runtime, "persist_turn", persist)

    result = asyncio.run(deepagents_runtime.run_chat_stream(
        request=ChatRequest(datasource_id=4, question="分析历次考试"),
        current_user_id=7, workspace_oid=1, emit=emit,
        checkpointer=InMemorySaver(), model=model,
    ))

    assert result == 42 and model.i == (6 if final_answer == _RAW_MINIMAX_CALL else 5)
    # 目录已内联进系统提示，首轮直接查询不再被预检拦截
    assert sorted(queries) == sorted([*statements, "SELECT bypass FROM exam_facts"])
    assert sum(event == "result" for event, _ in events) == 65
    assert records[0]["plan_states"] == [{"index": 0, "state": "running"}]
    if final_answer in {"", _RAW_MINIMAX_CALL}:
        assert not records[0]["is_success"]
        assert records[0]["summary"] == "本轮未能生成分析结论。请查看本轮已返回的查询结果。"
    else:
        assert records[0]["is_success"]
        assert records[0]["summary"] == final_answer


def test_chat_scope_check_looks_up_school_in_edu_database(monkeypatch):
    from datasource.service import edu_permission
    from src.chat.api import chat
    from src.datasource.db import db

    monkeypatch.setattr(
        edu_permission,
        "edu_scope_dict_for_user_id",
        lambda _uid: {"edu_role": "school_admin", "school_id": "GZ_123"},
    )
    config = {"host": "edu-db"}
    monkeypatch.setattr(chat, "edu_db_connection", lambda: ("pg", config))

    def execute(db_type, actual_config, sql):
        assert db_type == "pg" and actual_config is config and "FROM tb_school" in sql
        return True, "", {"rows": [["GZ_123", "扬州中学", "扬州中学"]]}

    monkeypatch.setattr(db, "execute_sql", execute)
    assert chat._edu_out_of_scope_message(7, "扬州中学均分", datasource_id=4) is None
    assert (
        chat._edu_out_of_scope_message(7, "新华中学均分", datasource_id=4) == "当前数据无权限访问"
    )


def test_describe_view_uses_data_semantic_plan_without_execution(monkeypatch):
    binding = {"bundle_id": "edu-v1", "manifest_hash": "a" * 64}
    settings = Settings(
        _env_file=None,
        database_edu_url="postgresql://reader:example@edu-db/exam_data",
        datasemantic_url="http://planner",
        datasemantic_token="test-only",
        datasemantic_bindings={4: binding},
    )
    context = {
        **binding,
        "dialect": "postgres",
        "models": [{"name": "score_model", "columns": [{"name": "score"}]}],
        "views": [{"name": "exam_summary", "description": "考试成绩"}],
    }
    requests = []

    def call(method, url, **kwargs):
        requests.append(method)
        if method == "GET":
            assert url.endswith("/internal/v1/bundles/edu-v1/context")
            data = context
        else:
            assert url.endswith("/internal/v1/plan")
            assert kwargs["json"] == {
                **binding,
                "semantic_sql": 'SELECT * FROM "exam_summary" LIMIT 0',
            }
            data = {
                **binding,
                "dialect": "postgres",
                "physical_sql": (
                    "WITH exam_summary AS (SELECT school_id, AVG(score) AS average_score "
                    "FROM public.tb_score GROUP BY school_id) SELECT * FROM exam_summary LIMIT 0"
                ),
            }
        return httpx.Response(200, request=httpx.Request(method, url), json=data)

    monkeypatch.setattr(semantic_query.httpx, "request", call)
    monkeypatch.setattr(
        semantic_query, "get_db_session", lambda: pytest.fail("metadata used the DB")
    )
    runner = semantic_query.SemanticQuery(settings, 4, 1, 7)
    assert runner.describe("score_model")["columns"] == [{"name": "score"}]
    with pytest.raises(semantic_query.SemanticViewDescriptionRequiredError, match="exam_summary"):
        runner.run("SELECT average_score FROM exam_summary")
    assert requests == ["GET"]
    batch = runner.describe_many(["score_model", "EXAM_SUMMARY", "exam_summary", "missing"])
    assert [item["name"] for item in batch["descriptions"]] == ["score_model", "exam_summary"]
    assert [item["name"] for item in batch["errors"]] == ["missing"]
    summary = batch["descriptions"][1]
    assert summary["description"] == "考试成绩"
    assert summary["columns"] == [{"name": "school_id"}, {"name": "average_score"}]
    assert runner.describe("exam_summary") == summary
    for sql in (
        "SELECT avg_score FROM exam_summary",
        "SELECT s.avg_score FROM exam_summary s JOIN score_model m ON s.school_id = m.score",
    ):
        with pytest.raises(
            semantic_query.SemanticQueryError, match="未发布字段 avg_score.*average_score"
        ):
            runner.run(sql)
    with pytest.raises(semantic_query.SemanticQueryError, match="未出现在当前发布物"):
        runner.describe("public.tb_score")
    with pytest.raises(semantic_query.SemanticQueryError, match="不带 schema"):
        runner.run("SELECT column_name FROM information_schema.columns")
    assert requests == ["GET", "POST"]


def test_view_description_cache_is_scoped_to_published_version(monkeypatch):
    runner = semantic_query.SemanticQuery(Settings(_env_file=None), 4, 1, 7)
    binding = {"bundle_id": "exam", "manifest_hash": "a" * 64}
    plans = []
    monkeypatch.setattr(runner, "_binding", lambda: binding.copy())
    monkeypatch.setattr(runner, "_call", lambda *_: {
        **binding, "dialect": "postgres", "views": [{"name": "summary"}],
    })

    def plan(_sql, release, _dialect):
        column = f"value_{release['manifest_hash'][0]}"
        plans.append(column)
        return {"physical_sql": f"SELECT 1 AS {column}"}

    monkeypatch.setattr(runner, "_plan", plan)
    assert runner.describe("summary")["columns"] == [{"name": "value_a"}]
    assert runner.describe("SUMMARY")["columns"] == [{"name": "value_a"}]
    binding["manifest_hash"] = "b" * 64
    assert runner.describe("summary")["columns"] == [{"name": "value_b"}]
    assert plans == ["value_a", "value_b"]


def test_semantic_plan_uses_original_permission_functions(monkeypatch):
    binding = {"bundle_id": "edu-v1", "manifest_hash": "a" * 64}
    settings = Settings(
        _env_file=None,
        database_edu_url="postgresql://reader:example@edu-db:5544/exam_data",
        datasemantic_url="http://planner",
        datasemantic_token="test-only",
        datasemantic_bindings={4: binding, 8: {**binding, "bundle_id": "edu-v2"}},
    )
    physical = (
        "WITH tb_score AS (SELECT score, school_id FROM public.tb_score), "
        "expanded AS (SELECT * FROM tb_score) SELECT SUM(score) AS total FROM expanded"
    )
    trace = []
    user = type("User", (), {"id": 7})()

    def call(method, url, **kwargs):
        path = url.split("/internal/v1/")[1]
        trace.append(method)
        if method == "GET":
            assert path == "bundles/edu-v1/context"
            data = {
                **binding,
                "dialect": "postgres",
                "views": [{"name": "exam_summary", "columns": [{"name": "total_score"}]}],
            }
        else:
            assert path == "plan"
            assert "SUM(total_score) FROM exam_summary" in kwargs["json"]["semantic_sql"]
            data = {**binding, "dialect": "postgres", "physical_sql": physical}
        return httpx.Response(200, request=httpx.Request(method, url), json=data)

    def rewrite(_session, actual_user, ds_id, _db_type, sql, *, tables_hint):
        assert actual_user is user and ds_id == 4 and tables_hint == ["tb_score"]
        trace.append("row")
        if omit_row:
            return sql
        return sql.replace(
            "public.tb_score AS tb_score)",
            "public.tb_score AS tb_score WHERE school_id = 's1' AND class = 'c1')",
        )

    def validate(_session, actual_user, ds_id, _db_type, sql):
        assert actual_user is user and ds_id == 4 and "school_id = 's1'" in sql
        trace.append("column")
        return "hidden column" if deny_column else None

    def execute(sql, **kwargs):
        assert "school_id = 's1'" in sql and kwargs["max_fix_attempts"] == 0
        assert 'FROM "public".tb_score' in sql
        assert "SELECT *" not in sql
        assert kwargs["db_type"] == "pg"
        assert kwargs["config"]["host"] == "edu-db"
        assert kwargs["config"]["port"] == 5544
        assert kwargs["config"]["database"] == "exam_data"
        trace.append("execute")
        return type(
            "Outcome",
            (),
            {
                "success": True,
                "result": {"columns": ["total", "hidden"], "rows": [[80, "sensitive"]]},
            },
        )()

    def filter_result(_session, actual_user, ds_id, _db_type, _sql, result):
        assert actual_user is user and ds_id == 4 and result["columns"][-1] == "hidden"
        trace.append("filter")
        return {"columns": ["total"], "rows": [[80]]}

    monkeypatch.setattr(semantic_query.httpx, "request", call)
    monkeypatch.setattr(semantic_query, "get_db_session", lambda: nullcontext(object()))

    def authorize(_session, actual_user, ds_id, workspace_oid):
        assert actual_user is user and ds_id == 4 and workspace_oid == 1

    monkeypatch.setattr(semantic_query, "assert_datasource_accessible", authorize)
    monkeypatch.setattr(
        semantic_query, "get_user_by_id", lambda _session, uid: user if uid == 7 else None
    )
    monkeypatch.setattr(semantic_query, "apply_sql_privacy", lambda sql: (sql, []))
    monkeypatch.setattr(semantic_query, "apply_result_privacy", lambda result: result)
    monkeypatch.setattr(semantic_query, "apply_permissions_for_execute", rewrite)
    monkeypatch.setattr(semantic_query, "bypasses_data_row_column_scope", lambda user: False)
    monkeypatch.setattr(
        semantic_query,
        "collect_row_predicate_sqls",
        lambda *_a: ["school_id = 's1'", "class = 'c1'"],
    )
    monkeypatch.setattr(semantic_query, "validate_sql_column_permissions", validate)
    monkeypatch.setattr(semantic_query, "run_sql_with_auto_fix", execute)
    monkeypatch.setattr(semantic_query, "filter_exec_result_by_column_permissions", filter_result)
    runner = semantic_query.SemanticQuery(settings, 4, 1, 7)
    sql = "SELECT SUM(total_score) FROM exam_summary"

    deny_column = omit_row = False
    runner.describe("exam_summary")
    assert runner.run(sql)["rows"] == [[80]]
    assert trace == ["GET", "POST", "row", "column", "execute", "filter"]

    trace.clear()
    assert runner.run(sql, execute=False)["bundle_id"] == "edu-v1"
    assert trace == ["POST", "row", "column"]

    trace.clear()
    omit_row = True
    with pytest.raises(semantic_query.SemanticQueryError, match="原行权限"):
        runner.run(sql)
    assert trace == ["POST", "row"]

    trace.clear()
    omit_row = False
    deny_column = True
    with pytest.raises(semantic_query.SemanticQueryError, match="hidden column"):
        runner.run(sql)
    assert trace == ["POST", "row", "column"]

    with pytest.raises(semantic_query.SemanticQueryError, match="尚未绑定"):
        semantic_query.SemanticQuery(settings, 5, 1, 7).context()

    runner.settings = settings.model_copy(update={"database_edu_url": ""})
    with pytest.raises(semantic_query.SemanticQueryError, match="未配置 DATABASE_EDU_URL"):
        runner.run(sql)


def test_published_columns_allow_output_aliases_and_cte_columns():
    tree = semantic_query._query(
        "WITH totals AS (SELECT s.school_id, s.average_score FROM exam_summary AS s) "
        "SELECT school_id, AVG(average_score) AS avg_score FROM totals "
        "GROUP BY school_id ORDER BY avg_score",
        "postgres",
    )
    semantic_query._check_published_columns(
        tree, "postgres", {"exam_summary": ("视图", "exam_summary", {
            name: {"name": name} for name in ("school_id", "average_score")
        })}
    )


@pytest.mark.parametrize(
    ("sql", "model", "field", "code", "candidates"),
    [
        (
            "SELECT o.xkqm, COUNT(*) AS n FROM tb_score_overview AS o GROUP BY o.xkqm",
            "tb_score_overview",
            "xkqm",
            "FIELD_NOT_PUBLISHED",
            [{"name": "xkqk", "description": "选科编码"}, {"name": "xkkm", "description": "选考科目组合"}],
        ),
        (
            "SELECT e.subject, COUNT(sc.id) FROM tb_score AS sc JOIN tb_exam AS e "
            "ON sc.exam_id = e.id WHERE sc.exam_batch_id = 6 GROUP BY e.subject",
            "tb_score",
            "exam_batch_id",
            "FIELD_WRONG_SOURCE",
            [{"alias": "e", "relation": "tb_exam", "name": "exam_batch_id", "description": "考试批次"}],
        ),
    ],
)
def test_semantic_query_rejects_unpublished_model_fields_before_planning(
    monkeypatch, sql, model, field, code, candidates,
):
    settings = Settings(_env_file=None, database_edu_url="postgresql://reader:example@edu-db/exam_data")
    runner = semantic_query.SemanticQuery(settings, 4, 1, 7)
    columns = {
        "tb_score_overview": ("exam_batch_id", "xkqk", "xkkm"),
        "tb_score": ("id", "exam_id"),
        "tb_exam": ("id", "subject", "exam_batch_id"),
    }
    descriptions = {"xkqk": "选科编码", "xkkm": "选考科目组合", "exam_batch_id": "考试批次"}
    monkeypatch.setattr(runner, "context", lambda: {
        "bundle_id": "exam", "manifest_hash": "a" * 64,
        "dialect": "postgres",
        "models": [
            {"name": name, "columns": [
                {"name": column, "description": descriptions.get(column, "")} for column in fields
            ]}
            for name, fields in columns.items()
        ],
    })
    monkeypatch.setattr(runner, "_plan", lambda *_: pytest.fail("invalid SQL reached DataSemantic"))

    with pytest.raises(semantic_query.SemanticFieldError, match=f"逻辑模型 {model} 未发布字段 {field}") as error:
        runner.run(sql)
    assert error.value.detail["code"] == code
    assert error.value.detail["source"]["relation"] == model
    assert error.value.detail["field"] == field
    assert sorted(error.value.detail["candidates"], key=lambda item: item["name"]) == sorted(
        candidates, key=lambda item: item["name"]
    )
    monkeypatch.setattr(semantic_query, "_query", lambda *_: pytest.fail("cached rejection was parsed again"))
    with pytest.raises(semantic_query.SemanticFieldError) as repeated:
        runner.run(sql)
    assert repeated.value.detail == error.value.detail


@pytest.mark.parametrize(
    ("dialect", "aggregate"),
    [
        ("postgres", "STRING_AGG(name, ',' ORDER BY name)"),
        ("mysql", "GROUP_CONCAT(name ORDER BY name SEPARATOR ',')"),
    ],
)
def test_plan_accepts_builtin_string_aggregation(dialect, aggregate):
    sql = f"SELECT {aggregate} AS names FROM school_model"
    tree = semantic_query._query(sql, dialect)
    assert tree.sql(dialect=dialect).startswith(f"SELECT {aggregate}")
    semantic_query._query(tree.sql(dialect=dialect), dialect, compiled=True)


@pytest.mark.parametrize(
    "sql",
    [
        "WITH tb_score AS (SELECT score FROM public.tb_score) SELECT AVG(score) FROM tb_score",
        "SELECT score FROM private.tb_score",
        "SELECT score FROM public.other_source",
        "SELECT pg_sleep(5) FROM public.tb_score",
        "SELECT public.STRING_AGG(score, ',') FROM public.tb_score",
        "SELECT * FROM public.tb_score",
        "SELECT score INTO stolen FROM public.tb_score",
    ],
)
def test_plan_rejects_unapproved_sql_shapes(sql):
    with pytest.raises(semantic_query.SemanticQueryError):
        tree = semantic_query._query(sql, "postgres")
        semantic_query._check_sources(tree, {"tb_score"}, schema="public")
