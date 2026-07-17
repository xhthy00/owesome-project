"""教育报告配置 API + config_store 单元测试。

验证 Phase 3 阈值配置 CRUD：GET 默认值、PUT 部分更新、RESET 回默认、
无效值校验、以及配置更新后 ``compute_score_stats_tool`` 的 KPI 自动重算
（Phase 3 验收点）。
"""

from __future__ import annotations

import asyncio

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from common.router import register_routers
from src.agent.education import config_store
from src.agent.education.tools import compute_score_stats_tool


def _client() -> TestClient:
    app = FastAPI()
    register_routers(app)
    return TestClient(app)


def test_config_get_default_pass_threshold():
    config_store.reset_config()
    client = _client()
    r = client.get("/api/v1/education/report-config")
    assert r.status_code == 200
    assert r.json()["data"]["pass_threshold"] == 60.0
    assert r.json()["data"]["excellent_threshold"] == 85.0


def test_config_put_updates_and_persists_in_process():
    config_store.reset_config()
    client = _client()
    r = client.put("/api/v1/education/report-config", json={"pass_threshold": 50, "excellent_threshold": 90})
    assert r.status_code == 200
    assert r.json()["data"]["pass_threshold"] == 50.0
    assert r.json()["data"]["excellent_threshold"] == 90.0
    # 进程内生效
    assert config_store.get_config().pass_threshold == 50.0


def test_config_reset_restores_defaults():
    config_store.reset_config()
    client = _client()
    client.put("/api/v1/education/report-config", json={"pass_threshold": 50})
    r = client.post("/api/v1/education/report-config/reset")
    assert r.status_code == 200
    assert config_store.get_config().pass_threshold == 60.0


def test_config_put_rejects_negative_threshold():
    config_store.reset_config()
    client = _client()
    r = client.put("/api/v1/education/report-config", json={"pass_threshold": -1})
    assert r.status_code == 422


def test_config_update_affects_compute_score_stats_tool():
    """Phase 3 验收：及格线改为 50 后，pass_rate 自动重算。"""
    config_store.reset_config()
    try:
        # 默认 60 分及格：[55,60,70] → 2/3 及格
        r1 = asyncio.run(compute_score_stats_tool.execute(scores=[55, 60, 70]))
        assert r1.data["pass_rate"] == 66.67 or abs(r1.data["pass_rate"] - 66.67) < 0.1
        # 配置改为 50 分及格：全部及格
        config_store.update_config({"pass_threshold": 50})
        r2 = asyncio.run(compute_score_stats_tool.execute(scores=[55, 60, 70]))
        assert r2.data["pass_rate"] == 100.0
    finally:
        config_store.reset_config()


# ---- 批量报告 API（Phase 4） ----------------------------------------------


def test_batch_report_endpoint_generates_per_class(monkeypatch):
    """mock 数据源底层，验证批量端点按班级列表逐个生成。"""
    from src.agent.resource.tool import business as biz
    from system.api.system import get_current_user
    from system.schemas import UserResponse
    from system.workspace_scope import get_workspace_oid

    schema = [
        {"name": "student_score", "comment": "学生考试成绩", "fields": [
            {"name": "id", "comment": "学号"},
            {"name": "cls", "comment": "班级"},
            {"name": "math", "comment": "数学成绩"},
        ]},
    ]
    monkeypatch.setattr(biz, "_load_datasource", lambda ds_id, workspace_oid=None: ("pg", {}, "ds"))
    monkeypatch.setattr("src.datasource.db.db.get_schema_info", lambda *_a, **_kw: schema)

    def fake_exec_by_user_id(user_id, datasource_id, workspace_oid, sql, **kwargs):
        rows = [[80], [70]] if "初三1班" in sql else [[60]]
        return True, "ok", {"columns": ["score"], "rows": rows, "row_count": len(rows)}, sql

    monkeypatch.setattr(
        "src.datasource.service.execute_with_permission.execute_sql_with_permission_by_user_id",
        fake_exec_by_user_id,
    )

    def fake_assert(session, user, datasource_id, workspace_oid):
        return SimpleNamespace(id=datasource_id, oid=workspace_oid)

    monkeypatch.setattr(
        "src.agent.education.api.assert_datasource_accessible",
        fake_assert,
    )

    app = FastAPI()
    register_routers(app)
    app.dependency_overrides[get_current_user] = lambda: UserResponse(
        id=1, account="admin", name="Admin", email=None, oid=1,
        status=1, language="zh-CN", origin=0, create_time=0,
    )
    app.dependency_overrides[get_workspace_oid] = lambda: 1
    client = TestClient(app)
    r = client.post(
        "/api/v1/education/batch-report",
        json={
            "datasource_id": 1,
            "question": "生成{class}期中成绩分析报告",
            "class_names": ["初三1班", "初三2班"],
        },
    )
    assert r.status_code == 200
    items = r.json()["data"]["items"]
    assert len(items) == 2
    assert items[0]["class_name"] == "初三1班"
    assert items[0]["template_name"] == "education/class_overview.html"
    assert items[0]["error"] is None
    assert items[0]["html_length"] > 0
    assert items[1]["class_name"] == "初三2班"


