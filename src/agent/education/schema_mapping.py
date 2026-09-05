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
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_DEFAULT_SCHEMA_CONFIG_PATH = Path("config/education_schema.json")

#: 用户口中的「考试」= 批次；tb_exam 是批次下的单科试卷。
EXAM_JOIN = (
    "JOIN tb_exam e ON sc.exam_id = e.id\n"
    "LEFT JOIN tb_exam_batch eb ON e.exam_batch_id = eb.id"
)
EXAM_NAME_SQL = "COALESCE(eb.batch_name, e.exam_name)"
_JC_YEAR_RE = re.compile(r"(\d{4})\s*届")
_JC_YEAR_ONLY_RE = re.compile(r"^(\d{4})(?:\.0+)?$")


def normalize_jc_label(raw: str) -> str:
    """把 ``2026`` / ``2026届`` / ``2026届高三5月模拟`` 收成 ``2026届``。"""
    text = str(raw or "").strip()
    if not text:
        return ""
    matched = _JC_YEAR_RE.search(text)
    if matched:
        year = int(matched.group(1))
        if 1990 <= year <= 2099:
            return f"{year}届"
    only = _JC_YEAR_ONLY_RE.fullmatch(text)
    if only:
        year = int(only.group(1))
        if 1990 <= year <= 2099:
            return f"{year}届"
    return ""


def extract_jc_labels(values: list[str]) -> list[str]:
    """从考试名/届次原值提取去重后的届次标签，新年份在前。"""
    out: list[str] = []
    seen: set[str] = set()
    for raw in values:
        label = normalize_jc_label(raw)
        if not label or label in seen:
            continue
        seen.add(label)
        out.append(label)
    out.sort(reverse=True)
    return out


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


@dataclass
class EducationSchemaMeta:
    """``education_schema.json`` 中的元数据（比例阈值、表注释、维度样例）。"""

    pass_ratio: float = 0.6
    excellent_ratio: float = 0.85
    score_segment_ratios: list[float] = field(default_factory=lambda: [0.6, 0.7, 0.8, 0.9])
    table_comments: dict[str, str] = field(default_factory=dict)
    dimension_samples: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class EducationSchemaBundle:
    """配置加载结果：映射 + 元数据。"""

    mapping: ScoreSchemaMapping
    meta: EducationSchemaMeta = field(default_factory=EducationSchemaMeta)


def _schema_config_path(path: Path | str | None = None) -> Path:
    if path is not None:
        return Path(path)
    env_path = os.environ.get("EDU_SCHEMA_CONFIG_PATH", "").strip()
    if env_path:
        return Path(env_path)
    return _DEFAULT_SCHEMA_CONFIG_PATH


def load_schema_from_config(path: Path | str | None = None) -> EducationSchemaBundle | None:
    """从 ``education_schema.json`` 加载固定 Schema 映射。

    文件不存在或解析失败返回 None（调用方回退启发式推断）。
    """
    cfg_path = _schema_config_path(path)
    if not cfg_path.is_file():
        return None
    try:
        raw = json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(raw, dict):
        return None

    mode = str(raw.get("mode") or "normalized")
    mapping = ScoreSchemaMapping(
        mode=mode,
        table=str(raw.get("table") or ""),
        tables={str(k): str(v) for k, v in (raw.get("tables") or {}).items()},
        joins=[str(j) for j in (raw.get("joins") or [])],
        fields={str(k): str(v) for k, v in (raw.get("fields") or {}).items()},
        subject_columns={
            str(k): str(v) for k, v in (raw.get("subject_columns") or {}).items()
        },
        source=str(raw.get("source") or "config_edu"),
    )
    defaults = raw.get("defaults") if isinstance(raw.get("defaults"), dict) else {}
    meta = EducationSchemaMeta(
        pass_ratio=float(defaults.get("pass_ratio", 0.6)),
        excellent_ratio=float(defaults.get("excellent_ratio", 0.85)),
        score_segment_ratios=[
            float(x) for x in (defaults.get("score_segment_ratios") or [0.6, 0.7, 0.8, 0.9])
        ],
        table_comments={
            str(k): str(v) for k, v in (raw.get("table_comments") or {}).items()
        },
        dimension_samples={
            str(k): [str(x) for x in v]
            for k, v in (raw.get("dimension_samples") or {}).items()
            if isinstance(v, list)
        },
    )
    from src.agent.education.privacy_mode import overlay_schema_fields, overlay_table_comments

    mapping.fields = overlay_schema_fields(mapping.fields)
    meta.table_comments = overlay_table_comments(meta.table_comments)
    return EducationSchemaBundle(mapping=mapping, meta=meta)


def validate_mapping_against_schema(
    mapping: ScoreSchemaMapping,
    live_schema: list[dict[str, Any]],
) -> list[str]:
    """对比配置映射与数据源实际表名，返回缺失表 warning 列表（不阻断）。"""
    live_names = {str(t.get("name") or "") for t in live_schema}
    warnings: list[str] = []
    if mapping.mode == "wide":
        if mapping.table and mapping.table not in live_names:
            warnings.append(f"宽表 `{mapping.table}` 在当前数据源中不存在")
        return warnings
    for role, table_name in mapping.tables.items():
        if table_name and table_name not in live_names:
            warnings.append(f"配置表 `{table_name}`（{role}）在当前数据源中不存在")
    return warnings


def get_table_comments_from_config(path: Path | str | None = None) -> dict[str, str]:
    """读取配置中的 table_comments；无配置时返回空 dict。"""
    bundle = load_schema_from_config(path)
    if bundle is None:
        return {}
    return dict(bundle.meta.table_comments)


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
    "EXAM_JOIN",
    "EXAM_NAME_SQL",
    "EducationSchemaBundle",
    "EducationSchemaMeta",
    "ScoreSchemaMapping",
    "extract_jc_labels",
    "get_table_comments_from_config",
    "infer_normalized_mapping",
    "infer_wide_mapping",
    "load_schema_from_config",
    "normalize_jc_label",
    "validate_mapping_against_schema",
]
