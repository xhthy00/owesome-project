"""审计日志 crud 权限隔离与分页。"""
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from audit.api.audit import router as audit_router
from audit.crud.audit import pager_access_log, pager_login_log, pager_operation_log
from audit.models import AuditAccessLog, AuditLoginLog, AuditOperationLog
from common.core.database import get_session


def _utcnow_naive() -> datetime:
    """与 created_at 列一致的 naive UTC wall-time（写入器存的是 UTC wall-time）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _seed_access(session, user_id, account):
    session.add(AuditAccessLog(
        request_method="POST", request_path="/x", query_text="q",
        user_id=user_id, user_account=account, success=True,
    ))
    session.commit()


def _seed_operation(session, user_id, account):
    session.add(AuditOperationLog(
        operation_type="create", resource_type="user", resource_id="1",
        user_id=user_id, user_account=account, success=True,
    ))
    session.commit()


def _seed_login(session, account, success, user_id=None):
    session.add(AuditLoginLog(
        account=account, success=success, user_account=account, user_id=user_id,
    ))
    session.commit()


def test_admin_sees_all_access_logs(session, admin_user, normal_user):
    _seed_access(session, 1, "admin")
    _seed_access(session, 2, "allen")
    total, rows = pager_access_log(session, admin_user, 1, 10)
    assert total == 2
    assert {r.user_account for r in rows} == {"admin", "allen"}


def test_normal_user_sees_only_own_access_logs(session, admin_user, normal_user):
    _seed_access(session, 1, "admin")
    _seed_access(session, 2, "allen")
    total, rows = pager_access_log(session, normal_user, 1, 10)
    assert total == 1
    assert rows[0].user_account == "allen"


def test_admin_sees_all_operation_logs(session, admin_user, normal_user):
    _seed_operation(session, 1, "admin")
    _seed_operation(session, 2, "allen")
    total, rows = pager_operation_log(session, admin_user, 1, 10)
    assert total == 2


def test_normal_user_sees_only_own_operation_logs(session, admin_user, normal_user):
    _seed_operation(session, 1, "admin")
    _seed_operation(session, 2, "allen")
    total, rows = pager_operation_log(session, normal_user, 1, 10)
    assert total == 1
    assert rows[0].user_account == "allen"


def test_admin_sees_all_login_logs_including_failures(session, admin_user):
    _seed_login(session, "allen", False, user_id=None)
    _seed_login(session, "admin", True, user_id=1)
    total, rows = pager_login_log(session, admin_user, 1, 10)
    assert total == 2


def test_normal_user_sees_only_own_login_logs_by_account(session, admin_user, normal_user):
    # allen 的失败登录（user_id 为空）+ admin 的登录
    _seed_login(session, "allen", False, user_id=None)
    _seed_login(session, "admin", True, user_id=1)
    total, rows = pager_login_log(session, normal_user, 1, 10)
    assert total == 1
    assert rows[0].account == "allen"
    assert rows[0].success is False


def test_access_log_pagination(session, admin_user):
    for i in range(5):
        _seed_access(session, 1, "admin")
    total, page1 = pager_access_log(session, admin_user, 1, 2)
    total2, page2 = pager_access_log(session, admin_user, 2, 2)
    assert total == 5
    assert len(page1) == 2
    assert len(page2) == 2
    # 不重复
    assert {r.id for r in page1}.isdisjoint({r.id for r in page2})


def test_access_log_filter_by_datasource(session, admin_user):
    session.add(AuditAccessLog(
        request_method="POST", request_path="/x", datasource_id=1, query_text="a",
        user_id=1, user_account="admin", success=True,
    ))
    session.add(AuditAccessLog(
        request_method="POST", request_path="/x", datasource_id=2, query_text="b",
        user_id=1, user_account="admin", success=True,
    ))
    session.commit()
    total, rows = pager_access_log(session, admin_user, 1, 10, datasource_id=1)
    assert total == 1
    assert rows[0].datasource_id == 1


def test_access_log_filter_by_time_range(session, admin_user):
    # 两条 created_at 明显不同的访问日志：一条 2 小时前，一条现在。
    now_naive = _utcnow_naive()
    old = AuditAccessLog(
        request_method="POST", request_path="/x", query_text="old",
        user_id=1, user_account="admin", success=True,
        created_at=now_naive - timedelta(hours=2),
    )
    recent = AuditAccessLog(
        request_method="POST", request_path="/x", query_text="recent",
        user_id=1, user_account="admin", success=True,
        created_at=now_naive,
    )
    session.add_all([old, recent])
    session.commit()
    # 时间窗 [now-1h, now+1min]：仅包含 recent，排除 old。
    start_ms = int((datetime.now(timezone.utc) - timedelta(hours=1)).timestamp() * 1000)
    end_ms = int((datetime.now(timezone.utc) + timedelta(minutes=1)).timestamp() * 1000)
    total, rows = pager_access_log(session, admin_user, 1, 10, start_time=start_ms, end_time=end_ms)
    assert total == 1
    assert rows[0].query_text == "recent"


def test_normal_user_cannot_escalate_access_log(session, admin_user, normal_user):
    # 普通用户传入 user_id=1（admin 的 id）试图看 admin 的日志，应被忽略。
    _seed_access(session, 1, "admin")
    _seed_access(session, 2, "allen")
    total, rows = pager_access_log(session, normal_user, 1, 10, user_id=1)
    assert total == 1
    assert rows[0].user_account == "allen"


def test_normal_user_cannot_escalate_operation_log(session, admin_user, normal_user):
    _seed_operation(session, 1, "admin")
    _seed_operation(session, 2, "allen")
    total, rows = pager_operation_log(session, normal_user, 1, 10, user_id=1)
    assert total == 1
    assert rows[0].user_account == "allen"


def test_normal_user_cannot_escalate_login_log(session, admin_user, normal_user):
    # 普通用户传 account="admin" 试图看 admin 的登录，应被忽略，仅返回 allen 自己的失败登录。
    _seed_login(session, "allen", False, user_id=None)
    _seed_login(session, "admin", True, user_id=1)
    total, rows = pager_login_log(session, normal_user, 1, 10, account="admin")
    assert total == 1
    assert rows[0].account == "allen"
    assert rows[0].success is False


def _build_app(session, current_user):
    app = FastAPI()
    app.include_router(audit_router, prefix="/api/v1")
    app.dependency_overrides[get_session] = lambda: session
    from system.api.auth_deps import get_current_user
    app.dependency_overrides[get_current_user] = lambda: current_user
    return app


def test_api_access_log_admin(session, admin_user):
    _seed_access(session, 1, "admin")
    _seed_access(session, 2, "allen")
    app = _build_app(session, admin_user)
    resp = TestClient(app).get("/api/v1/audit/access/pager/1/10")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 200
    assert body["data"]["total"] == 2


def test_api_access_log_normal_user_isolated(session, admin_user, normal_user):
    _seed_access(session, 1, "admin")
    _seed_access(session, 2, "allen")
    app = _build_app(session, normal_user)
    resp = TestClient(app).get("/api/v1/audit/access/pager/1/10")
    body = resp.json()
    assert body["data"]["total"] == 1
    assert body["data"]["items"][0]["user_account"] == "allen"


def test_api_login_log_normal_user_by_account(session, admin_user, normal_user):
    _seed_login(session, "allen", False, user_id=None)
    _seed_login(session, "admin", True, user_id=1)
    app = _build_app(session, normal_user)
    resp = TestClient(app).get("/api/v1/audit/login/pager/1/10")
    body = resp.json()
    assert body["data"]["total"] == 1
    assert body["data"]["items"][0]["account"] == "allen"
