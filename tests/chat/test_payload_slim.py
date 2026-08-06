"""Tests for chat payload slim helpers and history-detail loading."""

from __future__ import annotations

from src.chat.utils.payload_slim import slim_exec_result, slim_tool_calls


def test_slim_exec_result_truncates_rows_keeps_count():
    er = {
        "columns": ["a"],
        "rows": [[i] for i in range(250)],
        "sql": "select 1",
    }
    out = slim_exec_result(er, max_rows=100)
    assert len(out["rows"]) == 100
    assert out["row_count"] == 250
    assert out["rows_truncated"] is True
    assert out["sql"] == "select 1"
    assert len(er["rows"]) == 250  # 原对象不改


def test_slim_exec_result_noop_when_small():
    er = {"columns": ["a"], "rows": [[1], [2]], "row_count": 2}
    assert slim_exec_result(er, max_rows=100) is er


def test_slim_tool_calls_truncates_content_and_rows():
    calls = [
        {
            "tool": "execute_sql",
            "content": "x" * 5000,
            "data": {
                "columns": ["score"],
                "rows": [[i] for i in range(300)],
                "sql": "SELECT score FROM t",
            },
        },
        {
            "tool": "find_related_tables",
            "content": "ok",
            "data": [{"name": "tb_score"}],
        },
    ]
    out = slim_tool_calls(calls, max_rows=100, max_content=100)
    assert out[0]["content_truncated"] is True
    assert len(out[0]["content"]) < 120
    assert len(out[0]["data"]["rows"]) == 100
    assert out[0]["data"]["row_count"] == 300
    assert out[1]["data"] == [{"name": "tb_score"}]
