"""Cross-question regressions for interruptions, batching and provider history."""

import asyncio
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from pydantic import ValidationError

from src.chat.schemas import ChatRequest
from src.chat.service import deepagents_runtime as runtime
from src.chat.service import semantic_query
from src.chat.service.human_questions import HumanQuestionInput, clarification_payload
from src.llm.openai import ReasoningChatOpenAI
from tests.chat.test_deepagents_runtime import ToolCallingModel, _install_query_mocks


def call(name, id_, **args):
    return {"name": name, "id": id_, "args": args, "type": "tool_call"}


def query(id_="query"):
    return AIMessage(content="", tool_calls=[call("query_semantic", id_, sql=f"SELECT {id_} FROM exam_facts")])


@pytest.fixture
def host(monkeypatch):
    _install_query_mocks(monkeypatch)
    monkeypatch.setattr(runtime, "authorization_fingerprint", lambda *_a: "authorized")
    monkeypatch.setattr(runtime, "load_conversation_history", lambda *_a: [])
    monkeypatch.setattr(runtime, "_thread_lock", lambda *_a: nullcontext())
    monkeypatch.setattr(runtime, "out_of_scope_question", lambda *_a, **_kw: None)
    events, records, graphs = [], [], []

    async def emit(event, payload):
        events.append((event, payload))

    async def persist(**record):
        records.append(record)
        return len(records)

    original = runtime.create_deep_agent

    def create(**kwargs):
        graph = original(**kwargs)
        graphs.append(graph)
        return graph

    monkeypatch.setattr(runtime, "persist_turn", persist)
    monkeypatch.setattr(runtime, "create_deep_agent", create)
    return dict(emit=emit, checkpointer=InMemorySaver(), current_user_id=7, workspace_oid=1), events, records, graphs


def test_human_form_normalizes_mycowork_xml_shaped_arguments():
    form = HumanQuestionInput.model_validate({
        "question": "请确认范围", "options": {"item": {"label": "期中"}},
        "fields": {"item": {"item": [
            {"question": "考试", "kind": "single", "required": True, "options": {"item": [{"label": "期中"}, "期末"]}},
            {"label": "科目", "kind": "multiple", "options": ["语文", "数学"]},
            {"label": "备注", "kind": "text", "placeholder": "可选"},
        ]}},
    })
    assert form.options == ["期中"]
    assert form.fields[0].label == "考试"
    assert form.fields[0].options == ["期中", "期末"]
    assert form.fields[0].required is True
    assert form.fields[1].required is False
    assert form.fields[2].placeholder == "可选"
    assert HumanQuestionInput.model_validate({"question": "范围", "fields": {"label": "范围", "kind": "text"}}).fields[0].kind == "text"


@pytest.mark.parametrize("endpoint", [
    None, "https://api.openai.com/v1", "https://api.deepseek.com/v1",
    "https://api.moonshot.cn/v1", "https://api.minimaxi.com/v1", "https://llm.example.com/v1",
])
def test_every_gateway_can_clarify_before_planning_without_bypassing_query_gate(host, endpoint):
    from tests.chat.test_deepagents_runtime import _planning_call

    args, events, records, _ = host
    bindings = []

    class GatewayModel(ToolCallingModel):
        openai_api_base: str | None = None

        def bind_tools(self, tools, **kwargs):
            bindings.append(({t.name for t in tools}, kwargs.get("tool_choice")))
            return self

    model = GatewayModel(openai_api_base=endpoint, responses=[
        AIMessage(content="", tool_calls=[call("ask_human", "ask", question="请选择考试批次")]),
        query("premature"), _planning_call(), query("approved"), AIMessage(content="比较结果"),
    ])
    assert runtime._planning_tool_choice(model, can_clarify=False) == runtime._tool_choice_for(model, "write_todos")

    async def run():
        await runtime.run_chat_stream(request=ChatRequest(datasource_id=4, conversation_id=9,
            question="比较两次考试各科均分"), model=model, **args)
        assert bindings[0] == ({"write_todos", "ask_human"}, "auto")
        assert not any(event in {"task_plan", "result"} for event, _ in events)
        pending = records[-1]["exec_result"]["interrupt_id"]
        assert records[-1]["plans"] is None
        await runtime.run_chat_stream(request=ChatRequest(datasource_id=4, conversation_id=9,
            question="期中和期末", resume_interrupt_id=pending), model=model, **args)

    asyncio.run(run())
    assert records[-1]["is_success"]
    assert records[-1]["resolved_question"] == "比较两次考试各科均分"
    assert records[-1]["summary"] == "比较结果"
    executed = [entry.get("args", {}).get("sql") for record in records for entry in record["tool_calls"]
                if entry["tool"] == "query_semantic"]
    assert executed == ["SELECT approved FROM exam_facts"]


