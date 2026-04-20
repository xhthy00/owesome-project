"""System API routes (auth, users)."""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from common.core.database import get_session
from common.core.security import create_access_token
from system.schemas import UserCreate, UserResponse, TokenResponse
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
    from src.common.core.security import decode_access_token

    payload = decode_access_token(token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )
    user_id = int(payload.get("sub"))
    user = get_user_by_id(session, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )
    return user


@router.post("/register", response_model=UserResponse)
def register(user_in: UserCreate, session: Session = Depends(get_session)):
    """Register a new user."""
    existing = get_user_by_account(session, user_in.account)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Account already exists",
        )
    user = create_user(
        session,
        account=user_in.account,
        name=user_in.name,
        password=user_in.password,
        email=user_in.email,
        oid=user_in.oid,
        language=user_in.language,
    )
    return user


@router.post("/login", response_model=TokenResponse)
def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    session: Session = Depends(get_session),
):
    """Login and get access token."""
    user = authenticate(session, form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect account or password",
        )
    access_token = create_access_token(user.id)
    return TokenResponse(access_token=access_token)


@router.get("/me", response_model=UserResponse)
def get_me(current_user = Depends(get_current_user)):
    """Get current user info."""
    return current_user