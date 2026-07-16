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
from src.agent.education.query_parse import (
    extract_district_target,
    extract_school_target,
    is_citywide_analysis_query,
)
from src.agent.education.report_types import Audience, ReportSpec, ReportType
from src.agent.education.schema_mapping import ScoreSchemaMapping, load_schema_from_config
from src.agent.education.diagnostic_report import build_diagnostic_data
from src.agent.education.knowledge_tier import (
    build_ability_tier_summary,
    build_ability_tier_table_html,
    build_question_type_table_html,
)
from src.agent.education.stats import compute_item_metrics, compute_rankings, compute_score_stats, identify_at_risk_students
from src.agent.education.subject_diagnosis import (
    build_diagnosis_recommendations,
    build_diagnosis_summary,
    build_item_table_html,
    build_knowledge_table_html,
    enrich_knowledge_rows,
    knowledge_names_subquery_join,
    knowledge_weighted_join,
)
from src.agent.education.templates import select_report_template

logger = logging.getLogger(__name__)

#: 执行 SQL 的回调签名：(sql) -> {"columns": [...], "rows": [[...], ...], "row_count": int}
ExecuteSqlFn = Callable[[str], Awaitable[dict[str, Any]]]
#: 解析 schema 的回调签名：() -> ScoreSchemaMapping
ResolveSchemaFn = Callable[[], Awaitable[ScoreSchemaMapping]]


# ---- 意图识别 ------------------------------------------------------------

# 关键词 → ReportType。仅作「非专用探测器」场景的回落；顺序敏感：越具体越靠前。
# 专用探测器（全市 / 个人 / 多场 / 各班对比 / 学校报告）优先于本表。
_INTENT_KEYWORDS: list[tuple[ReportType, tuple[str, ...]]] = [
    (ReportType.DIAGNOSTIC_REPORT, ("结构化诊断", "区域诊断报告", "诊断报告三节", "全市区县诊断")),
    (ReportType.COMPREHENSIVE, (
        "综合分析报告", "综合报告", "综合分析", "多次考试", "三次考试", "两次考试",
        "纵向分析", "所有考试", "全部考试", "历次考试", "各次考试",
        "所有数学考试", "所有语文考试",
    )),
    (ReportType.TIER_ALERT, ("预警", "临界生", "退步生", "偏科", "分层")),
    (ReportType.TREND_TRACKING, ("趋势", "变化", "历次成绩", "历次", "走势", "进退步")),
    (ReportType.STUDENT_PROFILE, ("学生个体", "个人报告", "该生", "这名学生", "这几次考试")),
    # 群体特征须先于班级横向对比关键词回落（避免「对比」泛化）
    (ReportType.GROUP_FEATURE, (
        "群体特征", "群体对比特征", "对比特征", "按班级群体", "群体特征报告", "群体对比分析",
    )),
    # 各班横向对比须先于科目诊断
    (ReportType.GRADE_COMPARISON, (
        "年级对比", "各班对比", "班级对比", "年级排名", "班级排名",
        "各个班级", "各班级", "横向对比", "横向分析", "横向多维", "多维对比", "多维分析",
    )),
    (ReportType.SUBJECT_DIAGNOSIS, (
        "科目诊断", "科目分析", "学科诊断", "某科", "数学分析", "语文分析",
        "小题", "逐题", "每一小题", "每一题", "知识点", "详细分析", "诊断报告",
    )),
    (ReportType.CLASS_OVERVIEW, (
        "班级总览", "成绩总览", "班级成绩总览", "总览报告", "成绩概览", "班级成绩概览",
        "班级报告", "班级分析", "班级成绩", "期中分析", "期末分析",
    )),
]

_AUDIENCE_KEYWORDS: list[tuple[Audience, tuple[str, ...]]] = [
    (Audience.PARENT, ("家长", "给家长", "家长版")),
    (Audience.PRINCIPAL, ("校长", "给校长")),
    (Audience.HEAD_TEACHER, ("班主任", "给班主任")),
    (Audience.SUBJECT_TEACHER, ("任课", "任课教师", "科任")),
]


