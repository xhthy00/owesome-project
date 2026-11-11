import asyncio
import json

import pytest
from langchain_core.messages import AIMessage

from src.chat.service.evidence_answer import evidence_table, summarize_verified_evidence
from tests.chat.test_permission_contract import runtime_harness as runtime_harness
from tests.chat.test_permission_contract import tool_call

RESULT = {
    "columns": ["school_mean_sx", "city_mean_sx", "sx_diff"],
    "rows": [[114.5, 89, 25.5]],
    "row_count": 1,
    "sql": "SELECT school_mean_sx,city_mean_sx,sx_diff FROM approved_result",
}
ANSWER = "本校数学均分114.50分，全市均分89.00分，本校高25.50分。"


def verified_reply(*, coverage="complete", ids=None, answer=ANSWER):
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "submit_verified_answer",
                "id": "answer",
                "args": {"answer": answer, "evidence_ids": ids or [1], "coverage": coverage},
            }
        ],
    )


class AnswerModel:
    def __init__(self, response):
        self.response = response
        self.messages = []
        self.tools = []

    def bind_tools(self, tools, **kwargs):
        self.tools = [tool.name for tool in tools]
        return self

    async def ainvoke(self, messages, **kwargs):
        self.messages = messages
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def synthesize(model, results=None):
    return asyncio.run(
        summarize_verified_evidence(
            model=model,
            question="本校数学均分比全市高还是低，差多少分？",
            followup="2026届高三11月期中，批次1",
            results=results or [RESULT],
            tool_choice="required",
        )
    )


def test_answer_uses_successful_evidence_and_has_no_data_query_tools():
    model = AnswerModel(verified_reply())
    result = synthesize(model)
    assert result.text == ANSWER and result.coverage == "complete"
    assert result.model_generated
    assert model.tools == ["submit_verified_answer"]
    payload = json.loads(model.messages[1].content)
    assert payload["successful_evidence"][0]["rows"] == [[114.5, 89, 25.5]]
    assert "error_detail" not in payload and "failed_queries" not in payload


@pytest.mark.parametrize(
    "response",
    [
        AIMessage(content="权限没开通"),
        AIMessage(
            content="",
            tool_calls=[
                {"name": "query_semantic", "id": "bad", "args": {"sql": "SELECT private_field"}}
            ],
        ),
        verified_reply(ids=[9]),
        verified_reply(ids=[1, 1]),
        RuntimeError("provider unavailable"),
    ],
)
def test_bad_synthesis_falls_back_to_real_values_instead_of_erasing_results(response):
    result = synthesize(AnswerModel(response))
    assert not result.model_generated and result.coverage == "partial"
    assert "114.5" in result.text and "89" in result.text and "25.5" in result.text
    assert "权限没开通" not in result.text and "private_field" not in result.text


def test_truncated_evidence_cannot_be_declared_complete():
    result = synthesize(AnswerModel(verified_reply()), [{**RESULT, "rows_truncated": True}])
    assert result.coverage == "partial" and not result.model_generated


def test_fallback_table_escapes_data_and_marks_preview_truncation():
    result = evidence_table(
        [{**RESULT, "columns": ["value"], "rows": [["<script>|new\nline"]] * 21}]
    )
    assert "<script>" not in result.text and "&lt;script&gt;" in result.text
    assert "\\|" in result.text and "部分结果" in result.text


COMPARISON = """WITH own_mean AS (
SELECT s.subject,ROUND(AVG(s.score_value),2) AS school_mean FROM edu_scores s
WHERE s.city_id='C1' AND s.school_id='S1' AND s.cohort_id='G1' AND s.exam_batch_id=901
GROUP BY s.subject
), city_mean AS (
SELECT s.subject,ROUND(AVG(s.score_value),2) AS city_mean FROM edu_scores s
WHERE s.city_id='C1' AND s.cohort_id='G1' AND s.exam_batch_id=901
GROUP BY s.subject HAVING COUNT(s.score_value)>=10 AND COUNT(DISTINCT s.school_id)>=3
)
SELECT o.subject,o.school_mean,c.city_mean,o.school_mean-c.city_mean AS gap
FROM own_mean o JOIN city_mean c ON o.subject=c.subject"""
EXTRA_SCHOOL_LIST = """SELECT s.school_id,AVG(s.score_value) AS school_mean FROM edu_scores s
WHERE s.city_id='C1' AND s.cohort_id='G1' AND s.exam_batch_id=901 GROUP BY s.school_id"""


