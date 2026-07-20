"""System API routes (auth, users)."""

from fastapi import APIRouter, Depends, Request
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from common.core.database import SessionLocal, get_session
from common.core.security import create_access_token, verify_password
from common.exceptions.base import BadRequestException, UnauthorizedException
from common.schemas.response import success_response
from system.api.auth_deps import get_current_user
from system.authz import is_platform_admin
from system.crud.crud_user import create_user, get_user_by_account, get_user_by_id
from system.schemas import UserCreate

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
        raise UnauthorizedException("Incorrect account or password")
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
        raise UnauthorizedException("Account disabled")
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
        raise UnauthorizedException("Incorrect account or password")

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
):
    """Get current user info."""
    from datasource.service.edu_permission import edu_scope_summary

    db_user = get_user_by_id(session, current_user.id)
    edu_scope = edu_scope_summary(db_user) if db_user else {}
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
            "is_platform_admin": is_platform_admin(db_user) if db_user else False,
        }
    )
