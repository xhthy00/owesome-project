from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.chat.service.semantic_permission_validator import (
    PermissionValidator,
    verify_compiled_permissions,
)
from src.chat.service.semantic_query import _query
from src.datasource.service.education_authorization import (
    AUTH_KEY,
    AuthorizationError,
    EducationAuthorization,
    EducationGrant,
    PermissionManifest,
    context_for,
    read_authorization,
)


def manifest():
    return PermissionManifest.model_validate(
        {
            "workspace_oid": 1,
            "datasource_id": 4,
            "bundle_id": "edu",
            "manifest_hash": "a" * 64,
            "connection": {
                "host": "localhost",
                "port": 5432,
                "database": "unused",
                "username": "unused",
            },
            "relations": {
                "edu_scores": {
                    "physical_table": "scores",
                    "columns": {
                        k: k
                        for k in [
                            "city_id",
                            "district_id",
                            "school_id",
                            "cohort_id",
                            "class_id",
                            "student_id",
                            "exam_batch_id",
                            "exam_date",
                            "subject",
                            "score_value",
                        ]
                    },
                    "scope_columns": {
                        k: k
                        for k in [
                            "city_id",
                            "district_id",
                            "school_id",
                            "cohort_id",
                            "class_id",
                            "student_id",
                        ]
                    },
                    "date_column": "exam_date",
                    "metrics": {"subject_mean": "AVG(score_value)"},
                    "forbidden_columns": ["student_id"],
                }
            },
            "catalog": [
                {
                    "city_id": "C1",
                    "district_id": "D1",
                    "school_id": "S1",
                    "cohort_id": "G1",
                    "class_id": "B1",
                }
            ],
        }
    )


def actor(level="class", **kwargs):
    keys = {"city_id": "C1"}
    if level == "district":
        keys["district_id"] = "D1"
    if level in {"school", "grade", "class", "student"}:
        keys["school_id"] = "S1"
    if level in {"grade", "class", "student"}:
        keys["cohort_id"] = "G1"
    if level == "class":
        keys["class_id"] = "B1"
    if level == "student":
        keys["student_id"] = "ST1"
    grant = EducationGrant(id="g1", workspace_oid=1, datasource_id=4, level=level, **keys, **kwargs)
    doc = EducationAuthorization(revision=1, grants=(grant,))
    return SimpleNamespace(id=7, status=1, system_variables={AUTH_KEY: doc.model_dump(mode="json")})


def context(level="class", **kwargs):
    return context_for(actor(level, **kwargs), 1, 4, manifest())


OWN = """SELECT s.subject, AVG(s.score_value) AS mean_score FROM edu_scores s
WHERE s.city_id='C1' AND s.school_id='S1' AND s.cohort_id='G1' AND s.class_id='B1'
AND s.exam_batch_id=901 GROUP BY s.subject"""

BASELINE = """WITH own_scores AS (
SELECT s.subject, AVG(s.score_value) AS own_mean FROM edu_scores s
WHERE s.city_id='C1' AND s.school_id='S1' AND s.cohort_id='G1' AND s.class_id='B1'
AND s.exam_batch_id=901 GROUP BY s.subject
), grade_baseline AS (
SELECT s.subject, ROUND(AVG(s.score_value),2) AS grade_mean FROM edu_scores s
WHERE s.city_id='C1' AND s.school_id='S1' AND s.cohort_id='G1' AND s.exam_batch_id=901
GROUP BY s.subject HAVING COUNT(s.score_value)>=10 AND COUNT(DISTINCT s.class_id)>=3
)
SELECT o.subject, o.own_mean, g.grade_mean, o.own_mean-g.grade_mean AS gap
FROM own_scores o JOIN grade_baseline g ON g.subject=o.subject"""

RANK = """WITH dm AS (
SELECT s.district_id, AVG(s.score_value) AS mean_score FROM edu_scores s
WHERE s.city_id='C1' AND s.cohort_id='G1' AND s.exam_batch_id=901 AND s.subject='math'
GROUP BY s.district_id HAVING COUNT(s.score_value)>=10
), ranks AS (
SELECT d.district_id,d.mean_score,RANK() OVER(ORDER BY d.mean_score DESC) AS city_rank,
COUNT(*) OVER() AS units FROM dm d
)
SELECT r.district_id,ROUND(r.mean_score,2) AS own_mean,r.city_rank,r.units FROM ranks r
WHERE r.district_id='D1' AND r.units>=3"""


