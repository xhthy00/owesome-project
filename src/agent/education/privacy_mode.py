"""教育匿名脱敏展示开关。

默认开启：问数/报告只展示脱敏学号与校码，剔除姓名、真实学号、学校全称。
关闭后允许展示 xm/xh/s_name；sfzh/ksh 始终隐藏。
读取失败时保持脱敏（fail-closed）。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

_UNSET = object()
_lock = threading.Lock()
_cached: object = _UNSET
_cached_at = 0.0
_CACHE_TTL_SEC = 5.0

_ALWAYS_HIDDEN = frozenset({"sfzh", "ksh"})
_ANON_EXTRA_HIDDEN = frozenset({"xh", "s_name"})


def is_anonymize_display_enabled() -> bool:
    """当前是否匿名脱敏展示。默认 True。"""
    global _cached, _cached_at
    now = time.monotonic()
    with _lock:
        if _cached is not _UNSET and (now - _cached_at) < _CACHE_TTL_SEC:
            return bool(_cached)
    val = _read_from_db()
    with _lock:
        _cached = val
        _cached_at = time.monotonic()
    return val


def set_anonymize_display_cached(enabled: bool) -> None:
    global _cached, _cached_at
    with _lock:
        _cached = bool(enabled)
        _cached_at = time.monotonic()


def clear_anonymize_display_cache() -> None:
    global _cached, _cached_at
    with _lock:
        _cached = _UNSET
        _cached_at = 0.0


def _read_from_db() -> bool:
    try:
        from src.common.core.database import get_db_session
        from system.crud.crud_edu_privacy import get_anonymize_display

        with get_db_session() as session:
            return get_anonymize_display(session)
    except Exception as exc:  # noqa: BLE001
        logger.warning("read edu privacy flag failed, default anonymize=True: %s", exc)
        return True


def privacy_sql_instruction() -> str:
    """注入 SQL/规划提示的隐私口径。"""
    if is_anonymize_display_enabled():
        return (
            "学校字段：展示与过滤只用 sch.name 或 sch.id / sc.school_id（脱敏码）；"
            "**禁止** SELECT/引用 tb_school.s_name（可能含中文明文）。"
            "但 tb_score_overview.xx 仍是学校明文，点名学校用 xx LIKE '%校名%'；"
            "禁止把 GZ_ 校码写进 overview.xx。"
            "学生标识只用 student_id / anon_stu_id；"
            "**禁止** SELECT xm（姓名）、xh（真实学号）、sfzh/ksh。"
        )
    return (
        "当前已关闭匿名脱敏：可 SELECT xm（姓名）、xh（学号）、tb_school.s_name（学校全称）；"
        "点名学校时 tb_score 用 sch.s_name LIKE '%校名%'；"
        "tb_score_overview.xx 是学校明文（如「扬州中学」），用 xx LIKE '%校名%'；"
        "禁止 xx='GZ_…' / xx=tb_school.id / xx=tb_school.name（那些是脱敏校码，不是校名）；"
        "禁止 WHERE sch.name = '扬州中学'（name 仍是脱敏码）；"
        "仍禁止 SELECT sfzh/ksh（身份证/考生号）。"
    )


def privacy_student_hint() -> str:
    """Planner / DataAnalyst：SELECT 与结论如何点名学生。"""
    if is_anonymize_display_enabled():
        return (
            "涉及学生时 SELECT **仅用 student_id**（或 sc.student_id / st.id 学号），"
            "以及 score、exam_score、class 等；"
            "**禁止** SELECT/展示姓名明文（xm、name、student_name、真实姓名）；"
            "结论里点名学生时**一律写 student_id**，禁止写中文姓名。"
        )
    return (
        "当前已关闭匿名脱敏：涉及学生时可 SELECT xm（姓名）、xh/student_id（学号）、"
        "sch.s_name（学校全称）；结论里可用姓名/学号/校名。"
        "用户要求「给出姓名」时必须输出姓名，禁止改成只写 student_id。"
        "仍禁止 SELECT sfzh/ksh。"
    )


def privacy_conclusion_brief() -> str:
    """Summarizer [隐私] 段。"""
    if is_anonymize_display_enabled():
        return (
            "[隐私强制]\n"
            "- 点名学生时**一律写 student_id**（学号），**禁止**输出中文姓名明文；\n"
            "  若输入中同时出现姓名与 student_id，只保留 student_id。"
        )
    return (
        "[隐私]\n"
        "- 当前已关闭匿名脱敏：点名学生时**优先写姓名**（xm），可同时写学号；\n"
        "  用户要求「给出姓名」时必须输出姓名，禁止改成只写 student_id。\n"
        "  仍禁止输出身份证号、考生号。"
    )


def privacy_conclusion_constraint() -> str:
    """Summarizer 硬约束一行。"""
    if is_anonymize_display_enabled():
        return "点名学生只用 student_id，禁止输出姓名明文"
    return "当前已关闭匿名脱敏：点名学生可用姓名或学号；仍禁止输出身份证/考生号"


def privacy_who_ref() -> str:
    """事实问答里「最好/最高/谁」的点名口径。"""
    if is_anonymize_display_enabled():
        return "student_id"
    return "姓名（可附学号）"


def filter_display_fields(
    fields: list[dict[str, Any]],
    table_name: str = "",
) -> list[dict[str, Any]]:
    """describe_table：按开关隐藏隐私列。sfzh/ksh 始终隐藏。"""
    from src.agent.education.student_privacy import is_forbidden_student_name_col

    anon = is_anonymize_display_enabled()
    table_key = str(table_name or "").split(".")[-1].lower()
    out: list[dict[str, Any]] = []
    for field in fields:
        name = str(field.get("name") or "").strip()
        low = name.lower()
        if low in _ALWAYS_HIDDEN:
            continue
        if anon:
            if is_forbidden_student_name_col(name):
                continue
            if low in _ANON_EXTRA_HIDDEN:
                continue
            if table_key == "tb_school" and low == "s_name":
                continue
        out.append(field)
    return out


def apply_sql_privacy(sql: str) -> tuple[str, list[str]]:
    """执行前改写：脱敏开启时 s_name→name、xm→student_id。"""
    if not is_anonymize_display_enabled():
        return str(sql or ""), []
    from src.agent.education.school_cipher import rewrite_sql_school_s_name
    from src.agent.education.student_privacy import rewrite_sql_student_name_cols

    fixes: list[str] = []
    text, s_name_rewritten = rewrite_sql_school_s_name(sql)
    if s_name_rewritten:
        fixes.append("school_s_name→name")
    text, xm_rewritten = rewrite_sql_student_name_cols(text)
    if xm_rewritten:
        fixes.append("xm→student_id")
    return text, fixes


def apply_result_privacy(result: dict[str, Any] | None) -> dict[str, Any] | None:
    """执行后剥离：脱敏开启时去掉姓名/真实学号/校名明文；sfzh/ksh 始终去掉。"""
    if not isinstance(result, dict):
        return result
    drop = set(_ALWAYS_HIDDEN)
    if is_anonymize_display_enabled():
        from src.agent.education.school_cipher import strip_s_name_from_query_result
        from src.agent.education.student_privacy import strip_student_names_from_query_result

        result = strip_s_name_from_query_result(result) or result
        result = strip_student_names_from_query_result(result) or result
        drop |= set(_ANON_EXTRA_HIDDEN)
    return _strip_result_cols(result, drop)


def overlay_table_comments(comments: dict[str, str]) -> dict[str, str]:
    """关闭脱敏时在表注释上追加可查询明文列说明。"""
    if is_anonymize_display_enabled() or not comments:
        return comments
    out = dict(comments)
    note = "【当前已关闭匿名脱敏：可查询 xm/xh/s_name，仍禁止 sfzh/ksh】"
    for key in ("tb_school", "tb_score_overview", "tb_student", "tb_score"):
        if key in out and note not in out[key]:
            out[key] = f"{out[key]} {note}"
    return out


def overlay_schema_fields(fields: dict[str, str]) -> dict[str, str]:
    """关闭脱敏时学校展示改用 s_name。"""
    if is_anonymize_display_enabled() or not fields:
        return fields
    out = dict(fields)
    school = str(out.get("school_name") or "")
    if school in ("sch.name", "name"):
        out["school_name"] = "COALESCE(sch.s_name, sch.name)"
    return out


def _strip_result_cols(
    result: dict[str, Any],
    names: set[str],
) -> dict[str, Any]:
    if not names:
        return result
    lower = {str(n).lower() for n in names}
    columns = list(result.get("columns") or [])
    rows = list(result.get("rows") or [])
    drop_idx = [i for i, c in enumerate(columns) if str(c).lower() in lower]
    has_dict = bool(
        rows
        and isinstance(rows[0], dict)
        and any(
            str(k).lower() in lower
            for row in rows
            if isinstance(row, dict)
            for k in row
        )
    )
    if not drop_idx and not has_dict:
        return result
    drop = set(drop_idx)
    new_columns = [c for i, c in enumerate(columns) if i not in drop]
    new_rows: list[Any] = []
    for row in rows:
        if isinstance(row, dict):
            new_rows.append({k: v for k, v in row.items() if str(k).lower() not in lower})
        elif isinstance(row, (list, tuple)):
            new_rows.append([v for i, v in enumerate(row) if i not in drop])
        else:
            new_rows.append(row)
    out = dict(result)
    out["columns"] = new_columns
    out["rows"] = new_rows
    return out


__all__ = [
    "apply_result_privacy",
    "apply_sql_privacy",
    "clear_anonymize_display_cache",
    "filter_display_fields",
    "is_anonymize_display_enabled",
    "overlay_schema_fields",
    "overlay_table_comments",
    "privacy_conclusion_brief",
    "privacy_conclusion_constraint",
    "privacy_sql_instruction",
    "privacy_student_hint",
    "privacy_who_ref",
    "set_anonymize_display_cached",
]
