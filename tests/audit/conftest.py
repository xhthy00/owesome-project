"""audit 测试公共 fixture。"""
from datetime import datetime, timezone
from typing import Generator

import pytest
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

from audit.models import AuditAccessLog, AuditLoginLog, AuditOperationLog  # noqa: F401
from system.models import SysUser  # noqa: F401
from system.models.workspace import SysUserWorkspace, SysWorkspace  # noqa: F401


# SysUser.system_variables 是 PostgreSQL 的 JSONB 列，在 SQLite 上无法渲染。
# 给 JSONB 注册一个 sqlite 方言的编译钩子，降级成 JSON，使 create_all 能在
# in-memory SQLite 上建出 sys_user 表，admin_user / normal_user fixture 才可用。
@compiles(JSONB, "sqlite")
def _jsonb_to_json_sqlite(element, compiler, **kw):
    return "JSON"


# 审计表主键 id 是 BigInteger + autoincrement。SQLite 只对 ``INTEGER PRIMARY
# KEY``（rowid 别名）自动生成自增值，``BIGINT PRIMARY KEY`` 不会自增，插入会
# 触发 NOT NULL 约束。这里把 BigInteger 在 sqlite 方言下编译成 INTEGER，使主键
# 可自增；其余 BigInteger 列（user_id 等）同步降级为 INTEGER，对内存库测试无
# 功能影响。仅作用于测试的 in-memory SQLite，不影响线上 PostgreSQL（BIGSERIAL）。
@compiles(BigInteger, "sqlite")
def _bigint_to_integer_sqlite(element, compiler, **kw):
    return "INTEGER"


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
        create_time=int(datetime.now(timezone.utc).timestamp() * 1000),
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
        create_time=int(datetime.now(timezone.utc).timestamp() * 1000),
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user
