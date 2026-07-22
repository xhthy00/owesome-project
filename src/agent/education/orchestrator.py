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
        *,
        datasource_id: int | None = None,
        workspace_oid: int | None = None,
        user_id: int | None = None,
    ) -> None:
        self._execute_sql = execute_sql
        self._resolve_schema = resolve_schema
        if config is not None:
            self._config = config
        else:
            try:
                from src.agent.education.config_store import get_config

                self._config = get_config()
            except Exception:
                self._config = load_config()
        self._datasource_id = datasource_id
        self._workspace_oid = workspace_oid
        self._user_id = user_id
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
        return await self.run_spec(spec)

    async def run_spec(self, spec: ReportSpec) -> ReportResult:
        """按已构造的 ``ReportSpec`` 生成报告（跳过自然语言意图解析）。"""
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
        from dataclasses import replace

        cfg = replace(self._config)
        # 分数段边界可来自 schema；及格/优秀比例以 config_store（异常规则库表）为准，
        # 禁止用 schema 默认 0.6/0.85 覆盖用户配置。
        bundle = load_schema_from_config()
        if bundle is not None and bundle.meta.score_segment_ratios:
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
        exam_name_raw = spec.filters.get("exam_name", "")
        exam_name = _format_exam_label(exam_name_raw) or exam_name_raw
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
        if spec.report_type == ReportType.CLASS_OVERVIEW:
            from src.agent.education.subject_diagnosis import (
                build_class_overview_recommendations,
                build_class_overview_summary,
            )

            tip = str(data.get("DISPERSION_TIP") or "")
            data["SUMMARY"] = build_class_overview_summary(
                class_name=class_name or "",
                subject_name=subject or "",
                exam_name=exam_name or "",
                stats=stats,
                stdev_level=str(data.get("STDEV_LEVEL") or ""),
            )
            data["RECOMMENDATIONS"] = build_class_overview_recommendations(
                stats=stats,
                dispersion_tip=tip,
            )
        if spec.report_type == ReportType.DIAGNOSTIC_REPORT:
            self._fill_diagnostic(data, rows, cfg, spec, scope_label, exam_name, subject, school_name, class_name)
            return data
        if spec.report_type == ReportType.GRADE_COMPARISON:
            self._fill_grade_comparison(data, rows, stats, cfg, spec)
        if spec.report_type == ReportType.STUDENT_PROFILE:
            await self._fill_student_profile(data, rows, spec)
        if spec.report_type == ReportType.TREND_TRACKING:
            self._fill_trend_tracking(data, rows, spec, cfg)
        if spec.report_type == ReportType.TIER_ALERT:
            self._fill_tier_alert(data, rows, spec, cfg)
        if spec.report_type == ReportType.GROUP_FEATURE:
            self._fill_group_feature(data, rows, spec, cfg)
        if spec.report_type == ReportType.COMPREHENSIVE:
            self._fill_comprehensive(data, rows, spec, cfg)
        if spec.report_type == ReportType.CLASS_OVERVIEW and mapping.source == "config_edu":
            await self._fill_class_overview_rank(data, rows, spec, mapping, cfg)
        if spec.report_type == ReportType.SUBJECT_DIAGNOSIS and mapping.source == "config_edu":
            await self._fill_subject_diagnosis(
                data,
                rows,
                stats,
                charts,
                cfg,
                spec,
                mapping,
                school_name=school_name,
                class_name=class_name,
                exam_name=exam_name or "",
                subject=subject or "",
                full_score=full_score,
            )
        return data

    async def _fill_subject_diagnosis(
        self,
        data: dict[str, Any],
        rows: list[dict[str, Any]],
        stats: dict[str, Any],
        charts: dict[str, str],
        cfg: EducationConfig,
        spec: ReportSpec,
        mapping: ScoreSchemaMapping,
        *,
        school_name: str,
        class_name: str,
        exam_name: str,
        subject: str,
        full_score: float | None,
    ) -> None:
        """科目诊断：对齐聊天路径的小题/知识点查询 + 逐人小题建议。"""
        from collections import defaultdict

        from src.agent.education.knowledge_tier import ABILITY_LABELS

        item_rows: list[dict[str, Any]] = []
        knowledge_rows: list[dict[str, Any]] = []
        score_rows_for_archive: list[dict[str, Any]] = [
            {
                "student_id": r.get("student_id") or r.get("student_name"),
                "subject": r.get("subject") or subject,
                "score": r.get("score"),
                "exam_name": r.get("exam_name") or exam_name,
                "exam_score": r.get("exam_score"),
                "exam_id": r.get("exam_id"),
            }
            for r in rows
        ]
        weak_threshold = float(getattr(cfg, "weak_knowledge_threshold", 60.0) or 60.0)
        used_chat_fetch = False

        if self._datasource_id is not None:
            try:
                from src.agent.education.tools import _fetch_subject_diagnosis_rows
                from src.agent.resource.tool.business import _load_datasource

                db_type, _, _ = _load_datasource(self._datasource_id, self._workspace_oid)
                bundle = await asyncio.to_thread(
                    _fetch_subject_diagnosis_rows,
                    datasource_id=int(self._datasource_id),
                    workspace_oid=self._workspace_oid,
                    user_id=self._user_id,
                    school_name=school_name,
                    subject_name=subject,
                    exam_name=exam_name,
                    class_name=class_name,
                    db_type=db_type or "pg",
                )
                item_rows = list(bundle.get("item_rows") or [])
                knowledge_rows = list(bundle.get("knowledge_rows") or [])
                if bundle.get("score_rows"):
                    score_rows_for_archive = list(bundle["score_rows"])
                used_chat_fetch = True
                fetch_scores = [
                    float(r["score"])
                    for r in score_rows_for_archive
                    if isinstance(r, dict) and r.get("score") is not None
                ]
                if fetch_scores:
                    fs = full_score
                    for r in score_rows_for_archive:
                        if isinstance(r, dict) and r.get("exam_score") is not None:
                            try:
                                fs = float(r["exam_score"])
                                break
                            except (TypeError, ValueError):
                                pass
                    stats.update(compute_score_stats(fetch_scores, cfg, fs))
                    data["TOTAL_COUNT"] = str(stats.get("count") or 0)
                    data["AVG_SCORE"] = _fmt(stats.get("avg"))
                    data["PASS_RATE"] = _fmt_pct(stats.get("pass_rate"))
                    data["EXCELLENT_RATE"] = _fmt_pct(stats.get("excellent_rate"))
                    data["GOOD_RATE"] = _fmt_pct(stats.get("good_rate"))
                    data["LOW_SCORE_RATE"] = _fmt_pct(stats.get("low_score_rate"))
                    data["MAX_SCORE"] = _fmt(stats.get("max"))
                    data["MIN_SCORE"] = _fmt(stats.get("min"))
                    data["STDEV"] = _fmt(stats.get("stdev"))
                    data["SEGMENT_TABLE"] = _segment_table(
                        stats.get("segments", []), full_score=stats.get("full_score")
                    )
                    data.update(
                        _dispersion_fields(
                            stats.get("stdev"),
                            full_score=stats.get("full_score"),
                            variance=stats.get("variance"),
                        )
                    )
                    if spec.include_charts:
                        charts["SCORE_DIST_CHART"] = build_chart_option(
                            "score_distribution",
                            {
                                "segments": stats.get("segments", []),
                                "pass_rate": stats.get("pass_rate"),
                            },
                            title="分数段分布",
                        )
                        data["SCORE_DIST_CHART"] = charts.get("SCORE_DIST_CHART", "")
            except Exception as e:  # noqa: BLE001
                logger.warning("科目诊断 fetch 失败，回退编排 SQL: %s", e)
                used_chat_fetch = False
                item_rows = []
                knowledge_rows = []

        if not item_rows and not knowledge_rows:
            item_rows = await self._fetch_item_rows(spec, mapping)
            knowledge_rows = await self._fetch_knowledge_rows(spec, mapping)

        item_rows = compute_item_metrics(item_rows)
        knowledge_rows = enrich_knowledge_rows(knowledge_rows)
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
            exam_name=exam_name,
            subject_name=subject,
            stats=stats,
            item_rows=item_rows,
            knowledge_rows=knowledge_rows,
        )
        data["RECOMMENDATIONS"] = build_diagnosis_recommendations(
            knowledge_rows=knowledge_rows,
            item_rows=item_rows,
            stats=stats,
        )
        if knowledge_rows and spec.include_charts:
            charts["KNOWLEDGE_CHART"] = build_chart_option(
                "knowledge_bar",
                {
                    "categories": [str(r.get("knowledge_name") or "") for r in knowledge_rows[:12]],
                    "values": [float(r.get("score_rate") or 0) for r in knowledge_rows[:12]],
                },
                title="知识点得分率",
            )
            data["KNOWLEDGE_CHART"] = charts.get("KNOWLEDGE_CHART", "")
        tier = build_ability_tier_summary(knowledge_rows, weak_threshold=weak_threshold)
        tier_table = build_ability_tier_table_html(knowledge_rows)
        if tier_table:
            data["ABILITY_TIER_TABLE"] = tier_table
            levels = [s.get("ability_level") for s in tier.get("by_ability_level") or []]
            values = [float(s.get("avg_score_rate") or 0) for s in tier.get("by_ability_level") or []]
            data["ABILITY_TIER_CHART"] = build_chart_option(
                "ability_radar",
                {"levels": [ABILITY_LABELS.get(str(l), str(l)) for l in levels], "values": values},
                title="能力层级得分率",
            )
        qtype_table = build_question_type_table_html(item_rows)
        if qtype_table:
            data["QUESTION_TYPE_TABLE"] = qtype_table
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

        # 学生档案：与聊天一致，结合逐人小题明细给建议
        try:
            if self._datasource_id is not None:
                from src.agent.education.tools import _subject_diagnosis_student_archive

                archive = await asyncio.to_thread(
                    _subject_diagnosis_student_archive,
                    score_rows=score_rows_for_archive,
                    item_rows=item_rows,
                    exam_name=exam_name,
                    subject_name=subject,
                    school_name=school_name,
                    class_name=class_name,
                    full_score=stats.get("full_score") or full_score,
                    weak_threshold=weak_threshold,
                    datasource_id=int(self._datasource_id),
                    workspace_oid=self._workspace_oid,
                )
            else:
                from src.agent.education.comprehensive import build_student_archive_from_score_rows

                archive = build_student_archive_from_score_rows(
                    score_rows_for_archive,
                    exam_name=exam_name,
                    full_score=full_score,
                    single_subject=True,
                )
            if archive:
                data["STUDENT_ARCHIVE_TABLE"] = archive
        except Exception as e:  # noqa: BLE001
            logger.warning("科目诊断学生档案组装失败: %s", e)

        if used_chat_fetch and not item_rows and not knowledge_rows:
            data.setdefault(
                "SUMMARY",
                "<p>已查到总分 KPI，但小题/知识点明细为空；请确认已导入 tb_score_detail。</p>",
            )

    def _fill_grade_comparison(
        self,
        data: dict[str, Any],
        rows: list[dict[str, Any]],
        stats: dict[str, Any],
        cfg: EducationConfig,
        spec: ReportSpec,
    ) -> None:
        """填充班级横向对比模板字段：均分柱图 + 排名表 + 摘要。"""
        from src.agent.education.aggregation import aggregate_by
        from src.agent.education.dimension_parse import parse_grade_from_class
        from src.agent.education.school_intervention import build_class_compare_table_html

        school_name = spec.filters.get("school_name", "")
        subject = spec.filters.get("subject", "")
        exam_name = spec.filters.get("exam_name", "")

        class_agg = aggregate_by("class", rows, cfg)
        # 去掉「未知班级」噪声
        class_agg = [
            g for g in class_agg
            if str(g.get("dimension_value") or "").strip() not in ("", "未知班级")
        ]
        ranked = compute_rankings(
            [
                {
                    "name": str(g.get("dimension_value") or ""),
                    "value": float(g.get("avg") or 0),
                    "count": g.get("count"),
                    "avg": g.get("avg"),
                    "pass_rate": g.get("pass_rate"),
                    "excellent_rate": g.get("excellent_rate"),
                    "stdev": g.get("stdev"),
                    "dimension_value": g.get("dimension_value"),
                }
                for g in class_agg
            ],
            value_key="value",
            name_key="name",
        )

        # 年级名：从班级名推断，否则用学校名
        grade_name = ""
        for g in class_agg:
            grade_name = parse_grade_from_class(str(g.get("dimension_value") or "")) or ""
            if grade_name:
                break
        data["GRADE_NAME"] = grade_name or school_name or "年级"

        if not ranked:
            data["CLASS_COMPARE_CHART"] = ""
            data["CLASS_RANKING_TABLE"] = "<p>未查到可对比的班级成绩数据。</p>"
            data["DISPERSION_INFO"] = "<p>无班级数据，无法分析均衡度。</p>"
            data["SUMMARY"] = (
                f"<p>{school_name or '本校'}{subject or ''}{exam_name or '本次考试'}"
                "暂无各班成绩，请检查筛选条件或数据权限。</p>"
            )
            data["RECOMMENDATIONS"] = "<p>确认学校、考试、科目后重新生成；或换有数据的考试场次。</p>"
            return

        # 柱图：按均分升序（横向柱图 y 轴自下而上）
        chart_items = sorted(ranked, key=lambda x: float(x.get("value") or 0))
        data["CLASS_COMPARE_CHART"] = build_chart_option(
            "class_compare_bar",
            {
                "classes": [str(x.get("name") or "") for x in chart_items],
                "values": [round(float(x.get("value") or 0), 2) for x in chart_items],
            },
            title=f"{subject or '全科'}各班均分对比",
        )

        # 排名明细表（含校级基准）
        table_agg = [
            {
                "dimension_value": r.get("name"),
                "count": r.get("count"),
                "avg": r.get("avg"),
                "pass_rate": r.get("pass_rate"),
                "excellent_rate": r.get("excellent_rate"),
            }
            for r in ranked
        ]
        data["CLASS_RANKING_TABLE"] = build_class_compare_table_html(
            table_agg,
            school_stats=stats,
        ) or self._grade_ranking_table_html(ranked)

        top = ranked[0]
        bottom = ranked[-1]
        gap = float(top.get("value") or 0) - float(bottom.get("value") or 0)
        data["DISPERSION_INFO"] = (
            f"<p>共 <strong>{len(ranked)}</strong> 个班级参与对比；"
            f"最高均分 <strong>{_fmt(top.get('value'))}</strong>"
            f"（{top.get('name')}），"
            f"最低均分 <strong>{_fmt(bottom.get('value'))}</strong>"
            f"（{bottom.get('name')}），"
            f"班际分差 <strong>{_fmt(gap)}</strong> 分。</p>"
            f"<p>{data.get('DISPERSION_TIP') or ''}</p>"
        )
        data["SUMMARY"] = (
            f"<p>{school_name or '本校'}{exam_name or '本次考试'}{subject or '全科'}："
            f"<strong>{top.get('name')}</strong> 均分领先"
            f"（{_fmt(top.get('value'))}），"
            f"<strong>{bottom.get('name')}</strong> 相对偏低"
            f"（{_fmt(bottom.get('value'))}）；班际分差 {_fmt(gap)} 分。</p>"
        )
        data["RECOMMENDATIONS"] = (
            "<ul>"
            f"<li>关注均分末位班级（{bottom.get('name')}）的低分段与及格率提升。</li>"
            f"<li>组织领先班级（{top.get('name')}）经验分享，缩小班际差距。</li>"
            "<li>结合各班及格率/优秀率结构，安排分层辅导与临界生盯梢。</li>"
            "</ul>"
        )

    @staticmethod
    def _grade_ranking_table_html(ranked: list[dict[str, Any]]) -> str:
        rows = []
        for r in ranked:
            rows.append(
                "<tr>"
                f"<td>{r.get('rank') or '-'}</td>"
                f"<td>{r.get('name') or '-'}</td>"
                f"<td class='num'>{r.get('count') if r.get('count') is not None else '-'}</td>"
                f"<td class='num'>{_fmt(r.get('value'))}</td>"
                f"<td class='num'>{_fmt_pct(r.get('pass_rate'))}</td>"
                f"<td class='num'>{_fmt_pct(r.get('excellent_rate'))}</td>"
                "</tr>"
            )
        inner = (
            "<table class='edu-table'><thead><tr>"
            "<th>排名</th><th>班级</th><th class='num'>人数</th>"
            "<th class='num'>均分</th><th class='num'>及格率</th><th class='num'>优秀率</th>"
            "</tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table>"
        )
        return f'<div class="edu-table-wrap">{inner}</div>'

    def _fill_diagnostic(
        self,
        data: dict[str, Any],
        rows: list[dict[str, Any]],
        cfg: EducationConfig,
        spec: ReportSpec,
        scope_label: str,
        exam_name: str,
        subject: str,
        school_name: str,
        class_name: str,
    ) -> None:
        from src.agent.education.aggregation import prepare_score_rows_for_kpi
        from src.agent.education.diagnostic_report import (
            _exam_avg_trend_from_rows,
            _student_progress_from_rows,
            build_diagnostic_data,
        )

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
                "student_name": r.get("student_name"),
                # 勿用多选考试展示名回填，否则会污染跨场分组
                "exam_name": str(r.get("exam_name") or "").strip(),
            }
            for r in rows
        ]
        # S1/S2 用单场 KPI 口径，避免人次膨胀；S3 动态性必须用全量多场
        kpi_rows = prepare_score_rows_for_kpi(score_rows)
        exam_trend = _exam_avg_trend_from_rows(score_rows)
        progress = _student_progress_from_rows(score_rows)
        diag = build_diagnostic_data(
            kpi_rows or score_rows,
            trend_records=exam_trend or None,
            progress_records=progress or None,
            config=cfg,
            scope_label=scope_label,
            exam_name=exam_name or "",
            subject_name=subject or "",
        )
        diag["REPORT_TIME"] = _now_str()
        data.update(diag)

    async def _fill_student_profile(
        self,
        data: dict[str, Any],
        rows: list[dict[str, Any]],
        spec: ReportSpec,
    ) -> None:
        """学生画像：全科多场 + 小题知识点洞察（对齐聊天 build_student_exam_report_data_tool）。"""
        from src.agent.education.comprehensive import aggregate_student_item_insights
        from src.agent.education.query_parse import student_matches
        from src.agent.education.student_exam import build_student_exam_data
        from src.agent.education.tools import (
            _aggregate_long_table_records,
            _fetch_class_score_long_table,
            _fetch_student_item_rows_direct,
            _guess_long_table_fields,
        )

        student = str(spec.filters.get("student_name") or "").strip()
        class_name = str(spec.filters.get("class_name") or "").strip()
        school_name = str(spec.filters.get("school_name") or "").strip()
        subject_hint = str(spec.filters.get("subject") or "").strip()

        records, exam_order = _score_dicts_to_records(rows)
        # 有数据源时拉全班全科历次（不限 subject），支撑雷达图
        if self._datasource_id is not None:
            try:
                fetched = await asyncio.to_thread(
                    _fetch_class_score_long_table,
                    datasource_id=int(self._datasource_id),
                    class_name=class_name,
                    student_name=student,
                    school_name=school_name,
                    # 表单选了科目 → 单科；未选 → 全科多科雷达
                    subject_name=subject_hint,
                    workspace_oid=self._workspace_oid,
                )
                if fetched and fetched.get("rows") and fetched.get("columns"):
                    cols = [str(c) for c in fetched["columns"]]
                    ef, sf, subf, scf, tf = _guess_long_table_fields(
                        cols,
                        exam_field="exam_name",
                        student_field="student_id",
                        subject_field="subject_name",
                        score_field="score",
                        total_field="total",
                    )
                    records, exam_order = _aggregate_long_table_records(
                        list(fetched["rows"]),
                        cols,
                        exam_field=ef,
                        student_field=sf,
                        subject_field=subf,
                        score_field=scf,
                        total_field=tf,
                    )
                    if not class_name and fetched.get("class_name"):
                        class_name = str(fetched["class_name"])
            except Exception as e:  # noqa: BLE001
                logger.warning("学生画像拉全班成绩失败，回退编排 SQL: %s", e)

        if not records:
            data["SUMMARY"] = "<p>未检索到该生可用的成绩明细，请确认班级与学号/姓名。</p>"
            return

        student_item_insights: dict[str, dict[str, Any]] = {}
        if self._datasource_id is not None and student:
            try:
                detail_rows = await asyncio.to_thread(
                    _fetch_student_item_rows_direct,
                    datasource_id=int(self._datasource_id),
                    student_id=student,
                    subject_name=subject_hint,
                    workspace_oid=self._workspace_oid,
                )
                if not detail_rows and subject_hint:
                    detail_rows = await asyncio.to_thread(
                        _fetch_student_item_rows_direct,
                        datasource_id=int(self._datasource_id),
                        student_id=student,
                        subject_name="",
                        workspace_oid=self._workspace_oid,
                    )
                if detail_rows:
                    # 仅保留目标学生
                    detail_rows = [
                        r
                        for r in detail_rows
                        if student_matches(str(r.get("student_id") or ""), student)
                        or not r.get("student_id")
                    ] or detail_rows
                    student_item_insights = aggregate_student_item_insights(detail_rows)
            except Exception as e:  # noqa: BLE001
                logger.warning("学生画像小题明细拉取失败: %s", e)

        filled = build_student_exam_data(
            records,
            student_name=student,
            exam_order=exam_order,
            class_name=class_name,
            student_item_insights=student_item_insights or None,
        )
        if subject_hint:
            filled.setdefault("SUBJECT_NAME", subject_hint)
        # 无薄弱清单时给占位，避免模板空白刺眼
        if not str(filled.get("WEAK_KNOWLEDGE_LIST") or "").strip():
            filled["WEAK_KNOWLEDGE_LIST"] = "暂无（请确认已导入小题明细与知识点关联）"
        data.update(filled)

    def _fill_trend_tracking(
        self,
        data: dict[str, Any],
        rows: list[dict[str, Any]],
        spec: ReportSpec,
        cfg: EducationConfig,
    ) -> None:
        from src.agent.education.trend_tracking import build_trend_tracking_data

        records, exam_order = _score_dicts_to_records(rows)
        if not records:
            data["SUMMARY"] = "<p>未检索到可用于趋势分析的成绩明细。</p>"
            return
        filled = build_trend_tracking_data(
            records,
            exam_order,
            class_name=str(spec.filters.get("class_name") or ""),
            school_name=str(spec.filters.get("school_name") or ""),
            subject_name=str(spec.filters.get("subject") or ""),
            target_name=str(
                spec.filters.get("class_name")
                or spec.filters.get("school_name")
                or "跟踪对象"
            ),
            config=cfg,
        )
        data.update(filled)

    def _fill_tier_alert(
        self,
        data: dict[str, Any],
        rows: list[dict[str, Any]],
        spec: ReportSpec,
        cfg: EducationConfig,
    ) -> None:
        from dataclasses import replace

        from src.agent.education.aggregation import prepare_score_rows_for_kpi
        from src.agent.education.tools import (
            _build_tier_alert_template_data,
            _score_rows_to_at_risk_students,
        )

        subject = str(spec.filters.get("subject") or "")
        # 保留多科目行（不去重掉科目），便于偏科识别；仅收敛多场
        from src.agent.education.aggregation import narrow_score_rows_to_primary_exam

        narrowed, _ = narrow_score_rows_to_primary_exam(
            [
                {
                    "score": r.get("score"),
                    "exam_score": r.get("exam_score"),
                    "subject": r.get("subject") or subject,
                    "name": r.get("student_name") or r.get("student_id"),
                    "student_id": r.get("student_id"),
                    "student_name": r.get("student_name"),
                    "exam_name": r.get("exam_name"),
                    "exam_id": r.get("exam_id"),
                    "school_id": r.get("school_id"),
                    "class": r.get("class") or r.get("class_name"),
                    "class_name": r.get("class") or r.get("class_name"),
                }
                for r in rows
            ]
        )
        # 若无多科，退回 KPI 口径（学生去重）
        kpi_rows = narrowed if narrowed else prepare_score_rows_for_kpi(
            [
                {
                    "score": r.get("score"),
                    "exam_score": r.get("exam_score"),
                    "subject": r.get("subject") or subject,
                    "name": r.get("student_name") or r.get("student_id"),
                    "student_id": r.get("student_id"),
                    "exam_name": r.get("exam_name"),
                    "exam_id": r.get("exam_id"),
                    "school_id": r.get("school_id"),
                    "class": r.get("class") or r.get("class_name"),
                    "class_name": r.get("class") or r.get("class_name"),
                }
                for r in rows
            ]
        )
        students = _score_rows_to_at_risk_students(kpi_rows, default_subject=subject)

        fs = None
        for r in rows:
            if r.get("exam_score") is not None:
                try:
                    fs = float(r["exam_score"])
                    break
                except (TypeError, ValueError):
                    pass
        local_cfg = replace(cfg)
        if fs is not None and fs > 0:
            local_cfg.pass_threshold = fs * float(cfg.pass_ratio)
        at_risk = identify_at_risk_students(students, local_cfg)
        filled = _build_tier_alert_template_data(
            at_risk,
            class_name=str(spec.filters.get("class_name") or ""),
            school_name=str(spec.filters.get("school_name") or ""),
            subject_name=subject,
            exam_name=str(spec.filters.get("exam_name") or ""),
            pass_line=local_cfg.pass_threshold,
        )
        data.update(filled)

        # 编排器路径同样落异常提醒
        try:
            from src.agent.education.alert_service import (
                SOURCE_TIER_ALERT,
                upsert_from_at_risk_payload,
            )
            from src.agent.education.tools import _looks_like_school_id
            from src.common.core.database import get_db_session

            school_id = ""
            exam_id = ""
            for r in rows:
                school_id = school_id or str(r.get("school_id") or "").strip()
                exam_id = exam_id or str(r.get("exam_id") or "").strip()
            sn = str(spec.filters.get("school_name") or "").strip()
            if not school_id and sn and _looks_like_school_id(sn):
                school_id = sn
            ds_id = getattr(self, "_datasource_id", None)
            ws_oid = getattr(self, "_workspace_oid", None) or 1
            if school_id and ds_id is not None:
                with get_db_session() as session:
                    upsert_from_at_risk_payload(
                        session,
                        at_risk,
                        workspace_oid=int(ws_oid),
                        datasource_id=int(ds_id),
                        school_id=school_id,
                        exam_id=exam_id or str(spec.filters.get("exam_name") or "unknown"),
                        exam_name=str(spec.filters.get("exam_name") or ""),
                        class_name=str(spec.filters.get("class_name") or ""),
                        source=SOURCE_TIER_ALERT,
                    )
        except Exception:  # noqa: BLE001
            import logging

            logging.getLogger(__name__).exception(
                "orchestrator tier_alert anomaly alert upsert failed"
            )

    def _fill_group_feature(
        self,
        data: dict[str, Any],
        rows: list[dict[str, Any]],
        spec: ReportSpec,
        cfg: EducationConfig,
    ) -> None:
        from src.agent.education.group_feature import build_group_feature_data

        filled = build_group_feature_data(
            [
                {
                    "score": r.get("score"),
                    "exam_score": r.get("exam_score"),
                    "class": r.get("class"),
                    "class_name": r.get("class"),
                    "school_name": r.get("school_name"),
                    "district": r.get("district"),
                    "subject": r.get("subject"),
                    "grade": r.get("grade"),
                }
                for r in rows
            ],
            dimension=str(spec.filters.get("dimension") or "class"),
            config=cfg,
            school_name=str(spec.filters.get("school_name") or ""),
            subject_name=str(spec.filters.get("subject") or ""),
            exam_name=str(spec.filters.get("exam_name") or ""),
        )
        data.update(filled)

    def _fill_comprehensive(
        self,
        data: dict[str, Any],
        rows: list[dict[str, Any]],
        spec: ReportSpec,
        cfg: EducationConfig,
    ) -> None:
        from src.agent.education.comprehensive import build_comprehensive_data

        records, exam_order = _score_dicts_to_records(rows)
        if not records:
            data["SUMMARY"] = "<p>未检索到可用于综合分析的多场成绩明细。</p>"
            return
        filled = build_comprehensive_data(
            records,
            exam_order,
            class_name=str(spec.filters.get("class_name") or ""),
            config=cfg,
        )
        data.update(filled)

    async def _fill_class_overview_rank(
        self,
        data: dict[str, Any],
        class_rows: list[dict[str, Any]],
        spec: ReportSpec,
        mapping: ScoreSchemaMapping,
        cfg: EducationConfig,
    ) -> None:
        """用同校同年级各班 KPI 填充 RANK_INFO。"""
        from src.agent.education.aggregation import aggregate_by, prepare_score_rows_for_kpi
        from src.agent.education.dimension_parse import parse_grade_from_class
        from src.agent.resource.tool.business import _format_rank_info_html

        class_name = str(spec.filters.get("class_name") or "").strip()
        school_name = str(spec.filters.get("school_name") or "").strip()
        if not class_name and not school_name:
            return

        # 去掉本班过滤，拉同校对照
        peer_filters = dict(spec.filters)
        peer_filters.pop("class_name", None)
        peer_spec = ReportSpec(
            report_type=spec.report_type,
            audience=spec.audience,
            filters=peer_filters,
            include_charts=False,
        )
        try:
            peer_raw = await self._fetch_score_rows(peer_spec, mapping)
        except Exception:
            peer_raw = []
        peer_rows = prepare_score_rows_for_kpi(
            [
                {
                    "score": r.get("score"),
                    "exam_score": r.get("exam_score"),
                    "class": r.get("class"),
                    "class_name": r.get("class"),
                    "school_name": r.get("school_name"),
                    "subject": r.get("subject"),
                    "student_id": r.get("student_id"),
                    "exam_name": r.get("exam_name"),
                }
                for r in (peer_raw or class_rows)
            ]
        )
        class_agg = aggregate_by("class", peer_rows, cfg)
        class_agg = [
            g
            for g in class_agg
            if str(g.get("dimension_value") or "").strip() not in ("", "未知班级")
        ]
        if len(class_agg) < 2:
            return

        # 尽量限定同年级
        my_grade = parse_grade_from_class(class_name) if class_name else ""
        if my_grade:
            same = [
                g
                for g in class_agg
                if parse_grade_from_class(str(g.get("dimension_value") or "")) == my_grade
            ]
            if len(same) >= 2:
                class_agg = same

        target = class_name
        if not target and class_rows:
            target = str(class_rows[0].get("class") or "").strip()
        if not target:
            return

        def _rank_of(metric: str, *, higher_better: bool = True) -> dict[str, Any] | None:
            ranked = sorted(
                class_agg,
                key=lambda g: float(g.get(metric) or 0),
                reverse=higher_better,
            )
            total = len(ranked)
            cohort = sum(float(g.get(metric) or 0) for g in ranked) / total if total else 0
            for i, g in enumerate(ranked):
                if str(g.get("dimension_value") or "") == target:
                    val = g.get(metric)
                    return {
                        "指标": {"avg": "均分", "pass_rate": "及格率", "excellent_rate": "优秀率"}.get(
                            metric, metric
                        ),
                        "value": (
                            f"{float(val):.2f}%"
                            if metric.endswith("rate") and val is not None
                            else (round(float(val), 2) if val is not None else "-")
                        ),
                        "rank": i + 1,
                        "total": total,
                        "cohort_avg": (
                            f"{cohort:.2f}%"
                            if metric.endswith("rate")
                            else round(cohort, 2)
                        ),
                    }
            return None

        items = [x for x in (_rank_of("avg"), _rank_of("pass_rate"), _rank_of("excellent_rate")) if x]
        if not items:
            return
        grade_label = my_grade or "年级"
        scope = f"{school_name or ''}{grade_label} (共 {items[0]['total']} 个班)".strip()
        top = items[0]
        summary = (
            f"{target} 在均分上位列{grade_label}第 {top['rank']} / 共 {top['total']} 班"
        )
        data["RANK_INFO"] = _format_rank_info_html(
            {"scope": scope, "items": items, "summary": summary}
        )

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
        # tb_student 无姓名列（id 即学号），学生标识统一用 student_id
        sql = (
            f"SELECT {score_expr} AS score, {full_expr} AS exam_score,\n"
            f"       {class_expr} AS class, {school_expr} AS school_name,\n"
            f"       sch.district AS district, {subject_expr} AS subject,\n"
            f"       sc.student_id AS student_id,\n"
            f"       sc.student_id AS student_name,\n"
            f"       {exam_expr} AS exam_name\n"
            "FROM tb_score sc\n"
            "JOIN tb_school sch ON sc.school_id = sch.id\n"
            "JOIN tb_exam e ON sc.exam_id = e.id"
        )
        # 多场考试类报告：未指定考试时不按单场过滤，便于趋势/综合/学生画像/诊断动态性；
        # 若表单已选考试（含多选 ``a;;b``），则按所选过滤。
        filters = dict(spec.filters)
        if spec.report_type in (
            ReportType.STUDENT_PROFILE,
            ReportType.TREND_TRACKING,
            ReportType.COMPREHENSIVE,
            ReportType.DIAGNOSTIC_REPORT,
        ):
            if not str(filters.get("exam_name") or "").strip():
                filters.pop("exam_name", None)
        where = _filters_to_where(filters, school_expr, class_expr, subject_expr, exam_expr)
        if where:
            sql += f"\nWHERE {where}"
        limit = 5000 if spec.report_type in (
            ReportType.STUDENT_PROFILE,
            ReportType.TREND_TRACKING,
            ReportType.COMPREHENSIVE,
            ReportType.GRADE_COMPARISON,
            ReportType.GROUP_FEATURE,
            ReportType.DIAGNOSTIC_REPORT,
        ) else 1000
        return sql + f"\nLIMIT {limit}"

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


