"""审计日志分页查询，含权限隔离：超管看全部，普通用户仅看自己。"""
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Session, func, select

from audit.models import AuditAccessLog, AuditLoginLog, AuditOperationLog
from system.authz import is_platform_admin
from system.models import SysUser


def pager_access_log(
    session: Session,
    current_user: SysUser,
    page_num: int,
    page_size: int,
    user_id: Optional[int] = None,
    datasource_id: Optional[int] = None,
    success: Optional[bool] = None,
    start_time: Optional[int] = None,
    end_time: Optional[int] = None,
):
    stmt = select(AuditAccessLog)
    if not is_platform_admin(current_user):
        stmt = stmt.where(AuditAccessLog.user_id == current_user.id)
    else:
        if user_id is not None:
            stmt = stmt.where(AuditAccessLog.user_id == user_id)
    if datasource_id is not None:
        stmt = stmt.where(AuditAccessLog.datasource_id == datasource_id)
    if success is not None:
        stmt = stmt.where(AuditAccessLog.success == success)
    if start_time is not None:
        stmt = stmt.where(AuditAccessLog.created_at >= datetime.fromtimestamp(start_time / 1000, tz=timezone.utc).replace(tzinfo=None))
    if end_time is not None:
        stmt = stmt.where(AuditAccessLog.created_at <= datetime.fromtimestamp(end_time / 1000, tz=timezone.utc).replace(tzinfo=None))
    total = session.exec(select(func.count()).select_from(stmt.subquery())).one()
    rows = session.exec(
        stmt.order_by(AuditAccessLog.created_at.desc())
        .offset((page_num - 1) * page_size)
        .limit(page_size)
    ).all()
    return total, rows


def pager_operation_log(
    session: Session,
    current_user: SysUser,
    page_num: int,
    page_size: int,
    user_id: Optional[int] = None,
    resource_type: Optional[str] = None,
    operation_type: Optional[str] = None,
    success: Optional[bool] = None,
    start_time: Optional[int] = None,
    end_time: Optional[int] = None,
):
    stmt = select(AuditOperationLog)
    if not is_platform_admin(current_user):
        stmt = stmt.where(AuditOperationLog.user_id == current_user.id)
    else:
        if user_id is not None:
            stmt = stmt.where(AuditOperationLog.user_id == user_id)
    if resource_type is not None:
        stmt = stmt.where(AuditOperationLog.resource_type == resource_type)
    if operation_type is not None:
        stmt = stmt.where(AuditOperationLog.operation_type == operation_type)
    if success is not None:
        stmt = stmt.where(AuditOperationLog.success == success)
    if start_time is not None:
        stmt = stmt.where(AuditOperationLog.created_at >= datetime.fromtimestamp(start_time / 1000, tz=timezone.utc).replace(tzinfo=None))
    if end_time is not None:
        stmt = stmt.where(AuditOperationLog.created_at <= datetime.fromtimestamp(end_time / 1000, tz=timezone.utc).replace(tzinfo=None))
    total = session.exec(select(func.count()).select_from(stmt.subquery())).one()
    rows = session.exec(
        stmt.order_by(AuditOperationLog.created_at.desc())
        .offset((page_num - 1) * page_size)
        .limit(page_size)
    ).all()
    return total, rows


def pager_login_log(
    session: Session,
    current_user: SysUser,
    page_num: int,
    page_size: int,
    account: Optional[str] = None,
    success: Optional[bool] = None,
    start_time: Optional[int] = None,
    end_time: Optional[int] = None,
):
    stmt = select(AuditLoginLog)
    if not is_platform_admin(current_user):
        # 普通用户按 account 匹配（含登录失败记录，失败时 user_id 为空）
        stmt = stmt.where(AuditLoginLog.account == current_user.account)
    else:
        if account is not None:
            stmt = stmt.where(AuditLoginLog.account == account)
    if success is not None:
        stmt = stmt.where(AuditLoginLog.success == success)
    if start_time is not None:
        stmt = stmt.where(AuditLoginLog.created_at >= datetime.fromtimestamp(start_time / 1000, tz=timezone.utc).replace(tzinfo=None))
    if end_time is not None:
        stmt = stmt.where(AuditLoginLog.created_at <= datetime.fromtimestamp(end_time / 1000, tz=timezone.utc).replace(tzinfo=None))
    total = session.exec(select(func.count()).select_from(stmt.subquery())).one()
    rows = session.exec(
        stmt.order_by(AuditLoginLog.created_at.desc())
        .offset((page_num - 1) * page_size)
        .limit(page_size)
    ).all()
    return total, rows
