from types import SimpleNamespace

import pytest
from sqlglot import parse_one

from src.chat.service.semantic_permission_validator import PermissionValidator
from src.common.core.config import DataSemanticBinding
from src.datasource.service import education_source as source
from src.datasource.service.education_authorization import (
    AUTH_KEY,
    AuthorizationError,
    EducationAuthorization,
    EducationGrant,
    context_for,
    normalize_education_grant,
    read_authorization,
)


@pytest.fixture
def fixed_source(monkeypatch):
    settings = SimpleNamespace(
        database_edu_url="postgresql://readonly:secret@edu-host:5432/edu",
        datasemantic_bindings={1: DataSemanticBinding(bundle_id="edu", manifest_hash="a" * 64)},
        datasemantic_datasource_id=None,
        datasemantic_bundle_id="",
        datasemantic_manifest_hash="",
    )
    fields = [
        "xx",
        "dq",
        "jc",
        "bj",
        "exam_batch_id",
        "anon_stu_id",
        "zf6m",
        "yw",
        "sx",
        "xm",
        "sfzh",
        "ksh",
    ]
    monkeypatch.setattr(
        source,
        "_schema",
        lambda *_: [{"name": "tb_score_overview", "fields": [{"name": f} for f in fields]}],
    )
    from src.datasource.db import db

    queries = []

    def execute(kind, config, sql):
        queries.append((config, sql))
        return (
            True,
            "ok",
            {
                "columns": ["school_id", "district_id", "cohort_id", "class_id"],
                "rows": [["S1", "D1", "2027", "高三(1)班"], ["S1", "D1", "2027", "高三(2)班"]],
            },
        )

    monkeypatch.setattr(db, "execute_sql", execute)
    source._CACHE.clear()
    yield settings, queries
    source._CACHE.clear()


def test_single_source_comes_from_env_without_registered_datasource_or_mapping_file(fixed_source):
    settings, queries = fixed_source
    info = source.source_info(9, settings=settings)
    assert info["datasource_id"] == 1 and info["source_type"] == "DATABASE_EDU_URL"
    assert "secret" not in str(info) and "edu-host" not in str(info)
    policy = source.environment_manifest(9, settings=settings)
    assert policy.workspace_oid == 9
    assert policy.relations["tb_score_overview"].scope_columns["class_id"] == "bj"
    assert len(policy.catalog) == 2 and policy.catalog[0]["city_id"] == info["city_id"]
    assert queries[0][0]["read_only"]
    assert "score" not in queries[0][1].replace("tb_score_overview", "")
    assert "anon_stu_id" not in queries[0][1]
    assert source.environment_manifest(9, settings=settings) is policy and len(queries) == 1
    source.environment_manifest(9, settings=settings, refresh=True)
    assert len(queries) == 2


def test_another_requested_datasource_cannot_override_env(fixed_source):
    settings, queries = fixed_source
    with pytest.raises(AuthorizationError):
        source.environment_manifest(1, 42, settings=settings)
    assert not queries


@pytest.mark.parametrize(
    "new_url",
    [
        "postgresql://readonly:secret@new-host:5432/edu",
        "postgresql://readonly:secret@edu-host:6432/edu",
        "postgresql://readonly:secret@edu-host:5432/other",
        "postgresql://new-user:secret@edu-host:5432/edu",
        "postgresql://readonly:rotated-password@edu-host:5432/edu",
        "postgresql://new-user:rotated-password@new-host:6432/other",
    ],
)
def test_connection_changes_reload_metadata_without_changing_grants(fixed_source, new_url):
    settings, queries = fixed_source
    first = source.environment_manifest(1, settings=settings)
    grant = EducationGrant(
        id="g1",
        workspace_oid=1,
        datasource_id=1,
        level="class",
        city_id=first.catalog[0]["city_id"],
        school_id="S1",
        cohort_id="2027",
        class_id="高三(1)班",
    )
    original = EducationAuthorization(
        revision=7, grants=(grant,), managed_resources=("1:1",)
    ).model_dump(mode="json")
    actor = SimpleNamespace(id=7, status=1, system_variables={AUTH_KEY: original})
    old_context = context_for(actor, 1, 1, first)
    settings.database_edu_url = new_url
    current = source.environment_manifest(1, settings=settings)
    assert len(queries) == 2  # Connection changed: reload from the new destination.
    assert current is not first
    assert source.source_info(1, settings=settings)["city_id"] == "education"
    new_context = context_for(actor, 1, 1, current)
    assert old_context.grants == new_context.grants
    assert actor.system_variables[AUTH_KEY] == original
    sql = "SELECT AVG(s.yw) AS mean_yw FROM tb_score_overview s WHERE s.xx='S1' AND s.jc='2027' AND s.bj='高三(1)班'"
    assert PermissionValidator(new_context).prove(parse_one(sql)).scans
    with pytest.raises(AuthorizationError):
        PermissionValidator(new_context).prove(parse_one(sql.replace("高三(1)班", "高三(2)班")))


