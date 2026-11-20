"""权限分层学情总览 API 测试。"""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from common.router import register_routers
from src.agent.education import overview_api
from src.agent.education.overview import line_stats_for_totals
from src.datasource.service.education_authorization import EducationGrant, PermissionManifest
from src.datasource.service.education_report_scope import ReportScope
from system.api.auth_deps import get_current_user
from system.schemas import UserResponse
from system.workspace_scope import get_workspace_oid


def test_line_stats_collapses_same_line_name_across_tracks():
    candidates, stats = line_stats_for_totals(
        [
            {"line_name": "特控线", "threshold": 520, "track": "物理类"},
            {"line_name": "特控线", "threshold": 500, "track": "历史类"},
        ],
        [
            {"total": 600, "track": "物理类"},
            {"total": 450, "track": "历史类"},
        ],
    )
    assert candidates == 2
    assert len(stats) == 1
    assert stats[0]["reached"] == 1
    assert stats[0]["rate"] == 50.0


def _manifest() -> PermissionManifest:
    columns = {
        key: key
        for key in (
            "xx",
            "dq",
            "jc",
            "bj",
            "anon_stu_id",
            "exam_name",
            "zf6m",
            "yw",
            "sx",
            "yy",
            "xkkm",
        )
    }
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
                    "columns": columns,
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
                    "exam_column": "exam_name",
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
                },
                {
                    "city_id": "education",
                    "district_id": "D1",
                    "school_id": "A01扬州中学",
                    "cohort_id": "2026届",
                    "class_id": "高三(2)班",
                },
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


def _client(monkeypatch, scope: ReportScope, execute_sql=None):
    app = FastAPI()
    register_routers(app)
    app.dependency_overrides[get_current_user] = lambda: UserResponse(
        id=7,
        account="teacher",
        name="Teacher",
        email=None,
        oid=1,
        status=1,
        language="zh-CN",
        origin=0,
        create_time=0,
    )
    app.dependency_overrides[get_workspace_oid] = lambda: 1
    monkeypatch.setattr(
        overview_api,
        "_exam_line_tool_context",
        lambda _user: (4, 1, SimpleNamespace(edu_role="teacher")),
    )
    monkeypatch.setattr(overview_api, "_load_report_scope", lambda *a, **k: scope)

    class _Execute:
        async def __call__(self, sql: str, reference: str | None = None):
            if "tb_exam_batch" in sql:
                return {
                    "columns": ["batch_name", "exam_time", "jc", "exam_type"],
                    "rows": [
                        ["2026届高三12月月考", "2025-12-08", "2026", "city"],
                        ["2026届高三11月期中", "2025-11-18", "2026", "city"],
                        ["2026届高三9月月考", "2025-09-15", "2026", "city"],
                        ["2026届高三10月月考", "2025-10-10", "2026", "city"],
                        ["2025届高三5月月考", "2025-05-10", "2025", "city"],
                    ],
                }
            if "SELECT DISTINCT" in sql and "tb_score_overview" in sql:
                return {
                    "columns": ["exam_name", "track"],
                    "rows": [[name, "物理类"] for name in [
                        "2026届高三9月月考", "2026届高三10月月考", "2026届高三11月期中",
                        "2026届高三12月月考",
                    ]],
                }
            if reference is None and "tb_fraction_bar" in sql:
                return {
                    "columns": ["exam_name", "line_name", "threshold", "track"],
                    "rows": [
                        ["2026届高三11月期中", "特控线", 520, "物理类"],
                        ["2026届高三11月期中", "本科线", 450, "物理类"],
                        ["2026届高三9月月考", "特控线", 590, "物理类"],
                    ],
                }
            if "select *" in sql.lower() and "limit 1" in sql.lower():
                return {
                    "columns": [
                        "xx",
                        "dq",
                        "jc",
                        "bj",
                        "anon_stu_id",
                        "exam_name",
                        "zf6m",
                        "yw",
                        "sx",
                        "yy",
                        "xkkm",
                    ],
                    "rows": [],
                }
            if "group by 1, 2, 3" in sql.lower():
                return {
                    "columns": ["district", "school_name", "track", "candidates", "r0", "r1"],
                    "rows": [
                        ["D1", "高三(1)班", "", 30, 30, 30],
                        ["D1", "高三(2)班", "", 22, 20, 22],
                    ],
                }
            if "student_id" in sql.lower() and "order by" in sql.lower():
                if "2026届高三11月期中" not in sql and "AS total" in sql:
                    assert all(value in sql for value in ["高三(1)班", "2026届", "LIKE '物%'"])
                return {
                    "columns": [
                        "student_id",
                        "total",
                        "track",
                        "class_name",
                        "yw",
                        "sx",
                        "yy",
                    ],
                    "rows": [
                        ["s1", 600, "物理类", "高三(1)班", 120, 130, 110],
                        ["s2", 580, "物理类", "高三(1)班", 115, 125, 110],
                    ],
                }
            if "exam_name" in sql.lower() and "total" in sql.lower():
                return {
                    "columns": ["exam_name", "total", "yw", "sx", "yy"],
                    "rows": [
                        ["2026届高三11月期中", 600, 120, 130, 110],
                    ],
                }
            return {"columns": [], "rows": []}

    execute = _Execute()

    class _Orch:
        _execute_sql = execute

        async def _execute_reference(self, sql: str, reference: str):
            if reference == "grade":
                return {
                    "columns": ["avg", "n", "units"],
                    "rows": [[580.0, 40, 3]],
                }
            return await execute(sql)

    orchestrator = _Orch() if execute_sql is None else SimpleNamespace(_execute_sql=execute_sql)
    monkeypatch.setattr(overview_api, "_build_orchestrator", lambda *a, **k: orchestrator)
    return TestClient(app)


