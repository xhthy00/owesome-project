"""LangGraph Team 编排：结构与其它路径冒烟。"""

from __future__ import annotations

import asyncio

import pytest

from src.agent.resource.tool import business as biz
from src.chat.schemas import ChatRequest
from src.chat.service.agent_runner import run_team_stream
from src.chat.service.team_graph.graph import build_team_graph


def test_team_graph_structure():
    compiled = build_team_graph()
    nodes = list(compiled.get_graph().nodes.keys())
    assert "__start__" in nodes
    assert "__end__" in nodes
    for name in (
        "planner",
        "sub_tasks_loop",
        "persist_failure",
        "charter",
        "summarizer",
        "persist_success",
    ):
        assert name in nodes


class _ScriptedLlm:
    def __init__(self, replies: list[str]) -> None:
        self._q = list(replies)

    async def chat(self, messages: list[dict[str, str]]) -> str:
        if not self._q:
            raise AssertionError("LLM queue exhausted")
        return self._q.pop(0)


_FAKE_SCHEMA = [
    {
        "name": "users",
        "comment": "",
        "fields": [
            {"name": "id", "type": "int", "comment": ""},
            {"name": "name", "type": "varchar", "comment": ""},
        ],
    }
]


def _patch_db(monkeypatch, exec_sql_fn):
    monkeypatch.setattr(
        biz,
        "_load_datasource",
        lambda ds_id, workspace_oid=None: ("pg", {}, f"ds{ds_id}"),
    )
    import src.datasource.db.db as db_mod

    monkeypatch.setattr(db_mod, "get_schema_info", lambda db_type, config: _FAKE_SCHEMA)
    monkeypatch.setattr(db_mod, "execute_sql", exec_sql_fn)


def _collect_events():
    events: list[tuple[str, dict]] = []

    async def emit(event: str, data: dict) -> None:
        events.append((event, dict(data)))

    return events, emit


def _run(coro):
    return asyncio.run(coro)


def test_langgraph_single_sub_task_happy_path(monkeypatch):
    """与 test_team_single_sub_task_happy_path 对齐（默认已为 LangGraph）。"""
    _patch_db(
        monkeypatch,
        lambda *_a, **_kw: (True, "ok", {"columns": ["n"], "rows": [[5]]}),
    )
    llm = _ScriptedLlm(
        [
            '{"plans":["有多少用户"]}',
            '{"tool":"execute_sql","args":{"sql":"SELECT COUNT(*) AS n FROM users"}}',
            '{"tool":"terminate","args":{"final_answer":"5 人"}}',
            '{"chart_type":"table"}',
            "共有 5 位用户。",
        ]
    )
    events, emit = _collect_events()
    req = ChatRequest(question="有多少用户", datasource_id=1)
    record_id = _run(
        run_team_stream(
            request=req,
            current_user_id=1,
            emit=emit,
            llm_client=llm,
            persist=False,
        )
    )
    assert record_id == 0
    names = [e for e, _ in events]
    plan_payload = next(p for e, p in events if e == "plan")
    assert plan_payload["plans"] == ["有多少用户"]
    updates = [p for e, p in events if e == "plan_update"]
    assert len(updates) == 2
    assert updates[0]["state"] == "running"
    assert updates[1]["state"] == "ok"
    assert names.count("chart") == 1
    assert names.count("summary") == 1
    summary = next(p for e, p in events if e == "summary")
    assert summary["content"] == "共有 5 位用户。"


@pytest.fixture(autouse=True)
def _reset_team_orchestrator_env(monkeypatch):
    """清除显式 ``TEAM_ORCHESTRATOR`` 并刷新 settings 缓存，避免污染其它测试文件。"""
    yield
    from src.common.core import config as cfg_mod

    monkeypatch.delenv("TEAM_ORCHESTRATOR", raising=False)
    cfg_mod.get_settings.cache_clear()
