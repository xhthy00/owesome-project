"""成绩数据字段映射——兼容宽表与标准分表两种结构。

为什么需要这一层
----------------
各校成绩表结构五花八门，但大致两类：

1. **宽表**：一行一个学生一次考试，每个科目一列（``math`` / ``chinese`` / ...）。
   项目测试与文档示例里的 ``student_score`` 即此模式。
2. **标准分表**：``student`` / ``class`` / ``exam`` / ``score`` 四张表 JOIN，
   ``score`` 表里一行一个学生一个科目一次考试。

直接让 LLM 凭 Schema 写 SQL 在简单场景可行，但要做"班级各科均分 + 分数段
+ 排名"这类聚合统计时，LLM 容易在 JOIN / UNPIVOT（宽表转长表）上出错。
``ScoreSchemaMapping`` 把字段差异固定下来，``data_adapter`` 据此把查询结果
归一化成统一的 ``NormalizedScoreRow``，统计层只需面向一种数据形态写。

映射来源
--------
``resolve_score_schema`` 的优先级：``config/education_schema.json``（或环境变量
``EDU_SCHEMA_CONFIG_PATH`` 指定路径）> 数据源 schema 启发式推断 > 内置宽表默认。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_DEFAULT_SCHEMA_CONFIG_PATH = Path("config/education_schema.json")


@dataclass
class ScoreSchemaMapping:
    """成绩数据字段映射。

    ``mode == "wide"`` 时使用 ``table`` + ``subject_columns``；
    ``mode == "normalized"`` 时使用 ``tables`` + ``fields`` + ``joins``。
    """

    mode: str  # "wide" | "normalized"
    table: str = ""  # wide 模式主表名
    tables: dict[str, str] = field(default_factory=dict)  # normalized 模式各表别名
    joins: list[str] = field(default_factory=list)
    fields: dict[str, str] = field(default_factory=dict)
    #: 宽表模式：科目名 → 列名。
    subject_columns: dict[str, str] = field(default_factory=dict)
    #: 推断来源，便于排查为何选错映射。
    source: str = "default"

    def subject_column(self, subject: str) -> str | None:
        return self.subject_columns.get(subject)


# ---- 启发式推断 -----------------------------------------------------------

#: 常见学生/班级/考试/成绩字段别名（小写匹配），用于从 schema 猜字段。
_STUDENT_ID_HINTS = ("student_id", "stu_id", "sid", "id", "学号")
_STUDENT_NAME_HINTS = ("student_name", "stu_name", "name", "姓名", "学生姓名")
_CLASS_HINTS = ("class_name", "class", "cls", "班级", "班")
_EXAM_HINTS = ("exam_name", "exam", "考试", "考试名称", "exam_id")
_SUBJECT_HINTS = ("subject", "科目", "课程", "course")
_SCORE_HINTS = ("score", "grade", "分数", "成绩", "得分")
#: 常见科目列名（宽表模式）。
_SUBJECT_COLUMN_HINTS = {
    "语文": ("chinese", "yuwen", "语文"),
    "数学": ("math", "maths", "shuxue", "数学"),
    "英语": ("english", "yingyu", "英语", "外语"),
    "物理": ("physics", "wuli", "物理"),
    "化学": ("chemistry", "huaxue", "化学"),
    "生物": ("biology", "shengwu", "生物"),
    "政治": ("politics", "zhengzhi", "政治"),
    "历史": ("history", "lishi", "历史"),
    "地理": ("geography", "dili", "地理"),
}


def _norm(s: str) -> str:
    return (s or "").strip().lower()


def _match_field(fields: list[dict[str, Any]], hints: tuple[str, ...]) -> str | None:
    hint_set = {_norm(h) for h in hints}
    for f in fields:
        name = _norm(str(f.get("name") or ""))
        comment = _norm(str(f.get("comment") or ""))
        if name in hint_set or comment in hint_set:
            return str(f.get("name") or "")
        # 子串命中（如 comment="学生ID"）
        for h in hint_set:
            if h and (h in name or h in comment):
                return str(f.get("name") or "")
    return None


def infer_wide_mapping(
    table_name: str,
    fields: list[dict[str, Any]],
) -> ScoreSchemaMapping:
    """从一张宽表的字段列表推断映射。"""
    subject_cols: dict[str, str] = {}
    field_names = {_norm(str(f.get("name") or "")) for f in fields}
    for subject, candidates in _SUBJECT_COLUMN_HINTS.items():
        for cand in candidates:
            if cand in field_names:
                subject_cols[subject] = cand
                break

    return ScoreSchemaMapping(
        mode="wide",
        table=table_name,
        subject_columns=subject_cols,
        fields={
            "student_id": _match_field(fields, _STUDENT_ID_HINTS) or "",
            "student_name": _match_field(fields, _STUDENT_NAME_HINTS) or "",
            "class_name": _match_field(fields, _CLASS_HINTS) or "",
            "exam_name": _match_field(fields, _EXAM_HINTS) or "",
        },
        source="inferred_wide",
    )


def infer_normalized_mapping(schema: list[dict[str, Any]]) -> ScoreSchemaMapping | None:
    """从多表 schema 推断分表映射——Phase 1 仅做最简识别：找到名字含
    ``student/score/exam/class`` 的表并按 hints 取字段。识别不到返回 None。"""
    by_name = {str(t.get("name") or ""): t for t in schema}

    def find(keywords: tuple[str, ...]) -> dict[str, Any] | None:
        for name, t in by_name.items():
            low = _norm(name + " " + str(t.get("comment") or ""))
            if any(k in low for k in keywords):
                return t
        return None

    student_t = find(("student", "学生"))
    score_t = find(("score", "成绩"))
    if not student_t or not score_t:
        return None
    # 同一张表（如宽表 ``student_score``）同时命中 student/score 关键词时，
    # 不应判为分表模式——分表要求学生表与成绩表是**不同的**物理表。
    if str(student_t.get("name") or "") == str(score_t.get("name") or ""):
        return None
    exam_t = find(("exam", "考试"))
    class_t = find(("class", "班级"))

    student_fields = student_t.get("fields") or []
    score_fields = score_t.get("fields") or []

    mapping = ScoreSchemaMapping(
        mode="normalized",
        tables={
            "student": str(student_t.get("name") or ""),
            "score": str(score_t.get("name") or ""),
        },
        fields={
            "student_id": _match_field(student_fields, _STUDENT_ID_HINTS) or "id",
            "student_name": _match_field(student_fields, _STUDENT_NAME_HINTS) or "",
            "subject": _match_field(score_fields, _SUBJECT_HINTS) or "subject",
            "score": _match_field(score_fields, _SCORE_HINTS) or "score",
        },
        source="inferred_normalized",
    )
    if exam_t:
        mapping.tables["exam"] = str(exam_t.get("name") or "")
        mapping.fields["exam_name"] = _match_field(exam_t.get("fields") or [], _EXAM_HINTS) or ""
    if class_t:
        mapping.tables["class"] = str(class_t.get("name") or "")
        mapping.fields["class_name"] = _match_field(class_t.get("fields") or [], _CLASS_HINTS) or ""
    return mapping


__all__ = [
    "ScoreSchemaMapping",
    "infer_normalized_mapping",
    "infer_wide_mapping",
]
