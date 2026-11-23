"""Exercise the real mandatory reviewer and native clarification graph together."""

import asyncio
import json
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage
from pydantic import ValidationError

from src.chat.schemas import ChatRequest
from src.chat.service import deepagents_runtime as runtime
from src.chat.service import semantic_query
from src.chat.service.question_assessment import (
    QuestionAssessment,
    QuestionAssessmentError,
    review_question,
    validate_context_lookup,
)
from tests.chat.test_deepagents_runtime import ToolCallingModel, _planning_call
from tests.chat.test_runtime_resilience import call, host, query  # noqa: F401


def assessment(decision="ready", question="请确认分析范围", fields=None):
    args = {"decision": decision, "reason": f"评估结论：{decision}。"}
    if decision == "clarify":
        args["clarification"] = {"question": question, "fields": fields or [
            {"label": "分析范围", "kind": "text", "required": True},
        ]}
    return AIMessage(content="", tool_calls=[call("submit_question_assessment", f"review-{decision}", **args)])


@pytest.fixture
def reviewed(host, monkeypatch):  # noqa: F811
    monkeypatch.setattr(runtime, "review_question", review_question)
    return host


def request(question="分析达线率", **kwargs):
    return ChatRequest(datasource_id=4, conversation_id=9, question=question, **kwargs)


@pytest.mark.parametrize("question, label", [
    ("宝应地区本科达线率", "考试"), ("订单总额", "时间范围"),
    ("这位学生的成绩", "学生"), ("增长率是多少", "比较基准"),
    ("市区的均分", "地区范围"),
])
def test_every_simple_business_question_is_reviewed_and_can_interrupt(reviewed, question, label):
    args, events, records, _ = reviewed
    model = ToolCallingModel(responses=[assessment("clarify", fields=[
        {"label": label, "kind": "text", "required": True},
    ]), assessment(), query(), AIMessage(content="已按确认范围查询。")])

    async def scenario():
        await runtime.run_chat_stream(request=request(question), model=model, **args)
        assert any(e == "step" and p.get("label") == "评估查询条件" for e, p in events)
        assert not any(e in {"sql", "result", "task_plan"} for e, _ in events)
        pending = records[-1]["exec_result"]
        assert pending["fields"][0]["label"] == label
        assert [c["tool"] for c in records[-1]["tool_calls"]] == ["ask_human"]
        assert pending["question_assessment"]["decision"] == "clarify"
        await runtime.run_chat_stream(request=request("1. 已确认范围", resume_interrupt_id=pending["interrupt_id"]), model=model, **args)

    asyncio.run(scenario())
    assert records[-1]["resolved_question"] == question
    assert records[-1]["summary"] == "已按确认范围查询。"
    assert records[-1]["is_success"]
    assert records[-1]["exec_result"]["question_assessment"]["decision"] == "ready"


def test_ready_review_does_not_force_a_question_or_plan(reviewed):
    args, events, records, _ = reviewed
    model = ToolCallingModel(responses=[assessment(), query(), AIMessage(content="本次人数12。")])
    asyncio.run(runtime.run_chat_stream(request=request("期末考试总人数"), model=model, **args))
    assert records[-1]["is_success"]
    assert not any(e in {"clarify", "task_plan"} for e, _ in events)
    assert [c["tool"] for c in records[-1]["tool_calls"]] == ["query_semantic"]


@pytest.mark.parametrize("tool_name", ["describe_semantic_model", "query_semantic"])
def test_new_metadata_or_candidate_query_reopens_review_before_further_actions(reviewed, monkeypatch, tool_name):
    args, events, records, _ = reviewed
    monkeypatch.setattr(runtime.SemanticQuery, "describe_many", lambda *_a: {"columns": ["exam_id", "exam_name"]})
    candidate_args = {"names": ["exam_facts"]} if tool_name == "describe_semantic_model" else {
        "sql": "SELECT DISTINCT exam_id, exam_name FROM exam_facts",
    }
    model = ToolCallingModel(responses=[assessment(), AIMessage(content="", tool_calls=[call(tool_name, "discover", **candidate_args)]), assessment("clarify")])
    asyncio.run(runtime.run_chat_stream(request=request(), model=model, **args))
    assert records[-1]["exec_result"]["clarify"]
    assert records[-1]["exec_result"]["question_assessment"]["revision"] == 2
    assert any(e == "clarify" for e, _ in events)


