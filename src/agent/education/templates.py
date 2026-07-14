"""报告类型 → 模板名映射 + 模板所需 data keys 声明。

``select_report_template`` 返回模板相对路径（相对于
``src/agent/resource/templates/``）与该模板期望的 ``data`` 字段清单。Agent
据此知道要往 ``render_html_report(data=...)`` 里放哪些键，避免漏字段导致
模板留空。

当前已落地全部 9 类标准报告模板；每类报告页头均应展示 ``REPORT_TYPE``。
"""

from __future__ import annotations

from src.agent.education.report_types import Audience, ReportType, report_type_label

#: 模板相对路径（相对 ``src/agent/resource/templates/``）。
#: 仅列出已实现的模板；未实现的 ``ReportType`` 由 ``select_report_template``
#: 返回空 ``template_name``，Agent 回退到通用模板或 inline html。
_TEMPLATE_PATH: dict[ReportType, str] = {
    ReportType.CLASS_OVERVIEW: "education/class_overview.html",
    ReportType.GRADE_COMPARISON: "education/grade_comparison.html",
    ReportType.SUBJECT_DIAGNOSIS: "education/subject_diagnosis.html",
    ReportType.STUDENT_PROFILE: "education/student_exam_analysis.html",
    ReportType.TREND_TRACKING: "education/trend_tracking.html",
    ReportType.TIER_ALERT: "education/tier_alert.html",
    ReportType.GROUP_FEATURE: "education/group_feature.html",
    ReportType.COMPREHENSIVE: "education/comprehensive.html",
    ReportType.DIAGNOSTIC_REPORT: "education/diagnostic_report.html",
}

#: 额外模板文件名 → ReportType（非主路径，但仍属上述标准类型）
_ALIAS_TEMPLATE_STEMS: dict[str, ReportType] = {
    "student_profile": ReportType.STUDENT_PROFILE,
    "student_profile_parent": ReportType.STUDENT_PROFILE,
    "student_exam_analysis": ReportType.STUDENT_PROFILE,
    "student_subject_diagnosis": ReportType.STUDENT_PROFILE,
    "class_overview_parent": ReportType.CLASS_OVERVIEW,
}

#: 受众 → 模板变体后缀（Phase 2 启用；当前仅 ``parent`` 有简化版）。
_AUDIENCE_SUFFIX: dict[Audience, str] = {
    Audience.PARENT: "_parent",
}

