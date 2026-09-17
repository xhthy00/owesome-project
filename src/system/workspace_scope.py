"""请求级工作空间上下文：Header ``X-Workspace-Oid`` + 数据源归属校验。

工作空间成员校验暂未启用：空间定位与功能尚未明确，请求只解析 oid，
不要求用户在 ``sys_user_ws`` 中有成员关系。
"""

from __future__ import annotations

from fastapi import Depends, Header
from sqlalchemy.orm import Session

from common.exceptions.base import NotFoundException
from datasource.crud import crud_datasource
from datasource.models.datasource import CoreDatasource
from system.api.auth_deps import get_current_user
from system.authz import is_platform_admin
from system.schemas import UserResponse

WORKSPACE_OID_HEADER = "X-Workspace-Oid"


def _parse_oid_header(value: str | None) -> int | None:
    if value is None or not str(value).strip():
        return None
    try:
        return int(str(value).strip())
    except ValueError:
        return None


def is_platform_admin_user(user: UserResponse) -> bool:
    """与 ``authz.is_platform_admin`` 语义一致（JWT 上下文使用 UserResponse / SysUser）。"""
    return is_platform_admin(user)


def get_workspace_oid(
    x_workspace_oid: str | None = Header(None, alias=WORKSPACE_OID_HEADER),
    current_user: UserResponse = Depends(get_current_user),
) -> int:
    """解析当前请求的工作空间 oid：Header 优先，否则回落到用户的默认 ``user.oid``。"""
    parsed = _parse_oid_header(x_workspace_oid)
    return int(parsed if parsed is not None else int(current_user.oid))


def assert_datasource_accessible(
    session: Session,
    user: UserResponse,
    datasource_id: int,
    workspace_oid: int,
) -> CoreDatasource:
    """校验数据源存在且归属当前工作空间。"""
    _ = user
    ds = crud_datasource.get_datasource_by_id(session, datasource_id)
    if ds is None:
        raise NotFoundException("数据源不存在")
    if int(ds.oid) != int(workspace_oid):
        raise NotFoundException("数据源不存在")
    return ds