def test_lookup_phase_rejects_hallucinated_business_tool_calls(reviewed):
    args, events, records, _ = reviewed
    bindings = []

    class RecordingModel(ToolCallingModel):
        def bind_tools(self, tools, **kwargs):
            bindings.append({t.name for t in tools})
            return self

    model = RecordingModel(responses=[assessment("lookup"), query("unapproved"), assessment("clarify")])
    asyncio.run(runtime.run_chat_stream(request=request(), model=model, **args))
    assert "lookup_question_context" in bindings[1]
    assert not {"query_semantic", "write_todos", "chart_semantic_result"} & bindings[1]
    assert not any(e in {"sql", "result", "task_plan"} for e, _ in events)
    assert records[-1]["exec_result"]["clarify"]


def test_review_tokens_are_counted_without_overwriting_main_context_or_answer(reviewed):
    args, events, records, _ = reviewed

    def tokens(message, amount):
        return message.model_copy(update={"usage_metadata": {"input_tokens": amount, "output_tokens": 1, "total_tokens": amount + 1}})

    model = ToolCallingModel(responses=[tokens(assessment(), 100), tokens(query(), 200), tokens(AIMessage(content="业务结论"), 30)])
    asyncio.run(runtime.run_chat_stream(request=request(), model=model, **args))
    usage = [data for event, data in events if event == "usage"][-1]
    assert usage["total_tokens"] == 333
    assert usage["context_tokens"] == 30
    assert records[-1]["summary"] == "业务结论"
    assert not any("submit_question_assessment" in str(data) for event, data in events
                   if event in {"answer_delta", "reasoning_delta", "summary"})


def test_parallel_lookups_share_one_query_limit(reviewed, monkeypatch):
    args, _, records, _ = reviewed
    executed = []

    def run(_self, sql, **_kwargs):
        executed.append(sql)
        return {"sql": sql, "tables": [], "columns": ["exam_id"], "rows": [[1]]}

    monkeypatch.setattr(runtime.SemanticQuery, "run", run)
    calls = [call("lookup_question_context", f"lookup-{i}", sql=f"SELECT DISTINCT exam_id FROM exam_facts WHERE year = {2020 + i}") for i in range(6)]
    model = ToolCallingModel(responses=[assessment("lookup"), AIMessage(content="", tool_calls=calls), assessment("clarify")])
    asyncio.run(runtime.run_chat_stream(request=request(), model=model, **args))
    assert len(executed) == 4
    assert sum(c["success"] is False for c in records[-1]["tool_calls"] if c["tool"] == "lookup_question_context") == 2


