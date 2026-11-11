import csv
import io
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import BigInteger, create_engine, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool
from sqlmodel import Session

from audit.models.audit import AuditOperationLog
from common.core.database import get_session
from common.middlewares.exception import register_exception_handlers
from datasource.models.datasource import CoreDatasource
from datasource.models.permission import DsRule
from src.datasource.service.education_authorization import AUTH_KEY, PermissionManifest
from src.system.api import edu_permission_v2 as api
from system.api.auth_deps import get_current_user
from system.models.user import SysUser
from system.models.workspace import SysUserWorkspace


@compiles(JSONB, "sqlite")
def sqlite_json(_type, compiler, **kw):
    return "JSON"


@compiles(BigInteger, "sqlite")
def sqlite_integer(_type, compiler, **kw):
    return "INTEGER"


@pytest.fixture
def application(monkeypatch):
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in (
        SysUser.__table__,
        SysUserWorkspace.__table__,
        CoreDatasource.__table__,
        AuditOperationLog.__table__,
        DsRule.__table__,
    ):
        table.create(engine)
    with Session(engine) as session:
        for uid, role in [(1, "platform_admin"), (7, None), (8, None), (9, None)]:
            session.add(
                SysUser(
                    id=uid,
                    account=f"user{uid}",
                    name=f"U{uid}",
                    password="unused",
                    oid=1,
                    status=1,
                    create_time=0,
                    sys_role=role,
                    system_variables={"unrelated": "keep"},
                )
            )
            session.add(
                SysUserWorkspace(
                    id=uid, uid=uid, oid=1 if uid != 9 else 2, weight=1 if uid == 8 else 0
                )
            )
        session.add(
            CoreDatasource(
                id=4,
                name="edu",
                type="pg",
                type_name="postgres",
                configuration="{}",
                create_by=1,
                oid=1,
            )
        )
        session.commit()
    policy = PermissionManifest.model_validate(
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
                            "school_id",
                            "cohort_id",
                            "class_id",
                            "exam_batch_id",
                            "subject",
                            "score_value",
                        ]
                    },
                    "scope_columns": {
                        k: k for k in ["city_id", "school_id", "cohort_id", "class_id"]
                    },
                }
            },
            "catalog": [{"city_id": "C1", "school_id": "S1", "cohort_id": "G1", "class_id": "B1"}],
        }
    )
    monkeypatch.setattr(api, "environment_manifest", lambda *_, **__: policy)
    monkeypatch.setattr(
        api,
        "source_info",
        lambda oid: {
            "datasource_id": 4,
            "workspace_oid": oid,
            "name": "学情业务库",
            "source_type": "DATABASE_EDU_URL",
            "city_id": "C1",
            "city_label": "全市",
            "supports_history": False,
        },
    )
    active = {"id": 1}
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(api.router, prefix="/api/v1")

    def db():
        with Session(engine) as session:
            yield session

    def user():
        with Session(engine) as session:
            return session.get(SysUser, active["id"])

    app.dependency_overrides[get_session] = db
    app.dependency_overrides[get_current_user] = user
    with TestClient(app) as client:
        yield client, engine, active
    engine.dispose()


def grant(**updates):
    return {
        "id": "g1",
        "workspace_oid": 1,
        "datasource_id": 4,
        "level": "class",
        "city_id": "C1",
        "school_id": "S1",
        "cohort_id": "G1",
        "class_id": "B1",
        **updates,
    }


def put(client, grants, revision=0, uid=7):
    return client.put(
        f"/api/v1/permission/edu/v2/grants/{uid}?datasource_id=4",
        json={"expected_revision": revision, "grants": grants},
    )


def succeeded(response):
    return response.status_code == 200 and response.json().get("code", 200) == 200


def test_save_json_document_is_transactional_and_revision_checked(application):
    client, engine, _ = application
    response = put(client, [grant()])
    assert succeeded(response), response.text
    assert response.json()["data"]["revision"] == 1
    assert not succeeded(put(client, [], revision=0))
    with Session(engine) as session:
        user = session.get(SysUser, 7)
        assert user.system_variables["unrelated"] == "keep"
        assert len(user.system_variables[AUTH_KEY]["grants"]) == 1
        logs = session.query(AuditOperationLog).all()
        assert len(logs) == 1 and "before" in logs[0].detail
    assert succeeded(put(client, [], revision=1))
    with Session(engine) as session:
        document = session.get(SysUser, 7).system_variables[AUTH_KEY]
        assert document["revision"] == 2 and document["managed_resources"] == ["1:4"]