@pytest.mark.parametrize("companion", ["query", "plan", "plan_and_query"])
def test_clarification_takes_priority_over_every_companion_before_planning(host, companion):
    from tests.chat.test_deepagents_runtime import _planning_call

    args, events, records, _ = host
    calls = [call("ask_human", "ask", question="请选择考试")]
    if "plan" in companion:
        calls += _planning_call().tool_calls
    if "query" in companion:
        calls += query("deferred").tool_calls
    model = ToolCallingModel(responses=[AIMessage(content="", tool_calls=calls),
        _planning_call(), query("approved"), AIMessage(content="查询完成")])

    async def run():
        await runtime.run_chat_stream(request=ChatRequest(datasource_id=4, conversation_id=9,
            question="比较两次考试各科均分"), model=model, **args)
        assert any(event == "clarify" for event, _ in events), [
            (event, data) for event, data in events if event in {"error", "tool_call", "tool_result", "task_plan"}
        ]
        assert not any(event in {"task_plan", "result"} for event, _ in events)
        assert records[-1]["plans"] is None
        pending = records[-1]["exec_result"]["interrupt_id"]
        await runtime.run_chat_stream(request=ChatRequest(datasource_id=4, conversation_id=9,
            question="期中和期末", resume_interrupt_id=pending), model=model, **args)

    asyncio.run(run())
    assert records[-1]["is_success"]
    assert records[-1]["summary"] == "查询完成"
    assert all(entry.get("args", {}).get("sql") != "SELECT deferred FROM exam_facts"
               for record in records for entry in record["tool_calls"])


def test_stage_budget_transition_keeps_clarification_available(host, monkeypatch):
    args, events, records, _ = host
    settings = runtime.get_settings().model_copy(update={"chat_max_queries_per_stage": 1})
    monkeypatch.setattr(runtime, "get_settings", lambda: settings)
    bindings = []

    class GatewayModel(ToolCallingModel):
        openai_api_base: str = "https://api.openai.com/v1"

        def bind_tools(self, tools, **kwargs):
            bindings.append(({t.name for t in tools}, kwargs.get("tool_choice")))
            return self

    def plan(next_stage):
        return AIMessage(content="", tool_calls=[call("write_todos", f"plan-{next_stage}", todos=[
            {"content": "确认考试", "status": "in_progress" if next_stage == "pending" else "completed"},
            {"content": "比较人数", "status": next_stage},
        ])])

    model = GatewayModel(responses=[plan("pending"), query("candidates"),
        AIMessage(content="", tool_calls=[call("ask_human", "ask", question="请选择考试")]),
        plan("in_progress"), query("approved"), AIMessage(content="比较结果")])

    async def run():
        await runtime.run_chat_stream(request=ChatRequest(datasource_id=4, conversation_id=9,
            question="比较两次考试的人数"), model=model, **args)
        assert bindings[2] == ({"write_todos", "ask_human"}, "auto")
        pending = records[-1]["exec_result"]["interrupt_id"]
        assert any(event == "clarify" for event, _ in events)
        await runtime.run_chat_stream(request=ChatRequest(datasource_id=4, conversation_id=9,
            question="期中和期末", resume_interrupt_id=pending), model=model, **args)

    asyncio.run(run())
    assert records[-1]["is_success"]
    assert records[-1]["summary"] == "比较结果"


