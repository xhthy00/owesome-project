"""Permission read APIs for frontend-react permission pages."""

import json
from datetime import datetime
from typing import Dict, List

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from common.core.database import get_session
from common.exceptions.base import ForbiddenException
from common.schemas.response import success_response
from datasource.models.datasource import CoreDatasource
from datasource.models.permission import DsPermission
from system.api.auth_deps import get_current_user
from system.authz import can_manage_data_permissions, is_platform_admin
from system.crud.crud_edu_privacy import get_anonymize_display, set_anonymize_display
from system.crud.crud_menu_visible import get_visibility_map, set_visibility
from system.models.user import SysUser
from system.models.workspace import SysUserWorkspace

router = APIRouter(prefix="/permission", tags=["permission"])


def _role_code(user: SysUser, weight: int) -> str:
    if user.id == 1 and user.account == "admin":
        return "admin"
    if weight == 1:
        return "ws_admin"
    return "member"


@router.get("/roles")
def list_roles(current_user=Depends(get_current_user)):
    _ = current_user
    return success_response(
        data=[
            {"id": 1, "code": "admin", "name": "系统管理员", "description": "全局管理权限"},
            {"id": 2, "code": "ws_admin", "name": "工作空间管理员", "description": "工作空间内资源管理权限"},
            {"id": 3, "code": "member", "name": "普通成员", "description": "基础使用与查询权限"},
        ]
    )


@router.get("/grants/user-role")
def list_user_role_grants(
    session: Session = Depends(get_session),
    current_user=Depends(get_current_user),
):
    _ = current_user
    members = session.query(SysUserWorkspace).all()
    users = session.query(SysUser).all()
    member_map: Dict[tuple[int, int], int] = {(m.uid, m.oid): m.weight for m in members}

    grants: List[dict] = []
    seq = 1
    for user in users:
        oid = int(user.oid)
        weight = member_map.get((user.id, oid), 0)
        grants.append(
            {
                "id": seq,
                "user_id": user.id,
                "account": user.account,
                "role_codes": [_role_code(user, weight)],
                "oid": oid,
            }
        )
        seq += 1
    return success_response(data=grants)


@router.get("/grants/resource")
def list_resource_grants(
    session: Session = Depends(get_session),
    current_user=Depends(get_current_user),
):
    _ = current_user
    members = session.query(SysUserWorkspace).all()
    datasource_by_oid: Dict[int, List[int]] = {}
    for item in session.query(CoreDatasource.id, CoreDatasource.oid).all():
        datasource_by_oid.setdefault(int(item.oid), []).append(int(item.id))

    rows: List[dict] = []
    seq = 1
    for member in members:
        role = "ws_admin" if member.weight == 1 else "member"
        rows.append(
            {
                "id": seq,
                "principal_type": "role",
                "principal": role,
                "resource_type": "datasource",
                "resource_ids": datasource_by_oid.get(int(member.oid), []),
            }
        )
        seq += 1
    return success_response(data=rows)


@router.get("/data-rules")
def list_data_rules(
    session: Session = Depends(get_session),
    current_user=Depends(get_current_user),
):
    _ = current_user
    rules: List[dict] = []
    for item in session.query(DsPermission).order_by(DsPermission.id.desc()).all():
        scope = "row" if item.type == "row" else "column"
        rules.append(
            {
                "id": int(item.id),
                "scope": scope,
                "datasource_id": int(item.ds_id) if item.ds_id else 0,
                "table_name": item.table_name or "-",
                "rule": item.expression_tree or item.permissions or "",
                "enabled": bool(item.enable),
            }
        )
    return success_response(data=rules)


@router.post("/data-rules")
def create_data_rule(
    payload: dict,
    session: Session = Depends(get_session),
    current_user=Depends(get_current_user),
):
    if not can_manage_data_permissions(session, current_user):
        raise ForbiddenException("仅系统管理员或工作空间管理员可管理数据权限规则")
    rule = payload.get("rule")
    if isinstance(rule, (dict, list)):
        expr = json.dumps(rule, ensure_ascii=False)
    else:
        expr = rule
    perms = payload.get("permissions")
    if isinstance(perms, (dict, list)):
        perms_s = json.dumps(perms, ensure_ascii=False)
    else:
        perms_s = perms
    item = DsPermission(
        enable=bool(payload.get("enabled", True)),
        auth_target_type=payload.get("auth_target_type", "workspace"),
        auth_target_id=payload.get("auth_target_id"),
        type=payload.get("scope", "row"),
        ds_id=payload.get("datasource_id"),
        table_name=(payload.get("table_name") or "").strip() or None,
        expression_tree=expr,
        permissions=perms_s,
        white_list_user=payload.get("white_list_user"),
        create_time=datetime.now(),
    )
    session.add(item)
    session.commit()
    session.refresh(item)
    return success_response(data={"id": item.id}, message="Rule created")


class MenuVisibilityPayload(BaseModel):
    menu_key: str
    visible: bool


@router.get("/menu-visibility")
def list_menu_visibility(
    session: Session = Depends(get_session),
    current_user=Depends(get_current_user),
):
    """Return current menu visibility map. Missing keys default to visible."""
    _ = current_user
    return success_response(data=get_visibility_map(session))


@router.post("/menu-visibility")
def save_menu_visibility(
    payload: MenuVisibilityPayload,
    session: Session = Depends(get_session),
    current_user=Depends(get_current_user),
):
    """Upsert visibility for a menu key. Only platform admins can modify."""
    if not is_platform_admin(current_user):
        raise ForbiddenException("仅系统管理员可修改菜单可见性")
    set_visibility(session, payload.menu_key, payload.visible)
    return success_response(message="保存成功")


class EduPrivacyPayload(BaseModel):
    anonymize_display: bool


@router.get("/edu-privacy")
def get_edu_privacy(
    session: Session = Depends(get_session),
    current_user=Depends(get_current_user),
):
    """Return whether education queries/reports anonymize student/school PII."""
    _ = current_user
    return success_response(data={"anonymize_display": get_anonymize_display(session)})


@router.put("/edu-privacy")
def save_edu_privacy(
    payload: EduPrivacyPayload,
    session: Session = Depends(get_session),
    current_user=Depends(get_current_user),
):
    """Toggle anonymized display. Only platform admins can modify."""
    if not is_platform_admin(current_user):
        raise ForbiddenException("仅系统管理员可修改匿名脱敏展示开关")
    set_anonymize_display(session, payload.anonymize_display)
    from src.agent.education.privacy_mode import set_anonymize_display_cached

    set_anonymize_display_cached(payload.anonymize_display)
    return success_response(
        data={"anonymize_display": payload.anonymize_display},
        message="保存成功",
    )
