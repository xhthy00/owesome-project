"""run_team_stream 的集成测试：Planner → N × DataAnalyst → Charter → Summarizer。

覆盖目标：
1. 简单问题（Planner 给 1 个 sub_task）：全链路跑通，事件顺序正确；
2. 复杂问题（Planner 给 3 个 sub_task）：串行跑 3 个 DataAnalyst，Chart 基于最后
   一个成功 sub_task，Summarizer 综合所有 sub_task；
3. 多 sub_task 中 1 个失败：``plan_update`` 发 ok/error 混合，Chart/Summary 仍运行；
4. 所有 sub_task 都失败：跳过 Chart/Summarizer，发 ``error``；
5. Planner 失败 → 回落为 1 个 sub_task（原问题），继续走；
6. Charter LLM 乱码 → chart_type=table 且不阻塞 Summarizer；
7. Summarizer 抛异常 → 用 DataAnalyst 原文回落。
"""

from __future__ import annotations

import asyncio

from src.agent.resource.tool import business as biz
from src.chat.schemas import ChatRequest
from src.chat.service.agent_runner import run_team_stream


class _ScriptedLlm:
    """所有 Agent 共享的顺序消费队列：Planner 1 条 → N × (DataAnalyst K 轮) → Charter 1 条 → Summarizer 1 条。"""

    def __init__(self, replies: list[str]) -> None:
        self._q = list(replies)
        self.calls: list[list[dict[str, str]]] = []

    async def chat(self, messages: list[dict[str, str]]) -> str:
        self.calls.append(messages)
        if not self._q:
            raise AssertionError(
                f"LLM queue exhausted after {len(self.calls)} calls - "
                "test script under-specified"
            )
        return self._q.pop(0)


def _run(coro):
    return asyncio.run(coro)


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


# --------------------------------------------------------------------------- #
# 1. 简单问题（1 个 sub_task）
# --------------------------------------------------------------------------- #


