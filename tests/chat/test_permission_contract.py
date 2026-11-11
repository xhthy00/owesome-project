"""Contract conformance, compiler mutations and host-owned recovery regressions."""

from dataclasses import replace
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlglot import parse_one

from src.chat.service.semantic_permission_validator import (
    PermissionValidator,
    verify_compiled_permissions,
)
from src.chat.service.semantic_query import SemanticAuthorizationError, SemanticFieldError, _query
from src.datasource.service.education_authorization import AuthorizationError, PermissionManifest
from src.datasource.service.semantic_permission_contract import (
    CONTRACT_VERSION,
    PROVER_VERSION,
    PermissionRecovery,
    build_query_contract,
)
from tests.chat.test_explicit_permissions import (
    BASELINE,
    OWN,
    RANK,
    context,
    execution_query,
    manifest,
)


def test_model_validator_and_result_share_one_versioned_contract(monkeypatch):
    ctx = context(capabilities=("grade_baseline",))
    contract = build_query_contract(ctx.manifest)
    presented = ctx.model_context()
    assert presented["query_contract"]["fingerprint"] == contract.fingerprint
    assert presented["query_contract"]["version"] == CONTRACT_VERSION
    assert presented["query_contract"]["prover_version"] == PROVER_VERSION
    assert presented["reference_policy"] == contract.reference_policy.model_dump(mode="json")
    assert presented["relations"]["edu_scores"]["reference_query_contract"] == contract.relations[
        "edu_scores"
    ].model_dump(mode="json")
    assert presented["relations"]["edu_scores"]["reference_query_contract"]["sample_columns"] == {
        "subject_mean": "score_value"
    }
    assert "unused" not in str(presented["query_contract"])
    proof = PermissionValidator(ctx).prove(_query(BASELINE, "postgres"))
    assert proof.contract_fingerprint == contract.fingerprint
    query, _, _ = execution_query(monkeypatch, BASELINE, ctx)
    assert query.run(BASELINE)["query_contract"] == contract.identity()


@pytest.mark.parametrize(
    "change,value",
    [
        ("minimum_sample_size", 20),
        ("minimum_reference_units", 4),
        ("precision", 3),
    ],
)
def test_contract_changes_invalidate_proofs_before_compiled_plan_is_read(change, value):
    ctx = context(capabilities=("grade_baseline",))
    proof = PermissionValidator(ctx).prove(_query(BASELINE, "postgres"))
    updated = ctx.model_copy(update={"manifest": ctx.manifest.model_copy(update={change: value})})
    with pytest.raises(AuthorizationError) as caught:
        verify_compiled_permissions(parse_one("SELECT 1"), updated, proof)
    assert caught.value.detail["code"] == "AUTH_CONTRACT_MISMATCH"
    assert caught.value.detail["recovery_action"] == "refresh_context"
    assert not caught.value.detail["retryable"]


def test_proof_is_bound_to_authorization_and_prover_identity():
    ctx = context()
    proof = PermissionValidator(ctx).prove(_query(OWN, "postgres"))
    for forged, expected in [
        (replace(proof, authorization_fingerprint="other-user"), "AUTH_CONTEXT_STALE"),
        (replace(proof, contract_fingerprint="old-prover-version"), "AUTH_CONTRACT_MISMATCH"),
    ]:
        with pytest.raises(AuthorizationError) as caught:
            verify_compiled_permissions(parse_one("SELECT 1"), ctx, forged)
        assert caught.value.detail["code"] == expected


