"""chat-stream SSE 心跳回归：静默期必须发注释保活帧，且不打断真实事件。

背景：team 流水线在 LLM 往返 / SQL 执行 / 持久化阶段会有数十秒无事件输出的
"静默期"；浏览器到 :3001 之间链路上的中间网元（云 ELB / 企业代理 / 运营商
NAT）有 30~60s 空闲超时，会在静默期把连接按"空闲"掐断——前端表现为"卡死"，
但后端任务仍在跑并最终落库（刷新即看到完整结果）。修复方式见
``src/chat/api/chat.py::event_stream`` 的 SSE 注释心跳。

本测试**只**验证心跳行为本身（不是业务功能）：
- 队列静默超过 ``_SSE_KEEPALIVE_INTERVAL`` 时，yield 出 ``: keepalive`` 注释帧；
- 真实事件不受影响（静默前/后的事件都应原样到达）；
- 多个静默段应产生多帧心跳。
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.chat.api import chat as chat_api_mod
from src.chat.api.chat import router as chat_router
from system.api.auth_deps import get_current_user
from system.workspace_scope import get_workspace_oid


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(chat_router, prefix="/api/v1")

    class _AuthUser:
        id = 1
        account = "admin"
        oid = 1

    app.dependency_overrides[get_current_user] = lambda: _AuthUser()
    # chat_stream 还依赖 get_workspace_oid；它在非 admin 时会查库。这里直接固定
    # 返回 oid=1，避免测试需要真实 DB / workspace 成员表。
    app.dependency_overrides[get_workspace_oid] = lambda: 1
    return app


def _patch_slow_agent(monkeypatch, *, pre_idle: float, mid_idle: float) -> dict:
    """桩一个会在首事件前、两个事件之间都 sleep 的 agent，强制制造静默段。

    flag 记录被调用的时刻，供断言"静默段被心跳填满"用。
    """
    flag: dict[str, Any] = {"called": False}

    async def _fake_agent(**kwargs):
        flag["called"] = True
        emit = kwargs["emit"]
        # 首事件前静默：触发至少一帧心跳
        await asyncio.sleep(pre_idle)
        await emit("final_answer", {"text": "first"})
        # 两事件之间静默：再触发至少一帧心跳
        await asyncio.sleep(mid_idle)
        await emit("agent_thought", {"text": "second"})
        return 0

    import src.chat.service.agent_runner as runner_mod

    monkeypatch.setattr(runner_mod, "run_agent_stream", _fake_agent)
    monkeypatch.setattr(runner_mod, "run_team_stream", _fake_agent)
    return flag


def _stream_body(client: TestClient, payload: dict) -> str:
    with client.stream("POST", "/api/v1/chat/chat-stream", json=payload) as resp:
        assert resp.status_code == 200
        return "".join(resp.iter_text())


def test_sse_keepalive_emitted_during_silence(monkeypatch):
    """静默期超过心跳间隔时，响应体里出现 ``: keepalive`` 注释帧。"""
    # 把心跳间隔压到 0.05s，静默 0.2s → 应出现多帧心跳，且测试仍很快。
    monkeypatch.setattr(chat_api_mod, "_SSE_KEEPALIVE_INTERVAL", 0.05)
    _patch_slow_agent(monkeypatch, pre_idle=0.2, mid_idle=0.2)

    body = _stream_body(
        TestClient(_build_app()),
        # datasource_id=0 让 chat_stream 跳过 assert_datasource_accessible（chat.py
        # 里 `if request.datasource_id:` 为假），测试无需真实 DB / 数据源即可跑；
        # 桩 agent 不消费 datasource_id。缺省 agent 分支。
        {"question": "hi", "datasource_id": 0},
    )

    assert ": keepalive" in body, f"未见心跳帧，body={body!r}"
    # 至少两帧（前后两段静默各至少一帧）
    assert body.count(": keepalive") >= 2, f"心跳帧过少，body={body!r}"


def test_sse_keepalive_does_not_drop_real_events(monkeypatch):
    """心跳不应打断真实事件：final_answer / agent_thought / done 仍须原样到达。"""
    monkeypatch.setattr(chat_api_mod, "_SSE_KEEPALIVE_INTERVAL", 0.05)
    _patch_slow_agent(monkeypatch, pre_idle=0.15, mid_idle=0.15)

    body = _stream_body(
        TestClient(_build_app()),
        {"question": "hi", "datasource_id": 0},
    )

    assert "event: final_answer" in body and "first" in body
    assert "event: agent_thought" in body and "second" in body
    assert "event: done" in body
    # 心跳帧是注释行，绝不应被当成事件名
    assert "event: keepalive" not in body


def test_sse_keepalive_silent_when_events_flow(monkeypatch):
    """事件连续流出、无静默段时，不应产生多余心跳帧。"""
    monkeypatch.setattr(chat_api_mod, "_SSE_KEEPALIVE_INTERVAL", 0.05)

    async def _fast_agent(**kwargs):
        emit = kwargs["emit"]
        await emit("final_answer", {"text": "x"})
        await emit("agent_thought", {"text": "y"})
        return 0

    import src.chat.service.agent_runner as runner_mod

    monkeypatch.setattr(runner_mod, "run_agent_stream", _fast_agent)
    monkeypatch.setattr(runner_mod, "run_team_stream", _fast_agent)

    body = _stream_body(
        TestClient(_build_app()),
        {"question": "hi", "datasource_id": 0},
    )

    assert "event: final_answer" in body
    assert ": keepalive" not in body, f"不应有心跳帧，body={body!r}"
