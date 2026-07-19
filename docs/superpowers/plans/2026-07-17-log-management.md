# 日志管理系统 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为学情分析平台新增"系统管理 - 日志管理"功能，采集访问日志（NL2SQL/学情查询）、操作日志（增删改写接口）、登录日志（登录成功/失败），写入 PostgreSQL，超管可见全部、普通用户仅见自己。

**Architecture:** 新建独立 `src/audit/` feature 包（models/crud/service/api 四层）。采集层三类不同触发点：登录日志在 `/system/login` 端点内显式记录；操作/访问日志用装饰器 `@audit_operation` / `@audit_access` 贴到接口。写入统一 fire-and-forget（`asyncio.create_task` + `to_thread`）+ 独立 session，失败不影响业务。后端 crud 层强制权限隔离（超管全部 / 普通用户仅自己），前端扩展 `/system/me` 返回 `is_platform_admin` 并在 `side-bar.tsx` 对"系统管理"菜单组做超管过滤。

**Tech Stack:** 后端 FastAPI + SQLModel + Alembic + PostgreSQL + pytest + ruff；前端 Next.js 14 Pages Router + React 18 + antd 5 + Tailwind + TypeScript。

**Spec:** `docs/superpowers/specs/2026-07-17-log-management-design.md`

---

## 文件结构总览

### 后端新建
- `src/audit/__init__.py` — 空包初始化
- `src/audit/models/__init__.py` — 导出 3 个 model
- `src/audit/models/audit.py` — `AuditAccessLog` / `AuditOperationLog` / `AuditLoginLog` 三个 SQLModel 表
- `src/audit/crud/__init__.py` — 空
- `src/audit/crud/audit.py` — `pager_access_log` / `pager_operation_log` / `pager_login_log`，含权限隔离过滤
- `src/audit/service/__init__.py` — 空
- `src/audit/service/writer.py` — fire-and-forget 异步写入器 `log_access` / `log_operation` / `log_login`
- `src/audit/service/decorators.py` — `@audit_access` / `@audit_operation` 装饰器
- `src/audit/api/__init__.py` — 空
- `src/audit/api/audit.py` — `router = APIRouter(prefix="/audit", tags=["audit"])`，3 个分页查询端点

### 后端修改
- `src/common/router.py` — import 并注册 `audit_router`
- `src/system/api/system.py` — `/system/me` 返回 `is_platform_admin`；`/system/login` 成功/失败记录登录日志
- `src/system/schemas.py` — `UserResponse` 增加 `is_platform_admin: bool = False`
- `alembic/env.py` — import 新 model 注册 metadata（autogenerate 需要）
- `src/chat/api/chat.py` — 给 generate-sql / execute-sql / chat-stream 贴 `@audit_access`
- `src/agent/education/api.py` — 给学情查询端点贴 `@audit_access`
- `src/system/api/user.py` / `workspace.py` / `src/datasource/api/datasource.py` / `src/system/api/permission.py` / `src/system/api/edu_permission.py` — 写接口贴 `@audit_operation`

### 前端新建
- `frontend-react/src/api/audit.ts` — `auditApi` 对象 + 3 个分页查询函数 + 类型定义
- `frontend-react/pages/system/log/access.tsx` — 访问日志列表页
- `frontend-react/pages/system/log/operation.tsx` — 操作日志列表页
- `frontend-react/pages/system/log/login.tsx` — 登录日志列表页

### 前端修改
- `frontend-react/src/api/auth.ts` — `CurrentUser` 增加 `is_platform_admin: boolean`
- `frontend-react/src/components/layout/side-bar.tsx` — 加"系统管理"一级 + 3 个二级子菜单 + 超管过滤

### 测试新建
- `tests/audit/__init__.py`
- `tests/audit/conftest.py` — 测试用 session fixture
- `tests/audit/test_models.py`
- `tests/audit/test_service_writer.py`
- `tests/audit/test_api_query.py`
- `tests/audit/test_decorators.py`
- `tests/audit/test_login_log.py`

### Alembic 迁移
- `alembic/versions/<rev>_add_audit_log_tables.py` — 由 `alembic revision --autogenerate` 生成后核对

---

## 关键参照模式（来自代码库现状，供各任务照抄）

**分页端点范式**（`src/system/api/user.py:28`）：`@router.get("/pager/{page_num}/{page_size}")` + `session: Session = Depends(get_session)` + `current_user = Depends(get_current_user)` + 返回 `success_response(data={"total": total, "items": items})`。

**SQLModel 表范式**（`src/system/models/user.py`）：`class SysUser(SQLModel, table=True)` + `__tablename__` + `id: Optional[int] = Field(default=None, sa_column=Column(BigInteger, primary_key=True, autoincrement=True))`。

**fire-and-forget 写入范式**（`src/agent/audit/tool_call_log.py`）：`asyncio.create_task(asyncio.to_thread(_sync_write, ...))`，内部 `try/except` 只 `logger.warning`，不抛出。

**统一响应**：`from common.schemas.response import success_response`，`success_response(data=..., message=...)`，返回 `{code, message, data}`。

**权限判定**：`from system.authz import is_platform_admin`，`is_platform_admin(user)` 收 `SysUser` ORM 对象，判定 `user.id == 1 and user.account == "admin"`。

**当前用户**：`current_user = Depends(get_current_user)`（`src/system/api/system.py:24`），返回对象有 `id/account/name/oid` 字段。

**前端 API 范式**（`src/api/system.ts`）：`apiRequest<T>(path)` + `export const xxxApi = { fn: () => apiRequest(...) }`。

**前端列表页范式**（`pages/construct/permission/members.tsx`）：`Typography.Title level={4}` + antd `Table rowKey="id"` + `useEffect` 加载 + `rounded-2xl border ... bg-white shadow-sm` 容器。

---

## Task 1: 审计日志数据模型 + Alembic 迁移

**Files:**
- Create: `src/audit/__init__.py`, `src/audit/models/__init__.py`, `src/audit/models/audit.py`
- Create: `src/audit/crud/__init__.py`, `src/audit/service/__init__.py`, `src/audit/api/__init__.py`
- Create: `tests/audit/__init__.py`, `tests/audit/conftest.py`, `tests/audit/test_models.py`
- Modify: `alembic/env.py`
- Create: `alembic/versions/<rev>_add_audit_log_tables.py`（autogenerate）

- [ ] **Step 1: 创建包与空 __init__**

创建以下空文件：
- `src/audit/__init__.py`
- `src/audit/models/__init__.py`（先空，Step 3 填）
- `src/audit/crud/__init__.py`
- `src/audit/service/__init__.py`
- `src/audit/api/__init__.py`
- `tests/audit/__init__.py`

- [ ] **Step 2: 写 models/audit.py**

`src/audit/models/audit.py`:
```python
"""审计日志数据模型：访问日志 / 操作日志 / 登录日志。"""
from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, Column, DateTime, Index
from sqlmodel import Field, SQLModel


class _AuditBase(SQLModel):
    """三类审计日志公共字段。"""

    trace_id: str = Field(default="-", max_length=64)
    user_id: Optional[int] = Field(default=None, sa_column=Column(BigInteger))
    user_account: Optional[str] = Field(default=None, max_length=255)
    workspace_oid: Optional[int] = Field(default=None, sa_column=Column(BigInteger))
    ip: Optional[str] = Field(default=None, max_length=64)
    user_agent: Optional[str] = Field(default=None, max_length=500)
    success: bool = Field(default=True)
    error_msg: Optional[str] = Field(default=None, max_length=500)
    elapsed_ms: Optional[int] = Field(default=None)
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime, nullable=False, index=True),
    )


class AuditAccessLog(_AuditBase, table=True):
    """访问日志：NL2SQL 与学情查询接口。"""

    __tablename__ = "audit_access_log"
    __table_args__ = (
        Index("idx_audit_access_log_user_id", "user_id"),
        Index("idx_audit_access_log_created_at", "created_at"),
    )

    id: Optional[int] = Field(
        default=None, sa_column=Column(BigInteger, primary_key=True, autoincrement=True)
    )
    request_method: str = Field(max_length=16)
    request_path: str = Field(max_length=255)
    datasource_id: Optional[int] = Field(default=None, sa_column=Column(BigInteger))
    query_text: Optional[str] = Field(default=None, max_length=500)


class AuditOperationLog(_AuditBase, table=True):
    """操作日志：增删改写接口。"""

    __tablename__ = "audit_operation_log"
    __table_args__ = (
        Index("idx_audit_operation_log_user_id", "user_id"),
        Index("idx_audit_operation_log_created_at", "created_at"),
    )

    id: Optional[int] = Field(
        default=None, sa_column=Column(BigInteger, primary_key=True, autoincrement=True)
    )
    operation_type: str = Field(max_length=32)
    resource_type: str = Field(max_length=64)
    resource_id: Optional[str] = Field(default=None, max_length=128)
    detail: Optional[str] = Field(default=None, max_length=2000)


class AuditLoginLog(_AuditBase, table=True):
    """登录日志：登录成功 / 失败。"""

    __tablename__ = "audit_login_log"
    __table_args__ = (
        Index("idx_audit_login_log_account", "account"),
        Index("idx_audit_login_log_user_id", "user_id"),
        Index("idx_audit_login_log_created_at", "created_at"),
    )

    id: Optional[int] = Field(
        default=None, sa_column=Column(BigInteger, primary_key=True, autoincrement=True)
    )
    account: str = Field(max_length=255, nullable=False)
    fail_reason: Optional[str] = Field(default=None, max_length=255)
```

- [ ] **Step 3: 写 models/__init__.py 导出**

`src/audit/models/__init__.py`:
```python
from audit.models.audit import AuditAccessLog, AuditLoginLog, AuditOperationLog

__all__ = ["AuditAccessLog", "AuditOperationLog", "AuditLoginLog"]
```

- [ ] **Step 4: 写 conftest.py（测试用 session）**

`tests/audit/conftest.py`:
```python
"""audit 测试公共 fixture。"""
from datetime import datetime
from typing import Generator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

from audit.models import AuditAccessLog, AuditLoginLog, AuditOperationLog  # noqa: F401
from system.models import SysUser  # noqa: F401
from system.models.workspace import SysUserWorkspace, SysWorkspace  # noqa: F401


@pytest.fixture(name="engine")
def _engine():
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture(name="session")
def _session(engine) -> Generator[Session, None, None]:
    with Session(engine) as s:
        yield s


@pytest.fixture(name="admin_user")
def _admin_user(session: Session):
    """内置超级管理员（id=1, account=admin）。"""
    user = SysUser(
        id=1,
        account="admin",
        name="管理员",
        password="x",
        oid=1,
        status=1,
        create_time=int(datetime.utcnow().timestamp() * 1000),
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


@pytest.fixture(name="normal_user")
def _normal_user(session: Session):
    """普通用户（id=2, account=allen）。"""
    user = SysUser(
        id=2,
        account="allen",
        name="艾伦",
        password="x",
        oid=1,
        status=1,
        create_time=int(datetime.utcnow().timestamp() * 1000),
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user
```

