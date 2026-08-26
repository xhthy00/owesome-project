"""带行列权限的 SQL 执行 helper（Chat / Agent / 教育模块共用）。"""

from __future__ import annotations

from typing import Any, Optional

from sqlalchemy.orm import Session

from datasource.service.query_permission import (
    apply_permissions_for_execute,
    expand_overview_xx_school_code_literals,
    filter_exec_result_by_column_permissions,
    validate_sql_column_permissions,
)
from system.models.user import SysUser


def execute_sql_with_permission(
    session: Session,
    user: SysUser | None,
    db_type: str,
    config: dict[str, Any],
    datasource_id: int,
    sql: str,
    *,
    tables_hint: Optional[list[str]] = None,
) -> tuple[bool, str, dict[str, Any] | None, str]:
    """执行只读 SQL 并应用行列权限；失败时按 error 自动改写 SQL 重试。

    Returns:
        (success, message, result_dict, sql_run)
    """
    from datasource.service.sql_auto_fix import format_auto_fix_note, run_sql_with_auto_fix

    def _prepare(raw: str) -> str:
        if user is not None:
            return apply_permissions_for_execute(
                session, user, datasource_id, db_type, raw, tables_hint
            )
        return expand_overview_xx_school_code_literals(raw, db_type)

    sql_run = _prepare(sql)
    if user is not None:
        err = validate_sql_column_permissions(session, user, datasource_id, db_type, sql_run)
        if err:
            return False, err, {"error": err, "sql": sql_run}, sql_run

    outcome = run_sql_with_auto_fix(
        sql,
        db_type=db_type,
        config=config,
        prepare_sql=_prepare,
    )
    sql_run = outcome.sql_run
    note = format_auto_fix_note(outcome.fixes_applied, success=outcome.success)
    message = outcome.message
    if note and outcome.success:
        message = f"{message}{note}"

    if not outcome.success:
        if note:
            message = f"{message} {note}"
        return False, message, {"error": message, "sql": sql_run, "fixes_applied": outcome.fixes_applied}, sql_run

    result = outcome.result
    if not isinstance(result, dict):
        return outcome.success, message, result if isinstance(result, dict) else None, sql_run

    if user is not None:
        result = filter_exec_result_by_column_permissions(
            session, user, datasource_id, db_type, sql_run, result
        )
    if outcome.fixes_applied:
        result = dict(result)
        result["fixes_applied"] = outcome.fixes_applied
    return True, message, result, sql_run


def execute_sql_with_permission_by_user_id(
    user_id: int | None,
    datasource_id: int,
    workspace_oid: int | None,
    sql: str,
    *,
    tables_hint: Optional[list[str]] = None,
) -> tuple[bool, str, dict[str, Any] | None, str]:
    """按 user_id 加载用户并执行（教育工具 / API 便捷入口）。"""
    from src.agent.resource.tool.business import _load_datasource
    from src.common.core.database import get_db_session
    from src.system.crud.crud_user import get_user_by_id

    db_type, config, _ = _load_datasource(datasource_id, workspace_oid)
    if user_id is None:
        from datasource.service.sql_auto_fix import format_auto_fix_note, run_sql_with_auto_fix

        outcome = run_sql_with_auto_fix(sql, db_type=db_type, config=config)
        note = format_auto_fix_note(outcome.fixes_applied, success=outcome.success)
        message = outcome.message
        if note:
            message = f"{message} {note}" if not outcome.success else f"{message}{note}"
        result = outcome.result if isinstance(outcome.result, dict) else None
        return outcome.success, message, result, outcome.sql_run

    with get_db_session() as session:
        user = get_user_by_id(session, user_id)
        return execute_sql_with_permission(
            session,
            user,
            db_type,
            config,
            datasource_id,
            sql,
            tables_hint=tables_hint,
        )


__all__ = [
    "execute_sql_with_permission",
    "execute_sql_with_permission_by_user_id",
]
