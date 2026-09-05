"""教育 SQL 护栏：致命项拦截，其余告警。"""

from __future__ import annotations

import re
from typing import Sequence

_AVG_REACH = re.compile(r"\bAVG\s*\(\s*reach_rate\s*\)", re.IGNORECASE)
_MONTH_DISTRICT = re.compile(r"'月[\u4e00-\u9fff]{1,6}区'")
_SUBJECT_COLS = (
    "ywzw", "sxkg", "yyzw", "hxzh", "swzh", "zzzh", "dlzh",
    "yw", "sx", "yy", "wl", "hx", "sw", "ls", "zz", "dl",
)
_SUBJECT_COL_ALT = "|".join(_SUBJECT_COLS)
_SUBJECT_STAT = re.compile(
    r"\b(?:AVG|MEDIAN|MIN|MAX|SUM|STDDEV(?:_SAMP|_POP)?|STDEV(?:_SAMP|_POP)?|"
    r"VAR(?:_SAMP|_POP)?|VARIANCE)\s*\(\s*(?:[\w]+\.)?(?P<col>"
    + _SUBJECT_COL_ALT
    + r")\s*\)",
    re.IGNORECASE,
)
_PERCENTILE_SUBJECT = re.compile(
    r"PERCENTILE_\w+\s*\([^)]*\)\s*WITHIN\s+GROUP\s*\(\s*ORDER\s+BY\s+"
    r"(?:[\w]+\.)?(?P<col>" + _SUBJECT_COL_ALT + r")\b",
    re.IGNORECASE,
)


def _subject_stat_excludes_zero(sql: str, col: str, match_end: int) -> bool:
    """聚合后有 FILTER (WHERE col>0)，或整句有 AND/WHERE col>0。"""
    tail = sql[match_end : match_end + 180]
    col_re = re.escape(col)
    if re.search(rf"FILTER\s*\(\s*WHERE\s+[\w.]*{col_re}\s*>\s*0", tail, re.I):
        return True
    if re.search(rf"\b(?:AND|WHERE)\s+[\w.]*{col_re}\s*>\s*0", sql, re.I):
        return True
    return False


_EQ_LIT = re.compile(
    r"(?:exam_name|district|dq|school_name|s_name|xx|bj|class_name)\s*=\s*'([^']+)'",
    re.IGNORECASE,
)


def _unbound_literals(sql: str, bound: Sequence[str] | None) -> list[str]:
    allowed = [str(x).strip() for x in (bound or []) if str(x).strip()]
    if not allowed:
        return []
    bad: list[str] = []
    for lit in _EQ_LIT.findall(sql or ""):
        val = str(lit).strip()
        if not val:
            continue
        if any(val == a or val in a or a in val for a in allowed):
            continue
        if val not in bad:
            bad.append(val)
    return bad


def lint_edu_sql_blocks(sql: str, bound_literals: Sequence[str] | None = None) -> list[str]:
    """致命项：不得执行。"""
    s = sql or ""
    blocks: list[str] = []
    if _AVG_REACH.search(s):
        blocks.append(
            "检测到 AVG(reach_rate)：区县/全市达线率须 "
            "SUM(reached_count)/SUM(candidates) 重算，禁止对 reach_rate 求平均。"
        )
    if _MONTH_DISTRICT.search(s):
        blocks.append(
            "检测到疑似把「N月」拼进区县的字面量（如 '月广陵区'）："
            "请改为真实区县名或 district LIKE '%广陵%'，并先 peek_edu_filter_values。"
        )
    missing: list[str] = []
    for m in list(_SUBJECT_STAT.finditer(s)) + list(_PERCENTILE_SUBJECT.finditer(s)):
        col = (m.group("col") or "").lower()
        if col and col not in missing and not _subject_stat_excludes_zero(s, col, m.end()):
            missing.append(col)
    if missing:
        cols = "、".join(missing)
        blocks.append(
            f"检测到对 {cols} 的分数统计未排除 0 分：未选考/缺考在宽表为 0，"
            "计入会把选考科均分拉低、标准差/均衡性失真。"
            "凡均分/标准差/方差/中位/最值/分数段，须 AGG(col) FILTER (WHERE col > 0)，"
            "并同时给出 COUNT(*) FILTER (WHERE col > 0) 作为该科参考人数。"
        )
    if _overview_rank_missing_enrolled(s):
        blocks.append(
            "检测到全市班级/学校排名未排除市报生：tb_score_overview 须 "
            "AND xsxz='在籍生'，否则往届/市报虚拟班会挤占名次。"
        )
    unbound = _unbound_literals(s, bound_literals)
    if unbound:
        blocks.append(
            "WHERE 字面量不在本轮已绑定集合："
            + "、".join(unbound)
            + "。必须使用已确认的考试/学校/班级/区县。"
        )
    return blocks


def lint_edu_sql(sql: str, bound_literals: Sequence[str] | None = None) -> list[str]:
    """兼容旧接口：返回全部护栏文案（含致命项）。"""
    return lint_edu_sql_blocks(sql, bound_literals)


def _excludes_shibao(sql: str) -> bool:
    s = sql or ""
    if re.search(r"xsxz\s*=\s*'在籍生'", s, re.I):
        return True
    if re.search(r"xsxz[^\n]{0,60}市报", s):
        return True
    return False


def _overview_rank_missing_enrolled(sql: str) -> bool:
    s = sql or ""
    if "tb_score_overview" not in s.lower():
        return False
    ranked = bool(re.search(r"\b(?:RANK|DENSE_RANK|ROW_NUMBER)\s*\(", s, re.I))
    by_class = bool(re.search(r"GROUP\s+BY\s+[^;]*\bbj\b", s, re.I))
    if not ranked and not by_class:
        return False
    return not _excludes_shibao(s)


def format_lint_warnings(warnings: list[str]) -> str:
    if not warnings:
        return ""
    lines = ["【SQL lint 警告】请改写后重试："]
    lines.extend(f"- {w}" for w in warnings)
    return "\n".join(lines) + "\n"


def format_lint_blocks(blocks: list[str]) -> str:
    if not blocks:
        return ""
    lines = ["【SQL lint 拦截】禁止执行，请按下列口径改写后再 execute_sql："]
    lines.extend(f"- {w}" for w in blocks)
    return "\n".join(lines) + "\n"


__all__ = [
    "format_lint_blocks",
    "format_lint_warnings",
    "lint_edu_sql",
    "lint_edu_sql_blocks",
]