> **注意**：`from system.models import SysUser` 依赖 `src/system/models/__init__.py` 已导出 `SysUser`（现状已导出）。`SysUser` 的必填字段以 `src/system/models/user.py` 实际定义为准（account/name/password/oid/status/create_time），若 conftest 创建报 NOT NULL，按实际 model 补字段。

- [ ] **Step 5: 写 test_models.py**

`tests/audit/test_models.py`:
```python
"""审计日志模型字段与建表。"""
from audit.models import AuditAccessLog, AuditLoginLog, AuditOperationLog


def test_access_log_table_name():
    assert AuditAccessLog.__tablename__ == "audit_access_log"


def test_operation_log_table_name():
    assert AuditOperationLog.__tablename__ == "audit_operation_log"


def test_login_log_table_name():
    assert AuditLoginLog.__tablename__ == "audit_login_log"


def test_access_log_fields():
    log = AuditAccessLog(
        request_method="POST",
        request_path="/api/v1/chat/generate-sql",
        datasource_id=1,
        query_text="本月订单最多前三名",
        user_id=2,
        user_account="allen",
        success=True,
        elapsed_ms=120,
    )
    assert log.request_method == "POST"
    assert log.query_text == "本月订单最多前三名"
    assert log.success is True


def test_operation_log_fields():
    log = AuditOperationLog(
        operation_type="create",
        resource_type="user",
        resource_id="3",
        detail='{"account":"bob"}',
        user_id=1,
        user_account="admin",
        success=True,
    )
    assert log.operation_type == "create"
    assert log.resource_type == "user"


def test_login_log_fields():
    log = AuditLoginLog(
        account="allen",
        success=False,
        fail_reason="密码错误",
        user_account="allen",
    )
    assert log.account == "allen"
    assert log.fail_reason == "密码错误"
    assert log.success is False
```

- [ ] **Step 6: 运行测试验证通过**

Run: `uv run pytest tests/audit/test_models.py -v`
Expected: PASS（6 个测试全过）。

> 若报 `No module named 'audit'`，确认 `pyproject.toml` 的 `[tool.pytest.ini_options] pythonpath=["."]` 已含仓库根（现状已有，见 CLAUDE.md），且 `src/audit/__init__.py` 已创建。

- [ ] **Step 7: 修改 alembic/env.py 注册新 model**

读取 `alembic/env.py`，在现有 model import 区追加一行（与现有 `from system.models import ...` 同区）：
```python
from audit.models import AuditAccessLog, AuditLoginLog, AuditOperationLog  # noqa: F401
```
确保新 model 注册到 `SQLModel.metadata`，以便 autogenerate 识别。

- [ ] **Step 8: 生成 Alembic 迁移**

Run: `uv run alembic revision --autogenerate -m "add audit log tables"`
Expected: 生成 `alembic/versions/<rev>_add_audit_log_tables.py`，含 `op.create_table("audit_access_log", ...)` 等三张表。

- [ ] **Step 9: 核对迁移内容**

打开生成的迁移文件，核对：
- 三张表 `audit_access_log` / `audit_operation_log` / `audit_login_log` 字段与 model 一致。
- 索引：三表 `created_at` + `user_id`，登录日志额外 `account`。**若 autogenerate 漏掉索引，手动在 `upgrade()` 补 `op.create_index(...)`、在 `downgrade()` 补 `op.drop_index(...)`。**

- [ ] **Step 10: 应用迁移验证**

Run: `uv run alembic upgrade head`
Expected: 无报错。

- [ ] **Step 11: ruff 检查**

Run: `uv run ruff check src/audit tests/audit alembic/env.py`
Expected: 无错误。

- [ ] **Step 12: 提交**

```bash
git add src/audit tests/audit alembic/env.py alembic/versions
git commit -m "feat(audit): 新增审计日志数据模型与迁移"
```

---

## Task 2: fire-and-forget 异步写入器

**Files:**
- Create: `src/audit/service/writer.py`
- Test: `tests/audit/test_service_writer.py`

- [ ] **Step 1: 写 test_service_writer.py（失败测试）**

`tests/audit/test_service_writer.py`:
```python
"""审计日志异步写入器：截断 + fire-and-forget 不抛错。"""
import asyncio
from datetime import datetime

import pytest
from sqlmodel import Session, select

from audit.models import AuditAccessLog, AuditLoginLog, AuditOperationLog
from audit.service.writer import log_access, log_login, log_operation


def _wait_bg():
    """排空后台 to_thread 任务。"""
    async def _drain():
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
    asyncio.run(_drain())


def test_log_access_writes_row(session):
    log_access(
        session_factory=lambda: session,
        request_method="POST",
        request_path="/api/v1/chat/generate-sql",
        datasource_id=1,
        query_text="本月订单最多前三名",
        user_id=2,
        user_account="allen",
        success=True,
        elapsed_ms=120,
    )
    _wait_bg()
    rows = session.exec(select(AuditAccessLog)).all()
    assert len(rows) == 1
    assert rows[0].query_text == "本月订单最多前三名"
    assert rows[0].success is True


def test_log_access_truncates_query_text(session):
    long_text = "x" * 1000
    log_access(
        session_factory=lambda: session,
        request_method="POST",
        request_path="/api/v1/chat/generate-sql",
        query_text=long_text,
        user_id=2,
        success=True,
    )
    _wait_bg()
    rows = session.exec(select(AuditAccessLog)).all()
    assert len(rows[0].query_text) == 500


def test_log_operation_writes_row(session):
    log_operation(
        session_factory=lambda: session,
        operation_type="create",
        resource_type="user",
        resource_id="3",
        detail='{"account":"bob"}',
        user_id=1,
        user_account="admin",
        success=True,
    )
    _wait_bg()
    rows = session.exec(select(AuditOperationLog)).all()
    assert len(rows) == 1
    assert rows[0].operation_type == "create"
    assert rows[0].resource_type == "user"


def test_log_login_writes_success_and_fail(session):
    log_login(session_factory=lambda: session, account="allen", success=True, user_id=2, user_account="allen")
    log_login(
        session_factory=lambda: session,
        account="allen",
        success=False,
        fail_reason="密码错误",
        user_account="allen",
    )
    _wait_bg()
    rows = session.exec(select(AuditLoginLog)).all()
    assert len(rows) == 2
    assert rows[0].success is False  # DESC by created_at -> 最新失败在前
    assert rows[0].fail_reason == "密码错误"


def test_log_access_does_not_raise_on_db_error(session, monkeypatch):
    """写库失败时不应抛出，保证主业务不受影响。"""
    def _boom(self, *a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(Session, "add", _boom)
    # 不应抛异常
    log_access(
        session_factory=lambda: session,
        request_method="POST",
        request_path="/x",
        query_text="q",
        user_id=2,
        success=True,
    )
    _wait_bg()
```

- [ ] **Step 2: 运行测试验证失败**

Run: `uv run pytest tests/audit/test_service_writer.py -v`
Expected: FAIL（`ImportError: cannot import name 'log_access'`）。

- [ ] **Step 3: 写 service/writer.py**

`src/audit/service/writer.py`:
```python
"""审计日志 fire-and-forget 异步写入器。

所有写入走 asyncio.create_task + asyncio.to_thread 后台执行，
写库失败只 logger.warning，绝不抛出、不影响主业务。
每个写入使用独立 session（由 session_factory 创建），用完即关。
"""
import asyncio
import logging
from datetime import datetime
from typing import Callable, Optional

from sqlmodel import Session

from audit.models import AuditAccessLog, AuditLoginLog, AuditOperationLog
from common.core.trace import get_trace_id

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], Session]

_QUERY_TEXT_LIMIT = 500
_DETAIL_LIMIT = 2000
_ERROR_MSG_LIMIT = 500
_USER_AGENT_LIMIT = 500


def _truncate(text: Optional[str], limit: int) -> Optional[str]:
    if text is None:
        return None
    return text[:limit]


def _sync_write_access(factory: SessionFactory, **fields) -> None:
    try:
        with factory() as s:
            s.add(AuditAccessLog(**fields))
            s.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning("write access log failed: %s", e)


def _sync_write_operation(factory: SessionFactory, **fields) -> None:
    try:
        with factory() as s:
            s.add(AuditOperationLog(**fields))
            s.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning("write operation log failed: %s", e)


def _sync_write_login(factory: SessionFactory, **fields) -> None:
    try:
        with factory() as s:
            s.add(AuditLoginLog(**fields))
            s.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning("write login log failed: %s", e)


def _fire_and_forget(sync_fn, factory: SessionFactory, **fields) -> None:
    """提交后台写入任务；无运行中事件循环时降级为同步写（如测试/同步端点）。"""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(asyncio.to_thread(sync_fn, factory, **fields))
    except RuntimeError:
        # 无运行中事件循环（同步上下文），直接同步写
        sync_fn(factory, **fields)


def log_access(
    *,
    session_factory: SessionFactory,
    request_method: str,
    request_path: str,
    datasource_id: Optional[int] = None,
    query_text: Optional[str] = None,
    user_id: Optional[int] = None,
    user_account: Optional[str] = None,
    workspace_oid: Optional[int] = None,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
    success: bool = True,
    error_msg: Optional[str] = None,
    elapsed_ms: Optional[int] = None,
) -> None:
    fields = dict(
        trace_id=get_trace_id() or "-",
        user_id=user_id,
        user_account=user_account,
        workspace_oid=workspace_oid,
        ip=ip,
        user_agent=_truncate(user_agent, _USER_AGENT_LIMIT),
        success=success,
        error_msg=_truncate(error_msg, _ERROR_MSG_LIMIT),
        elapsed_ms=elapsed_ms,
        created_at=datetime.utcnow(),
        request_method=request_method,
        request_path=request_path,
        datasource_id=datasource_id,
        query_text=_truncate(query_text, _QUERY_TEXT_LIMIT),
    )
    _fire_and_forget(_sync_write_access, session_factory, **fields)


def log_operation(
    *,
    session_factory: SessionFactory,
    operation_type: str,
    resource_type: str,
    resource_id: Optional[str] = None,
    detail: Optional[str] = None,
    user_id: Optional[int] = None,
    user_account: Optional[str] = None,
    workspace_oid: Optional[int] = None,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
    success: bool = True,
    error_msg: Optional[str] = None,
    elapsed_ms: Optional[int] = None,
) -> None:
    fields = dict(
        trace_id=get_trace_id() or "-",
        user_id=user_id,
        user_account=user_account,
        workspace_oid=workspace_oid,
        ip=ip,
        user_agent=_truncate(user_agent, _USER_AGENT_LIMIT),
        success=success,
        error_msg=_truncate(error_msg, _ERROR_MSG_LIMIT),
        elapsed_ms=elapsed_ms,
        created_at=datetime.utcnow(),
        operation_type=operation_type,
        resource_type=resource_type,
        resource_id=resource_id,
        detail=_truncate(detail, _DETAIL_LIMIT),
    )
    _fire_and_forget(_sync_write_operation, session_factory, **fields)


def log_login(
    *,
    session_factory: SessionFactory,
    account: str,
    success: bool,
    user_id: Optional[int] = None,
    user_account: Optional[str] = None,
    workspace_oid: Optional[int] = None,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
    fail_reason: Optional[str] = None,
) -> None:
    fields = dict(
        trace_id=get_trace_id() or "-",
        user_id=user_id,
        user_account=user_account or account,
        workspace_oid=workspace_oid,
        ip=ip,
        user_agent=_truncate(user_agent, _USER_AGENT_LIMIT),
        success=success,
        error_msg=fail_reason,
        elapsed_ms=None,
        created_at=datetime.utcnow(),
        account=account,
        fail_reason=fail_reason,
    )
    _fire_and_forget(_sync_write_login, session_factory, **fields)
```

