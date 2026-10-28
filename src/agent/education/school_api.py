"""学校档案 API。学情库连接来自配置 ``DATABASE_EDU_URL``。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy import text

from audit.service.decorators import audit_access
from common.exceptions.base import BadRequestException, ForbiddenException, NotFoundException
from common.schemas.response import success_response
from datasource.db.db import WriteDbSession
from src.agent.education.knowledge_admin import parse_edu_database_url
from src.agent.education.school_admin import (
    create_school,
    delete_school,
    list_schools,
    school_impact,
    school_meta,
    update_school,
)
from system.api.auth_deps import get_current_user
from system.authz import is_platform_admin
from system.schemas import UserResponse

router = APIRouter(prefix="/education/schools", tags=["education-schools"])

_NOT_FOUND = "学校不存在"


class SchoolPayload(BaseModel):
    s_name: str = ""
    district: str = ""
    stage: str = "高中"
    type: str = ""


class _EduWriter:
    def __init__(self, session: WriteDbSession):
        self.session = session

    def query(self, sql: str, params: tuple | list = ()) -> list[dict[str, Any]]:
        ok, msg, data = self.session.execute_query(sql, params)
        if not ok:
            raise BadRequestException(msg or "查询失败")
        return _as_dicts(data)

    def write(self, sql: str, params: tuple | list = ()) -> int:
        ok, msg, data = self.session.execute_write(sql, params)
        if not ok:
            raise BadRequestException(msg or "写入失败")
        return int((data or {}).get("row_count") or 0)


def _edu_connection() -> tuple[str, dict]:
    from common.core.config import get_settings

    url = (get_settings().database_edu_url or "").strip()
    if not url:
        raise BadRequestException("未配置 DATABASE_EDU_URL")
    try:
        return parse_edu_database_url(url)
    except ValueError as exc:
        raise BadRequestException(str(exc)) from exc


def _require_admin(current_user: UserResponse) -> None:
    from common.core.database import get_db_session
    from system.crud.crud_user import get_user_by_id

    with get_db_session() as session:
        user = get_user_by_id(session, int(current_user.id))
        if not is_platform_admin(user):
            raise ForbiddenException("仅系统管理员可维护学校")


def _bound_account_count(school_id: str) -> int:
    from common.core.database import get_db_session

    with get_db_session() as session:
        rows = session.execute(
            text(
                "SELECT COUNT(*) FROM sys_user "
                "WHERE system_variables->>'school_id' = :sid"
            ),
            {"sid": school_id},
        ).fetchall()
    if not rows:
        return 0
    return int(rows[0][0] or 0)


def _run(db_type: str, config: dict, fn):
    try:
        with WriteDbSession(db_type, config) as session:
            result = fn(_EduWriter(session))
            session.commit()
            return result
    except ValueError as exc:
        message = str(exc)
        if message == _NOT_FOUND:
            raise NotFoundException(message) from exc
        raise BadRequestException(message) from exc


def _as_dicts(data: dict | None) -> list[dict[str, Any]]:
    cols = list((data or {}).get("columns") or [])
    rows: list[dict[str, Any]] = []
    for row in (data or {}).get("rows") or []:
        if isinstance(row, dict):
            rows.append(row)
        else:
            rows.append({cols[i]: row[i] for i in range(min(len(cols), len(row)))})
    return rows


def _payload(body: SchoolPayload) -> dict[str, str]:
    return body.model_dump()


@router.get("")
@audit_access(query_arg="keyword")
async def get_school_list(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str = "",
    district: str = "",
    school_type: str = "",
    current_user: UserResponse = Depends(get_current_user),
) -> dict:
    _ = request
    _require_admin(current_user)
    db_type, config = _edu_connection()
    data = _run(
        db_type,
        config,
        lambda db: list_schools(
            db,
            page=page,
            page_size=page_size,
            keyword=keyword,
            district=district,
            school_type=school_type,
        ),
    )
    return success_response(data)


@router.get("/meta")
async def get_school_meta(
    current_user: UserResponse = Depends(get_current_user),
) -> dict:
    _require_admin(current_user)
    db_type, config = _edu_connection()
    data = _run(db_type, config, school_meta)
    return success_response(data)


@router.post("")
@audit_access(query_arg="payload.s_name")
async def create_school_item(
    payload: SchoolPayload,
    request: Request,
    current_user: UserResponse = Depends(get_current_user),
) -> dict:
    _ = request
    _require_admin(current_user)
    db_type, config = _edu_connection()
    created = _run(db_type, config, lambda db: create_school(db, _payload(payload)))
    return success_response(created)


@router.post("/{school_id}/impact")
async def preview_school_impact(
    school_id: str,
    payload: SchoolPayload,
    current_user: UserResponse = Depends(get_current_user),
) -> dict:
    _require_admin(current_user)
    accounts = _bound_account_count(school_id)
    db_type, config = _edu_connection()
    impact = _run(
        db_type,
        config,
        lambda db: school_impact(
            db, school_id, account_count=accounts, proposal=_payload(payload)
        ),
    )
    return success_response(impact)


@router.get("/{school_id}/impact")
async def get_school_impact(
    school_id: str,
    current_user: UserResponse = Depends(get_current_user),
) -> dict:
    _require_admin(current_user)
    accounts = _bound_account_count(school_id)
    db_type, config = _edu_connection()
    impact = _run(
        db_type,
        config,
        lambda db: school_impact(db, school_id, account_count=accounts),
    )
    return success_response(impact)


@router.put("/{school_id}")
@audit_access(query_arg="school_id")
async def update_school_item(
    school_id: str,
    payload: SchoolPayload,
    request: Request,
    current_user: UserResponse = Depends(get_current_user),
) -> dict:
    _ = request
    _require_admin(current_user)
    accounts = _bound_account_count(school_id)
    db_type, config = _edu_connection()
    updated = _run(
        db_type,
        config,
        lambda db: update_school(db, school_id, _payload(payload), account_count=accounts),
    )
    return success_response(updated)


@router.delete("/{school_id}")
@audit_access(query_arg="school_id")
async def delete_school_item(
    school_id: str,
    request: Request,
    current_user: UserResponse = Depends(get_current_user),
) -> dict:
    _ = request
    _require_admin(current_user)
    accounts = _bound_account_count(school_id)
    db_type, config = _edu_connection()
    _run(
        db_type,
        config,
        lambda db: delete_school(db, school_id, account_count=accounts),
    )
    return success_response(message="删除成功")
