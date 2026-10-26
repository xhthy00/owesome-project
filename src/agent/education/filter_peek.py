"""教育问数：peek 过滤维值（考试/区县/线种），供 LLM 写 SQL 前对齐。

不做 embedding、不落运营库；只读业务表 DISTINCT。
"""

from __future__ import annotations

import re
from typing import Any, Callable

ExecuteFn = Callable[[str], tuple[bool, str, dict[str, Any] | None]]

_EDU_TABLES = frozenset(
    {
        "tb_score_indicator",
        "tb_score_contribution",
        "tb_score_overview",
        "tb_fraction_bar",
        "tb_exam_batch",
        "tb_score",
        "tb_school",
    }
)

_SQL_LIT = re.compile(
    r"(?:district|dq|exam_name|line_name|track)\s*=\s*'([^']*)'",
    re.IGNORECASE,
)


def _sql_quote(value: str) -> str:
    return str(value or "").replace("'", "''")


def _uniq_sorted(values: list[str], *, limit: int = 40) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        s = str(v or "").strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
        if len(out) >= limit:
            break
    return sorted(out)


def _col_values(result: dict[str, Any] | None, col: str) -> list[str]:
    if not isinstance(result, dict):
        return []
    columns = [str(c) for c in (result.get("columns") or [])]
    rows = result.get("rows") or []
    if col not in columns:
        # case-insensitive
        lower = {c.lower(): c for c in columns}
        real = lower.get(col.lower())
        if not real:
            return []
        col = real
        idx = columns.index(col)
    else:
        idx = columns.index(col)
    vals: list[str] = []
    for row in rows:
        if not isinstance(row, (list, tuple)) or idx >= len(row):
            continue
        vals.append(str(row[idx] if row[idx] is not None else "").strip())
    return _uniq_sorted(vals)


def touches_edu_table(sql: str) -> bool:
    """SQL 是否涉及教育学情相关表。"""
    s = (sql or "").lower()
    return any(t in s for t in _EDU_TABLES)


def extract_filter_literals(sql: str) -> list[str]:
    """从 SQL 抽出 district/exam_name 等等号字面量，供空结果提示。"""
    return _uniq_sorted([m.group(1) for m in _SQL_LIT.finditer(sql or "")], limit=20)


def format_peek_payload(payload: dict[str, Any]) -> str:
    """把 peek 结果格式化为给模型看的短文本。"""
    lines = [
        f"表 `{payload.get('table') or ''}` 过滤维值候选"
        + (f"（exam_hint={payload.get('exam_hint')!r}）" if payload.get("exam_hint") else "")
        + "：",
    ]
    for key, label in (
        ("exam_names", "考试 exam_name"),
        ("districts", "区县 district/dq"),
        ("line_names", "线种 line_name"),
        ("tracks", "选科 track"),
    ):
        vals = payload.get(key) or []
        if vals:
            preview = "、".join(vals[:25])
            more = f" 等{len(vals)}项" if len(vals) > 25 else ""
            lines.append(f"- {label}: {preview}{more}")
        else:
            lines.append(f"- {label}: （空）")
    lines.append(
        "写 WHERE 时字面量须来自以上候选，或用 LIKE '%线索%'；"
        "禁止把「N月」拼进区县（禁止「月广陵区」这类字面量）。"
    )
    if payload.get("error"):
        lines.append(f"- 注意: {payload['error']}")
    return "\n".join(lines)