> **session_factory 设计**：写入器不直接持有 session，而是接收一个返回新 session 的工厂。生产中传 `SessionLocal`（`common.core.database` 导出），测试中传 `lambda: session`（用同一内存 sqlite session，因 StaticPool 共享连接）。`common.core.database` 现有 `engine`/`SessionLocal`，若导出名不同，按实际调整（见 Task 3 Step 1 确认）。

- [ ] **Step 4: 运行测试验证通过**

Run: `uv run pytest tests/audit/test_service_writer.py -v`
Expected: PASS（5 个测试全过，含"写库失败不抛错"）。

> `test_log_login_writes_success_and_fail` 断言 `rows[0].success is False`（DESC）。若 sqlite 默认顺序非 DESC，调整为按 `account` 过滤后断言两条的 success 集合为 `{True, False}`，不依赖顺序：
> ```python
> assert {r.success for r in rows} == {True, False}
> ```
> 采用这个不依赖顺序的断言更稳妥，**请将 test 中该断言改为此形式**。

- [ ] **Step 5: ruff 检查**

Run: `uv run ruff check src/audit/service/writer.py tests/audit/test_service_writer.py`
Expected: 无错误。

- [ ] **Step 6: 提交**

```bash
git add src/audit/service/writer.py tests/audit/test_service_writer.py
git commit -m "feat(audit): 新增 fire-and-forget 异步写入器"
```

---

## Task 3: crud 分页查询 + 权限隔离

**Files:**
- Create: `src/audit/crud/audit.py`
- Test: `tests/audit/test_api_query.py`（本任务先写 crud 层测试部分）

- [ ] **Step 1: 确认 SessionLocal 导出名**

Run: `uv run python -c "import common.core.database as d; print([n for n in dir(d) if 'ession' in n or 'ngine' in n])"`
Expected: 输出含 `SessionLocal` 与 `engine`（或 `get_session`）。记录实际导出名，供 writer 与 api 使用。若导出为 `SessionLocal`，后续照用；若为别的名，全局替换。

- [ ] **Step 2: 写 crud/audit.py（含权限隔离）**

`src/audit/crud/audit.py`:
```python
"""审计日志分页查询，含权限隔离：超管看全部，普通用户仅看自己。"""
from datetime import datetime
from typing import Optional

from sqlmodel import Session, func, select

from audit.models import AuditAccessLog, AuditLoginLog, AuditOperationLog
from system.authz import is_platform_admin
from system.models import SysUser


def _apply_time_range(stmt, start_time: Optional[int], end_time: Optional[int]):
    if start_time is not None:
        # start_time/end_time 为毫秒时间戳，created_at 为 DateTime
        stmt = stmt.where(AuditAccessLog.created_at >= datetime.fromtimestamp(start_time / 1000))
    if end_time is not None:
        stmt = stmt.where(AuditAccessLog.created_at <= datetime.fromtimestamp(end_time / 1000))
    return stmt


def pager_access_log(
    session: Session,
    current_user: SysUser,
    page_num: int,
    page_size: int,
    user_id: Optional[int] = None,
    datasource_id: Optional[int] = None,
    success: Optional[bool] = None,
    start_time: Optional[int] = None,
    end_time: Optional[int] = None,
):
    stmt = select(AuditAccessLog)
    if not is_platform_admin(current_user):
        stmt = stmt.where(AuditAccessLog.user_id == current_user.id)
    else:
        if user_id is not None:
            stmt = stmt.where(AuditAccessLog.user_id == user_id)
    if datasource_id is not None:
        stmt = stmt.where(AuditAccessLog.datasource_id == datasource_id)
    if success is not None:
        stmt = stmt.where(AuditAccessLog.success == success)
    if start_time is not None:
        stmt = stmt.where(AuditAccessLog.created_at >= datetime.fromtimestamp(start_time / 1000))
    if end_time is not None:
        stmt = stmt.where(AuditAccessLog.created_at <= datetime.fromtimestamp(end_time / 1000))
    total = session.exec(select(func.count()).select_from(stmt.subquery())).one()
    rows = session.exec(
        stmt.order_by(AuditAccessLog.created_at.desc())
        .offset((page_num - 1) * page_size)
        .limit(page_size)
    ).all()
    return total, rows


def pager_operation_log(
    session: Session,
    current_user: SysUser,
    page_num: int,
    page_size: int,
    user_id: Optional[int] = None,
    resource_type: Optional[str] = None,
    operation_type: Optional[str] = None,
    success: Optional[bool] = None,
    start_time: Optional[int] = None,
    end_time: Optional[int] = None,
):
    stmt = select(AuditOperationLog)
    if not is_platform_admin(current_user):
        stmt = stmt.where(AuditOperationLog.user_id == current_user.id)
    else:
        if user_id is not None:
            stmt = stmt.where(AuditOperationLog.user_id == user_id)
    if resource_type is not None:
        stmt = stmt.where(AuditOperationLog.resource_type == resource_type)
    if operation_type is not None:
        stmt = stmt.where(AuditOperationLog.operation_type == operation_type)
    if success is not None:
        stmt = stmt.where(AuditOperationLog.success == success)
    if start_time is not None:
        stmt = stmt.where(AuditOperationLog.created_at >= datetime.fromtimestamp(start_time / 1000))
    if end_time is not None:
        stmt = stmt.where(AuditOperationLog.created_at <= datetime.fromtimestamp(end_time / 1000))
    total = session.exec(select(func.count()).select_from(stmt.subquery())).one()
    rows = session.exec(
        stmt.order_by(AuditOperationLog.created_at.desc())
        .offset((page_num - 1) * page_size)
        .limit(page_size)
    ).all()
    return total, rows


def pager_login_log(
    session: Session,
    current_user: SysUser,
    page_num: int,
    page_size: int,
    account: Optional[str] = None,
    success: Optional[bool] = None,
    start_time: Optional[int] = None,
    end_time: Optional[int] = None,
):
    stmt = select(AuditLoginLog)
    if not is_platform_admin(current_user):
        # 普通用户按 account 匹配（含登录失败记录，失败时 user_id 为空）
        stmt = stmt.where(AuditLoginLog.account == current_user.account)
    else:
        if account is not None:
            stmt = stmt.where(AuditLoginLog.account == account)
    if success is not None:
        stmt = stmt.where(AuditLoginLog.success == success)
    if start_time is not None:
        stmt = stmt.where(AuditLoginLog.created_at >= datetime.fromtimestamp(start_time / 1000))
    if end_time is not None:
        stmt = stmt.where(AuditLoginLog.created_at <= datetime.fromtimestamp(end_time / 1000))
    total = session.exec(select(func.count()).select_from(stmt.subquery())).one()
    rows = session.exec(
        stmt.order_by(AuditLoginLog.created_at.desc())
        .offset((page_num - 1) * page_size)
        .limit(page_size)
    ).all()
    return total, rows
```

> `_apply_time_range` 辅助函数未被使用（各 pager 内联了时间过滤以避免 model 类引用混乱），可删除该函数避免 ruff 未使用告警——**请在最终代码中删除 `_apply_time_range`**，三处时间过滤保持内联。

- [ ] **Step 3: 写 crud 权限隔离测试**

`tests/audit/test_api_query.py`（先写 crud 层测试，api 层测试在 Task 4 补）:
```python
"""审计日志 crud 权限隔离与分页。"""
from datetime import datetime

from audit.crud.audit import pager_access_log, pager_login_log, pager_operation_log
from audit.models import AuditAccessLog, AuditLoginLog, AuditOperationLog


def _seed_access(session, user_id, account):
    session.add(AuditAccessLog(
        request_method="POST", request_path="/x", query_text="q",
        user_id=user_id, user_account=account, success=True, created_at=datetime.utcnow(),
    ))
    session.commit()


def _seed_operation(session, user_id, account):
    session.add(AuditOperationLog(
        operation_type="create", resource_type="user", resource_id="1",
        user_id=user_id, user_account=account, success=True, created_at=datetime.utcnow(),
    ))
    session.commit()


def _seed_login(session, account, success, user_id=None):
    session.add(AuditLoginLog(
        account=account, success=success, user_account=account, user_id=user_id,
        created_at=datetime.utcnow(),
    ))
    session.commit()


def test_admin_sees_all_access_logs(session, admin_user, normal_user):
    _seed_access(session, 1, "admin")
    _seed_access(session, 2, "allen")
    total, rows = pager_access_log(session, admin_user, 1, 10)
    assert total == 2
    assert {r.user_account for r in rows} == {"admin", "allen"}


def test_normal_user_sees_only_own_access_logs(session, admin_user, normal_user):
    _seed_access(session, 1, "admin")
    _seed_access(session, 2, "allen")
    total, rows = pager_access_log(session, normal_user, 1, 10)
    assert total == 1
    assert rows[0].user_account == "allen"


def test_admin_sees_all_operation_logs(session, admin_user, normal_user):
    _seed_operation(session, 1, "admin")
    _seed_operation(session, 2, "allen")
    total, rows = pager_operation_log(session, admin_user, 1, 10)
    assert total == 2


def test_normal_user_sees_only_own_operation_logs(session, admin_user, normal_user):
    _seed_operation(session, 1, "admin")
    _seed_operation(session, 2, "allen")
    total, rows = pager_operation_log(session, normal_user, 1, 10)
    assert total == 1
    assert rows[0].user_account == "allen"


def test_admin_sees_all_login_logs_including_failures(session, admin_user):
    _seed_login(session, "allen", False, user_id=None)
    _seed_login(session, "admin", True, user_id=1)
    total, rows = pager_login_log(session, admin_user, 1, 10)
    assert total == 2


def test_normal_user_sees_only_own_login_logs_by_account(session, admin_user, normal_user):
    # allen 的失败登录（user_id 为空）+ admin 的登录
    _seed_login(session, "allen", False, user_id=None)
    _seed_login(session, "admin", True, user_id=1)
    total, rows = pager_login_log(session, normal_user, 1, 10)
    assert total == 1
    assert rows[0].account == "allen"
    assert rows[0].success is False


def test_access_log_pagination(session, admin_user):
    for i in range(5):
        _seed_access(session, 1, "admin")
    total, page1 = pager_access_log(session, admin_user, 1, 2)
    total2, page2 = pager_access_log(session, admin_user, 2, 2)
    assert total == 5
    assert len(page1) == 2
    assert len(page2) == 2
    # 不重复
    assert {r.id for r in page1}.isdisjoint({r.id for r in page2})


def test_access_log_filter_by_datasource(session, admin_user):
    session.add(AuditAccessLog(
        request_method="POST", request_path="/x", datasource_id=1, query_text="a",
        user_id=1, user_account="admin", success=True, created_at=datetime.utcnow(),
    ))
    session.add(AuditAccessLog(
        request_method="POST", request_path="/x", datasource_id=2, query_text="b",
        user_id=1, user_account="admin", success=True, created_at=datetime.utcnow(),
    ))
    session.commit()
    total, rows = pager_access_log(session, admin_user, 1, 10, datasource_id=1)
    assert total == 1
    assert rows[0].datasource_id == 1
```