#: 每个 report_type 模板期望的 ``data`` 字段清单（供 Agent 校验）。
#: 九大类均要求 ``REPORT_TYPE``（标准中文类型名）。
_REQUIRED_KEYS: dict[ReportType, list[str]] = {
    ReportType.CLASS_OVERVIEW: [
        "REPORT_TITLE", "REPORT_SUBTITLE", "REPORT_TIME", "REPORT_TYPE",
        "CLASS_NAME", "EXAM_NAME",
        "TOTAL_COUNT", "AVG_SCORE", "PASS_RATE", "EXCELLENT_RATE", "STDEV",
        "MAX_SCORE", "MIN_SCORE", "GOOD_RATE", "LOW_SCORE_RATE",
        "SCORE_DIST_CHART", "SEGMENT_TABLE", "SUBJECT_RADAR_CHART",
        "SUBJECT_BREAKDOWN", "RANK_INFO", "SUMMARY", "RECOMMENDATIONS",
        "VARIANCE", "STDEV_LEVEL", "STDEV_HINT", "VARIANCE_HINT", "DISPERSION_TIP",
    ],
    ReportType.GRADE_COMPARISON: [
        "REPORT_TITLE", "REPORT_SUBTITLE", "REPORT_TIME", "REPORT_TYPE",
        "GRADE_NAME", "EXAM_NAME", "SUBJECT_NAME",
        "CLASS_COMPARE_CHART", "CLASS_RANKING_TABLE",
        "DISPERSION_INFO", "SUMMARY", "RECOMMENDATIONS",
    ],
    ReportType.SUBJECT_DIAGNOSIS: [
        "REPORT_TITLE", "REPORT_SUBTITLE", "REPORT_TIME", "REPORT_TYPE",
        "SUBJECT_NAME", "EXAM_NAME", "SCOPE",
        "AVG_SCORE", "PASS_RATE", "EXCELLENT_RATE", "STDEV",
        "VARIANCE", "STDEV_LEVEL", "STDEV_HINT", "VARIANCE_HINT", "DISPERSION_TIP",
        "SCORE_DIST_CHART", "SEGMENT_TABLE", "ITEM_TABLE",
        "KNOWLEDGE_TABLE", "KNOWLEDGE_CHART", "WEAK_KNOWLEDGE_LIST",
        "SUMMARY", "RECOMMENDATIONS",
        "STUDENT_ARCHIVE_TABLE",
    ],
    ReportType.STUDENT_PROFILE: [
        "REPORT_TITLE", "REPORT_SUBTITLE", "REPORT_TIME", "REPORT_TYPE", "COVER_META",
        "STUDENT_NAME", "EXAM_NAME", "CLASS_NAME", "SUBJECT_NAME",
        "OVERVIEW_TITLE", "OVERVIEW_KPIS", "MULTI_EXAM_SECTION",
        "MULTI_EXAM_AVG", "GAP_TO_FIRST", "EXAM_COUNT",
        "OVERVIEW_INSIGHT", "SCORE_SUMMARY_TABLE", "KEY_METRICS_TABLE",
        "SUBJECT_ANALYSIS_HTML", "TOTAL_ANALYSIS_TABLE", "TOTAL_TREND_INSIGHT",
        "CONTRIBUTION_INSIGHT", "CLASS_DIFF_TABLE", "ASSESSMENT", "RECOMMENDATIONS",
        "SUBJECT_RADAR_CHART", "TREND_LINE_CHART", "TOTAL_TREND_CHART",
        # 兼容旧字段
        "TOTAL_SCORE", "CLASS_RANK", "GRADE_RANK", "SUBJECT_TABLE", "SUMMARY",
        "FULL_SCORE", "ITEM_TABLE", "KNOWLEDGE_TABLE", "WEAK_KNOWLEDGE_LIST",
    ],
    ReportType.TREND_TRACKING: [
        "REPORT_TITLE", "REPORT_SUBTITLE", "REPORT_TIME", "REPORT_TYPE",
        "TARGET_NAME", "SUBJECT_NAME",
        "TREND_CHART", "TREND_TABLE", "CHANGE_INFO",
        "SUMMARY", "RECOMMENDATIONS",
    ],
    ReportType.TIER_ALERT: [
        "REPORT_TITLE", "REPORT_SUBTITLE", "REPORT_TIME", "REPORT_TYPE",
        "SCOPE", "EXAM_NAME",
        "CRITICAL_COUNT", "REGRESSION_COUNT", "IMBALANCED_COUNT",
        "CRITICAL_TABLE", "REGRESSION_TABLE", "IMBALANCED_TABLE",
        "SUMMARY", "RECOMMENDATIONS",
    ],
    ReportType.GROUP_FEATURE: [
        "REPORT_TITLE", "REPORT_SUBTITLE", "REPORT_TIME", "REPORT_TYPE",
        "SCOPE", "EXAM_NAME", "SUBJECT_NAME", "GROUP_DIMENSION",
        "KPI_GRID",
        "GROUP_COMPARE_CHART", "PASS_COMPARE_CHART",
        "GROUP_TABLE", "FEATURE_CARDS", "DIFF_INFO",
        "KNOWLEDGE_COMPARE_TABLE", "QUESTION_TYPE_COMPARE_TABLE", "ITEM_COMPARE_TABLE",
        "KNOWLEDGE_SECTION_CLASS", "QUESTION_TYPE_SECTION_CLASS", "ITEM_SECTION_CLASS",
        "SUMMARY", "RECOMMENDATIONS",
    ],
    ReportType.COMPREHENSIVE: [
        # 封面 + 目录
        "COVER_TITLE", "COVER_SUBTITLE", "COVER_META", "REPORT_TIME", "REPORT_TYPE",
        # S1 班级整体概览
        "OVERVIEW_KPI_GRID", "OVERVIEW_INSIGHT",
        # S2 各科成绩趋势分析
        "SUBJECT_TREND_CHART", "SUBJECT_COMPARE_CHART", "SUBJECT_KPI_GRID",
        "SUBJECT_WARNING_INSIGHT", "SUBJECT_SUCCESS_INSIGHT",
        # S3 总分与各科相关性
        "CORRELATION_CHART", "CORRELATION_INSIGHT",
        # S4 学生趋势分布与水平分布
        "TREND_DIST_CHART", "LEVEL_DIST_CHART", "TREND_DIST_KPI_GRID",
        "TREND_DIST_INSIGHT",
        # S5 进步最快 & 退步最快
        "PROGRESS_REGRESS_CHART", "PROGRESS_TABLE", "REGRESS_TABLE",
        "PROGRESS_INSIGHT", "REGRESS_INSIGHT",
        # S6 偏科生诊断
        "IMBALANCE_CHART", "IMBALANCE_TABLE", "IMBALANCE_INSIGHT",
        # S7 单科进步/退步之最
        "SUBJECT_EXTREME_CHART", "SUBJECT_PROGRESS_TABLE", "SUBJECT_REGRESS_TABLE",
        "SUBJECT_EXTREME_INSIGHT",
        # S8 全体学生总分轨迹
        "TRAJECTORY_CHART", "TRAJECTORY_NOTE",
        # S9 每位学生详细档案与建议
        "STUDENT_ARCHIVE_TABLE",
    ],
    ReportType.DIAGNOSTIC_REPORT: [
        "REPORT_TITLE", "REPORT_SUBTITLE", "REPORT_TIME", "REPORT_TYPE",
        "SCOPE", "EXAM_NAME", "SUBJECT_NAME",
        "KPI_GRID", "GENERAL_TREND_CHART", "GENERAL_INSIGHT", "DISTRICT_COMPARE_CHART",
        "CLASS_DIFF_HEATMAP", "SEGMENT_COMPARE_TABLE", "SPECIAL_INSIGHT",
        "TREND_LINE_CHART", "PROGRESS_REGRESS_TABLE", "DYNAMIC_INSIGHT",
        "DISTRICT_SUMMARY", "AT_RISK_SUMMARY",
        "SUMMARY", "RECOMMENDATIONS",
    ],
}


