"""分析工具在 v2 分级权限下的范围/能力行为。"""

from types import SimpleNamespace

import pytest

from src.agent.education.orchestrator import ReportOrchestrator
from src.agent.education.report_types import Audience, ReportSpec, ReportType
from src.datasource.service.education_authorization import EducationGrant, PermissionManifest
from src.datasource.service.education_report_scope import ReportScope


def _manifest() -> PermissionManifest:
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
                "username": "u",
            },
            "relations": {
                "tb_score_overview": {
                    "physical_table": "tb_score_overview",
                    "columns": {k: k for k in ("xx", "dq", "jc", "bj", "anon_stu_id", "exam_batch_id", "zf6m")},
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
            "catalog": [
                {
                    "city_id": "education",
                    "district_id": "D1",
                    "school_id": "A01扬州中学",
                    "cohort_id": "2026届",
                    "class_id": "高三(1)班",
                }
            ],
        }
    )


def _scope(level: str, capabilities=(), **fields) -> ReportScope:
    base = {
        "id": "g1",
        "workspace_oid": 1,
        "datasource_id": 4,
        "level": level,
        "city_id": "education",
        "capabilities": tuple(capabilities),
    }
    base.update(fields)
    grant = EducationGrant(**base)
    return ReportScope(
        user_id=7,
        workspace_oid=1,
        datasource_id=4,
        manifest=_manifest(),
        grants=(grant,),
        levels=frozenset({level}),
        schools=(grant.school_id,) if grant.school_id else (),
        districts=(grant.district_id,) if grant.district_id else (),
        cohorts=(grant.cohort_id,) if grant.cohort_id else (),
        classes=(grant.class_id,) if grant.class_id else (),
        students=(grant.student_id,) if grant.student_id else (),
        capabilities=frozenset(capabilities),
        fingerprint="f" * 64,
    )


def _spec(report_type=ReportType.CLASS_OVERVIEW, **filters) -> ReportSpec:
    return ReportSpec(
        report_type=report_type,
        audience=Audience.DEFAULT,
        filters=filters,
        include_charts=False,
    )


def test_external_class_rank_is_skipped_without_city_scope():
    calls: list[tuple[str, str | None]] = []

    async def execute(sql, reference=None):
        calls.append((sql, reference))
        return {"columns": ["xx", "bj", "xxlb", "n", "avg_score"], "rows": []}

    async def schema():
        raise AssertionError("schema should not be read")

    orch = ReportOrchestrator(
        execute_sql=execute,
        resolve_schema=schema,
        report_scope=_scope("school", school_id="A01扬州中学"),
    )
    import asyncio

    snippet = asyncio.run(
        orch._compute_external_class_rank_snippet(
            _spec(exam_name="期中", school_name="扬州中学"),
            class_names=["高三(1)班"],
        )
    )
    assert snippet == ""
    assert calls == []


def test_external_class_rank_is_a_city_reference_for_city_scope():
    calls: list[tuple[str, str | None]] = []

    async def execute(sql, reference=None):
        calls.append((sql, reference))
        return {"columns": [], "rows": []}

    async def schema():
        raise AssertionError

    orch = ReportOrchestrator(
        execute_sql=execute,
        resolve_schema=schema,
        report_scope=_scope("city"),
    )
    import asyncio

    asyncio.run(
        orch._compute_external_class_rank_snippet(
            _spec(exam_name="期中", school_name="扬州中学"),
            class_names=["高三(1)班"],
        )
    )
    assert calls and calls[0][1] == "city"


def test_fetch_score_rows_passes_reference_to_callback():
    calls: list[str | None] = []

    async def execute(sql, reference=None):
        calls.append(reference)
        return {"columns": ["score"], "rows": [[80]]}

    async def schema():
        raise AssertionError

    orch = ReportOrchestrator(
        execute_sql=execute,
        resolve_schema=schema,
        report_scope=_scope(
            "class",
            school_id="A01扬州中学",
            cohort_id="2026届",
            class_id="高三(1)班",
            capabilities=("grade_baseline",),
        ),
    )
    orch._build_sql = lambda spec, mapping: "SELECT 1"
    import asyncio

    rows = asyncio.run(orch._fetch_score_rows(_spec(), None, reference="grade"))
    assert rows == [{"score": 80}]
    assert calls == ["grade"]


def test_city_position_requires_city_scope_even_with_city_baseline():
    s = _scope(
        "school",
        school_id="A01扬州中学",
        capabilities=("city_baseline",),
    )
    assert s.allows_city_reference()
    assert not s.allows_city_position()


@pytest.mark.parametrize(
    ("path", "blocked"),
    [
        ("/api/v1/education/generate-report", False),
        ("/api/v1/education/meta/options", False),
        ("/api/v1/education/report-history/1", False),
        ("/api/v1/education/dashboards/line-reach", False),
        ("/api/v1/education/anomaly-alerts", False),
        ("/api/v1/education/score-import/execute", True),
        ("/api/v1/education/raw-score-import/batches", True),
        ("/api/v1/education/fraction-bar", True),
    ],
)
def test_migrated_account_endpoint_allowlist(monkeypatch, path, blocked):
    from common.exceptions.base import ForbiddenException
    from system.api import auth_deps

    user = SimpleNamespace(
        id=7,
        status=1,
        system_variables={"edu_auth_v2": {"schema_version": 2, "grants": [{"id": "g"}]}},
    )
    monkeypatch.setattr(auth_deps, "decode_access_token", lambda _token: {"sub": "7"})
    monkeypatch.setattr(auth_deps, "get_user_by_id", lambda *_: user)
    request = SimpleNamespace(url=SimpleNamespace(path=path), method="POST")
    if blocked:
        with pytest.raises(ForbiddenException):
            auth_deps.get_current_user(token="t", session=object(), request=request)
    else:
        assert (
            auth_deps.get_current_user(token="t", session=object(), request=request)
            is user
        )
