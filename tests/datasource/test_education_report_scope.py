"""v2 分析工具范围：SQL 下推、能力门槛与请求点名校验。"""

from types import SimpleNamespace

import pytest
from sqlglot import exp, parse_one

from src.datasource.service.education_authorization import (
    AUTH_KEY,
    EducationGrant,
    PermissionManifest,
)
from src.datasource.service.education_report_scope import (
    ReportScope,
    ReportScopeError,
    load_report_scope,
)

CATALOG = [
    {
        "city_id": "education",
        "district_id": "D1",
        "school_id": "A01扬州中学",
        "cohort_id": "2026届",
        "class_id": "高三(1)班",
    },
    {
        "city_id": "education",
        "district_id": "D1",
        "school_id": "A01扬州中学",
        "cohort_id": "2026届",
        "class_id": "高三(2)班",
    },
    {
        "city_id": "education",
        "district_id": "D2",
        "school_id": "B02新华中学",
        "cohort_id": "2026届",
        "class_id": "高三(1)班",
    },
]


def manifest() -> PermissionManifest:
    return PermissionManifest.model_validate(
        {
            "workspace_oid": 1,
            "datasource_id": 4,
            "bundle_id": "edu",
            "manifest_hash": "a" * 64,
            "connection": {
                "host": "localhost",
                "port": 5432,
                "database": "edu",
                "username": "readonly",
            },
            "relations": {
                "tb_score_overview": {
                    "physical_table": "tb_score_overview",
                    "columns": {
                        key: key
                        for key in (
                            "xx",
                            "dq",
                            "jc",
                            "bj",
                            "anon_stu_id",
                            "exam_batch_id",
                            "zf6m",
                        )
                    },
                    "scope_columns": {
                        "district_id": "dq",
                        "school_id": "xx",
                        "cohort_id": "jc",
                        "class_id": "bj",
                        "student_id": "anon_stu_id",
                    },
                    "implicit_scope": {"city_id": "education"},
                    "metrics": {"mean_zf6m": "AVG(zf6m)"},
                    "metric_samples": {"mean_zf6m": "zf6m"},
                    "exam_column": "exam_batch_id",
                    "subject_column": None,
                    "sample_column": "zf6m",
                }
            },
            "catalog": CATALOG,
        }
    )


def grant(level: str, **fields) -> EducationGrant:
    base = {
        "id": f"g-{level}",
        "workspace_oid": 1,
        "datasource_id": 4,
        "level": level,
        "city_id": "education",
    }
    base.update(fields)
    return EducationGrant(**base)


def scope(*grants, capabilities=()) -> ReportScope:
    caps = frozenset(capabilities or (cap for g in grants for cap in g.capabilities))
    return ReportScope(
        user_id=7,
        workspace_oid=1,
        datasource_id=4,
        manifest=manifest(),
        grants=tuple(grants),
        levels=frozenset(g.level for g in grants),
        schools=tuple(sorted({g.school_id for g in grants if g.school_id})),
        districts=tuple(sorted({g.district_id for g in grants if g.district_id})),
        cohorts=tuple(sorted({g.cohort_id for g in grants if g.cohort_id})),
        classes=tuple(sorted({g.class_id for g in grants if g.class_id})),
        students=tuple(sorted({g.student_id for g in grants if g.student_id})),
        capabilities=caps,
        fingerprint="f" * 64,
    )


def test_class_scope_filters_overview_and_score_tables():
    s = scope(
        grant(
            "class",
            school_id="A01扬州中学",
            cohort_id="2026届",
            class_id="高三(1)班",
        )
    )
    sql = s.apply_sql(
        "SELECT xx, bj, AVG(zf6m) AS a FROM tb_score_overview o GROUP BY xx, bj",
        "pg",
    )
    clauses = [node.sql() for node in parse_one(sql).find_all(exp.Where)]
    assert any('"xx" =' in sql and '"bj" =' in sql for _ in clauses)
    assert "A01扬州中学" in sql and "高三(1)班" in sql

    score_sql = s.apply_sql(
        "SELECT sc.class, sc.student_id FROM tb_score sc "
        "JOIN tb_school sch ON sc.school_id = sch.id",
        "pg",
    )
    assert 'sc."student_id" IN (SELECT "anon_stu_id" FROM tb_score_overview' in score_sql


def test_city_scope_injects_nothing():
    s = scope(grant("city"))
    sql = "SELECT xx FROM tb_score_overview"
    assert s.apply_sql(sql, "pg") == sql
    assert s.overview_predicate() is None


def test_district_scope_uses_district_column():
    s = scope(grant("district", district_id="D1"))
    sql = s.apply_sql("SELECT xx FROM tb_score_overview", "pg")
    assert '"dq" = \'D1\'' in sql
    assert s.allowed_district_ids() == ("D1",)


