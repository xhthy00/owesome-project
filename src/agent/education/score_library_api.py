"""成绩库 API。数据读当前工作空间登记的 edu 业务库。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from common.exceptions.base import BadRequestException, ForbiddenException
from common.schemas.response import success_response
from system.api.auth_deps import get_current_user
from system.authz import is_platform_admin
from system.schemas import UserResponse
from system.workspace_scope import assert_datasource_accessible, get_workspace_oid

router = APIRouter(prefix="/education/score-library", tags=["education-score-library"])


def _db(current_user: UserResponse, workspace_oid: int):
    from common.core.database import get_db_session
    from datasource.service.edu_permission import parse_edu_scope
    from src.agent.education.raw_import import resolve_edu_datasource_id
    from src.agent.education.score_library import resolve_score_access
    from src.agent.resource.tool.business import _load_datasource
    from system.crud.crud_user import get_user_by_id

    with get_db_session() as session:
        ds_id = resolve_edu_datasource_id(session, workspace_oid)
        if not ds_id:
            raise BadRequestException("当前工作空间未登记 edu 业务库")
        assert_datasource_accessible(session, current_user, ds_id, workspace_oid)
        user = get_user_by_id(session, int(current_user.id))
        if user is None:
            raise ForbiddenException("用户不存在")
        scope = parse_edu_scope(user)
        try:
            access = resolve_score_access(scope, privileged=is_platform_admin(user))
        except ValueError as exc:
            raise ForbiddenException(str(exc)) from exc
    db_type, config, _name = _load_datasource(ds_id, workspace_oid)
    return db_type, config, access, scope.school_id


@router.get("/options")
async def score_library_options(
    exam_batch_id: int | None = Query(None),
    current_user: UserResponse = Depends(get_current_user),
    workspace_oid: int = Depends(get_workspace_oid),
) -> dict:
    db_type, config, access, school_id = _db(current_user, workspace_oid)
    from src.agent.education.score_library import list_filter_options

    try:
        data = list_filter_options(
            db_type,
            config,
            access=access,
            school_id=school_id,
            exam_batch_id=exam_batch_id,
        )
    except ValueError as exc:
        raise BadRequestException(str(exc)) from exc
    return success_response(data)


@router.get("")
async def list_score_library(
    exam_batch_id: int | None = Query(None),
    school: str = "",
    grade: str = "",
    class_name: str = "",
    track: str = "",
    subject: str = "",
    student: str = "",
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: UserResponse = Depends(get_current_user),
    workspace_oid: int = Depends(get_workspace_oid),
) -> dict:
    db_type, config, access, school_id = _db(current_user, workspace_oid)
    from src.agent.education.score_library import list_scores

    try:
        data = list_scores(
            db_type,
            config,
            access=access,
            school_id=school_id,
            exam_batch_id=exam_batch_id,
            school=school,
            grade=grade,
            class_name=class_name,
            track=track,
            subject=subject,
            student=student,
            page=page,
            page_size=page_size,
        )
    except ValueError as exc:
        raise BadRequestException(str(exc)) from exc
    return success_response(data)


@router.get("/detail")
async def score_library_detail(
    exam_batch_id: int = Query(...),
    anon_stu_id: str = Query(...),
    subject: str = Query(...),
    current_user: UserResponse = Depends(get_current_user),
    workspace_oid: int = Depends(get_workspace_oid),
) -> dict:
    db_type, config, access, school_id = _db(current_user, workspace_oid)
    from src.agent.education.score_library import list_score_detail

    try:
        data = list_score_detail(
            db_type,
            config,
            access=access,
            school_id=school_id,
            exam_batch_id=exam_batch_id,
            anon_stu_id=anon_stu_id,
            subject=subject,
        )
    except ValueError as exc:
        raise BadRequestException(str(exc)) from exc
    return success_response(data)