- [ ] **Step 4: 运行测试验证通过**

Run: `uv run pytest tests/audit/test_api_query.py -v`
Expected: PASS（8 个测试全过，含普通用户权限隔离核心用例）。

- [ ] **Step 5: ruff 检查**

Run: `uv run ruff check src/audit/crud/audit.py tests/audit/test_api_query.py`
Expected: 无错误（确认已删除未使用的 `_apply_time_range`）。

- [ ] **Step 6: 提交**

```bash
git add src/audit/crud/audit.py tests/audit/test_api_query.py
git commit -m "feat(audit): 新增分页查询 crud 含权限隔离"
```

---

## Task 4: audit API router + 注册

**Files:**
- Create: `src/audit/api/audit.py`
- Modify: `src/common/router.py`
- Modify: `tests/audit/test_api_query.py`（追加 api 层测试）

- [ ] **Step 1: 写 api/audit.py**

`src/audit/api/audit.py`:
```python
"""审计日志查询 API：3 个分页端点，权限隔离在 crud 层。"""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session

from audit.crud.audit import pager_access_log, pager_login_log, pager_operation_log
from common.core.database import get_session
from common.schemas.response import success_response
from system.api.system import get_current_user
from system.models import SysUser

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("/access/pager/{page_num}/{page_size}")
def list_access_log(
    page_num: int,
    page_size: int,
    user_id: Optional[int] = Query(default=None),
    datasource_id: Optional[int] = Query(default=None),
    success: Optional[bool] = Query(default=None),
    start_time: Optional[int] = Query(default=None),
    end_time: Optional[int] = Query(default=None),
    session: Session = Depends(get_session),
    current_user: SysUser = Depends(get_current_user),
):
    total, items = pager_access_log(
        session, current_user, page_num, page_size,
        user_id=user_id, datasource_id=datasource_id, success=success,
        start_time=start_time, end_time=end_time,
    )
    return success_response(data={"total": total, "items": [_access_to_dict(i) for i in items]})


@router.get("/operation/pager/{page_num}/{page_size}")
def list_operation_log(
    page_num: int,
    page_size: int,
    user_id: Optional[int] = Query(default=None),
    resource_type: Optional[str] = Query(default=None),
    operation_type: Optional[str] = Query(default=None),
    success: Optional[bool] = Query(default=None),
    start_time: Optional[int] = Query(default=None),
    end_time: Optional[int] = Query(default=None),
    session: Session = Depends(get_session),
    current_user: SysUser = Depends(get_current_user),
):
    total, items = pager_operation_log(
        session, current_user, page_num, page_size,
        user_id=user_id, resource_type=resource_type, operation_type=operation_type,
        success=success, start_time=start_time, end_time=end_time,
    )
    return success_response(data={"total": total, "items": [_operation_to_dict(i) for i in items]})


@router.get("/login/pager/{page_num}/{page_size}")
def list_login_log(
    page_num: int,
    page_size: int,
    account: Optional[str] = Query(default=None),
    success: Optional[bool] = Query(default=None),
    start_time: Optional[int] = Query(default=None),
    end_time: Optional[int] = Query(default=None),
    session: Session = Depends(get_session),
    current_user: SysUser = Depends(get_current_user),
):
    total, items = pager_login_log(
        session, current_user, page_num, page_size,
        account=account, success=success, start_time=start_time, end_time=end_time,
    )
    return success_response(data={"total": total, "items": [_login_to_dict(i) for i in items]})


def _access_to_dict(log) -> dict:
    return {
        "id": log.id,
        "trace_id": log.trace_id,
        "user_id": log.user_id,
        "user_account": log.user_account,
        "workspace_oid": log.workspace_oid,
        "ip": log.ip,
        "user_agent": log.user_agent,
        "success": log.success,
        "error_msg": log.error_msg,
        "elapsed_ms": log.elapsed_ms,
        "created_at": log.created_at.isoformat() if log.created_at else None,
        "request_method": log.request_method,
        "request_path": log.request_path,
        "datasource_id": log.datasource_id,
        "query_text": log.query_text,
    }


def _operation_to_dict(log) -> dict:
    return {
        "id": log.id,
        "trace_id": log.trace_id,
        "user_id": log.user_id,
        "user_account": log.user_account,
        "workspace_oid": log.workspace_oid,
        "ip": log.ip,
        "user_agent": log.user_agent,
        "success": log.success,
        "error_msg": log.error_msg,
        "elapsed_ms": log.elapsed_ms,
        "created_at": log.created_at.isoformat() if log.created_at else None,
        "operation_type": log.operation_type,
        "resource_type": log.resource_type,
        "resource_id": log.resource_id,
        "detail": log.detail,
    }


def _login_to_dict(log) -> dict:
    return {
        "id": log.id,
        "trace_id": log.trace_id,
        "user_id": log.user_id,
        "user_account": log.user_account,
        "workspace_oid": log.workspace_oid,
        "ip": log.ip,
        "user_agent": log.user_agent,
        "success": log.success,
        "error_msg": log.error_msg,
        "created_at": log.created_at.isoformat() if log.created_at else None,
        "account": log.account,
        "fail_reason": log.fail_reason,
    }
```

> **import 注意**：`from system.api.system import get_current_user` 可能产生循环 import（`system.py` 若 import audit）。Task 5 修改 `system.py` 时若出现循环，则把 `get_current_user` 抽到 `system/api/auth_deps.py` 或改用局部 import。**先按现状跑，循环 import 出现再处理**——优先保持 `get_current_user` 在 `system.py`，audit import 它；若 Task 5 在 system.py 内 import audit.service 导致循环，则在 system.py 内用函数内局部 import `from audit.service.writer import log_login`。

- [ ] **Step 2: 注册 router**

修改 `src/common/router.py`：在顶部 import 区加 `from audit.api.audit import router as audit_router`，并在 `get_all_routers()` 返回列表中加入 `audit_router`。具体位置见文件现状（`router.py:3-11` import，`router.py:14-26` 列表）。

示例（按现有结构追加）：
```python
from audit.api.audit import router as audit_router
# ... 其他 import
```
```python
def get_all_routers():
    return [
        # ... 现有 router
        audit_router,
    ]
```

- [ ] **Step 3: 追加 api 层测试**

在 `tests/audit/test_api_query.py` 末尾追加 api 端点测试。需要构造 FastAPI TestClient 并覆盖 `get_session` 依赖。追加：
```python
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from audit.api.audit import router as audit_router
from common.core.database import get_session


def _build_app(session, current_user):
    app = FastAPI()
    app.include_router(audit_router, prefix="/api/v1")
    app.dependency_overrides[get_session] = lambda: session
    from system.api.system import get_current_user
    app.dependency_overrides[get_current_user] = lambda: current_user
    return app


def test_api_access_log_admin(session, admin_user):
    _seed_access(session, 1, "admin")
    _seed_access(session, 2, "allen")
    app = _build_app(session, admin_user)
    resp = TestClient(app).get("/api/v1/audit/access/pager/1/10")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 200
    assert body["data"]["total"] == 2


def test_api_access_log_normal_user_isolated(session, admin_user, normal_user):
    _seed_access(session, 1, "admin")
    _seed_access(session, 2, "allen")
    app = _build_app(session, normal_user)
    resp = TestClient(app).get("/api/v1/audit/access/pager/1/10")
    body = resp.json()
    assert body["data"]["total"] == 1
    assert body["data"]["items"][0]["user_account"] == "allen"


def test_api_login_log_normal_user_by_account(session, admin_user, normal_user):
    _seed_login(session, "allen", False, user_id=None)
    _seed_login(session, "admin", True, user_id=1)
    app = _build_app(session, normal_user)
    resp = TestClient(app).get("/api/v1/audit/login/pager/1/10")
    body = resp.json()
    assert body["data"]["total"] == 1
    assert body["data"]["items"][0]["account"] == "allen"
```

- [ ] **Step 4: 运行测试验证通过**

Run: `uv run pytest tests/audit/test_api_query.py -v`
Expected: PASS（crud 8 个 + api 3 个 = 11 个测试全过）。

> 若 TestClient 因 `success_response` 返回结构或 `code` 字段名不符而断言失败，用 `print(resp.json())` 对照实际 `success_response` 信封调整断言键名（设计文档确认信封为 `{code, message, data}`）。

- [ ] **Step 5: ruff 检查**

Run: `uv run ruff check src/audit/api/audit.py src/common/router.py tests/audit/test_api_query.py`
Expected: 无错误。

- [ ] **Step 6: 提交**

```bash
git add src/audit/api/audit.py src/common/router.py tests/audit/test_api_query.py
git commit -m "feat(audit): 新增日志查询 API 并注册路由"
```

---

## Task 5: 登录日志采集 + /system/me 返回 is_platform_admin

**Files:**
- Modify: `src/system/schemas.py`
- Modify: `src/system/api/system.py`
- Test: `tests/audit/test_login_log.py`

- [ ] **Step 1: 写 test_login_log.py（失败测试）**

`tests/audit/test_login_log.py`:
```python
"""登录日志采集：成功 / 失败各记一条。"""
from datetime import datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from audit.models import AuditLoginLog
from audit.service.writer import log_login  # noqa: F401  确保可 import
from common.core.database import get_session
from system.api.system import router as system_router


def _build_app(session):
    app = FastAPI()
    app.include_router(system_router, prefix="/api/v1")
    app.dependency_overrides[get_session] = lambda: session
    return app


def test_login_success_logs(session, monkeypatch):
    # 用真实 register 造一个用户，再调 login
    app = _build_app(session)
    client = TestClient(app)
    client.post("/api/v1/system/register", json={"account": "allen", "name": "艾伦", "password": "123456"})
    # login 是 OAuth2 form
    resp = client.post(
        "/api/v1/system/login",
        data={"username": "allen", "password": "123456"},
    )
    assert resp.status_code == 200
    # 排空后台任务后查登录日志
    import asyncio
    asyncio.run(asyncio.sleep(0))  # 让出控制权
    rows = session.exec(select(AuditLoginLog)).all()
    success_rows = [r for r in rows if r.account == "allen" and r.success]
    assert len(success_rows) >= 1


def test_login_wrong_password_logs_failure(session):
    app = _build_app(session)
    client = TestClient(app)
    client.post("/api/v1/system/register", json={"account": "allen", "name": "艾伦", "password": "123456"})
    resp = client.post(
        "/api/v1/system/login",
        data={"username": "allen", "password": "wrong"},
    )
    # 业务码非 200（密码错误），但 HTTP 200（统一信封）
    rows = session.exec(select(AuditLoginLog)).all()
    fail_rows = [r for r in rows if r.account == "allen" and not r.success]
    assert len(fail_rows) >= 1
    assert fail_rows[0].fail_reason is not None


def test_me_returns_is_platform_admin(session, admin_user):
    app = _build_app(session)
    client = TestClient(app)
    # 先登录拿 token
    # admin 密码未知，直接用 dependency override 注入 current_user
    from system.api.system import get_current_user
    app.dependency_overrides[get_current_user] = lambda: admin_user
    resp = client.get("/api/v1/system/me")
    body = resp.json()
    assert body["data"]["is_platform_admin"] is True
```