def test_overview_school_uses_class_dimension(monkeypatch):
    client = _client(monkeypatch, _scope("school", school_id="A01扬州中学"))
    resp = client.get("/api/v1/education/overview", params={"exam_name": "2026届高三11月期中"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["level"] == "school"
    assert data["chart"]["dimension"] == "class"
    assert [row["label"] for row in data["chart"]["rows"]] == ["高三(1)班", "高三(2)班"]
    assert data["kpis"]["candidates"] == 52
    assert data["scope"]["label"] == "扬州中学"


def test_overview_district_uses_school_dimension(monkeypatch):
    client = _client(monkeypatch, _scope("district", district_id="D1"))
    resp = client.get("/api/v1/education/overview", params={"exam_name": "2026届高三11月期中"})
    data = resp.json()["data"]
    assert data["level"] == "district"
    assert data["chart"]["dimension"] == "school"
    assert data["chart"]["rows"]


def test_overview_grade_uses_class_dimension(monkeypatch):
    client = _client(
        monkeypatch,
        _scope("grade", school_id="A01扬州中学", cohort_id="2026届"),
    )
    resp = client.get("/api/v1/education/overview", params={"exam_name": "2026届高三11月期中"})
    data = resp.json()["data"]
    assert data["level"] == "grade"
    assert data["chart"]["dimension"] == "class"
    assert len(data["chart"]["rows"]) == 2


def test_overview_class_returns_students_and_grade_baseline(monkeypatch):
    client = _client(
        monkeypatch,
        _scope(
            "class",
            school_id="A01扬州中学",
            cohort_id="2026届",
            class_id="高三(1)班",
            capabilities=("grade_baseline",),
        ),
    )
    resp = client.get("/api/v1/education/overview", params={
        "exam_name": "2026届高三11月期中", "track": "物理类",
        "cohort": "2026届", "class_name": "高三(1)班",
    })
    data = resp.json()["data"]
    assert data["level"] == "class"
    assert data["chart"]["dimension"] == "student"
    assert [row["label"] for row in data["chart"]["rows"]] == ["s1", "s2"]
    assert data["secondary"]["grade_baseline"]["avg"] == 580.0
    assert data["secondary"]["grade_baseline"]["gap"] == 10.0
    trends = data["secondary"]["class_trends"]
    assert [point["date"] for point in trends] == ["2025-09-15", "2025-10-10", "2025-11-18"]
    assert trends[0]["special"] == {
        "rate": 50.0, "reached": 1, "candidates": 2,
        "thresholds": [{"track": "物理类", "score": 590}],
    }
    assert trends[1]["special"] is None
    assert trends[2]["special"]["rate"] == 100.0
    assert trends[2]["avg"] == 590.0
    assert any(item["code"] == "class_scope" for item in data["notices"])
    dumped = str(data)
    assert '"xm"' not in dumped
    assert '"s_name"' not in dumped


def test_overview_class_without_grade_baseline_reports_capability(monkeypatch):
    client = _client(
        monkeypatch,
        _scope(
            "class",
            school_id="A01扬州中学",
            cohort_id="2026届",
            class_id="高三(1)班",
        ),
    )
    resp = client.get("/api/v1/education/overview", params={"exam_name": "2026届高三11月期中"})
    data = resp.json()["data"]
    assert any(item["code"] == "capability_missing" for item in data["notices"])
    assert "grade_baseline" not in data["secondary"]


@pytest.fixture
def scored_exams_db():
    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.executescript("""
        CREATE TABLE tb_score_overview (
            xx TEXT, dq TEXT, jc TEXT, bj TEXT, anon_stu_id TEXT,
            exam_name TEXT, zf6m REAL, sx REAL, xkkm TEXT
        );
        CREATE TABLE tb_exam_batch (batch_name TEXT, exam_time TEXT, jc TEXT, exam_type TEXT);
        CREATE TABLE tb_fraction_bar (exam_name TEXT, line_name TEXT, threshold REAL, track TEXT);

        INSERT INTO tb_exam_batch VALUES
            ('2027届高三9月月考', '2026-09-10', '2027', 'city'),
            ('2027届高三11月期中', '2026-11-10', '2027', 'city'),
            ('2027届高三11月校考', '2026-11-16', '2027', 'school'),
            ('2027届高三11月模拟', '2026-11-18', '2027', 'city'),
            ('2026届补考', '2026-11-19', '2026', 'city');
        INSERT INTO tb_score_overview VALUES
            ('A01扬州中学', 'D1', '2027届', '高三(1)班', 's1', '2027届高三9月月考', 600, 110, '物化生'),
            ('A01扬州中学', 'D1', '2027届', '高三(1)班', 's1', '2027届高三11月期中', 610, 120, '物化生'),
            ('A01扬州中学', 'D1', '2027届', '高三(2)班', 's2', '2027届高三11月校考', 620, 115, '史政地'),
            ('A01扬州中学', 'D1', '2027届', '高三(1)班', 's1', '2027届高三11月模拟', NULL, NULL, '物化生'),
            ('A01扬州中学', 'D1', '2026届', '高三(1)班', 's3', '2026届补考', 630, 130, '物化生');
        INSERT INTO tb_fraction_bar VALUES
            ('2027届高三9月月考', '本科线', 500, '物理类'),
            ('2027届高三11月模拟', '本科线', 500, '物理类'),
            ('2026届补考', '本科线', 500, '物理类');
    """)
    yield db
    db.close()


def _scored_exams_client(monkeypatch, db, level: str, **fields):
    scope = _scope(level, school_id="A01扬州中学", cohort_id="2027届", **fields)

    async def execute(sql: str):
        cursor = db.execute(scope.apply_sql(sql, "postgres"))
        return {"columns": [column[0] for column in cursor.description], "rows": cursor.fetchall()}

    return _client(monkeypatch, scope, execute)


def test_class_overview_exams_follow_scope_and_default_to_latest_scored_exam(monkeypatch, scored_exams_db):
    client = _scored_exams_client(monkeypatch, scored_exams_db, "class", class_id="高三(1)班")
    path = "/api/v1/education/overview"
    data = client.get(path).json()["data"]
    assert data["filters"]["exams"] == ["2027届高三11月期中", "2027届高三9月月考"]
    assert data["exam_name"] == "2027届高三11月期中"
    assert data["kpis"] == {"candidates": 1, "by_line": []}
    assert [row["total"] for row in data["chart"]["rows"]] == [610]

    old = client.get(path, params={"exam_name": "2027届高三9月月考"}).json()["data"]
    assert old["exam_name"] == "2027届高三9月月考"
    assert old["kpis"]["by_line"][0]["reached"] == 1

    unauthorized = client.get(path, params={"exam_name": "2026届补考"}).json()["data"]
    assert unauthorized["exam_name"] == data["exam_name"]
    assert unauthorized["chart"]["rows"] == data["chart"]["rows"]

    empty = client.get(path, params={"cohort": "2026届"}).json()["data"]
    assert empty["exam_name"] is None
    assert empty["filters"]["exams"] == []
    assert empty["chart"]["rows"] == []


def test_grade_overview_retains_counts_when_latest_exam_has_no_lines(monkeypatch, scored_exams_db):
    client = _scored_exams_client(monkeypatch, scored_exams_db, "grade")
    data = client.get("/api/v1/education/overview").json()["data"]
    assert data["exam_name"] == "2027届高三11月校考"
    assert data["kpis"] == {"candidates": 1, "by_line": []}
    assert data["chart"]["rows"][0]["label"] == "高三(2)班"
    assert "no_lines" in {item["code"] for item in data["notices"]}


def test_personal_overview_defaults_to_latest_and_all_exams_remains_available(monkeypatch, scored_exams_db):
    client = _scored_exams_client(monkeypatch, scored_exams_db, "student", student_id="s1")
    path = "/api/v1/education/overview"
    latest = client.get(path).json()["data"]
    assert latest["exam_name"] == "2027届高三11月期中"
    assert [row["total"] for row in latest["chart"]["rows"]] == [610]
    history = client.get(path, params={"exam_name": ""}).json()["data"]
    assert history["exam_name"] == ""
    assert [row["total"] for row in history["chart"]["rows"]] == [600, 610]
    assert history["secondary"]["subject_avg"][0]["avg"] == 120
