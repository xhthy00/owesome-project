"""审计日志查询 API：3 个分页端点，权限隔离在 crud 层。"""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session

from audit.crud.audit import pager_access_log, pager_login_log, pager_operation_log
from common.core.database import get_session
from common.schemas.response import success_response
from system.api.auth_deps import get_current_user
from system.models import SysUser

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("/access/pager/{page_num}/{page_size}")
def list_access_log(
    page_num: int,
    page_size: int,
    user_id: Optional[int] = Query(default=None),
    datasource_id: Optional[int] = Query(default=None),
    success: Optional[bool] = Query(default=None),
    start_time: Optional[int] = Query(default=None),
    end_time: Optional[int] = Query(default=None),
    session: Session = Depends(get_session),
    current_user: SysUser = Depends(get_current_user),
):
    total, items = pager_access_log(
        session, current_user, page_num, page_size,
        user_id=user_id, datasource_id=datasource_id, success=success,
        start_time=start_time, end_time=end_time,
    )
    return success_response(data={"total": total, "items": [_access_to_dict(i) for i in items]})


@router.get("/operation/pager/{page_num}/{page_size}")
def list_operation_log(
    page_num: int,
    page_size: int,
    user_id: Optional[int] = Query(default=None),
    resource_type: Optional[str] = Query(default=None),
    operation_type: Optional[str] = Query(default=None),
    success: Optional[bool] = Query(default=None),
    start_time: Optional[int] = Query(default=None),
    end_time: Optional[int] = Query(default=None),
    session: Session = Depends(get_session),
    current_user: SysUser = Depends(get_current_user),
):
    total, items = pager_operation_log(
        session, current_user, page_num, page_size,
        user_id=user_id, resource_type=resource_type, operation_type=operation_type,
        success=success, start_time=start_time, end_time=end_time,
    )
    return success_response(data={"total": total, "items": [_operation_to_dict(i) for i in items]})


@router.get("/login/pager/{page_num}/{page_size}")
def list_login_log(
    page_num: int,
    page_size: int,
    account: Optional[str] = Query(default=None),
    success: Optional[bool] = Query(default=None),
    start_time: Optional[int] = Query(default=None),
    end_time: Optional[int] = Query(default=None),
    session: Session = Depends(get_session),
    current_user: SysUser = Depends(get_current_user),
):
    total, items = pager_login_log(
        session, current_user, page_num, page_size,
        account=account, success=success, start_time=start_time, end_time=end_time,
    )
    return success_response(data={"total": total, "items": [_login_to_dict(i) for i in items]})


def _access_to_dict(log) -> dict:
    return {
        "id": log.id,
        "trace_id": log.trace_id,
        "user_id": log.user_id,
        "user_account": log.user_account,
        "workspace_oid": log.workspace_oid,
        "ip": log.ip,
        "user_agent": log.user_agent,
        "success": log.success,
        "error_msg": log.error_msg,
        "elapsed_ms": log.elapsed_ms,
        "created_at": log.created_at.isoformat() if log.created_at else None,
        "request_method": log.request_method,
        "request_path": log.request_path,
        "datasource_id": log.datasource_id,
        "query_text": log.query_text,
    }


def _operation_to_dict(log) -> dict:
    return {
        "id": log.id,
        "trace_id": log.trace_id,
        "user_id": log.user_id,
        "user_account": log.user_account,
        "workspace_oid": log.workspace_oid,
        "ip": log.ip,
        "user_agent": log.user_agent,
        "success": log.success,
        "error_msg": log.error_msg,
        "elapsed_ms": log.elapsed_ms,
        "created_at": log.created_at.isoformat() if log.created_at else None,
        "operation_type": log.operation_type,
        "resource_type": log.resource_type,
        "resource_id": log.resource_id,
        "request_method": log.request_method,
        "request_path": log.request_path,
        "detail": log.detail,
    }


def _login_to_dict(log) -> dict:
    return {
        "id": log.id,
        "trace_id": log.trace_id,
        "user_id": log.user_id,
        "user_account": log.user_account,
        "workspace_oid": log.workspace_oid,
        "ip": log.ip,
        "user_agent": log.user_agent,
        "success": log.success,
        "error_msg": log.error_msg,
        "created_at": log.created_at.isoformat() if log.created_at else None,
        "account": log.account,
        "fail_reason": log.fail_reason,
    }