> **测试策略**：登录成功测试依赖 register 造用户 + 真实 login 流程，验证日志被记录。失败测试验证密码错误也记日志。`is_platform_admin` 测试用 dependency override 注入 admin_user，避免依赖真实密码。后台写入排空用 `asyncio.run(asyncio.sleep(0))` 可能不足以排空 `to_thread`（TestClient 同步运行，无运行中 loop → writer 走同步降级分支直接写），因此**实际同步上下文下 writer 已同步写库**，断言应能直接查到。若查不到，确认 writer 的 `_fire_and_forget` 在无运行 loop 时走同步分支（Task 2 已实现）。

- [ ] **Step 2: 运行测试验证失败**

Run: `uv run pytest tests/audit/test_login_log.py -v`
Expected: FAIL（login 端点尚未记录日志；`/system/me` 未返回 `is_platform_admin`）。

- [ ] **Step 3: 修改 schemas.py 增加 is_platform_admin**

`src/system/schemas.py` 的 `UserResponse`（约 `:25`）增加字段：
```python
is_platform_admin: bool = False
```
加在现有字段末尾（`create_time` 之后），保持 `Config.from_attributes = True`。

- [ ] **Step 4: 修改 system.py 的 /system/me**

在 `src/system/api/system.py` 的 `/system/me` 端点（约 `:86`），构造响应时计算并注入 `is_platform_admin`。读取该端点现有实现，在返回前加：
```python
from system.authz import is_platform_admin
# ... 现有构造 response 字典处
response_dict["is_platform_admin"] = is_platform_admin(current_user)
```
若端点返回 `UserResponse` 对象（靠 `from_attributes`），则改为返回显式 dict 或赋值属性。**以实际代码为准**：若 me 端点返回 `success_response(data=UserResponse.model_validate(current_user))`，需改为 dict 形式或扩展。最稳妥：构造 dict 显式带 `is_platform_admin`。

- [ ] **Step 5: 修改 system.py 的 /system/login 记录登录日志**

在 `src/system/api/system.py` 的 `/system/login` 端点（约 `:70`），在成功分支与各失败分支记录登录日志。读取现有 login 实现（OAuth2PasswordRequestForm + `authenticate`），在以下位置插入：

顶部 import（函数内局部 import 避免循环）：
```python
from common.core.database import SessionLocal
```
在端点函数内，开头解析 IP/UA：
```python
from fastapi import Request
# 端点签名加 request: Request 参数
account = form_data.username
ip = _client_ip(request)
ua = request.headers.get("user-agent")
```
成功分支（authenticate 返回 user 后）：
```python
from audit.service.writer import log_login
log_login(
    session_factory=SessionLocal,
    account=account, success=True, user_id=user.id,
    user_account=user.account, ip=ip, user_agent=ua,
)
```
失败分支（密码错误，authenticate 返回 None）：
```python
log_login(
    session_factory=SessionLocal,
    account=account, success=False, fail_reason="密码错误",
    user_account=account, ip=ip, user_agent=ua,
)
```
账号不存在分支（get_user_by_account 返回 None）：
```python
log_login(
    session_factory=SessionLocal,
    account=account, success=False, fail_reason="账号不存在",
    user_account=account, ip=ip, user_agent=ua,
)
```
账号禁用分支（user.status != 1，若现有 login 有此判定）：
```python
log_login(
    session_factory=SessionLocal,
    account=account, success=False, fail_reason="账号已禁用",
    user_account=account, ip=ip, user_agent=ua,
)
```

在文件底部加 IP 解析辅助函数：
```python
def _client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    real = request.headers.get("x-real-ip")
    if real:
        return real.strip()
    return request.client.host if request.client else ""
```

> **关键**：login 端点签名要加 `request: Request` 参数。`SessionLocal` 导出名以 Task 3 Step 1 确认的实际名准。所有 `log_login` 调用都在对应分支的 return 之前。**务必每个失败分支都记**，否则失败登录追溯缺失。

- [ ] **Step 6: 运行测试验证通过**

Run: `uv run pytest tests/audit/test_login_log.py -v`
Expected: PASS（3 个测试全过）。

> 若 `test_login_success_logs` 因后台 `to_thread` 未排空而查不到，确认 TestClient 同步上下文下 writer 走同步降级（Task 2 `_fire_and_forget` 的 `RuntimeError` 分支）。同步降级会立即写，无需排空。若仍查不到，在测试中改用 `session.exec(select(AuditLoginLog).where(AuditLoginLog.account == "allen")).all()` 并 `session.expire_all()` 刷新。

- [ ] **Step 7: 回归测试**

Run: `uv run pytest tests/ -v --timeout=60`
Expected: 现有测试 + 新增 audit 测试全过，无回归。

- [ ] **Step 8: ruff 检查**

Run: `uv run ruff check src/system tests/audit/test_login_log.py`
Expected: 无错误。

- [ ] **Step 9: 提交**

```bash
git add src/system/schemas.py src/system/api/system.py tests/audit/test_login_log.py
git commit -m "feat(audit): 登录日志采集 + /system/me 返回 is_platform_admin"
```

---

## Task 6: 操作日志 / 访问日志采集装饰器

**Files:**
- Create: `src/audit/service/decorators.py`
- Test: `tests/audit/test_decorators.py`

- [ ] **Step 1: 写 test_decorators.py（失败测试）**

`tests/audit/test_decorators.py`:
```python
"""操作/访问日志装饰器：成功失败都记录，不阻塞原函数返回/异常。"""
from datetime import datetime

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from audit.models import AuditAccessLog, AuditOperationLog
from common.core.database import get_session
from system.api.system import get_current_user
from system.models import SysUser


def _build_app(session, current_user, with_failing_op=False):
    from audit.service.decorators import audit_access, audit_operation
    app = FastAPI()
    router = APIRouter()

    @router.post("/chat/generate-sql")
    @audit_access(datasource_id_arg="datasource_id", query_arg="question")
    def gen_sql(request: Request, datasource_id: int = 1, question: str = "q",
                session: Session = Depends(get_session),
                current_user: SysUser = Depends(get_current_user)):
        return {"ok": True}

    @router.post("/user")
    @audit_operation(operation_type="create", resource_type="user", resource_id_arg="user_id")
    def create_user(request: Request, body: dict = None,
                    session: Session = Depends(get_session),
                    current_user: SysUser = Depends(get_current_user)):
        return {"ok": True}

    if with_failing_op:
        @router.delete("/user/{uid}")
        @audit_operation(operation_type="delete", resource_type="user", resource_id_arg="uid")
        def delete_user(request: Request, uid: int,
                        session: Session = Depends(get_session),
                        current_user: SysUser = Depends(get_current_user)):
            raise ValueError("boom")

    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_current_user] = lambda: current_user
    return app


def test_access_decorator_logs_on_success(session, normal_user):
    app = _build_app(session, normal_user)
    resp = TestClient(app).post("/api/v1/chat/generate-sql?datasource_id=1&question=hello")
    assert resp.status_code == 200
    rows = session.exec(select(AuditAccessLog)).all()
    assert len(rows) == 1
    assert rows[0].query_text == "hello"
    assert rows[0].success is True
    assert rows[0].request_path == "/api/v1/chat/generate-sql"


def test_operation_decorator_logs_on_success(session, admin_user):
    app = _build_app(session, admin_user)
    resp = TestClient(app).post("/api/v1/user", json={"user_id": "5"})
    assert resp.status_code == 200
    rows = session.exec(select(AuditOperationLog)).all()
    assert len(rows) == 1
    assert rows[0].operation_type == "create"
    assert rows[0].resource_type == "user"
    assert rows[0].user_account == "admin"


def test_operation_decorator_logs_on_failure_and_propagates(session, admin_user):
    app = _build_app(session, admin_user, with_failing_op=True)
    resp = TestClient(app).delete("/api/v1/user/7")
    # 异常被 FastAPI 转 500，但日志仍记录 success=False
    rows = session.exec(select(AuditOperationLog).where(AuditOperationLog.operation_type == "delete")).all()
    assert len(rows) == 1
    assert rows[0].success is False
    assert rows[0].error_msg is not None
```

> 装饰器需从 FastAPI 已解析的参数中取 `request`、`datasource_id_arg`/`query_arg`/`resource_id_arg` 指定的参数值。装饰器用 `inspect.signature` 包装同步端点函数，从 kwargs 取这些参数。

- [ ] **Step 2: 运行测试验证失败**

Run: `uv run pytest tests/audit/test_decorators.py -v`
Expected: FAIL（`ImportError: cannot import name 'audit_access'`）。

- [ ] **Step 3: 写 service/decorators.py**

`src/audit/service/decorators.py`:
```python
"""操作/访问日志采集装饰器。

用法：
    @router.post("/user")
    @audit_operation(operation_type="create", resource_type="user", resource_id_arg="user_id")
    def create_user(request: Request, ..., current_user=Depends(get_current_user)): ...

装饰器从端点函数 kwargs 中取 request / current_user 及 *_arg 指定的业务参数，
调用 writer 记录日志，不改变原函数返回值与异常传播。
"""
import functools
import inspect
import logging
from typing import Callable, Optional

from fastapi import Request
from sqlmodel import Session

from audit.service.writer import log_access, log_operation
from common.core.database import SessionLocal

logger = logging.getLogger(__name__)


def _client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    real = request.headers.get("x-real-ip")
    if real:
        return real.strip()
    return request.client.host if request.client else ""


def _safe_get(kwargs: dict, name: Optional[str]):
    if not name:
        return None
    return kwargs.get(name)


def audit_access(
    *,
    datasource_id_arg: Optional[str] = None,
    query_arg: Optional[str] = None,
):
    def decorator(func: Callable):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            request: Optional[Request] = kwargs.get("request")
            current_user = kwargs.get("current_user")
            session: Optional[Session] = kwargs.get("session")
            factory = SessionLocal
            import time
            start = time.time()
            success = True
            error_msg = None
            try:
                result = func(*args, **kwargs)
                return result
            except Exception as e:
                success = False
                error_msg = str(e)
                raise
            finally:
                try:
                    log_access(
                        session_factory=factory,
                        request_method=request.method if request else "POST",
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

        return wrapper

    return decorator


def audit_operation(
    *,
    operation_type: str,
    resource_type: str,
    resource_id_arg: Optional[str] = None,
    detail_arg: Optional[str] = None,
):
    def decorator(func: Callable):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            request: Optional[Request] = kwargs.get("request")
            current_user = kwargs.get("current_user")
            factory = SessionLocal
            import time
            start = time.time()
            success = True
            error_msg = None
            try:
                result = func(*args, **kwargs)
                return result
            except Exception as e:
                success = False
                error_msg = str(e)
                raise
            finally:
                try:
                    detail = None
                    if detail_arg and detail_arg in kwargs:
                        import json
                        try:
                            detail = json.dumps(_jsonable(kwargs[detail_arg]), ensure_ascii=False)
                        except Exception:  # noqa: BLE001
                            detail = str(kwargs[detail_arg])
                    log_operation(
                        session_factory=factory,
                        operation_type=operation_type,
                        resource_type=resource_type,
                        resource_id=str(_safe_get(kwargs, resource_id_arg)) if _safe_get(kwargs, resource_id_arg) is not None else None,
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

        return wrapper

    return decorator


def _jsonable(obj):
    try:
        return obj.dict() if hasattr(obj, "dict") else obj
    except Exception:  # noqa: BLE001
        return str(obj)
```

