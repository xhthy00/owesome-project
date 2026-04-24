"""容错 JSON 解析单元测试。"""

from __future__ import annotations

import pytest

from src.agent.util.json_parser import parse_json_tolerant


def test_plain_object():
    assert parse_json_tolerant('{"a": 1}') == {"a": 1}


def test_plain_array():
    assert parse_json_tolerant("[1, 2, 3]") == [1, 2, 3]


def test_json_code_block():
    text = """这里是模型解释：
```json
{"action": "echo", "args": {"x": 1}}
```
多余的尾巴文本也应被忽略。
"""
    assert parse_json_tolerant(text) == {"action": "echo", "args": {"x": 1}}


def test_generic_code_block():
    text = "```\n[1, 2]\n```"
    assert parse_json_tolerant(text) == [1, 2]


def test_substring_fallback_object():
    text = "prefix noise ... {\"ok\": true} ... trailing"
    assert parse_json_tolerant(text) == {"ok": True}


def test_substring_fallback_array():
    text = "note: result is [\"a\", \"b\"] end."
    assert parse_json_tolerant(text) == ["a", "b"]


def test_parses_json_after_think_block():
    text = "<think>先想一下步骤</think>\n{\"tool\":\"list_tables\",\"args\":{}}"
    assert parse_json_tolerant(text) == {"tool": "list_tables", "args": {}}


def test_parses_embedded_json_object_without_code_fence():
    text = "prefix text\n{\"tool\":\"describe_table\",\"args\":{\"table_name\":\"users\"}}\ntrailing"
    assert parse_json_tolerant(text) == {
        "tool": "describe_table",
        "args": {"table_name": "users"},
    }


def test_empty_raises():
    with pytest.raises(ValueError):
        parse_json_tolerant("")


def test_none_raises():
    with pytest.raises(ValueError):
        parse_json_tolerant(None)  # type: ignore[arg-type]


def test_unparsable_raises():
    with pytest.raises(ValueError):
        parse_json_tolerant("completely non-json text with no braces")