def test_school_alias_matches_code_prefixed_names():
    s = scope(grant("school", school_id="A01扬州中学"))
    assert s.matches_school("扬州中学")
    assert s.matches_school("A01扬州中学")
    assert not s.matches_school("新华中学")
    with pytest.raises(ReportScopeError):
        s.assert_in_scope(school_name="B02新华中学")
    s.assert_in_scope(school_name="扬州中学")


def test_class_request_outside_grants_is_rejected():
    s = scope(
        grant(
            "class",
            school_id="A01扬州中学",
            cohort_id="2026届",
            class_id="高三(1)班",
        )
    )
    s.assert_in_scope(class_name="高三(1)班")
    with pytest.raises(ReportScopeError):
        s.assert_in_scope(class_name="高三(2)班")


def test_city_reference_requires_capability_but_city_scope_bypasses():
    s = scope(grant("school", school_id="A01扬州中学"))
    with pytest.raises(ReportScopeError):
        s.apply_sql("SELECT xx FROM tb_score_overview", "pg", reference="city")
    city = scope(grant("city"))
    sql = "SELECT xx FROM tb_score_overview"
    assert city.apply_sql(sql, "pg", reference="city") == sql


def test_grade_reference_requires_grade_baseline_and_drops_class_filter():
    s = scope(
        grant(
            "class",
            school_id="A01扬州中学",
            cohort_id="2026届",
            class_id="高三(1)班",
        )
    )
    with pytest.raises(ReportScopeError):
        s.apply_sql("SELECT xx FROM tb_score_overview", "pg", reference="grade")
    allowed = scope(
        grant(
            "class",
            school_id="A01扬州中学",
            cohort_id="2026届",
            class_id="高三(1)班",
            capabilities=("grade_baseline",),
        )
    )
    sql = allowed.apply_sql(
        "SELECT xx FROM tb_score_overview", "pg", reference="grade"
    )
    assert '"xx" = \'A01扬州中学\'' in sql
    assert '"jc" = \'2026届\'' in sql
    assert '"bj"' not in sql


def test_indicator_rows_and_catalog_are_filtered_by_scope():
    s = scope(grant("school", school_id="A01扬州中学"))
    rows = [
        {"school_name": "扬州中学", "district": "D1"},
        {"school_name": "新华中学", "district": "D2"},
    ]
    assert s.filter_indicator_rows(rows) == [rows[0]]
    assert s.allowed_district_ids() == ("D1",)


def test_load_report_scope_returns_none_for_legacy_account():
    user = SimpleNamespace(id=7, status=1, system_variables={})
    assert load_report_scope(SimpleNamespace(), user, 1, 4) is None


def test_load_report_scope_rejects_migrated_account_without_active_grants(monkeypatch):
    user = SimpleNamespace(id=7, status=1, system_variables={AUTH_KEY: {"schema_version": 2}})
    from src.datasource.service import education_source

    monkeypatch.setattr(
        education_source, "permission_manifest_for_user", lambda *a, **k: manifest()
    )
    with pytest.raises(ReportScopeError):
        load_report_scope(SimpleNamespace(), user, 1, 4)


def test_cte_and_union_branches_are_all_scoped():
    s = scope(
        grant(
            "class",
            school_id="A01扬州中学",
            cohort_id="2026届",
            class_id="高三(1)班",
        )
    )
    sql = (
        "WITH one AS (SELECT xx FROM tb_score_overview) "
        "SELECT * FROM one UNION ALL SELECT xx FROM tb_score_overview"
    )
    scoped = s.apply_sql(sql, "pg")
    parsed = parse_one(scoped)
    assert sum(1 for _ in parsed.find_all(exp.Where)) == 2


def test_detail_table_is_scoped_through_overview_students():
    s = scope(grant("school", school_id="A01扬州中学"))
    scoped = s.apply_sql(
        "SELECT sd.question_no, COUNT(*) FROM tb_score_detail sd GROUP BY sd.question_no",
        "pg",
    )
    assert 'sd."student_id" IN (SELECT "anon_stu_id" FROM tb_score_overview' in scoped


def test_district_reference_requires_district_grant():
    from dataclasses import replace

    s = replace(
        scope(grant("school", school_id="A01扬州中学")),
        capabilities=frozenset({"district_self_rank"}),
    )
    with pytest.raises(ReportScopeError):
        s.apply_sql("SELECT xx FROM tb_score_overview", "pg", reference="district")


def test_expired_grant_is_rejected_at_execution_time():
    from dataclasses import replace
    from datetime import datetime, timezone

    expired = grant("school", school_id="A01扬州中学").model_copy(
        update={"valid_to": datetime(2020, 1, 1, tzinfo=timezone.utc)}
    )
    s = replace(scope(expired), grants=(expired,))
    with pytest.raises(ReportScopeError):
        s.assert_active()