# ---- 分析工具 generate-report ---------------------------------------------


def _auth_client(monkeypatch):
    from common.middlewares.exception import register_exception_handlers
    from src.agent.resource.tool import business as biz
    from system.api.system import get_current_user
    from system.schemas import UserResponse
    from system.workspace_scope import get_workspace_oid

    schema = [
        {
            "name": "student_score",
            "comment": "学生考试成绩",
            "fields": [
                {"name": "id", "comment": "学号"},
                {"name": "cls", "comment": "班级"},
                {"name": "math", "comment": "数学成绩"},
            ],
        },
    ]
    monkeypatch.setattr(biz, "_load_datasource", lambda ds_id, workspace_oid=None: ("pg", {}, "ds"))
    monkeypatch.setattr("src.datasource.db.db.get_schema_info", lambda *_a, **_kw: schema)

    def fake_exec_by_user_id(user_id, datasource_id, workspace_oid, sql, **kwargs):
        return True, "ok", {"columns": ["score"], "rows": [[80], [70], [60]], "row_count": 3}, sql

    monkeypatch.setattr(
        "src.datasource.service.execute_with_permission.execute_sql_with_permission_by_user_id",
        fake_exec_by_user_id,
    )
    monkeypatch.setattr(
        "src.agent.education.api.assert_datasource_accessible",
        lambda session, user, datasource_id, workspace_oid: SimpleNamespace(
            id=datasource_id, oid=workspace_oid
        ),
    )

    app = FastAPI()
    register_exception_handlers(app)
    register_routers(app)
    app.dependency_overrides[get_current_user] = lambda: UserResponse(
        id=1,
        account="admin",
        name="Admin",
        email=None,
        oid=1,
        status=1,
        language="zh-CN",
        origin=0,
        create_time=0,
    )
    app.dependency_overrides[get_workspace_oid] = lambda: 1
    return TestClient(app)