@pytest.mark.parametrize("reply", ["期中", {"answer": "1. 考试：期中\n2. 科目：语文；数学"}])
def test_human_tool_uses_native_interrupt_and_returns_only_the_answer(reply):
    fields = [{"label": "考试", "kind": "single", "options": ["期中", "期末"], "required": True}]
    model = ToolCallingModel(responses=[
        AIMessage(content="", tool_calls=[call("ask_human", "ask", question="请确认范围", fields=fields)]),
        AIMessage(content="已收到补充信息"),
    ])

    async def run():
        graph = runtime.create_deep_agent(model=model, tools=[runtime.ask_human], checkpointer=InMemorySaver())
        config = {"configurable": {"thread_id": "human-form-contract"}}
        result = await graph.ainvoke({"messages": [HumanMessage(content="分析考试")]}, config)
        pending = result["__interrupt__"][0]
        assert pending.value == {"kind": "human", "question": "请确认范围", "options": [],
                                 "fields": [{**fields[0], "placeholder": ""}]}
        result = await graph.ainvoke(Command(resume={pending.id: reply}), config)
        tool_reply = next(m for m in result["messages"] if isinstance(m, ToolMessage) and m.tool_call_id == "ask")
        assert tool_reply.content == (reply["answer"] if isinstance(reply, dict) else reply)
        assert "{'answer':" not in tool_reply.content

    asyncio.run(run())


def test_structured_human_form_survives_sse_persistence_and_resumption(host):
    args, events, records, graphs = host
    fields = [
        {"label": "考试", "kind": "single", "options": ["期中", "期末"], "required": True},
        {"label": "科目", "kind": "multiple", "options": ["语文", "数学"]},
        {"label": "备注", "kind": "text", "placeholder": "可选"},
    ]
    reply = "1. 考试：期中\n2. 科目：语文；数学"
    model = ToolCallingModel(responses=[
        query("lookup"), AIMessage(content="", tool_calls=[call("ask_human", "ask", question="确认范围", fields=fields)]),
        query("answer"), AIMessage(content="查询完成"),
    ])

    async def run():
        await runtime.run_chat_stream(request=ChatRequest(datasource_id=4, conversation_id=9, question="分析考试"), model=model, **args)
        form = records[-1]["exec_result"]
        assert form["question_id"] == form["interrupt_id"]
        assert form["status"] == "pending"
        assert form["fields"][1]["kind"] == "multiple"
        assert form["fields"][2]["required"] is False
        assert next(data for event, data in events if event == "clarify")["fields"] == form["fields"]
        await runtime.run_chat_stream(request=ChatRequest(datasource_id=4, conversation_id=9, question=reply,
            resume_interrupt_id=form["interrupt_id"]), model=model, **args)
        assert records[-1]["exec_result"]["human_reply_to"] == form["interrupt_id"]
        assert records[-1]["exec_result"]["human_answer"] == reply
        assert ("human.answered", {"interrupt_id": form["interrupt_id"], "answer": reply}) in events
        assert records[-1]["resolved_question"] == "分析考试"
        snapshot = await graphs[-1].aget_state({"configurable": {"thread_id": "awesome:deepagents:v1:1:7:4:9"}})
        assert not snapshot.interrupts
        assert any(isinstance(m, ToolMessage) and m.tool_call_id == "ask" and m.content == reply
                   for m in snapshot.values["messages"])
        assert records[-1]["is_success"]

    asyncio.run(run())


def test_old_interrupts_still_have_a_compatible_form_payload():
    payload = clarification_payload(SimpleNamespace(id="old-id", value={"kind": "ask_human", "question": "哪次考试？"}), "分析考试")
    assert payload["fields"] == [] and payload["options"] == []
    assert payload["prompt"] == "哪次考试？" and payload["interrupt_id"] == "old-id"


def test_multi_field_answers_can_exceed_the_single_question_limit():
    with pytest.raises(ValidationError, match="新提问最多"):
        ChatRequest(datasource_id=4, question="问" * 1001)
    assert len(ChatRequest(datasource_id=4, question="答" * 8000, resume_interrupt_id="ask").question) == 8000
    with pytest.raises(ValidationError):
        ChatRequest(datasource_id=4, question="答" * 8001, resume_interrupt_id="ask")