def test_old_connection_hash_grants_remain_valid_without_rewriting_storage(fixed_source):
    settings, _ = fixed_source
    legacy_id = "education-0123456789abcdef"
    grant = EducationGrant(
        id="old-g1",
        workspace_oid=1,
        datasource_id=1,
        level="class",
        city_id=legacy_id,
        school_id="S1",
        cohort_id="2027",
        class_id="高三(1)班",
        capabilities=("grade_baseline",),
        can_delegate=True,
    )
    document = EducationAuthorization(revision=9, grants=(grant,)).model_dump(mode="json")
    user = SimpleNamespace(id=7, status=1, system_variables={AUTH_KEY: document})
    settings.database_edu_url = "postgresql://migrated:new-password@new-host:6432/new_edu"
    policy = source.environment_manifest(1, settings=settings)
    context = context_for(user, 1, 1, policy)
    assert context.grants[0].city_id == "education"
    assert context.grants[0].model_dump(exclude={"city_id"}) == grant.model_dump(
        exclude={"city_id"}
    )
    assert context.document.revision == 9
    assert user.system_variables[AUTH_KEY] == document
    assert read_authorization(user).grants[0].city_id == legacy_id
    for changes in ({"workspace_oid": 2}, {"datasource_id": 99}, {"city_id": "another-city"}):
        unrelated = grant.model_copy(update=changes)
        assert normalize_education_grant(unrelated, policy) == unrelated
    custom = policy.model_copy(
        update={
            "relations": {
                "tb_score_overview": policy.relations["tb_score_overview"].model_copy(
                    update={"implicit_scope": {"city_id": "another-resource"}}
                )
            }
        }
    )
    assert normalize_education_grant(grant, custom) == grant


def test_wide_table_scope_is_explicit_except_connection_bound_city(fixed_source):
    settings, _ = fixed_source
    manifest = source.environment_manifest(1, settings=settings)
    grant = EducationGrant(
        id="g1",
        workspace_oid=1,
        datasource_id=1,
        level="class",
        city_id=manifest.catalog[0]["city_id"],
        school_id="S1",
        cohort_id="2027",
        class_id="高三(1)班",
        capabilities=("grade_baseline",),
    )
    actor = SimpleNamespace(
        id=7,
        status=1,
        system_variables={
            AUTH_KEY: EducationAuthorization(grants=(grant,)).model_dump(mode="json")
        },
    )
    ctx = context_for(actor, 1, 1, manifest)
    sql = "SELECT AVG(s.yw) AS mean_yw FROM tb_score_overview s WHERE s.xx='S1' AND s.jc='2027' AND s.bj='高三(1)班' AND s.exam_batch_id=1"
    assert PermissionValidator(ctx).prove(parse_one(sql)).scans
    with pytest.raises(AuthorizationError):
        PermissionValidator(ctx).prove(parse_one(sql.replace("AND s.bj='高三(1)班'", "")))
    baseline = "SELECT ROUND(AVG(s.yw),2) AS mean_yw FROM tb_score_overview s WHERE s.xx='S1' AND s.jc='2027' AND s.exam_batch_id=1 HAVING COUNT(s.yw)>=10 AND COUNT(DISTINCT s.bj)>=3"
    assert PermissionValidator(ctx).prove(parse_one(baseline)).releases
    with pytest.raises(AuthorizationError):
        PermissionValidator(ctx).prove(parse_one(baseline.replace("COUNT(s.yw)", "COUNT(s.zf6m)")))


def test_missing_structure_is_reported_instead_of_guessing(fixed_source, monkeypatch):
    settings, _ = fixed_source
    monkeypatch.setattr(source, "_schema", lambda *_: [])
    with pytest.raises(AuthorizationError, match="tb_score_overview"):
        source.environment_manifest(1, settings=settings)


CITY_MEAN = """SELECT ROUND(AVG(s.sx),2) AS city_mean_sx
FROM tb_score_overview s WHERE s.exam_batch_id=2 AND s.jc=2027
HAVING COUNT(s.sx)>=10 AND COUNT(DISTINCT s.xx)>=3"""


def school_city_context(fixed_source):
    settings, _ = fixed_source
    manifest = source.environment_manifest(1, settings=settings)
    grant = EducationGrant(
        id="school",
        workspace_oid=1,
        datasource_id=1,
        level="school",
        city_id="education",
        school_id="S1",
        capabilities=("city_baseline",),
    )
    actor = SimpleNamespace(
        id=7,
        status=1,
        system_variables={
            AUTH_KEY: EducationAuthorization(revision=3, grants=(grant,)).model_dump(mode="json")
        },
    )
    return context_for(actor, 1, 1, manifest)