def test_discovery_then_review_interrupts_before_analysis_and_rechecks_answer(reviewed, monkeypatch):
    args, events, records, _ = reviewed
    executed, reviews = [], []
    monkeypatch.setattr(runtime.SemanticQuery, "describe_many", lambda *_a: {"columns": ["exam_id", "exam_name"]})

    def run(_self, sql, *, execute):
        executed.append(sql)
        return {"sql": sql, "tables": ["exam_facts"], "bundle_id": "edu", "manifest_hash": "a" * 64,
                "columns": ["exam_id", "exam_name"] if "DISTINCT" in sql else ["value"],
                "rows": [[1, "期中"], [2, "期末"]] if "DISTINCT" in sql else [[12]]}

    monkeypatch.setattr(runtime.SemanticQuery, "run", run)

    class RecordingModel(ToolCallingModel):
        def _generate(self, messages, *a, **kw):
            if "独立条件评估器" in str(messages[0].content):
                reviews.append(json.loads(messages[-1].content))
            return super()._generate(messages, *a, **kw)

    model = RecordingModel(responses=[
        assessment("lookup"), AIMessage(content="", tool_calls=[call("describe_semantic_model", "describe", names=["exam_facts"])]),
        assessment("lookup"), AIMessage(content="", tool_calls=[call("lookup_question_context", "lookup", sql="SELECT DISTINCT exam_id, exam_name FROM exam_facts")]),
        assessment("clarify", fields=[{"label": "考试", "kind": "single", "required": True, "options": ["期中", "期末"]}]),
        assessment(), query(), AIMessage(content="期末考试人数12。"),
    ])

    async def scenario():
        await runtime.run_chat_stream(request=request(), model=model, **args)
        assert executed == ["SELECT DISTINCT exam_id, exam_name FROM exam_facts"]
        assert not any(e == "result" for e, _ in events)
        pending = records[-1]["exec_result"]
        assert pending["fields"][0]["options"] == ["期中", "期末"]
        assert "期末" in json.dumps(reviews[-1], ensure_ascii=False)
        await runtime.run_chat_stream(request=request("1. 考试：期末", resume_interrupt_id=pending["interrupt_id"]), model=model, **args)

    asyncio.run(scenario())
    assert records[-1]["is_success"]
    assert len(reviews) == 4
    assert "1. 考试：期末" in json.dumps(reviews[-1], ensure_ascii=False)
    assert records[-1]["summary"] == "期末考试人数12。"
    assert len(executed) == 2


@pytest.mark.parametrize("status", ["configured", "missing", "unavailable"])
def test_prediction_lines_follow_a_verified_batch_and_match_both_keys(monkeypatch, status):
    semantic = semantic_query.SemanticQuery(SimpleNamespace(
        database_edu_url="postgresql://localhost/exam", chat_query_timeout_seconds=10,
    ), 1, 1, 7)
    authorization = SimpleNamespace(fingerprint="current", manifest=SimpleNamespace(physical_schema="public"))
    monkeypatch.setattr(semantic, "_fresh_authorization", lambda: authorization)

    def read(db_type, config, sql):
        assert db_type == "pg" and config["read_only"] is True
        assert "exam_batch_id = 12" in sql and "exam_name = '2027届高三11月期中'" in sql
        if status == "unavailable":
            return False, "connection refused", None
        return True, "", {
            "columns": ["exam_name", "wl_score_bk", "ls_score_bk"],
            "rows": [["2027届高三11月期中", 419, 419]] if status == "configured" else [],
        }

    monkeypatch.setattr(semantic_query, "execute_sql", read)
    candidate = {
        "sql": "SELECT DISTINCT exam_batch_id, exam_name FROM tb_score_overview",
        "columns": ["exam_batch_id", "exam_name"], "rows": [[12, "2027届高三11月期中"]],
        "tables": ["tb_score_overview"], "permission_mode": "model_explicit_v2",
        "authorization_fingerprint": "current",
    }
    assert semantic.prediction_line_context({**candidate, "rows_truncated": True}) is None
    assert semantic.prediction_line_context({**candidate, "rows": [[12, "期中"], [13, "期末"]]}) is None
    assert semantic.prediction_line_context({
        **candidate, "sql": "SELECT DISTINCT jc AS exam_batch_id, exam_name FROM tb_score_overview",
    }) is None
    with pytest.raises(semantic_query.SemanticAuthorizationError):
        semantic.prediction_line_context({**candidate, "authorization_fingerprint": "stale"})

    result = semantic.prediction_line_context(candidate)
    assert result["status"] == status
    assert {(line["track"], line["threshold"]) for line in result["lines"]} == (
        {("物理类", 419), ("历史类", 419)} if status == "configured" else set()
    )


