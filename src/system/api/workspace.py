"""Workspace and member management APIs."""

from datetime import datetime

from fastapi import APIRouter, Body, Depends, Query
from sqlalchemy.orm import Session

from common.core.database import get_session
from common.exceptions.base import BadRequestException, NotFoundException
from common.schemas.response import success_response
from system.api.system import get_current_user
from system.models.user import SysUser
from system.models.workspace import SysUserWorkspace, SysWorkspace

router = APIRouter(prefix="/system/workspace", tags=["workspace"])


@router.get("")
def list_workspaces(
    session: Session = Depends(get_session),
    current_user=Depends(get_current_user),
):
    _ = current_user
    rows = session.query(SysWorkspace).order_by(SysWorkspace.id.asc()).all()
    return success_response(
        data=[{"id": item.id, "name": item.name, "create_time": item.create_time} for item in rows]
    )


@router.get("/{workspace_id}")
def get_workspace(
    workspace_id: int,
    session: Session = Depends(get_session),
    current_user=Depends(get_current_user),
):
    _ = current_user
    row = session.query(SysWorkspace).filter(SysWorkspace.id == workspace_id).first()
    if not row:
        raise NotFoundException("Workspace not found")
    return success_response(data={"id": row.id, "name": row.name, "create_time": row.create_time})


@router.post("")
def create_workspace(
    payload: dict,
    session: Session = Depends(get_session),
    current_user=Depends(get_current_user),
):
    _ = current_user
    name = (payload.get("name") or "").strip()
    if not name:
        raise BadRequestException("Workspace name is required")
    exists = session.query(SysWorkspace).filter(SysWorkspace.name == name).first()
    if exists:
        raise BadRequestException("Workspace already exists")
    row = SysWorkspace(name=name, create_time=int(datetime.now().timestamp() * 1000))
    session.add(row)
    session.commit()
    session.refresh(row)
    return success_response(data={"id": row.id, "name": row.name}, message="Workspace created")


@router.put("")
def update_workspace(
    payload: dict,
    session: Session = Depends(get_session),
    current_user=Depends(get_current_user),
):
    _ = current_user
    workspace_id = payload.get("id")
    row = session.query(SysWorkspace).filter(SysWorkspace.id == workspace_id).first()
    if not row:
        raise NotFoundException("Workspace not found")
    name = (payload.get("name") or "").strip()
    if name:
        row.name = name
    session.commit()
    return success_response(data={"id": row.id, "name": row.name}, message="Workspace updated")


@router.delete("/{workspace_id}")
def delete_workspace(
    workspace_id: int,
    session: Session = Depends(get_session),
    current_user=Depends(get_current_user),
):
    _ = current_user
    if workspace_id == 1:
        raise BadRequestException("Default workspace cannot be deleted")
    row = session.query(SysWorkspace).filter(SysWorkspace.id == workspace_id).first()
    if not row:
        raise NotFoundException("Workspace not found")
    session.query(SysUserWorkspace).filter(SysUserWorkspace.oid == workspace_id).delete()
    session.delete(row)
    session.commit()
    return success_response(data={"id": workspace_id}, message="Workspace deleted")


@router.get("/uws/pager/{page_num}/{page_size}")
def list_workspace_members(
    page_num: int,
    page_size: int,
    oid: int = Query(...),
    keyword: str | None = Query(default=None),
    session: Session = Depends(get_session),
    current_user=Depends(get_current_user),
):
    _ = current_user
    q = session.query(SysUserWorkspace).filter(SysUserWorkspace.oid == oid)
    if keyword:
        user_ids = [
            u.id
            for u in session.query(SysUser)
            .filter(
                (SysUser.name.ilike(f"%{keyword}%"))
                | (SysUser.account.ilike(f"%{keyword}%"))
                | (SysUser.email.ilike(f"%{keyword}%"))
            )
            .all()
        ]
        q = q.filter(SysUserWorkspace.uid.in_(user_ids or [-1]))
    total = q.count()
    rows = q.offset((page_num - 1) * page_size).limit(page_size).all()
    user_ids = [item.uid for item in rows]
    user_map = {u.id: u for u in session.query(SysUser).filter(SysUser.id.in_(user_ids)).all()} if user_ids else {}
    items = []
    for item in rows:
        user = user_map.get(item.uid)
        if not user:
            continue
        items.append(
            {
                "id": item.id,
                "uid": item.uid,
                "oid": item.oid,
                "weight": item.weight,
                "account": user.account,
                "name": user.name,
                "email": user.email,
            }
        )
    return success_response(data={"total": total, "items": items})