@pytest.mark.parametrize(
    "sql,kwargs",
    [
        (BASELINE.replace(">=10", ">=19"), {"minimum_sample_size": 20}),
        (BASELINE, {"minimum_reference_units": 4}),
        (BASELINE, {"precision": 3}),
    ],
)
def test_advertised_thresholds_are_executed_not_prompt_only(sql, kwargs):
    ctx = context(capabilities=("grade_baseline",))
    ctx = ctx.model_copy(update={"manifest": ctx.manifest.model_copy(update=kwargs)})
    with pytest.raises(AuthorizationError) as caught:
        PermissionValidator(ctx).prove(_query(sql, "postgres"))
    assert caught.value.detail["category"] == "query_invalid"
    good = (
        BASELINE.replace(">=10", f">={kwargs.get('minimum_sample_size', 10)}")
        .replace(">=3", f">={kwargs.get('minimum_reference_units', 3)}")
        .replace("),2)", f"),{kwargs.get('precision', 2)})")
    )
    assert PermissionValidator(ctx).prove(_query(good, "postgres")).releases


@pytest.mark.parametrize(
    "formula,sample",
    [
        ("AVG(missing_score)", "score_value"),
        ("AVG(score_value); SELECT score_value FROM private_scores", "score_value"),
        ("AVG(score_value) OVER ()", "score_value"),
        ("(SELECT AVG(score_value) FROM private_scores)", "score_value"),
        ("score_value", "score_value"),
        ("AVG(score_value)", "missing_sample"),
    ],
)
def test_unimplementable_metric_contracts_fail_during_policy_loading(formula, sample):
    data = manifest().model_dump(mode="json")
    data["relations"]["edu_scores"].update(metrics={"subject_mean": formula}, sample_column=sample)
    with pytest.raises(ValidationError):
        PermissionManifest.model_validate(data)


def test_explicitly_approved_count_metric_matches_advertised_output_permission():
    ctx = context(capabilities=("grade_baseline",))
    data = ctx.manifest.model_dump(mode="json")
    data["relations"]["edu_scores"]["metrics"]["sample_count"] = "COUNT(score_value)"
    policy = PermissionManifest.model_validate(data)
    ctx = ctx.model_copy(update={"manifest": policy})
    rules = ctx.model_context()["relations"]["edu_scores"]["reference_query_contract"]
    assert rules["baseline_count_output_allowed"] and rules["approved_count_metric_ids"] == [
        "sample_count"
    ]
    sql = BASELINE.replace(
        "ROUND(AVG(s.score_value),2) AS grade_mean", "ROUND(COUNT(s.score_value),2) AS grade_mean"
    )
    assert PermissionValidator(ctx).prove(_query(sql, "postgres")).releases


@pytest.mark.parametrize(
    "code,category,action",
    [
        ("AUTH_SCOPE_OUTSIDE_GRANT", "access_denied", "stop"),
        ("AUTH_REVOKED", "access_denied", "stop"),
        ("AUTH_COLUMN_FORBIDDEN", "access_denied", "stop"),
        ("AUTH_SCOPE_MISSING", "query_invalid", "repair_sql"),
        ("AUTH_RELEASE_SHAPE_INVALID", "query_invalid", "repair_sql"),
        ("AUTH_QUERY_SHAPE_UNSUPPORTED", "query_unsupported", "repair_sql"),
        ("AUTH_MAPPING_MISSING", "policy_invalid", "contact_admin"),
        ("AUTH_CONTEXT_STALE", "context_changed", "refresh_context"),
        ("AUTH_PHYSICAL_PROOF_FAILED", "compiled_plan_unverifiable", "contact_admin"),
        ("AUTH_UNKNOWN", "policy_invalid", "contact_admin"),
    ],
)
def test_error_taxonomy_cannot_be_relabeled_by_a_caller(code, category, action):
    error = AuthorizationError(
        code, "test", retryable=True, category="access_granted", requires_user_input=True
    )
    assert error.detail["category"] == category
    assert error.detail["recovery_action"] == action
    assert error.detail["retryable"] is (action == "repair_sql")
    assert not error.detail["requires_user_input"]
    recovery = PermissionRecovery()
    recovery.reject(error.detail)
    assert recovery.blocked is (action != "repair_sql")
    assert recovery.human_retry_blocked


