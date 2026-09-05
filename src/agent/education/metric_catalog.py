"""小型认证指标表：口语别名 → 确定性 SQL 编译。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from src.agent.education.clarification import SLOT_CLASS, SLOT_EXAM, SLOT_SCHOOL, SLOT_SUBJECT
from src.agent.education.kpi_sql import SCORE_JOIN_FROM, build_kpi_aggregate_sql
from src.agent.education.query_parse import (
    is_line_reach_query,
    is_overview_total_query,
    is_score_stat_query,
    is_subject_strength_query,
)
from src.agent.education.schema_mapping import EXAM_NAME_SQL

METRIC_SCORE_KPI = "score_kpi"
METRIC_LINE_REACH = "line_reach_count"
METRIC_SUBJECT_STRENGTH = "subject_strength"
METRIC_OVERVIEW_TOTAL = "overview_total"

_OVERVIEW_COL = {
    "三门": "zf3m",
    "语数外": "zf3m",
    "语数英": "zf3m",
    "四门": "zf4m",
    "六门": "zf6m",
    "全科": "zf6m",
}


@dataclass(frozen=True)
class MetricSpec:
    id: str
    aliases: tuple[str, ...]
    required_dims: tuple[str, ...]


_CATALOG: tuple[MetricSpec, ...] = (
    MetricSpec(
        METRIC_OVERVIEW_TOTAL,
        ("语数外", "三门均分", "四门均分", "六门均分", "全科总分", "zf3m", "zf4m", "zf6m"),
        (SLOT_EXAM,),
    ),
    MetricSpec(
        METRIC_LINE_REACH,
        ("达线", "上线", "本科线", "特控线", "分数线"),
        (SLOT_EXAM,),
    ),
    MetricSpec(
        METRIC_SUBJECT_STRENGTH,
        ("优势学科", "薄弱学科", "强项", "弱项", "短板"),
        (SLOT_EXAM,),
    ),
    MetricSpec(
        METRIC_SCORE_KPI,
        ("均分", "平均分", "及格率", "优秀率", "最高分", "最低分", "怎么样", "成绩"),
        (SLOT_EXAM,),
    ),
)


def metric_by_id(metric_id: str) -> MetricSpec | None:
    mid = str(metric_id or "").strip()
    if not mid:
        return None
    return next((s for s in _CATALOG if s.id == mid), None)


def resolve_metric(question: str) -> MetricSpec | None:
    """命中认证指标；未命中则走受约束 ReAct。"""
    q = (question or "").strip()
    if not q:
        return None
    if is_overview_total_query(q):
        return next(s for s in _CATALOG if s.id == METRIC_OVERVIEW_TOTAL)
    if is_line_reach_query(q):
        return next(s for s in _CATALOG if s.id == METRIC_LINE_REACH)
    if is_subject_strength_query(q):
        return next(s for s in _CATALOG if s.id == METRIC_SUBJECT_STRENGTH)
    if is_score_stat_query(q):
        return next(s for s in _CATALOG if s.id == METRIC_SCORE_KPI)
    return None


def _sql_quote(value: str) -> str:
    return str(value or "").replace("'", "''")


def build_metric_where(bound: Mapping[str, str], *, enrolled: bool = True) -> str:
    """用已绑定规范名拼 WHERE，不让模型写过滤。"""
    parts: list[str] = []
    exam = str((bound or {}).get(SLOT_EXAM) or "").strip()
    school = str((bound or {}).get(SLOT_SCHOOL) or "").strip()
    cls = str((bound or {}).get(SLOT_CLASS) or "").strip()
    subject = str((bound or {}).get(SLOT_SUBJECT) or "").strip()
    district = str((bound or {}).get("district") or "").strip()
    if exam:
        parts.append(f"{EXAM_NAME_SQL} = '{_sql_quote(exam)}'")
    if school:
        parts.append(
            f"(sch.s_name LIKE '%{_sql_quote(school)}%' OR sch.name LIKE '%{_sql_quote(school)}%')"
        )
    if cls:
        parts.append(
            f"(sc.class_name = '{_sql_quote(cls)}' OR sc.bj = '{_sql_quote(cls)}')"
        )
    if subject:
        parts.append(
            f"(e.subject_name LIKE '%{_sql_quote(subject)}%' OR e.subject LIKE '%{_sql_quote(subject)}%')"
        )
    if district:
        parts.append(
            f"(sc.district LIKE '%{_sql_quote(district)}%' OR sch.district LIKE '%{_sql_quote(district)}%')"
        )
    if enrolled:
        parts.append("(sc.xsxz = '在籍生' OR sc.xsxz IS NULL)")
    if not parts:
        return ""
    return "WHERE " + " AND ".join(parts)


def overview_score_column(question: str) -> str:
    q = question or ""
    for hint, col in _OVERVIEW_COL.items():
        if hint in q:
            return col
    return "zf6m"


def compile_metric_sql(
    spec: MetricSpec,
    bound: Mapping[str, str],
    *,
    question: str = "",
    config: Any = None,
) -> str:
    """编译认证指标 SQL。"""
    from src.agent.education.config import EducationConfig

    cfg = config if isinstance(config, EducationConfig) else EducationConfig()
    where = build_metric_where(bound)
    if spec.id == METRIC_SCORE_KPI:
        return build_kpi_aggregate_sql(where, cfg)
    if spec.id == METRIC_OVERVIEW_TOTAL:
        col = overview_score_column(question)
        where_ov = where.replace("sc.class_name", "bj").replace("sc.bj", "bj")
        where_ov = where_ov.replace("sc.xsxz", "xsxz").replace("sc.district", "dq")
        return (
            "SELECT "
            f"ROUND(AVG({col})::numeric, 2) AS avg, "
            f"COUNT(*) FILTER (WHERE {col} > 0) AS n "
            f"FROM tb_score_overview\n{where_ov}\n"
            "AND xsxz = '在籍生'"
        )
    if spec.id == METRIC_LINE_REACH:
        exam = _sql_quote(str((bound or {}).get(SLOT_EXAM) or ""))
        school = _sql_quote(str((bound or {}).get(SLOT_SCHOOL) or ""))
        bits = [f"exam_name = '{exam}'"] if exam else []
        if school:
            bits.append(f"school_name LIKE '%{school}%'")
        district = _sql_quote(str((bound or {}).get("district") or ""))
        if district:
            bits.append(f"district LIKE '%{district}%'")
        where_lr = ("WHERE " + " AND ".join(bits)) if bits else ""
        return (
            "SELECT line_name, "
            "SUM(reached_count) AS reached, "
            "SUM(candidates) AS candidates, "
            "ROUND(SUM(reached_count)::numeric / NULLIF(SUM(candidates), 0) * 100, 2) AS rate "
            f"FROM tb_score_indicator\n{where_lr}\n"
            "GROUP BY line_name"
        )
    # 优势/薄弱：各科均分全市排名骨架，供工具执行
    return (
        "SELECT e.subject_name AS subject, "
        "ROUND(AVG(sc.score) FILTER (WHERE sc.score > 0)::numeric, 2) AS avg, "
        "COUNT(*) FILTER (WHERE sc.score > 0) AS n "
        f"{SCORE_JOIN_FROM}\n{where}\n"
        "GROUP BY e.subject_name"
    )


def format_metric_plan(spec: MetricSpec, bound: Mapping[str, str], question: str) -> str:
    """生成「调认证指标工具」子任务文案。"""
    bits = [f"metric_id='{spec.id}'"]
    if bound.get(SLOT_EXAM):
        bits.append(f"exam_name='{bound[SLOT_EXAM]}'")
    if bound.get(SLOT_SCHOOL):
        bits.append(f"school_name='{bound[SLOT_SCHOOL]}'")
    if bound.get(SLOT_CLASS):
        bits.append(f"class_name='{bound[SLOT_CLASS]}'")
    if bound.get(SLOT_SUBJECT):
        bits.append(f"subject_name='{bound[SLOT_SUBJECT]}'")
    args = ", ".join(bits)
    return (
        f"调 query_certified_metric_tool({args}) 按认证口径直接出数；"
        "**禁止** execute_sql 手写均分/达线率；"
        "**禁止**调任何 build_*_report / render_html / 生成 HTML 报告；"
        "答完即 terminate。"
        f"原问：{question}"
    )


__all__ = [
    "METRIC_LINE_REACH",
    "METRIC_OVERVIEW_TOTAL",
    "METRIC_SCORE_KPI",
    "METRIC_SUBJECT_STRENGTH",
    "MetricSpec",
    "build_metric_where",
    "compile_metric_sql",
    "format_metric_plan",
    "metric_by_id",
    "overview_score_column",
    "resolve_metric",
]
