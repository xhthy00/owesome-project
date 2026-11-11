"""Resource-bound JSONB education authorization management and verification."""

import csv
import io
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import Field
from sqlalchemy import or_
from sqlalchemy.orm import Session
from sqlglot.errors import SqlglotError

from common.core.database import get_session
from common.exceptions.base import BadRequestException, ForbiddenException, NotFoundException
from common.schemas.response import success_response
from src.chat.service.semantic_query import SemanticQueryError
from src.datasource.service.education_authorization import (
    AuthorizationError,
    EducationAuthorization,
    EducationGrant,
    StrictModel,
    assert_can_manage,
    assert_delegated_grants,
    assert_workspace_member,
    context_for,
    fingerprint,
    normalize_education_grant,
    read_authorization,
    replace_resource_grants,
    validate_grant_catalog,
)
from src.datasource.service.education_source import environment_manifest, source_info
from system.api.auth_deps import get_current_user
from system.authz import is_platform_admin
from system.models.user import SysUser
from system.models.workspace import SysUserWorkspace
from system.workspace_scope import get_workspace_oid

router = APIRouter(prefix="/permission/edu/v2", tags=["permission-edu-v2"])


@router.get("/bindings")
def bindings(
    session: Session = Depends(get_session),
    current_user=Depends(get_current_user),
    workspace_oid: int = Depends(get_workspace_oid),
):
    try:
        assert_workspace_member(session, current_user, workspace_oid)
        return success_response(data=[source_info(workspace_oid)])
    except AuthorizationError as exc:
        handle_error(exc)


@router.get("/source")
def get_source(
    session: Session = Depends(get_session),
    current_user=Depends(get_current_user),
    workspace_oid: int = Depends(get_workspace_oid),
):
    try:
        assert_workspace_member(session, current_user, workspace_oid)
        return success_response(data=source_info(workspace_oid))
    except AuthorizationError as exc:
        handle_error(exc)


class GrantUpdate(StrictModel):
    expected_revision: int = Field(ge=0)
    grants: tuple[EducationGrant, ...] = Field(default=(), max_length=500)


class EffectiveRequest(StrictModel):
    user_id: int
    datasource_id: int | None = None
    sql: str | None = Field(default=None, max_length=50_000)


class BatchRequest(StrictModel):
    datasource_id: int | None = None
    csv: str = Field(max_length=1_000_000)


def handle_error(exc):
    if isinstance(exc, AuthorizationError):
        if exc.detail["code"] in {"AUTH_SCOPE_OUTSIDE_GRANT", "AUTH_REVOKED"}:
            raise ForbiddenException(str(exc)) from exc
        raise BadRequestException(str(exc)) from exc
    raise exc


def resource(session, actor, datasource_id, workspace_oid):
    assert_workspace_member(session, actor, workspace_oid)
    return environment_manifest(workspace_oid, datasource_id)


def target_user(session, user_id, *, lock=False):
    query = session.query(SysUser).filter(SysUser.id == user_id)
    if lock:
        query = query.populate_existing().with_for_update()
    user = query.first()
    if user is None:
        raise NotFoundException("用户不存在")
    return user


def response_document(user, workspace_oid, datasource_id, *, manifest=None):
    doc = read_authorization(user, manifest=manifest)
    return {
        "schema_version": 2,
        "revision": doc.revision,
        "grants": [
            g.model_dump(mode="json")
            for g in doc.grants
            if g.workspace_oid == workspace_oid and g.datasource_id == datasource_id
        ],
    }