def test_recovery_budget_survives_successes_and_terminal_state_cannot_reopen():
    recovery = PermissionRecovery()
    invalid = AuthorizationError("AUTH_SCOPE_MISSING", "missing filter").detail
    recovery.reject(invalid)
    recovery.success()
    assert not recovery.human_retry_blocked
    recovery.reject(invalid)
    assert not recovery.blocked
    recovery.reject(invalid)
    assert recovery.blocked
    assert recovery.last_error["recovery_action"] == "stop"
    assert recovery.last_error["remaining_sql_repairs"] == 0
    assert recovery.last_error["repair_budget_exhausted"]
    error = recovery.public_error()
    recovery.success()
    assert recovery.blocked and recovery.public_error() == error
    recovery.reject(AuthorizationError("AUTH_REVOKED", "revoked").detail)
    assert recovery.public_error() == error


@pytest.mark.parametrize(
    "sql,ctx",
    [
        (BASELINE, context(capabilities=("grade_baseline",))),
        (RANK, context("district", capabilities=("district_self_rank",))),
    ],
)
@pytest.mark.parametrize(
    "shape", ["direct", "projection", "shared", "nested_shared", "quoted_cast", "limited"]
)
def test_real_compiler_shape_conformance_matrix(sql, ctx, shape):
    proof = PermissionValidator(ctx).prove(_query(sql, "postgres"))
    physical = sql.replace("edu_scores s", "public.scores s")
    columns = ",".join(ctx.manifest.relations["edu_scores"].columns)
    if shape == "projection":
        physical = sql.replace("edu_scores s", f"(SELECT {columns} FROM public.scores) s")
    elif shape in {"shared", "nested_shared"}:
        # Wren's shared model projection: every consumer keeps its own filters.
        physical = sql.replace("edu_scores s", "shared_source s")
        if physical.startswith("WITH "):
            physical = (
                f"WITH shared_source AS (SELECT {columns} FROM public.scores), " + physical[5:]
            )
        else:
            physical = f"WITH shared_source AS (SELECT {columns} FROM public.scores) " + physical
        if shape == "nested_shared":
            physical = physical.replace(
                f"SELECT {columns} FROM public.scores",
                f"SELECT {columns} FROM (SELECT {columns} FROM public.scores) inner_source",
            )
    elif shape == "quoted_cast":
        physical = (
            physical.replace("AVG(s.score_value)", 'AVG(s."score_value")')
            .replace(
                'ROUND(AVG(s."score_value"),2)', 'ROUND(CAST(AVG(s."score_value") AS DECIMAL),2)'
            )
            .replace("ROUND(r.mean_score,2)", "ROUND(CAST(r.mean_score AS DECIMAL),2)")
        )
    elif shape == "limited":
        physical += " LIMIT 200"
    original = physical
    verify_compiled_permissions(_query(physical, "postgres", compiled=True), ctx, proof)
    assert physical == original  # Only the validation copy may be normalized.
    for tampered in [
        physical.replace("s.city_id='C1'", "s.city_id IN ('C1','C2')"),
        physical.replace("s.exam_batch_id=901", "s.exam_batch_id=902"),
        physical.replace("public.scores", "public.private_scores"),
    ]:
        with pytest.raises(AuthorizationError) as caught:
            verify_compiled_permissions(_query(tampered, "postgres", compiled=True), ctx, proof)
        assert caught.value.detail["category"] == "compiled_plan_unverifiable"
        assert caught.value.detail["recovery_action"] == "contact_admin"


