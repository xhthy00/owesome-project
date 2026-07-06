"""教育权限配置 API 单元测试（mock 鉴权与数据库）。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from common.core.database import get_session
from common.router import register_routers
from system.api.system import get_current_user
from system.schemas import UserResponse


def _admin_user() -> UserResponse:
    return UserResponse(
        id=1,
        account="admin",
        name="Admin",
        email=None,
        oid=1,
        status=1,
        language="zh-CN",
        origin=0,
        create_time=0,
    )


@pytest.fixture
def auth_client(monkeypatch):
    app = FastAPI()
    register_routers(app)
    app.dependency_overrides[get_current_user] = _admin_user
    monkeypatch.setattr("system.api.edu_permission.can_manage_data_permissions", lambda _s, _u: True)
    monkeypatch.setattr("system.api.user.can_manage_data_permissions", lambda _s, _u: True)
    return TestClient(app)


def test_list_edu_roles(auth_client):
    r = auth_client.get("/api/v1/permission/edu/roles")
    assert r.status_code == 200
    data = r.json()["data"]
    codes = {item["code"] for item in data}
    assert codes == {"bureau_admin", "school_admin", "teacher", "student"}


def test_batch_bind_csv_success(auth_client, monkeypatch):
    user = SimpleNamespace(
        id=10,
        account="li_teacher",
        system_variables={},
    )

    class FakeQuery:
        def filter(self, *_a, **_kw):
            return self

        def first(self):
            return user

    session = MagicMock()
    session.query.return_value = FakeQuery()
    session.add = MagicMock()
    session.commit = MagicMock()

    def fake_get_session():
        yield session

    auth_client.app.dependency_overrides[get_session] = fake_get_session

    csv = (
        "account,edu_role,school_id,school_name,class_names,student_id\n"
        "li_teacher,teacher,1,南京市第一中学,高一(1)班,\n"
    )
    r = auth_client.post("/api/v1/permission/edu/batch-bind", json={"csv": csv})
    assert r.status_code == 200
    assert r.json()["data"]["success"] == 1
    assert user.system_variables.get("edu_role") == "teacher"


def test_batch_bind_unknown_user(auth_client, monkeypatch):
    class FakeQuery:
        def filter(self, *_a, **_kw):
            return self

        def first(self):
            return None

    session = MagicMock()
    session.query.return_value = FakeQuery()
    session.commit = MagicMock()

    def fake_get_session():
        yield session

    auth_client.app.dependency_overrides[get_session] = fake_get_session

    csv = "account,edu_role,school_id,school_name,class_names,student_id\nnobody,teacher,1,,高一(1)班,\n"
    r = auth_client.post("/api/v1/permission/edu/batch-bind", json={"csv": csv})
    assert r.status_code == 200
    assert r.json()["data"]["success"] == 0
    assert r.json()["data"]["failed"]