@router.get("/users")
def list_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str = Query("", max_length=100),
    session: Session = Depends(get_session),
    current_user=Depends(get_current_user),
    workspace_oid: int = Depends(get_workspace_oid),
):
    """Page workspace accounts without returning credentials or unrelated authorizations."""
    try:
        policy = resource(session, current_user, None, workspace_oid)
        now = datetime.now(timezone.utc)
        platform_admin = is_platform_admin(current_user)
        delegations = []
        if not platform_admin:
            manager = (
                session.query(SysUserWorkspace)
                .filter(
                    SysUserWorkspace.uid == current_user.id,
                    SysUserWorkspace.oid == workspace_oid,
                    SysUserWorkspace.weight == 1,
                )
                .first()
            )
            delegations = [
                g
                for g in read_authorization(current_user, manifest=policy).grants
                if g.workspace_oid == workspace_oid
                and g.datasource_id == policy.datasource_id
                and g.can_delegate
                and g.active(now)
            ]
            if not manager or not delegations:
                raise AuthorizationError("AUTH_SCOPE_OUTSIDE_GRANT", "需要当前空间的授权管理权限")
        membership = (
            session.query(SysUserWorkspace.id)
            .filter(SysUserWorkspace.uid == SysUser.id, SysUserWorkspace.oid == workspace_oid)
            .exists()
        )
        query = session.query(SysUser).filter(membership)
        if keyword.strip():
            escaped = keyword.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            query = query.filter(
                or_(
                    SysUser.account.ilike(f"%{escaped}%", escape="\\"),
                    SysUser.name.ilike(f"%{escaped}%", escape="\\"),
                )
            )
        total = query.count()
        rows = query.order_by(SysUser.id).offset((page - 1) * page_size).limit(page_size).all()
        items = []
        for user in rows:
            item = {
                "id": user.id,
                "account": user.account,
                "name": user.name,
                "status": user.status,
                "manageable": False,
            }
            try:
                authorization = read_authorization(user, manifest=policy)
                scoped = [
                    g
                    for g in authorization.grants
                    if (g.workspace_oid, g.datasource_id) == (workspace_oid, policy.datasource_id)
                ]
                # Membership is guaranteed by the paged query. Reuse the pure
                # containment check, avoiding membership/manager SQL per account.
                if not platform_admin:
                    assert_delegated_grants(delegations, scoped)
                item.update(
                    manageable=True,
                    document={
                        "schema_version": 2,
                        "revision": authorization.revision,
                        "grants": [g.model_dump(mode="json") for g in scoped],
                    },
                    active_count=sum(g.active(now) for g in scoped),
                    managed=bool(scoped)
                    or f"{workspace_oid}:{policy.datasource_id}" in authorization.managed_resources,
                )
            except (AuthorizationError, ValueError, TypeError):
                # A broader or malformed document must neither leak nor disable the entire page.
                item["message"] = "授权超出管理范围或配置异常，请联系上级管理员"
            items.append(item)
        return success_response(
            data={"items": items, "total": total, "page": page, "page_size": page_size}
        )
    except AuthorizationError as exc:
        handle_error(exc)


@router.get("/catalog")
def catalog(
    datasource_id: int | None = Query(None),
    refresh: bool = Query(False),
    session: Session = Depends(get_session),
    current_user=Depends(get_current_user),
    workspace_oid: int = Depends(get_workspace_oid),
):
    try:
        assert_workspace_member(session, current_user, workspace_oid)
        policy = environment_manifest(workspace_oid, datasource_id, refresh=refresh)
        if is_platform_admin(current_user):
            rows = policy.catalog
        else:
            ctx = context_for(
                current_user, workspace_oid, policy.datasource_id, policy, session=session
            )
            rows = [
                row
                for row in policy.catalog
                if any(
                    all(row.get(k) == v for k, v in g.keys().items() if k != "student_id")
                    for g in ctx.grants
                )
            ]
        return success_response(
            data={
                "items": rows,
                "levels": ["city", "district", "school", "grade", "class", "student"],
                "workspace_oid": workspace_oid,
                "datasource_id": policy.datasource_id,
                "city_id": policy.catalog[0].get("city_id") if policy.catalog else None,
                "city_label": "全市",
                "supports_history": False,
            }
        )
    except AuthorizationError as exc:
        handle_error(exc)


@router.get("/grants/{user_id}")
def get_grants(
    user_id: int,
    datasource_id: int | None = Query(None),
    session: Session = Depends(get_session),
    current_user=Depends(get_current_user),
    workspace_oid: int = Depends(get_workspace_oid),
):
    try:
        policy = resource(session, current_user, datasource_id, workspace_oid)
        datasource_id = policy.datasource_id
        user = target_user(session, user_id)
        if user_id != current_user.id:
            assert_can_manage(
                session, current_user, user, workspace_oid, datasource_id, manifest=policy
            )
        return success_response(
            data=response_document(user, workspace_oid, datasource_id, manifest=policy)
        )
    except AuthorizationError as exc:
        handle_error(exc)


def save(session, actor, user, policy, payload, *, validate_only=False):
    payload = payload.model_copy(
        update={"grants": tuple(normalize_education_grant(g, policy) for g in payload.grants)}
    )
    if any(
        (g.workspace_oid, g.datasource_id) != (policy.workspace_oid, policy.datasource_id)
        for g in payload.grants
    ):
        raise AuthorizationError("AUTH_SCOPE_OUTSIDE_GRANT", "只能配置当前工作空间的固定学情库")
    assert_can_manage(
        session,
        actor,
        user,
        policy.workspace_oid,
        policy.datasource_id,
        payload.grants,
        manifest=policy,
    )
    for grant in payload.grants:
        validate_grant_catalog(grant, policy)
    if read_authorization(user).revision != payload.expected_revision:
        raise AuthorizationError("AUTH_CONTEXT_STALE", "权限已被修改，请刷新后重试")
    current = read_authorization(user)
    remaining = tuple(
        g
        for g in current.grants
        if (g.workspace_oid, g.datasource_id) != (policy.workspace_oid, policy.datasource_id)
    )
    # Preview validates the complete resulting document too (ID collisions/size).
    EducationAuthorization(
        revision=current.revision + 1,
        grants=(*remaining, *payload.grants),
        managed_resources=current.managed_resources,
    )
    if validate_only:
        return
    before = fingerprint(read_authorization(user))
    document = replace_resource_grants(
        user, policy.workspace_oid, policy.datasource_id, payload.expected_revision, payload.grants
    )
    from audit.models.audit import AuditOperationLog

    # Transactional operation record, not fire-and-forget. Grant values are not logged.
    session.add(
        AuditOperationLog(
            user_id=actor.id,
            user_account=actor.account,
            workspace_oid=policy.workspace_oid,
            operation_type="update",
            resource_type="edu_auth_v2",
            resource_id=str(user.id),
            request_method="PUT",
            request_path="/permission/edu/v2/grants",
            detail=json.dumps(
                {
                    "datasource_id": policy.datasource_id,
                    "before": before,
                    "after": fingerprint(document),
                    "revision": document.revision,
                }
            ),
        )
    )
    session.add(user)


