"""agent_runner 的集成测试：验证 SSE 事件桥接 + legacy 兼容事件 + 持久化 hook。

覆盖目标：
1. 完整 happy path：agent 跑 4 轮（list_tables → describe_table → execute_sql → terminate），
   发出的 SSE 事件清单与顺序正确；
2. execute_sql 成功时**自动补发** ``sql`` + ``result`` 两条老事件，无需改前端；
3. Agent 不 terminate（达上限）时，runner 发 ``error`` 事件 + record_id=0；
4. persist=False 时不访问 DB；persist=True 且无 conversation_id 时返回 0。
"""

from __future__ import annotations

import asyncio

import pytest

from src.agent.resource.tool import business as biz
from src.chat.schemas import ChatRequest
from src.chat.service.agent_runner import (
    _is_lockable_table,
    _maybe_emit_report,
    _on_tool_result,
    _RunConstraints,
    _RunState,
    _sql_hits_locked_tables,
    run_agent_stream,
)


class _ScriptedLlm:
    def __init__(self, replies: list[str]) -> None:
        self._q = list(replies)

    async def chat(self, messages: list[dict[str, str]]) -> str:
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


def test_happy_path_emits_full_event_sequence(monkeypatch):
    _patch_db(
        monkeypatch,
        lambda db_type, config, sql: (True, "ok", {"columns": ["n"], "rows": [[5]]}),
    )
    llm = _ScriptedLlm(
        [
            '{"thoughts": "看表", "tool": "list_tables", "args": {}}',
            '{"thoughts": "看列", "tool": "describe_table", "args": {"table_name": "users"}}',
            '{"thoughts": "查", "tool": "execute_sql", "args": {"sql": "SELECT COUNT(*) AS n FROM users"}}',
            '{"thoughts": "结束", "tool": "terminate", "args": {"final_answer": "共 5 人"}}',
        ]
    )
    events, emit = _collect_events()

    req = ChatRequest(question="有多少用户", datasource_id=1)
    record_id = _run(
        run_agent_stream(
            request=req,
            current_user_id=7,
            emit=emit,
            llm_client=llm,
            persist=False,
        )
    )

    assert record_id == 0
    names = [e for e, _ in events]
    assert "agent_thought" in names
    assert names.count("tool_call") == 3
    assert names.count("tool_result") == 4
    assert "sql" in names and "result" in names
    assert "final_answer" in names
    assert "summary" in names  # agent 模式也走权威对齐后处理
    assert "error" not in names

    sql_event = next(p for e, p in events if e == "sql")
    assert sql_event["sql"] == "SELECT COUNT(*) AS n FROM users"
    result_event = next(p for e, p in events if e == "result")
    assert result_event == {"columns": ["n"], "rows": [[5]], "row_count": 1}

    i_tool_result_sql = next(
        i for i, (e, p) in enumerate(events)
        if e == "tool_result" and p.get("tool") == "execute_sql"
    )
    i_sql = next(i for i, (e, _) in enumerate(events) if e == "sql")
    i_result = next(i for i, (e, _) in enumerate(events) if e == "result")
    assert i_tool_result_sql < i_sql < i_result