def school_city_comparison():
    return (
        """WITH school_mean AS (
    SELECT ROUND(AVG(s.sx),2) AS school_mean_sx FROM tb_score_overview s
    WHERE s.xx='S1' AND s.exam_batch_id=2 AND s.jc=2027
    ), city_mean AS ("""
        + CITY_MEAN
        + """)
    SELECT o.school_mean_sx,c.city_mean_sx,o.school_mean_sx-c.city_mean_sx AS gap
    FROM school_mean o CROSS JOIN city_mean c"""
    )


@pytest.mark.parametrize(
    "sql,reason",
    [
        (
            CITY_MEAN.replace("HAVING", "AND s.sx IS NOT NULL HAVING"),
            "reference_population_filter_not_allowed",
        ),
        (
            CITY_MEAN.replace("AS city_mean_sx", "AS city_mean_sx, COUNT(s.sx) AS score_n"),
            "baseline_count_output_not_allowed",
        ),
        (CITY_MEAN.replace("HAVING", "GROUP BY s.jc HAVING"), None),
        (CITY_MEAN.replace("AVG(s.sx)", "AVG(CASE WHEN s.xx='S1' THEN s.sx END)"), None),
    ],
)
def test_school_city_baseline_failures_explain_sql_repair_not_missing_grant(
    fixed_source, sql, reason
):
    ctx = school_city_context(fixed_source)
    with pytest.raises(AuthorizationError) as caught:
        PermissionValidator(ctx).prove(parse_one(sql))
    detail = caught.value.detail
    assert detail["failure_stage"] == "sql_validation" and detail["sql_repair_required"]
    assert detail["granted_capabilities"] == ["city_baseline"]
    if reason:
        assert detail["reason"] == reason
    contract = detail["reference_query_contracts"]["tb_score_overview"]
    assert not contract["baseline_count_output_allowed"]
    assert contract["aggregate_expressions"]["mean_sx"] == "AVG(sx)"
    assert not contract["metric_identifiers_are_fields"]
    assert contract["allowed_baseline_group_columns"] == ["exam_batch_id"]
    if reason == "reference_population_filter_not_allowed":
        assert "sx IS NULL" in detail["disallowed_filters"][0]


def test_city_capability_does_not_release_other_school_details(fixed_source):
    ctx = school_city_context(fixed_source)
    with pytest.raises(AuthorizationError):
        PermissionValidator(ctx).prove(
            parse_one(
                "SELECT s.xx,s.sx FROM tb_score_overview s WHERE s.exam_batch_id=2 AND s.jc=2027"
            )
        )
    ordinary = ctx.grants[0].model_copy(update={"capabilities": ()})
    with pytest.raises(AuthorizationError):
        PermissionValidator(ctx.model_copy(update={"grants": (ordinary,)})).prove(
            parse_one(CITY_MEAN)
        )


def test_city_comparison_shared_compiler_source_preserves_scope_and_values(fixed_source):
    import sqlite3

    from src.chat.service.semantic_permission_validator import verify_compiled_permissions

    ctx = school_city_context(fixed_source)
    sql = school_city_comparison()
    proof = PermissionValidator(ctx).prove(parse_one(sql))
    # Same shared source and ROUND widening cast shape as the real Wren plan.
    compiled = (
        sql.replace("tb_score_overview s", "shared_scores s")
        .replace(
            "WITH school_mean",
            """WITH shared_scores AS (
        SELECT p.sx AS sx,p.xx AS xx,p.jc AS jc,p.exam_batch_id AS exam_batch_id
        FROM tb_score_overview p), school_mean""",
        )
        .replace("ROUND(AVG(s.sx),", "ROUND(CAST(AVG(s.sx) AS DECIMAL),")
    )
    verify_compiled_permissions(parse_one(compiled), ctx, proof)
    with sqlite3.connect(":memory:") as db:
        db.execute(
            "CREATE TABLE tb_score_overview (xx TEXT,jc INTEGER,exam_batch_id INTEGER,sx REAL)"
        )
        db.executemany(
            "INSERT INTO tb_score_overview VALUES (?,?,?,?)",
            [
                (school, cohort, exam, score)
                for school, cohort, exam, score in [
                    ("S1", 2027, 2, 100),
                    ("S2", 2027, 2, 80),
                    ("S3", 2027, 2, 60),
                    ("S1", 2026, 2, 1000),
                    ("S1", 2027, 3, 2000),
                    ("S3", 2027, 2, None),
                ]
                for _ in range(10)
            ],
        )
        assert db.execute(sql).fetchall() == db.execute(compiled).fetchall() == [(100, 80, 20)]
    for changed in [
        compiled.replace("s.jc=2027", "s.jc=2026"),
        compiled.replace("HAVING COUNT(s.sx)>=10 AND COUNT(DISTINCT s.xx)>=3", ""),
        compiled.replace("s.xx='S1'", "s.xx='S2'"),
        compiled
        + " UNION ALL SELECT t.sx,t.sx,t.sx FROM shared_scores t WHERE t.jc=2027 AND t.exam_batch_id=2",
    ]:
        with pytest.raises(AuthorizationError):
            verify_compiled_permissions(parse_one(changed), ctx, proof)
