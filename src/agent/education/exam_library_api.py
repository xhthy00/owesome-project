"""试卷库 API。使用固定学情库和账号的数据范围。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from common.exceptions.base import BadRequestException, ForbiddenException, NotFoundException
from common.schemas.response import success_response
from system.api.auth_deps import get_current_user
from system.schemas import UserResponse
from system.workspace_scope import get_workspace_oid

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
    from datasource.service.edu_scope_view import EduScope
    from src.agent.education.exam_library import build_actor, build_scoped_actor
    from src.datasource.edu_connection import edu_db_connection
    from src.datasource.service.education_authorization import AUTH_KEY, AuthorizationError
    from src.datasource.service.education_report_scope import load_report_scope
    from src.datasource.service.education_source import source_settings
    from system.crud.crud_user import get_user_by_id

    report_scope = None
    with get_db_session() as session:
        user = get_user_by_id(session, int(current_user.id))
        if user is None:
            raise ForbiddenException("用户不存在")
        actor = build_actor(user, EduScope())
        variables = getattr(user, "system_variables", None)
        if not isinstance(variables, dict) or AUTH_KEY not in variables:
            raise ForbiddenException("当前账号没有有效的教育授权，请联系管理员配置")
        try:
            _, db_type, config, ds_id, _ = source_settings()
            report_scope = load_report_scope(session, user, workspace_oid, ds_id)
        except AuthorizationError as exc:
            raise ForbiddenException(exc.detail.get("user_message") or str(exc)) from exc
        if report_scope is None or not report_scope.levels & {"city", "school", "class"}:
            raise ForbiddenException("当前账号没有试卷库访问权限")
    if report_scope is not None:
        actor = _call(lambda: build_scoped_actor(db_type, config, actor, report_scope.grants))
    return db_type, config, actor


def _call(action):
    try:
        return action()
    except ValueError as exc:
        raise BadRequestException(str(exc)) from exc


def paper_upload_context(current_user: UserResponse, workspace_oid: int):
    from src.agent.education.exam_library import paper_upload_schools

    db_type, config, actor = _db(current_user, workspace_oid)
    if not actor.can_maintain and not paper_upload_schools(actor):
        raise ForbiddenException("当前账号没有可上传试卷的学校授权")
    return db_type, config, actor


@router.get("/upload-options")
async def get_paper_upload_options(
    current_user: UserResponse = Depends(get_current_user),
    workspace_oid: int = Depends(get_workspace_oid),
) -> dict:
    from src.agent.education.exam_library import paper_upload_schools

    _, _, actor = _db(current_user, workspace_oid)
    return success_response({
        "batch_import": actor.can_maintain,
        "schools": [
            {"id": key, "name": name, "exam_type": kind}
            for key, (name, kind) in paper_upload_schools(actor).items()
        ],
    })


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
    exam_type: str | None = None
    jc: str | int | None = None


@router.get("/batches")
async def list_exam_batches(
    current_user: UserResponse = Depends(get_current_user),
    workspace_oid: int = Depends(get_workspace_oid),
) -> dict:
    db_type, config, actor = _db(current_user, workspace_oid)
    from src.agent.education.exam_library import list_exam_batches as load_batches

    data = _call(lambda: load_batches(db_type, config, actor))
    return success_response(data)


@router.post("/batches")
async def create_exam_batch(
    body: BatchBody,
    current_user: UserResponse = Depends(get_current_user),
    workspace_oid: int = Depends(get_workspace_oid),
) -> dict:
    db_type, config, actor = _db(current_user, workspace_oid)
    from src.agent.education.exam_library import create_exam_batch as add_batch

    data = _call(lambda: add_batch(db_type, config, actor, body.model_dump()))
    return success_response(data, message="批次已创建")


@router.put("/batches/{batch_id}")
async def update_exam_batch(
    batch_id: int,
    body: BatchBody,
    current_user: UserResponse = Depends(get_current_user),
    workspace_oid: int = Depends(get_workspace_oid),
) -> dict:
    db_type, config, actor = _db(current_user, workspace_oid)
    from src.agent.education.exam_library import update_exam_batch as save_batch

    data = _call(lambda: save_batch(db_type, config, actor, batch_id, body.model_dump()))
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
    from src.agent.education.exam_library import delete_exam_batch as remove_batch

    deleted = _call(lambda: remove_batch(db_type, config, actor, batch_id))
    if not deleted:
        raise NotFoundException("考试批次不存在")
    return success_response({"id": batch_id}, message="批次已删除")


@router.get("/match/{task_id}")
async def get_match_task(
    task_id: int,
    current_user: UserResponse = Depends(get_current_user),
    workspace_oid: int = Depends(get_workspace_oid),
) -> dict:
    db_type, config, actor = _db(current_user, workspace_oid)
    from src.agent.education.exam_library import get_exam
    from src.agent.education.exam_match import get_task

    task = _call(lambda: get_task(db_type, config, task_id))
    if task is None:
        raise NotFoundException("核对任务不存在")
    if not actor.see_all and _call(lambda: get_exam(db_type, config, actor, task["exam_id"])) is None:
        raise NotFoundException("核对任务不存在")
    return success_response(task)


@router.post("/match/{task_id}/cancel")
async def cancel_match_task(
    task_id: int,
    current_user: UserResponse = Depends(get_current_user),
    workspace_oid: int = Depends(get_workspace_oid),
) -> dict:
    db_type, config, actor = _db(current_user, workspace_oid)
    from src.agent.education.exam_match import cancel_check, get_task

    if not actor.can_maintain:
        raise ForbiddenException("无权取消核对任务")
    if _call(lambda: get_task(db_type, config, task_id)) is None:
        raise NotFoundException("核对任务不存在")
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