def configure_comparison(harness, monkeypatch):
    from types import SimpleNamespace

    from src.chat.service import deepagents_runtime as runtime
    from src.chat.service import semantic_query

    saved = []

    async def persist(**kwargs):
        saved.append(kwargs)
        return 1

    monkeypatch.setattr(runtime, "persist_turn", persist)
    monkeypatch.setattr(
        semantic_query,
        "run_sql_with_auto_fix",
        lambda *_args, **_kwargs: SimpleNamespace(
            success=True,
            result={
                "columns": ["subject", "school_mean", "city_mean", "gap"],
                "rows": [["math", 114.5, 89, 25.5]],
            },
        ),
    )
    return saved


def test_successful_comparison_survives_rejected_school_ranking(runtime_harness, monkeypatch):
    harness = runtime_harness
    saved = configure_comparison(harness, monkeypatch)
    events = harness.run(
        [
            tool_call(COMPARISON, "comparison"),
            tool_call(EXTRA_SCHOOL_LIST, "unrequested-ranking"),
            AIMessage(content="全市均分无法读取"),
            verified_reply(),
        ]
    )
    assert len(harness.plans) == 1  # The school list is rejected before compilation.
    failures = [
        data for event, data in events if event == "tool_result" and not data.get("success")
    ]
    assert failures[0]["data"]["error_detail"]["reason"] == "reference_grouping_not_allowed"
    answer = next(data["content"] for event, data in events if event == "summary")
    assert ANSWER in answer and "无法读取" not in answer
    assert saved[0]["is_success"] and saved[0]["sql_error"] is None
    assert saved[0]["exec_result"]["answer_coverage"]["coverage"] == "complete"
    assert any(not call.get("success") for call in saved[0]["tool_calls"])


def test_partial_synthesis_keeps_values_without_claiming_whole_question_complete(
    runtime_harness, monkeypatch
):
    harness = runtime_harness
    saved = configure_comparison(harness, monkeypatch)
    events = harness.run(
        [
            tool_call(COMPARISON, "comparison"),
            tool_call(EXTRA_SCHOOL_LIST, "ranking"),
            AIMessage(content="权限不足，没有结果"),
            verified_reply(coverage="partial", answer="已查到本校数学均分114.50分。"),
        ]
    )
    answer = next(data["content"] for event, data in events if event == "summary")
    assert "114.50" in answer and "没有结果" not in answer
    assert not saved[0]["is_success"]
    assert saved[0]["exec_result"]["answer_coverage"]["coverage"] == "partial"


def test_revocation_during_answer_synthesis_prevents_republishing_old_values(
    runtime_harness, monkeypatch
):
    harness = runtime_harness
    saved = configure_comparison(harness, monkeypatch)
    original = harness.query.authorization_context
    calls = 0

    def authorization():
        nonlocal calls
        calls += 1
        return original() if calls <= 2 else None

    monkeypatch.setattr(harness.query, "authorization_context", authorization)
    events = harness.run(
        [
            tool_call(COMPARISON, "comparison"),
            tool_call(EXTRA_SCHOOL_LIST, "ranking"),
            AIMessage(content="无结果"),
            verified_reply(),
        ]
    )
    answer = next(data["content"] for event, data in events if event == "summary")
    assert "114.5" not in answer and "89" not in answer
    assert "上下文" in answer and not saved[0]["is_success"]
    assert "answer_coverage" not in saved[0]["exec_result"]


def test_model_call_limit_uses_a_table_without_additional_synthesis(runtime_harness, monkeypatch):
    from src.chat.service import deepagents_runtime as runtime
    from src.common.core.config import Settings

    harness = runtime_harness
    saved = configure_comparison(harness, monkeypatch)
    monkeypatch.setattr(runtime, "get_settings", lambda: Settings(_env_file=None, chat_max_steps=3))

    async def forbidden(**kwargs):
        raise AssertionError("No model budget remains")

    monkeypatch.setattr(runtime, "summarize_verified_evidence", forbidden)
    events = harness.run(
        [
            tool_call(COMPARISON, "comparison"),
            tool_call(EXTRA_SCHOOL_LIST, "ranking"),
            AIMessage(content="没有结果"),
        ]
    )
    answer = next(data["content"] for event, data in events if event == "summary")
    assert "114.5" in answer and "89" in answer and "25.5" in answer
    assert saved[0]["exec_result"]["answer_coverage"]["model_generated"] is False


