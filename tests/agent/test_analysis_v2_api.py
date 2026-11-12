"""分析工具 API 的 v2 分权接线（不连接真实学情库）。"""

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from common.middlewares.exception import register_exception_handlers
from common.router import register_routers
from system.api.auth_deps import get_current_user
from system.schemas import UserResponse
from system.workspace_scope import get_workspace_oid


def _scope(*, citywide: bool = False, levels=("class",), classes=("高三(1)班",)):
    return SimpleNamespace(
        datasource_id=1,
        fingerprint="f" * 64,
        citywide=citywide,
        levels=frozenset(levels),
        schools=("A01扬州中学",),
        districts=(),
        cohorts=(),
        classes=tuple(classes),
        capabilities=frozenset(),
        needs_grade_reference=not citywide and bool(set(levels) & {"class", "student"}),
        allows_city_position=lambda: citywide,
        allows_city_reference=lambda: citywide,
        allows_grade_reference=lambda: False,
        legacy_role=lambda: "teacher" if not citywide else "bureau_admin",
        assert_in_scope=lambda **_: None,
        summary=lambda: {"edu_role": "v2", "datasource_id": 1},
    )


def _client(monkeypatch, scope):
    from src.agent.education import api

    captured: dict = {}

    class FakeOrch:
        async def run_spec(self, spec):
            return SimpleNamespace(
                html="<div class='edu-container'>ok</div>",
                stats={"count": 1},
                error=None,
                spec=spec,
                template_name="education/class_overview.html",
            )

        def _title(self, spec):
            return "报告"

    monkeypatch.setattr(api, "_load_report_scope", lambda *a, **k: scope)
    monkeypatch.setattr(
        api,
        "assert_datasource_accessible",
        lambda *a, **k: SimpleNamespace(id=1, oid=1),
    )
    def build_orchestrator(*args, **kwargs):
        captured["kwargs"] = kwargs
        return FakeOrch()

    monkeypatch.setattr(api, "_build_orchestrator", build_orchestrator)

    app = FastAPI()
    register_exception_handlers(app)
    register_routers(app)
    app.dependency_overrides[get_current_user] = lambda: UserResponse(
        id=1,
        account="u",
        name="U",
        oid=1,
        status=1,
        language="zh-CN",
        origin=0,
        create_time=0,
    )
    app.dependency_overrides[get_workspace_oid] = lambda: 1
    return TestClient(app), captured


def test_generate_report_passes_v2_scope_to_orchestrator(monkeypatch):
    client, captured = _client(monkeypatch, _scope())
    r = client.post(
        "/api/v1/education/generate-report",
        json={
            "datasource_id": 1,
            "report_type": "class_overview",
            "filters": {"class_name": "高三(1)班", "exam_name": "期中"},
        },
    )
    assert r.status_code == 200, r.text
    assert captured["kwargs"]["report_scope"] is not None


def test_generate_report_rejects_bureau_types_without_city_scope(monkeypatch):
    client, captured = _client(monkeypatch, _scope())
    r = client.post(
        "/api/v1/education/generate-report",
        json={"datasource_id": 1, "report_type": "subject_avg", "filters": {}},
    )
    assert r.json()["code"] == 403
    assert captured == {}


def test_generate_report_rejects_elite_rank_shift_without_city_scope(monkeypatch):
    client, captured = _client(monkeypatch, _scope())
    r = client.post(
        "/api/v1/education/generate-report",
        json={"datasource_id": 1, "report_type": "elite_rank_shift", "filters": {}},
    )
    assert r.json()["code"] == 403
    assert captured == {}


def test_generate_report_allows_citywide_bureau_types(monkeypatch):
    client, captured = _client(
        monkeypatch, _scope(citywide=True, levels=("city",), classes=())
    )
    r = client.post(
        "/api/v1/education/generate-report",
        json={"datasource_id": 1, "report_type": "subject_avg", "filters": {}},
    )
    assert r.status_code == 200, r.text
    assert captured["kwargs"]["report_scope"] is not None


def test_save_report_history_stores_v2_fingerprint(monkeypatch):
    import src.common.core.database as db
    from src.chat.crud import chat as chat_crud

    client, _ = _client(monkeypatch, _scope())
    created: dict = {}

    class _Sess:
        def __enter__(self):
            return SimpleNamespace()

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(db, "get_db_session", lambda: _Sess())
    monkeypatch.setattr(
        chat_crud,
        "create_conversation",
        lambda session, **kwargs: SimpleNamespace(id=101),
    )
    monkeypatch.setattr(
        chat_crud,
        "create_conversation_record",
        lambda session, **kwargs: created.update(kwargs) or SimpleNamespace(id=202),
    )
    r = client.post(
        "/api/v1/education/save-report-history",
        json={
            "datasource_id": 1,
            "title": "高三(1)班报告",
            "html": "<div>ok</div>",
            "report_type": "class_overview",
        },
    )
    assert r.status_code == 200, r.text
    assert created["exec_result"]["permission_mode"] == "model_explicit_v2"
    assert created["exec_result"]["authorization_fingerprint"] == "f" * 64
