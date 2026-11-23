"""跨切面鉴权依赖：get_current_user 与 oauth2_scheme。

从 system.api.system 抽出，供 system 自身与 audit 等其他 feature 包共用，
避免 system.api.system ↔ audit 的循环 import。

教育权限只有 V2 一个事实源。``/api/v1/education`` 下：
* 读类入口（已按 V2 范围下推）允许所有登录账号，由各接口校验有效授权；
* 其余维护类入口只允许平台管理员，不再依据旧教育角色放行。
"""

from fastapi import Depends, Request
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from common.core.database import get_session
from common.core.security import decode_access_token
from common.exceptions.base import ForbiddenException, NotFoundException, UnauthorizedException
from system.authz import is_platform_admin
from system.crud.crud_user import get_user_by_id
from system.schemas import UserResponse

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/system/login")

#: V2 已适配范围下推的读类入口。
_V2_READ_PREFIXES = (
    "/api/v1/education/generate-report",
    "/api/v1/education/batch-report",
    "/api/v1/education/diagnostic-report",
    "/api/v1/education/save-report-history",
    "/api/v1/education/report-history",
    "/api/v1/education/meta/options",
    "/api/v1/education/dimensions",
    "/api/v1/education/dashboards/line-reach",
    "/api/v1/education/overview",
    "/api/v1/education/anomaly-alerts",
    "/api/v1/education/exam-library",
    "/api/v1/education/score-library",
)


def _matches_prefix(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix + "/")


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
    path = request.url.path if request is not None else ""
    if path.startswith("/api/v1/education/") and not any(
        _matches_prefix(path, prefix) for prefix in _V2_READ_PREFIXES
    ):
        # V1 的“教育角色”授权已下线：维护/写入类教育入口只对平台管理员开放。
        if not is_platform_admin(user):
            raise ForbiddenException("该入口仅平台管理员可用；教育数据请通过智能问数或授权入口访问")
    return user