def test_empty_success_is_not_misreported_as_an_authorization_change():
    from src.chat.service.evidence_answer import revalidate_evidence

    result = asyncio.run(
        revalidate_evidence(
            lambda: {"authorization_fingerprint": "current"},
            [
                {
                    **RESULT,
                    "rows": [],
                    "permission_mode": "model_explicit_v2",
                    "authorization_fingerprint": "current",
                }
            ],
        )
    )
    assert result.results == [] and result.error is None


def test_current_evidence_excludes_old_or_unattested_results():
    from src.chat.service.evidence_answer import revalidate_evidence

    rows = [
        RESULT,
        {**RESULT, "permission_mode": "model_explicit_v2", "authorization_fingerprint": "old"},
        {**RESULT, "permission_mode": "model_explicit_v2", "authorization_fingerprint": "current"},
    ]
    result = asyncio.run(
        revalidate_evidence(lambda: {"authorization_fingerprint": "current"}, rows)
    )
    assert result.results == [rows[2]]
    changed = asyncio.run(
        revalidate_evidence(lambda: {"authorization_fingerprint": "new"}, rows, expected="current")
    )
    assert not changed.results and changed.error["category"] == "context_changed"


def test_blank_synthesis_uses_the_result_table():
    result = synthesize(AnswerModel(verified_reply(answer="   ")))
    assert not result.model_generated and "114.5" in result.text


def test_large_evidence_is_bounded_and_cannot_be_marked_complete():
    from src.chat.service.evidence_answer import evidence_payload

    rows = [["x" * 1000] * 30] * 30
    results = [
        {"columns": [f"c{i}" for i in range(30)], "rows": rows, "sql": "x" * 50000}
        for _ in range(8)
    ]
    items = evidence_payload(results)
    assert len(json.dumps(items)) < 42000
    assert all(item["preview_truncated"] for item in items)
    assert len({item["id"] for item in items}) == len(items)


def test_evidence_synthesis_timeout_keeps_successful_values():
    class SlowModel(AnswerModel):
        async def ainvoke(self, messages, **kwargs):
            await asyncio.sleep(1)
            return self.response

    result = asyncio.run(
        summarize_verified_evidence(
            model=SlowModel(verified_reply()),
            question="均分",
            followup="批次1",
            results=[RESULT],
            tool_choice="required",
            timeout_seconds=0.001,
        )
    )
    assert not result.model_generated and "114.5" in result.text


def test_checkpoint_retains_the_corrected_answer_for_followups(runtime_harness, monkeypatch):
    from src.chat.service import deepagents_runtime as runtime

    harness = runtime_harness
    configure_comparison(harness, monkeypatch)
    original_create = runtime.create_deep_agent
    graphs = []

    def create(*args, **kwargs):
        graph = original_create(*args, **kwargs)
        graphs.append(graph)
        return graph

    monkeypatch.setattr(runtime, "create_deep_agent", create)
    harness.run(
        [
            tool_call(COMPARISON, "comparison"),
            tool_call(EXTRA_SCHOOL_LIST, "extra"),
            AIMessage(content="没有权限，没有结果"),
            verified_reply(),
        ]
    )
    state = asyncio.run(
        graphs[0].aget_state({"configurable": {"thread_id": "awesome:deepagents:v1:1:7:4:9"}})
    )
    assert not state.next
    assert ANSWER in state.values["messages"][-1].text
    assert "没有结果" not in state.values["messages"][-1].text


def test_table_does_not_render_untrusted_markdown_images():
    value = "![image](https://external.test/pixel)"
    result = evidence_table([{**RESULT, "columns": ["label"], "rows": [[value]]}])
    assert "![image]" not in result.text
    assert "\\!\\[image\\]" in result.text


def test_cancellation_during_synthesis_recovers_checkpoint_without_persisting(
    runtime_harness, monkeypatch
):
    from src.chat.service import deepagents_runtime as runtime

    harness = runtime_harness
    saved = configure_comparison(harness, monkeypatch)
    original = runtime._recover_checkpoint
    recovered = []

    async def recover(*args, **kwargs):
        recovered.append(True)
        await original(*args, **kwargs)

    async def cancel(**kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(runtime, "_recover_checkpoint", recover)
    monkeypatch.setattr(runtime, "summarize_verified_evidence", cancel)
    with pytest.raises(asyncio.CancelledError):
        harness.run(
            [
                tool_call(COMPARISON, "comparison"),
                tool_call(EXTRA_SCHOOL_LIST, "extra"),
                AIMessage(content="无结果"),
            ]
        )
    assert recovered == [True] and not saved