def prove(sql, ctx=None):
    ctx = ctx or context()
    return PermissionValidator(ctx).prove(_query(sql, "postgres"))


def test_json_storage_and_expiry_are_scoped():
    user = actor(valid_to=datetime.now(timezone.utc) - timedelta(seconds=1))
    assert read_authorization(user).revision == 1
    with pytest.raises(AuthorizationError):
        context_for(user, 1, 4, manifest())
    with pytest.raises(AuthorizationError):
        context_for(actor(), 2, 4, manifest())
    with pytest.raises(AuthorizationError):
        context_for(SimpleNamespace(id=7, status=1, system_variables={}), 1, 4, manifest())


def test_ordinary_model_query_is_verified_without_injection():
    proof = prove(OWN)
    assert proof.scans and not proof.releases
    assert proof.output_columns == ("subject", "mean_score")


@pytest.mark.parametrize(
    "sql",
    [
        OWN.replace("AND s.class_id='B1'", ""),
        OWN.replace("'B1'", "'B2'"),
        OWN.replace("s.city_id='C1'", "(s.city_id='C1' OR 1=1)"),
        OWN.replace("s.class_id='B1'", "s.class_id IN ('B1','B2')"),
        OWN.replace("s.cohort_id='G1'", "s.cohort_id LIKE 'G%'"),
        OWN.replace("s.subject,", "s.student_id,"),
        OWN + " UNION ALL SELECT subject,score_value FROM edu_scores",
    ],
)
def test_ordinary_permission_mutations_rejected(sql):
    with pytest.raises(AuthorizationError):
        prove(sql)


def test_complete_grants_cannot_be_cross_combined():
    user = actor()
    doc = read_authorization(user)
    other = doc.grants[0].model_copy(update={"id": "g2", "school_id": "S2", "class_id": "B2"})
    user.system_variables[AUTH_KEY] = doc.model_copy(
        update={"grants": (*doc.grants, other)}
    ).model_dump(mode="json")
    policy = manifest()
    policy = policy.model_copy(
        update={
            "catalog": (
                *policy.catalog,
                {"city_id": "C1", "school_id": "S2", "cohort_id": "G1", "class_id": "B2"},
            )
        }
    )
    ctx = context_for(user, 1, 4, policy)
    prove(
        OWN.replace("s.school_id='S1'", "s.school_id='S2'").replace(
            "s.class_id='B1'", "s.class_id='B2'"
        ),
        ctx,
    )
    with pytest.raises(AuthorizationError):
        prove(
            OWN.replace("s.school_id='S1'", "s.school_id IN ('S1','S2')").replace(
                "s.class_id='B1'", "s.class_id IN ('B1','B2')"
            ),
            ctx,
        )


def test_grade_baseline_is_model_sql_not_a_generated_template():
    proof = prove(BASELINE, context(capabilities=("grade_baseline",)))
    assert proof.releases
    with pytest.raises(AuthorizationError):
        prove(BASELINE)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda s: s.replace("HAVING COUNT(s.score_value)>=10", ""),
        lambda s: s.replace("ROUND(AVG(s.score_value),2)", "AVG(s.score_value)"),
        lambda s: s.replace("COUNT(s.score_value)>=10", "COUNT(s.score_value)>=1"),
        lambda s: s.replace("SELECT s.subject, ROUND", "SELECT s.class_id, ROUND"),
        lambda s: s.replace(
            "SELECT o.subject, o.own_mean, g.grade_mean, o.own_mean-g.grade_mean AS gap",
            "SELECT s.subject,s.score_value",
        ).replace(
            "FROM own_scores o JOIN grade_baseline g ON g.subject=o.subject", "FROM edu_scores s"
        ),
    ],
)
def test_baseline_population_and_output_mutations_rejected(mutate):
    with pytest.raises(AuthorizationError):
        prove(mutate(BASELINE), context(capabilities=("grade_baseline",)))


def test_district_rank_releases_only_target():
    proof = prove(RANK, context("district", capabilities=("district_self_rank",)))
    assert proof.releases


