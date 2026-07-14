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


def _student_key(row: dict[str, Any]) -> str:
    for k in ("student_id", "student_name", "student", "name", "学号"):
        v = row.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""


def _exam_key(row: dict[str, Any]) -> str:
    for k in ("exam_id", "exam_name", "exam"):
        v = row.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""


def pick_primary_exam_id(score_rows: list[dict[str, Any]]) -> str | None:
    """多场考试并存时，选参与学生数（或行数）最多的一场。"""
    by_exam_students: dict[str, set[str]] = defaultdict(set)
    by_exam_rows: dict[str, int] = defaultdict(int)
    for r in score_rows:
        if not isinstance(r, dict):
            continue
        eid = _exam_key(r)
        if not eid:
            continue
        by_exam_rows[eid] += 1
        sk = _student_key(r)
        if sk:
            by_exam_students[eid].add(sk)
    if not by_exam_rows:
        return None

    def _sort_key(eid: str) -> tuple[int, int, int]:
        students = len(by_exam_students.get(eid) or ())
        rows = by_exam_rows[eid]
        try:
            id_num = int(float(eid))
        except (TypeError, ValueError):
            id_num = 0
        return (students or rows, rows, id_num)

    return max(by_exam_rows.keys(), key=_sort_key)


def narrow_score_rows_to_primary_exam(
    score_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], str | None]:
    """若含多场考试，只保留参与人数最多的一场。"""
    rows = [dict(r) for r in score_rows if isinstance(r, dict)]
    exams = {_exam_key(r) for r in rows if _exam_key(r)}
    if len(exams) <= 1:
        return rows, (next(iter(exams)) if exams else None)
    primary = pick_primary_exam_id(rows)
    if not primary:
        return rows, None
    return [r for r in rows if _exam_key(r) == primary], primary


def dedupe_score_rows_by_student(
    score_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """同一学生（+科目）保留一行；无学生标识的行全部保留。"""
    out: list[dict[str, Any]] = []
    index_by_key: dict[str, int] = {}
    for r in score_rows:
        if not isinstance(r, dict):
            continue
        nr = dict(r)
        sk = _student_key(nr)
        if not sk:
            out.append(nr)
            continue
        subj = str(nr.get("subject") or nr.get("subject_name") or "").strip()
        key = f"{sk}|{subj}"
        if key in index_by_key:
            out[index_by_key[key]] = nr
        else:
            index_by_key[key] = len(out)
            out.append(nr)
    return out


def prepare_score_rows_for_kpi(
    score_rows: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """单场 KPI 口径：收敛多场考试 + 按学生去重，避免班级人数按人次膨胀。"""
    narrowed, _ = narrow_score_rows_to_primary_exam(list(score_rows or []))
    return dedupe_score_rows_by_student(narrowed)


__all__ = [
    "DIMENSIONS",
    "aggregate_by",
    "aggregate_hierarchy",
    "dedupe_score_rows_by_student",
    "narrow_score_rows_to_primary_exam",
    "pick_primary_exam_id",
    "prepare_score_rows_for_kpi",
]
