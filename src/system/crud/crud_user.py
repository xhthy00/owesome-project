"""User CRUD operations."""

from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from system.models.user import SysUser
from system.models.workspace import SysUserWorkspace
from common.core.security import get_password_hash, verify_password


def get_user_by_account(session: Session, account: str) -> Optional[SysUser]:
    """Get user by account."""
    return session.query(SysUser).filter(SysUser.account == account).first()


def get_user_by_id(session: Session, user_id: int) -> Optional[SysUser]:
    """Get user by id."""
    return session.query(SysUser).filter(SysUser.id == user_id).first()


def create_user(session: Session, account: str, name: str, password: str, **kwargs) -> SysUser:
    """Create a new user."""
    oid = int(kwargs.get("oid", 1) or 1)
    user = SysUser(
        account=account,
        name=name,
        password=get_password_hash(password),
        email=kwargs.get("email"),
        oid=oid,
        status=kwargs.get("status", 1),
        create_time=kwargs.get("create_time", int(datetime.now().timestamp() * 1000)),
        language=kwargs.get("language", "zh-CN"),
        origin=kwargs.get("origin", 0),
    )
    session.add(user)
    session.flush()
    if (
        session.query(SysUserWorkspace)
        .filter(SysUserWorkspace.uid == user.id, SysUserWorkspace.oid == oid)
        .first()
        is None
    ):
        session.add(
            SysUserWorkspace(
                uid=user.id,
                oid=oid,
                weight=int(kwargs.get("workspace_weight", 0) or 0),
                create_time=datetime.now(),
            )
        )
    session.commit()
    session.refresh(user)
    return user


def authenticate(session: Session, account: str, password: str) -> Optional[SysUser]:
    """Authenticate user with account and password."""
    user = get_user_by_account(session, account)
    if not user:
        return None
    if not verify_password(password, user.password):
        return None
    return user