@router.put("/grants/{user_id}")
def put_grants(
    user_id: int,
    payload: GrantUpdate,
    datasource_id: int | None = Query(None),
    session: Session = Depends(get_session),
    current_user=Depends(get_current_user),
    workspace_oid: int = Depends(get_workspace_oid),
):
    try:
        policy = resource(session, current_user, datasource_id, workspace_oid)
        datasource_id = policy.datasource_id
        user = target_user(session, user_id, lock=True)
        save(session, current_user, user, policy, payload)
        session.commit()
        return success_response(
            data=response_document(user, workspace_oid, datasource_id, manifest=policy)
        )
    except AuthorizationError as exc:
        session.rollback()
        handle_error(exc)


@router.post("/effective")
def effective(
    payload: EffectiveRequest,
    session: Session = Depends(get_session),
    current_user=Depends(get_current_user),
    workspace_oid: int = Depends(get_workspace_oid),
):
    try:
        policy = resource(session, current_user, payload.datasource_id, workspace_oid)
        user = target_user(session, payload.user_id)
        if user.id != current_user.id:
            assert_can_manage(
                session, current_user, user, workspace_oid, policy.datasource_id, manifest=policy
            )
        ctx = context_for(user, workspace_oid, policy.datasource_id, policy, session=session)
        result = ctx.model_context()
        if payload.sql:
            from src.chat.service.semantic_permission_validator import PermissionValidator
            from src.chat.service.semantic_query import _query

            proof = PermissionValidator(ctx).prove(_query(payload.sql, policy.dialect))
            result["validation"] = {
                "phase": "semantic",
                "allowed": True,
                "output_columns": proof.output_columns,
                "physical_verification_required": True,
            }
        return success_response(data=result)
    except AuthorizationError as exc:
        return success_response(data={"validation": {"allowed": False, **exc.detail}})
    except (SemanticQueryError, SqlglotError) as exc:
        return success_response(
            data={
                "validation": {"allowed": False, "code": "SQL_INVALID", "message": str(exc)[:250]}
            }
        )


def parse_batch(text):
    rows = list(csv.DictReader(io.StringIO(text.lstrip("\ufeff"))))
    if not rows or len(rows) > 500:
        raise BadRequestException("CSV须包含1至500行")
    result = []
    for row in rows:
        try:
            grants = json.loads(row["grants_json"])
            result.append(
                (
                    int(row["user_id"]),
                    GrantUpdate(expected_revision=int(row["expected_revision"]), grants=grants),
                )
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise BadRequestException(
                "CSV需包含user_id,expected_revision,grants_json，授权必须为有效JSON数组"
            ) from exc
    if len({uid for uid, _ in result}) != len(result):
        raise BadRequestException("同一用户在一个批次中只能出现一次")
    return sorted(result, key=lambda row: row[0])


def batch(payload, session, actor, workspace_oid, *, apply):
    try:
        policy = resource(session, actor, payload.datasource_id, workspace_oid)
        rows = parse_batch(payload.csv)
        # Lock in stable order, validate every row before any mutation; apply is atomic.
        users = [(target_user(session, uid, lock=apply), update) for uid, update in rows]
        for user, update in users:
            save(session, actor, user, policy, update, validate_only=True)
        if apply:
            for user, update in users:
                save(session, actor, user, policy, update)
            session.commit()
        return success_response(
            data={
                "validated": len(rows),
                "applied": len(rows) if apply else 0,
                "users": [
                    {
                        "user_id": u.id,
                        "expected_revision": p.expected_revision,
                        "grant_count": len(p.grants),
                    }
                    for u, p in users
                ],
            }
        )
    except AuthorizationError as exc:
        session.rollback()
        handle_error(exc)


@router.post("/batch-bind/preview")
def preview_batch(
    payload: BatchRequest,
    session: Session = Depends(get_session),
    current_user=Depends(get_current_user),
    workspace_oid: int = Depends(get_workspace_oid),
):
    return batch(payload, session, current_user, workspace_oid, apply=False)


@router.post("/batch-bind/apply")
def apply_batch(
    payload: BatchRequest,
    session: Session = Depends(get_session),
    current_user=Depends(get_current_user),
    workspace_oid: int = Depends(get_workspace_oid),
):
    return batch(payload, session, current_user, workspace_oid, apply=True)
