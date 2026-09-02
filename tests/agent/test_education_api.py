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
    from system.api.auth_deps import get_current_user
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
        "datasource.service.edu_permission.edu_scope_dict_for_user_id",
        lambda _uid: {},
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


def test_generate_report_denies_teacher_other_class(monkeypatch):
    from src.agent.education.scope_guard import OUT_OF_SCOPE_MESSAGE

    client = _auth_client(monkeypatch)
    monkeypatch.setattr(
        "datasource.service.edu_permission.edu_scope_dict_for_user_id",
        lambda _uid: {
            "edu_role": "teacher",
            "school_name": "扬州中学",
            "class_names": ["高一(1)班"],
        },
    )
    r = client.post(
        "/api/v1/education/generate-report",
        json={
            "datasource_id": 1,
            "report_type": "class_overview",
            "filters": {"class_name": "高一(3)班", "exam_name": "期中"},
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 403
    assert body["message"] == OUT_OF_SCOPE_MESSAGE


def test_generate_report_rejects_unsupported_type(monkeypatch):
    client = _auth_client(monkeypatch)
    r = client.post(
        "/api/v1/education/generate-report",
        json={
            "datasource_id": 1,
            "report_type": "not_a_real_type",
            "filters": {},
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 400
    assert "不支持" in (body.get("message") or "")


def test_generate_report_allows_phase2_types(monkeypatch):
    """Phase2：报告类型均在白名单内（此处抽测 trend / diagnostic / 达线）。"""
    client = _auth_client(monkeypatch)
    for rt in ("trend_tracking", "diagnostic_report", "tier_alert", "line_reach"):
        r = client.post(
            "/api/v1/education/generate-report",
            json={
                "datasource_id": 1,
                "report_type": rt,
                "filters": {"class_name": "初三1班"},
                "include_charts": False,
            },
        )
        assert r.status_code == 200, rt
        body = r.json()
        assert body["code"] == 200, (rt, body.get("message"))
        assert body["data"]["report_type"] == rt


def test_batch_report_with_report_type(monkeypatch):
    """Phase2：batch-report 支持 report_type + class_names 确定性路径。"""
    client = _auth_client(monkeypatch)
    r = client.post(
        "/api/v1/education/batch-report",
        json={
            "datasource_id": 1,
            "report_type": "class_overview",
            "class_names": ["初三1班", "初三2班"],
            "filters": {"exam_name": "期中"},
            "include_charts": False,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 200, body.get("message")
    items = body["data"]["items"]
    assert len(items) == 2
    assert {i["class_name"] for i in items} == {"初三1班", "初三2班"}
    assert all(i["report_type"] == "class_overview" for i in items)


def test_save_report_history(monkeypatch):
    """Phase2：保存到任务历史，写入 Conversation + reports。"""
    from types import SimpleNamespace

    client = _auth_client(monkeypatch)

    created = {}

    def fake_create_conversation(session, **kwargs):
        created["conversation"] = kwargs
        return SimpleNamespace(id=101)

    def fake_create_record(session, **kwargs):
        created["record"] = kwargs
        return SimpleNamespace(id=202)

    monkeypatch.setattr(
        "src.chat.crud.chat.create_conversation",
        fake_create_conversation,
    )
    monkeypatch.setattr(
        "src.chat.crud.chat.create_conversation_record",
        fake_create_record,
    )
    # get_db_session context manager
    class _Sess:
        def __enter__(self):
            return SimpleNamespace()

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(
        "src.common.core.database.get_db_session",
        lambda: _Sess(),
    )

    r = client.post(
        "/api/v1/education/save-report-history",
        json={
            "datasource_id": 1,
            "title": "初三1班学情报告",
            "html": "<div class='edu-container'>ok</div>",
            "report_type": "class_overview",
            "report_type_label": "班级总览报告",
            "question": "分析工具测试",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 200, body.get("message")
    assert body["data"]["conversation_id"] == 101
    assert body["data"]["record_id"] == 202
    assert created["record"]["reports"][0]["title"] == "初三1班学情报告"
    assert created["record"]["agent_mode"] == "analysis_tool"


def test_list_report_history(monkeypatch):
    """Phase3：报告历史列表复用 analysis_tool 记录。"""
    from datetime import datetime
    from types import SimpleNamespace

    client = _auth_client(monkeypatch)

    class _Sess:
        def __enter__(self):
            return SimpleNamespace()

        def __exit__(self, *a):
            return False

    monkeypatch.setattr("src.common.core.database.get_db_session", lambda: _Sess())

    rec = SimpleNamespace(
        id=11,
        conversation_id=22,
        question="分析工具 · 班级总览",
        summary="已保存报告",
        agent_mode="analysis_tool",
        create_time=datetime(2026, 7, 17, 20, 0, 0),
        reports='[{"title":"初三1班报告","html":"<p>x</p>","report_type":"class_overview","report_type_label":"班级总览报告"}]',
    )
    conv = SimpleNamespace(
        id=22,
        title="[分析工具] 初三1班报告",
        datasource_id=1,
        datasource_name="学情分析",
    )
    monkeypatch.setattr(
        "src.chat.crud.chat.list_analysis_tool_records",
        lambda **kwargs: [(rec, conv)],
    )

    r = client.get("/api/v1/education/report-history?limit=20")
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 200, body.get("message")
    assert body["data"]["total"] == 1
    item = body["data"]["items"][0]
    assert item["record_id"] == 11
    assert item["title"] == "初三1班报告"
    assert item["report_type"] == "class_overview"
    assert "html" not in item
    assert item["html_length"] == len("<p>x</p>")


def test_get_report_history_detail(monkeypatch):
    from datetime import datetime
    from types import SimpleNamespace

    client = _auth_client(monkeypatch)

    class _Sess:
        def __enter__(self):
            return SimpleNamespace()

        def __exit__(self, *a):
            return False

    monkeypatch.setattr("src.common.core.database.get_db_session", lambda: _Sess())

    rec = SimpleNamespace(
        id=11,
        conversation_id=22,
        question="q",
        summary="s",
        agent_mode="analysis_tool",
        create_time=datetime(2026, 7, 17, 20, 0, 0),
        reports='[{"title":"T","html":"<div>hi</div>","report_type":"class_overview","report_type_label":"班级总览报告"}]',
    )
    conv = SimpleNamespace(
        id=22,
        title="[分析工具] T",
        datasource_id=1,
        datasource_name="学情分析",
    )
    monkeypatch.setattr("src.chat.crud.chat.get_record_by_id", lambda *a, **k: rec)
    monkeypatch.setattr("src.chat.crud.chat.get_conversation_by_id", lambda *a, **k: conv)

    r = client.get("/api/v1/education/report-history/11")
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 200
    assert body["data"]["html"] == "<div>hi</div>"
    assert body["data"]["title"] == "T"


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


# ---- 预测线达线看板 --------------------------------------------------------


def _mock_line_reach_session(monkeypatch, *, edu_role: str = "bureau_admin"):
    class _Sess:
        def __enter__(self):
            return SimpleNamespace()

        def __exit__(self, *a):
            return False

    monkeypatch.setattr("src.common.core.database.get_db_session", lambda: _Sess())
    monkeypatch.setattr(
        "system.crud.crud_user.get_user_by_id",
        lambda session, uid: SimpleNamespace(
            id=uid,
            system_variables={"edu_role": edu_role},
        ),
    )


def _line_reach_execute():
    import re

    async def fake_execute(sql: str):
        s = sql.lower()
        empty_probe = bool(re.search(r"\blimit\s+0\b", s))
        if "tb_fraction_bar" in s:
            cols = ["exam_name", "line_name", "threshold", "track"]
            rows = [] if empty_probe else [
                ["5月模考", "特控线", 480, "物理类"],
                ["5月模考", "本科线", 400, "物理类"],
            ]
            return {"columns": cols, "rows": rows, "row_count": len(rows)}
        if "tb_score_overview" in s:
            if "group by" in s:
                return {
                    "columns": ["district", "school_name", "candidates", "r0", "r1"],
                    "rows": [
                        ["邗江区", "SCHOOL_A", 2, 1, 2],
                        ["广陵区", "SCHOOL_B", 1, 0, 0],
                        ["江都区", "SCHOOL_C", 1, 1, 1],
                    ],
                    "row_count": 3,
                }
            cols = ["student_id", "exam_name", "zf", "wl"]
            rows = [] if empty_probe else [
                ["s1", "5月模考", 500, 80],
            ]
            return {"columns": cols, "rows": rows, "row_count": len(rows)}
        if "tb_score" in s:
            return {
                "columns": ["student_id", "school_id", "class"],
                "rows": [
                    ["s1", "sch-a", "1班"],
                    ["s2", "sch-a", "1班"],
                    ["s3", "sch-b", "2班"],
                    ["s4", "sch-c", "3班"],
                ],
                "row_count": 4,
            }
        if "tb_school" in s:
            return {
                "columns": ["id", "district", "name"],
                "rows": [
                    ["sch-a", "邗江区", "SCHOOL_A"],
                    ["sch-b", "广陵区", "SCHOOL_B"],
                    ["sch-c", "江都区", "SCHOOL_C"],
                ],
                "row_count": 3,
            }
        return {"columns": [], "rows": [], "row_count": 0}

    return fake_execute


def test_line_reach_api_aggregates_districts(monkeypatch):
    client = _auth_client(monkeypatch)
    _mock_line_reach_session(monkeypatch, edu_role="bureau_admin")
    sqls: list[str] = []
    inner = _line_reach_execute()

    async def wrapped(sql: str):
        sqls.append(sql)
        return await inner(sql)

    monkeypatch.setattr(
        "src.agent.education.api._build_orchestrator",
        lambda *a, **k: SimpleNamespace(_execute_sql=wrapped),
    )
    r = client.get(
        "/api/v1/education/dashboards/line-reach",
        params={"datasource_id": 1, "exam_name": "5月模考", "track": "物理类"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 200
    data = body["data"]
    assert data["accessible"] is True
    assert data["kpis"]["candidates"] == 4
    by_line = {x["line_name"]: x for x in data["kpis"]["by_line"]}
    assert by_line["特控线"]["reached"] == 2
    assert by_line["特控线"]["rate"] == 50.0
    assert by_line["本科线"]["reached"] == 3
    assert by_line["本科线"]["rate"] == 75.0
    districts = {d["district"]: d for d in data["districts"]}
    assert districts["邗江区"]["candidates"] == 2
    dumped = str(data)
    assert "xm" not in dumped
    assert "s_name" not in dumped
    fetch_sqls = [s for s in sqls if "LIMIT 0" not in s.upper()]
    assert fetch_sqls
    assert all("xm" not in s.lower().split("from")[0] for s in fetch_sqls)


def test_line_reach_meta_lists_exams(monkeypatch):
    client = _auth_client(monkeypatch)
    _mock_line_reach_session(monkeypatch, edu_role="bureau_admin")
    monkeypatch.setattr(
        "src.agent.education.api._build_orchestrator",
        lambda *a, **k: SimpleNamespace(_execute_sql=_line_reach_execute()),
    )
    r = client.get(
        "/api/v1/education/dashboards/line-reach/meta",
        params={"datasource_id": 1},
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert "5月模考" in data["exams"]
    assert "物理类" in data["tracks"]
    assert {x["line_name"] for x in data["lines"]} == {"特控线", "本科线"}


def test_overview_agg_sql_citywide_scopes_each_bar():
    from src.agent.education.api import _overview_agg_sql
    import re

    sql = _overview_agg_sql(
        ["zf6m", "dq", "xx", "exam_name", "xkkm"],
        [
            {"line_name": "本科线", "threshold": 461, "track": "物理类"},
            {"line_name": "本科线", "threshold": 453, "track": "历史类"},
        ],
        exam_name="5月模考",
        track="",
    )
    assert sql is not None
    assert "LIKE '物%'" in sql
    assert "GROUP BY 1, 2, 3" in sql
    sums = re.findall(r"SUM\(CASE WHEN (.*?) THEN", sql)
    assert sums
    assert all("LIKE" not in s for s in sums)


def test_overview_agg_sql_without_school_grain():
    from src.agent.education.api import _overview_agg_sql

    sql = _overview_agg_sql(
        ["zf6m", "dq", "xx", "exam_name", "xkkm"],
        [{"line_name": "本科线", "threshold": 461, "track": "物理类"}],
        exam_name="5月模考",
        track="",
        include_school=False,
    )
    assert sql is not None
    assert "GROUP BY 1, 3" in sql
    assert "xx" not in sql


def test_line_reach_api_student_forbidden(monkeypatch):
    client = _auth_client(monkeypatch)
    _mock_line_reach_session(monkeypatch, edu_role="student")
    r = client.get(
        "/api/v1/education/dashboards/line-reach",
        params={"datasource_id": 1},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 403
    assert "学生" in (body.get("message") or "")


def test_fraction_bar_put_recomputes(monkeypatch):
    client = _auth_client(monkeypatch)
    _mock_line_reach_session(monkeypatch, edu_role="bureau_admin")
    captured: dict = {}

    def fake_upsert(db_type, config, *, exam_name, lines, exam_batch_id=None):
        captured["exam"] = exam_name
        captured["lines"] = lines
        captured["exam_batch_id"] = exam_batch_id
        return {
            "exam_name": exam_name,
            "exam_batch_id": exam_batch_id,
            "indicator_rows": 4,
            "bar_count": 2,
            "empty_scores": False,
            "fraction_bar": "inserted",
        }

    monkeypatch.setattr(
        "src.agent.education.score_indicator.upsert_fraction_bar_and_recompute",
        fake_upsert,
    )
    r = client.put(
        "/api/v1/education/fraction-bar",
        json={
            "datasource_id": 1,
            "exam_batch_id": 12,
            "exam_name": "5月模考",
            "lines": [{"track": "物理类", "line_code": "bk", "threshold": 400}],
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 200
    assert body["data"]["indicator_rows"] == 4
    assert captured["exam"] == "5月模考"
    assert captured["exam_batch_id"] == 12


def test_fraction_bar_put_requires_exam(monkeypatch):
    client = _auth_client(monkeypatch)
    _mock_line_reach_session(monkeypatch, edu_role="bureau_admin")
    r = client.put(
        "/api/v1/education/fraction-bar",
        json={"datasource_id": 1, "lines": []},
    )
    assert r.status_code == 200
    assert r.json()["code"] == 400


def test_fraction_bar_student_forbidden(monkeypatch):
    client = _auth_client(monkeypatch)
    _mock_line_reach_session(monkeypatch, edu_role="student")
    r = client.put(
        "/api/v1/education/fraction-bar",
        json={"datasource_id": 1, "exam_name": "5月模考", "lines": []},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 403
    assert "学生" in (body.get("message") or "")


def test_fraction_bar_list_api(monkeypatch):
    client = _auth_client(monkeypatch)
    _mock_line_reach_session(monkeypatch, edu_role="bureau_admin")
    monkeypatch.setattr(
        "src.agent.education.score_indicator.list_fraction_bars",
        lambda *a, **k: {
            "columns": ["exam_name", "wl_score_bk"],
            "line_catalog": [
                {"line_code": "bk", "line_name": "本科线", "wl_column": "wl_score_bk", "ls_column": None}
            ],
            "exams": [{"exam_name": "5月模考", "exam_batch_id": 12, "lines": []}],
            "batches": [{"id": 12, "batch_name": "5月模考"}],
        },
    )
    r = client.get("/api/v1/education/fraction-bar", params={"datasource_id": 1})
    assert r.status_code == 200
    assert r.json()["data"]["exams"][0]["exam_name"] == "5月模考"
    assert r.json()["data"]["batches"][0]["id"] == 12


def test_score_indicator_recompute_api(monkeypatch):
    client = _auth_client(monkeypatch)
    _mock_line_reach_session(monkeypatch, edu_role="bureau_admin")
    monkeypatch.setattr(
        "src.agent.education.score_indicator.recompute_exams",
        lambda *a, **k: {"exams": [{"exam_name": "5月模考", "indicator_rows": 3}], "skipped": [], "indicator_rows": 3},
    )
    r = client.post(
        "/api/v1/education/score-indicator/recompute",
        json={"datasource_id": 1, "exam_name": "5月模考"},
    )
    assert r.status_code == 200
    assert r.json()["data"]["indicator_rows"] == 3