@pytest.mark.parametrize(
    "mutate",
    [
        lambda s: s.replace("r.district_id='D1' AND", ""),
        lambda s: s.replace("r.units>=3", "r.units>=1"),
        lambda s: s.replace(
            "ORDER BY d.mean_score DESC", "PARTITION BY d.district_id ORDER BY d.mean_score DESC"
        ),
        lambda s: s.replace("FROM dm d", "FROM dm d WHERE d.district_id='D1'"),
        lambda s: s.replace("ROUND(r.mean_score,2)", "r.mean_score"),
        lambda s: s.replace("r.district_id='D1'", "r.district_id IN ('D1','D2')"),
        lambda s: s.replace("s.city_id='C1'", "s.city_id IN ('C1','C2')"),
    ],
)
def test_rank_leakage_mutations_rejected(mutate):
    with pytest.raises(AuthorizationError):
        prove(mutate(RANK), context("district", capabilities=("district_self_rank",)))


def test_physical_compiler_wrappers_preserve_proof():
    ctx = context()
    logical = prove(OWN, ctx)
    physical = OWN.replace("edu_scores s", "public.scores s")
    verify_compiled_permissions(_query(physical, "postgres", compiled=True), ctx, logical)
    wrapped = physical.replace(
        "public.scores s",
        "(SELECT city_id,school_id,cohort_id,class_id,subject,score_value,exam_batch_id FROM public.scores) s",
    )
    verify_compiled_permissions(_query(wrapped, "postgres", compiled=True), ctx, logical)
    for mutation in [
        physical.replace("AND s.class_id='B1'", ""),
        physical.replace("public.scores", "public.unreviewed"),
        physical.replace("=901", "=902"),
    ]:
        with pytest.raises(AuthorizationError):
            verify_compiled_permissions(_query(mutation, "postgres", compiled=True), ctx, logical)


@pytest.mark.parametrize(
    "sql,ctx",
    [
        (BASELINE, context(capabilities=("grade_baseline",))),
        (RANK, context("district", capabilities=("district_self_rank",))),
    ],
)
def test_reference_physical_plans_must_match_logical_proof(sql, ctx):
    proof = prove(sql, ctx)
    physical = sql.replace("edu_scores s", "public.scores s")
    verify_compiled_permissions(_query(physical, "postgres", compiled=True), ctx, proof)


def test_history_requires_explicit_date_bounds():
    ctx = context(history_from="2026-01-01", history_to="2026-12-31")
    with pytest.raises(AuthorizationError):
        prove(OWN, ctx)
    prove(
        OWN.replace(
            "GROUP BY", "AND s.exam_date >= '2026-01-01' AND s.exam_date <= '2026-12-31' GROUP BY"
        ),
        ctx,
    )


def test_revocation_preserves_resource_tombstone_and_other_variables():
    from src.datasource.service.education_authorization import replace_resource_grants

    user = actor()
    user.system_variables["unrelated"] = {"keep": True}
    document = replace_resource_grants(user, 1, 4, 1, ())
    assert document.revision == 2 and not document.grants
    assert document.managed_resources == ("1:4",)
    assert user.system_variables["unrelated"] == {"keep": True}
    with pytest.raises(AuthorizationError):
        replace_resource_grants(user, 1, 4, 1, ())


def execution_query(monkeypatch, sql=OWN, ctx=None):
    from src.chat.service import semantic_query as module

    ctx = ctx or context()
    settings = SimpleNamespace(
        database_edu_url="postgresql://unused:unused@localhost/unused",
        chat_query_timeout_seconds=1,
        semantic_permission_manifest_path="",
    )
    query = module.SemanticQuery(settings, 4, 1, 7)
    publication = {
        "bundle_id": "edu",
        "manifest_hash": "a" * 64,
        "dialect": "postgres",
        "models": [
            {
                "name": "edu_scores",
                "columns": [{"name": c} for c in ctx.manifest.relations["edu_scores"].columns],
            }
        ],
    }
    monkeypatch.setattr(query, "context", lambda: publication)
    monkeypatch.setattr(query, "_permission_manifest", lambda: ctx.manifest)
    monkeypatch.setattr(query, "_fresh_authorization", lambda: ctx)
    plans, executions = [], []

    def planner(generated_sql, binding, dialect):
        plans.append(generated_sql)
        return {"physical_sql": generated_sql.replace("edu_scores", "scores"), "request_id": "p1"}

    def execute(sql, **kwargs):
        executions.append((sql, kwargs))
        columns = PermissionValidator(ctx).prove(_query(plans[-1], "postgres")).output_columns
        return SimpleNamespace(
            success=True, result={"columns": list(columns), "rows": [[1] * len(columns)]}
        )

    monkeypatch.setattr(query, "_plan", planner)
    monkeypatch.setattr(module, "run_sql_with_auto_fix", execute)
    monkeypatch.setattr(module, "apply_result_privacy", lambda result: result)
    monkeypatch.setattr(
        module,
        "apply_permissions_for_execute",
        lambda *_: pytest.fail("v2 must not inject permissions"),
    )
    return query, plans, executions