def test_reply_acknowledgement_does_not_depend_on_display_record_persistence(host, monkeypatch):
    args, events, records, _ = host
    model = ToolCallingModel(responses=[
        AIMessage(content="", tool_calls=[call("ask_human", "ask", question="哪次考试？")]),
        query(), AIMessage(content="查询完成"),
    ])

    async def run():
        await runtime.run_chat_stream(request=ChatRequest(datasource_id=4, conversation_id=9, question="考试人数"), model=model, **args)
        pending = records[-1]["exec_result"]["interrupt_id"]

        async def unavailable(**_kwargs):
            return 0

        monkeypatch.setattr(runtime, "persist_turn", unavailable)
        result = await runtime.run_chat_stream(request=ChatRequest(datasource_id=4, conversation_id=9, question="期中",
            resume_interrupt_id=pending), model=model, **args)
        assert result == 0
        assert ("human.answered", {"interrupt_id": pending, "answer": "期中"}) in events
        assert any(event == "step" and data.get("name") == "persist_turn" and data["status"] == "error" for event, data in events)
        events.clear()
        # A duplicate request never re-executes the already consumed tool call.
        calls_before = model.i
        result = await runtime.run_chat_stream(request=ChatRequest(datasource_id=4, conversation_id=9, question="期中",
            resume_interrupt_id=pending), model=model, **args)
        assert result == 0 and model.i == calls_before
        assert ("human.cancelled", {"interrupt_id": pending}) in events

    asyncio.run(run())


@pytest.mark.parametrize("batched", [False, True])
def test_clarification_after_lookup_and_mixed_batch_resumes(host, batched):
    args, events, records, _ = host
    ask = [call("ask_human", "ask", question="请选择考试")]
    if batched:
        ask.append(call("query_semantic", "deferred", sql="SELECT deferred FROM exam_facts"))
    model = ToolCallingModel(responses=[query("lookup"), AIMessage(content="", tool_calls=ask),
                                        query("answer"), AIMessage(content="本轮人数为 12。")])

    async def run():
        await runtime.run_chat_stream(request=ChatRequest(datasource_id=4, conversation_id=9, question="考试人数"), model=model, **args)
        pending = records[-1]["exec_result"]["interrupt_id"]
        assert any(e == "clarify" for e, _ in events)
        await runtime.run_chat_stream(request=ChatRequest(datasource_id=4, conversation_id=9, question="期中考试", resume_interrupt_id=pending), model=model, **args)

    asyncio.run(run())
    assert records[-1]["is_success"]
    assert records[-1]["resolved_question"] == "考试人数"
    assert records[-1]["summary"] == "本轮人数为 12。"
    assert not any(c.get("args", {}).get("sql") == "SELECT deferred FROM exam_facts"
                   for record in records for c in record["tool_calls"])


def test_pending_question_requires_explicit_id_and_new_question_keeps_history(host):
    args, events, records, _ = host
    model = ToolCallingModel(responses=[query(), AIMessage(content="之前已确认的结果"),
        AIMessage(content="", tool_calls=[call("ask_human", "ask", question="哪次考试？")])])
    seen = []

    class NextModel(ToolCallingModel):
        def _generate(self, messages, *a, **kw):
            seen.append(messages)
            return super()._generate(messages, *a, **kw)

    async def run():
        for question in ("人数是多少", "及格人数呢"):
            await runtime.run_chat_stream(request=ChatRequest(datasource_id=4, conversation_id=9, question=question), model=model, **args)
        result = await runtime.run_chat_stream(request=ChatRequest(datasource_id=4, conversation_id=9, question="新的一条普通问题"), model=model, **args)
        assert result == 0 and "追问卡片" in events[-1][1]["error"]
        await runtime.run_chat_stream(request=ChatRequest(datasource_id=4, conversation_id=9, question="换个问题，查总人数"),
            model=NextModel(responses=[query("new"), AIMessage(content="新问题结果")]), **args)

    asyncio.run(run())
    assert any(m.content == "之前已确认的结果" for m in seen[0])
    assert any(isinstance(m, ToolMessage) and m.tool_call_id == "ask" for m in seen[0])
    assert records[-1]["summary"] == "新问题结果"
    assert records[-1]["exec_result"]["human_cancelled_to"]
    assert any(event == "human.cancelled" for event, _ in events)


def test_truncated_output_stops_once_with_specific_error(host):
    args, events, records, _ = host
    model = ToolCallingModel(responses=[AIMessage(content="", response_metadata={"finish_reason": "length"}), AIMessage(content="不应调用")])
    asyncio.run(runtime.run_chat_stream(request=ChatRequest(datasource_id=4, question="对比学校成绩排名"), model=model, **args))
    assert model.i == 1
    assert "长度上限" in records[-1]["summary"]
    assert not records[-1]["is_success"]
    assert runtime._PLAN_FAILURE not in records[-1]["summary"]


