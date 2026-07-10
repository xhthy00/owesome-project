"""多维聚合——按维度 GROUP BY 后计算 KPI。"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from src.agent.education.config import EducationConfig
from src.agent.education.dimension_parse import parse_grade_from_class
from src.agent.education.stats import compute_score_stats

DIMENSIONS = (
    "citywide",
    "district",
    "school",
    "grade",
    "class",
    "subject",
    "score_segment",
    "question",
    "question_type",
    "knowledge",
)


def _row_key(row: dict[str, Any], dimension: str) -> str:
    if dimension == "citywide":
        return "全市"
    if dimension == "grade":
        cls = row.get("class_name") or row.get("class") or ""
        return parse_grade_from_class(str(cls)) or "未识别年级"
    if dimension == "district":
        return str(row.get("district") or row.get("区县") or "未知区县")
    if dimension == "school":
        return str(row.get("school_name") or row.get("school") or "未知学校")
    if dimension == "class":
        return str(row.get("class_name") or row.get("class") or "未知班级")
    if dimension == "subject":
        return str(row.get("subject") or row.get("subject_name") or "未知学科")
    if dimension == "question":
        return str(row.get("question_no") or "未知题目")
    if dimension == "question_type":
        return str(row.get("question_type") or "未知题型")
    if dimension == "knowledge":
        return str(row.get("knowledge_name") or "未关联知识点")
    if dimension == "score_segment":
        score = _float(row.get("score"))
        if score is None:
            return "未知"
        fs = _float(row.get("exam_score") or row.get("full_score"))
        from src.agent.education.stats import score_segment_label

        return score_segment_label(score, full_score=fs)
    return str(row.get(dimension) or "未知")


def _group_rows(rows: list[dict[str, Any]], dimension: str) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[_row_key(row, dimension)].append(row)
    return dict(groups)


def aggregate_by(
    dimension: str,
    rows: list[dict[str, Any]],
    config: EducationConfig,
    *,
    score_key: str = "score",
    full_score_key: str = "exam_score",
) -> list[dict[str, Any]]:
    """按单一维度聚合，返回每组 stats + dimension_value。"""
    if dimension == "citywide":
        scores = [_float(row.get(score_key)) for row in rows]
        scores = [s for s in scores if s is not None]
        fs = _resolve_full_score(rows, full_score_key)
        stats = compute_score_stats(scores, config, fs)
        return [{"dimension": dimension, "dimension_value": "全市", **stats}]

    if dimension == "score_segment":
        scores = [_float(row.get(score_key)) for row in rows]
        scores = [s for s in scores if s is not None]
        fs = _resolve_full_score(rows, full_score_key)
        base = compute_score_stats(scores, config, fs)
        return [
            {
                "dimension": dimension,
                "dimension_value": seg.get("label"),
                "count": seg.get("count"),
                "ratio": seg.get("ratio"),
            }
            for seg in base.get("segments") or []
        ]

    groups = _group_rows(rows, dimension)
    result: list[dict[str, Any]] = []
    for key, group in sorted(groups.items()):
        scores = [_float(r.get(score_key)) for r in group]
        scores = [s for s in scores if s is not None]
        fs = _resolve_full_score(group, full_score_key)
        stats = compute_score_stats(scores, config, fs)
        result.append({"dimension": dimension, "dimension_value": key, **stats})
    return result


def aggregate_hierarchy(
    rows: list[dict[str, Any]],
    config: EducationConfig,
    *,
    levels: tuple[str, ...] = ("citywide", "district", "school", "grade", "class"),
) -> dict[str, list[dict[str, Any]]]:
    """自上而下多级聚合。"""
    return {level: aggregate_by(level, rows, config) for level in levels}


def _float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _resolve_full_score(rows: list[dict[str, Any]], key: str) -> float | None:
    seen: set[float] = set()
    for row in rows:
        v = _float(row.get(key) or row.get("full_score"))
        if v is not None:
            seen.add(v)
    if not seen:
        return None
    return max(seen) if len(seen) > 1 else seen.pop()


__all__ = ["DIMENSIONS", "aggregate_by", "aggregate_hierarchy"]