@pytest.mark.parametrize("execute", [True, False])
def test_query_service_validates_before_compilation_and_never_injects(monkeypatch, execute):
    query, plans, executions = execution_query(monkeypatch)
    result = query.run(OWN, execute=execute)
    assert plans
    assert bool(executions) == execute
    assert "scores" not in result["tables"]
    assert "public.scores" not in result["sql"]
    assert result["permission_mode"] == "model_explicit_v2"
    if execute:
        assert executions[0][1]["max_fix_attempts"] == 0
        assert executions[0][1]["config"]["read_only"]
    plans.clear()
    executions.clear()
    with pytest.raises(Exception) as error:
        query.run(OWN.replace("AND s.class_id='B1'", ""), execute=execute)
    assert getattr(error.value, "detail")["code"] == "AUTH_SCOPE_MISSING"
    assert not plans and not executions


def test_tampered_compiled_plan_never_executes(monkeypatch):
    query, plans, executions = execution_query(monkeypatch)
    monkeypatch.setattr(
        query,
        "_plan",
        lambda *_: {
            "physical_sql": OWN.replace("edu_scores", "scores").replace("AND s.class_id='B1'", "")
        },
    )
    with pytest.raises(Exception) as error:
        query.run(OWN)
    assert error.value.detail["code"] == "AUTH_PHYSICAL_PROOF_FAILED"
    assert not executions


@pytest.mark.parametrize("after_execution", [False, True])
def test_revoke_in_flight_discards_result(monkeypatch, after_execution):
    query, plans, executions = execution_query(monkeypatch)
    ctx = context()
    changed = ctx.model_copy(update={"fingerprint": "revoked"})
    calls = []

    def fresh():
        calls.append(True)
        return changed if len(calls) >= (3 if after_execution else 2) else ctx

    monkeypatch.setattr(query, "_fresh_authorization", fresh)
    with pytest.raises(Exception) as error:
        query.run(OWN)
    assert error.value.detail["code"] in {"AUTH_CONTEXT_STALE", "AUTH_REVOKED"}
    assert len(executions) == int(after_execution)


@pytest.mark.parametrize(
    "sql,ctx",
    [
        (BASELINE, context(capabilities=("grade_baseline",))),
        (RANK, context("district", capabilities=("district_self_rank",))),
    ],
)
def test_reference_end_to_end_service_keeps_pools_internal(monkeypatch, sql, ctx):
    query, plans, executions = execution_query(monkeypatch, sql, ctx)
    result = query.run(sql)
    assert executions and "public" not in result["sql"]
    assert "physical_sql" not in result


def test_reference_pool_cannot_be_consumed_via_extra_query_path():
    sql = RANK.replace(
        "SELECT r.district_id,ROUND(r.mean_score,2) AS own_mean,r.city_rank,r.units FROM ranks r\nWHERE r.district_id='D1' AND r.units>=3",
        "SELECT r.district_id,r.mean_score,r.city_rank,r.units FROM ranks r",
    )
    with pytest.raises(AuthorizationError):
        prove(sql, context("district", capabilities=("district_self_rank",)))


def test_existing_hard_rule_is_not_exempted_for_baseline():
    ctx = context(capabilities=("grade_baseline",)).model_copy(
        update={"required_row_rules": {"edu_scores": ("subject = 'math'",)}}
    )
    with pytest.raises(AuthorizationError):
        prove(BASELINE, ctx)
    prove(
        BASELINE.replace("AND s.exam_batch_id=901", "AND s.subject='math' AND s.exam_batch_id=901"),
        ctx,
    )


