"""操作/访问日志采集装饰器。

用法：
    @router.post("/user")
    @audit_operation(operation_type="create", resource_type="user", resource_id_arg="payload")
    def create_user(
        request: Request,
        payload: dict,
        current_user=Depends(get_current_user),
    ): ...

装饰器从端点函数 kwargs 中取真正的 ``fastapi.Request``、``current_user``
及 *_arg 指定的业务参数，调用 writer 记录日志，不改变原函数返回值与异常传播。
写库走 SessionLocal，fire-and-forget，失败只 warning 不抛。

支持同步端点函数与 async 端点函数。
"""
import functools
import inspect
import json
import logging
import time
from typing import Callable, Optional, get_type_hints

from fastapi import Request

from audit.service.writer import log_access, log_operation
from common.core.database import SessionLocal

logger = logging.getLogger(__name__)


def _find_http_request(kwargs: dict) -> Optional[Request]:
    """在端点 kwargs 中查找 fastapi.Request 实例（不依赖参数名）。"""
    for value in kwargs.values():
        if isinstance(value, Request):
            return value
    return None


def _client_ip(request: Request) -> Optional[str]:
    """优先从 XFF/X-Real-IP 取真实客户端 IP，回退 request.client.host。"""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    real = request.headers.get("x-real-ip")
    if real:
        return real.strip()
    return request.client.host if request.client else None


def _safe_get(kwargs: dict, name: Optional[str]):
    """按 *_arg 名从端点 kwargs 取值，支持点号属性路径；缺名或 None 返回 None。"""
    if not name:
        return None
    parts = name.split(".")
    obj = kwargs.get(parts[0])
    for part in parts[1:]:
        if obj is None:
            return None
        obj = getattr(obj, part, None)
    return obj


def _jsonable(obj):
    """端点入参可序列化为 JSON。优先 pydantic v2 model_dump()，回退 .dict()，其余原样。"""
    if hasattr(obj, "model_dump"):
        try:
            return obj.model_dump()
        except Exception:  # noqa: BLE001
            return str(obj)
    if hasattr(obj, "dict"):
        try:
            return obj.dict()
        except Exception:  # noqa: BLE001
            return str(obj)
    return obj


def _preserve_endpoint_signature(wrapper: Callable, func: Callable) -> Callable:
    """让 FastAPI 仍按原端点签名解析 Body/Depends（wraps 默认只保留 *args/**kwargs）。

    需把注解解析成真实类型：``from __future__ import annotations`` 下签名里是字符串，
    FastAPI 会用 wrapper 的 ``__globals__``（装饰器模块）求值，解析不到 Pydantic 模型，
    会把 ``req`` 误判为 query 参数导致 422。
    """
    sig = inspect.signature(func)
    try:
        hints = get_type_hints(func)
    except Exception:  # noqa: BLE001
        hints = {}
    if hints:
        params = [
            p.replace(annotation=hints[name]) if name in hints else p
            for name, p in sig.parameters.items()
        ]
        ret = hints.get("return", sig.return_annotation)
        sig = sig.replace(parameters=params, return_annotation=ret)
    wrapper.__signature__ = sig  # type: ignore[attr-defined]
    try:
        wrapper.__annotations__ = dict(hints) if hints else dict(getattr(func, "__annotations__", {}) or {})
    except Exception:  # noqa: BLE001
        pass
    return wrapper


def audit_access(*, datasource_id_arg: Optional[str] = None, query_arg: Optional[str] = None):
    """访问日志装饰器：贴在 NL2SQL / 查询端点上，成功失败都记一条 AuditAccessLog。

    自动在端点 kwargs 中查找 ``fastapi.Request`` 以获取 method/path/ip/user-agent；
    取 ``current_user`` 获取用户身份；``datasource_id_arg`` / ``query_arg`` 支持点号
    路径（如 ``request.datasource_id`` / ``req.question``）。异常透传不吞错；日志写库
    失败只 warning。
    """

    def decorator(func: Callable):
        is_async = inspect.iscoroutinefunction(func)

        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            request = _find_http_request(kwargs)
            current_user = kwargs.get("current_user")
            start = time.time()
            success = True
            error_msg: Optional[str] = None
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                success = False
                error_msg = str(e)
                raise
            finally:
                _write_access_log(
                    request, current_user, kwargs, datasource_id_arg, query_arg, success, error_msg, start
                )

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            request = _find_http_request(kwargs)
            current_user = kwargs.get("current_user")
            start = time.time()
            success = True
            error_msg: Optional[str] = None
            try:
                return func(*args, **kwargs)
            except Exception as e:
                success = False
                error_msg = str(e)
                raise
            finally:
                _write_access_log(
                    request, current_user, kwargs, datasource_id_arg, query_arg, success, error_msg, start
                )

        if is_async:
            return _preserve_endpoint_signature(async_wrapper, func)
        return _preserve_endpoint_signature(sync_wrapper, func)

    return decorator