def _split_exam_filter(raw: str) -> list[str]:
    """支持单场或 ``a;;b;;c`` 多场（分析工具多选考试）。"""
    text = (raw or "").strip()
    if not text:
        return []
    if ";;" in text:
        return [p.strip() for p in text.split(";;") if p.strip()]
    return [text]


def _format_exam_label(raw: str) -> str:
    names = _split_exam_filter(raw)
    return "、".join(names) if names else ""


def _filters_to_where(
    filters: dict[str, str],
    school_expr: str,
    class_expr: str,
    subject_expr: str,
    exam_expr: str,
) -> str:
    parts: list[str] = []
    if filters.get("school_name"):
        parts.append(f"{school_expr} LIKE '%{_sql_escape(filters['school_name'])}%'")
    if filters.get("class_name"):
        # 班级名在库内写法不一（高一1班 / 高一(1)班），用模糊匹配降低空结果
        parts.append(f"{class_expr} LIKE '%{_sql_escape(filters['class_name'])}%'")
    if filters.get("subject"):
        parts.append(f"{subject_expr} LIKE '%{_sql_escape(filters['subject'])}%'")
    if filters.get("exam_name"):
        exam_names = _split_exam_filter(filters["exam_name"])
        if len(exam_names) > 1:
            ors = " OR ".join(
                f"{exam_expr} LIKE '%{_sql_escape(n)}%'" for n in exam_names
            )
            parts.append(f"({ors})")
        elif exam_names:
            parts.append(f"{exam_expr} LIKE '%{_sql_escape(exam_names[0])}%'")
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