@pytest.mark.parametrize("city_baseline", [False, True])
def test_runtime_model_repairs_missing_permission_without_host_injection(
    monkeypatch, city_baseline
):
    import asyncio
    from contextlib import nullcontext

    from langchain_core.messages import AIMessage
    from langgraph.checkpoint.memory import InMemorySaver

    from src.chat.schemas import ChatRequest
    from src.chat.service import deepagents_runtime as runtime
    from src.common.core.config import Settings
    from tests.chat.test_deepagents_runtime import ToolCallingModel, assessment_ready

    ctx = context("school", capabilities=("city_baseline",)) if city_baseline else context()
    good_sql = (
        (
            "SELECT ROUND(AVG(s.score_value),2) AS city_mean FROM edu_scores s "
            "WHERE s.city_id='C1' AND s.cohort_id='G1' AND s.exam_batch_id=901 "
            "HAVING COUNT(s.score_value)>=10 AND COUNT(DISTINCT s.school_id)>=3"
        )
        if city_baseline
        else OWN
    )
    bad_sql = (
        good_sql.replace(" AS city_mean", " AS city_mean, COUNT(s.score_value) AS n")
        if city_baseline
        else OWN.replace("AND s.class_id='B1'", "")
    )
    query, plans, executions = execution_query(monkeypatch, good_sql, ctx)
    monkeypatch.setattr(query, "authorization_context", lambda: ctx.model_context())
    monkeypatch.setattr(runtime, "SemanticQuery", lambda *_args: query)
    monkeypatch.setattr(
        runtime, "get_settings", lambda: Settings(_env_file=None, chat_max_steps=10)
    )
    monkeypatch.setattr(runtime, "authorization_fingerprint", lambda *_args: "stable")
    monkeypatch.setattr(runtime, "load_conversation_history", lambda *_args: [])
    monkeypatch.setattr(runtime, "_thread_lock", lambda *_args: nullcontext())
    monkeypatch.setattr(runtime, "_planning_required", lambda *_args: False)
    monkeypatch.setattr(runtime, "review_question", assessment_ready)
    monkeypatch.setattr(runtime, "log_tool_call_fire_and_forget", lambda **_kwargs: None)
    events = []

    async def emit(event, data):
        events.append((event, data))

    async def persist(**_kwargs):
        return 1

    monkeypatch.setattr(runtime, "persist_turn", persist)
    model = ToolCallingModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "query_semantic",
                        "args": {"sql": bad_sql},
                        "id": "missing",
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "query_semantic", "args": {"sql": good_sql}, "id": "repaired"}
                ],
            ),
            AIMessage(content="已按本班授权范围完成查询。"),
        ]
    )
    asyncio.run(
        runtime.run_chat_stream(
            request=ChatRequest(datasource_id=4, conversation_id=9, question="本班各科均分"),
            current_user_id=7,
            workspace_oid=1,
            emit=emit,
            checkpointer=InMemorySaver(),
            model=model,
        )
    )
    assert len(plans) == len(executions) == 1
    failures = [
        data for event, data in events if event == "tool_result" and not data.get("success")
    ]
    assert any(
        item.get("data", {}).get("error_detail", {}).get("code")
        == ("AUTH_RELEASE_SHAPE_INVALID" if city_baseline else "AUTH_SCOPE_MISSING")
        for item in failures
    )
    assert any(
        event == "result" and data.get("permission_mode") == "model_explicit_v2"
        for event, data in events
    )
    assert not any("public.scores" in str(data) for _, data in events)