def test_exception_rolls_back_failed_turn_without_losing_prior_history(host):
    args, _, records, graphs = host
    seen = []

    class FailingModel(ToolCallingModel):
        def _generate(self, messages, *a, **kw):
            raise RuntimeError("provider internal details must not reach user")

    class NextModel(ToolCallingModel):
        def _generate(self, messages, *a, **kw):
            seen.append(messages)
            return super()._generate(messages, *a, **kw)

    async def turn(question, model):
        await runtime.run_chat_stream(request=ChatRequest(datasource_id=4, conversation_id=9, question=question), model=model, **args)

    async def run():
        await turn("人数是多少", ToolCallingModel(responses=[query(), AIMessage(content="之前的结果")]))
        await turn("故障中的问题", FailingModel(responses=[]))
        snapshot = await graphs[-1].aget_state({"configurable": {"thread_id": "awesome:deepagents:v1:1:7:4:9"}})
        assert not snapshot.next
        assert records[-1]["is_success"] is False
        assert "provider internal" not in records[-1]["summary"]
        await turn("继续查人数", NextModel(responses=[query("next"), AIMessage(content="恢复后结果")]))

    asyncio.run(run())
    assert any(m.content == "之前的结果" for m in seen[0])
    assert not any(m.content == "故障中的问题" for m in seen[0])