class ReportIntentResolver:
    """把自然语言问题映射到 ``ReportSpec``（纯规则，无 LLM）。

    优先级与 ``agent_runner`` / Planner 确定性格径对齐：
    全市 → 个人学生 → 成绩趋势 → 多场综合 → 分层预警 → 群体特征 → 班级总览 → 各班横向 →
    结构化诊断 → 学校科目报告 → 关键词回落。
    """

    def resolve(self, question: str, audience_hint: str | None = None) -> ReportSpec:
        q = (question or "").strip()
        from src.agent.education.query_parse import (
            extract_student_target,
            is_citywide_analysis_query,
            is_class_overview_query,
            is_individual_student_analysis_query,
            is_multi_exam_class_analysis_query,
            is_group_feature_query,
            is_school_class_comparison_query,
            is_school_exam_report_query,
            is_structured_diagnostic_query,
            is_tier_alert_query,
            is_trend_tracking_query,
        )

        if is_citywide_analysis_query(q):
            report_type = ReportType.DIAGNOSTIC_REPORT
        elif is_individual_student_analysis_query(q):
            report_type = ReportType.STUDENT_PROFILE
        elif is_trend_tracking_query(q):
            report_type = ReportType.TREND_TRACKING
        elif is_multi_exam_class_analysis_query(q):
            report_type = ReportType.COMPREHENSIVE
        elif is_tier_alert_query(q):
            report_type = ReportType.TIER_ALERT
        elif is_group_feature_query(q):
            report_type = ReportType.GROUP_FEATURE
        elif is_class_overview_query(q):
            report_type = ReportType.CLASS_OVERVIEW
        elif is_school_class_comparison_query(q):
            report_type = ReportType.GRADE_COMPARISON
        elif is_structured_diagnostic_query(q):
            report_type = ReportType.DIAGNOSTIC_REPORT
        elif is_school_exam_report_query(q):
            report_type = ReportType.SUBJECT_DIAGNOSIS
        elif "学情报告" in q and any(
            h in q for h in ("孩子", "家长", "该生", "学生", "个人", "这名")
        ):
            # 无明确学号时的家长/个体学情（探测器要求 extract_student_target）
            report_type = ReportType.STUDENT_PROFILE
        elif extract_student_target(q) and any(
            h in q for h in ("知识点", "成绩分析", "学情", "薄弱", "加强", "分析报告")
        ):
            report_type = ReportType.STUDENT_PROFILE
        else:
            report_type = ReportType.CLASS_OVERVIEW  # 兜底
            for rt, keywords in _INTENT_KEYWORDS:
                if any(k in q for k in keywords):
                    report_type = rt
                    break

        audience = self._resolve_audience(q, audience_hint)
        # 简单的过滤条件抽取：班级名（初三/初二/高一...N班）、科目、考试名。
        filters: dict[str, str] = {}
        class_name = _extract_class_name(q)
        if class_name and report_type != ReportType.GRADE_COMPARISON:
            filters["class_name"] = class_name
        subject = _extract_subject(q)
        if subject:
            filters["subject"] = subject
        exam_name = _extract_exam(q)
        if exam_name:
            filters["exam_name"] = exam_name
        school_name = extract_school_target(q)
        if school_name:
            filters["school_name"] = school_name
        district_name = extract_district_target(q)
        if district_name:
            filters["district"] = district_name
        if is_citywide_analysis_query(q):
            filters["scope"] = "全市"
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

_CLASS_RE = _re.compile(
    r"高[一二三]\(\d+\)班|"
    r"(初三|初二|初一|高三|高二|高一|九年级|八年级|七年级|六年级|五年级|四年级|三年级)[\d班]*\d?班"
)
_SUBJECT_RE = _re.compile(r"(数学|语文|英语|物理|化学|生物|政治|历史|地理|科学)")
_EXAM_RE = _re.compile(r"(期中|期末|月考|摸底|模拟|单元测验)")
_EXAM_FULL_RE = _re.compile(
    r"([\u4e00-\u9fff]{2,30}?(?:质量检测|模拟考试|学情检测|单元测验|期末考试|期中考试|检测试卷))"
)


def _extract_class_name(q: str) -> str | None:
    from src.agent.education.query_parse import normalize_fullwidth_parentheses

    text = normalize_fullwidth_parentheses(q or "")
    m = _CLASS_RE.search(text)
    return m.group(0) if m else None


def _extract_subject(q: str) -> str | None:
    m = _SUBJECT_RE.search(q)
    return m.group(1) if m else None


