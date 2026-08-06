"""会话落库 / 历史详情的大字段瘦身。

team 模式下 ``exec_result`` / ``tool_calls`` 常含全量明细行（万级），
单条记录可达数 MB，导致 ``GET /conversations/{id}`` 从远端库拉文本极慢。
"""

from __future__ import annotations

from typing import Any

#: 落库与详情返回时，明细行上限。
MAX_RESULT_ROWS = 100

#: tool_calls.content 截断长度。
MAX_TOOL_CONTENT_CHARS = 4000

__all__ = [
    "MAX_RESULT_ROWS",
    "MAX_TOOL_CONTENT_CHARS",
    "slim_exec_result",
    "slim_tool_calls",
]


def slim_exec_result(
    exec_result: Any,
    *,
    max_rows: int = MAX_RESULT_ROWS,
) -> Any:
    """截断 ``rows``，保留 ``row_count`` / ``columns`` / ``sql``。"""
    if not isinstance(exec_result, dict):
        return exec_result
    rows = exec_result.get("rows")
    if not isinstance(rows, list) or len(rows) <= max_rows:
        return exec_result
    out = dict(exec_result)
    out["rows"] = rows[:max_rows]
    if out.get("row_count") is None:
        out["row_count"] = len(rows)
    out["rows_truncated"] = True
    return out


def _slim_tool_data(data: Any, *, max_rows: int) -> Any:
    if isinstance(data, dict):
        out = dict(data)
        rows = out.get("rows")
        if isinstance(rows, list) and len(rows) > max_rows:
            out["rows"] = rows[:max_rows]
            if out.get("row_count") is None:
                out["row_count"] = len(rows)
            out["rows_truncated"] = True
        # 嵌套 chunks（报告片段）保持原样，体积通常远小于明细行
        return out
    if isinstance(data, list):
        # find_related_tables 等：列表本身很小，原样返回
        return data
    return data


def slim_tool_calls(
    tool_calls: Any,
    *,
    max_rows: int = MAX_RESULT_ROWS,
    max_content: int = MAX_TOOL_CONTENT_CHARS,
) -> Any:
    """截断工具结果中的明细行与超长 content。"""
    if not isinstance(tool_calls, list):
        return tool_calls
    slimmed: list[Any] = []
    for tc in tool_calls:
        if not isinstance(tc, dict):
            slimmed.append(tc)
            continue
        item = dict(tc)
        content = item.get("content")
        if isinstance(content, str) and len(content) > max_content:
            item["content"] = content[:max_content] + "…（已截断）"
            item["content_truncated"] = True
        if "data" in item:
            item["data"] = _slim_tool_data(item.get("data"), max_rows=max_rows)
        slimmed.append(item)
    return slimmed
