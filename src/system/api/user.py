"""User management APIs."""

from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from common.core.database import get_session
from common.core.security import get_password_hash
from common.exceptions.base import BadRequestException, NotFoundException
from common.schemas.response import success_response
from system.api.system import get_current_user
from system.models.user import SysUser

router = APIRouter(prefix="/user", tags=["user"])


@router.get("/pager/{page_num}/{page_size}")
def pager_users(
    page_num: int,
    page_size: int,
    session: Session = Depends(get_session),
    current_user=Depends(get_current_user),
):
    _ = current_user
    q = session.query(SysUser).order_by(SysUser.id.asc())
    total = q.count()
    rows = q.offset((page_num - 1) * page_size).limit(page_size).all()
    items = [
        {
            "id": item.id,
            "account": item.account,
            "name": item.name,
            "email": item.email,
            "oid": item.oid,
            "status": item.status,
            "language": item.language,
            "origin": item.origin,
            "create_time": item.create_time,
        }
        for item in rows
    ]
    return success_response(data={"total": total, "items": items})


@router.post("")
def create_user(
    payload: dict,
    session: Session = Depends(get_session),
    current_user=Depends(get_current_user),
):
    _ = current_user
    account = (payload.get("account") or "").strip()
    name = (payload.get("name") or "").strip()
    password = payload.get("password") or "123456"
    if not account or not name:
        raise BadRequestException("account and name are required")
    if session.query(SysUser).filter(SysUser.account == account).first():
        raise BadRequestException("account already exists")
    now = int(datetime.now().timestamp() * 1000)
    row = SysUser(
        account=account,
        name=name,
        password=get_password_hash(password),
        email=payload.get("email"),
        oid=int(payload.get("oid", 1)),
        status=int(payload.get("status", 1)),
        create_time=now,
        language=payload.get("language", "zh-CN"),
        origin=int(payload.get("origin", 0)),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return success_response(data={"id": row.id}, message="User created")


@router.put("")
def update_user(
    payload: dict,
    session: Session = Depends(get_session),
    current_user=Depends(get_current_user),
):
    _ = current_user
    user_id = payload.get("id")
    row = session.query(SysUser).filter(SysUser.id == user_id).first()
    if not row:
        raise NotFoundException("User not found")
    for field in ("name", "email", "language"):
        if field in payload and payload[field] is not None:
            setattr(row, field, payload[field])
    if "oid" in payload and payload["oid"] is not None:
        row.oid = int(payload["oid"])
    if "status" in payload and payload["status"] is not None:
        row.status = int(payload["status"])
    session.commit()
    return success_response(data={"id": row.id}, message="User updated")


@router.delete("/{user_id}")
def delete_user(
    user_id: int,
    session: Session = Depends(get_session),
    current_user=Depends(get_current_user),
):
    _ = current_user
    row = session.query(SysUser).filter(SysUser.id == user_id).first()
    if not row:
        raise NotFoundException("User not found")
    session.delete(row)
    session.commit()
    return success_response(data={"id": user_id}, message="User deleted")


@router.patch("/status")
def update_user_status(
    payload: dict,
    session: Session = Depends(get_session),
    current_user=Depends(get_current_user),
):
    _ = current_user
    user_id = payload.get("id")
    row = session.query(SysUser).filter(SysUser.id == user_id).first()
    if not row:
        raise NotFoundException("User not found")
    row.status = int(payload.get("status", row.status))
    session.commit()
    return success_response(data={"id": row.id, "status": row.status}, message="Status updated")


@router.patch("/pwd/{user_id}")
def reset_user_pwd(
    user_id: int,
    payload: dict,
    session: Session = Depends(get_session),
    current_user=Depends(get_current_user),
):
    _ = current_user
    row = session.query(SysUser).filter(SysUser.id == user_id).first()
    if not row:
        raise NotFoundException("User not found")
    password = payload.get("password") or "123456"
    row.password = get_password_hash(password)
    session.commit()
    return success_response(data={"id": row.id}, message="Password reset")