def test_cancellation_clears_pending_work_and_preserves_prior_turn(host):
    args, _, _, graphs = host

    async def run():
        entered = asyncio.Event()

        class WaitingModel(ToolCallingModel):
            async def _agenerate(self, messages, *a, **kw):
                entered.set()
                await asyncio.Future()

        await runtime.run_chat_stream(request=ChatRequest(datasource_id=4, conversation_id=9, question="人数是多少"),
            model=ToolCallingModel(responses=[query(), AIMessage(content="之前的结果")]), **args)
        task = asyncio.create_task(runtime.run_chat_stream(request=ChatRequest(datasource_id=4, conversation_id=9, question="停止这轮"),
            model=WaitingModel(responses=[]), **args))
        await asyncio.wait_for(entered.wait(), 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        snapshot = await graphs[-1].aget_state({"configurable": {"thread_id": "awesome:deepagents:v1:1:7:4:9"}})
        assert not snapshot.next
        assert any(m.content == "之前的结果" for m in snapshot.values["messages"])

    asyncio.run(run())


def test_chart_waits_for_same_batch_query_instead_of_using_previous_rows(host, monkeypatch):
    args, _, records, _ = host
    charts = []

    def run_query(_self, sql, *, execute):
        return {"sql": sql, "tables": ["exam_facts"], "bundle_id": "edu", "manifest_hash": "a" * 64,
                "columns": ["label", "value"], "rows": [["current", 2 if "second" in sql else 1]]}

    async def chart(**kwargs):
        charts.append(kwargs["data"])
        return SimpleNamespace(data={"option": "{}", "chart_type": "bar"}, content="图表已生成")

    monkeypatch.setattr(semantic_query.SemanticQuery, "run", run_query)
    monkeypatch.setattr(runtime.build_chart_option_tool, "execute", chart)
    chart_call = call("chart_semantic_result", "stale-chart", x_column="label", y_columns=["value"], kind="bar")
    model = ToolCallingModel(responses=[query("first"),
        AIMessage(content="", tool_calls=[call("query_semantic", "second", sql="SELECT second FROM exam_facts"), chart_call]),
        AIMessage(content="", tool_calls=[{**chart_call, "id": "current-chart"}]),
        AIMessage(content="本轮结论")])
    asyncio.run(runtime.run_chat_stream(request=ChatRequest(datasource_id=4, question="查询人数并画图"), model=model, **args))
    assert len(charts) == 1
    assert charts[0]["metrics"][0]["values"] == [2.0]
    assert records[-1]["is_success"]


@pytest.mark.parametrize("question", ["你好", "谢谢！", "解释一下什么是均分", "什么是标准差？"])
def test_greetings_and_explicit_metric_definitions_need_no_business_query(host, question):
    args, events, records, _ = host
    asyncio.run(runtime.run_chat_stream(request=ChatRequest(datasource_id=4, question=question),
        model=ToolCallingModel(responses=[AIMessage(content="通用说明，不涉及实际业务数值。")]), **args))
    assert records[-1]["is_success"]
    assert not any(e == "error" for e, _ in events)


@pytest.mark.parametrize("question", ["为什么新华中学平均分下降", "解释一下扬州中学今年的成绩", "人数是多少"])
def test_business_questions_still_require_actual_query_evidence(host, question):
    args, _, records, _ = host
    asyncio.run(runtime.run_chat_stream(request=ChatRequest(datasource_id=4, question=question),
        model=ToolCallingModel(responses=[AIMessage(content="我猜人数为 999")]), **args))
    assert not records[-1]["is_success"]
    assert "999" not in records[-1]["summary"]


@pytest.mark.parametrize("question", ["", "  \n\t"])
def test_empty_questions_are_rejected_before_model_execution(question):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ChatRequest(datasource_id=4, question=question)


@pytest.mark.parametrize("failed", [True, False])
def test_smoke_cli_uses_current_runtime_and_does_not_count_error_summary_as_success(monkeypatch, failed):
    from src.agent import smoke

    async def run_chat_stream(*, max_model_calls, emit, **_kwargs):
        assert max_model_calls == 3
        if failed:
            await emit("error", {"error": "本轮失败"})
        await emit("summary", {"content": "摘要"})

    monkeypatch.setattr(runtime, "run_chat_stream", run_chat_stream)
    result = asyncio.run(smoke._run("测试问题", 4, 7, 3, False))
    assert result == (1 if failed else 0)


@pytest.mark.parametrize("repaired", [True, False])
def test_query_failure_is_not_silently_counted_as_complete(host, monkeypatch, repaired):
    args, _, records, _ = host

    def run_query(_self, sql, *, execute):
        if "bad" in sql:
            raise semantic_query.SemanticQueryError("查询失败")
        return {"sql": sql, "tables": [], "bundle_id": "edu", "manifest_hash": "a" * 64,
                "columns": ["value"], "rows": [[12]]}

    monkeypatch.setattr(semantic_query.SemanticQuery, "run", run_query)
    responses = [query("bad"), query("fixed")] if repaired else [query("good"), query("bad")]
    responses.append(AIMessage(content="本轮结论"))
    asyncio.run(runtime.run_chat_stream(request=ChatRequest(datasource_id=4, question="人数是多少"),
        model=ToolCallingModel(responses=responses), **args))
    assert records[-1]["is_success"] is repaired
    assert ("部分查询未完成" in records[-1]["summary"]) is (not repaired)


@pytest.mark.parametrize("sql", [
    "DELETE FROM t", "WITH removed AS (DELETE FROM t RETURNING *) SELECT * FROM removed",
    "SELECT * INTO backup FROM t", "SELECT 1; DROP TABLE t", "SELECT * FROM t FOR UPDATE", "SELECT (",
])
def test_readonly_sql_validator_rejects_writes_and_parse_errors(sql):
    from datasource.db.db import check_sql_read

    assert not check_sql_read(sql, "pg")


@pytest.mark.parametrize("dialect", ["pg", "mysql"])
def test_semantic_queries_enforce_readonly_and_database_execution_timeout(monkeypatch, dialect):
    from datasource.db import db as driver

    calls, sessions = [], []
    cursor = SimpleNamespace(
        execute=lambda *a: calls.append(a), description=[("value",)],
        fetchall=lambda: [(1,)], close=lambda: None,
    )
    conn = SimpleNamespace(cursor=lambda: cursor, close=lambda: None,
                           set_session=lambda **kw: sessions.append(kw))
    if dialect == "pg":
        import psycopg2
        monkeypatch.setattr(psycopg2, "connect", lambda **_kw: conn)
    else:
        import pymysql

        def connect(**kwargs):
            assert kwargs["read_timeout"] == 7
            return conn

        monkeypatch.setattr(pymysql, "connect", connect)
    success, _, result = driver.execute_sql(dialect, {
        "read_only": True, "query_timeout_seconds": 7,
    }, "SELECT 1")
    assert success and result["rows"] == [[1]]
    assert calls[-1] == ("SELECT 1",)
    if dialect == "pg":
        assert sessions == [{"readonly": True}]
        assert calls[0] == ("SET LOCAL statement_timeout = %s", (7000,))
    else:
        assert calls[:2] == [("SET TRANSACTION READ ONLY",), ("START TRANSACTION",)]
        assert calls[2] == ("SET SESSION MAX_EXECUTION_TIME = %s", (7000,))


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_database_values_cannot_break_sse_json(value):
    import json
    from decimal import Decimal

    from datasource.db.db import convert_value

    converted = convert_value([value, {"decimal": Decimal("NaN"), "valid": Decimal("1.25")}])
    assert converted == [None, {"decimal": None, "valid": 1.25}]
    json.dumps(converted, allow_nan=False)


def test_multiple_clarifications_produce_only_one_resumable_interrupt(host):
    args, _, records, _ = host
    model = ToolCallingModel(responses=[AIMessage(content="", tool_calls=[
        call("ask_human", "first", question="请选择考试"),
        call("ask_human", "second", question="请选择学校"),
    ]), query(), AIMessage(content="本轮结果")])

    async def run():
        await runtime.run_chat_stream(request=ChatRequest(datasource_id=4, conversation_id=9, question="人数是多少"), model=model, **args)
        pending = records[-1]["exec_result"]
        assert pending["prompt"] == "请选择考试"
        await runtime.run_chat_stream(request=ChatRequest(datasource_id=4, conversation_id=9, question="期中考试",
            resume_interrupt_id=pending["interrupt_id"]), model=model, **args)

    asyncio.run(run())
    assert records[-1]["is_success"]


@pytest.mark.parametrize("completes", [True, False])
def test_remaining_pending_stages_cannot_be_silently_skipped(host, completes):
    args, events, records, _ = host

    def plan(second_status):
        return AIMessage(content="", tool_calls=[call("write_todos", f"plan-{second_status}", todos=[
            {"content": "查询人数", "status": "in_progress" if second_status == "pending" else "completed"},
            {"content": "查询达线人数", "status": second_status},
        ])])

    responses = [plan("pending"), query("first"), AIMessage(content="提前结束的草稿")]
    if completes:
        responses += [plan("in_progress"), query("second"), plan("completed"), AIMessage(content="完整分析结论")]
    else:
        responses += [AIMessage(content="再次提前结束"), AIMessage(content="仍未执行后续阶段")]
    asyncio.run(runtime.run_chat_stream(request=ChatRequest(datasource_id=4, question="对比考试人数及达线人数"),
        model=ToolCallingModel(responses=responses), **args))
    assert records[-1]["is_success"] is completes
    assert "提前结束" not in records[-1]["summary"]
    if completes:
        assert records[-1]["summary"] == "完整分析结论"
    else:
        assert "未执行阶段" in records[-1]["summary"]


@pytest.mark.parametrize("provider", ["openai", "ollama"])
def test_provider_wrappers_preserve_assistant_and_tool_roles(provider):
    from src.llm.ollama import OllamaLLM
    from src.llm.openai import OpenAILLM

    wrapper = OpenAILLM(api_key="test") if provider == "openai" else OllamaLLM()
    seen = []
    wrapper._llm = SimpleNamespace(invoke=lambda messages, **_kw: seen.extend(messages) or AIMessage(content="done"))
    wrapper.chat([
        {"role": "user", "content": "question"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "q", "type": "function", "function": {"name": "query", "arguments": "{}"}}]},
        {"role": "tool", "content": "result", "tool_call_id": "q"},
    ])
    assert [m.type for m in seen] == ["human", "ai", "tool"]
    assert seen[-1].tool_call_id == "q"