> **关键设计**：装饰器包装同步函数，依赖 FastAPI 把 `request` / `current_user` / `session` 作为 kwargs 注入（端点签名必须含这些参数）。`SessionLocal` 为写入 session 工厂（Task 3 确认导出名）。装饰器在 `finally` 记录日志，确保成功失败都记；失败时 `raise` 透传原异常，不吞错。`import time` 放函数内避免顶部 unused（实际可放顶部，ruff 不报；保持顶部更清晰——**请将 `import time` 移到文件顶部 import 区**）。

- [ ] **Step 4: 运行测试验证通过**

Run: `uv run pytest tests/audit/test_decorators.py -v`
Expected: PASS（3 个测试全过）。

> 若装饰器取不到 `request`（kwargs 无 request），确认端点签名含 `request: Request` 参数——FastAPI 才会注入。测试中的示例端点已含。

- [ ] **Step 5: ruff 检查**

Run: `uv run ruff check src/audit/service/decorators.py tests/audit/test_decorators.py`
Expected: 无错误（`import time` 已移顶部，删除函数内重复 import）。

- [ ] **Step 6: 提交**

```bash
git add src/audit/service/decorators.py tests/audit/test_decorators.py
git commit -m "feat(audit): 新增操作/访问日志采集装饰器"
```

---

## Task 7: 给后端接口贴装饰器

**Files:**
- Modify: `src/chat/api/chat.py`（generate-sql / execute-sql / chat-stream）
- Modify: `src/agent/education/api.py`（学情查询端点）
- Modify: `src/system/api/user.py`, `workspace.py`, `src/datasource/api/datasource.py`, `src/system/api/permission.py`, `src/system/api/edu_permission.py`（写接口）

- [ ] **Step 1: 确认各端点签名与可用参数**

读取以下文件，记录每个待装饰端点的函数名、路径、是否含 `request: Request` / `current_user` 参数、业务参数名（datasource_id/question/resource id 字段名）：
- `src/chat/api/chat.py` — generate-sql / execute-sql / chat-stream
- `src/agent/education/api.py` — 所有 GET 学情查询端点
- `src/system/api/user.py` — POST/PUT/DELETE/PATCH 端点
- `src/system/api/workspace.py` — 写端点
- `src/datasource/api/datasource.py` — 写端点
- `src/system/api/permission.py` — POST data-rules 等写端点
- `src/system/api/edu_permission.py` — batch-bind / effective 等写端点

> 若某端点签名不含 `request: Request`，**先给该端点签名加 `request: Request` 参数**（FastAPI 不要求端点用 request，但加了无害，装饰器才能取）。若不含 `current_user`，加 `current_user = Depends(get_current_user)`（写接口本就该鉴权）。

- [ ] **Step 2: 给访问日志接口贴 @audit_access**

在 `src/chat/api/chat.py` 三个端点函数上方贴装饰器（在 `@router.post(...)` 之下、`def` 之上）：
```python
from audit.service.decorators import audit_access

@router.post("/generate-sql")
@audit_access(datasource_id_arg="<实际参数名>", query_arg="<实际参数名>")
def generate_sql(request: Request, ...):
```
参数名以 Step 1 读取为准（常见为 `datasource_id`、`question`/`query`/`natural_language`）。chat-stream 是流式端点（可能 async + StreamingResponse），**若装饰器同步包装与 async 端点冲突**，则 chat-stream 暂不贴装饰器，改为在端点内手动调 `log_access(...)`（在流结束后记录）。记录此决策。

在 `src/agent/education/api.py` 学情查询端点贴 `@audit_access`，`query_arg` 指向自然语言问题参数（若该端点入参是 JSON body 而非 query 参数，则 `query_arg` 取 body 中的字段——需装饰器支持从 body 取，若 body 是 Pydantic model，`kwargs[body_arg]` 是 model 实例，需调整装饰器或手动记录）。

> **若学情端点入参是 body model 且 query 字段在 body 内**，装饰器当前 `query_arg` 只从 kwargs 顶层取，取不到 body 内字段。处理：在该端点内手动调 `log_access(query_text=body.question, ...)`，不贴装饰器。**优先用装饰器覆盖 query 参数型端点，body 型端点手动记录**。记录哪些端点用了哪种方式。

- [ ] **Step 3: 给操作日志接口贴 @audit_operation**

在以下文件的写端点（POST/PUT/DELETE/PATCH）贴装饰器：
```python
from audit.service.decorators import audit_operation

@router.post("/user")
@audit_operation(operation_type="create", resource_type="user", resource_id_arg="<id参数名>")
def create_user(...):
```
- `src/system/api/user.py`：POST `/user`(create)、PUT `/user`(update)、DELETE `/user/{user_id}`(delete)、PATCH `/user/status`(patch)、PATCH `/user/pwd/{user_id}`(patch)、edu-scope 写端点
- `src/system/api/workspace.py`：工作空间与成员的写端点
- `src/datasource/api/datasource.py`：数据源写端点
- `src/system/api/permission.py`：POST data-rules
- `src/system/api/edu_permission.py`：batch-bind、effective

`operation_type` 按 HTTP 语义：POST→create、PUT→update、DELETE→delete、PATCH→patch。`resource_type` 按实体：user/workspace/datasource/permission/edu_permission。`resource_id_arg` 指向路径/body 中的 id 参数名（无则 None）。

> **装饰器顺序**：`@router.xxx` 在最外，`@audit_operation` 紧贴函数（装饰器先应用，router 再注册被装饰后的函数）。即：
> ```python
> @router.post("/user")
> @audit_operation(...)
> def create_user(...):
> ```

- [ ] **Step 4: 运行后端冒烟验证**

Run: `uv run python -c "from src.main import app; print('ok')"`
Expected: app 加载无错（确认装饰器 import 与 router 注册无误，无循环 import）。

- [ ] **Step 5: 回归测试**

Run: `uv run pytest tests/ -v --timeout=60`
Expected: 全过，无回归。重点关注 chat 与 system 相关测试。

> 若现有 chat/system 测试因装饰器副作用失败，检查装饰器是否改变了端点签名（FastAPI 依赖注入依赖函数签名，`functools.wraps` 保留 `__wrapped__`，FastAPI 用 `inspect.signature` 通常能穿透 wraps）。若签名穿透失败导致依赖注入异常，用 `inspect.signature(func, follow_wrapped=True)` 确认，或改装饰器用 `wrapt` 库（项目未装则避免引入，改用显式 signature 保留）。

- [ ] **Step 6: ruff 检查**

Run: `uv run ruff check src/`
Expected: 无错误。

- [ ] **Step 7: 提交**

```bash
git add src/chat/api/chat.py src/agent/education/api.py src/system/api/ src/datasource/api/datasource.py
git commit -m "feat(audit): 给关键查询与写接口接入日志采集装饰器"
```

---

## Task 8: 前端 - 扩展 CurrentUser + 审计日志 API

**Files:**
- Modify: `frontend-react/src/api/auth.ts`
- Create: `frontend-react/src/api/audit.ts`

- [ ] **Step 1: 扩展 CurrentUser**

修改 `frontend-react/src/api/auth.ts` 的 `CurrentUser` 接口（约 `:13-23`），在字段末尾加：
```typescript
is_platform_admin: boolean;
```

- [ ] **Step 2: 写 src/api/audit.ts**

`frontend-react/src/api/audit.ts`:
```typescript
import { apiRequest } from "./client";

export interface AuditAccessLogItem {
  id: number;
  trace_id: string;
  user_id: number | null;
  user_account: string | null;
  workspace_oid: number | null;
  ip: string | null;
  user_agent: string | null;
  success: boolean;
  error_msg: string | null;
  elapsed_ms: number | null;
  created_at: string | null;
  request_method: string;
  request_path: string;
  datasource_id: number | null;
  query_text: string | null;
}

export interface AuditOperationLogItem {
  id: number;
  trace_id: string;
  user_id: number | null;
  user_account: string | null;
  workspace_oid: number | null;
  ip: string | null;
  user_agent: string | null;
  success: boolean;
  error_msg: string | null;
  elapsed_ms: number | null;
  created_at: string | null;
  operation_type: string;
  resource_type: string;
  resource_id: string | null;
  detail: string | null;
}

export interface AuditLoginLogItem {
  id: number;
  trace_id: string;
  user_id: number | null;
  user_account: string | null;
  ip: string | null;
  user_agent: string | null;
  success: boolean;
  error_msg: string | null;
  created_at: string | null;
  account: string;
  fail_reason: string | null;
}

export interface PagerResult<T> {
  total: number;
  items: T[];
}

export interface AccessLogQuery {
  user_id?: number;
  datasource_id?: number;
  success?: boolean;
  start_time?: number;
  end_time?: number;
}

export interface OperationLogQuery {
  user_id?: number;
  resource_type?: string;
  operation_type?: string;
  success?: boolean;
  start_time?: number;
  end_time?: number;
}

export interface LoginLogQuery {
  account?: string;
  success?: boolean;
  start_time?: number;
  end_time?: number;
}

function buildQuery(params: Record<string, unknown>): string {
  const sp = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "") sp.append(k, String(v));
  });
  const s = sp.toString();
  return s ? `?${s}` : "";
}

export const auditApi = {
  pagerAccessLog(page: number, pageSize: number, q: AccessLogQuery = {}) {
    return apiRequest<PagerResult<AuditAccessLogItem>>(
      `/audit/access/pager/${page}/${pageSize}${buildQuery(q)}`,
    );
  },
  pagerOperationLog(page: number, pageSize: number, q: OperationLogQuery = {}) {
    return apiRequest<PagerResult<AuditOperationLogItem>>(
      `/audit/operation/pager/${page}/${pageSize}${buildQuery(q)}`,
    );
  },
  pagerLoginLog(page: number, pageSize: number, q: LoginLogQuery = {}) {
    return apiRequest<PagerResult<AuditLoginLogItem>>(
      `/audit/login/pager/${page}/${pageSize}${buildQuery(q)}`,
    );
  },
};
```