@router.post("/uws")
def add_workspace_member(
    payload: dict,
    session: Session = Depends(get_session),
    current_user=Depends(get_current_user),
):
    _ = current_user
    uid = payload.get("uid")
    uid_list = payload.get("uid_list") or []
    oid = payload.get("oid")
    weight = int(payload.get("weight", 0))
    if uid and not uid_list:
        uid_list = [uid]
    if not uid_list:
        raise BadRequestException("uid_list is required")
    if not oid:
        oid = int(current_user.oid)
    created = 0
    now_ts = datetime.now()
    for one_uid in uid_list:
        exists = (
            session.query(SysUserWorkspace)
            .filter(SysUserWorkspace.uid == one_uid, SysUserWorkspace.oid == oid)
            .first()
        )
        if exists:
            continue
        session.add(SysUserWorkspace(uid=one_uid, oid=oid, weight=weight, create_time=now_ts))
        created += 1
    session.commit()
    return success_response(data={"created": created}, message="Member added")


@router.put("/uws")
def update_workspace_member(
    payload: dict,
    session: Session = Depends(get_session),
    current_user=Depends(get_current_user),
):
    _ = current_user
    member_id = payload.get("id")
    uid = payload.get("uid")
    oid = payload.get("oid")
    row = None
    if member_id is not None:
        row = session.query(SysUserWorkspace).filter(SysUserWorkspace.id == member_id).first()
    elif uid is not None and oid is not None:
        row = (
            session.query(SysUserWorkspace)
            .filter(SysUserWorkspace.uid == uid, SysUserWorkspace.oid == oid)
            .first()
        )
    if not row:
        raise NotFoundException("Workspace member not found")
    row.weight = int(payload.get("weight", row.weight))
    session.commit()
    return success_response(data={"id": row.id, "weight": row.weight}, message="Member updated")


@router.get("/uws/option/pager/{page_num}/{page_size}")
def list_workspace_option_users(
    page_num: int,
    page_size: int,
    oid: int = Query(...),
    keyword: str | None = Query(default=None),
    session: Session = Depends(get_session),
    current_user=Depends(get_current_user),
):
    _ = current_user
    member_uid_set = {
        m.uid for m in session.query(SysUserWorkspace).filter(SysUserWorkspace.oid == oid).all()
    }
    q = session.query(SysUser)
    if keyword:
        q = q.filter(
            (SysUser.name.ilike(f"%{keyword}%"))
            | (SysUser.account.ilike(f"%{keyword}%"))
            | (SysUser.email.ilike(f"%{keyword}%"))
        )
    users = [u for u in q.order_by(SysUser.id.asc()).all() if u.id not in member_uid_set]
    total = len(users)
    start = (page_num - 1) * page_size
    items = users[start : start + page_size]
    return success_response(
        data={
            "total": total,
            "items": [{"id": u.id, "name": u.name, "account": u.account, "email": u.email} for u in items],
        }
    )


@router.get("/uws/option")
def get_workspace_option_user(
    keyword: str = Query(...),
    session: Session = Depends(get_session),
    current_user=Depends(get_current_user),
):
    _ = current_user
    user = (
        session.query(SysUser)
        .filter((SysUser.account == keyword) | (SysUser.name == keyword) | (SysUser.email == keyword))
        .first()
    )
    if not user:
        return success_response(data={})
    return success_response(data={"id": user.id, "name": user.name, "account": user.account, "email": user.email})


@router.delete("/uws")
def remove_workspace_member(
    payload: dict = Body(default={}),
    uid: int | None = Query(default=None),
    oid: int | None = Query(default=None),
    session: Session = Depends(get_session),
    current_user=Depends(get_current_user),
):
    _ = current_user
    uid_list = payload.get("uid_list") or ([] if uid is None else [uid])
    oid_value = payload.get("oid", oid if oid is not None else int(current_user.oid))
    if not uid_list:
        raise BadRequestException("uid_list is required")
    session.query(SysUserWorkspace).filter(
        SysUserWorkspace.uid.in_(uid_list), SysUserWorkspace.oid == oid_value
    ).delete(synchronize_session=False)
    session.commit()
    return success_response(data={"uid_list": uid_list, "oid": oid_value}, message="Member removed")