def test_ordinary_user_cannot_change_or_read_another_authorization(application):
    client, _, active = application
    active["id"] = 7
    assert not succeeded(put(client, [grant()]))
    assert succeeded(client.get("/api/v1/permission/edu/v2/grants/7?datasource_id=4"))
    assert not succeeded(client.get("/api/v1/permission/edu/v2/grants/8?datasource_id=4"))


def test_workspace_admin_needs_explicit_delegation(application):
    client, _, active = application
    active["id"] = 8
    assert not succeeded(put(client, [grant()]))
    active["id"] = 1
    assert succeeded(put(client, [grant(id="delegate", can_delegate=True)], uid=8))
    active["id"] = 8
    assert succeeded(put(client, [grant()]))
    assert not succeeded(
        put(client, [grant(level="school", class_id=None, cohort_id=None)], revision=1)
    )


def test_cross_workspace_and_catalog_mismatch_rejected(application):
    client, _, _ = application
    assert not succeeded(put(client, [grant(workspace_oid=2)]))
    assert not succeeded(put(client, [grant(school_id="S2")]))
    assert not succeeded(put(client, [grant()], uid=9))
    response = client.get("/api/v1/permission/edu/v2/catalog?datasource_id=4")
    assert succeeded(response) and response.json()["data"]["items"]


def test_batch_preview_does_not_write_and_invalid_apply_is_atomic(application):
    client, engine, _ = application

    def text(rows):
        stream = io.StringIO()
        writer = csv.writer(stream)
        writer.writerow(["user_id", "expected_revision", "grants_json"])
        for uid, revision, grants in rows:
            writer.writerow([uid, revision, json.dumps(grants)])
        return stream.getvalue()

    csv_text = text([(7, 0, [grant()]), (8, 0, [grant(id="g2")])])
    preview = client.post(
        "/api/v1/permission/edu/v2/batch-bind/preview", json={"datasource_id": 4, "csv": csv_text}
    )
    assert succeeded(preview), preview.text
    with Session(engine) as session:
        assert AUTH_KEY not in session.get(SysUser, 7).system_variables
    bad_csv = text([(7, 0, [grant()]), (8, 0, [grant(id="g2", school_id="S2")])])
    assert not succeeded(
        client.post(
            "/api/v1/permission/edu/v2/batch-bind/apply", json={"datasource_id": 4, "csv": bad_csv}
        )
    )
    with Session(engine) as session:
        assert AUTH_KEY not in session.get(SysUser, 7).system_variables
        assert not session.query(AuditOperationLog).all()
    applied = client.post(
        "/api/v1/permission/edu/v2/batch-bind/apply", json={"datasource_id": 4, "csv": csv_text}
    )
    assert succeeded(applied), applied.text
    assert applied.json()["data"]["applied"] == 2


def test_control_plane_cannot_be_used_to_take_over_another_grant(application, monkeypatch):
    from common.exceptions.base import ForbiddenException, UnauthorizedException
    from datasource.api.datasource import _require_datasource_admin
    from system.api import auth_deps
    from system.api.user import _assert_can_mutate_account
    from system.api.workspace import _require_workspace_manager

    _, engine, _ = application
    with Session(engine) as session:
        ordinary = session.get(SysUser, 7)
        target = session.get(SysUser, 8)
        with pytest.raises(ForbiddenException):
            _assert_can_mutate_account(ordinary, target)
        with pytest.raises(ForbiddenException):
            _require_workspace_manager(session, ordinary, 1)
        with pytest.raises(ForbiddenException):
            _require_datasource_admin(ordinary)
        _require_workspace_manager(session, target, 1)
        with pytest.raises(ForbiddenException):
            _require_workspace_manager(session, target, 2)
        monkeypatch.setattr(auth_deps, "decode_access_token", lambda _: {"sub": 7})
        ordinary.status = 0
        monkeypatch.setattr(auth_deps, "get_user_by_id", lambda *_: ordinary)
        with pytest.raises(UnauthorizedException):
            auth_deps.get_current_user("unused", session)
        from starlette.requests import Request

        ordinary.status = 1
        ordinary.system_variables = {AUTH_KEY: {"managed_resources": ["1:4"], "grants": []}}
        legacy_request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/api/v1/education/score-library",
                "headers": [],
            }
        )
        with pytest.raises(ForbiddenException):
            auth_deps.get_current_user("unused", session, legacy_request)


