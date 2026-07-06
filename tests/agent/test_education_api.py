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