def test_prediction_lines_reach_reviewer_before_the_clarification_form(reviewed, monkeypatch):
    args, events, records, _ = reviewed
    exam = "2027届高三11月期中"
    monkeypatch.setattr(runtime.SemanticQuery, "authorization_context", lambda _self: {})
    monkeypatch.setattr(runtime.SemanticQuery, "context", lambda _self: {
        "models": [{"name": "tb_score_overview"}], "dialect": "postgres",
    })
    candidate = {"columns": ["exam_batch_id", "exam_name"], "rows": [[12, exam]]}
    monkeypatch.setattr(runtime.SemanticQuery, "run", lambda *_a, **_kw: candidate)

    def load_lines(_self, result):
        assert result is candidate
        return {"exam_batch_id": 12, "status": "configured", "lines": [
            {"line_name": "本科线", "track": "物理类", "threshold": 419},
            {"line_name": "本科线", "track": "历史类", "threshold": 419},
        ]}

    monkeypatch.setattr(runtime.SemanticQuery, "prediction_line_context", load_lines)

    class RecordingModel(ToolCallingModel):
        def _generate(self, messages, *a, **kw):
            if "独立条件评估器" in str(messages[0].content):
                payload = json.loads(messages[-1].content)
                assert payload["published_directory"]["exam_prediction_lines_available"] is True
                for item in payload["conversation"]:
                    if item["name"] == "lookup_question_context" and item["role"] == "tool":
                        assert json.loads(item["content"])["prediction_lines"]["lines"][0]["threshold"] == 419
                        return super()._generate(messages, *a, **kw)
                assert len(self.responses) == 3
            return super()._generate(messages, *a, **kw)

    model = RecordingModel(responses=[
        assessment("lookup"), AIMessage(content="", tool_calls=[call(
            "lookup_question_context", "batch", sql="SELECT DISTINCT exam_batch_id, exam_name FROM tb_score_overview",
        )]),
        assessment("clarify", question="请确认自定义对照基准", fields=[
            {"label": "对照基准", "kind": "text", "required": True},
        ]),
    ])
    asyncio.run(runtime.run_chat_stream(request=request(f"分析{exam}本科线达线率，并使用自定义对照基准"), model=model, **args))
    assert any(event == "clarify" for event, _ in events)
    assert [field["label"] for field in records[-1]["exec_result"]["fields"]] == ["对照基准"]
    assert records[-1]["tool_calls"][0]["data"]["prediction_lines"]["status"] == "configured"


@pytest.mark.parametrize("response", [
    AIMessage(content="条件大概够了，可以直接查"),
    AIMessage(content="", tool_calls=[call("query_semantic", "bypass", sql="SELECT value FROM exam_facts")]),
    AIMessage(content="", tool_calls=[call("submit_question_assessment", "invalid", decision="clarify", reason="缺条件")]),
    AIMessage(content="", response_metadata={"finish_reason": "length"}),
])
def test_invalid_review_fails_closed_without_business_actions(reviewed, response):
    args, events, records, _ = reviewed
    model = ToolCallingModel(responses=[response, query(), AIMessage(content="不能执行")])
    asyncio.run(runtime.run_chat_stream(request=request(), model=model, **args))
    assert not records[-1]["is_success"]
    assert "查询条件评估" in records[-1]["summary"]
    assert not any(e in {"sql", "result", "clarify", "task_plan"} for e, _ in events)
    assert model.i == (1 if response.response_metadata.get("finish_reason") == "length" else 2)


def test_evaluation_and_analysis_share_the_same_call_budget(reviewed):
    args, events, records, _ = reviewed
    model = ToolCallingModel(responses=[assessment(), query(), AIMessage(content="不能执行")])
    asyncio.run(runtime.run_chat_stream(request=request(), model=model, max_model_calls=1, **args))
    assert model.i == 1
    assert not records[-1]["is_success"]
    assert "调用上限" in records[-1]["summary"]
    assert not any(e == "result" for e, _ in events)


