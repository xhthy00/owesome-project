"""匿名脱敏展示开关 API。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from common.core.database import get_session
from common.router import register_routers
from system.api.auth_deps import get_current_user
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


def _member_user() -> UserResponse:
    return UserResponse(
        id=2,
        account="teacher",
        name="Teacher",
        email=None,
        oid=1,
        status=1,
        language="zh-CN",
        origin=0,
        create_time=0,
    )


@pytest.fixture
def admin_client(monkeypatch):
    app = FastAPI()
    register_routers(app)
    app.dependency_overrides[get_current_user] = _admin_user
    return TestClient(app)


def test_get_edu_privacy_default_true(admin_client, monkeypatch):
    session = MagicMock()
    monkeypatch.setattr(
        "system.api.permission.get_anonymize_display",
        lambda _s: True,
    )

    def fake_get_session():
        yield session

    admin_client.app.dependency_overrides[get_session] = fake_get_session
    r = admin_client.get("/api/v1/permission/edu-privacy")
    assert r.status_code == 200
    assert r.json()["data"]["anonymize_display"] is True


def test_put_edu_privacy_admin(admin_client, monkeypatch):
    session = MagicMock()
    called = {}

    def fake_set(_s, enabled: bool):
        called["enabled"] = enabled
        return SimpleNamespace(anonymize_display=enabled)

    monkeypatch.setattr("system.api.permission.set_anonymize_display", fake_set)
    monkeypatch.setattr(
        "src.agent.education.privacy_mode.set_anonymize_display_cached",
        lambda enabled: called.update({"cached": enabled}),
    )

    def fake_get_session():
        yield session

    admin_client.app.dependency_overrides[get_session] = fake_get_session
    r = admin_client.put("/api/v1/permission/edu-privacy", json={"anonymize_display": False})
    assert r.status_code == 200
    assert r.json()["data"]["anonymize_display"] is False
    assert called["enabled"] is False
    assert called["cached"] is False


def test_put_edu_privacy_forbidden_for_member(monkeypatch):
    from common.middlewares.exception import register_exception_handlers

    app = FastAPI()
    register_routers(app)
    register_exception_handlers(app)
    app.dependency_overrides[get_current_user] = _member_user
    session = MagicMock()

    def fake_get_session():
        yield session

    app.dependency_overrides[get_session] = fake_get_session
    client = TestClient(app)
    r = client.put("/api/v1/permission/edu-privacy", json={"anonymize_display": False})
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 403
    assert "系统管理员" in body["message"]