def test_permission_preview_checks_semantic_sql_without_planning_or_executing(
    application, monkeypatch
):
    from src.chat.service.semantic_query import SemanticQuery

    monkeypatch.setattr(
        SemanticQuery, "_plan", lambda *_: pytest.fail("preview must not compile/execute")
    )
    client, _, _ = application
    assert succeeded(put(client, [grant()]))
    sql = "SELECT s.subject FROM edu_scores s WHERE s.city_id='C1' AND s.school_id='S1' AND s.cohort_id='G1' AND s.class_id='B1'"
    request = {"user_id": 7, "datasource_id": 4, "sql": sql}
    valid = client.post("/api/v1/permission/edu/v2/effective", json=request)
    assert succeeded(valid), valid.text
    assert valid.json()["data"]["validation"]["allowed"]
    assert valid.json()["data"]["validation"]["physical_verification_required"]
    request["sql"] = sql.replace("AND s.class_id='B1'", "")
    invalid = client.post("/api/v1/permission/edu/v2/effective", json=request)
    assert not invalid.json()["data"]["validation"]["allowed"]
    assert invalid.json()["data"]["validation"]["code"] == "AUTH_SCOPE_MISSING"
    request["sql"] = "DELETE FROM edu_scores"
    malformed = client.post("/api/v1/permission/edu/v2/effective", json=request)
    assert malformed.json()["data"]["validation"]["code"] == "SQL_INVALID"


def test_fixed_education_source_needs_no_datasource_choice(application):
    client, _, _ = application
    info = client.get("/api/v1/permission/edu/v2/source")
    assert succeeded(info) and info.json()["data"]["source_type"] == "DATABASE_EDU_URL"
    assert "password" not in info.text and "host" not in info.text
    directory = client.get("/api/v1/permission/edu/v2/catalog")
    assert succeeded(directory) and directory.json()["data"]["datasource_id"] == 4
    saved = client.put(
        "/api/v1/permission/edu/v2/grants/7", json={"expected_revision": 0, "grants": [grant()]}
    )
    assert succeeded(saved), saved.text
    assert saved.json()["data"]["grants"][0]["datasource_id"] == 4
    fetched = client.get("/api/v1/permission/edu/v2/grants/7")
    assert fetched.json()["data"]["revision"] == 1
    denied = client.put(
        "/api/v1/permission/edu/v2/grants/7",
        json={"expected_revision": 1, "grants": [grant(datasource_id=999)]},
    )
    assert not succeeded(denied)
    assert client.get("/api/v1/permission/edu/v2/grants/7").json()["data"]["revision"] == 1


def test_legacy_connection_ids_can_be_read_delegated_and_saved_as_logical_resource(
    application, monkeypatch
):
    client, engine, active = application
    policy = api.environment_manifest(1, 4)
    relation = policy.relations["edu_scores"].model_copy(
        update={
            "implicit_scope": {"city_id": "education"},
            "scope_columns": {
                k: v
                for k, v in policy.relations["edu_scores"].scope_columns.items()
                if k != "city_id"
            },
        }
    )
    policy = policy.model_copy(
        update={
            "relations": {"edu_scores": relation},
            "catalog": tuple({**row, "city_id": "education"} for row in policy.catalog),
        }
    )
    monkeypatch.setattr(api, "environment_manifest", lambda *_, **__: policy)
    legacy_id = "education-0123456789abcdef"
    with Session(engine) as session:
        for uid in (7, 8):
            user = session.get(SysUser, uid)
            user.system_variables = {
                "unrelated": "keep",
                AUTH_KEY: {
                    "schema_version": 2,
                    "revision": 5,
                    "managed_resources": ["1:4"],
                    "grants": [grant(city_id=legacy_id, can_delegate=uid == 8)],
                },
            }
            session.add(user)
        session.commit()
    active["id"] = 8  # A bounded delegate must also recognize both legacy documents.
    fetched = client.get("/api/v1/permission/edu/v2/grants/7")
    assert succeeded(fetched), fetched.text
    assert fetched.json()["data"]["grants"][0]["city_id"] == "education"
    assert fetched.json()["data"]["revision"] == 5
    with Session(engine) as session:
        assert (
            session.get(SysUser, 7).system_variables[AUTH_KEY]["grants"][0]["city_id"] == legacy_id
        )
    saved = client.put(
        "/api/v1/permission/edu/v2/grants/7",
        json={"expected_revision": 5, "grants": [grant(city_id=legacy_id)]},
    )
    assert succeeded(saved), saved.text
    assert saved.json()["data"]["revision"] == 6
    with Session(engine) as session:
        user = session.get(SysUser, 7)
        assert user.system_variables[AUTH_KEY]["grants"][0]["city_id"] == "education"
        assert user.system_variables["unrelated"] == "keep"