def _write_access_log(
    request: Optional[Request],
    current_user,
    kwargs: dict,
    datasource_id_arg: Optional[str],
    query_arg: Optional[str],
    success: bool,
    error_msg: Optional[str],
    start: float,
):
    """访问日志 fire-and-forget 写入。"""
    try:
        log_access(
            session_factory=SessionLocal,
            request_method=request.method if request else "",
            request_path=request.url.path if request else "",
            datasource_id=_safe_get(kwargs, datasource_id_arg),
            query_text=_safe_get(kwargs, query_arg),
            user_id=getattr(current_user, "id", None),
            user_account=getattr(current_user, "account", None),
            workspace_oid=getattr(current_user, "oid", None),
            ip=_client_ip(request) if request else None,
            user_agent=request.headers.get("user-agent") if request else None,
            success=success,
            error_msg=error_msg,
            elapsed_ms=int((time.time() - start) * 1000),
        )
    except Exception as log_err:  # noqa: BLE001
        logger.warning("audit_access log failed: %s", log_err)


def audit_operation(
    *,
    operation_type: str,
    resource_type: str,
    resource_id_arg: Optional[str] = None,
    detail_arg: Optional[str] = None,
):
    """操作日志装饰器：贴在增删改端点上，记一条 AuditOperationLog。

    operation_type / resource_type 必填；resource_id_arg / detail_arg 支持点号路径。
    异常透传不吞错；日志写库失败只 warning。
    """

    def decorator(func: Callable):
        is_async = inspect.iscoroutinefunction(func)

        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            request = _find_http_request(kwargs)
            current_user = kwargs.get("current_user")
            start = time.time()
            success = True
            error_msg: Optional[str] = None
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                success = False
                error_msg = str(e)
                raise
            finally:
                _write_operation_log(
                    request,
                    current_user,
                    kwargs,
                    operation_type,
                    resource_type,
                    resource_id_arg,
                    detail_arg,
                    success,
                    error_msg,
                    start,
                )

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            request = _find_http_request(kwargs)
            current_user = kwargs.get("current_user")
            start = time.time()
            success = True
            error_msg: Optional[str] = None
            try:
                return func(*args, **kwargs)
            except Exception as e:
                success = False
                error_msg = str(e)
                raise
            finally:
                _write_operation_log(
                    request,
                    current_user,
                    kwargs,
                    operation_type,
                    resource_type,
                    resource_id_arg,
                    detail_arg,
                    success,
                    error_msg,
                    start,
                )

        if is_async:
            return _preserve_endpoint_signature(async_wrapper, func)
        return _preserve_endpoint_signature(sync_wrapper, func)

    return decorator


def _write_operation_log(
    request: Optional[Request],
    current_user,
    kwargs: dict,
    operation_type: str,
    resource_type: str,
    resource_id_arg: Optional[str],
    detail_arg: Optional[str],
    success: bool,
    error_msg: Optional[str],
    start: float,
):
    """操作日志 fire-and-forget 写入。"""
    try:
        detail: Optional[str] = None
        if detail_arg:
            raw = _safe_get(kwargs, detail_arg)
            if raw is not None:
                try:
                    detail = json.dumps(_jsonable(raw), ensure_ascii=False)
                except Exception:  # noqa: BLE001
                    detail = str(raw)
        rid = _safe_get(kwargs, resource_id_arg)
        log_operation(
            session_factory=SessionLocal,
            operation_type=operation_type,
            resource_type=resource_type,
            resource_id=str(rid) if rid is not None else None,
            request_method=request.method if request else "",
            request_path=request.url.path if request else "",
            detail=detail,
            user_id=getattr(current_user, "id", None),
            user_account=getattr(current_user, "account", None),
            workspace_oid=getattr(current_user, "oid", None),
            ip=_client_ip(request) if request else None,
            user_agent=request.headers.get("user-agent") if request else None,
            success=success,
            error_msg=error_msg,
            elapsed_ms=int((time.time() - start) * 1000),
        )
    except Exception as log_err:  # noqa: BLE001
        logger.warning("audit_operation log failed: %s", log_err)