def test_team_single_sub_task_happy_path(monkeypatch):
    _patch_db(
        monkeypatch,
        lambda *_a, **_kw: (True, "ok", {"columns": ["n"], "rows": [[5]]}),
    )
    llm = _ScriptedLlm(
        [
            # Planner
            '{"plans":["有多少用户"]}',
            # DataAnalyst
            '{"tool":"execute_sql","args":{"sql":"SELECT COUNT(*) AS n FROM users"}}',
            '{"tool":"terminate","args":{"final_answer":"5 人"}}',
            # Charter
            '{"chart_type":"table"}',
            # Summarizer
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
    assert "error" not in names

    summary = next(p for e, p in events if e == "summary")
    assert summary["content"] == "共有 5 位用户。"


# --------------------------------------------------------------------------- #
# 2. 复杂问题（多 sub_task 全成功）
# --------------------------------------------------------------------------- #


def test_team_multi_sub_tasks_all_succeed(monkeypatch):
    _patch_db(
        monkeypatch,
        lambda *_a, **_kw: (True, "ok", {"columns": ["amount"], "rows": [[100]]}),
    )
    llm = _ScriptedLlm(
        [
            # Planner 拆 3 个
            '{"plans":["Q2 销售额","Q3 销售额","对比原因"]}',
            # DataAnalyst × 3（每个 2 轮）
            '{"tool":"execute_sql","args":{"sql":"SELECT amount FROM sales WHERE q=2"}}',
            '{"tool":"terminate","args":{"final_answer":"Q2=100"}}',
            '{"tool":"execute_sql","args":{"sql":"SELECT amount FROM sales WHERE q=3"}}',
            '{"tool":"terminate","args":{"final_answer":"Q3=100"}}',
            '{"tool":"execute_sql","args":{"sql":"SELECT amount FROM sales WHERE change=true"}}',
            '{"tool":"terminate","args":{"final_answer":"持平"}}',
            # Charter
            '{"chart_type":"bar","chart_config":{"x":"q","y":["amount"]}}',
            # Summarizer
            "Q2 与 Q3 销售持平，均为 100。",
        ]
    )
    events, emit = _collect_events()

    req = ChatRequest(question="对比 Q2/Q3 销售并分析原因", datasource_id=1)
    _run(
        run_team_stream(
            request=req,
            current_user_id=1,
            emit=emit,
            llm_client=llm,
            persist=False,
        )
    )

    plan_payload = next(p for e, p in events if e == "plan")
    assert len(plan_payload["plans"]) == 3

    updates = [p for e, p in events if e == "plan_update"]
    # 每个 sub_task 一条 running + 一条 ok = 6 条
    assert len(updates) == 6
    running = [u for u in updates if u["state"] == "running"]
    ok = [u for u in updates if u["state"] == "ok"]
    assert len(running) == 3 and len(ok) == 3
    assert [u["index"] for u in running] == [0, 1, 2]

    chart = next(p for e, p in events if e == "chart")
    assert chart["chart_type"] == "bar"

    summary = next(p for e, p in events if e == "summary")
    assert "100" in summary["content"]

    # Summarizer 的 prompt 必须看到所有 3 个子任务
    summarizer_call = llm.calls[-1][0]["content"]  # system prompt
    assert "子任务 1" in summarizer_call
    assert "子任务 2" in summarizer_call
    assert "子任务 3" in summarizer_call


# --------------------------------------------------------------------------- #
# 3. 多 sub_task 中部分失败
# --------------------------------------------------------------------------- #


def test_team_multi_sub_tasks_partial_failure(monkeypatch):
    """第 2 个 sub_task 的 DataAnalyst 不 terminate → 该 sub_task 失败，但
    其他 sub_task 成功，Chart/Summary 仍应基于成功的最后一个跑起来。"""
    _patch_db(
        monkeypatch,
        lambda *_a, **_kw: (True, "ok", {"columns": ["n"], "rows": [[7]]}),
    )

    # list_tables 成功且两轮触顶时会触发 react_agent 的「自动收敛」视为成功；
    # ToolPack 持有 FunctionTool 实例，需替换 ``list_tables._fn`` 而非模块名。
    _lt_tool = biz.list_tables
    _orig_lt_fn = _lt_tool._fn
    _list_tables_count: list[int] = [0]

    def _list_tables_fail_twice(*args: object, **kwargs: object):
        _list_tables_count[0] += 1
        if _list_tables_count[0] <= 2:
            raise RuntimeError("forced list_tables failure for sub2")
        return _orig_lt_fn(*args, **kwargs)

    monkeypatch.setattr(_lt_tool, "_fn", _list_tables_fail_twice)

    import src.agent.expand.data_analyst as da_mod
    import src.chat.service.agent_runner as runner_mod

    orig = da_mod.build_data_analyst

    def capped(*args, **kwargs):
        # max=2 让"只调 list_tables 不 terminate"的 sub_task 快速触顶
        kwargs["max_react_rounds"] = 2
        return orig(*args, **kwargs)

    monkeypatch.setattr(da_mod, "build_data_analyst", capped)
    monkeypatch.setattr(runner_mod, "build_data_analyst", capped)

    llm = _ScriptedLlm(
        [
            # Planner
            '{"plans":["sub1","sub2","sub3"]}',
            # sub1: 成功
            '{"tool":"execute_sql","args":{"sql":"SELECT 1 AS n"}}',
            '{"tool":"terminate","args":{"final_answer":"ok1"}}',
            # sub2: 只调 list_tables 不 terminate，两轮触顶
            '{"tool":"list_tables","args":{}}',
            '{"tool":"list_tables","args":{}}',
            # sub3: 成功
            '{"tool":"execute_sql","args":{"sql":"SELECT 3 AS n"}}',
            '{"tool":"terminate","args":{"final_answer":"ok3"}}',
            # Charter（基于 sub3）
            '{"chart_type":"table"}',
            # Summarizer
            "部分查询完成",
        ]
    )
    events, emit = _collect_events()
    req = ChatRequest(question="q", datasource_id=1)
    _run(
        run_team_stream(
            request=req,
            current_user_id=1,
            emit=emit,
            llm_client=llm,
            persist=False,
        )
    )

    updates = [p for e, p in events if e == "plan_update"]
    states_by_idx = {}
    for u in updates:
        if u["state"] != "running":
            states_by_idx[u["index"]] = u["state"]
    assert states_by_idx == {0: "ok", 1: "error", 2: "ok"}

    # Chart / Summary 仍应发出（基于 sub3 的成功结果）
    assert any(e == "chart" for e, _ in events)
    assert any(e == "summary" for e, _ in events)


# --------------------------------------------------------------------------- #
# 4. 所有 sub_task 都失败
# --------------------------------------------------------------------------- #


def test_team_all_sub_tasks_fail_skips_chart_and_summary(monkeypatch):
    _patch_db(
        monkeypatch,
        lambda *_a, **_kw: (True, "ok", {"columns": [], "rows": []}),
    )

    _lt_tool = biz.list_tables

    def _list_tables_always_fail(*_a: object, **_kw: object) -> None:
        raise RuntimeError("forced list_tables failure")

    monkeypatch.setattr(_lt_tool, "_fn", _list_tables_always_fail)

    import src.agent.expand.data_analyst as da_mod
    import src.chat.service.agent_runner as runner_mod

    orig = da_mod.build_data_analyst

    def capped(*args, **kwargs):
        kwargs["max_react_rounds"] = 2
        return orig(*args, **kwargs)

    monkeypatch.setattr(da_mod, "build_data_analyst", capped)
    monkeypatch.setattr(runner_mod, "build_data_analyst", capped)

    llm = _ScriptedLlm(
        [
            '{"plans":["sub1","sub2"]}',
            '{"tool":"list_tables","args":{}}',
            '{"tool":"list_tables","args":{}}',
            '{"tool":"list_tables","args":{}}',
            '{"tool":"list_tables","args":{}}',
        ]
    )
    events, emit = _collect_events()
    req = ChatRequest(question="q", datasource_id=1)
    _run(
        run_team_stream(
            request=req,
            current_user_id=1,
            emit=emit,
            llm_client=llm,
            persist=False,
        )
    )

    names = [e for e, _ in events]
    assert "error" in names
    assert "chart" not in names, "全部失败时不应跑 Charter"
    assert "summary" not in names, "全部失败时不应跑 Summarizer"


# --------------------------------------------------------------------------- #
# 5. Planner 失败回落
# --------------------------------------------------------------------------- #


def test_team_planner_garbage_falls_back_to_single_plan(monkeypatch):
    _patch_db(
        monkeypatch,
        lambda *_a, **_kw: (True, "ok", {"columns": ["n"], "rows": [[1]]}),
    )
    llm = _ScriptedLlm(
        [
            # Planner 给乱码 → 回落为 [原问题]
            "this is not json",
            # DataAnalyst
            '{"tool":"execute_sql","args":{"sql":"SELECT 1 AS n"}}',
            '{"tool":"terminate","args":{"final_answer":"1 条"}}',
            # Charter
            '{"chart_type":"table"}',
            # Summarizer
            "查到 1 条数据。",
        ]
    )
    events, emit = _collect_events()
    req = ChatRequest(question="原始问题", datasource_id=1)
    _run(
        run_team_stream(
            request=req,
            current_user_id=1,
            emit=emit,
            llm_client=llm,
            persist=False,
        )
    )

    plan_payload = next(p for e, p in events if e == "plan")
    assert plan_payload["plans"] == ["原始问题"]
    assert any(e == "summary" for e, _ in events)


# --------------------------------------------------------------------------- #
# 6. Charter 回落 + Summarizer 异常回落
# --------------------------------------------------------------------------- #


def test_team_charter_garbage_falls_back_and_summary_continues(monkeypatch):
    _patch_db(
        monkeypatch,
        lambda *_a, **_kw: (True, "ok", {"columns": ["c"], "rows": [[1]]}),
    )
    llm = _ScriptedLlm(
        [
            '{"plans":["q"]}',
            '{"tool":"execute_sql","args":{"sql":"SELECT 1 AS c"}}',
            '{"tool":"terminate","args":{"final_answer":"ok"}}',
            "not json",  # Charter
            "就 1 条。",   # Summarizer
        ]
    )
    events, emit = _collect_events()
    req = ChatRequest(question="q", datasource_id=1)
    _run(
        run_team_stream(
            request=req,
            current_user_id=1,
            emit=emit,
            llm_client=llm,
            persist=False,
        )
    )

    chart = next(p for e, p in events if e == "chart")
    assert chart["chart_type"] == "table"
    assert any(e == "summary" for e, _ in events)


def test_team_summarizer_failure_falls_back_to_data_analyst_reply(monkeypatch):
    _patch_db(
        monkeypatch,
        lambda *_a, **_kw: (True, "ok", {"columns": ["c"], "rows": [[1]]}),
    )

    class _Flaky:
        def __init__(self, good: list[str]) -> None:
            self._q = list(good)

        async def chat(self, messages):
            if self._q:
                return self._q.pop(0)
            raise RuntimeError("summarizer exploded")

    llm = _Flaky(
        [
            '{"plans":["q"]}',
            '{"tool":"execute_sql","args":{"sql":"SELECT 1 AS c"}}',
            '{"tool":"terminate","args":{"final_answer":"DA 原始结论"}}',
            '{"chart_type":"table"}',
        ]
    )
    events, emit = _collect_events()
    req = ChatRequest(question="q", datasource_id=1)
    _run(
        run_team_stream(
            request=req,
            current_user_id=1,
            emit=emit,
            llm_client=llm,
            persist=False,
        )
    )

    summary = next(p for e, p in events if e == "summary")
    assert summary["content"] == "DA 原始结论"
    assert any(
        e == "agent_speak" and p.get("agent") == "Summarizer" and p.get("status") == "error"
        for e, p in events
    )


# --------------------------------------------------------------------------- #
# 8. sub_task_index 注入（前端按子任务归组的契约）
# --------------------------------------------------------------------------- #


def test_team_tool_events_carry_sub_task_index(monkeypatch):
    """多 sub_task 场景下，每个 sub_task 内部产生的 tool_call / tool_result 事件
    payload 必须带 sub_task_index，且和该 sub_task 的 plan 索引一致——这是前端按
    子任务折叠展示的契约。单 Agent 模式 (run_agent_stream) 下不应有这个字段。"""
    _patch_db(
        monkeypatch,
        lambda *_a, **_kw: (True, "ok", {"columns": ["n"], "rows": [[1]]}),
    )
    llm = _ScriptedLlm(
        [
            '{"plans":["sub-A","sub-B"]}',
            # sub 0
            '{"tool":"execute_sql","args":{"sql":"SELECT 1 AS n"}}',
            '{"tool":"terminate","args":{"final_answer":"A"}}',
            # sub 1
            '{"tool":"execute_sql","args":{"sql":"SELECT 2 AS n"}}',
            '{"tool":"terminate","args":{"final_answer":"B"}}',
            '{"chart_type":"table"}',
            "综合结论",
        ]
    )
    events, emit = _collect_events()
    req = ChatRequest(question="q", datasource_id=1)
    _run(
        run_team_stream(
            request=req,
            current_user_id=1,
            emit=emit,
            llm_client=llm,
            persist=False,
        )
    )

    tool_calls = [p for e, p in events if e == "tool_call"]
    tool_results = [p for e, p in events if e == "tool_result"]
    # terminate 只发 tool_result，不发 tool_call（是终止信号，不是真"调用"）
    # 每 sub_task：1 条 tool_call（execute_sql） + 2 条 tool_result（execute_sql + terminate）
    assert len(tool_calls) == 2 and len(tool_results) == 4

    assert tool_calls[0].get("sub_task_index") == 0
    assert tool_calls[1].get("sub_task_index") == 1
    assert [r.get("sub_task_index") for r in tool_results] == [0, 0, 1, 1]


def test_agent_mode_tool_events_have_no_sub_task_index(monkeypatch):
    """单 Agent 模式（非 team）下，payload 里不应出现 sub_task_index——避免前端
    在 agent 模式下误以为当前结果属于某个子任务。"""
    from src.chat.service.agent_runner import run_agent_stream

    _patch_db(
        monkeypatch,
        lambda *_a, **_kw: (True, "ok", {"columns": ["n"], "rows": [[1]]}),
    )
    llm = _ScriptedLlm(
        [
            '{"tool":"execute_sql","args":{"sql":"SELECT 1 AS n"}}',
            '{"tool":"terminate","args":{"final_answer":"done"}}',
        ]
    )
    events, emit = _collect_events()
    req = ChatRequest(question="q", datasource_id=1)
    _run(
        run_agent_stream(
            request=req,
            current_user_id=1,
            emit=emit,
            llm_client=llm,
            persist=False,
        )
    )

    tool_calls = [p for e, p in events if e == "tool_call"]
    assert tool_calls, "至少要有 1 条 tool_call"
    assert all("sub_task_index" not in p for p in tool_calls)


# --------------------------------------------------------------------------- #
# 9. schema 探索类问题（不 execute_sql）仍视为成功
# --------------------------------------------------------------------------- #


def test_team_schema_exploration_without_execute_sql_is_success(monkeypatch):
    """Team 模式下，Planner 拆出的 sub_task 若是元数据查询（"XX 表结构"），
    DataAnalyst 只靠 describe_table + sample_rows 回答也算成功：
    - plan_update 应为 ok 而非 error；
    - 不发 error 事件；
    - Charter/Summarizer 仍走完，summary 事件正常下发。
    """
    _patch_db(
        monkeypatch,
        lambda *_a, **_kw: (True, "ok", {"columns": ["id", "name"], "rows": [[1, "a"]]}),
    )
    llm = _ScriptedLlm(
        [
            '{"plans":["users 表有哪些字段"]}',
            '{"tool":"describe_table","args":{"table_name":"users"}}',
            '{"tool":"sample_rows","args":{"table_name":"users"}}',
            '{"tool":"terminate","args":{"final_answer":"users 表含 id/name 两列"}}',
            '{"chart_type":"table"}',
            "users 表结构如上：id + name 两列。",
        ]
    )
    events, emit = _collect_events()

    req = ChatRequest(question="users 表有哪些字段", datasource_id=1)
    _run(
        run_team_stream(
            request=req,
            current_user_id=1,
            emit=emit,
            llm_client=llm,
            persist=False,
        )
    )

    names = [e for e, _ in events]
    assert "error" not in names, f"schema 探索不应触发 error：{names}"

    updates = [p for e, p in events if e == "plan_update"]
    ok_updates = [u for u in updates if u.get("state") == "ok"]
    assert len(ok_updates) == 1, (
        f"应有 1 条 plan_update(ok)，实际：{[(u.get('state'), u.get('error')) for u in updates]}"
    )
    # 没 execute_sql → row_count 兜底为 0，sql 为空串
    assert ok_updates[0]["row_count"] == 0
    assert ok_updates[0]["sql"] == ""

    assert "summary" in names, "Charter/Summarizer 仍应照常执行"
    assert "final_answer" in names


def test_team_routes_sub_task_to_tool_expert(monkeypatch):
    """Planner 指定 sub_task_agent=ToolExpert 时，应路由到 ToolAgent。"""
    llm = _ScriptedLlm(
        [
            '{"plans":['
            '{"task":"计算 (15*12)+30","sub_task_agent":"ToolExpert"},'
            '{"task":"给出一句结论","sub_task_agent":"DataAnalyst"}'
            "]}",
            '{"tool":"calculate","args":{"expression":"(15*12)+30"}}',
            '{"tool":"terminate","args":{"final_answer":"210"}}',
            '{"tool":"terminate","args":{"final_answer":"已计算完成"}}',
            '{"chart_type":"table"}',
            "最终结果为 210。",
        ]
    )
    events, emit = _collect_events()
    req = ChatRequest(question="先计算再总结", datasource_id=1)

    _run(
        run_team_stream(
            request=req,
            current_user_id=1,
            emit=emit,
            llm_client=llm,
            persist=False,
        )
    )

    plan_payload = next(p for e, p in events if e == "plan")
    assert plan_payload["sub_task_agents"] == ["ToolExpert", "DataAnalyst"]

    running_updates = [
        p for e, p in events if e == "plan_update" and p.get("state") == "running"
    ]
    assert running_updates[0]["sub_task_agent"] == "ToolExpert"
    assert running_updates[1]["sub_task_agent"] == "DataAnalyst"

    calc_calls = [
        p for e, p in events
        if e == "tool_call" and p.get("tool") == "calculate"
    ]
    assert calc_calls, "应看到 ToolExpert 发起 calculate 调用"
    assert calc_calls[0].get("agent") == "ToolExpert"
    assert calc_calls[0].get("sub_task_index") == 0

    # 与 DataAnalyst 一致：ToolExpert 子任务须独立广播 agent_speak
    te_speaks = [
        p for e, p in events if e == "agent_speak" and p.get("agent") == "ToolExpert"
    ]
    assert any(p.get("status") == "start" for p in te_speaks)
    assert any(p.get("status") == "end" for p in te_speaks)


def test_team_disable_tool_agent_falls_back_to_data_analyst(monkeypatch):
    """enable_tool_agent=False 时，Planner 标记 ToolExpert 也应回退 DataAnalyst。"""
    llm = _ScriptedLlm(
        [
            '{"plans":[{"task":"算一下 1+1","sub_task_agent":"ToolExpert"}]}',
            '{"tool":"terminate","args":{"final_answer":"2"}}',
            '{"chart_type":"table"}',
            "结果是 2。",
        ]
    )
    events, emit = _collect_events()
    req = ChatRequest(question="算一下 1+1", datasource_id=1)

    _run(
        run_team_stream(
            request=req,
            current_user_id=1,
            emit=emit,
            llm_client=llm,
            persist=False,
            enable_tool_agent=False,
        )
    )

    plan_payload = next(p for e, p in events if e == "plan")
    assert plan_payload["sub_task_agents"] == ["DataAnalyst"]
    running = next(p for e, p in events if e == "plan_update" and p.get("state") == "running")
    assert running["sub_task_agent"] == "DataAnalyst"
    # 回退后不应出现 ToolExpert 发言
    assert not any(
        e == "tool_call" and p.get("agent") == "ToolExpert"
        for e, p in events
    )


def test_team_report_event_carries_sub_task_index(monkeypatch):
    _patch_db(
        monkeypatch,
        lambda *_a, **_kw: (True, "ok", {"columns": [], "rows": []}),
    )
    llm = _ScriptedLlm(
        [
            '{"plans":["输出报告"]}',
            '{"tool":"render_html_report","args":{"title":"TeamReport","html":"<html><body>ok</body></html>"}}',
            '{"tool":"terminate","args":{"final_answer":"done"}}',
            '{"chart_type":"table"}',
            "报告已生成。",
        ]
    )
    events, emit = _collect_events()
    req = ChatRequest(question="输出报告", datasource_id=1)
    _run(
        run_team_stream(
            request=req,
            current_user_id=1,
            emit=emit,
            llm_client=llm,
            persist=False,
        )
    )

    report = next((p for e, p in events if e == "report"), None)
    assert report is not None
    assert report["title"] == "TeamReport"
    assert report.get("sub_task_index") == 0
