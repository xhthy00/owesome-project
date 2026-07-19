"""操作/访问日志装饰器：成功失败都记录，不阻塞原函数返回/异常。

装饰器内部走生产 SessionLocal（fire-and-forget），测试通过 monkeypatch
``audit.service.decorators.SessionLocal`` 指向测试共享 session（StaticPool），
使写库行在测试 session 的 select 中可见。
"""
import json

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlmodel import Session, select

from audit.models import AuditAccessLog, AuditOperationLog
from audit.service import decorators as dec_mod
from audit.service.decorators import audit_access, audit_operation
from common.core.database import get_session
from system.api.auth_deps import get_current_user
from system.models import SysUser


def _factory(test_session: Session):
    """返回一个 session 工厂：每次新开一个绑定同一 engine 的 Session。
    避免装饰器 writer ``with factory() as s`` 调用 close() 关掉测试共享 session。
    StaticPool 保证所有 Session 共享单一底层连接，测试 session 可读到提交行。
    """
    engine = test_session.get_bind()

    def _make():
        return Session(engine)

    return _make


def _build_app(session, current_user, with_failing_op=False):
    app = FastAPI()
    router = APIRouter()

    @router.post("/chat/generate-sql")
    @audit_access(datasource_id_arg="datasource_id", query_arg="question")
    def gen_sql(
        request: Request,
        datasource_id: int = 1,
        question: str = "q",
        session: Session = Depends(get_session),
        current_user: SysUser = Depends(get_current_user),
    ):
        return {"ok": True}

    @router.post("/user")
    @audit_operation(
        operation_type="create",
        resource_type="user",
        resource_id_arg="user_id",
        detail_arg="body",
    )
    def create_user(
        request: Request,
        user_id: str = None,
        body: dict = None,
        session: Session = Depends(get_session),
        current_user: SysUser = Depends(get_current_user),
    ):
        return {"ok": True}

    if with_failing_op:

        @router.delete("/user/{uid}")
        @audit_operation(
            operation_type="delete", resource_type="user", resource_id_arg="uid"
        )
        def delete_user(
            request: Request,
            uid: int,
            session: Session = Depends(get_session),
            current_user: SysUser = Depends(get_current_user),
        ):
            raise ValueError("boom")

    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_current_user] = lambda: current_user
    return app


def test_access_decorator_logs_on_success(session, normal_user, monkeypatch):
    monkeypatch.setattr(dec_mod, "SessionLocal", _factory(session))
    app = _build_app(session, normal_user)
    resp = TestClient(app, raise_server_exceptions=False).post(
        "/api/v1/chat/generate-sql?datasource_id=1&question=hello"
    )
    assert resp.status_code == 200
    rows = session.exec(select(AuditAccessLog)).all()
    assert len(rows) == 1
    assert rows[0].query_text == "hello"
    assert rows[0].success is True
    assert rows[0].request_path == "/api/v1/chat/generate-sql"
    assert rows[0].datasource_id == 1
    assert rows[0].user_account == "allen"


def test_operation_decorator_logs_on_success(session, admin_user, monkeypatch):
    monkeypatch.setattr(dec_mod, "SessionLocal", _factory(session))
    app = _build_app(session, admin_user)
    resp = TestClient(app, raise_server_exceptions=False).post(
        "/api/v1/user?user_id=5", json={"account": "bob"}
    )
    assert resp.status_code == 200
    rows = session.exec(select(AuditOperationLog)).all()
    assert len(rows) == 1
    assert rows[0].operation_type == "create"
    assert rows[0].resource_type == "user"
    assert rows[0].resource_id == "5"
    assert rows[0].user_account == "admin"
    assert rows[0].detail is not None
    assert "account" in rows[0].detail


def test_operation_decorator_logs_on_failure_and_propagates(session, admin_user, monkeypatch):
    monkeypatch.setattr(dec_mod, "SessionLocal", _factory(session))
    app = _build_app(session, admin_user, with_failing_op=True)
    resp = TestClient(app, raise_server_exceptions=False).delete("/api/v1/user/7")
    # ValueError 被 FastAPI 转 500；raise_server_exceptions=False 阻止 re-raise 给测试。
    assert resp.status_code == 500
    rows = session.exec(
        select(AuditOperationLog).where(AuditOperationLog.operation_type == "delete")
    ).all()
    assert len(rows) == 1
    assert rows[0].success is False
    assert rows[0].error_msg is not None
    assert rows[0].resource_id == "7"


class _Body(BaseModel):
    account: str
    name: str


def _build_app_with_pydantic_body(session, current_user):
    app = FastAPI()
    router = APIRouter()

    @router.post("/user-pydantic")
    @audit_operation(
        operation_type="create", resource_type="user", detail_arg="body"
    )
    def create_user_pydantic(
        request: Request,
        body: _Body,
        session: Session = Depends(get_session),
        current_user: SysUser = Depends(get_current_user),
    ):
        return {"ok": True}

    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_current_user] = lambda: current_user
    return app


def test_operation_decorator_serializes_pydantic_detail(session, admin_user, monkeypatch):
    monkeypatch.setattr(dec_mod, "SessionLocal", _factory(session))
    app = _build_app_with_pydantic_body(session, admin_user)
    resp = TestClient(app, raise_server_exceptions=False).post(
        "/api/v1/user-pydantic", json={"account": "bob", "name": "Bob"}
    )
    assert resp.status_code == 200
    rows = session.exec(
        select(AuditOperationLog).where(AuditOperationLog.operation_type == "create")
    ).all()
    assert len(rows) == 1
    parsed = json.loads(rows[0].detail)
    assert parsed["account"] == "bob"
    assert parsed["name"] == "Bob"


def test_decorator_swallows_log_write_error(session, normal_user, monkeypatch):
    monkeypatch.setattr(dec_mod, "SessionLocal", _factory(session))

    def _boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(dec_mod, "log_access", _boom)
    app = _build_app(session, normal_user)
    resp = TestClient(app, raise_server_exceptions=False).post(
        "/api/v1/chat/generate-sql?question=hi"
    )
    assert resp.status_code == 200
    assert session.exec(select(AuditAccessLog)).all() == []