@pytest.fixture
def runtime_harness(monkeypatch):
    import asyncio
    from contextlib import nullcontext

    from langgraph.checkpoint.memory import InMemorySaver

    from src.chat.schemas import ChatRequest
    from src.chat.service import deepagents_runtime as runtime
    from src.common.core.config import Settings
    from tests.chat.test_deepagents_runtime import ToolCallingModel, assessment_ready

    ctx = context("school", capabilities=("city_baseline",))
    query, plans, executions = execution_query(monkeypatch, ctx=ctx)
    monkeypatch.setattr(query, "authorization_context", lambda: ctx.model_context())
    monkeypatch.setattr(runtime, "SemanticQuery", lambda *_: query)
    monkeypatch.setattr(
        runtime, "get_settings", lambda: Settings(_env_file=None, chat_max_steps=12)
    )
    monkeypatch.setattr(runtime, "authorization_fingerprint", lambda *_: "stable")
    monkeypatch.setattr(runtime, "load_conversation_history", lambda *_: [])
    monkeypatch.setattr(runtime, "_thread_lock", lambda *_: nullcontext())
    monkeypatch.setattr(runtime, "_planning_required", lambda *_: False)
    monkeypatch.setattr(runtime, "review_question", assessment_ready)
    monkeypatch.setattr(runtime, "log_tool_call_fire_and_forget", lambda **_: None)
    events = []

    async def emit(event, data):
        events.append((event, data))

    async def persist(**kwargs):
        return 1

    monkeypatch.setattr(runtime, "persist_turn", persist)

    def run(responses):
        asyncio.run(
            runtime.run_chat_stream(
                request=ChatRequest(
                    datasource_id=4, conversation_id=9, question="本校与全市数学均分"
                ),
                current_user_id=7,
                workspace_oid=1,
                emit=emit,
                checkpointer=InMemorySaver(),
                model=ToolCallingModel(responses=responses),
            )
        )
        return events

    return SimpleNamespace(run=run, query=query, events=events, plans=plans, executions=executions)


def tool_call(sql, id_):
    from langchain_core.messages import AIMessage

    return AIMessage(
        content="", tool_calls=[{"name": "query_semantic", "id": id_, "args": {"sql": sql}}]
    )


def test_first_technical_failure_cannot_open_a_human_retry_form(runtime_harness):
    from langchain_core.messages import AIMessage

    harness = runtime_harness
    events = harness.run(
        [
            tool_call("SELECT s.score_value FROM edu_scores s", "invalid"),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "ask_human",
                        "id": "retry",
                        "args": {"question": "是否继续尝试？", "options": ["继续", "重新授权"]},
                    }
                ],
            ),
            tool_call(OWN, "repaired"),
            AIMessage(content="查询完成"),
        ]
    )
    assert len(harness.plans) == len(harness.executions) == 1
    assert not any(event == "clarify" for event, _ in events)
    assert ("summary", {"content": "查询完成"}) in events


def test_parallel_bad_queries_cannot_exceed_host_repair_budget(runtime_harness, monkeypatch):
    import threading
    import time

    from langchain_core.messages import AIMessage

    harness = runtime_harness
    original = harness.query.run
    running, maximum = 0, 0
    lock = threading.Lock()

    def slow_run(*args, **kwargs):
        nonlocal running, maximum
        with lock:
            running += 1
            maximum = max(maximum, running)
        try:
            time.sleep(0.01)
            return original(*args, **kwargs)
        finally:
            with lock:
                running -= 1

    monkeypatch.setattr(harness.query, "run", slow_run)
    events = harness.run(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "query_semantic",
                        "id": f"bad-{i}",
                        "args": {"sql": "SELECT s.score_value FROM edu_scores s"},
                    }
                    for i in range(5)
                ],
            ),
            AIMessage(content="账号授权已失效，请重新授权"),
        ]
    )
    assert maximum == 1 and not harness.plans and not harness.executions
    assert len([data for event, data in events if event == "tool_call"]) == 3
    answer = next(data["content"] for event, data in events if event == "summary")
    assert "查询 SQL" in answer and "授权已失效" not in answer


@pytest.mark.parametrize(
    "code,expected",
    [
        ("AUTH_REVOKED", "当前授权不允许"),
        ("AUTH_QUERY_SHAPE_UNSUPPORTED", "暂不支持"),
        ("AUTH_PHYSICAL_PROOF_FAILED", "编译结果未通过安全校验"),
    ],
)
def test_host_owns_error_attribution_even_if_model_invents_missing_grants(
    runtime_harness, monkeypatch, code, expected
):
    from langchain_core.messages import AIMessage

    harness = runtime_harness

    def fail(*args, **kwargs):
        raise SemanticAuthorizationError(AuthorizationError(code, "test failure"))

    monkeypatch.setattr(harness.query, "run", fail)
    events = harness.run(
        [tool_call(OWN, "failed"), AIMessage(content="全市基准权限未开通，请用户重新授权")]
    )
    answer = next(data["content"] for event, data in events if event == "summary")
    assert expected in answer and "权限未开通" not in answer


