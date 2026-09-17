"""平台与工作空间维度的授权判断（数据权限管理、执行侧豁免）。"""

from __future__ import annotations

from sqlalchemy.orm import Session

from system.models.user import SysUser
from system.models.workspace import SysUserWorkspace

SYS_ROLE_SUPER_ADMIN = "super_admin"
SYS_ROLE_PLATFORM_ADMIN = "platform_admin"


def _sys_role(user) -> str:
    return str(getattr(user, "sys_role", None) or "").strip()


def is_legacy_builtin_admin(user) -> bool:
    """历史内置账号：id=1 且 account=admin，作为超管兜底。"""
    if user is None:
        return False
    try:
        uid = int(getattr(user, "id", 0) or 0)
    except (TypeError, ValueError):
        return False
    return uid == 1 and str(getattr(user, "account", "") or "") == "admin"


def is_super_admin(user: SysUser | None) -> bool:
    """仅超管。内置 admin 始终为超管，避免迁移漏写 sys_role。"""
    if user is None:
        return False
    if is_legacy_builtin_admin(user):
        return True
    return _sys_role(user) == SYS_ROLE_SUPER_ADMIN


def is_platform_admin(user: SysUser | None) -> bool:
    """超管或平台管理员。现有 admin 仍走超管兜底，行为与改前一致。"""
    if user is None:
        return False
    if is_super_admin(user):
        return True
    return _sys_role(user) == SYS_ROLE_PLATFORM_ADMIN


def effective_sys_role(user: SysUser | None) -> str | None:
    """对外展示的系统角色：超管优先于平台管理员。"""
    if user is None:
        return None
    if is_super_admin(user):
        return SYS_ROLE_SUPER_ADMIN
    if is_platform_admin(user):
        return SYS_ROLE_PLATFORM_ADMIN
    return None


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