def test_account_list_is_workspace_paged_searchable_and_minimal(application):
    client, engine, _ = application
    assert succeeded(put(client, [grant()]))
    with Session(engine) as session:
        for uid in range(20, 620):
            session.add(
                SysUser(
                    id=uid,
                    account=f"teacher{uid}",
                    name=f"教师{uid}",
                    password="never-return",
                    oid=1,
                    status=1,
                    create_time=0,
                    system_variables={"secret": "never-return"},
                )
            )
            session.add(SysUserWorkspace(id=uid, uid=uid, oid=1, weight=0))
        session.commit()
    statements = []

    def record_statement(*args):
        statements.append(args[2])

    event.listen(engine, "before_cursor_execute", record_statement)
    try:
        result = client.get("/api/v1/permission/edu/v2/users?page=6&page_size=100").json()["data"]
    finally:
        event.remove(engine, "before_cursor_execute", record_statement)
    assert len(statements) <= 6  # Page size must not add per-account SQL round trips.
    assert result["total"] == 603 and len(result["items"]) == 100
    assert result["items"][0]["id"] == 517
    assert "password" not in result["items"][0] and "system_variables" not in result["items"][0]
    assert "never-return" not in json.dumps(result)
    result = client.get("/api/v1/permission/edu/v2/users?keyword=USER7").json()["data"]
    assert result["total"] == 1
    user = result["items"][0]
    assert user["id"] == 7 and user["manageable"] and user["active_count"] == 1
    assert user["document"]["revision"] == 1
    assert user["document"]["grants"][0]["class_id"] == "B1"
    assert client.get("/api/v1/permission/edu/v2/users?keyword=user9").json()["data"]["total"] == 0
    assert client.get("/api/v1/permission/edu/v2/users?keyword=%25").json()["data"]["total"] == 0
    assert client.get("/api/v1/permission/edu/v2/users?page_size=500").status_code == 422


def test_account_list_requires_management_and_hides_broader_grants(application):
    client, _, active = application
    active["id"] = 7
    assert not succeeded(client.get("/api/v1/permission/edu/v2/users"))
    active["id"] = 1
    assert succeeded(put(client, [grant(id="delegate", can_delegate=True)], uid=8))
    assert succeeded(put(client, [grant(level="school", cohort_id=None, class_id=None)]))
    active["id"] = 8
    response = client.get("/api/v1/permission/edu/v2/users")
    assert succeeded(response), response.text
    row = next(u for u in response.json()["data"]["items"] if u["id"] == 7)
    assert not row["manageable"] and "document" not in row and "active_count" not in row


def test_account_list_distinguishes_unconfigured_revoked_and_invalid(application):
    client, engine, _ = application
    assert succeeded(put(client, []))
    with Session(engine) as session:
        user = session.get(SysUser, 8)
        user.system_variables = {AUTH_KEY: {"schema_version": 2, "grants": "invalid"}}
        session.add(user)
        session.commit()
    result = client.get("/api/v1/permission/edu/v2/users").json()["data"]
    rows = {u["id"]: u for u in result["items"]}
    assert not rows[1]["managed"]
    assert rows[7]["managed"] and rows[7]["active_count"] == 0
    assert not rows[8]["manageable"] and "document" not in rows[8]


def test_changed_revision_after_bulk_preview_rejects_every_write(application):
    client, engine, _ = application
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(["user_id", "expected_revision", "grants_json"])
    for uid in [7, 8]:
        writer.writerow([uid, 0, json.dumps([grant()])])
    payload = {"csv": stream.getvalue()}
    assert succeeded(client.post("/api/v1/permission/edu/v2/batch-bind/preview", json=payload))
    # A different administrator changes one target after preview.
    assert succeeded(put(client, [], uid=8))
    response = client.post("/api/v1/permission/edu/v2/batch-bind/apply", json=payload)
    assert not succeeded(response)
    with Session(engine) as session:
        assert AUTH_KEY not in session.get(SysUser, 7).system_variables
        assert session.get(SysUser, 8).system_variables[AUTH_KEY]["revision"] == 1
        assert len(session.query(AuditOperationLog).all()) == 1
