"""Chat stream compatibility and permission checks around the DeepAgents entry point."""

from __future__ import annotations

import pytest
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
    app.state.chat_checkpointer = object()
    app.include_router(chat_router, prefix="/api/v1")
    monkeypatch.setattr("src.chat.api.chat._edu_out_of_scope_message", lambda *_a, **_kw: None)

    class _AuthUser:
        id = 1
        account = "admin"
        oid = 1

    app.dependency_overrides[get_current_user] = lambda: _AuthUser()
    app.dependency_overrides[get_workspace_oid] = lambda: 1
    return app


def _patch_agent(monkeypatch, calls: list) -> None:
    async def fake_runtime(**kwargs):
        calls.append(kwargs["request"].agent_mode)
        await kwargs["emit"]("summary", {"content": "stubbed-deepagents"})
        return 0

    monkeypatch.setattr("src.chat.service.deepagents_runtime.run_chat_stream", fake_runtime)


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


@pytest.mark.parametrize("mode", [None, "agent", "team", "legacy"])
def test_compatible_modes_use_deepagents(monkeypatch, mode):
    calls = []
    _patch_agent(monkeypatch, calls)
    payload = {"question": "hi", "datasource_id": 1}
    if mode is not None:
        payload.update(agent_mode=mode, enable_tool_agent=False)
    body = _stream_body(TestClient(_build_app(monkeypatch)), payload)

    assert calls == ["agent"]
    assert "stubbed-deepagents" in body
    assert "event: done" in body


@pytest.mark.parametrize("path,execute", [("generate-sql", False), ("execute-sql", True)])
def test_sql_endpoints_use_semantic_runtime(monkeypatch, path, execute):
    async def runtime(**kwargs):
        assert kwargs["execute"] == execute
        await kwargs["emit"]("sql", {"sql": "SELECT score FROM scores", "bundle_id": "edu"})
        if execute:
            await kwargs["emit"]("result", {"columns": ["score"], "rows": [[12]]})
        return 0

    monkeypatch.setattr("src.chat.service.deepagents_runtime.run_chat_stream", runtime)
    response = TestClient(_build_app(monkeypatch)).post(
        f"/api/v1/chat/{path}", json={"question": "查询分数", "datasource_id": 1},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["bundle_id"] == "edu" and not data["error"]
    if execute:
        assert data["result"]["rows"] == [[12]]
    else:
        assert data["is_valid"] is True


def test_chat_stream_rejects_foreign_conversation(monkeypatch):
    app = _build_app(monkeypatch)
    monkeypatch.setattr(
        "src.chat.api.chat.chat_crud.get_conversation_by_id",
        lambda *_args, **_kwargs: None,
    )
    response = TestClient(app).post(
        "/api/v1/chat/chat-stream",
        json={"question": "hi", "datasource_id": 1, "conversation_id": 99},
    )
    assert response.status_code == 404


def test_chat_stream_rejects_conversation_datasource_mismatch(monkeypatch):
    app = _build_app(monkeypatch)

    class Conversation:
        datasource_id = 2

    monkeypatch.setattr(
        "src.chat.api.chat.chat_crud.get_conversation_by_id",
        lambda *_args, **_kwargs: Conversation(),
    )
    response = TestClient(app).post(
        "/api/v1/chat/chat-stream",
        json={"question": "hi", "datasource_id": 1, "conversation_id": 99},
    )
    assert response.status_code == 409


def test_agent_mode_rejects_unknown_value(monkeypatch):
    calls = []
    _patch_agent(monkeypatch, calls)

    resp = TestClient(_build_app(monkeypatch)).post(
        "/api/v1/chat/chat-stream",
        json={"question": "hi", "datasource_id": 1, "agent_mode": "wizard"},
    )
    assert resp.status_code == 422
    assert not calls


def test_chat_stream_emits_x_trace_id_header(monkeypatch):
    """每个 chat-stream 响应都应带 ``X-Trace-Id`` 响应头；不同请求的 trace_id 不同。"""
    calls = []
    _patch_agent(monkeypatch, calls)
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
    from src.chat.api.chat import _edu_out_of_scope_message

    app = _build_app(monkeypatch)
    monkeypatch.setattr("src.chat.api.chat._edu_out_of_scope_message", _edu_out_of_scope_message)
    calls = []
    _patch_agent(monkeypatch, calls)
    monkeypatch.setattr(
        "datasource.service.edu_permission.edu_scope_dict_for_user_id",
        lambda _uid: {
            "edu_role": "teacher",
            "school_name": "扬州中学",
            "class_names": ["高一(1)班"],
        },
    )
    body = _stream_body(
        TestClient(app),
        {"question": "高一(3)班数学均分", "datasource_id": 1, "agent_mode": "team"},
    )
    assert not calls
    assert OUT_OF_SCOPE_MESSAGE in body
    assert "event: error" in body
