"""审计日志 fire-and-forget 异步写入器。

所有写入走 asyncio.create_task + asyncio.to_thread 后台执行，
写库失败只 logger.warning，绝不抛出、不影响主业务。
每个写入使用独立 session（由 session_factory 创建），用完即关。

session_factory 契约：必须返回一个 Session，且该 Session 本身是上下文
管理器（``__exit__`` 时自行关闭）。生产传 ``common.core.database.SessionLocal``；
测试可传返回共享 session 的 lambda（StaticPool 共享底层连接，提交后行可见）。
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Callable, Optional

from sqlmodel import Session

from audit.models import AuditAccessLog, AuditLoginLog, AuditOperationLog
from common.core.trace import get_trace_id

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], Session]

_QUERY_TEXT_LIMIT = 500
_DETAIL_LIMIT = 2000
_ERROR_MSG_LIMIT = 500
_USER_AGENT_LIMIT = 500


def _truncate(text: Optional[str], limit: int) -> Optional[str]:
    if text is None:
        return None
    return text[:limit]


def _sync_write_access(factory: SessionFactory, **fields) -> None:
    try:
        with factory() as s:
            s.add(AuditAccessLog(**fields))
            s.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning("write access log failed: %s", e)


def _sync_write_operation(factory: SessionFactory, **fields) -> None:
    try:
        with factory() as s:
            s.add(AuditOperationLog(**fields))
            s.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning("write operation log failed: %s", e)


def _sync_write_login(factory: SessionFactory, **fields) -> None:
    try:
        with factory() as s:
            s.add(AuditLoginLog(**fields))
            s.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning("write login log failed: %s", e)


def _fire_and_forget(sync_fn, factory: SessionFactory, **fields) -> None:
    """提交后台写入任务；无运行中事件循环时降级为同步写（如测试/同步端点）。"""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(asyncio.to_thread(sync_fn, factory, **fields))
    except RuntimeError:
        # 无运行中事件循环（同步上下文），直接同步写
        sync_fn(factory, **fields)


def log_access(
    *,
    session_factory: SessionFactory,
    request_method: str,
    request_path: str,
    datasource_id: Optional[int] = None,
    query_text: Optional[str] = None,
    user_id: Optional[int] = None,
    user_account: Optional[str] = None,
    workspace_oid: Optional[int] = None,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
    success: bool = True,
    error_msg: Optional[str] = None,
    elapsed_ms: Optional[int] = None,
) -> None:
    fields = dict(
        trace_id=get_trace_id() or "-",
        user_id=user_id,
        user_account=user_account,
        workspace_oid=workspace_oid,
        ip=ip,
        user_agent=_truncate(user_agent, _USER_AGENT_LIMIT),
        success=success,
        error_msg=_truncate(error_msg, _ERROR_MSG_LIMIT),
        elapsed_ms=elapsed_ms,
        created_at=datetime.now(timezone.utc),
        request_method=request_method,
        request_path=request_path,
        datasource_id=datasource_id,
        query_text=_truncate(query_text, _QUERY_TEXT_LIMIT),
    )
    _fire_and_forget(_sync_write_access, session_factory, **fields)


def log_operation(
    *,
    session_factory: SessionFactory,
    operation_type: str,
    resource_type: str,
    resource_id: Optional[str] = None,
    request_method: Optional[str] = None,
    request_path: Optional[str] = None,
    detail: Optional[str] = None,
    user_id: Optional[int] = None,
    user_account: Optional[str] = None,
    workspace_oid: Optional[int] = None,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
    success: bool = True,
    error_msg: Optional[str] = None,
    elapsed_ms: Optional[int] = None,
) -> None:
    fields = dict(
        trace_id=get_trace_id() or "-",
        user_id=user_id,
        user_account=user_account,
        workspace_oid=workspace_oid,
        ip=ip,
        user_agent=_truncate(user_agent, _USER_AGENT_LIMIT),
        success=success,
        error_msg=_truncate(error_msg, _ERROR_MSG_LIMIT),
        elapsed_ms=elapsed_ms,
        created_at=datetime.now(timezone.utc),
        operation_type=operation_type,
        resource_type=resource_type,
        resource_id=resource_id,
        request_method=request_method or "",
        request_path=request_path or "",
        detail=_truncate(detail, _DETAIL_LIMIT),
    )
    _fire_and_forget(_sync_write_operation, session_factory, **fields)


def log_login(
    *,
    session_factory: SessionFactory,
    account: str,
    success: bool,
    user_id: Optional[int] = None,
    user_account: Optional[str] = None,
    workspace_oid: Optional[int] = None,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
    fail_reason: Optional[str] = None,
) -> None:
    fields = dict(
        trace_id=get_trace_id() or "-",
        user_id=user_id,
        user_account=user_account or account,
        workspace_oid=workspace_oid,
        ip=ip,
        user_agent=_truncate(user_agent, _USER_AGENT_LIMIT),
        success=success,
        elapsed_ms=None,
        created_at=datetime.now(timezone.utc),
        account=account,
        fail_reason=fail_reason,
    )
    _fire_and_forget(_sync_write_login, session_factory, **fields)