> `apiRequest<T>` 签名与 `system.ts` 一致（`client.ts:29-65`），返回解析后的 data（即 `PagerResult<T>`）。若 `apiRequest` 返回的是完整信封 `{code,message,data}`，则类型改为 `apiRequest<{code,message,data: PagerResult<T>}>` 后取 `.data`——**以 `system.ts` 现有 pagerUsers 的实际取值方式为准对齐**（读取 `system.ts:38-39` 确认 `pagerUsers` 返回的是 data 还是信封）。

- [ ] **Step 3: typecheck**

Run: `cd frontend-react && npm run typecheck`
Expected: 无错误（`CurrentUser.is_platform_admin` 后端尚未全量返回，但类型已加；若其他页面访问 `currentUser.is_platform_admin` 报 undefined 运行时风险，类型层不报）。

- [ ] **Step 4: lint**

Run: `cd frontend-react && npm run lint`
Expected: 无错误。

- [ ] **Step 5: 提交**

```bash
git add frontend-react/src/api/auth.ts frontend-react/src/api/audit.ts
git commit -m "feat(frontend): 扩展 CurrentUser + 新增审计日志 API"
```

---

## Task 9: 前端 - 侧边栏"系统管理"菜单 + 超管过滤

**Files:**
- Modify: `frontend-react/src/components/layout/side-bar.tsx`

- [ ] **Step 1: 读取 side-bar.tsx 现状**

读取 `frontend-react/src/components/layout/side-bar.tsx` 全文，定位：
- `routes` 数组（`:22-28`）
- `permissionSubRoutes` 数组（`:30-36`）
- `permissionExpanded` state（`:44`）
- 二级菜单展开渲染逻辑（`:202-247`，`isPermissionRoot` 特判）
- 折叠态渲染分支（`:123-173`）
- 选中态逻辑（`:144, :201`）
- 顶部 import（图标）
- 组件如何获取当前用户（是否已有 `getCurrentUser()` 调用或从 props/context 取）

- [ ] **Step 2: 加 systemSubRoutes 与展开 state**

在 `permissionSubRoutes` 下方加：
```typescript
const systemSubRoutes = [
  { key: "system-log-access", path: "/system/log/access", label: "访问日志" },
  { key: "system-log-operation", path: "/system/log/operation", label: "操作日志" },
  { key: "system-log-login", path: "/system/log/login", label: "登录日志" },
];
```
在 `permissionExpanded` state 旁加：
```typescript
const [systemExpanded, setSystemExpanded] = useState(true);
```
import `SettingOutlined`（若未导入）：`import { ..., SettingOutlined } from "@ant-design/icons";`

- [ ] **Step 3: 加"系统管理"一级菜单（带超管过滤）**

在 `routes` 数组末尾加（仅声明，渲染时按超管过滤）：
```typescript
{ key: "system", path: "/system", label: "系统管理", icon: <SettingOutlined /> },
```

在组件内获取当前用户（若组件已有 `currentUser` 变量则复用，否则调 `getCurrentUser()`）：
```typescript
import { getCurrentUser } from "@/api/auth";  // 路径以现有 import 风格为准
// 在组件函数体内：
const currentUser = getCurrentUser();  // 同步从 localStorage/缓存取
const isPlatformAdmin = !!currentUser?.is_platform_admin;
```
> 若 `getCurrentUser` 是 async 或需从 context 取，按 `_app.tsx` 现有取法对齐（`_app.tsx:67` 已调 `getCurrentUser()`，可能存于 context）。**以现有组件获取当前用户的方式为准**，避免重复请求。

渲染 `routes` 时过滤掉非超管的"系统管理"：
```typescript
const visibleRoutes = routes.filter(
  (r) => r.key !== "system" || isPlatformAdmin,
);
```
用 `visibleRoutes` 替代原 `routes` 遍历渲染。

- [ ] **Step 4: 加二级菜单展开渲染（仿 permission）**

在二级菜单渲染区（`isPermissionRoot` 分支旁），复制一份改为 `isSystemRoot`：
```typescript
const isSystemRoot = item.key === "system";
```
在 `isPermissionRoot` 的展开渲染 block 旁，加 `isSystemRoot` 的同构 block：展开时渲染 `systemSubRoutes`（用 `systemExpanded` state + `setSystemExpanded`），选中态逻辑同 permission。具体复制 `permissionSubRoutes.map(...)` 的渲染代码，替换为 `systemSubRoutes` 与 `systemExpanded`。

- [ ] **Step 5: 处理折叠态**

在折叠态分支（`isMenuExpand=false`，`:123-173`），"系统管理"仅显示 icon，点击跳第一个子页面 `/system/log/access`（与现有 permission 折叠态行为一致——若 permission 折叠态跳 `/construct/permission`，则 system 跳 `/system/log/access`）。折叠态不展开二级菜单。

- [ ] **Step 6: typecheck + lint**

Run: `cd frontend-react && npm run typecheck && npm run lint`
Expected: 无错误。

- [ ] **Step 7: 手动验证（dev server）**

Run: `cd frontend-react && npm run dev`（或 `npm run devW` on Windows）
- 用超管 admin 登录：侧边栏应出现"系统管理"一级菜单，展开可见 3 个子菜单，点击进入对应页面（页面在 Task 10-12 创建，此时可能 404，正常）。
- 用普通用户登录：侧边栏**不应**出现"系统管理"。
- 直接访问 `/system/log/access`：普通用户也能进页面（页面内 API 调用会被后端限制只返回自己的日志），菜单不可见但路由可达——这是设计预期（后端 API 隔离是真正的安全保证）。

- [ ] **Step 8: 提交**

```bash
git add frontend-react/src/components/layout/side-bar.tsx
git commit -m "feat(frontend): 侧边栏新增系统管理菜单并对超管过滤"
```

---

## Task 10: 前端 - 访问日志列表页

**Files:**
- Create: `frontend-react/pages/system/log/access.tsx`

- [ ] **Step 1: 写 access.tsx**

`frontend-react/pages/system/log/access.tsx`:
```tsx
import { ReloadOutlined, SearchOutlined } from "@ant-design/icons";
import { Button, DatePicker, Input, Select, Space, Table, Tag, Typography, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import dayjs from "dayjs";
import { useEffect, useState } from "react";
import { auditApi, type AuditAccessLogItem } from "@/api/audit";

const { Title, Text } = Typography;
const { RangePicker } = DatePicker;

export default function AccessLogPage() {
  const [rows, setRows] = useState<AuditAccessLogItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [range, setRange] = useState<[dayjs.Dayjs, dayjs.Dayjs] | null>(null);
  const [successFilter, setSuccessFilter] = useState<string>("");
  const [keyword, setKeyword] = useState("");

  const load = async (p = page, ps = pageSize) => {
    setLoading(true);
    try {
      const q: Record<string, unknown> = {};
      if (range && range[0] && range[1]) {
        q.start_time = range[0].startOf("day").valueOf();
        q.end_time = range[1].endOf("day").valueOf();
      }
      if (successFilter !== "") q.success = successFilter === "true";
      const res = await auditApi.pagerAccessLog(p, ps, q);
      setRows(res.items);
      setTotal(res.total);
    } catch (e) {
      message.error("加载访问日志失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void load(1); /* eslint-disable-next-line */ }, []);

  const columns: ColumnsType<AuditAccessLogItem> = [
    { title: "时间", dataIndex: "created_at", width: 180, render: (v) => v ? dayjs(v).format("YYYY-MM-DD HH:mm:ss") : "-" },
    { title: "用户", dataIndex: "user_account", width: 120 },
    { title: "IP", dataIndex: "ip", width: 140 },
    { title: "接口", dataIndex: "request_path", ellipsis: true },
    { title: "数据源", dataIndex: "datasource_id", width: 90, render: (v) => v ?? "-" },
    { title: "查询内容", dataIndex: "query_text", ellipsis: true, render: (v) => v ?? "-" },
    { title: "结果", dataIndex: "success", width: 90, render: (v) => <Tag color={v ? "green" : "red"}>{v ? "成功" : "失败"}</Tag> },
    { title: "耗时(ms)", dataIndex: "elapsed_ms", width: 100, render: (v) => v ?? "-" },
  ];

  return (
    <div className="p-6">
      <div className="mb-4 flex items-center justify-between">
        <div>
          <Title level={4} className="!mb-1">访问日志</Title>
          <Text className="oc-muted">记录 NL2SQL 与学情查询接口的访问</Text>
        </div>
        <Space>
          <RangePicker value={range} onChange={(v) => setRange(v as [dayjs.Dayjs, dayjs.Dayjs] | null)} />
          <Select
            style={{ width: 120 }}
            value={successFilter}
            onChange={setSuccessFilter}
            options={[{ value: "", label: "全部结果" }, { value: "true", label: "成功" }, { value: "false", label: "失败" }]}
          />
          <Input allowClear placeholder="查询内容" value={keyword} onChange={(e) => setKeyword(e.target.value)} onPressEnter={() => { setPage(1); void load(1); }} style={{ width: 180 }} />
          <Button icon={<SearchOutlined />} onClick={() => { setPage(1); void load(1); }}>查询</Button>
          <Button icon={<ReloadOutlined />} onClick={() => void load()}>刷新</Button>
        </Space>
      </div>
      <div className="overflow-hidden rounded-2xl border border-[#e5e7eb] bg-white shadow-sm">
        <Table
          rowKey="id"
          columns={columns}
          dataSource={rows}
          loading={loading}
          pagination={{
            current: page, pageSize, total,
            showSizeChanger: true, showTotal: (t) => `共 ${t} 条`,
            onChange: (p, ps) => { setPage(p); setPageSize(ps); void load(p, ps); },
          }}
          expandable={{
            expandedRowRender: (r) => (
              <div className="text-sm">
                <div>trace_id: {r.trace_id}</div>
                <div>UA: {r.user_agent ?? "-"}</div>
                <div>查询内容: {r.query_text ?? "-"}</div>
                <div>错误信息: {r.error_msg ?? "-"}</div>
              </div>
            ),
          }}
        />
      </div>
    </div>
  );
}
```

> **依赖确认**：`dayjs` 是否已装（antd 5 内置依赖通常已装）——若 `npm run typecheck` 报 dayjs 缺失，`npm i dayjs`。`@/api/audit` 路径别名以 `tsconfig.json` paths 为准（与 `@/api/auth` 一致）。`oc-muted` class 以现有页面用法为准（`members.tsx` 用过）。

- [ ] **Step 2: typecheck + lint**

Run: `cd frontend-react && npm run typecheck && npm run lint`
Expected: 无错误。

- [ ] **Step 3: 手动验证**

启动 dev server，超管登录访问 `/system/log/access`：表格显示访问日志，时间/用户/IP/查询内容/结果/耗时列正确，分页可翻页，时间范围+结果筛选生效，行展开显示 trace_id/UA/完整查询/错误信息。

- [ ] **Step 4: 提交**

```bash
git add frontend-react/pages/system/log/access.tsx
git commit -m "feat(frontend): 新增访问日志列表页"
```

---

## Task 11: 前端 - 操作日志列表页

**Files:**
- Create: `frontend-react/pages/system/log/operation.tsx`

- [ ] **Step 1: 写 operation.tsx**