def _peek_select_sql(tbl: str, hint: str) -> str | None:
    """按表生成 DISTINCT 探查 SQL。试卷/批次读考试专名，不改查指标表。"""
    quoted = _sql_quote(hint) if hint else ""
    if tbl == "tb_score_overview":
        where = f" WHERE exam_name LIKE '%{quoted}%'" if quoted else ""
        return (
            "SELECT DISTINCT exam_name, dq AS district "
            f"FROM tb_score_overview{where} "
            "ORDER BY 1, 2 LIMIT 200"
        )
    if tbl == "tb_exam_batch":
        where = f" WHERE batch_name LIKE '%{quoted}%'" if quoted else ""
        return (
            "SELECT DISTINCT batch_name AS exam_name "
            f"FROM tb_exam_batch{where} "
            "ORDER BY 1 LIMIT 200"
        )
    if tbl == "tb_exam":
        expr = "COALESCE(eb.batch_name, e.exam_name)"
        where = f" WHERE {expr} LIKE '%{quoted}%'" if quoted else ""
        return (
            f"SELECT DISTINCT {expr} AS exam_name "
            "FROM tb_exam e "
            "LEFT JOIN tb_exam_batch eb ON e.exam_batch_id = eb.id"
            f"{where} ORDER BY 1 LIMIT 200"
        )
    if tbl == "tb_score_indicator":
        where = f" WHERE exam_name LIKE '%{quoted}%'" if quoted else ""
        return (
            "SELECT DISTINCT exam_name, district, line_name, track "
            f"FROM tb_score_indicator{where} "
            "ORDER BY 1, 2, 3, 4 LIMIT 500"
        )
    return None


def _peek_payload(tbl: str, hint: str, sql: str, result: dict[str, Any] | None) -> dict[str, Any]:
    districts = _col_values(result, "district") or _col_values(result, "dq")
    return {
        "table": tbl,
        "exam_hint": hint,
        "exam_names": _col_values(result, "exam_name"),
        "districts": districts,
        "line_names": _col_values(result, "line_name"),
        "tracks": _col_values(result, "track"),
        "sql": sql,
    }


def peek_edu_filter_values(
    execute: ExecuteFn,
    *,
    exam_hint: str = "",
    table: str = "tb_score_indicator",
) -> dict[str, Any]:
    """执行 DISTINCT 探查，返回结构化维值。

    ``execute(sql) -> (ok, msg, result)``，result 形如 ``{columns, rows}``。
    指标表未收录的学校考试（如「新华中学2027届高三10月月考」）改查总览/批次专名，
    避免把其它场次当成这场考试的候选。
    """
    hint = str(exam_hint or "").strip()
    primary = (table or "tb_score_indicator").strip() or "tb_score_indicator"
    tables_try = [primary]
    for extra in ("tb_score_overview", "tb_exam_batch", "tb_score_indicator"):
        if extra not in tables_try:
            tables_try.append(extra)

    last_error = ""
    for tbl in tables_try:
        sql = _peek_select_sql(tbl, hint)
        if not sql:
            continue
        ok, msg, result = execute(sql)
        if not ok:
            last_error = msg or "查询失败"
            continue
        payload = _peek_payload(tbl, hint, sql, result)
        if payload["exam_names"] or not hint:
            return payload

    if hint:
        sql_all = _peek_select_sql("tb_score_indicator", "")
        ok_all, msg_all, result_all = execute(sql_all or "")
        if ok_all and result_all:
            payload = _peek_payload("tb_score_indicator", hint, sql_all or "", result_all)
            payload["note"] = "exam_hint 未命中行，已回退为全表候选"
            return payload
        last_error = msg_all or last_error

    return {
        "table": primary,
        "exam_hint": hint,
        "exam_names": [],
        "districts": [],
        "line_names": [],
        "tracks": [],
        "error": last_error or "无法读取过滤维值",
    }


def empty_result_protocol_note(sql: str) -> str:
    """空结果时追加到 execute_sql observation 的固定协议。"""
    lits = extract_filter_literals(sql)
    lit_line = f"检测到的过滤字面量: {', '.join(lits)}" if lits else "未解析到等号过滤字面量"
    return (
        "\n【空结果协议】\n"
        "- 禁止断言「数据未纳入 / 该区无数据 / 查不到」；\n"
        "- 已绑定考试/班级仍为空：调用 peek_edu_filter_values 对照后最多改写 2 次；"
        "仍空则说明该范围无成绩，并列近场考试让用户选；\n"
        "- 过滤字面量未经本轮绑定：视为漏网，须追问考试/班级，禁止继续猜；\n"
        f"- {lit_line}；若含「月…区」则多半是把「N月」误拼进区县，应改为真实区县名。\n"
    )


__all__ = [
    "empty_result_protocol_note",
    "extract_filter_literals",
    "format_peek_payload",
    "peek_edu_filter_values",
    "touches_edu_table",
]