def _score_dicts_to_records(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """成绩扁平行 → ``{exam, student, subjects, total}`` records + exam_order。"""
    agg: dict[tuple[str, str], dict[str, Any]] = {}
    exam_seen: list[str] = []
    exam_set: set[str] = set()
    for r in rows:
        if not isinstance(r, dict):
            continue
        exam = str(r.get("exam_name") or r.get("exam") or "").strip()
        student = str(
            r.get("student_name") or r.get("student") or r.get("student_id") or ""
        ).strip()
        subject = str(r.get("subject") or r.get("subject_name") or "").strip()
        if not exam or not student:
            continue
        if exam not in exam_set:
            exam_set.add(exam)
            exam_seen.append(exam)
        key = (exam, student)
        slot = agg.setdefault(
            key, {"exam": exam, "student": student, "subjects": {}, "total": 0.0}
        )
        score_raw = r.get("score")
        if subject and score_raw is not None:
            try:
                slot["subjects"][subject] = float(score_raw)
            except (TypeError, ValueError):
                pass
        elif score_raw is not None and not subject:
            try:
                sv = float(score_raw)
                slot["subjects"].setdefault("成绩", sv)
                slot["total"] = sv
            except (TypeError, ValueError):
                pass
    for slot in agg.values():
        subs = slot.get("subjects") or {}
        if subs:
            slot["total"] = sum(float(v) for v in subs.values())
    return list(agg.values()), exam_seen


def _exam_avg_trend_records(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按考试聚合均分，供诊断报告 GENERAL_TREND_CHART。"""
    buckets: dict[str, list[float]] = {}
    order: list[str] = []
    for r in rows:
        if not isinstance(r, dict) or r.get("score") is None:
            continue
        exam = str(r.get("exam_name") or r.get("exam") or "").strip()
        if not exam:
            continue
        try:
            score = float(r["score"])
        except (TypeError, ValueError):
            continue
        if exam not in buckets:
            buckets[exam] = []
            order.append(exam)
        buckets[exam].append(score)
    out: list[dict[str, Any]] = []
    for exam in order:
        vals = buckets.get(exam) or []
        if not vals:
            continue
        out.append({"exam": exam, "avg": round(sum(vals) / len(vals), 2)})
    return out


__all__ = ["ReportIntentResolver", "ReportOrchestrator", "ReportResult"]