@pytest.mark.parametrize(
    "sql,ctx,expected",
    [
        (BASELINE, context(capabilities=("grade_baseline",)), [["math", 100.0, 70.0, 30.0]]),
        (RANK, context("district", capabilities=("district_self_rank",)), [["D1", 70.0, 2, 3]]),
    ],
)
def test_real_relational_execution_excludes_foreign_city_and_cohort(
    monkeypatch, sql, ctx, expected
):
    import sqlite3

    from src.chat.service import semantic_query as module

    query, _, _ = execution_query(monkeypatch, sql, ctx)
    with sqlite3.connect(":memory:") as db:
        db.execute("ATTACH DATABASE ':memory:' AS public")
        db.execute(
            "CREATE TABLE public.scores (city_id TEXT,district_id TEXT,school_id TEXT,cohort_id TEXT,class_id TEXT,"
            "student_id TEXT,exam_batch_id INTEGER,exam_date TEXT,subject TEXT,score_value REAL)"
        )
        populations = [
            ("C1", "D1", "S1", "G1", "B1", 100),
            ("C1", "D1", "S1", "G1", "B2", 70),
            ("C1", "D1", "S1", "G1", "B3", 40),
            ("C1", "D2", "S2", "G1", "B1", 80),
            ("C1", "D3", "S3", "G1", "B1", 60),
            ("C2", "D4", "S9", "G1", "B1", 1000),
            ("C1", "D1", "S1", "G2", "B1", 9999),
        ]
        for city, district, school, cohort, cls, score in populations:
            db.executemany(
                "INSERT INTO public.scores VALUES (?,?,?,?,?,?,?,?,?,?)",
                [
                    (
                        city,
                        district,
                        school,
                        cohort,
                        cls,
                        f"{city}-{district}-{cohort}-{cls}-{i}",
                        901,
                        "2026-11-10",
                        "math",
                        score,
                    )
                    for i in range(10)
                ],
            )

        def execute(physical_sql, **kwargs):
            assert kwargs["max_fix_attempts"] == 0
            cursor = db.execute(physical_sql)
            return SimpleNamespace(
                success=True,
                result={"columns": [c[0] for c in cursor.description], "rows": cursor.fetchall()},
            )

        monkeypatch.setattr(module, "run_sql_with_auto_fix", execute)
        result = query.run(sql)
    assert result["rows"] == expected


def test_permission_repair_exhaustion_cannot_restart_with_human_retry_loop(monkeypatch):
    import asyncio
    from contextlib import nullcontext

    from langchain_core.messages import AIMessage
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
    monkeypatch.setattr(runtime, "_planning_required", lambda *_: True)
    monkeypatch.setattr(runtime, "review_question", assessment_ready)
    monkeypatch.setattr(runtime, "log_tool_call_fire_and_forget", lambda **_: None)
    events, bindings = [], []

    async def emit(event, data):
        events.append((event, data))

    async def persist(**kwargs):
        return 1

    monkeypatch.setattr(runtime, "persist_turn", persist)

    class RecordingModel(ToolCallingModel):
        def bind_tools(self, tools, **kwargs):
            bindings.append({tool.name for tool in tools})
            return self

    invalid = "SELECT s.score_value FROM edu_scores s"
    model = RecordingModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_todos",
                        "id": "plan",
                        "args": {
                            "todos": [
                                {"content": "查询本校与全市数学均分", "status": "in_progress"},
                                {"content": "计算分差", "status": "pending"},
                            ]
                        },
                    }
                ],
            ),
            *[
                AIMessage(
                    content="",
                    tool_calls=[
                        {"name": "query_semantic", "id": f"invalid-{i}", "args": {"sql": invalid}}
                    ],
                )
                for i in range(3)
            ],
            # Even a provider ignoring the restricted tool list cannot reopen a wait.
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "ask_human",
                        "id": "retry-loop",
                        "args": {
                            "question": "是否继续尝试读取全市基准？",
                            "options": ["继续", "仅本校"],
                        },
                    }
                ],
            ),
            AIMessage(content="本轮SQL表达式未通过校验，未取得全市均分。已有对照能力有效。"),
        ]
    )
    asyncio.run(
        runtime.run_chat_stream(
            request=ChatRequest(
                datasource_id=4, conversation_id=9, question="本校与全市数学均分对照"
            ),
            current_user_id=7,
            workspace_oid=1,
            emit=emit,
            checkpointer=InMemorySaver(),
            model=model,
        )
    )
    assert not plans and not executions
    assert (
        len(
            [
                data
                for event, data in events
                if event == "tool_result" and data.get("tool") == "query_semantic"
            ]
        )
        == 3
    )
    assert not any(event == "clarify" for event, _ in events)
    assert not any("ask_human" in names or "query_semantic" in names for names in bindings[-2:])
    assert any(event == "summary" and "SQL" in data["content"] for event, data in events)