def test_partial_success_preserves_results_but_replaces_wrong_failure_explanation(
    runtime_harness, monkeypatch
):
    from langchain_core.messages import AIMessage

    harness = runtime_harness
    original = harness.query.run
    calls = 0

    def run(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise SemanticAuthorizationError(
                AuthorizationError("AUTH_PHYSICAL_PROOF_FAILED", "plan failed")
            )
        return original(*args, **kwargs)

    monkeypatch.setattr(harness.query, "run", run)
    events = harness.run(
        [tool_call(OWN, "own"), tool_call(OWN, "city"), AIMessage(content="你的全市权限没开通")]
    )
    assert sum(event == "result" for event, _ in events) == 1
    answer = next(data["content"] for event, data in events if event == "summary")
    assert "编译结果未通过安全校验" in answer and "没开通" not in answer


def test_metric_id_error_explains_expression_without_reaching_planner(monkeypatch):
    query, plans, _ = execution_query(monkeypatch)
    with pytest.raises(SemanticFieldError) as caught:
        query.run("SELECT subject_mean FROM edu_scores")
    assert not plans
    assert caught.value.detail["metric_definition"] == {
        "id": "subject_mean",
        "expression": "AVG(score_value)",
    }


@pytest.mark.parametrize(
    "status,category,action",
    [
        (400, "query_unsupported", "repair_sql"),
        (422, "query_unsupported", "repair_sql"),
        (404, "context_changed", "refresh_context"),
        (409, "context_changed", "refresh_context"),
        (503, "service_unavailable", "contact_admin"),
    ],
)
def test_compiler_http_failures_keep_their_operational_category(
    monkeypatch, status, category, action
):
    import httpx

    from src.chat.service import semantic_query
    from src.common.core.config import Settings

    runner = semantic_query.SemanticQuery(
        Settings(
            _env_file=None, datasemantic_url="https://semantic.test", datasemantic_token="secret"
        ),
        4,
        1,
        7,
    )
    response = httpx.Response(
        status, json={}, request=httpx.Request("POST", "https://semantic.test/plan")
    )
    monkeypatch.setattr(semantic_query.httpx, "request", lambda *_args, **_kwargs: response)
    with pytest.raises(semantic_query.SemanticContractError) as caught:
        runner._call("POST", "plan", {})
    assert caught.value.detail["category"] == category
    assert caught.value.detail["recovery_action"] == action
    assert "secret" not in str(caught.value.detail)


def test_database_failure_is_not_reported_as_denied_permission(monkeypatch):
    from src.chat.service import semantic_query

    query, _, _ = execution_query(monkeypatch)
    monkeypatch.setattr(
        semantic_query,
        "run_sql_with_auto_fix",
        lambda *_args, **_kwargs: SimpleNamespace(success=False, result=None),
    )
    with pytest.raises(semantic_query.SemanticContractError) as caught:
        query.run(OWN)
    assert caught.value.detail["category"] == "execution_failed"
    assert caught.value.detail["recovery_action"] == "contact_admin"


def test_invalid_compiler_sql_is_terminal_and_never_repaired_by_the_model(monkeypatch):
    query, _, executions = execution_query(monkeypatch)
    monkeypatch.setattr(
        query, "_plan", lambda *_args: {"physical_sql": "DELETE FROM public.scores"}
    )
    with pytest.raises(SemanticAuthorizationError) as caught:
        query.run(OWN)
    assert caught.value.detail["category"] == "compiled_plan_unverifiable"
    assert not caught.value.detail["retryable"] and not executions
