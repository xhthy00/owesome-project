"""SQLBot-compatible datasource permission APIs."""

import json
from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from common.core.database import get_session
from common.exceptions.base import ForbiddenException, NotFoundException
from common.schemas.response import success_response
from datasource.crud import crud_datasource
from datasource.models.datasource import CoreTable
from datasource.models.permission import DsPermission, DsRule
from system.api.auth_deps import get_current_user
from system.authz import can_manage_data_permissions
from system.workspace_scope import get_workspace_oid

router = APIRouter(prefix="/ds_permission", tags=["ds_permission"])


def _json_config_field(value, default: str) -> str:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, str):
        return value
    return str(value)


def _require_data_permission_manager(session: Session, current_user) -> None:
    if not can_manage_data_permissions(session, current_user):
        raise ForbiddenException("仅系统管理员或工作空间管理员可管理数据权限规则")


@router.post("/list")
def list_permissions(
    session: Session = Depends(get_session),
    current_user=Depends(get_current_user),
    workspace_oid: int = Depends(get_workspace_oid),
):
    _require_data_permission_manager(session, current_user)
    table_map = {int(t.id): t.table_name for t in session.query(CoreTable.id, CoreTable.table_name).all()}
    rules = (
        session.query(DsRule)
        .filter(DsRule.oid == int(workspace_oid))
        .order_by(DsRule.id.desc())
        .all()
    )
    data = []
    for rule in rules:
        permission_ids = json.loads(rule.permission_list or "[]")
        users = json.loads(rule.user_list or "[]")
        permissions = (
            session.query(DsPermission).filter(DsPermission.id.in_(permission_ids or [-1])).all()
            if permission_ids
            else []
        )
        data.append(
            {
                "id": rule.id,
                "name": rule.name,
                "users": users,
                "permissions": [
                    {
                        "id": p.id,
                        "name": f"rule_{p.id}",
                        "type": p.type,
                        "ds_id": p.ds_id,
                        "table_name": p.table_name or table_map.get(int(p.table_id or 0), ""),
                        "expression_tree": json.loads(p.expression_tree or "{}"),
                        "permissions": json.loads(p.permissions or "[]"),
                    }
                    for p in permissions
                ],
            }
        )
    return success_response(data=data)


@router.post("/save")
def save_permissions(
    payload: dict,
    session: Session = Depends(get_session),
    current_user=Depends(get_current_user),
    workspace_oid: int = Depends(get_workspace_oid),
):
    _require_data_permission_manager(session, current_user)
    now = datetime.now()
    rule_id = payload.get("id")
    if rule_id:
        rule = session.query(DsRule).filter(DsRule.id == rule_id).first()
        if rule is None:
            raise NotFoundException("规则不存在")
        if int(getattr(rule, "oid", 1) or 1) != int(workspace_oid):
            raise NotFoundException("规则不存在")
    else:
        rule = DsRule(enable=True, create_time=now, name="", oid=int(workspace_oid))
        session.add(rule)
        session.flush()

    permission_ids: list[int] = []
    for item in payload.get("permissions", []):
        permission_id = item.get("id")
        model = (
            session.query(DsPermission).filter(DsPermission.id == permission_id).first()
            if permission_id
            else None
        )
        if model is None:
            model = DsPermission(
                enable=True,
                auth_target_type="workspace",
                create_time=now,
                type=item.get("type", "row"),
            )
            session.add(model)
            session.flush()
        ds_id_raw = item.get("ds_id")
        model.ds_id = ds_id_raw
        if ds_id_raw is not None:
            ds_row = crud_datasource.get_datasource_by_id(session, int(ds_id_raw))
            if ds_row is None or int(ds_row.oid) != int(workspace_oid):
                raise ForbiddenException("权限条目必须引用当前工作空间内的数据源")

        table_name = (item.get("table_name") or "").strip()
        if not table_name and item.get("table_id"):
            table = session.query(CoreTable.table_name).filter(CoreTable.id == int(item.get("table_id"))).first()
            table_name = table.table_name if table else ""
        model.table_name = table_name or None
        model.table_id = None
        model.type = item.get("type", model.type)
        model.expression_tree = _json_config_field(item.get("expression_tree"), "{}")
        model.permissions = _json_config_field(item.get("permissions"), "[]")
        permission_ids.append(model.id)

    rule.name = payload.get("name", rule.name)
    rule.enable = True
    rule.oid = int(workspace_oid)
    rule.permission_list = json.dumps(permission_ids)
    rule.user_list = json.dumps(payload.get("users", []))
    rule.create_time = now

    session.commit()
    return success_response(data={"id": rule.id}, message="saved")


@router.post("/delete/{rule_id}")
def delete_permission_rule(
    rule_id: int,
    session: Session = Depends(get_session),
    current_user=Depends(get_current_user),
    workspace_oid: int = Depends(get_workspace_oid),
):
    _require_data_permission_manager(session, current_user)
    rule = session.query(DsRule).filter(DsRule.id == rule_id).first()
    if rule and int(getattr(rule, "oid", 1) or 1) != int(workspace_oid):
        raise NotFoundException("规则不存在")
    if rule:
        permission_ids = json.loads(rule.permission_list or "[]")
        if permission_ids:
            session.query(DsPermission).filter(DsPermission.id.in_(permission_ids)).delete(
                synchronize_session=False
            )
        session.delete(rule)
        session.commit()
    return success_response(data={"id": rule_id}, message="deleted")