def test_assessment_hooks_do_not_consume_the_model_call_budget_as_graph_steps(reviewed, monkeypatch):
    args, _, records, _ = reviewed
    settings = runtime.get_settings().model_copy(update={"chat_max_queries_per_stage": None})
    monkeypatch.setattr(runtime, "get_settings", lambda: settings)
    model = ToolCallingModel(responses=[assessment(), *[query(f"query_{i}") for i in range(25)]])
    asyncio.run(runtime.run_chat_stream(request=request(), model=model, max_model_calls=20, **args))
    assert model.i == 20
    assert "模型调用上限" in records[-1]["summary"]
    assert "执行步数上限" not in records[-1]["summary"]


def test_new_turn_is_reviewed_again_with_full_history(reviewed):
    args, events, records, _ = reviewed
    model = ToolCallingModel(responses=[assessment(), query(), AIMessage(content="期末人数12。"), assessment("clarify")])

    async def scenario():
        await runtime.run_chat_stream(request=request("期末人数"), model=model, **args)
        await runtime.run_chat_stream(request=request("另一个班的呢"), model=model, **args)

    asyncio.run(scenario())
    assert len(records) == 2 and records[-1]["exec_result"]["clarify"]
    assert sum(c["tool"] == "query_semantic" for r in records for c in r["tool_calls"]) == 1


def test_incomplete_human_reply_is_reviewed_before_asking_only_remaining_fields(reviewed):
    args, _, records, _ = reviewed
    model = ToolCallingModel(responses=[assessment("clarify", question="请提供考试与班级"),
        assessment("clarify", question="请确认班级", fields=[{"label": "班级", "kind": "text", "required": True}])])

    async def scenario():
        await runtime.run_chat_stream(request=request(), model=model, **args)
        first = records[-1]["exec_result"]["interrupt_id"]
        await runtime.run_chat_stream(request=request("期末考试", resume_interrupt_id=first), model=model, **args)
        second = records[-1]["exec_result"]
        assert second["interrupt_id"] != first
        assert second["fields"][0]["label"] == "班级"
        assert records[-1]["resolved_question"] == "分析达线率"
        assert records[-1]["exec_result"]["human_reply_to"] == first

    asyncio.run(scenario())
    assert not any(c["tool"] == "query_semantic" for r in records for c in r["tool_calls"])


@pytest.mark.parametrize("execute", [True, False])
def test_noninteractive_paths_return_clarification_without_executing(reviewed, execute):
    args, events, _, _ = reviewed
    model = ToolCallingModel(responses=[assessment("clarify")])
    asyncio.run(runtime.run_chat_stream(request=ChatRequest(datasource_id=4, question="本次达线率"), model=model, execute=execute, **args))
    assert any(e == "clarify" and p["prompt"] == "请确认分析范围" for e, p in events)
    assert not any(e in {"sql", "result"} for e, _ in events)


def test_generate_sql_review_does_not_execute_candidate_queries(reviewed, monkeypatch):
    args, events, _, _ = reviewed
    monkeypatch.setattr(runtime.SemanticQuery, "run", lambda *_a, **_kw: pytest.fail("SQL generation executed a query"))
    model = ToolCallingModel(responses=[assessment("lookup"), AIMessage(content="", tool_calls=[
        call("lookup_question_context", "lookup", sql="SELECT DISTINCT exam_id FROM exam_facts"),
    ]), assessment("clarify")])
    asyncio.run(runtime.run_chat_stream(request=ChatRequest(datasource_id=4, question="本次达线率"), model=model, execute=False, **args))
    assert any(e == "clarify" for e, _ in events)


@pytest.mark.parametrize("question", ["你好", "解释一下什么是均分"])
def test_greetings_and_generic_definitions_need_no_model_review_or_question(reviewed, question):
    args, events, records, _ = reviewed
    model = ToolCallingModel(responses=[AIMessage(content="通用说明")])
    asyncio.run(runtime.run_chat_stream(request=request(question), model=model, **args))
    assert records[-1]["is_success"]
    assert records[-1]["exec_result"]["question_assessment"]["skipped"] is True
    assert not any(e == "clarify" for e, _ in events)


