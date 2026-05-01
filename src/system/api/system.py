"""System API routes (auth, users)."""

from fastapi import APIRouter, Depends
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from common.core.database import get_session
from common.core.security import create_access_token, decode_access_token
from common.exceptions.base import UnauthorizedException, BadRequestException, NotFoundException
from common.schemas.response import success_response
from system.schemas import UserCreate, UserResponse
from system.crud.crud_user import (
    get_user_by_account,
    create_user,
    authenticate,
    get_user_by_id,
)

router = APIRouter(prefix="/system", tags=["system"])

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/system/login")


def get_current_user(
    token: str = Depends(oauth2_scheme),
    session: Session = Depends(get_session),
) -> UserResponse:
    """Get current authenticated user from JWT token."""
    payload = decode_access_token(token)
    if payload is None:
        raise UnauthorizedException("Invalid or expired token")
    user_id = int(payload.get("sub"))
    user = get_user_by_id(session, user_id)
    if user is None:
        raise NotFoundException("User not found")
    return user


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
    form_data: OAuth2PasswordRequestForm = Depends(),
    session: Session = Depends(get_session),
):
    """Login and get access token."""
    user = authenticate(session, form_data.username, form_data.password)
    if not user:
        raise UnauthorizedException("Incorrect account or password")
    access_token = create_access_token(user.id)
    return success_response(
        data={"access_token": access_token, "token_type": "bearer"},
        message="Login successful"
    )


@router.get("/me")
def get_me(current_user = Depends(get_current_user)):
    """Get current user info."""
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
        }
    )
