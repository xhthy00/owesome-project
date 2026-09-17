"""User management APIs."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.orm import Session

from audit.service.decorators import audit_operation
from common.core.database import get_session
from common.core.security import get_password_hash
from common.exceptions.base import BadRequestException, ForbiddenException, NotFoundException
from common.schemas.response import success_response
from datasource.service.edu_permission import (
    EduScope,
    clear_edu_scope_from_variables,
    edu_scope_summary,
    merge_edu_scope_into_variables,
    parse_edu_scope,
    resolve_scope_school,
    validate_edu_scope,
)
from system.api.auth_deps import get_current_user
from system.authz import can_manage_data_permissions
from system.models.user import SysUser
from system.models.workspace import SysUserWorkspace
from system.schemas import EduScopePayload
from system.workspace_scope import get_workspace_oid

router = APIRouter(prefix="/user", tags=["user"])

_DEFAULT_PASSWORD = "123456"
_USER_CSV_TEMPLATE = (
    "account,name,email,oid\n"
    "zhangsan,张三,,1\n"
    "lisi,李四,,1\n"
)


@dataclass
class UserCsvRow:
    account: str
    name: str
    email: str | None = None
    oid: int | None = None


def _csv_delimiter(sample: str) -> str:
    comma = sample.count(",")
    semi = sample.count(";")
    tab = sample.count("\t")
    if tab > comma and tab >= semi:
        return "\t"
    if semi > comma:
        return ";"
    return ","


def parse_user_csv_text(csv_text: str) -> list[UserCsvRow]:
    """Parse user-import CSV; tolerate Excel/Windows CRLF, BOM, tab/semicolon."""
    text = (
        csv_text.replace("\ufeff", "")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .strip()
    )
    if not text:
        return []
    if text.startswith("PK"):
        raise BadRequestException("这是 Excel 工作簿，请另存为 CSV UTF-8 后再导入")
    lines = [ln for ln in text.split("\n") if ln.strip() and not ln.lstrip().startswith("#")]
    if lines and lines[0].lower().startswith("sep="):
        lines = lines[1:]
    text = "\n".join(lines).strip()
    if not text:
        return []
    try:
        reader = csv.DictReader(io.StringIO(text, newline=""), delimiter=_csv_delimiter(text[:2048]))
        headers = {str(h or "").strip().lstrip("\ufeff") for h in (reader.fieldnames or [])}
        if "account" not in headers or "name" not in headers:
            raise BadRequestException(
                "CSV 表头必须包含 account,name。请用「下载 CSV 模板」，不要改列名"
            )
        rows: list[UserCsvRow] = []
        for r in reader:
            if not r:
                continue
            norm = {
                str(k or "").strip().lstrip("\ufeff"): ("" if v is None else str(v).strip())
                for k, v in r.items()
            }
            if not any(norm.values()):
                continue
            oid_raw = norm.get("oid") or ""
            oid: int | None = None
            if oid_raw:
                try:
                    oid = int(oid_raw)
                except ValueError:
                    oid = -1
            rows.append(
                UserCsvRow(
                    account=norm.get("account") or "",
                    name=norm.get("name") or "",
                    email=norm.get("email") or None,
                    oid=oid,
                )
            )
        return rows
    except csv.Error as exc:
        raise BadRequestException(
            "CSV 解析失败，请使用 UTF-8 逗号分隔文件（Excel 请另存为 CSV UTF-8）"
        ) from exc


def _ensure_user_workspace(session: Session, uid: int, oid: int) -> None:
    exists = (
        session.query(SysUserWorkspace)
        .filter(SysUserWorkspace.uid == uid, SysUserWorkspace.oid == oid)
        .first()
    )
    if exists is None:
        session.add(
            SysUserWorkspace(uid=uid, oid=oid, weight=0, create_time=datetime.now())
        )


@router.get("/pager/{page_num}/{page_size}")
def pager_users(
    page_num: int,
    page_size: int,
    session: Session = Depends(get_session),
    current_user=Depends(get_current_user),
):
    _ = current_user
    q = session.query(SysUser).order_by(SysUser.id.asc())
    total = q.count()
    rows = q.offset((page_num - 1) * page_size).limit(page_size).all()
    items = []
    for item in rows:
        scope = parse_edu_scope(item)
        summary = edu_scope_summary(item)
        items.append(
            {
                "id": item.id,
                "account": item.account,
                "name": item.name,
                "email": item.email,
                "oid": item.oid,
                "status": item.status,
                "language": item.language,
                "origin": item.origin,
                "create_time": item.create_time,
                "edu_role": scope.edu_role or None,
                "edu_role_label": summary.get("edu_role_label"),
                "school_id": scope.school_id or None,
                "school_name": scope.school_name or None,
                "class_names": scope.class_names or None,
                "student_id": scope.student_id or None,
                "edu_remark": scope.remark or None,
            }
        )
    return success_response(data={"total": total, "items": items})


@router.get("/csv-template")
def download_user_csv_template(current_user=Depends(get_current_user)):
    _ = current_user
    return Response(
        content=_USER_CSV_TEMPLATE,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=user_import_template.csv"},
    )


@router.post("/batch")
@audit_operation(operation_type="create", resource_type="user", detail_arg="payload")
def batch_create_users(
    request: Request,
    payload: dict[str, Any],
    session: Session = Depends(get_session),
    current_user=Depends(get_current_user),
):
    _ = request
    _ = current_user
    csv_text = payload.get("csv")
    if not csv_text or not isinstance(csv_text, str):
        raise BadRequestException("需提供 csv 文本")
    rows = parse_user_csv_text(csv_text)
    if not rows:
        raise BadRequestException("CSV 没有有效数据行")

    success_count = 0
    failed: list[dict[str, Any]] = []
    seen: set[str] = set()
    now = int(datetime.now().timestamp() * 1000)
    for idx, row in enumerate(rows, start=1):
        account = (row.account or "").strip()
        name = (row.name or "").strip()
        if not account or not name:
            failed.append({"row": idx, "account": account, "reason": "account、name 为必填"})
            continue
        if account in seen:
            failed.append({"row": idx, "account": account, "reason": "账号在本文件中重复"})
            continue
        seen.add(account)
        if row.oid == -1:
            failed.append({"row": idx, "account": account, "reason": "oid 必须是数字"})
            continue
        oid = int(row.oid) if row.oid not in (None, 0) else 1
        if oid <= 0:
            failed.append({"row": idx, "account": account, "reason": "oid 无效"})
            continue
        if session.query(SysUser).filter(SysUser.account == account).first():
            failed.append({"row": idx, "account": account, "reason": "账号已存在"})
            continue
        user = SysUser(
            account=account,
            name=name,
            password=get_password_hash(_DEFAULT_PASSWORD),
            email=row.email,
            oid=oid,
            status=1,
            create_time=now,
            language="zh-CN",
            origin=0,
        )
        session.add(user)
        session.flush()
        _ensure_user_workspace(session, user.id, oid)
        success_count += 1

    session.commit()
    return success_response(
        data={"success": success_count, "failed": failed},
        message=f"批量创建完成：成功 {success_count} 条，失败 {len(failed)} 条（初始密码 {_DEFAULT_PASSWORD}）",
    )


@router.post("")
@audit_operation(operation_type="create", resource_type="user", detail_arg="payload")
def create_user(
    request: Request,
    payload: dict,
    session: Session = Depends(get_session),
    current_user=Depends(get_current_user),
):
    _ = current_user
    account = (payload.get("account") or "").strip()
    name = (payload.get("name") or "").strip()
    password = payload.get("password") or _DEFAULT_PASSWORD
    if not account or not name:
        raise BadRequestException("account and name are required")
    if session.query(SysUser).filter(SysUser.account == account).first():
        raise BadRequestException("account already exists")
    now = int(datetime.now().timestamp() * 1000)
    oid = int(payload.get("oid", 1))
    row = SysUser(
        account=account,
        name=name,
        password=get_password_hash(password),
        email=payload.get("email"),
        oid=oid,
        status=int(payload.get("status", 1)),
        create_time=now,
        language=payload.get("language", "zh-CN"),
        origin=int(payload.get("origin", 0)),
    )
    session.add(row)
    session.flush()
    _ensure_user_workspace(session, row.id, oid)
    session.commit()
    session.refresh(row)
    return success_response(data={"id": row.id}, message="User created")


@router.put("")
@audit_operation(operation_type="update", resource_type="user", detail_arg="payload")
def update_user(
    request: Request,
    payload: dict,
    session: Session = Depends(get_session),
    current_user=Depends(get_current_user),
):
    _ = current_user
    user_id = payload.get("id")
    row = session.query(SysUser).filter(SysUser.id == user_id).first()
    if not row:
        raise NotFoundException("User not found")
    for field in ("name", "email", "language"):
        if field in payload and payload[field] is not None:
            setattr(row, field, payload[field])
    if "oid" in payload and payload["oid"] is not None:
        row.oid = int(payload["oid"])
    if "status" in payload and payload["status"] is not None:
        row.status = int(payload["status"])
    session.commit()
    return success_response(data={"id": row.id}, message="User updated")


@router.delete("/{user_id}")
@audit_operation(operation_type="delete", resource_type="user", resource_id_arg="user_id")
def delete_user(
    request: Request,
    user_id: int,
    session: Session = Depends(get_session),
    current_user=Depends(get_current_user),
):
    row = session.query(SysUser).filter(SysUser.id == user_id).first()
    if not row:
        raise NotFoundException("User not found")
    if int(user_id) == int(current_user.id):
        raise BadRequestException("不能删除当前登录账号")
    if int(row.id) == 1 and str(row.account or "") == "admin":
        raise BadRequestException("不能删除系统管理员")
    session.query(SysUserWorkspace).filter(SysUserWorkspace.uid == user_id).delete(
        synchronize_session=False
    )
    session.delete(row)
    session.commit()
    return success_response(data={"id": user_id}, message="User deleted")


@router.patch("/status")
@audit_operation(operation_type="patch", resource_type="user", detail_arg="payload")
def update_user_status(
    request: Request,
    payload: dict,
    session: Session = Depends(get_session),
    current_user=Depends(get_current_user),
):
    _ = current_user
    user_id = payload.get("id")
    row = session.query(SysUser).filter(SysUser.id == user_id).first()
    if not row:
        raise NotFoundException("User not found")
    row.status = int(payload.get("status", row.status))
    session.commit()
    return success_response(data={"id": row.id, "status": row.status}, message="Status updated")


@router.patch("/pwd/{user_id}")
@audit_operation(operation_type="patch", resource_type="user", resource_id_arg="user_id")
def reset_user_pwd(
    request: Request,
    user_id: int,
    payload: dict,
    session: Session = Depends(get_session),
    current_user=Depends(get_current_user),
):
    _ = current_user
    row = session.query(SysUser).filter(SysUser.id == user_id).first()
    if not row:
        raise NotFoundException("User not found")
    password = payload.get("password") or "123456"
    row.password = get_password_hash(password)
    session.commit()
    return success_response(data={"id": row.id}, message="Password reset")


@router.get("/{user_id}/edu-scope")
def get_user_edu_scope(
    user_id: int,
    session: Session = Depends(get_session),
    current_user=Depends(get_current_user),
):
    _ = current_user
    row = session.query(SysUser).filter(SysUser.id == user_id).first()
    if not row:
        raise NotFoundException("User not found")
    scope = parse_edu_scope(row)
    return success_response(data={"user_id": row.id, "account": row.account, **scope.to_dict()})


@router.put("/{user_id}/edu-scope")
def update_user_edu_scope(
    user_id: int,
    payload: EduScopePayload,
    session: Session = Depends(get_session),
    current_user=Depends(get_current_user),
    workspace_oid: int = Depends(get_workspace_oid),
    datasource_id: int | None = Query(None),
):
    if not can_manage_data_permissions(session, current_user):
        raise ForbiddenException("仅系统管理员或工作空间管理员可管理教育权限")
    row = session.query(SysUser).filter(SysUser.id == user_id).first()
    if not row:
        raise NotFoundException("User not found")
    scope = EduScope(
        edu_role=str(payload.edu_role or "").strip(),
        school_id=str(payload.school_id or "").strip(),
        school_name=str(payload.school_name or "").strip(),
        class_names=list(payload.class_names or []),
        student_id=str(payload.student_id or "").strip(),
        remark=str(payload.edu_remark or "").strip()[:80],
    )
    resolve_err = resolve_scope_school(
        session, scope, datasource_id=datasource_id, workspace_oid=workspace_oid
    )
    if resolve_err:
        raise BadRequestException(resolve_err)
    errors = validate_edu_scope(scope)
    if errors:
        raise BadRequestException("; ".join(errors))
    row.system_variables = merge_edu_scope_into_variables(row.system_variables, scope)
    session.commit()
    session.refresh(row)
    return success_response(
        data={"user_id": row.id, **parse_edu_scope(row).to_dict()},
        message="教育权限范围已更新",
    )


@router.delete("/{user_id}/edu-scope")
def delete_user_edu_scope(
    user_id: int,
    session: Session = Depends(get_session),
    current_user=Depends(get_current_user),
):
    """移除用户的教育四级数据权限（保留 system_variables 中的其他字段）。"""
    if not can_manage_data_permissions(session, current_user):
        raise ForbiddenException("仅系统管理员或工作空间管理员可管理教育权限")
    row = session.query(SysUser).filter(SysUser.id == user_id).first()
    if not row:
        raise NotFoundException("User not found")
    had_scope = bool(parse_edu_scope(row).edu_role)
    row.system_variables = clear_edu_scope_from_variables(row.system_variables)
    session.commit()
    session.refresh(row)
    return success_response(
        data={"user_id": row.id, **parse_edu_scope(row).to_dict()},
        message="教育权限已删除" if had_scope else "该用户本无教育权限配置",
    )