def test_complex_path_uses_same_review_before_planning(reviewed):
    args, events, records, _ = reviewed
    model = ToolCallingModel(responses=[assessment("clarify"), assessment(), _planning_call(), query(), AIMessage(content="比较完成")])

    async def scenario():
        await runtime.run_chat_stream(request=request("比较两次考试各科均分"), model=model, **args)
        assert not any(e == "task_plan" for e, _ in events)
        pending = records[-1]["exec_result"]["interrupt_id"]
        await runtime.run_chat_stream(request=request("期中和期末", resume_interrupt_id=pending), model=model, **args)

    asyncio.run(scenario())
    assert records[-1]["is_success"]
    assert any(e == "task_plan" for e, _ in events)


@pytest.mark.parametrize("sql", [
    "SELECT SUM(value) FROM exam_facts", "SELECT DISTINCT AVG(value) FROM exam_facts",
    "SELECT * FROM exam_facts", "SELECT DISTINCT * FROM exam_facts",
    "SELECT DISTINCT f.* FROM exam_facts f",
    "SELECT DISTINCT exam_id FROM exam_facts LIMIT 1",
    "DELETE FROM exam_facts", "SELECT DISTINCT id FROM exam_facts; DELETE FROM exam_facts",
])
def test_candidate_lookup_cannot_be_used_as_an_analysis_bypass(sql):
    with pytest.raises(QuestionAssessmentError):
        validate_context_lookup(sql, "postgres")


def test_candidate_lookup_accepts_ids_and_labels():
    validate_context_lookup("SELECT DISTINCT exam_id, exam_name FROM exam_facts WHERE year = 2026 ORDER BY exam_id", "postgres")


def test_assessment_schema_and_json_gateway_fallback(reviewed):
    with pytest.raises(ValidationError):
        QuestionAssessment(decision="clarify", reason="缺条件")
    args, _, records, _ = reviewed
    model = ToolCallingModel(responses=[AIMessage(content='{"decision":"ready","reason":"用户条件完整"}'), query(), AIMessage(content="完成")])
    asyncio.run(runtime.run_chat_stream(request=request("明确考试人数"), model=model, **args))
    assert records[-1]["is_success"]


def test_one_bounded_format_repair_can_recover_without_releasing_queries_early(reviewed):
    args, events, records, _ = reviewed
    model = ToolCallingModel(responses=[AIMessage(content="普通正文不是结构化评估"), assessment(), query(), AIMessage(content="完成")])
    asyncio.run(runtime.run_chat_stream(request=request(), model=model, **args))
    assert records[-1]["is_success"]
    assert [c["tool"] for c in records[-1]["tool_calls"]] == ["query_semantic"]
    assert not any(e == "error" for e, _ in events)



@pytest.fixture
def semantic(monkeypatch):
    query = semantic_query.SemanticQuery(SimpleNamespace(
        database_edu_url="postgresql://localhost/exam", chat_query_timeout_seconds=10,
    ), 1, 1, 7)
    binding = {"bundle_id": "edu", "manifest_hash": "release"}
    relation = SimpleNamespace(columns={"zf6m": "zf6m"}, forbidden_columns=())
    manifest = SimpleNamespace(
        relations={"tb_score_overview": relation}, physical_schema="public",
        connection=SimpleNamespace(model_dump=lambda: {"database": "exam"}),
    )
    authorization = SimpleNamespace(manifest=manifest, grants=(), fingerprint="active")
    monkeypatch.setattr(query, "_binding", lambda: binding)
    monkeypatch.setattr(query, "_permission_manifest", lambda: manifest)
    monkeypatch.setattr(query, "_fresh_authorization", lambda: authorization)
    monkeypatch.setattr(query, "_call", lambda *_a, **_kw: {
        **binding, "dialect": "postgres",
        "models": [{"name": "tb_score_overview", "columns": [{"name": "zf6m"}]}],
        "views": [
            {"name": "v_exam_cutoff_lines", "columns": [{"name": "threshold"}]},
            {"name": "v_cutoff_subject_contribution", "columns": [{"name": "threshold"}]},
        ],
    })
    monkeypatch.setattr(semantic_query, "get_db_session", lambda: nullcontext(object()))
    monkeypatch.setattr(semantic_query, "get_user_by_id", lambda *_: SimpleNamespace(status=1))
    monkeypatch.setattr(semantic_query, "assert_datasource_accessible", lambda *_: None)
    return query


