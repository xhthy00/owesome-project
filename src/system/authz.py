"""平台与工作空间维度的授权判断（数据权限管理、执行侧豁免）。"""

from __future__ import annotations

from sqlalchemy.orm import Session

from system.models.user import SysUser
from system.models.workspace import SysUserWorkspace


def is_platform_admin(user: SysUser | None) -> bool:
    """与 ``permission._role_code`` 一致：内置 admin 账号视为平台管理员。"""
    if user is None:
        return False
    return user.id == 1 and user.account == "admin"


def is_workspace_admin(session: Session, user_id: int) -> bool:
    """任一工作空间中 ``weight == 1`` 即视为工作空间管理员。"""
    row = (
        session.query(SysUserWorkspace)
        .filter(SysUserWorkspace.uid == user_id, SysUserWorkspace.weight == 1)
        .first()
    )
    return row is not None


def can_manage_data_permissions(session: Session, user: SysUser | None) -> bool:
    """管理规则组 / ``ds_permission`` 写接口：平台管理员或任一空间管理员。"""
    if user is None:
        return False
    if is_platform_admin(user):
        return True
    return is_workspace_admin(session, user.id)


def bypasses_column_visibility(session: Session, user: SysUser | None) -> bool:
    """列级隐藏（schema/校验/结果列裁剪）是否跳过：平台管理员或任一工作空间管理员。"""
    if user is None:
        return False
    if is_platform_admin(user):
        return True
    return is_workspace_admin(session, user.id)


def bypasses_data_row_column_scope(user: SysUser | None) -> bool:
    """是否跳过**行级**权限谓词合并（如 WHERE 注入）；仅平台管理员跳过。

    列级可见性豁免见 ``bypasses_column_visibility``。"""
    return is_platform_admin(user)
