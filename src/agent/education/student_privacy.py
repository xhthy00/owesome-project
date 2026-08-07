"""学生姓名隐私：对外一律用 student_id，禁止明文姓名回灌给 Agent。

edu 库部分宽表/概览表仍可能残留 ``xm`` / ``姓名`` 等明文列；Agent 经
describe/sample 发现后会 SELECT 并写进结论。此处在执行前改写、结果中剔除，
与 ``school_cipher`` 对 ``s_name`` 的处理同构。
"""

from __future__ import annotations

import re
from typing import Any

#: 对外禁止暴露的姓名类列名（小写比对）
_FORBIDDEN_NAME_COLS = frozenset(
    {
        "xm",
        "姓名",
        "真实姓名",
        "学生姓名",
        "stu_name",
        "stuname",
        "real_name",
        "realname",
    }
)

_XM_IDENT = re.compile(
    r"\b([A-Za-z_][\w]*\.)?xm\b",
    re.IGNORECASE,
)


def is_forbidden_student_name_col(name: str) -> bool:
    return str(name or "").strip().lower() in _FORBIDDEN_NAME_COLS


def filter_schema_fields(fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """describe_table：隐藏姓名明文列，避免 Agent 选中。"""
    return [f for f in fields if not is_forbidden_student_name_col(str(f.get("name") or ""))]


def _map_sql_outside_string_literals(sql: str, transform) -> str:
    parts: list[str] = []
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        if ch in "'\"":
            q = ch
            j = i + 1
            while j < n:
                if sql[j] == "\\" and j + 1 < n:
                    j += 2
                    continue
                if sql[j] == q:
                    if q == "'" and j + 1 < n and sql[j + 1] == "'":
                        j += 2
                        continue
                    j += 1
                    break
                j += 1
            parts.append(sql[i:j])
            i = j
            continue
        j = i
        while j < n and sql[j] not in "'\"":
            j += 1
        parts.append(transform(sql[i:j]))
        i = j
    return "".join(parts)


def rewrite_sql_student_name_cols(sql: str) -> tuple[str, bool]:
    """将 SQL 中的 ``xm`` 标识符改写为 ``student_id``（保留表别名）。

    概览表若无 ``student_id`` 而有 ``xh``，执行失败后由 Agent 改用学号列；
    优先保证不把明文姓名查出。
    """
    text = str(sql or "")
    if not text or "xm" not in text.lower():
        return text, False

    changed = False

    def _chunk(chunk: str) -> str:
        nonlocal changed

        def _sub(m: re.Match[str]) -> str:
            nonlocal changed
            changed = True
            prefix = m.group(1) or ""
            return f"{prefix}student_id"

        return _XM_IDENT.sub(_sub, chunk)

    out = _map_sql_outside_string_literals(text, _chunk)
    return out, changed


def strip_student_names_from_query_result(
    result: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """从查询/采样结果中去掉姓名明文列，避免回灌给 LLM。"""
    if not isinstance(result, dict):
        return result
    columns = list(result.get("columns") or [])
    rows = list(result.get("rows") or [])
    drop_idx = [
        i for i, c in enumerate(columns) if is_forbidden_student_name_col(str(c))
    ]
    has_dict_name = bool(
        rows
        and isinstance(rows[0], dict)
        and any(
            is_forbidden_student_name_col(str(k))
            for r in rows
            if isinstance(r, dict)
            for k in r
        )
    )
    if not drop_idx and not has_dict_name:
        return result

    drop = set(drop_idx)
    new_columns = [c for i, c in enumerate(columns) if i not in drop]
    new_rows: list[Any] = []
    for row in rows:
        if isinstance(row, dict):
            new_rows.append(
                {k: v for k, v in row.items() if not is_forbidden_student_name_col(str(k))}
            )
        elif isinstance(row, (list, tuple)):
            new_rows.append([v for i, v in enumerate(row) if i not in drop])
        else:
            new_rows.append(row)
    out = dict(result)
    out["columns"] = new_columns
    out["rows"] = new_rows
    return out


__all__ = [
    "filter_schema_fields",
    "is_forbidden_student_name_col",
    "rewrite_sql_student_name_cols",
    "strip_student_names_from_query_result",
]