`frontend-react/pages/system/log/operation.tsx`:
```tsx
import { ReloadOutlined, SearchOutlined } from "@ant-design/icons";
import { Button, DatePicker, Input, Select, Space, Table, Tag, Typography, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import dayjs from "dayjs";
import { useEffect, useState } from "react";
import { auditApi, type AuditOperationLogItem } from "@/api/audit";

const { Title, Text } = Typography;
const { RangePicker } = DatePicker;

const OPERATION_TYPES = ["create", "update", "delete", "patch"];
const RESOURCE_TYPES = ["user", "workspace", "datasource", "permission", "edu_permission"];

export default function OperationLogPage() {
  const [rows, setRows] = useState<AuditOperationLogItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [range, setRange] = useState<[dayjs.Dayjs, dayjs.Dayjs] | null>(null);
  const [operationType, setOperationType] = useState<string>("");
  const [resourceType, setResourceType] = useState<string>("");

  const load = async (p = page, ps = pageSize) => {
    setLoading(true);
    try {
      const q: Record<string, unknown> = {};
      if (range && range[0] && range[1]) {
        q.start_time = range[0].startOf("day").valueOf();
        q.end_time = range[1].endOf("day").valueOf();
      }
      if (operationType) q.operation_type = operationType;
      if (resourceType) q.resource_type = resourceType;
      const res = await auditApi.pagerOperationLog(p, ps, q);
      setRows(res.items);
      setTotal(res.total);
    } catch {
      message.error("加载操作日志失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void load(1); /* eslint-disable-next-line */ }, []);

  const columns: ColumnsType<AuditOperationLogItem> = [
    { title: "时间", dataIndex: "created_at", width: 180, render: (v) => v ? dayjs(v).format("YYYY-MM-DD HH:mm:ss") : "-" },
    { title: "用户", dataIndex: "user_account", width: 120 },
    { title: "IP", dataIndex: "ip", width: 140 },
    { title: "操作", dataIndex: "operation_type", width: 90, render: (v) => <Tag>{v}</Tag> },
    { title: "资源类型", dataIndex: "resource_type", width: 120 },
    { title: "资源ID", dataIndex: "resource_id", width: 100 },
    { title: "结果", dataIndex: "success", width: 90, render: (v) => <Tag color={v ? "green" : "red"}>{v ? "成功" : "失败"}</Tag> },
  ];

  return (
    <div className="p-6">
      <div className="mb-4 flex items-center justify-between">
        <div>
          <Title level={4} className="!mb-1">操作日志</Title>
          <Text className="oc-muted">记录增删改写接口的操作</Text>
        </div>
        <Space>
          <RangePicker value={range} onChange={(v) => setRange(v as [dayjs.Dayjs, dayjs.Dayjs] | null)} />
          <Select style={{ width: 130 }} value={operationType} onChange={setOperationType} options={[{ value: "", label: "全部操作" }, ...OPERATION_TYPES.map((t) => ({ value: t, label: t }))]} />
          <Select style={{ width: 150 }} value={resourceType} onChange={setResourceType} options={[{ value: "", label: "全部资源" }, ...RESOURCE_TYPES.map((t) => ({ value: t, label: t }))]} />
          <Button icon={<SearchOutlined />} onClick={() => { setPage(1); void load(1); }}>查询</Button>
          <Button icon={<ReloadOutlined />} onClick={() => void load()}>刷新</Button>
        </Space>
      </div>
      <div className="overflow-hidden rounded-2xl border border-[#e5e7eb] bg-white shadow-sm">
        <Table
          rowKey="id"
          columns={columns}
          dataSource={rows}
          loading={loading}
          pagination={{
            current: page, pageSize, total,
            showSizeChanger: true, showTotal: (t) => `共 ${t} 条`,
            onChange: (p, ps) => { setPage(p); setPageSize(ps); void load(p, ps); },
          }}
          expandable={{
            expandedRowRender: (r) => (
              <div className="text-sm">
                <div>trace_id: {r.trace_id}</div>
                <div>UA: {r.user_agent ?? "-"}</div>
                <div>详情: {r.detail ?? "-"}</div>
                <div>错误信息: {r.error_msg ?? "-"}</div>
              </div>
            ),
          }}
        />
      </div>
    </div>
  );
}
```

- [ ] **Step 2: typecheck + lint**

Run: `cd frontend-react && npm run typecheck && npm run lint`
Expected: 无错误。

- [ ] **Step 3: 手动验证**

超管访问 `/system/log/operation`：表格显示操作日志，操作/资源类型筛选生效，行展开显示 detail。

- [ ] **Step 4: 提交**

```bash
git add frontend-react/pages/system/log/operation.tsx
git commit -m "feat(frontend): 新增操作日志列表页"
```

---

## Task 12: 前端 - 登录日志列表页

**Files:**
- Create: `frontend-react/pages/system/log/login.tsx`

- [ ] **Step 1: 写 login.tsx**

`frontend-react/pages/system/log/login.tsx`:
```tsx
import { ReloadOutlined, SearchOutlined } from "@ant-design/icons";
import { Button, DatePicker, Input, Select, Space, Table, Tag, Typography, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import dayjs from "dayjs";
import { useEffect, useState } from "react";
import { auditApi, type AuditLoginLogItem } from "@/api/audit";

const { Title, Text } = Typography;
const { RangePicker } = DatePicker;

export default function LoginLogPage() {
  const [rows, setRows] = useState<AuditLoginLogItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [range, setRange] = useState<[dayjs.Dayjs, dayjs.Dayjs] | null>(null);
  const [successFilter, setSuccessFilter] = useState<string>("");
  const [account, setAccount] = useState("");

  const load = async (p = page, ps = pageSize) => {
    setLoading(true);
    try {
      const q: Record<string, unknown> = {};
      if (range && range[0] && range[1]) {
        q.start_time = range[0].startOf("day").valueOf();
        q.end_time = range[1].endOf("day").valueOf();
      }
      if (successFilter !== "") q.success = successFilter === "true";
      if (account) q.account = account;
      const res = await auditApi.pagerLoginLog(p, ps, q);
      setRows(res.items);
      setTotal(res.total);
    } catch {
      message.error("加载登录日志失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void load(1); /* eslint-disable-next-line */ }, []);

  const columns: ColumnsType<AuditLoginLogItem> = [
    { title: "时间", dataIndex: "created_at", width: 180, render: (v) => v ? dayjs(v).format("YYYY-MM-DD HH:mm:ss") : "-" },
    { title: "账号", dataIndex: "account", width: 140 },
    { title: "IP", dataIndex: "ip", width: 140 },
    { title: "结果", dataIndex: "success", width: 90, render: (v) => <Tag color={v ? "green" : "red"}>{v ? "成功" : "失败"}</Tag> },
    { title: "失败原因", dataIndex: "fail_reason", ellipsis: true, render: (v) => v ?? "-" },
  ];

  return (
    <div className="p-6">
      <div className="mb-4 flex items-center justify-between">
        <div>
          <Title level={4} className="!mb-1">登录日志</Title>
          <Text className="oc-muted">记录登录成功与失败事件</Text>
        </div>
        <Space>
          <RangePicker value={range} onChange={(v) => setRange(v as [dayjs.Dayjs, dayjs.Dayjs] | null)} />
          <Select style={{ width: 120 }} value={successFilter} onChange={setSuccessFilter} options={[{ value: "", label: "全部结果" }, { value: "true", label: "成功" }, { value: "false", label: "失败" }]} />
          <Input allowClear placeholder="账号" value={account} onChange={(e) => setAccount(e.target.value)} onPressEnter={() => { setPage(1); void load(1); }} style={{ width: 160 }} />
          <Button icon={<SearchOutlined />} onClick={() => { setPage(1); void load(1); }}>查询</Button>
          <Button icon={<ReloadOutlined />} onClick={() => void load()}>刷新</Button>
        </Space>
      </div>
      <div className="overflow-hidden rounded-2xl border border-[#e5e7eb] bg-white shadow-sm">
        <Table
          rowKey="id"
          columns={columns}
          dataSource={rows}
          loading={loading}
          pagination={{
            current: page, pageSize, total,
            showSizeChanger: true, showTotal: (t) => `共 ${t} 条`,
            onChange: (p, ps) => { setPage(p); setPageSize(ps); void load(p, ps); },
          }}
          expandable={{
            expandedRowRender: (r) => (
              <div className="text-sm">
                <div>trace_id: {r.trace_id}</div>
                <div>UA: {r.user_agent ?? "-"}</div>
                <div>错误信息: {r.error_msg ?? "-"}</div>
              </div>
            ),
          }}
        />
      </div>
    </div>
  );
}
```

- [ ] **Step 2: typecheck + lint**

Run: `cd frontend-react && npm run typecheck && npm run lint`
Expected: 无错误。

- [ ] **Step 3: 手动验证**

超管访问 `/system/log/login`：表格显示所有用户登录日志（含失败），账号/结果筛选生效。普通用户访问：只看到自己 account 的登录记录（含失败）。触发一次登录失败（输错密码）后刷新，应见失败记录。

- [ ] **Step 4: 提交**

```bash
git add frontend-react/pages/system/log/login.tsx
git commit -m "feat(frontend): 新增登录日志列表页"
```

---

## Task 13: 全量验证与收尾

**Files:**
- 无新增，全量回归

- [ ] **Step 1: 后端全量测试**

Run: `uv run pytest tests/ -v --timeout=60`
Expected: 全过，含 `tests/audit/` 全部。

- [ ] **Step 2: 后端 lint**

Run: `uv run ruff check .`
Expected: 无错误。

- [ ] **Step 3: 前端 typecheck + lint**

Run: `cd frontend-react && npm run typecheck && npm run lint`
Expected: 无错误。

- [ ] **Step 4: 端到端手动验证清单**

启动后端 `uv run uvicorn src.main:app --reload --port 8000` + 前端 `cd frontend-react && npm run dev`，逐项验证：
- 超管 admin 登录：侧边栏可见"系统管理" → 3 个子菜单均可进入，表格有数据。
- 普通用户登录：侧边栏无"系统管理"；直接访问 `/system/log/access` 能进页面但只显示自己的访问日志；`/audit/access/pager` API 只返回自己的记录。
- 登录失败（输错密码）：登录日志出现失败记录 + fail_reason。
- 触发一次 NL2SQL 查询：访问日志出现该查询记录（query_text 含自然语言问题）。
- 触发一次写操作（如新增用户）：操作日志出现 create 记录。
- 普通用户调 `/audit/access/pager?user_id=1`（尝试越权看 admin 的）：后端忽略 user_id 参数，仍只返回自己的（crud 层强制隔离）。

- [ ] **Step 5: 提交收尾**

若有未提交的改动：
```bash
git add -A
git commit -m "chore(audit): 日志管理功能收尾"
```

- [ ] **Step 6: 更新 memory（可选）**

若实现中发现了项目级非显然约定（如 SessionLocal 实际导出名、apiRequest 返回结构、side-bar 取当前用户方式），写入 `C:\Users\great_allen\.claude\projects\D--data-whole-project-xhthy00-awesome-data\memory\` 对应 memory 文件并在 MEMORY.md 加索引行。
