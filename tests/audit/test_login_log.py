"""登录日志采集：成功 / 失败各记一条。"""
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from audit.models import AuditLoginLog
from common.core.database import get_session
from common.core.security import get_password_hash
from system.api.system import router as system_router
from system.models import SysUser


def _build_app(engine, session, monkeypatch):
    """构造 app，并把 system.api.system.SessionLocal 替换为返回新 session 的工厂
    （与测试 session 共享 StaticPool 的底层连接，故日志提交后行可见），
    使 log_login 写入测试 sqlite 而非真实库。

    不能直接返回 fixture 的测试 session：writer 的 ``with factory() as s`` 在
    ``__exit__`` 会 close 掉它，导致系统代码后续访问的 ORM 对象被 detach。
    """
    import system.api.system as sysmod
    monkeypatch.setattr(sysmod, "SessionLocal", lambda: Session(engine))
    app = FastAPI()
    app.include_router(system_router, prefix="/api/v1")
    app.dependency_overrides[get_session] = lambda: session
    return app


def test_login_success_logs(engine, session, monkeypatch):
    app = _build_app(engine, session, monkeypatch)
    client = TestClient(app)
    client.post("/api/v1/system/register", json={"account": "allen", "name": "艾伦", "password": "123456"})
    resp = client.post("/api/v1/system/login", data={"username": "allen", "password": "123456"})
    assert resp.status_code == 200
    rows = session.exec(select(AuditLoginLog).where(AuditLoginLog.account == "allen")).all()
    success_rows = [r for r in rows if r.success]
    assert len(success_rows) == 1
    assert success_rows[0].fail_reason is None


def test_login_wrong_password_logs_failure(engine, session, monkeypatch):
    app = _build_app(engine, session, monkeypatch)
    # 失败登录由 UnauthorizedException 抛出；精简 app 未注册异常处理器，
    # 用 raise_server_exceptions=False 让 TestClient 返回 401 响应而非重抛异常。
    client = TestClient(app, raise_server_exceptions=False)
    client.post("/api/v1/system/register", json={"account": "allen", "name": "艾伦", "password": "123456"})
    client.post("/api/v1/system/login", data={"username": "allen", "password": "wrong"})
    rows = session.exec(select(AuditLoginLog).where(AuditLoginLog.account == "allen")).all()
    fail_rows = [r for r in rows if not r.success]
    assert len(fail_rows) == 1
    assert fail_rows[0].fail_reason == "密码错误"


def test_login_account_not_found_logs_failure(engine, session, monkeypatch):
    app = _build_app(engine, session, monkeypatch)
    client = TestClient(app, raise_server_exceptions=False)
    client.post("/api/v1/system/login", data={"username": "ghost", "password": "x"})
    rows = session.exec(select(AuditLoginLog).where(AuditLoginLog.account == "ghost")).all()
    fail_rows = [r for r in rows if not r.success]
    assert len(fail_rows) == 1
    assert fail_rows[0].fail_reason == "账号不存在"


def test_login_disabled_account_logs_failure(engine, session, monkeypatch):
    """status != 1 的账号即使密码正确也判失败，且 fail_reason 为"账号已禁用"。

    直接构造 status=0 的 SysUser 写入测试库（密码用真实 hash），覆盖 status
    检查早于密码检查的分支——这是最易因 login 分支顺序调整而静默回归的场景。
    """
    app = _build_app(engine, session, monkeypatch)
    client = TestClient(app, raise_server_exceptions=False)
    session.add(
        SysUser(
            account="banned",
            name="禁用用户",
            password=get_password_hash("123456"),
            oid=1,
            status=0,
            create_time=0,
        )
    )
    session.commit()
    client.post("/api/v1/system/login", data={"username": "banned", "password": "123456"})
    rows = session.exec(select(AuditLoginLog).where(AuditLoginLog.account == "banned")).all()
    fail_rows = [r for r in rows if not r.success]
    assert len(fail_rows) == 1
    assert fail_rows[0].fail_reason == "账号已禁用"


def test_me_returns_is_platform_admin(engine, session, admin_user, monkeypatch):
    app = _build_app(engine, session, monkeypatch)
    client = TestClient(app)
    from system.api.auth_deps import get_current_user
    app.dependency_overrides[get_current_user] = lambda: admin_user
    resp = client.get("/api/v1/system/me")
    body = resp.json()
    assert body["data"]["is_platform_admin"] is True