def test_public_cutoff_readable_without_grants_but_scores_are_scoped(semantic, monkeypatch):
    assert [view["name"] for view in semantic.context()["views"]] == ["v_exam_cutoff_lines"]
    semantic.describe("v_exam_cutoff_lines")
    monkeypatch.setattr(semantic, "_plan", lambda *_: {
        "physical_sql": 'SELECT wl_score_bk AS threshold FROM "public".tb_fraction_bar',
    })

    def read(db_type, config, sql):
        assert db_type == "pg" and config["read_only"]
        assert 'FROM "public".tb_fraction_bar' in sql
        return True, "", {"columns": ["threshold"], "rows": [[419]]}

    monkeypatch.setattr(semantic_query, "execute_sql", read)
    assert semantic.run("SELECT threshold FROM v_exam_cutoff_lines")["rows"] == [[419]]
    with pytest.raises(semantic_query.SemanticAuthorizationError, match="有效的教育授权"):
        semantic.run("SELECT zf6m FROM tb_score_overview")
    with pytest.raises(semantic_query.SemanticAuthorizationError, match="有效的教育授权"):
        semantic.run("SELECT b.threshold, s.zf6m FROM v_exam_cutoff_lines b CROSS JOIN tb_score_overview s")


def test_public_cutoff_plan_cannot_read_another_table(semantic, monkeypatch):
    semantic.describe("v_exam_cutoff_lines")
    monkeypatch.setattr(semantic, "_plan", lambda *_: {
        "physical_sql": 'SELECT zf6m AS threshold FROM "public".tb_score_overview',
    })
    monkeypatch.setattr(semantic_query, "execute_sql", lambda *_: pytest.fail("unexpected SQL execution"))
    with pytest.raises(semantic_query.SemanticQueryError):
        semantic.run("SELECT threshold FROM v_exam_cutoff_lines")


def test_public_union_case_order_is_fixed_before_planning(semantic, monkeypatch):
    semantic.describe("v_exam_cutoff_lines")
    plans = []

    def plan(sql, *_):
        plans.append(sql)
        return {"physical_sql": 'SELECT wl_score_bk AS threshold FROM "public".tb_fraction_bar'}

    monkeypatch.setattr(semantic, "_plan", plan)
    monkeypatch.setattr(semantic_query, "execute_sql", lambda *_: (
        True, "", {"columns": ["对照层级", "threshold"], "rows": [["本班", 419], ["全市", 419]]},
    ))
    combined = (
        "SELECT '本班' AS 对照层级, threshold FROM v_exam_cutoff_lines "
        "UNION ALL SELECT '全市', threshold FROM v_exam_cutoff_lines"
    )
    result = semantic.run(f"{combined} ORDER BY CASE 对照层级 WHEN '本班' THEN 1 ELSE 2 END LIMIT 2 OFFSET 1")
    assert result["rows"][0] == ["本班", 419]
    assert len(plans) == 1
    assert f"WITH ordered_results AS ({combined})" in plans[0]
    assert "FROM ordered_results ORDER BY CASE 对照层级" in plans[0]
    assert plans[0].endswith("LIMIT 2 OFFSET 1")

    semantic.run(f"{combined} ORDER BY 1")
    assert plans[1] == f"{combined} ORDER BY 1"
