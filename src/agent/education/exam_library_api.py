"""试卷库 API。数据写在当前工作空间登记的 edu 业务库。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from common.exceptions.base import BadRequestException, ForbiddenException, NotFoundException
from common.schemas.response import success_response
from system.api.auth_deps import get_current_user
from system.schemas import UserResponse
from system.workspace_scope import assert_datasource_accessible, get_workspace_oid

router = APIRouter(prefix="/education/exam-library", tags=["education-exam-library"])


class ExamBody(BaseModel):
    exam_name: str
    exam_type: str = "teacher"
    subject: str
    exam_score: float
    exam_time: str | None = None
    school_id: str | None = None
    owner_id: str | None = None
    exam_batch_id: int | None = None


class KnowledgeLinkBody(BaseModel):
    knowledge_id: int
    weight: float | None = None


class QuestionBody(BaseModel):
    id: int | None = None
    question_no: str
    question_type: str | None = None
    question_score: float = 0
    content: str | None = None
    knowledge_links: list[KnowledgeLinkBody] = Field(default_factory=list)


class QuestionSaveBody(BaseModel):
    questions: list[QuestionBody] = Field(default_factory=list)


class ApplyOperation(BaseModel):
    item_id: int
    add_ids: list[int] = Field(default_factory=list)
    remove_ids: list[int] = Field(default_factory=list)
    weights: dict[str, float] = Field(default_factory=dict)


class ApplyBody(BaseModel):
    operations: list[ApplyOperation] = Field(default_factory=list)


def _db(current_user: UserResponse, workspace_oid: int):
    from common.core.database import get_db_session
    from datasource.service.edu_permission import parse_edu_scope
    from src.agent.education.exam_library import build_actor
    from src.agent.education.raw_import import resolve_edu_datasource_id
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
    if scope.edu_role == "student":
        raise ForbiddenException("学生账号不能管理试卷库")
    db_type, config, _name = _load_datasource(ds_id, workspace_oid)
    return db_type, config, build_actor(user, scope)


def _call(action):
    try:
        return action()
    except ValueError as exc:
        raise BadRequestException(str(exc)) from exc


def _require_batch_manager(actor) -> None:
    if not actor.see_all:
        raise ForbiddenException("只有管理员和教育局可以维护考试批次")


@router.get("")
async def list_exam_library(
    keyword: str = "",
    exam_type: str = "",
    subject: str = "",
    exam_batch_id: int | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: UserResponse = Depends(get_current_user),
    workspace_oid: int = Depends(get_workspace_oid),
) -> dict:
    db_type, config, actor = _db(current_user, workspace_oid)
    from src.agent.education.exam_library import list_exams

    data = _call(
        lambda: list_exams(
            db_type,
            config,
            actor,
            keyword=keyword,
            exam_type=exam_type,
            subject=subject,
            exam_batch_id=exam_batch_id,
            page=page,
            page_size=page_size,
        )
    )
    return success_response(data)


@router.post("")
async def create_exam_library(
    body: ExamBody,
    current_user: UserResponse = Depends(get_current_user),
    workspace_oid: int = Depends(get_workspace_oid),
) -> dict:
    db_type, config, actor = _db(current_user, workspace_oid)
    from src.agent.education.exam_library import create_exam

    data = _call(lambda: create_exam(db_type, config, actor, body.model_dump()))
    return success_response(data, message="试卷已创建")


class BatchBody(BaseModel):
    batch_name: str
    exam_time: str | None = None


@router.get("/batches")
async def list_exam_batches(
    current_user: UserResponse = Depends(get_current_user),
    workspace_oid: int = Depends(get_workspace_oid),
) -> dict:
    db_type, config, _actor = _db(current_user, workspace_oid)
    from src.agent.education.exam_library import list_exam_batches as load_batches

    data = _call(lambda: load_batches(db_type, config))
    return success_response(data)


@router.post("/batches")
async def create_exam_batch(
    body: BatchBody,
    current_user: UserResponse = Depends(get_current_user),
    workspace_oid: int = Depends(get_workspace_oid),
) -> dict:
    db_type, config, actor = _db(current_user, workspace_oid)
    _require_batch_manager(actor)
    from src.agent.education.exam_library import create_exam_batch as add_batch

    data = _call(lambda: add_batch(db_type, config, body.model_dump()))
    return success_response(data, message="批次已创建")


@router.put("/batches/{batch_id}")
async def update_exam_batch(
    batch_id: int,
    body: BatchBody,
    current_user: UserResponse = Depends(get_current_user),
    workspace_oid: int = Depends(get_workspace_oid),
) -> dict:
    db_type, config, actor = _db(current_user, workspace_oid)
    _require_batch_manager(actor)
    from src.agent.education.exam_library import update_exam_batch as save_batch

    data = _call(lambda: save_batch(db_type, config, batch_id, body.model_dump()))
    if data is None:
        raise NotFoundException("考试批次不存在")
    return success_response(data, message="批次已更新")


@router.delete("/batches/{batch_id}")
async def delete_exam_batch(
    batch_id: int,
    current_user: UserResponse = Depends(get_current_user),
    workspace_oid: int = Depends(get_workspace_oid),
) -> dict:
    db_type, config, actor = _db(current_user, workspace_oid)
    _require_batch_manager(actor)
    from src.agent.education.exam_library import delete_exam_batch as remove_batch

    deleted = _call(lambda: remove_batch(db_type, config, batch_id))
    if not deleted:
        raise NotFoundException("考试批次不存在")
    return success_response({"id": batch_id}, message="批次已删除")


@router.get("/match/{task_id}")
async def get_match_task(
    task_id: int,
    current_user: UserResponse = Depends(get_current_user),
    workspace_oid: int = Depends(get_workspace_oid),
) -> dict:
    db_type, config, _actor = _db(current_user, workspace_oid)
    from src.agent.education.exam_match import get_task

    task = _call(lambda: get_task(db_type, config, task_id))
    if task is None:
        raise NotFoundException("核对任务不存在")
    return success_response(task)


@router.post("/match/{task_id}/cancel")
async def cancel_match_task(
    task_id: int,
    current_user: UserResponse = Depends(get_current_user),
    workspace_oid: int = Depends(get_workspace_oid),
) -> dict:
    _db(current_user, workspace_oid)
    from src.agent.education.exam_match import cancel_check

    cancel_check(task_id)
    return success_response({"id": task_id}, message="已请求取消")


@router.post("/match/{task_id}/apply")
async def apply_match_task(
    task_id: int,
    body: ApplyBody,
    current_user: UserResponse = Depends(get_current_user),
    workspace_oid: int = Depends(get_workspace_oid),
) -> dict:
    db_type, config, actor = _db(current_user, workspace_oid)
    from src.agent.education.exam_match import apply_suggestions

    data = _call(
        lambda: apply_suggestions(
            db_type,
            config,
            actor,
            task_id,
            [item.model_dump() for item in body.operations],
        )
    )
    return success_response(data, message="已采纳所选建议")


@router.get("/{exam_id}")
async def get_exam_library(
    exam_id: int,
    current_user: UserResponse = Depends(get_current_user),
    workspace_oid: int = Depends(get_workspace_oid),
) -> dict:
    db_type, config, actor = _db(current_user, workspace_oid)
    from src.agent.education.exam_library import get_exam

    data = _call(lambda: get_exam(db_type, config, actor, exam_id))
    if data is None:
        raise NotFoundException("试卷不存在或无权查看")
    return success_response(data)


@router.put("/{exam_id}")
async def update_exam_library(
    exam_id: int,
    body: ExamBody,
    current_user: UserResponse = Depends(get_current_user),
    workspace_oid: int = Depends(get_workspace_oid),
) -> dict:
    db_type, config, actor = _db(current_user, workspace_oid)
    from src.agent.education.exam_library import update_exam

    data = _call(lambda: update_exam(db_type, config, actor, exam_id, body.model_dump()))
    if data is None:
        raise NotFoundException("试卷不存在或无权查看")
    return success_response(data, message="试卷已更新")


@router.delete("/{exam_id}")
async def delete_exam_library(
    exam_id: int,
    current_user: UserResponse = Depends(get_current_user),
    workspace_oid: int = Depends(get_workspace_oid),
) -> dict:
    db_type, config, actor = _db(current_user, workspace_oid)
    from src.agent.education.exam_library import delete_exam

    deleted = _call(lambda: delete_exam(db_type, config, actor, exam_id))
    if not deleted:
        raise NotFoundException("试卷不存在或无权查看")
    return success_response({"id": exam_id}, message="试卷已删除")


@router.put("/{exam_id}/questions")
async def save_exam_questions(
    exam_id: int,
    body: QuestionSaveBody,
    current_user: UserResponse = Depends(get_current_user),
    workspace_oid: int = Depends(get_workspace_oid),
) -> dict:
    db_type, config, actor = _db(current_user, workspace_oid)
    from src.agent.education.exam_library import save_questions

    data = _call(
        lambda: save_questions(
            db_type,
            config,
            actor,
            exam_id,
            [item.model_dump() for item in body.questions],
        )
    )
    if data is None:
        raise NotFoundException("试卷不存在或无权查看")
    return success_response(data, message="题目已保存")


@router.post("/{exam_id}/match")
async def match_exam(
    exam_id: int,
    current_user: UserResponse = Depends(get_current_user),
    workspace_oid: int = Depends(get_workspace_oid),
) -> dict:
    db_type, config, actor = _db(current_user, workspace_oid)
    from src.agent.education.exam_match import start_check

    data = _call(lambda: start_check(db_type, config, actor, exam_id))
    return success_response(data, message="已开始核对")


@router.post("/{exam_id}/questions/{question_id}/match")
async def match_question(
    exam_id: int,
    question_id: int,
    current_user: UserResponse = Depends(get_current_user),
    workspace_oid: int = Depends(get_workspace_oid),
) -> dict:
    db_type, config, actor = _db(current_user, workspace_oid)
    from src.agent.education.exam_match import start_check

    data = _call(lambda: start_check(db_type, config, actor, exam_id, question_id))
    return success_response(data, message="已开始核对")


@router.get("/{exam_id}/match-report")
async def exam_match_report(
    exam_id: int,
    task_id: int | None = None,
    current_user: UserResponse = Depends(get_current_user),
    workspace_oid: int = Depends(get_workspace_oid),
) -> dict:
    db_type, config, actor = _db(current_user, workspace_oid)
    from src.agent.education.exam_match import get_report

    data = _call(lambda: get_report(db_type, config, actor, exam_id, task_id))
    return success_response(data)
