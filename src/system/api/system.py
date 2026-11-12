"""System API routes (auth, users)."""

from fastapi import APIRouter, Depends, Request
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from common.core.database import SessionLocal, get_session
from common.core.security import create_access_token, get_password_hash, verify_password
from common.exceptions.base import BadRequestException, NotFoundException, UnauthorizedException
from common.schemas.response import success_response
from system.api.auth_deps import get_current_user
from system.authz import effective_sys_role, is_platform_admin, is_super_admin
from system.crud.crud_user import create_user, get_user_by_account, get_user_by_id
from system.schemas import UserCreate
from system.workspace_scope import get_workspace_oid

router = APIRouter(prefix="/system", tags=["system"])


def _client_ip(request: Request) -> str:
    """从请求头解析客户端真实 IP。"""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    real = request.headers.get("x-real-ip")
    if real:
        return real.strip()
    return request.client.host if request.client else ""


@router.post("/register")
def register(user_in: UserCreate, session: Session = Depends(get_session)):
    """Register a new user."""
    existing = get_user_by_account(session, user_in.account)
    if existing:
        raise BadRequestException("Account already exists")
    user = create_user(
        session,
        account=user_in.account,
        name=user_in.name,
        password=user_in.password,
        email=user_in.email,
        oid=user_in.oid,
        language=user_in.language,
    )
    return success_response(
        data={
            "id": user.id,
            "account": user.account,
            "name": user.name,
            "email": user.email,
            "oid": user.oid,
            "status": user.status,
            "language": user.language,
            "origin": user.origin,
            "create_time": user.create_time,
        },
        message="User registered successfully"
    )


@router.post("/login")
def login(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    session: Session = Depends(get_session),
):
    """Login and get access token. Records login audit log (success/fail)."""
    from audit.service.writer import log_login  # 局部 import 避免循环

    account = form_data.username
    ip = _client_ip(request)
    ua = request.headers.get("user-agent")

    user = get_user_by_account(session, account)
    if user is None:
        log_login(
            session_factory=SessionLocal,
            account=account,
            success=False,
            fail_reason="账号不存在",
            user_account=account,
            ip=ip,
            user_agent=ua,
        )
        raise UnauthorizedException("账号或密码错误")
    if user.status != 1:
        log_login(
            session_factory=SessionLocal,
            account=account,
            success=False,
            fail_reason="账号已禁用",
            user_id=user.id,
            user_account=account,
            ip=ip,
            user_agent=ua,
        )
        raise UnauthorizedException("账号已禁用")
    if not verify_password(form_data.password, user.password):
        log_login(
            session_factory=SessionLocal,
            account=account,
            success=False,
            fail_reason="密码错误",
            user_id=user.id,
            user_account=account,
            ip=ip,
            user_agent=ua,
        )
        raise UnauthorizedException("账号或密码错误")

    log_login(
        session_factory=SessionLocal,
        account=account,
        success=True,
        user_id=user.id,
        user_account=user.account,
        ip=ip,
        user_agent=ua,
    )
    access_token = create_access_token(user.id)
    return success_response(
        data={"access_token": access_token, "token_type": "bearer"},
        message="Login successful"
    )


@router.get("/me")
def get_me(
    current_user=Depends(get_current_user),
    session: Session = Depends(get_session),
    workspace_oid: int = Depends(get_workspace_oid),
):
    """Get current user info."""
    from datasource.service.edu_permission import (
        edu_scope_summary,
        looks_like_school_token,
        lookup_school_display_name,
    )

    db_user = get_user_by_id(session, current_user.id)
    edu_scope = edu_scope_summary(db_user) if db_user else {}
    raw_variables = getattr(db_user, "system_variables", None)
    if isinstance(raw_variables, dict) and "edu_auth_v2" in raw_variables:
        # v2：分析工具/前端需要资源数据源、范围层级、学校/班级与能力。
        try:
            from src.datasource.service.education_report_scope import load_report_scope
            from src.datasource.service.education_source import source_settings

            _, _kind, _config, datasource_id, _binding = source_settings()
            scope = load_report_scope(
                session, db_user, int(workspace_oid), int(datasource_id)
            )
            edu_scope = scope.summary() if scope is not None else {"edu_role": "v2"}
        except Exception:
            edu_scope = {"edu_role": "v2"}
    if edu_scope and edu_scope.get("edu_role") != "v2":
        sid = str(edu_scope.get("school_id") or "").strip()
        stored = str(edu_scope.get("school_name") or "").strip()
        looked = ""
        if sid:
            try:
                looked = lookup_school_display_name(
                    session, sid, workspace_oid=int(current_user.oid)
                )
            except Exception:
                looked = ""
        plain = ""
        for cand in (looked, stored):
            if cand and not looks_like_school_token(cand):
                plain = cand
                break
        if plain:
            edu_scope["school_name"] = plain
    return success_response(
        data={
            "id": current_user.id,
            "account": current_user.account,
            "name": current_user.name,
            "email": current_user.email,
            "oid": current_user.oid,
            "status": current_user.status,
            "language": current_user.language,
            "origin": current_user.origin,
            "create_time": current_user.create_time,
            "edu_scope": edu_scope,
            "sys_role": effective_sys_role(db_user),
            "is_platform_admin": is_platform_admin(db_user) if db_user else False,
            "is_super_admin": is_super_admin(db_user) if db_user else False,
        }
    )


@router.patch("/me")
def update_me(
    payload: dict,
    session: Session = Depends(get_session),
    current_user=Depends(get_current_user),
):
    """当前用户修改昵称。"""
    name = str(payload.get("name") or "").strip()
    if not name:
        raise BadRequestException("昵称不能为空")
    if len(name) > 255:
        raise BadRequestException("昵称过长")
    user = get_user_by_id(session, current_user.id)
    if user is None:
        raise NotFoundException("User not found")
    user.name = name
    session.commit()
    session.refresh(user)
    return success_response(data={"id": user.id, "name": user.name}, message="昵称已更新")


@router.patch("/me/password")
def update_my_password(
    payload: dict,
    session: Session = Depends(get_session),
    current_user=Depends(get_current_user),
):
    """当前用户修改密码，需校验原密码。"""
    old_password = str(payload.get("old_password") or "")
    new_password = str(payload.get("new_password") or "")
    if not old_password or not new_password:
        raise BadRequestException("请填写原密码和新密码")
    if len(new_password) < 6:
        raise BadRequestException("新密码至少 6 位")
    if old_password == new_password:
        raise BadRequestException("新密码不能与原密码相同")
    user = get_user_by_id(session, current_user.id)
    if user is None:
        raise NotFoundException("User not found")
    if not verify_password(old_password, user.password):
        raise BadRequestException("原密码错误")
    user.password = get_password_hash(new_password)
    session.commit()
    return success_response(data={"id": user.id}, message="密码已更新")
