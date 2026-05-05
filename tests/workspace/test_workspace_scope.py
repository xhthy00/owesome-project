"""工作空间与数据源归属：轻量单元测试（无 DB 容器依赖）。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from common.exceptions.base import NotFoundException
from system.schemas import UserResponse

import src.system.workspace_scope as workspace_scope


def test_assert_datasource_accessible_oid_mismatch_raises_not_found(monkeypatch):
    session = MagicMock()
    user = UserResponse(
        id=2,
        account="u1",
        name="u1",
        oid=1,
        email=None,
        status=1,
        language="zh",
        origin=0,
        create_time=0,
    )
    ds = SimpleNamespace(id=5, oid=99)

    monkeypatch.setattr(
        workspace_scope.crud_datasource,
        "get_datasource_by_id",
        lambda s, i: ds if i == 5 else None,
    )
    monkeypatch.setattr(workspace_scope, "is_platform_admin_user", lambda u: False)
    monkeypatch.setattr(
        workspace_scope,
        "user_is_member_of_workspace",
        lambda s, uid, oid: True,
    )

    with pytest.raises(NotFoundException):
        workspace_scope.assert_datasource_accessible(session, user, 5, workspace_oid=1)
