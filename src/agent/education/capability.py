"""教育数据源能力检测——新维度字段是否可用。"""

from __future__ import annotations

from typing import Any

_OPTIONAL_FIELD_COLUMNS: dict[str, tuple[str, ...]] = {
    "district": ("district", "区县"),
    "question_type": ("question_type", "题型"),
    "ability_level": ("ability_level", "能力层级"),
}


def _schema_columns(schema: list[dict[str, Any]] | None) -> set[str]:
    cols: set[str] = set()
    if not schema:
        return cols
    for table in schema:
        for field in table.get("fields") or []:
            name = str(field.get("name") or "").lower()
            if name:
                cols.add(name)
    return cols


def detect_available_dimensions(
    schema: list[dict[str, Any]] | None = None,
    *,
    mapping_fields: dict[str, str] | None = None,
) -> dict[str, bool]:
    """检测可选 DB 维度是否可用。年级始终 True（从 class 解析）。"""
    cols = _schema_columns(schema)
    out: dict[str, bool] = {"grade": True, "citywide": True}
    for dim, candidates in _OPTIONAL_FIELD_COLUMNS.items():
        if cols:
            out[dim] = any(c.lower() in cols for c in candidates)
        elif mapping_fields and dim in mapping_fields:
            out[dim] = True
        else:
            out[dim] = False
    return out


def filter_supported_dimensions(
    dimensions: tuple[str, ...] | list[str],
    available: dict[str, bool],
) -> list[str]:
    """过滤不可用维度，citywide/grade/school/class/subject 始终保留。"""
    always = {"citywide", "grade", "school", "class", "subject", "score_segment", "question", "knowledge"}
    result: list[str] = []
    for d in dimensions:
        if d in always or available.get(d, False):
            result.append(d)
    return result


__all__ = ["detect_available_dimensions", "filter_supported_dimensions"]