def resolve_report_type_from_template(template: str) -> ReportType | None:
    """由模板路径解析对应标准 ``ReportType``（九大类之一）。"""
    path = (template or "").replace("\\", "/").strip()
    if not path:
        return None
    stem = path.rsplit("/", 1)[-1]
    if stem.endswith(".html"):
        stem = stem[: -len(".html")]
    if stem in _ALIAS_TEMPLATE_STEMS:
        return _ALIAS_TEMPLATE_STEMS[stem]
    for rt, p in _TEMPLATE_PATH.items():
        if path.endswith(p) or path == p:
            return rt
        p_stem = p.rsplit("/", 1)[-1]
        if p_stem.endswith(".html"):
            p_stem = p_stem[: -len(".html")]
        if stem == p_stem:
            return rt
    return None


def ensure_report_type_in_data(template: str, data: dict) -> dict:
    """九大类模板：保证 ``REPORT_TYPE`` 为标准中文名（含枚举值纠偏）。"""
    out = dict(data)
    raw = str(out.get("REPORT_TYPE") or "").strip()
    rt = resolve_report_type_from_template(template)
    if raw:
        # LLM 常直接填入 class_overview 等枚举值，统一换成中文角标
        try:
            out["REPORT_TYPE"] = report_type_label(ReportType(raw))
            return out
        except ValueError:
            return out
    if rt is not None:
        out["REPORT_TYPE"] = report_type_label(rt)
    return out


def select_report_template(
    report_type: ReportType,
    audience: Audience = Audience.DEFAULT,
) -> dict[str, object]:
    """返回 ``{template_name, data_keys}``。

    若该报告类型模板尚未实现，``template_name`` 为空串，``data_keys`` 为空列表
    ——Agent 应据此回退到通用 ``score_analysis_report`` 或 inline html。
    """
    base = _TEMPLATE_PATH.get(report_type, "")
    suffix = _AUDIENCE_SUFFIX.get(audience, "")
    if base and suffix:
        # audience 变体：class_overview_parent.html
        stem, _, ext = base.rpartition(".")
        candidate = f"{stem}{suffix}.{ext}" if stem else base
    else:
        candidate = base

    return {
        "template_name": candidate,
        "data_keys": list(_REQUIRED_KEYS.get(report_type, [])),
    }


__all__ = [
    "ensure_report_type_in_data",
    "resolve_report_type_from_template",
    "select_report_template",
]