def _extract_exam(q: str) -> str | None:
    from src.agent.education.query_parse import normalize_fullwidth_parentheses

    text = normalize_fullwidth_parentheses(q or "")
    m = _EXAM_FULL_RE.search(text)
    if m:
        return m.group(1).strip("在的于对")
    m = _EXAM_RE.search(text)
    if m:
        return m.group(1)
    from src.agent.education.query_parse import extract_exam_name_hint

    return extract_exam_name_hint(text)


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
        full_score = _resolve_full_score_from_rows(rows)
        cfg = self._config
        bundle = load_schema_from_config()
        if bundle is not None:
            cfg.pass_ratio = bundle.meta.pass_ratio
            cfg.excellent_ratio = bundle.meta.excellent_ratio
            if bundle.meta.score_segment_ratios:
                cfg.score_segment_ratios = list(bundle.meta.score_segment_ratios)
        stats = compute_score_stats(scores, cfg, full_score)

        charts: dict[str, str] = {}
        if spec.include_charts:
            charts["SCORE_DIST_CHART"] = build_chart_option(
                "score_distribution",
                {"segments": stats.get("segments", []), "pass_rate": stats.get("pass_rate")},
                title="分数段分布",
            )

        from src.agent.education.report_types import report_type_label

        class_name = spec.filters.get("class_name", "")
        subject = spec.filters.get("subject", "")
        exam_name = spec.filters.get("exam_name", "")
        school_name = spec.filters.get("school_name", "")
        scope_label = school_name or class_name or spec.filters.get("scope") or "全年级"
        # 组装通用字段；具体模板未用到的 key 由 Jinja2/regex 兜底为空。
        data: dict[str, Any] = {
            "REPORT_TITLE": self._title(spec),
            "REPORT_TYPE": report_type_label(spec.report_type),
            "REPORT_SUBTITLE": self._subtitle(spec),
            "REPORT_TIME": _now_str(),
            "CLASS_NAME": class_name,
            "EXAM_NAME": exam_name or "本次考试",
            "SUBJECT_NAME": subject or "全科",
            "GRADE_NAME": "",
            "SCOPE": scope_label,
            "TOTAL_COUNT": str(stats.get("count") or 0),
            "AVG_SCORE": _fmt(stats.get("avg")),
            "PASS_RATE": _fmt_pct(stats.get("pass_rate")),
            "EXCELLENT_RATE": _fmt_pct(stats.get("excellent_rate")),
            "GOOD_RATE": _fmt_pct(stats.get("good_rate")),
            "LOW_SCORE_RATE": _fmt_pct(stats.get("low_score_rate")),
            "MAX_SCORE": _fmt(stats.get("max")),
            "MIN_SCORE": _fmt(stats.get("min")),
            "STDEV": _fmt(stats.get("stdev")),
            "VARIANCE": _fmt(stats.get("variance")),
            "SCORE_DIST_CHART": charts.get("SCORE_DIST_CHART", ""),
            "SUBJECT_RADAR_CHART": "",
            "SUBJECT_BREAKDOWN": "",
            "RANK_INFO": "",
            "SEGMENT_TABLE": _segment_table(stats.get("segments", []), full_score=stats.get("full_score")),
            "ITEM_TABLE": "",
            "KNOWLEDGE_TABLE": "",
            "WEAK_KNOWLEDGE_LIST": "",
            "SUMMARY": "<p>由 ReportOrchestrator 自动生成。</p>",
            "RECOMMENDATIONS": "<p>结合 KPI 与分数段分布关注薄弱区间。</p>",
            "STUDENT_ARCHIVE_TABLE": "",
            "_stats": stats,
            "_charts": charts,
        }
        data.update(_dispersion_fields(stats.get("stdev"), full_score=stats.get("full_score"), variance=stats.get("variance")))
        if spec.report_type == ReportType.CLASS_OVERVIEW and not data.get("SUBJECT_RADAR_CHART"):
            try:
                from src.agent.resource.tool.business import _fill_class_overview_ability_portrait

                _fill_class_overview_ability_portrait(data, rows)
            except Exception:
                pass
        if spec.report_type == ReportType.DIAGNOSTIC_REPORT:
            score_rows = [
                {
                    "score": r.get("score"),
                    "exam_score": r.get("exam_score"),
                    "class": r.get("class") or class_name,
                    "class_name": r.get("class") or class_name,
                    "school_name": r.get("school_name") or school_name,
                    "district": r.get("district"),
                    "subject": r.get("subject") or subject,
                    "student_id": r.get("student_id"),
                }
                for r in rows
            ]
            diag = build_diagnostic_data(
                score_rows,
                config=cfg,
                scope_label=scope_label,
                exam_name=exam_name or "",
                subject_name=subject or "",
            )
            diag["REPORT_TIME"] = _now_str()
            data.update(diag)
            return data
        if spec.report_type == ReportType.SUBJECT_DIAGNOSIS and mapping.source == "config_edu":
            item_rows = compute_item_metrics(await self._fetch_item_rows(spec, mapping))
            knowledge_rows = enrich_knowledge_rows(await self._fetch_knowledge_rows(spec, mapping))
            data["ITEM_TABLE"] = build_item_table_html(item_rows)
            data["KNOWLEDGE_TABLE"] = build_knowledge_table_html(knowledge_rows)
            weak = [
                str(r.get("knowledge_name") or "")
                for r in knowledge_rows
                if r.get("level") == "需加强"
            ]
            data["WEAK_KNOWLEDGE_LIST"] = "、".join(weak[:8])
            data["SUMMARY"] = build_diagnosis_summary(
                school_name=school_name,
                exam_name=exam_name or "",
                subject_name=subject or "",
                stats=stats,
                item_rows=item_rows,
                knowledge_rows=knowledge_rows,
            )
            data["RECOMMENDATIONS"] = build_diagnosis_recommendations(
                knowledge_rows=knowledge_rows,
                item_rows=item_rows,
                stats=stats,
            )
            if knowledge_rows:
                charts["KNOWLEDGE_CHART"] = build_chart_option(
                    "knowledge_bar",
                    {
                        "categories": [str(r.get("knowledge_name") or "") for r in knowledge_rows[:12]],
                        "values": [float(r.get("score_rate") or 0) for r in knowledge_rows[:12]],
                    },
                    title="知识点得分率",
                )
                data["KNOWLEDGE_CHART"] = charts.get("KNOWLEDGE_CHART", "")
            tier = build_ability_tier_summary(knowledge_rows)
            tier_table = build_ability_tier_table_html(knowledge_rows)
            if tier_table:
                data["ABILITY_TIER_TABLE"] = tier_table
                levels = [s.get("ability_level") for s in tier.get("by_ability_level") or []]
                values = [float(s.get("avg_score_rate") or 0) for s in tier.get("by_ability_level") or []]
                from src.agent.education.knowledge_tier import ABILITY_LABELS
                data["ABILITY_TIER_CHART"] = build_chart_option(
                    "ability_radar",
                    {"levels": [ABILITY_LABELS.get(str(l), str(l)) for l in levels], "values": values},
                    title="能力层级得分率",
                )
            qtype_table = build_question_type_table_html(item_rows)
            if qtype_table:
                data["QUESTION_TYPE_TABLE"] = qtype_table
                from collections import defaultdict
                buckets: dict[str, list[float]] = defaultdict(list)
                for ir in item_rows:
                    if ir.get("question_type") and ir.get("score_rate") is not None:
                        buckets[str(ir["question_type"])].append(float(ir["score_rate"]))
                if buckets:
                    cats = sorted(buckets.keys())
                    vals = [round(sum(buckets[c]) / len(buckets[c]), 2) for c in cats]
                    data["QUESTION_TYPE_CHART"] = build_chart_option(
                        "question_type_bar",
                        {"categories": cats, "values": vals},
                        title="题型得分率",
                    )
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
        """按 mapping 生成只读 SELECT。"""
        if mapping.source == "config_edu" and mapping.mode == "normalized":
            return self._build_sql_config_edu(spec, mapping)
        class_name = spec.filters.get("class_name")
        subject = spec.filters.get("subject")
        if mapping.mode == "wide":
            table = mapping.table
            subject_col = next(iter(mapping.subject_columns.values()), None)
            if not subject_col:
                return ""
            where = f" WHERE {mapping.fields.get('class_name') or 'class'} = '{_sql_escape(class_name)}'" if class_name else ""
            return f"SELECT {subject_col} AS score FROM {table}{where} LIMIT 1000"
        score_tbl = mapping.tables.get("score", "score")
        score_field = mapping.fields.get("score", "score")
        subject_field = mapping.fields.get("subject", "subject")
        where_parts: list[str] = []
        if subject:
            where_parts.append(f"{subject_field} = '{_sql_escape(subject)}'")
        where = (" WHERE " + " AND ".join(where_parts)) if where_parts else ""
        return f"SELECT {score_field} AS score FROM {score_tbl}{where} LIMIT 1000"

    def _build_sql_config_edu(self, spec: ReportSpec, mapping: ScoreSchemaMapping) -> str:
        f = mapping.fields
        score_expr = f.get("score", "sc.score")
        full_expr = f.get("full_score", "sc.exam_score")
        subject_expr = f.get("subject", "sc.subject_name")
        class_expr = f.get("class_name", "sc.class")
        school_expr = f.get("school_name", "sch.name")
        exam_expr = f.get("exam_name", "e.exam_name")
        sql = (
            f"SELECT {score_expr} AS score, {full_expr} AS exam_score,\n"
            f"       {class_expr} AS class, {school_expr} AS school_name,\n"
            f"       sch.district AS district, {subject_expr} AS subject,\n"
            f"       sc.student_id AS student_id\n"
            "FROM tb_score sc\n"
            "JOIN tb_school sch ON sc.school_id = sch.id\n"
            "JOIN tb_exam e ON sc.exam_id = e.id"
        )
        where = _filters_to_where(spec.filters, school_expr, class_expr, subject_expr, exam_expr)
        if where:
            sql += f"\nWHERE {where}"
        return sql + "\nLIMIT 1000"

    def _build_item_diagnosis_sql(self, spec: ReportSpec, mapping: ScoreSchemaMapping) -> str:
        f = mapping.fields
        school_expr = f.get("school_name", "sch.name")
        class_expr = f.get("class_name", "sc.class")
        subject_expr = f.get("subject", "sc.subject_name")
        exam_expr = f.get("exam_name", "e.exam_name")
        kn_join = knowledge_names_subquery_join("pg")
        sql = (
            "SELECT sd.question_no,\n"
            "       COALESCE(kn.knowledge_name, '未关联知识点') AS knowledge_name,\n"
            "       eq.question_type AS question_type,\n"
            "       COALESCE(eq.question_score, sd.question_score) AS full_score,\n"
            "       ROUND(AVG(sd.score), 2) AS avg_score,\n"
            "       ROUND(AVG(sd.score)::numeric / NULLIF(COALESCE(eq.question_score, sd.question_score), 0) * 100, 2) AS score_rate\n"
            "FROM tb_score_detail sd\n"
            "LEFT JOIN tb_exam_question eq ON sd.question_id = eq.id\n"
            "    AND (eq.exam_id IS NULL OR eq.exam_id = sd.exam_id)\n"
            f"{kn_join}"
            "JOIN tb_score sc ON sd.exam_id = sc.exam_id AND sd.student_id = sc.student_id\n"
            "JOIN tb_school sch ON sc.school_id = sch.id\n"
            "JOIN tb_exam e ON sc.exam_id = e.id"
        )
        where = _filters_to_where(spec.filters, school_expr, class_expr, subject_expr, exam_expr)
        if where:
            sql += f"\nWHERE {where}"
        return (
            sql
            + "\nGROUP BY sd.question_no, COALESCE(kn.knowledge_name, '未关联知识点'), "
            "eq.question_type, COALESCE(eq.question_score, sd.question_score)\n"
            "ORDER BY sd.question_no\nLIMIT 1000"
        )

    def _build_knowledge_diagnosis_sql(self, spec: ReportSpec, mapping: ScoreSchemaMapping) -> str:
        f = mapping.fields
        school_expr = f.get("school_name", "sch.name")
        class_expr = f.get("class_name", "sc.class")
        subject_expr = f.get("subject", "sc.subject_name")
        exam_expr = f.get("exam_name", "e.exam_name")
        w_join = knowledge_weighted_join()
        full_score_expr = "COALESCE(eq.question_score, sd.question_score)"
        sql = (
            "SELECT COALESCE(k.knowledge_name, '未关联知识点') AS knowledge_name,\n"
            "       k.ability_level AS ability_level,\n"
            "       COUNT(DISTINCT sd.question_no) AS question_count,\n"
            f"       ROUND(SUM(sd.score * COALESCE(eqk.w_norm, 1))::numeric / "
            f"NULLIF(SUM({full_score_expr} * COALESCE(eqk.w_norm, 1)), 0) * 100, 2) AS score_rate\n"
            "FROM tb_score_detail sd\n"
            "LEFT JOIN tb_exam_question eq ON sd.question_id = eq.id\n"
            "    AND (eq.exam_id IS NULL OR eq.exam_id = sd.exam_id)\n"
            f"{w_join}"
            "JOIN tb_score sc ON sd.exam_id = sc.exam_id AND sd.student_id = sc.student_id\n"
            "JOIN tb_school sch ON sc.school_id = sch.id\n"
            "JOIN tb_exam e ON sc.exam_id = e.id"
        )
        where = _filters_to_where(spec.filters, school_expr, class_expr, subject_expr, exam_expr)
        if where:
            sql += f"\nWHERE {where}"
        return sql + "\nGROUP BY COALESCE(k.knowledge_name, '未关联知识点'), k.ability_level\nORDER BY score_rate ASC\nLIMIT 1000"

    async def _fetch_knowledge_rows(
        self,
        spec: ReportSpec,
        mapping: ScoreSchemaMapping,
    ) -> list[dict[str, Any]]:
        sql = self._build_knowledge_diagnosis_sql(spec, mapping)
        result = await self._execute_sql(sql)
        cols = result.get("columns") or []
        raw_rows = result.get("rows") or []
        return [dict(zip(cols, row)) for row in raw_rows]

    async def _fetch_item_rows(
        self,
        spec: ReportSpec,
        mapping: ScoreSchemaMapping,
    ) -> list[dict[str, Any]]:
        sql = self._build_item_diagnosis_sql(spec, mapping)
        result = await self._execute_sql(sql)
        cols = result.get("columns") or []
        raw_rows = result.get("rows") or []
        return [dict(zip(cols, row)) for row in raw_rows]

    @staticmethod
    def _title(spec: ReportSpec) -> str:
        from src.agent.education.report_types import report_type_label

        label = report_type_label(spec.report_type)
        prefix = spec.filters.get("class_name") or spec.filters.get("subject") or ""
        return f"{prefix}{label}".strip()

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


