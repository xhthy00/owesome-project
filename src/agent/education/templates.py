"""报告类型 → 模板名映射 + 模板所需 data keys 声明。

``select_report_template`` 返回模板相对路径（相对于
``src/agent/resource/templates/``）与该模板期望的 ``data`` 字段清单。Agent
据此知道要往 ``render_html_report(data=...)`` 里放哪些键，避免漏字段导致
模板留空。

Phase 1 落地 4 类模板（含升级后的通用 ``score_analysis_report``），其余
``ReportType`` 在对应阶段补齐模板与 keys。
"""

from __future__ import annotations

from src.agent.education.report_types import Audience, ReportType

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
}

#: 受众 → 模板变体后缀（Phase 2 启用；当前仅 ``parent`` 有简化版）。
_AUDIENCE_SUFFIX: dict[Audience, str] = {
    Audience.PARENT: "_parent",
}

#: 每个 report_type 模板期望的 ``data`` 字段清单（供 Agent 校验）。
_REQUIRED_KEYS: dict[ReportType, list[str]] = {
    ReportType.CLASS_OVERVIEW: [
        "REPORT_TITLE", "REPORT_SUBTITLE", "REPORT_TIME",
        "CLASS_NAME", "EXAM_NAME",
        "TOTAL_COUNT", "AVG_SCORE", "PASS_RATE", "EXCELLENT_RATE", "STDEV",
        "SCORE_DIST_CHART", "SUBJECT_RADAR_CHART",
        "SUBJECT_BREAKDOWN", "RANK_INFO", "SUMMARY", "RECOMMENDATIONS",
    ],
    ReportType.GRADE_COMPARISON: [
        "REPORT_TITLE", "REPORT_SUBTITLE", "REPORT_TIME",
        "GRADE_NAME", "EXAM_NAME", "SUBJECT_NAME",
        "CLASS_COMPARE_CHART", "CLASS_RANKING_TABLE",
        "DISPERSION_INFO", "SUMMARY", "RECOMMENDATIONS",
    ],
    ReportType.SUBJECT_DIAGNOSIS: [
        "REPORT_TITLE", "REPORT_SUBTITLE", "REPORT_TIME",
        "SUBJECT_NAME", "EXAM_NAME", "SCOPE",
        "AVG_SCORE", "PASS_RATE", "EXCELLENT_RATE", "STDEV",
        "SCORE_DIST_CHART", "SEGMENT_TABLE",
        "SUMMARY", "RECOMMENDATIONS",
    ],
    ReportType.STUDENT_PROFILE: [
        "REPORT_TITLE", "REPORT_SUBTITLE", "REPORT_TIME", "COVER_META",
        "STUDENT_NAME", "EXAM_NAME", "CLASS_NAME",
        "OVERVIEW_INSIGHT", "SCORE_SUMMARY_TABLE", "KEY_METRICS_TABLE",
        "SUBJECT_ANALYSIS_HTML", "TOTAL_ANALYSIS_TABLE", "TOTAL_TREND_INSIGHT",
        "CONTRIBUTION_INSIGHT", "CLASS_DIFF_TABLE", "ASSESSMENT", "RECOMMENDATIONS",
        "SUBJECT_RADAR_CHART", "TREND_LINE_CHART", "TOTAL_TREND_CHART",
        # 兼容旧字段
        "TOTAL_SCORE", "CLASS_RANK", "GRADE_RANK", "SUBJECT_TABLE", "SUMMARY",
    ],
    ReportType.TREND_TRACKING: [
        "REPORT_TITLE", "REPORT_SUBTITLE", "REPORT_TIME",
        "TARGET_NAME", "SUBJECT_NAME",
        "TREND_CHART", "TREND_TABLE", "CHANGE_INFO",
        "SUMMARY", "RECOMMENDATIONS",
    ],
    ReportType.TIER_ALERT: [
        "REPORT_TITLE", "REPORT_SUBTITLE", "REPORT_TIME",
        "SCOPE", "EXAM_NAME",
        "CRITICAL_COUNT", "REGRESSION_COUNT", "IMBALANCED_COUNT",
        "CRITICAL_TABLE", "REGRESSION_TABLE", "IMBALANCED_TABLE",
        "SUMMARY", "RECOMMENDATIONS",
    ],
    ReportType.GROUP_FEATURE: [
        "REPORT_TITLE", "REPORT_SUBTITLE", "REPORT_TIME",
        "SCOPE", "EXAM_NAME", "GROUP_DIMENSION",
        "GROUP_COMPARE_CHART", "GROUP_TABLE",
        "DIFF_INFO",
        "SUMMARY", "RECOMMENDATIONS",
    ],
    ReportType.COMPREHENSIVE: [
        # 封面 + 目录
        "COVER_TITLE", "COVER_SUBTITLE", "COVER_META", "REPORT_TIME",
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
}


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


__all__ = ["select_report_template"]
