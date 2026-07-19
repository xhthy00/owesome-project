"""审计日志异步写入器：截断 + fire-and-forget 不抛错。"""
import asyncio

from sqlmodel import Session, select

from audit.models import AuditAccessLog, AuditLoginLog, AuditOperationLog
from audit.service.writer import log_access, log_login, log_operation


def _wait_bg():
    """排空后台 to_thread 任务。"""
    async def _drain():
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
    asyncio.run(_drain())


def test_log_access_writes_row(session):
    log_access(
        session_factory=lambda: session,
        request_method="POST",
        request_path="/api/v1/chat/generate-sql",
        datasource_id=1,
        query_text="本月订单最多前三名",
        user_id=2,
        user_account="allen",
        success=True,
        elapsed_ms=120,
    )
    _wait_bg()
    rows = session.exec(select(AuditAccessLog)).all()
    assert len(rows) == 1
    assert rows[0].query_text == "本月订单最多前三名"
    assert rows[0].success is True


def test_log_access_truncates_query_text(session):
    long_text = "x" * 1000
    log_access(
        session_factory=lambda: session,
        request_method="POST",
        request_path="/api/v1/chat/generate-sql",
        query_text=long_text,
        user_id=2,
        success=True,
    )
    _wait_bg()
    rows = session.exec(select(AuditAccessLog)).all()
    assert len(rows[0].query_text) == 500


def test_log_operation_writes_row(session):
    log_operation(
        session_factory=lambda: session,
        operation_type="create",
        resource_type="user",
        resource_id="3",
        request_method="POST",
        request_path="/api/v1/user",
        detail='{"account":"bob"}',
        user_id=1,
        user_account="admin",
        success=True,
    )
    _wait_bg()
    rows = session.exec(select(AuditOperationLog)).all()
    assert len(rows) == 1
    assert rows[0].operation_type == "create"
    assert rows[0].resource_type == "user"
    assert rows[0].request_method == "POST"
    assert rows[0].request_path == "/api/v1/user"


def test_log_login_writes_success_and_fail(session):
    log_login(session_factory=lambda: session, account="allen", success=True, user_id=2, user_account="allen")
    log_login(
        session_factory=lambda: session,
        account="allen",
        success=False,
        fail_reason="密码错误",
        user_account="allen",
    )
    _wait_bg()
    rows = session.exec(select(AuditLoginLog)).all()
    assert len(rows) == 2
    assert {r.success for r in rows} == {True, False}


def test_log_access_does_not_raise_on_db_error(session, monkeypatch):
    """写库失败时不应抛出，保证主业务不受影响。"""
    def _boom(self, *a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(Session, "add", _boom)
    # 不应抛异常
    log_access(
        session_factory=lambda: session,
        request_method="POST",
        request_path="/x",
        query_text="q",
        user_id=2,
        success=True,
    )
    _wait_bg()


def test_log_access_async_path_writes_row(engine, session):
    """覆盖生产异步路径：在运行中事件循环内调用，走 create_task + to_thread。"""
    # 给工作线程一个全新 session（绑定同一 engine，StaticPool 共享连接），
    # 避免跨线程复用测试 session；提交后行经共享连接对测试 session 可见。
    def factory():
        return Session(engine)

    async def runner():
        # 此处存在运行中 loop -> _fire_and_forget 走异步分支
        log_access(
            session_factory=factory,
            request_method="POST",
            request_path="/x",
            query_text="async-q",
            user_id=2,
            user_account="allen",
            success=True,
        )
        # 排空本 loop 内创建的后台任务
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        await asyncio.gather(*pending, return_exceptions=True)

    asyncio.run(runner())
    rows = session.exec(select(AuditAccessLog)).all()
    assert len(rows) == 1
    assert rows[0].query_text == "async-q"
