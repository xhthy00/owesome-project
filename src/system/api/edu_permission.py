"""教育四级数据权限配置 API。"""

from __future__ import annotations

import csv
import io
from typing import Any

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from common.core.database import get_session
from common.exceptions.base import BadRequestException, ForbiddenException, NotFoundException
from common.schemas.response import success_response
from datasource.service.edu_permission import (
    EduScope,
    build_edu_row_predicates,
    edu_scope_summary,
    list_edu_roles,
    merge_edu_scope_into_variables,
    parse_edu_scope,
    validate_edu_scope,
)
from datasource.service.query_permission import apply_permissions_for_execute
from system.api.auth_deps import get_current_user
from system.authz import can_manage_data_permissions
from system.crud.crud_user import get_user_by_id
from system.models.user import SysUser
from system.schemas import EduBatchBindRow, EduEffectiveRequest, EduScopePayload

router = APIRouter(prefix="/permission/edu", tags=["permission-edu"])

_CSV_TEMPLATE = (
    "account,edu_role,school_id,school_name,class_names,student_id\n"
    "zhang_principal,school_admin,1,南京市第一中学,,\n"
    "li_teacher,teacher,1,南京市第一中学,高一(1)班|高一(2)班,\n"
    "wang_student,student,1,南京市第一中学,,STU20240002\n"
    "bureau_user,bureau_admin,,,,\n"
)


def _require_manager(session: Session, current_user) -> None:
    if not can_manage_data_permissions(session, current_user):
        raise ForbiddenException("仅系统管理员或工作空间管理员可管理教育权限")


def _scope_from_payload(payload: EduScopePayload) -> EduScope:
    class_names = list(payload.class_names or [])
    return EduScope(
        edu_role=str(payload.edu_role or "").strip(),
        school_id=str(payload.school_id or "").strip(),
        school_name=str(payload.school_name or "").strip(),
        class_names=class_names,
        student_id=str(payload.student_id or "").strip(),
    )


def _scope_from_batch_row(row: EduBatchBindRow) -> EduScope:
    class_names: list[str] = []
    if row.class_names and str(row.class_names).strip():
        class_names = [
            p.strip()
            for p in str(row.class_names).replace("|", ",").split(",")
            if p.strip()
        ]
    return EduScope(
        edu_role=str(row.edu_role or "").strip(),
        school_id=str(row.school_id or "").strip(),
        school_name=str(row.school_name or "").strip(),
        class_names=class_names,
        student_id=str(row.student_id or "").strip(),
    )


@router.get("/roles")
def get_edu_roles(current_user=Depends(get_current_user)):
    _ = current_user
    return success_response(data=list_edu_roles())


@router.get("/template")
def download_csv_template(current_user=Depends(get_current_user)):
    _ = current_user
    return Response(
        content=_CSV_TEMPLATE,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=edu_permission_template.csv"},
    )


@router.post("/batch-bind")
def batch_bind_edu_scope(
    payload: dict[str, Any],
    session: Session = Depends(get_session),
    current_user=Depends(get_current_user),
):
    _require_manager(session, current_user)
    rows_raw = payload.get("rows")
    csv_text = payload.get("csv")
    rows: list[EduBatchBindRow] = []

    if csv_text and isinstance(csv_text, str):
        reader = csv.DictReader(io.StringIO(csv_text.strip()))
        for r in reader:
            rows.append(
                EduBatchBindRow(
                    account=(r.get("account") or "").strip(),
                    edu_role=(r.get("edu_role") or "").strip(),
                    school_id=(r.get("school_id") or "").strip() or None,
                    school_name=(r.get("school_name") or "").strip() or None,
                    class_names=(r.get("class_names") or "").strip() or None,
                    student_id=(r.get("student_id") or "").strip() or None,
                )
            )
    elif isinstance(rows_raw, list):
        for item in rows_raw:
            if isinstance(item, dict):
                rows.append(EduBatchBindRow(**item))
    else:
        raise BadRequestException("需提供 rows 数组或 csv 文本")

    success_count = 0
    failed: list[dict[str, Any]] = []
    for idx, row in enumerate(rows, start=1):
        account = (row.account or "").strip()
        if not account:
            failed.append({"row": idx, "account": account, "reason": "account 为空"})
            continue
        user = session.query(SysUser).filter(SysUser.account == account).first()
        if user is None:
            failed.append({"row": idx, "account": account, "reason": "用户不存在"})
            continue
        scope = _scope_from_batch_row(row)
        errors = validate_edu_scope(scope)
        if errors:
            failed.append({"row": idx, "account": account, "reason": "; ".join(errors)})
            continue
        user.system_variables = merge_edu_scope_into_variables(user.system_variables, scope)
        session.add(user)
        success_count += 1

    session.commit()
    return success_response(
        data={"success": success_count, "failed": failed},
        message=f"批量绑定完成：成功 {success_count} 条，失败 {len(failed)} 条",
    )


@router.post("/effective")
def preview_effective_permission(
    req: EduEffectiveRequest,
    session: Session = Depends(get_session),
    current_user=Depends(get_current_user),
):
    _require_manager(session, current_user)
    user = get_user_by_id(session, req.user_id)
    if user is None:
        raise NotFoundException("User not found")

    summary = edu_scope_summary(user)
    preds = build_edu_row_predicates(user, "pg")
    out: dict[str, Any] = {
        "edu_scope": summary,
        "edu_predicates": preds,
    }
    if req.sql and req.sql.strip():
        ds_id = int(req.datasource_id or 1)
        merged = apply_permissions_for_execute(session, user, ds_id, "pg", req.sql.strip())
        out["original_sql"] = req.sql.strip()
        out["merged_sql"] = merged
    return success_response(data=out)
