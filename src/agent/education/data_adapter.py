"""把 SQL 原始行归一化为统一的 ``NormalizedScoreRow``。

无论上游是宽表（一行多科）还是分表（一行一科一次考试），归一化后都是
"一个学生 + 一个科目 + 一次考试 + 一个分数"的长表行——统计层据此写一份逻辑
即可，不必为两种结构各写一套。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.agent.education.schema_mapping import ScoreSchemaMapping


@dataclass
class NormalizedScoreRow:
    student_id: str
    student_name: str = ""
    class_name: str = ""
    exam_name: str = ""
    subject: str = ""
    score: float | None = None


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _cell(row: dict[str, Any], key: str) -> Any:
    if not key:
        return None
    # 列名可能带表别名前缀（如 ``s.name``），先精确再后缀匹配。
    if key in row:
        return row[key]
    suffix = "." + key.split(".")[-1]
    for k, v in row.items():
        if k == key or k.endswith(suffix):
            return v
    return None


def normalize_rows(
    rows: list[dict[str, Any]],
    mapping: ScoreSchemaMapping,
) -> list[NormalizedScoreRow]:
    """把查询结果行按 ``mapping`` 归一化。

    宽表模式：对每个 ``subject_columns`` 条目拆出一行，``score`` 取该列值；
    分表模式：一行直接映射成一条 ``NormalizedScoreRow``。
    """
    out: list[NormalizedScoreRow] = []
    if not rows:
        return out

    if mapping.mode == "wide":
        for row in rows:
            sid = str(_cell(row, mapping.fields.get("student_id") or "") or "")
            sname = str(_cell(row, mapping.fields.get("student_name") or "") or "")
            cls = str(_cell(row, mapping.fields.get("class_name") or "") or "")
            exam = str(_cell(row, mapping.fields.get("exam_name") or "") or "")
            for subject, col in mapping.subject_columns.items():
                score = _num(_cell(row, col))
                out.append(NormalizedScoreRow(
                    student_id=sid,
                    student_name=sname,
                    class_name=cls,
                    exam_name=exam,
                    subject=subject,
                    score=score,
                ))
        return out

    # normalized
    sid_key = mapping.fields.get("student_id") or ""
    sname_key = mapping.fields.get("student_name") or ""
    cls_key = mapping.fields.get("class_name") or ""
    exam_key = mapping.fields.get("exam_name") or ""
    subj_key = mapping.fields.get("subject") or "subject"
    score_key = mapping.fields.get("score") or "score"
    for row in rows:
        out.append(NormalizedScoreRow(
            student_id=str(_cell(row, sid_key) or ""),
            student_name=str(_cell(row, sname_key) or ""),
            class_name=str(_cell(row, cls_key) or ""),
            exam_name=str(_cell(row, exam_key) or ""),
            subject=str(_cell(row, subj_key) or ""),
            score=_num(_cell(row, score_key)),
        ))
    return out


def group_by_subject(rows: list[NormalizedScoreRow]) -> dict[str, list[float]]:
    """按科目分组，返回 ``{科目: [分数, ...]}``（剔除 None）。"""
    grouped: dict[str, list[float]] = {}
    for r in rows:
        if r.score is None:
            continue
        grouped.setdefault(r.subject or "总分", []).append(r.score)
    return grouped


def group_by_class_subject(rows: list[NormalizedScoreRow]) -> dict[tuple[str, str], list[float]]:
    """按 (班级, 科目) 分组。班级为空时用 ``"未知班级"``。"""
    grouped: dict[tuple[str, str], list[float]] = {}
    for r in rows:
        if r.score is None:
            continue
        key = (r.class_name or "未知班级", r.subject or "总分")
        grouped.setdefault(key, []).append(r.score)
    return grouped


__all__ = [
    "NormalizedScoreRow",
    "group_by_class_subject",
    "group_by_subject",
    "normalize_rows",
]