def _fmt_pct(v: Any) -> str:
    if v is None:
        return "-"
    return f"{_fmt(v)}%"


def _dispersion_fields(
    stdev: Any,
    *,
    full_score: float | None = None,
    variance: Any = None,
) -> dict[str, str]:
    from src.agent.education.stats import describe_score_dispersion

    try:
        stdev_f = float(stdev) if stdev is not None else None
    except (TypeError, ValueError):
        stdev_f = None
    try:
        var_f = float(variance) if variance is not None else None
    except (TypeError, ValueError):
        var_f = None
    info = describe_score_dispersion(stdev_f, full_score=full_score, variance=var_f)
    out = {
        "STDEV_LEVEL": str(info["level"]),
        "STDEV_LEVEL_CLASS": str(info["level_class"]),
        "STDEV_HINT": str(info["stdev_hint"]),
        "VARIANCE_HINT": str(info["variance_hint"]),
        "DISPERSION_TIP": str(info["tip"]),
    }
    if info["variance"] != "-":
        out["VARIANCE"] = _fmt(info["variance"])
    return out


def _sql_escape(val: str) -> str:
    return (val or "").replace("'", "''")


def _filters_to_where(
    filters: dict[str, str],
    school_expr: str,
    class_expr: str,
    subject_expr: str,
    exam_expr: str,
) -> str:
    parts: list[str] = []
    if filters.get("school_name"):
        parts.append(f"{school_expr} = '{_sql_escape(filters['school_name'])}'")
    if filters.get("class_name"):
        parts.append(f"{class_expr} = '{_sql_escape(filters['class_name'])}'")
    if filters.get("subject"):
        parts.append(f"{subject_expr} = '{_sql_escape(filters['subject'])}'")
    if filters.get("exam_name"):
        parts.append(f"{exam_expr} LIKE '%{_sql_escape(filters['exam_name'])}%'")
    if filters.get("district"):
        parts.append(f"sch.district = '{_sql_escape(filters['district'])}'")
    return " AND ".join(parts)


def _resolve_full_score_from_rows(rows: list[dict[str, Any]]) -> float | None:
    seen: set[float] = set()
    for row in rows:
        raw = row.get("exam_score")
        if raw is None or raw == "":
            continue
        try:
            seen.add(float(raw))
        except (TypeError, ValueError):
            continue
    if not seen:
        return None
    return max(seen) if len(seen) > 1 else seen.pop()


def _now_str() -> str:
    from datetime import datetime

    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _segment_table(segments: list[dict[str, Any]], *, full_score: float | None = None) -> str:
    from src.agent.education.subject_diagnosis import build_segment_table_html

    return build_segment_table_html(segments, full_score=full_score)


__all__ = ["ReportIntentResolver", "ReportOrchestrator", "ReportResult"]