def test_execute_sql_failure_is_fed_back_not_emitted_as_legacy_sql(monkeypatch):
    call_count = {"n": 0}

    def fake_exec(db_type, config, sql):
        call_count["n"] += 1
        if "age > 999" in sql:
            return False, "column age has no row > 999", None
        return True, "ok", {"columns": ["n"], "rows": [[3]]}

    _patch_db(monkeypatch, fake_exec)
    llm = _ScriptedLlm(
        [
            '{"tool": "execute_sql", "args": {"sql": "SELECT COUNT(*) n FROM users WHERE age > 999"}}',
            '{"tool": "execute_sql", "args": {"sql": "SELECT COUNT(*) n FROM users WHERE age > 10"}}',
            '{"tool": "terminate", "args": {"final_answer": "3 人"}}',
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

    sql_events = [p for e, p in events if e == "sql"]
    result_events = [p for e, p in events if e == "result"]
    assert len(sql_events) == 1
    assert sql_events[0]["sql"] == "SELECT COUNT(*) n FROM users WHERE age > 10"
    assert len(result_events) == 1
    assert call_count["n"] == 2


def test_max_rounds_with_valid_observation_auto_converges(monkeypatch):
    """达轮数上限但最后观察有效时，ReAct 会自动收敛为 final_answer（非 error）。

    runner 再对结论做权威人数对齐，并 emit summary（与 team 口径一致）。
    """
    _patch_db(
        monkeypatch,
        lambda *a, **kw: (True, "ok", {"columns": [], "rows": []}),
    )
    llm = _ScriptedLlm(['{"tool": "list_tables", "args": {}}'] * 20)
    events, emit = _collect_events()

    req = ChatRequest(question="q", datasource_id=1)

    import src.agent.expand.data_analyst as da_mod
    orig = da_mod.build_data_analyst

    def capped_factory(*args, **kwargs):
        kwargs["max_react_rounds"] = 3
        return orig(*args, **kwargs)

    monkeypatch.setattr(da_mod, "build_data_analyst", capped_factory)
    import src.chat.service.agent_runner as runner_mod
    monkeypatch.setattr(runner_mod, "build_data_analyst", capped_factory)

    record_id = _run(
        run_agent_stream(
            request=req,
            current_user_id=1,
            emit=emit,
            llm_client=llm,
            persist=False,
        )
    )

    assert record_id == 0
    names = [e for e, _ in events]
    assert "final_answer" in names
    assert "summary" in names
    assert "error" not in names


def test_max_rounds_without_valid_observation_emits_error(monkeypatch):
    """达上限且无有效观察时，仍应发 error、不发 final_answer。"""
    _patch_db(
        monkeypatch,
        lambda *a, **kw: (False, "boom", None),
    )
    # 故意让工具名非法，Observation 失败且无法收敛
    llm = _ScriptedLlm(
        ['{"tool": "not_a_real_tool", "args": {}}'] * 20
    )
    events, emit = _collect_events()
    req = ChatRequest(question="q", datasource_id=1)

    import src.agent.expand.data_analyst as da_mod
    orig = da_mod.build_data_analyst

    def capped_factory(*args, **kwargs):
        kwargs["max_react_rounds"] = 2
        return orig(*args, **kwargs)

    monkeypatch.setattr(da_mod, "build_data_analyst", capped_factory)
    import src.chat.service.agent_runner as runner_mod
    monkeypatch.setattr(runner_mod, "build_data_analyst", capped_factory)

    record_id = _run(
        run_agent_stream(
            request=req,
            current_user_id=1,
            emit=emit,
            llm_client=llm,
            persist=False,
        )
    )
    assert record_id == 0
    assert any(e == "error" for e, _ in events)
    assert not any(e == "final_answer" for e, _ in events)


def test_persist_false_never_touches_database(monkeypatch):
    _patch_db(
        monkeypatch,
        lambda *a, **kw: (True, "ok", {"columns": ["x"], "rows": [[1]]}),
    )

    import src.chat.crud.chat as chat_crud
    def _boom(**_kwargs):
        pytest.fail("create_conversation_record should NOT be called when persist=False")
    monkeypatch.setattr(chat_crud, "create_conversation_record", _boom)

    llm = _ScriptedLlm(
        [
            '{"tool": "execute_sql", "args": {"sql": "SELECT 1 AS x"}}',
            '{"tool": "terminate", "args": {"final_answer": "ok"}}',
        ]
    )
    _events, emit = _collect_events()

    req = ChatRequest(question="q", datasource_id=1, conversation_id=42)
    record_id = _run(
        run_agent_stream(
            request=req,
            current_user_id=1,
            emit=emit,
            llm_client=llm,
            persist=False,
        )
    )
    assert record_id == 0


def test_schema_exploration_without_execute_sql_is_success(monkeypatch):
    """只做 schema 探索（list_tables → describe_table → sample_rows → terminate），
    从不调 execute_sql，也应判定为成功：不发 error 事件、final_answer 正常下发。

    背景：用户问"有哪些学生相关的表" / "XX 表长啥样" 这类元数据问题时，DataAnalyst
    通过 describe_table + sample_rows 就能给出完整答案，无需真正 SELECT。先前
    runner 把"成功"硬等于"调过 execute_sql"，会在用户已经拿到漂亮 markdown 的
    同时误发 error 事件，前端就出现"执行失败 + 查询结果"的矛盾 UI。
    """
    sample_exec_calls: list[str] = []

    def fake_exec(db_type, config, sql):
        # 只有 sample_rows 内部会走 execute_sql，不应被 LLM 显式调用
        sample_exec_calls.append(sql)
        return True, "ok", {"columns": ["id", "name"], "rows": [[1, "a"]]}

    _patch_db(monkeypatch, fake_exec)
    llm = _ScriptedLlm(
        [
            '{"thoughts": "看表", "tool": "list_tables", "args": {}}',
            '{"thoughts": "看字段", "tool": "describe_table", "args": {"table_name": "users"}}',
            '{"thoughts": "看样例", "tool": "sample_rows", "args": {"table_name": "users"}}',
            '{"thoughts": "回答", "tool": "terminate", "args": {"final_answer": "`users` 表含 id/name 两列。"}}',
        ]
    )
    events, emit = _collect_events()

    req = ChatRequest(question="有哪些用户相关的表", datasource_id=1)
    _run(
        run_agent_stream(
            request=req,
            current_user_id=1,
            emit=emit,
            llm_client=llm,
            persist=False,
        )
    )

    names = [e for e, _ in events]
    assert "final_answer" in names, "Agent 已 terminate，必须透传 final_answer"
    assert "error" not in names, (
        "schema 探索无需 execute_sql 也是合法路径，不能发 error；"
        f"实际事件序列: {names}"
    )
    # 未走 execute_sql 工具 → 没有 sql / result 老事件
    assert "sql" not in names
    assert "result" not in names
    # sample_rows 内部会通过 db.execute_sql 拉样例，但不是工具层 execute_sql
    assert len(sample_exec_calls) == 1


def test_render_html_report_emits_report_event(monkeypatch):
    _patch_db(
        monkeypatch,
        lambda *a, **kw: (True, "ok", {"columns": [], "rows": []}),
    )
    llm = _ScriptedLlm(
        [
            '{"tool":"render_html_report","args":{"title":"R","html":"<html><body><h1>x</h1></body></html>"}}',
            '{"tool":"terminate","args":{"final_answer":"done"}}',
        ]
    )
    events, emit = _collect_events()
    req = ChatRequest(question="生成报告", datasource_id=1)
    _run(
        run_agent_stream(
            request=req,
            current_user_id=1,
            emit=emit,
            llm_client=llm,
            persist=False,
        )
    )

    report = next((p for e, p in events if e == "report"), None)
    assert report is not None
    assert report["title"] == "R"
    assert "<h1>x</h1>" in report["html"]


def test_chat_request_agent_mode_accepts_agent_team_legacy():
    req = ChatRequest(question="q", datasource_id=1)
    assert req.agent_mode == "agent"

    for mode in ("agent", "team", "legacy"):
        assert ChatRequest(question="q", datasource_id=1, agent_mode=mode).agent_mode == mode

    with pytest.raises(Exception):
        ChatRequest(question="q", datasource_id=1, agent_mode="wizard")


def test_sql_hits_locked_tables_helper():
    assert _sql_hits_locked_tables("SELECT * FROM chusan_zhengzhi LIMIT 10", ["chusan_zhengzhi"])
    assert _sql_hits_locked_tables("SELECT * FROM `chusan_zhengzhi` LIMIT 10", ["chusan_zhengzhi"])
    assert not _sql_hits_locked_tables("SELECT * FROM student_score LIMIT 10", ["chusan_zhengzhi"])


def test_describe_exam_batch_does_not_lock_table():
    assert _is_lockable_table("tb_exam_batch") is False
    assert _is_lockable_table("tb_school") is False
    assert _is_lockable_table("chusan_zhengzhi") is True
    state = _RunState(
        sub_task_index=0,
        constraints=_RunConstraints(locked_tables=[], required_keywords=[]),
    )
    _on_tool_result(
        state,
        {
            "tool": "describe_table",
            "success": True,
            "round": 1,
            "data": {"name": "tb_exam_batch"},
        },
    )
    assert state.constraints.locked_tables == []


def test_line_reach_report_bypasses_exam_batch_table_lock():
    events: list[tuple[str, dict]] = []

    async def emit(event: str, data: dict) -> None:
        events.append((event, dict(data)))

    state = _RunState(
        sub_task_index=0,
        constraints=_RunConstraints(
            locked_tables=["tb_exam_batch"],
            required_keywords=[],
        ),
    )
    state.last_sql = "SELECT exam_name FROM tb_score_indicator LIMIT 10"
    payload = {
        "agent": "ToolExpert",
        "tool": "build_line_reach_report_data_tool",
        "data": {
            "output_type": "html",
            "title": "全市达线分析",
            "mode": "inline",
            "html": "<html><body><p>参考人数 17700</p></body></html>",
        },
    }
    _run(_maybe_emit_report(payload, emit, state))
    assert "report" in [e for e, _ in events]
    assert "error" not in [e for e, _ in events]


def test_report_is_blocked_when_sql_violates_locked_tables():
    events: list[tuple[str, dict]] = []

    async def emit(event: str, data: dict) -> None:
        events.append((event, dict(data)))

    state = _RunState(
        sub_task_index=1,
        constraints=_RunConstraints(
            locked_tables=["chusan_zhengzhi"],
            required_keywords=["初三", "政治"],
            source_sub_task_index=0,
        ),
    )
    state.last_sql = "SELECT * FROM student_score ORDER BY 总分 DESC LIMIT 10"
    payload = {
        "agent": "ToolExpert",
        "sub_task_index": 1,
        "data": {
            "output_type": "html",
            "title": "R",
            "mode": "inline",
            "html": "<html><body>wrong scope</body></html>",
        },
    }

    _run(_maybe_emit_report(payload, emit, state))

    names = [e for e, _ in events]
    assert "report" not in names
    assert "error" in names
    err = next(p for e, p in events if e == "error")
    assert "报告已拦截" in err.get("error", "")


def test_report_is_blocked_when_school_scope_mismatches():
    events: list[tuple[str, dict]] = []

    async def emit(event: str, data: dict) -> None:
        events.append((event, dict(data)))

    state = _RunState(
        sub_task_index=2,
        constraints=_RunConstraints(
            locked_tables=[],
            required_keywords=["数学", "期末"],
            target_school="南京市第一中学",
            report_data={
                "sub_tasks": [
                    {
                        "sub_task_agent": "DataAnalyst",
                        "final_answer": "共 24 人",
                        "exec_result": {
                            "columns": ["学生", "分数"],
                            "rows": [],
                            "row_count": 24,
                        },
                    }
                ]
            },
        ),
    )
    payload = {
        "agent": "ToolExpert",
        "sub_task_index": 2,
        "data": {
            "output_type": "html",
            "title": "高一数学学科诊断报告",
            "mode": "inline",
            "html": (
                "<html><body>"
                "<span>高一(1)班+高一(2)班</span>"
                "<p>参考人数 40 人</p>"
                "</body></html>"
            ),
        },
    }

    _run(_maybe_emit_report(payload, emit, state))

    names = [e for e, _ in events]
    assert "report" not in names


def test_report_passes_when_school_scope_matches_upstream():
    events: list[tuple[str, dict]] = []

    async def emit(event: str, data: dict) -> None:
        events.append((event, dict(data)))

    state = _RunState(
        sub_task_index=2,
        constraints=_RunConstraints(
            locked_tables=[],
            required_keywords=["数学"],
            target_school="南京市第一中学",
            report_data={
                "sub_tasks": [
                    {
                        "sub_task_agent": "DataAnalyst",
                        "final_answer": "共 24 人",
                    }
                ]
            },
        ),
    )
    payload = {
        "agent": "ToolExpert",
        "data": {
            "output_type": "html",
            "title": "南京市第一中学数学诊断",
            "mode": "inline",
            "html": "<html><body><span>南京市第一中学</span><p>参考人数 24 人</p></body></html>",
        },
    }

    _run(_maybe_emit_report(payload, emit, state))

    names = [e for e, _ in events]
    assert "report" in names


def test_report_skipped_when_deduplicated():
    events: list[tuple[str, dict]] = []

    async def emit(event: str, data: dict) -> None:
        events.append((event, dict(data)))

    state = _RunState()
    payload = {
        "deduplicated": True,
        "data": {
            "output_type": "html",
            "title": "数学诊断报告",
            "html": "<html><body><div class='edu-card'>ok</div></body></html>",
        },
    }
    _run(_maybe_emit_report(payload, emit, state))
    assert "report" not in [e for e, _ in events]
    assert state.reports == []


def test_report_skipped_when_kpi_empty_shell():
    events: list[tuple[str, dict]] = []

    async def emit(event: str, data: dict) -> None:
        events.append((event, dict(data)))

    state = _RunState()
    html = (
        "<html><body><p>KPI 将显示为空</p>"
        "<div class='edu-kpi'><div class='value'>-</div></div>"
        "<div class='edu-kpi'><div class='value'>-</div></div>"
        "<div class='edu-kpi'><div class='value'>-</div></div>"
        "</body></html>"
    )
    payload = {
        "data": {
            "output_type": "html",
            "title": "数学诊断报告",
            "html": html,
        },
    }
    _run(_maybe_emit_report(payload, emit, state))
    assert "report" not in [e for e, _ in events]


def test_report_dedupes_same_type_weaker_copy():
    events: list[tuple[str, dict]] = []

    async def emit(event: str, data: dict) -> None:
        events.append((event, dict(data)))

    state = _RunState()
    rich = "<html><body>" + ("<tr><td>1</td></tr>" * 20) + "<p>充实报告</p></body></html>"
    weak = "<html><body><p>弱报告</p></body></html>"
    first = {
        "data": {
            "output_type": "html",
            "title": "【科目诊断报告】数学诊断报告",
            "report_type": "subject_diagnosis",
            "report_type_label": "科目诊断报告",
            "html": rich,
        },
    }
    second = {
        "data": {
            "output_type": "html",
            "title": "【科目诊断报告】数学诊断报告",
            "report_type": "subject_diagnosis",
            "report_type_label": "科目诊断报告",
            "html": weak,
        },
    }
    _run(_maybe_emit_report(first, emit, state))
    _run(_maybe_emit_report(second, emit, state))
    assert len([e for e, _ in events if e == "report"]) == 1
    assert len(state.reports) == 1


def test_class_overall_analysis_is_school_exam_report_query():
    from src.agent.education.query_parse import is_school_exam_report_query

    q = "扬州中学高三(10)班 · 连淮扬镇数学考试 · 班级整体分析"
    assert is_school_exam_report_query(q) is True


def test_bloated_school_plan_replaced_when_multiple_report_builders():
    from src.agent.expand.planner import should_replace_with_school_exam_plan

    q = "分析扬州中学高三(10)班在连淮扬镇数学考试的成绩，形成详细报告"
    plans = [
        {"sub_task": "查 KPI", "sub_task_agent": "DataAnalyst"},
        {"sub_task": "fetch_subject_diagnosis_data_tool", "sub_task_agent": "ToolExpert"},
        {
            "sub_task": "build_subject_diagnosis_sections_tool(render=true)",
            "sub_task_agent": "ToolExpert",
        },
        {
            "sub_task": "build_diagnostic_report_data_tool(render=true) 个性化诊断",
            "sub_task_agent": "ToolExpert",
        },
    ]
    assert should_replace_with_school_exam_plan(q, plans) is True