@pytest.mark.parametrize("as_dict", [True, False])
@pytest.mark.parametrize("provider", ["openai", "ollama"])
def test_structured_prompt_is_applied_without_mutating_history(provider, as_dict):
    from src.llm.ollama import OllamaLLM
    from src.llm.openai import OpenAILLM

    wrapper = OpenAILLM(api_key="test") if provider == "openai" else OllamaLLM()
    seen = []
    wrapper._llm = SimpleNamespace(invoke=lambda messages, **_kw: seen.extend(messages) or AIMessage(content='{"ok":true}'))
    last = {"role": "user", "content": "question"} if as_dict else HumanMessage(content="question")
    messages = [last]
    assert wrapper.chat_with_structured_output(messages, {"type": "json_object", "schema": {}}) == {"ok": True}
    assert "JSON" in seen[-1].content
    assert (last["content"] if as_dict else last.content) == "question"


def test_parse_history_does_not_add_phantom_empty_turns():
    from src.llm.base import parse_history

    assert [m.content for m in parse_history([{"role": "assistant", "content": "answer"}])] == ["answer"]


@pytest.mark.parametrize("base_url", ["http://ollama:11434", "http://ollama:11434/v1/"])
def test_ollama_does_not_duplicate_api_version(base_url):
    from src.llm.ollama import OllamaLLM

    assert OllamaLLM(base_url=base_url)._llm.openai_api_base == "http://ollama:11434/v1"


