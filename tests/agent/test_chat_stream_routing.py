"""/api/v1/chat/chat-stream 的路由回归：确保 ``agent_mode`` 分流不会悄悄串台。

这不是一个业务功能测试（业务由 test_agent_runner / test_team_runner 覆盖），
**只**验证：
- 缺省进入 agent 分支；
- ``agent_mode="agent"`` 进入 agent 分支，legacy / team 代码 **绝不** 被触发；
- ``agent_mode="team"``  进入 team 分支，agent / legacy 代码 **绝不** 被触发；
- ``agent_mode="legacy"`` 进入 legacy 分支，agent / team 代码 **绝不** 被触发；
- 非法值被 Pydantic 拒绝（422）。
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.chat.api.chat import router as chat_router
from system.api.auth_deps import get_current_user
from system.workspace_scope import get_workspace_oid


def _build_app(monkeypatch) -> FastAPI:
    monkeypatch.setattr(
        "src.chat.api.chat.assert_datasource_accessible",
        lambda *_a, **_k: None,
    )
    app = FastAPI()
    app.include_router(chat_router, prefix="/api/v1")

    class _AuthUser:
        id = 1
        account = "admin"
        oid = 1

    app.dependency_overrides[get_current_user] = lambda: _AuthUser()
    app.dependency_overrides[get_workspace_oid] = lambda: 1
    return app


def _patch_agent(monkeypatch, flag: dict) -> None:
    async def _fake_agent(**kwargs):
        flag["agent_called"] = True
        emit = kwargs["emit"]
        await emit("final_answer", {"text": "stubbed-agent"})
        return 0

    async def _fake_team(**kwargs):
        flag["team_called"] = True
        flag["enable_tool_agent"] = kwargs.get("enable_tool_agent")
        emit = kwargs["emit"]
        await emit("summary", {"content": "stubbed-team"})
        return 0

    import src.chat.service.agent_runner as runner_mod

    monkeypatch.setattr(runner_mod, "run_agent_stream", _fake_agent)
    monkeypatch.setattr(runner_mod, "run_team_stream", _fake_team)


def _patch_legacy(monkeypatch, flag: dict) -> None:
    class _FakeGenerator:
        def generate_sql(self, **kwargs):
            flag["legacy_called"] = True
            return {
                "is_valid": False,
                "sql": "",
                "error": "stubbed-legacy",
                "chart_type": "table",
                "tables": [],
                "steps": [],
                "reasoning": "",
            }

    import src.chat.api.chat as chat_api_mod

    monkeypatch.setattr(chat_api_mod, "SQLGenerator", _FakeGenerator)


def _stream_body(client: TestClient, payload: dict) -> str:
    with client.stream("POST", "/api/v1/chat/chat-stream", json=payload) as resp:
        assert resp.status_code == 200
        return "".join(resp.iter_text())


def _stream_response(client: TestClient, payload: dict) -> tuple[str, dict[str, str]]:
    """变体：同时返回 body 和 headers，用于 X-Trace-Id 断言。"""
    with client.stream("POST", "/api/v1/chat/chat-stream", json=payload) as resp:
        assert resp.status_code == 200
        headers = dict(resp.headers)
        body = "".join(resp.iter_text())
    return body, headers


def _fresh_flag() -> dict:
    return {
        "agent_called": False,
        "team_called": False,
        "legacy_called": False,
        "enable_tool_agent": None,
    }


def test_agent_mode_defaults_to_agent_branch(monkeypatch):
    flag = _fresh_flag()
    _patch_agent(monkeypatch, flag)
    _patch_legacy(monkeypatch, flag)

    body = _stream_body(
        TestClient(_build_app(monkeypatch)),
        {"question": "hi", "datasource_id": 1},
    )

    assert flag["agent_called"] is True
    assert flag["team_called"] is False
    assert flag["legacy_called"] is False
    assert "stubbed-agent" in body
    assert "final_answer" in body
    assert "done" in body


def test_agent_mode_explicit_team_routes_to_team_runner(monkeypatch):
    flag = _fresh_flag()
    _patch_agent(monkeypatch, flag)
    _patch_legacy(monkeypatch, flag)

    body = _stream_body(
        TestClient(_build_app(monkeypatch)),
        {"question": "hi", "datasource_id": 1, "agent_mode": "team"},
    )

    assert flag["team_called"] is True
    assert flag["agent_called"] is False
    assert flag["legacy_called"] is False
    assert flag["enable_tool_agent"] is True
    assert "stubbed-team" in body


def test_team_mode_can_disable_tool_agent(monkeypatch):
    flag = _fresh_flag()
    _patch_agent(monkeypatch, flag)
    _patch_legacy(monkeypatch, flag)

    _stream_body(
        TestClient(_build_app(monkeypatch)),
        {
            "question": "hi",
            "datasource_id": 1,
            "agent_mode": "team",
            "enable_tool_agent": False,
        },
    )
    assert flag["team_called"] is True
    assert flag["enable_tool_agent"] is False


def test_agent_mode_explicit_legacy_routes_to_sql_generator(monkeypatch):
    flag = _fresh_flag()
    _patch_agent(monkeypatch, flag)
    _patch_legacy(monkeypatch, flag)

    body = _stream_body(
        TestClient(_build_app(monkeypatch)),
        {"question": "hi", "datasource_id": 1, "agent_mode": "legacy"},
    )

    assert flag["legacy_called"] is True
    assert flag["agent_called"] is False
    assert flag["team_called"] is False
    assert "stubbed-legacy" in body


def test_agent_mode_rejects_unknown_value(monkeypatch):
    flag = _fresh_flag()
    _patch_agent(monkeypatch, flag)
    _patch_legacy(monkeypatch, flag)

    resp = TestClient(_build_app(monkeypatch)).post(
        "/api/v1/chat/chat-stream",
        json={"question": "hi", "datasource_id": 1, "agent_mode": "wizard"},
    )
    assert resp.status_code == 422
    assert not any(flag.values())


def test_chat_stream_emits_x_trace_id_header(monkeypatch):
    """每个 chat-stream 响应都应带 ``X-Trace-Id`` 响应头；不同请求的 trace_id 不同。"""
    flag = _fresh_flag()
    _patch_agent(monkeypatch, flag)
    _patch_legacy(monkeypatch, flag)
    client = TestClient(_build_app(monkeypatch))

    _, headers_1 = _stream_response(client, {"question": "q1", "datasource_id": 1})
    _, headers_2 = _stream_response(client, {"question": "q2", "datasource_id": 1})

    tid_1 = headers_1.get("x-trace-id") or headers_1.get("X-Trace-Id")
    tid_2 = headers_2.get("x-trace-id") or headers_2.get("X-Trace-Id")
    assert tid_1, f"X-Trace-Id missing, headers={headers_1}"
    assert tid_2, f"X-Trace-Id missing, headers={headers_2}"
    assert tid_1 != tid_2, "每次请求应分配独立 trace_id"
    assert len(tid_1) >= 8 and tid_1 != "-"


def test_chat_stream_denies_teacher_other_class_before_runner(monkeypatch):
    from src.agent.education.scope_guard import OUT_OF_SCOPE_MESSAGE

    flag = _fresh_flag()
    _patch_agent(monkeypatch, flag)
    _patch_legacy(monkeypatch, flag)
    monkeypatch.setattr(
        "datasource.service.edu_permission.edu_scope_dict_for_user_id",
        lambda _uid: {
            "edu_role": "teacher",
            "school_name": "扬州中学",
            "class_names": ["高一(1)班"],
        },
    )
    body = _stream_body(
        TestClient(_build_app(monkeypatch)),
        {"question": "高一(3)班数学均分", "datasource_id": 1, "agent_mode": "team"},
    )
    assert flag["team_called"] is False
    assert flag["agent_called"] is False
    assert flag["legacy_called"] is False
    assert OUT_OF_SCOPE_MESSAGE in body
    assert "event: error" in body
