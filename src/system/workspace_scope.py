"""请求级工作空间上下文：Header ``X-Workspace-Oid`` + 成员校验 + 数据源归属校验。"""

from __future__ import annotations

from fastapi import Depends, Header
from sqlalchemy.orm import Session

from common.core.database import get_session
from common.exceptions.base import ForbiddenException, NotFoundException
from datasource.crud import crud_datasource
from datasource.models.datasource import CoreDatasource
from system.api.system import get_current_user
from system.models.workspace import SysUserWorkspace
from system.schemas import UserResponse

WORKSPACE_OID_HEADER = "X-Workspace-Oid"


def _parse_oid_header(value: str | None) -> int | None:
    if value is None or not str(value).strip():
        return None
    try:
        return int(str(value).strip())
    except ValueError:
        return None


def user_is_member_of_workspace(session: Session, user_id: int, oid: int) -> bool:
    row = (
        session.query(SysUserWorkspace)
        .filter(SysUserWorkspace.uid == user_id, SysUserWorkspace.oid == oid)
        .first()
    )
    return row is not None


def is_platform_admin_user(user: UserResponse) -> bool:
    """与 ``authz.is_platform_admin`` 语义一致（JWT 上下文使用 UserResponse）。"""
    return user.id == 1 and user.account == "admin"


def _member_workspace_oids(session: Session, user_id: int) -> list[int]:
    rows = (
        session.query(SysUserWorkspace.oid)
        .filter(SysUserWorkspace.uid == user_id)
        .order_by(SysUserWorkspace.oid.asc())
        .all()
    )
    return [int(r[0]) for r in rows]


def get_workspace_oid(
    x_workspace_oid: str | None = Header(None, alias=WORKSPACE_OID_HEADER),
    current_user: UserResponse = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> int:
    """解析当前请求的工作空间 oid：Header 优先，否则回落到用户的默认 ``user.oid``。

    非平台管理员须为所选空间成员。若 Header / 默认 ``oid`` 指向非成员空间（常见为
    历史 localStorage 或列表曾展示全量空间），则自动回落到该用户任意已加入空间，
    避免误配导致全站 403。
    """
    parsed = _parse_oid_header(x_workspace_oid)
    candidate = int(parsed if parsed is not None else int(current_user.oid))
    if is_platform_admin_user(current_user):
        return candidate

    uid = current_user.id
    if user_is_member_of_workspace(session, uid, candidate):
        return candidate

    member_oids = _member_workspace_oids(session, uid)
    if not member_oids:
        raise ForbiddenException("无权访问该工作空间或您不是该空间成员")

    default_oid = int(current_user.oid)
    if default_oid in member_oids:
        return default_oid
    return member_oids[0]


def assert_datasource_accessible(
    session: Session,
    user: UserResponse,
    datasource_id: int,
    workspace_oid: int,
) -> CoreDatasource:
    """校验数据源存在、归属当前工作空间，且（非平台管理员时）用户为该空间成员。"""
    ds = crud_datasource.get_datasource_by_id(session, datasource_id)
    if ds is None:
        raise NotFoundException("数据源不存在")
    if int(ds.oid) != int(workspace_oid):
        raise NotFoundException("数据源不存在")
    if is_platform_admin_user(user):
        return ds
    if not user_is_member_of_workspace(session, user.id, int(workspace_oid)):
        raise ForbiddenException("无权访问该工作空间下的数据源")
    return ds
