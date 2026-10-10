"""知识点管理 API。数据写在当前工作空间登记的 edu 业务库。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from audit.service.decorators import audit_access
from common.exceptions.base import BadRequestException, ForbiddenException, NotFoundException
from common.schemas.response import success_response
from datasource.db.db import WriteDbSession
from src.agent.education.knowledge_admin import (
    cascade_options,
    create_knowledge,
    delete_knowledge,
    import_knowledge_rows,
    knowledge_access_error,
    list_knowledge,
    list_versions,
    parse_knowledge_xlsx,
    update_knowledge,
)
from system.api.auth_deps import get_current_user
from system.authz import is_platform_admin
from system.schemas import UserResponse
from system.workspace_scope import assert_datasource_accessible, get_workspace_oid

router = APIRouter(prefix="/education/knowledge", tags=["education-knowledge"])

_TEMPLATE = (
    Path(__file__).resolve().parents[1]
    / "resource"
    / "templates"
    / "education"
    / "knowledge_import_template.xlsx"
)
_NOT_FOUND = "知识点不存在"


class KnowledgePayload(BaseModel):
    stage: str = ""
    grade: str = ""
    subject: str = ""
    subject_no: str = ""
    module: str = ""
    module_no: str = ""
    chapter: str = ""
    chapter_no: str = ""
    secondary: str = ""
    secondary_no: str = ""
    knowledge_name: str = ""
    knowledge_no: str = ""


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


def _prepare(current_user: UserResponse, workspace_oid: int) -> tuple[str, dict, str]:
    from common.core.database import get_db_session
    from datasource.service.edu_permission import parse_edu_scope
    from src.agent.education.raw_import import resolve_edu_datasource_id
    from src.agent.resource.tool.business import _load_datasource
    from system.crud.crud_user import get_user_by_id

    with get_db_session() as session:
        ds_id = resolve_edu_datasource_id(session, workspace_oid)
        if not ds_id:
            raise BadRequestException("当前工作空间未登记 edu 业务库")
        assert_datasource_accessible(session, current_user, ds_id, workspace_oid)
        user = get_user_by_id(session, int(current_user.id))
        scope = parse_edu_scope(user)
        err = knowledge_access_error(
            scope.edu_role, is_platform_admin=is_platform_admin(user)
        )
        if err:
            raise ForbiddenException(err)
        account = str(getattr(user, "account", "") or current_user.account or "").strip()
    db_type, config, _ = _load_datasource(ds_id, workspace_oid)
    return db_type, config, account or "system"


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


@router.get("")
@audit_access(query_arg="keyword")
async def get_knowledge_list(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    stage: str = "",
    grade: str = "",
    subject: str = "",
    module: str = "",
    chapter: str = "",
    secondary: str = "",
    keyword: str = "",
    current_user: UserResponse = Depends(get_current_user),
    workspace_oid: int = Depends(get_workspace_oid),
) -> dict:
    _ = request
    db_type, config, _operator = _prepare(current_user, workspace_oid)
    data = _run(
        db_type,
        config,
        lambda db: list_knowledge(
            db,
            page=page,
            page_size=page_size,
            stage=stage,
            grade=grade,
            subject=subject,
            module=module,
            chapter=chapter,
            secondary=secondary,
            keyword=keyword,
        ),
    )
    return success_response(data)


@router.get("/cascade")
async def get_knowledge_cascade(
    level: str = Query(...),
    stage: str = "",
    grade: str = "",
    subject: str = "",
    module: str = "",
    chapter: str = "",
    current_user: UserResponse = Depends(get_current_user),
    workspace_oid: int = Depends(get_workspace_oid),
) -> dict:
    db_type, config, _operator = _prepare(current_user, workspace_oid)
    options = _run(
        db_type,
        config,
        lambda db: cascade_options(
            db,
            level,
            {
                "stage": stage,
                "grade": grade,
                "subject": subject,
                "module": module,
                "chapter": chapter,
            },
        ),
    )
    return success_response({"level": level, "options": options})


@router.get("/template")
async def download_knowledge_template(
    current_user: UserResponse = Depends(get_current_user),
    workspace_oid: int = Depends(get_workspace_oid),
) -> FileResponse:
    _prepare(current_user, workspace_oid)
    if not _TEMPLATE.is_file():
        raise BadRequestException("模板文件不存在")
    return FileResponse(
        _TEMPLATE,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename="知识点导入模板.xlsx",
    )


@router.post("/import")
@audit_access(query_arg="current_user.id")
async def import_knowledge_file(
    request: Request,
    file: UploadFile = File(...),
    current_user: UserResponse = Depends(get_current_user),
    workspace_oid: int = Depends(get_workspace_oid),
) -> dict:
    _ = request
    name = (file.filename or "").lower()
    if not name.endswith(".xlsx"):
        raise BadRequestException("请上传 .xlsx 格式的 Excel 文件")
    data = file.file.read()
    if not data:
        raise BadRequestException("上传文件为空")
    try:
        rows, errors = parse_knowledge_xlsx(data)
    except ValueError as exc:
        raise BadRequestException(str(exc)) from exc
    db_type, config, operator = _prepare(current_user, workspace_oid)
    inserted, updated = _run(
        db_type, config, lambda db: import_knowledge_rows(db, rows, operator)
    )
    return success_response(
        {
            "inserted": inserted,
            "updated": updated,
            "errors": [item.to_dict() for item in errors],
        }
    )


@router.post("")
@audit_access(query_arg="payload.knowledge_no")
async def create_knowledge_item(
    request: Request,
    payload: KnowledgePayload,
    current_user: UserResponse = Depends(get_current_user),
    workspace_oid: int = Depends(get_workspace_oid),
) -> dict:
    _ = request
    db_type, config, operator = _prepare(current_user, workspace_oid)
    row = _run(db_type, config, lambda db: create_knowledge(db, payload.model_dump(), operator))
    return success_response(row, message="保存成功")


@router.put("/{knowledge_id}")
@audit_access(query_arg="knowledge_id")
async def update_knowledge_item(
    request: Request,
    knowledge_id: int,
    payload: KnowledgePayload,
    current_user: UserResponse = Depends(get_current_user),
    workspace_oid: int = Depends(get_workspace_oid),
) -> dict:
    _ = request
    db_type, config, operator = _prepare(current_user, workspace_oid)
    row = _run(
        db_type,
        config,
        lambda db: update_knowledge(db, knowledge_id, payload.model_dump(), operator),
    )
    return success_response(row, message="保存成功")


@router.delete("/{knowledge_id}")
@audit_access(query_arg="knowledge_id")
async def delete_knowledge_item(
    request: Request,
    knowledge_id: int,
    current_user: UserResponse = Depends(get_current_user),
    workspace_oid: int = Depends(get_workspace_oid),
) -> dict:
    _ = request
    db_type, config, operator = _prepare(current_user, workspace_oid)

    def _delete(db) -> None:
        delete_knowledge(db, knowledge_id, operator)

    _run(db_type, config, _delete)
    return success_response(message="删除成功")


@router.get("/{knowledge_id}/versions")
async def get_knowledge_versions(
    knowledge_id: int,
    current_user: UserResponse = Depends(get_current_user),
    workspace_oid: int = Depends(get_workspace_oid),
) -> dict:
    db_type, config, _operator = _prepare(current_user, workspace_oid)
    versions = _run(db_type, config, lambda db: list_versions(db, knowledge_id))
    return success_response({"versions": versions})


__all__ = ["router"]
