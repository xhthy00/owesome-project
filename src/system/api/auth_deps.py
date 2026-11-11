"""跨切面鉴权依赖：get_current_user 与 oauth2_scheme。

从 system.api.system 抽出，供 system 自身与 audit 等其他 feature 包共用，
避免 system.api.system ↔ audit 的循环 import。
"""

from fastapi import Depends, Request
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from common.core.database import get_session
from common.core.security import decode_access_token
from common.exceptions.base import ForbiddenException, NotFoundException, UnauthorizedException
from system.crud.crud_user import get_user_by_id
from system.schemas import UserResponse

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/system/login")


def get_current_user(
    token: str = Depends(oauth2_scheme),
    session: Session = Depends(get_session),
    request: Request = None,
) -> UserResponse:
    """根据 JWT token 获取当前已认证用户。"""
    payload = decode_access_token(token)
    if payload is None:
        raise UnauthorizedException("Invalid or expired token")
    user_id = int(payload.get("sub"))
    user = get_user_by_id(session, user_id)
    if user is None:
        raise NotFoundException("User not found")
    if int(user.status) != 1:
        raise UnauthorizedException("账号已停用")
    # Legacy education endpoints do not understand the resource-bound v2 scopes.
    # Keep them available to unmigrated accounts; prevent migrated accounts from
    # bypassing query_semantic through the old report/library/raw-query APIs.
    raw_variables = getattr(user, "system_variables", None)
    variables = raw_variables if isinstance(raw_variables, dict) else {}
    document = variables.get("edu_auth_v2") or {}
    if (
        request is not None
        and request.url.path.startswith("/api/v1/education/")
        and not (
            request.method == "GET"
            and request.url.path.startswith("/api/v1/education/report-history")
        )
        and isinstance(document, dict)
        and (document.get("managed_resources") or document.get("grants"))
    ):
        raise ForbiddenException("此账号已使用分级权限，请通过智能问数查询学情数据")
    return user
