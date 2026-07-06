"""教育学情场景 SQL 自动修正：根据数据库 error 改写 LLM 生成的常见错误 SQL。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


MAX_SQL_AUTO_FIX_ATTEMPTS = 3


@dataclass
class SqlAutoFixResult:
    success: bool
    message: str
    result: Any
    sql_run: str
    sql_work: str
    fixes_applied: list[str] = field(default_factory=list)


def _q(db_type: str) -> str:
    return "`" if db_type == "mysql" else '"'


def _fix_student_id_on_student_table(sql: str, error: str, db_type: str) -> Optional[tuple[str, str]]:
    """tb_student 主键为 id，LLM 常误写 st.student_id。"""
    if not re.search(r"student_id\s+does not exist", error, re.I):
        return None

    alias: str | None = None
    m = re.search(r'column\s+"?(\w+)"?\.student_id\s+does not exist', error, re.I)
    if m and m.group(1).lower() not in ("sc", "sd"):
        alias = m.group(1)
    elif re.search(r"\bst\.student_id\b", sql, re.I):
        alias = "st"

    if not alias:
        return None

    pattern = rf"\b{re.escape(alias)}\.student_id\b"
    new_sql = re.sub(pattern, f"{alias}.id", sql, flags=re.I)
    if new_sql == sql:
        return None
    return new_sql, f"{alias}.student_id → {alias}.id（tb_student 主键为 id）"


def _fix_school_id_on_score_detail(sql: str, error: str, db_type: str) -> Optional[tuple[str, str]]:
    """school_id 在 tb_score（sc），不在 tb_score_detail（sd）。"""
    if not re.search(r"school_id", error, re.I):
        return None
    if not re.search(r"\bsd\.(?:\"|')?school_id", sql, re.I):
        return None

    q = _q(db_type)
    new_sql = re.sub(
        rf"\bsd\.(?:{q})?school_id(?:{q})?",
        f"sc.{q}school_id{q}",
        sql,
        flags=re.I,
    )
    new_sql = _ensure_score_join_for_detail(new_sql)
    if new_sql == sql:
        return None
    return new_sql, f"sd.school_id → sc.school_id，并确保 JOIN tb_score"


def _fix_class_on_score_detail(sql: str, error: str, db_type: str) -> Optional[tuple[str, str]]:
    if not re.search(r"\bsd\.(?:\"|')?class", sql, re.I):
        return None
    if "class" not in error.lower():
        return None

    q = _q(db_type)
    new_sql = re.sub(
        rf"\bsd\.(?:{q})?class(?:{q})?",
        f"sc.{q}class{q}",
        sql,
        flags=re.I,
    )
    new_sql = _ensure_score_join_for_detail(new_sql)
    if new_sql == sql:
        return None
    return new_sql, f"sd.class → sc.class，并确保 JOIN tb_score"


def _fix_ambiguous_class(sql: str, error: str, db_type: str) -> Optional[tuple[str, str]]:
    if "ambiguous" not in error.lower() or "class" not in error.lower():
        return None
    if not re.search(r"\btb_score\b", sql, re.I):
        return None

    q = _q(db_type)
    pattern = rf"(?<!\.){q}class{q}"
    new_sql = re.sub(pattern, f"sc.{q}class{q}", sql)
    if new_sql == sql:
        return None
    return new_sql, f"限定 class 列为 sc.class（消除歧义）"


def _fix_missing_sd_reference(sql: str, error: str, db_type: str) -> Optional[tuple[str, str]]:
    if 'missing FROM-clause entry for table "sd"' not in error:
        return None
    if re.search(r"\btb_score_detail\b", sql, re.I):
        return None
    if not re.search(r"\bsd\.", sql, re.I):
        return None

    new_sql = re.sub(r"\bsd\.exam_id\b", "sc.exam_id", sql, flags=re.I)
    new_sql = re.sub(r"\bsd\.student_id\b", "sc.student_id", new_sql, flags=re.I)
    if new_sql == sql:
        return None
    return new_sql, "sd.* → sc.*（当前查询未 JOIN tb_score_detail）"


def _fix_exam_school_id(sql: str, error: str, db_type: str) -> Optional[tuple[str, str]]:
    if "school_id" not in error.lower():
        return None

    q = _q(db_type)
    new_sql = sql
    changed = False
    for alias in ("e", "exam"):
        pat = rf"\b{alias}\.(?:{q})?school_id(?:{q})?"
        if re.search(pat, new_sql, re.I):
            new_sql = re.sub(pat, f"sc.{q}school_id{q}", new_sql, flags=re.I)
            changed = True
    if not changed:
        return None
    new_sql = _ensure_score_join_for_detail(new_sql) if "tb_score_detail" in new_sql.lower() else new_sql
    if not re.search(r"\btb_score\b", new_sql, re.I):
        new_sql = _ensure_score_join_standalone(new_sql)
    return new_sql, "考试表无 school_id，改用 sc.school_id"


def _ensure_score_join_for_detail(sql: str) -> str:
    if not re.search(r"\btb_score_detail\b", sql, re.I):
        return sql
    if re.search(r"\btb_score\b", sql, re.I):
        return sql

    def _repl(m: re.Match[str]) -> str:
        sd_alias = m.group(1)
        return (
            f"{m.group(0)} JOIN tb_score sc ON {sd_alias}.exam_id = sc.exam_id "
            f"AND {sd_alias}.student_id = sc.student_id"
        )

    return re.sub(
        r"FROM\s+tb_score_detail\s+(?:AS\s+)?(\w+)",
        _repl,
        sql,
        count=1,
        flags=re.I,
    )


def _ensure_score_join_standalone(sql: str) -> str:
    """FROM tb_school / tb_exam 等但未 JOIN tb_score 时补 sc。"""
    if re.search(r"\btb_score\b", sql, re.I):
        return sql
    if re.search(r"\btb_school\b", sql, re.I) and re.search(r"\bJOIN\s+tb_exam\b", sql, re.I):
        return re.sub(
            r"(JOIN\s+tb_exam\s+(?:AS\s+)?(\w+))",
            r"\1 JOIN tb_score sc ON sc.exam_id = \2.id AND sc.school_id = sch.id",
            sql,
            count=1,
            flags=re.I,
        )
    return sql


_RULES: list[Callable[[str, str, str], Optional[tuple[str, str]]]] = [
    _fix_student_id_on_student_table,
    _fix_school_id_on_score_detail,
    _fix_class_on_score_detail,
    _fix_missing_sd_reference,
    _fix_ambiguous_class,
    _fix_exam_school_id,
]


def suggest_sql_fix(sql: str, error: str, db_type: str) -> Optional[tuple[str, str]]:
    """根据执行错误返回 (修正后 SQL, 修正说明)；无法修正时返回 None。"""
    err = (error or "").strip()
    if not err or not sql.strip():
        return None
    for rule in _RULES:
        out = rule(sql, err, db_type)
        if out:
            return out
    return None


def run_sql_with_auto_fix(
    sql: str,
    *,
    db_type: str,
    config: dict[str, Any],
    prepare_sql: Callable[[str], str] | None = None,
    max_fix_attempts: int = MAX_SQL_AUTO_FIX_ATTEMPTS,
) -> SqlAutoFixResult:
    """执行 SQL；失败时按 error 自动改写并重试。"""
    from src.datasource.db.db import execute_sql as db_execute_sql

    sql_work = sql.strip()
    fixes_applied: list[str] = []
    last_message = ""
    last_sql_run = sql_work
    last_result: Any = None

    for attempt in range(max_fix_attempts + 1):
        sql_run = prepare_sql(sql_work) if prepare_sql else sql_work
        last_sql_run = sql_run
        success, message, result = db_execute_sql(db_type=db_type, config=config, sql=sql_run)
        last_message = message
        last_result = result
        if success:
            return SqlAutoFixResult(
                success=True,
                message=message,
                result=result,
                sql_run=sql_run,
                sql_work=sql_work,
                fixes_applied=fixes_applied,
            )
        if attempt >= max_fix_attempts:
            break
        fix = suggest_sql_fix(sql_work, message, db_type)
        if not fix:
            break
        new_sql, desc = fix
        if new_sql.strip() == sql_work:
            break
        sql_work = new_sql.strip()
        fixes_applied.append(desc)

    return SqlAutoFixResult(
        success=False,
        message=last_message,
        result=last_result,
        sql_run=last_sql_run,
        sql_work=sql_work,
        fixes_applied=fixes_applied,
    )


def format_auto_fix_note(fixes_applied: list[str], *, success: bool) -> str:
    if not fixes_applied:
        return ""
    joined = "；".join(fixes_applied)
    if success:
        return f"（已自动修正：{joined}）"
    return f"（已尝试自动修正仍失败：{joined}）"


__all__ = [
    "MAX_SQL_AUTO_FIX_ATTEMPTS",
    "SqlAutoFixResult",
    "format_auto_fix_note",
    "run_sql_with_auto_fix",
    "suggest_sql_fix",
]