def test_metadata_outage_does_not_consume_a_pending_clarification(host, monkeypatch):
    args, events, records, _ = host
    model = ToolCallingModel(responses=[AIMessage(content="", tool_calls=[call("ask_human", "ask", question="哪次考试？")]),
                                        query(), AIMessage(content="已恢复查询")])
    context = semantic_query.SemanticQuery.context

    def unavailable(_self):
        raise semantic_query.SemanticQueryError("语义目录暂时不可用")

    async def run():
        await runtime.run_chat_stream(request=ChatRequest(datasource_id=4, conversation_id=9, question="人数是多少"), model=model, **args)
        pending = records[-1]["exec_result"]["interrupt_id"]
        reply = ChatRequest(datasource_id=4, conversation_id=9, question="期中考试", resume_interrupt_id=pending)
        monkeypatch.setattr(semantic_query.SemanticQuery, "context", unavailable)
        await runtime.run_chat_stream(request=reply, model=model, **args)
        assert events[-1][1]["preserve_interrupt"] is True
        monkeypatch.setattr(semantic_query.SemanticQuery, "context", context)
        await runtime.run_chat_stream(request=reply, model=model, **args)

    asyncio.run(run())
    assert records[-1]["is_success"]
    assert records[-1]["summary"] == "已恢复查询"


def test_temporary_repair_instructions_do_not_leak_into_the_next_question(host):
    from langchain_core.messages import SystemMessage

    from tests.chat.test_deepagents_runtime import _planning_call

    args, _, _, _ = host
    seen = []

    class NextModel(ToolCallingModel):
        def _generate(self, messages, *a, **kw):
            seen.append(messages)
            return super()._generate(messages, *a, **kw)

    async def run():
        await runtime.run_chat_stream(request=ChatRequest(datasource_id=4, conversation_id=9, question="对比两次考试人数"),
            model=ToolCallingModel(responses=[query("premature"), _planning_call(), query("ok"), AIMessage(content="已完成首轮")]), **args)
        await runtime.run_chat_stream(request=ChatRequest(datasource_id=4, conversation_id=9, question="及格人数呢"),
            model=NextModel(responses=[query("next"), AIMessage(content="已完成第二轮")]), **args)

    asyncio.run(run())
    assert any(m.content == "已完成首轮" for m in seen[0])
    assert not any(isinstance(m, SystemMessage) and m.additional_kwargs.get("chat_runtime_instruction") for m in seen[0])


@pytest.mark.parametrize("endpoint,preserved", [
    ("https://api.minimaxi.com/v1", True), ("https://api.deepseek.com/v1", True),
    ("https://api.openai.com/v1", False), ("https://api.minimax.io.example.test/v1", False),
])
def test_reasoning_survives_provider_tool_roundtrip_only_for_supported_hosts(endpoint, preserved):
    model = ReasoningChatOpenAI(model="test", api_key="test", base_url=endpoint)
    raw = {"choices": [{"message": {"role": "assistant", "content": "", "reasoning_content": "hidden reasoning",
        "tool_calls": [{"id": "q", "type": "function", "function": {"name": "query", "arguments": "{}"}}]}, "finish_reason": "tool_calls"}]}
    message = model._create_chat_result(raw).generations[0].message
    assert message.additional_kwargs["reasoning_content"] == "hidden reasoning"
    wire = model._get_request_payload([HumanMessage(content="question"), message, ToolMessage(content="result", tool_call_id="q")])
    assert (wire["messages"][1].get("reasoning_content") == "hidden reasoning") is preserved