def test_generate_report_class_overview(monkeypatch):
    client = _auth_client(monkeypatch)
    r = client.post(
        "/api/v1/education/generate-report",
        json={
            "datasource_id": 1,
            "report_type": "class_overview",
            "audience": "head_teacher",
            "filters": {"class_name": "初三1班", "exam_name": "期中"},
            "include_charts": True,
        },
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["error"] is None
    assert data["report_type"] == "class_overview"
    assert data["report_type_label"] == "班级总览报告"
    assert "edu-container" in data["html"]
    assert "初三1班" in data["html"]
    assert data["title"]


def test_generate_report_rejects_unsupported_type(monkeypatch):
    client = _auth_client(monkeypatch)
    r = client.post(
        "/api/v1/education/generate-report",
        json={
            "datasource_id": 1,
            "report_type": "diagnostic_report",
            "filters": {},
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 400
    assert "不支持" in (body.get("message") or "")


def test_generate_report_empty_rows_returns_actionable_error(monkeypatch):
    """查不到成绩时不应只返回空壳 HTML，应给出可操作的 error。"""
    from common.middlewares.exception import register_exception_handlers
    from src.agent.resource.tool import business as biz
    from system.api.system import get_current_user
    from system.schemas import UserResponse
    from system.workspace_scope import get_workspace_oid

    monkeypatch.setattr(biz, "_load_datasource", lambda ds_id, workspace_oid=None: ("pg", {}, "ds"))
    monkeypatch.setattr(
        "src.datasource.db.db.get_schema_info",
        lambda *_a, **_kw: [
            {
                "name": "student_score",
                "fields": [
                    {"name": "cls", "comment": "班级"},
                    {"name": "math", "comment": "数学成绩"},
                ],
            }
        ],
    )
    monkeypatch.setattr(
        "src.datasource.service.execute_with_permission.execute_sql_with_permission_by_user_id",
        lambda *a, **k: (True, "ok", {"columns": ["score"], "rows": [], "row_count": 0}, "SELECT 1"),
    )
    monkeypatch.setattr(
        "src.agent.education.api.assert_datasource_accessible",
        lambda *a, **k: SimpleNamespace(id=1, oid=1),
    )

    app = FastAPI()
    register_exception_handlers(app)
    register_routers(app)
    app.dependency_overrides[get_current_user] = lambda: UserResponse(
        id=1, account="admin", name="Admin", email=None, oid=1,
        status=1, language="zh-CN", origin=0, create_time=0,
    )
    app.dependency_overrides[get_workspace_oid] = lambda: 1
    client = TestClient(app)
    r = client.post(
        "/api/v1/education/generate-report",
        json={
            "datasource_id": 1,
            "report_type": "class_overview",
            "filters": {"class_name": "不存在的班级"},
        },
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["error"]
    assert "未查到成绩数据" in data["error"]
    assert "不存在的班级" in data["error"]


def test_meta_options_returns_distinct_lists(monkeypatch):
    client = _auth_client(monkeypatch)

    async def fake_load(orch, **kwargs):
        return {
            "schools": ["一中"],
            "exams": ["期中"],
            "classes": ["高一(1)班"],
            "subjects": ["数学"],
        }

    monkeypatch.setattr("src.agent.education.api._load_meta_options", fake_load)
    monkeypatch.setattr(
        "src.system.crud.crud_user.get_user_by_id",
        lambda session, uid: SimpleNamespace(id=uid, system_variables={"edu_role": "bureau_admin"}),
    )
    r = client.get("/api/v1/education/meta/options", params={"datasource_id": 1})
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["classes"] == ["高一(1)班"]
    assert data["exams"] == ["期中"]
    assert data["subjects"] == ["数学"]


def test_meta_options_school_scope_filters_schools():
    """配置了 school_name 时，学校下拉只保留权限内学校。"""
    import asyncio

    from datasource.service.edu_permission import EduScope
    from src.agent.education.api import _load_meta_options
    from src.agent.education.orchestrator import ReportOrchestrator
    from src.agent.education.schema_mapping import ScoreSchemaMapping

    calls: list[str] = []

    async def fake_execute(sql: str):
        calls.append(sql)
        if "sch.name AS v" in sql and "tb_score" in sql:
            return {
                "columns": ["v"],
                "rows": [["扬州中学"], ["新华中学"], ["邗江中学"]],
                "row_count": 3,
            }
        return {"columns": ["v"], "rows": [["期中"], ["高一(1)班"], ["数学"]], "row_count": 1}

    async def fake_schema():
        return ScoreSchemaMapping(
            mode="normalized",
            source="config_edu",
            tables={},
            fields={},
        )

    orch = ReportOrchestrator(execute_sql=fake_execute, resolve_schema=fake_schema)
    opts = asyncio.run(
        _load_meta_options(
            orch,
            school_name=None,
            exam_name=None,
            class_name=None,
            subject=None,
            edu_scope=EduScope(edu_role="school_admin", school_id="1", school_name="扬州中学"),
        )
    )
    assert opts["schools"] == ["扬州中学"]
    assert any("tb_score" in s for s in calls), "学校下拉必须经 tb_score 才能挂权限"
