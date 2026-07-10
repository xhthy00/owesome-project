"""维度解析——年级从 class 提取，区县从 school 行读取。"""

from __future__ import annotations

import re
from typing import Any

_GRADE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^(高[一二三])"),
    re.compile(r"^(初[一二三])"),
    re.compile(r"^([一二三四五六七八九]年级)"),
)


def parse_grade_from_class(class_name: str) -> str | None:
    """从班级名提取年级，如「高一(1)班」→「高一」。"""
    raw = re.sub(r"\s+", "", str(class_name or ""))
    if not raw:
        return None
    for pat in _GRADE_PATTERNS:
        m = pat.match(raw)
        if m:
            return m.group(1)
    return None


def parse_class_only(class_name: str) -> str:
    """保留完整班级名。"""
    return re.sub(r"\s+", "", str(class_name or ""))


def parse_district(school_row: dict[str, Any] | None) -> str | None:
    """读取学校行中的区县字段。"""
    if not school_row:
        return None
    for key in ("district", "区县", "district_name"):
        val = school_row.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    return None


def class_matches_grade(class_name: str, grade: str) -> bool:
    """判断班级是否属于指定年级（前缀或解析结果匹配）。"""
    g = re.sub(r"\s+", "", str(grade or ""))
    if not g:
        return True
    cls = parse_class_only(class_name)
    parsed = parse_grade_from_class(cls)
    if parsed and parsed == g:
        return True
    return cls.startswith(g)


__all__ = [
    "class_matches_grade",
    "parse_class_only",
    "parse_district",
    "parse_grade_from_class",
]
