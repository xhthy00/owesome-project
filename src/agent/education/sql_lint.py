"""教育 SQL 轻量 lint：只告警，不拦截执行。"""

from __future__ import annotations

import re

_AVG_REACH = re.compile(r"\bAVG\s*\(\s*reach_rate\s*\)", re.IGNORECASE)
_MONTH_DISTRICT = re.compile(r"'月[\u4e00-\u9fff]{1,6}区'")


def lint_edu_sql(sql: str) -> list[str]:
    """返回告警文案列表（空表示无问题）。"""
    s = sql or ""
    warnings: list[str] = []
    if _AVG_REACH.search(s):
        warnings.append(
            "检测到 AVG(reach_rate)：区县/全市达线率须 "
            "SUM(reached_count)/SUM(candidates) 重算，禁止对 reach_rate 求平均。"
        )
    if _MONTH_DISTRICT.search(s):
        warnings.append(
            "检测到疑似把「N月」拼进区县的字面量（如 '月广陵区'）："
            "请改为真实区县名或 district LIKE '%广陵%'，并先 peek_edu_filter_values。"
        )
    return warnings


def format_lint_warnings(warnings: list[str]) -> str:
    if not warnings:
        return ""
    lines = ["【SQL lint 警告】请改写后重试："]
    lines.extend(f"- {w}" for w in warnings)
    return "\n".join(lines) + "\n"


__all__ = ["format_lint_warnings", "lint_edu_sql"]
