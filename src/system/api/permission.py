"""Permission read APIs for frontend-react permission pages."""

from datetime import datetime
from typing import Dict, List

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from common.core.database import get_session
from common.schemas.response import success_response
from datasource.models.datasource import CoreDatasource, CoreTable
from datasource.models.permission import DsPermission
from system.api.system import get_current_user
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
    table_map = {table.id: table.table_name for table in session.query(CoreTable).all()}
    rules: List[dict] = []
    for item in session.query(DsPermission).order_by(DsPermission.id.desc()).all():
        scope = "row" if item.type == "row" else "column"
        rules.append(
            {
                "id": int(item.id),
                "scope": scope,
                "datasource_id": int(item.ds_id) if item.ds_id else 0,
                "table_name": table_map.get(item.table_id, f"table_{item.table_id}" if item.table_id else "-"),
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
    _ = current_user
    item = DsPermission(
        enable=bool(payload.get("enabled", True)),
        auth_target_type=payload.get("auth_target_type", "workspace"),
        auth_target_id=payload.get("auth_target_id"),
        type=payload.get("scope", "row"),
        ds_id=payload.get("datasource_id"),
        table_id=payload.get("table_id"),
        expression_tree=payload.get("rule"),
        permissions=payload.get("permissions"),
        white_list_user=payload.get("white_list_user"),
        create_time=datetime.now(),
    )
    session.add(item)
    session.commit()
    session.refresh(item)
    return success_response(data={"id": item.id}, message="Rule created")
