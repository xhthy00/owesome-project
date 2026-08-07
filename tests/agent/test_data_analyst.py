"""DataAnalystAgent 端到端测试。

用 Fake LLM + monkeypatch 数据源底层，跑一次完整的 ReAct 链路：
    list_tables -> describe_table -> execute_sql -> terminate
验证最终回复包含面向用户的结论，且每一步的 observation 都真的被回灌到下一轮 prompt。
"""

from __future__ import annotations

import asyncio

from src.agent.core.agent import AgentMessage
from src.agent.expand.data_analyst import DataAnalystAgent, build_data_analyst
from src.agent.expand.user_proxy import UserProxyAgent
from src.agent.resource.tool import business as biz
from src.agent.resource.tool.business import build_default_toolpack


class _ScriptedLlm:
    def __init__(self, replies: list[str]) -> None:
        self._q = list(replies)
        self.calls: list[list[dict[str, str]]] = []

    async def chat(self, messages: list[dict[str, str]]) -> str:
        self.calls.append(messages)
        return self._q.pop(0)


def _run(coro):
    return asyncio.run(coro)


_FAKE_SCHEMA = [
    {
        "name": "users",
        "comment": "用户表",
        "fields": [
            {"name": "id", "type": "int", "comment": "用户 ID"},
            {"name": "name", "type": "varchar", "comment": "姓名"},
            {"name": "age", "type": "int", "comment": "年龄"},
        ],
    },
    {
        "name": "orders",
        "comment": "订单表",
        "fields": [
            {"name": "id", "type": "int", "comment": "订单 ID"},
            {"name": "user_id", "type": "int", "comment": "用户 ID"},
            {"name": "amount", "type": "numeric", "comment": "金额"},
        ],
    },
]


def _patch_datasource(monkeypatch, execute_sql_impl):
    monkeypatch.setattr(
        biz,
        "_load_datasource",
        lambda ds_id, workspace_oid=None: ("pg", {}, f"ds_{ds_id}"),
    )

    def _fake_get_schema_info(db_type, config):
        return _FAKE_SCHEMA

    import src.datasource.db.db as db_mod
    monkeypatch.setattr(db_mod, "get_schema_info", _fake_get_schema_info)
    monkeypatch.setattr(db_mod, "execute_sql", execute_sql_impl)


def test_data_analyst_full_happy_path(monkeypatch):
    executed_sqls: list[str] = []

    def fake_execute_sql(db_type, config, sql):
        executed_sqls.append(sql)
        return True, "ok", {
            "columns": ["total"],
            "rows": [[42]],
        }

    _patch_datasource(monkeypatch, fake_execute_sql)

    llm = _ScriptedLlm(
        [
            '{"thoughts": "先看有哪些表", "tool": "list_tables", "args": {}}',
            '{"thoughts": "查看 users 结构", "tool": "describe_table", "args": {"table_name": "users"}}',
            '{"thoughts": "数数用户", "tool": "execute_sql", "args": {"sql": "SELECT COUNT(*) AS total FROM users"}}',
            (
                '{"thoughts": "结果已到手", "tool": "terminate", "args": '
                '{"final_answer": "用户共有 42 人。"}}'
            ),
        ]
    )

    agent = build_data_analyst(llm_client=llm, datasource_id=1, user_id=7)

    reply = _run(
        agent.generate_reply(
            received_message=AgentMessage(content="用户有多少人？", role="user"),
            sender=UserProxyAgent(),
        )
    )

    assert reply.action_report.terminate is True
    assert "42 人" in reply.content
    assert "SELECT" not in reply.content
    assert reply.rounds == 4

    assert executed_sqls == ["SELECT COUNT(*) AS total FROM users"]

    round2 = llm.calls[1]
    assert any("observation from list_tables" in m["content"] and "users" in m["content"] for m in round2)
    round3 = llm.calls[2]
    assert any("observation from describe_table" in m["content"] and "age" in m["content"] for m in round3)
    round4 = llm.calls[3]
    assert any("observation from execute_sql" in m["content"] and "42" in m["content"] for m in round4)


def test_data_analyst_retries_on_sql_error_via_observation(monkeypatch):
    calls = {"n": 0}

    def fake_execute_sql(db_type, config, sql):
        calls["n"] += 1
        if "WHERE age > 100000" in sql:
            return False, 'syntax/column misuse: condition unrealistic', None
        return True, "ok", {"columns": ["cnt"], "rows": [[7]]}

    _patch_datasource(monkeypatch, fake_execute_sql)

    llm = _ScriptedLlm(
        [
            '{"tool": "execute_sql", "args": {"sql": "SELECT COUNT(*) AS cnt FROM users WHERE age > 100000"}}',
            '{"tool": "execute_sql", "args": {"sql": "SELECT COUNT(*) AS cnt FROM users WHERE age > 30"}}',
            '{"tool": "terminate", "args": {"final_answer": "年龄 > 30 的用户有 7 人。"}}',
        ]
    )
    agent = build_data_analyst(llm_client=llm, datasource_id=1)

    reply = _run(
        agent.generate_reply(
            received_message=AgentMessage(content="年龄 > 30 的用户数？", role="user"),
            sender=UserProxyAgent(),
        )
    )

    assert reply.action_report.terminate is True
    assert "7 人" in reply.content
    assert calls["n"] == 2
    round2 = llm.calls[1]
    assert any(
        "observation from execute_sql" in m["content"]
        and "SQL 执行失败" in m["content"]
        for m in round2
    )


def test_build_data_analyst_binds_runtime_context():
    pack = build_default_toolpack(datasource_id=9, user_id=123)
    assert pack.bindings == {"datasource_id": 9, "user_id": 123, "workspace_oid": 1}

    rendered = pack.render_prompt()
    assert "datasource_id" not in rendered
    assert "user_id" not in rendered
    assert "list_tables" in rendered
    assert "terminate" in rendered


def test_data_analyst_profile_has_tools_prompt_placeholder():
    assert "{{tools_prompt}}" in DataAnalystAgent.profile.desc
    assert "{{scope_constraints}}" in DataAnalystAgent.profile.desc


def test_data_analyst_injects_scope_constraints_in_system_prompt():
    llm = _ScriptedLlm(['{"tool": "terminate", "args": {"final_answer": "ok"}}'])
    agent = build_data_analyst(llm_client=llm, datasource_id=1)

    _run(
        agent.generate_reply(
            received_message=AgentMessage(
                content="查询小题得分率",
                role="user",
                context={
                    "constraints": {
                        "target_school": "南京市第一中学",
                        "target_student": None,
                        "required_keywords": ["数学", "期末"],
                        "locked_tables": [],
                    }
                },
            ),
            sender=UserProxyAgent(),
        )
    )

    system_msg = llm.calls[0][0]["content"]
    assert "南京市第一中学" in system_msg
    assert "数学" in system_msg
    assert "范围必须与用户指定范围一致" in system_msg
    assert "{{scope_constraints}}" not in system_msg
