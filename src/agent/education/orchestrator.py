"""报告编排层（Phase 3）。

将「意图识别 → 查询规划 → 统计 → 图表 → 渲染」收敛为一条确定性管线，
作为 Team 模式（Planner → 子任务）的替代路径。Team 模式擅长多步 LLM 推理，
Orchestrator 擅长**结构已知**的报告：报告类型一旦识别，后续步骤无需 LLM 介入，
全部走领域工具，输出可复现。

设计要点：
- ``ReportIntentResolver``：纯规则的关键词匹配，把自然语言映射到 ``ReportSpec``。
  识别失败时回退 ``class_overview``，保证总有产出。
- ``ReportOrchestrator``：依赖注入 ``execute_sql`` 与 ``resolve_schema`` 两个可替换
  的协程回调，便于单测用 mock 替换真实数据源；统计/图表/渲染复用 ``education``
  包内既有纯函数与 ``render_html_report``。
- 不直接对接 SSE/HTTP，由调用方（未来的 ``agent_mode=report`` 入口或 API）
  取 ``ReportResult`` 后自行 emit。
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from src.agent.education.charts import build_chart_option
from src.agent.education.config import EducationConfig, load_config
from src.agent.education.report_types import Audience, ReportSpec, ReportType
from src.agent.education.schema_mapping import ScoreSchemaMapping
from src.agent.education.stats import compute_rankings, compute_score_stats, identify_at_risk_students
from src.agent.education.templates import select_report_template

logger = logging.getLogger(__name__)

#: 执行 SQL 的回调签名：(sql) -> {"columns": [...], "rows": [[...], ...], "row_count": int}
ExecuteSqlFn = Callable[[str], Awaitable[dict[str, Any]]]
#: 解析 schema 的回调签名：() -> ScoreSchemaMapping
ResolveSchemaFn = Callable[[], Awaitable[ScoreSchemaMapping]]


# ---- 意图识别 ------------------------------------------------------------

# 关键词 → ReportType。顺序敏感：越具体越靠前。
_INTENT_KEYWORDS: list[tuple[ReportType, tuple[str, ...]]] = [
    (ReportType.COMPREHENSIVE, ("综合分析报告", "综合报告", "综合分析", "多次考试", "三次考试", "两次考试", "纵向分析", "学业诊断报告")),
    (ReportType.TIER_ALERT, ("预警", "临界生", "退步生", "偏科", "分层")),
    (ReportType.TREND_TRACKING, ("趋势", "变化", "历次", "走势", "进退步")),
    (ReportType.STUDENT_PROFILE, ("学生个体", "个人报告", "该生", "这名学生", "学情报告", "这几次考试", "考试成绩分析")),
    (ReportType.SUBJECT_DIAGNOSIS, ("科目诊断", "科目分析", "学科诊断", "某科", "数学分析", "语文分析")),
    (ReportType.GRADE_COMPARISON, ("年级对比", "各班对比", "班级对比", "年级排名", "班级排名")),
    (ReportType.CLASS_OVERVIEW, ("班级总览", "班级报告", "班级分析", "班级成绩", "期中分析", "期末分析")),
]

_AUDIENCE_KEYWORDS: list[tuple[Audience, tuple[str, ...]]] = [
    (Audience.PARENT, ("家长", "给家长", "家长版")),
    (Audience.PRINCIPAL, ("校长", "给校长")),
    (Audience.HEAD_TEACHER, ("班主任", "给班主任")),
    (Audience.SUBJECT_TEACHER, ("任课", "任课教师", "科任")),
]


class ReportIntentResolver:
    """把自然语言问题映射到 ``ReportSpec``（纯规则，无 LLM）。"""

    def resolve(self, question: str, audience_hint: str | None = None) -> ReportSpec:
        q = (question or "").strip()
        report_type = ReportType.CLASS_OVERVIEW  # 兜底
        for rt, keywords in _INTENT_KEYWORDS:
            if any(k in q for k in keywords):
                report_type = rt
                break

        audience = self._resolve_audience(q, audience_hint)
        # 简单的过滤条件抽取：班级名（初三/初二/高一...N班）、科目、考试名。
        filters: dict[str, str] = {}
        class_name = _extract_class_name(q)
        if class_name:
            filters["class_name"] = class_name
        subject = _extract_subject(q)
        if subject:
            filters["subject"] = subject
        exam_name = _extract_exam(q)
        if exam_name:
            filters["exam_name"] = exam_name
        return ReportSpec(
            report_type=report_type,
            audience=audience,
            filters=filters,
            include_charts=True,
        )

    @staticmethod
    def _resolve_audience(question: str, hint: str | None) -> Audience:
        if hint:
            try:
                return Audience(hint)
            except ValueError:
                pass
        for aud, keywords in _AUDIENCE_KEYWORDS:
            if any(k in question for k in keywords):
                return aud
        return Audience.DEFAULT


import re as _re

_CLASS_RE = _re.compile(r"(初三|初二|初一|高三|高二|高一|九年级|八年级|七年级|六年级|五年级|四年级|三年级)[\d班]*\d?班")
_SUBJECT_RE = _re.compile(r"(数学|语文|英语|物理|化学|生物|政治|历史|地理|科学)")
_EXAM_RE = _re.compile(r"(期中|期末|月考|摸底|模拟|单元测验)")


def _extract_class_name(q: str) -> str | None:
    m = _CLASS_RE.search(q)
    return m.group(0) if m else None


def _extract_subject(q: str) -> str | None:
    m = _SUBJECT_RE.search(q)
    return m.group(1) if m else None


def _extract_exam(q: str) -> str | None:
    m = _EXAM_RE.search(q)
    return m.group(1) if m else None


# ---- 编排 ----------------------------------------------------------------


@dataclass
class ReportResult:
    """Orchestrator 产出。"""

    html: str
    spec: ReportSpec
    template_name: str
    data_keys: list[str]
    stats: dict[str, Any] = field(default_factory=dict)
    charts: dict[str, str] = field(default_factory=dict)
    error: str | None = None


class ReportOrchestrator:
    """端到端报告编排器。

    Args:
        execute_sql: 执行 SQL 的协程回调，返回 ``{columns, rows, row_count}``。
        resolve_schema: 返回 ``ScoreSchemaMapping`` 的协程回调。
        config: 阈值配置；None 时用 ``load_config()`` 默认。
    """

    def __init__(
        self,
        execute_sql: ExecuteSqlFn,
        resolve_schema: ResolveSchemaFn,
        config: EducationConfig | None = None,
    ) -> None:
        self._execute_sql = execute_sql
        self._resolve_schema = resolve_schema
        self._config = config or load_config()
        self.intent_resolver = ReportIntentResolver()

    async def run(
        self,
        question: str,
        audience_hint: str | None = None,
        locked_class: str | None = None,
    ) -> ReportResult:
        spec = self.intent_resolver.resolve(question, audience_hint)
        # 权限联动（Phase 4）：班主任等角色只能看本班时，强制把 class 过滤锁定，
        # 忽略问题里出现的其它班级，复用 locked_tables 的"约束优先"哲学。
        if locked_class:
            spec.filters["class_name"] = locked_class
        template_info = select_report_template(spec.report_type, spec.audience)
        template_name = template_info["template_name"]
        data_keys = list(template_info["data_keys"])

        if not template_name:
            return ReportResult(
                html="",
                spec=spec,
                template_name="",
                data_keys=data_keys,
                error=f"报告类型 {spec.report_type.value} 模板尚未实现",
            )

        try:
            mapping = await self._resolve_schema()
            data = await self._gather_data(spec, mapping)
            # 渲染延迟导入，避免 business 在测试 mock 时循环引用。
            from src.agent.resource.tool.business import _render_template_html

            html = _render_template_html(template_name, data)
            return ReportResult(
                html=html,
                spec=spec,
                template_name=template_name,
                data_keys=data_keys,
                stats=data.get("_stats", {}),
                charts=data.get("_charts", {}),
            )
        except Exception as e:  # noqa: BLE001 - 编排层兜底，避免整条链路抛出
            logger.warning("ReportOrchestrator 失败: %s", e)
            return ReportResult(
                html="",
                spec=spec,
                template_name=template_name,
                data_keys=data_keys,
                error=str(e),
            )

    async def _gather_data(
        self,
        spec: ReportSpec,
        mapping: ScoreSchemaMapping,
    ) -> dict[str, Any]:
        """按报告类型查数 + 统计 + 图表，组装模板 data 字典。

        Phase 3 实现按报告类型的 SQL 模板化查询；为保持编排层可单测，SQL 由
        ``_build_sql`` 生成（基于 mapping），统计/图表走纯函数。
        """
        rows = await self._fetch_score_rows(spec, mapping)
        scores = [float(r.get("score") or 0) for r in rows if r.get("score") is not None]
        stats = compute_score_stats(scores, self._config)

        charts: dict[str, str] = {}
        if spec.include_charts:
            charts["SCORE_DIST_CHART"] = build_chart_option(
                "score_distribution",
                {"segments": stats.get("segments", []), "pass_rate": stats.get("pass_rate")},
                title="分数段分布",
            )

        class_name = spec.filters.get("class_name", "")
        subject = spec.filters.get("subject", "")
        exam_name = spec.filters.get("exam_name", "")
        # 组装通用字段；具体模板未用到的 key 由 Jinja2/regex 兜底为空。
        data: dict[str, Any] = {
            "REPORT_TITLE": self._title(spec),
            "REPORT_SUBTITLE": self._subtitle(spec),
            "REPORT_TIME": _now_str(),
            "CLASS_NAME": class_name,
            "EXAM_NAME": exam_name or "本次考试",
            "SUBJECT_NAME": subject or "全科",
            "GRADE_NAME": "",
            "SCOPE": class_name or "全年级",
            "TOTAL_COUNT": str(stats.get("count") or 0),
            "AVG_SCORE": _fmt(stats.get("avg")),
            "PASS_RATE": _fmt(stats.get("pass_rate")),
            "EXCELLENT_RATE": _fmt(stats.get("excellent_rate")),
            "STDEV": _fmt(stats.get("stdev")),
            "SCORE_DIST_CHART": charts.get("SCORE_DIST_CHART", ""),
            "SUBJECT_RADAR_CHART": "",
            "SUBJECT_BREAKDOWN": "",
            "RANK_INFO": "",
            "SEGMENT_TABLE": _segment_table(stats.get("segments", [])),
            "SUMMARY": "<p>由 ReportOrchestrator 自动生成。</p>",
            "RECOMMENDATIONS": "<p>结合 KPI 与分数段分布关注薄弱区间。</p>",
            "_stats": stats,
            "_charts": charts,
        }
        return data

    async def _fetch_score_rows(
        self,
        spec: ReportSpec,
        mapping: ScoreSchemaMapping,
    ) -> list[dict[str, Any]]:
        sql = self._build_sql(spec, mapping)
        if not sql:
            return []
        result = await self._execute_sql(sql)
        cols = result.get("columns") or []
        raw_rows = result.get("rows") or []
        return [dict(zip(cols, row)) for row in raw_rows]

    def _build_sql(self, spec: ReportSpec, mapping: ScoreSchemaMapping) -> str:
        """按 mapping 生成只读 SELECT。Phase 3 仅覆盖宽表 + 分表的最简均分查询。"""
        class_name = spec.filters.get("class_name")
        subject = spec.filters.get("subject")
        if mapping.mode == "wide":
            table = mapping.table
            # 宽表：取第一个科目列做均分演示（完整实现需遍历 subject_columns）
            subject_col = next(iter(mapping.subject_columns.values()), None)
            if not subject_col:
                return ""
            where = f" WHERE {mapping.fields.get('class_name') or 'class'} = '{class_name}'" if class_name else ""
            return f"SELECT {subject_col} AS score FROM {table}{where} LIMIT 1000"
        # 分表
        score_tbl = mapping.tables.get("score", "score")
        score_field = mapping.fields.get("score", "score")
        subject_field = mapping.fields.get("subject", "subject")
        where_parts: list[str] = []
        if subject:
            where_parts.append(f"{subject_field} = '{subject}'")
        where = (" WHERE " + " AND ".join(where_parts)) if where_parts else ""
        return f"SELECT {score_field} AS score FROM {score_tbl}{where} LIMIT 1000"

    @staticmethod
    def _title(spec: ReportSpec) -> str:
        labels = {
            ReportType.CLASS_OVERVIEW: "班级成绩分析报告",
            ReportType.GRADE_COMPARISON: "年级班级对比报告",
            ReportType.SUBJECT_DIAGNOSIS: "科目诊断报告",
            ReportType.STUDENT_PROFILE: "学生学情报告",
            ReportType.TREND_TRACKING: "成绩趋势追踪报告",
            ReportType.TIER_ALERT: "分层预警报告",
            ReportType.GROUP_FEATURE: "群体特征报告",
            ReportType.COMPREHENSIVE: "综合分析报告",
        }
        prefix = spec.filters.get("class_name") or spec.filters.get("subject") or ""
        return f"{prefix}{labels.get(spec.report_type, '学情报告')}".strip()

    @staticmethod
    def _subtitle(spec: ReportSpec) -> str:
        audience_label = {
            Audience.PARENT: "家长版",
            Audience.PRINCIPAL: "校长版",
            Audience.HEAD_TEACHER: "班主任版",
            Audience.SUBJECT_TEACHER: "任课教师版",
        }.get(spec.audience, "")
        return audience_label or "学情总览"


def _fmt(v: Any) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.2f}"
    return str(v)


def _now_str() -> str:
    from datetime import datetime

    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _segment_table(segments: list[dict[str, Any]]) -> str:
    if not segments:
        return "<p class='edu-sub'>暂无分数段数据</p>"
    rows = "".join(
        f"<tr><td>{s.get('label', '')}</td><td>{s.get('count', 0)}</td><td>{_fmt(s.get('ratio'))}%</td></tr>"
        for s in segments
    )
    return (
        "<table class='edu-table'><thead><tr><th>分数段</th><th>人数</th><th>占比</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
    )


__all__ = ["ReportIntentResolver", "ReportOrchestrator", "ReportResult"]
