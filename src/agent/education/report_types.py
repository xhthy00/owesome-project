"""报告类型 / 受众 / 报告规格。

``ReportType`` 是当前系统的标准学情报告枚举；生成报告时应在页头
角标 / 列表标题中显示对应中文名（见 ``REPORT_TYPE_LABELS``）。

``Audience`` 决定同一份数据的叙事风格（校长看宏观排名、家长看个体建议），
模板层据此切换文案密度与术语。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum


class ReportType(str, Enum):
    """学情报告类型。

    值即模板文件名（去掉 ``.html``），``select_report_template`` 直接用。
    """

    CLASS_OVERVIEW = "class_overview"        # 班级总览
    GRADE_COMPARISON = "grade_comparison"    # 年级横向对比
    SUBJECT_DIAGNOSIS = "subject_diagnosis"  # 科目诊断
    STUDENT_PROFILE = "student_profile"      # 学生个体
    TREND_TRACKING = "trend_tracking"        # 历次趋势
    TIER_ALERT = "tier_alert"                # 分层预警
    GROUP_FEATURE = "group_feature"          # 群体特征
    COMPREHENSIVE = "comprehensive"          # 多次考试综合分析报告
    DIAGNOSTIC_REPORT = "diagnostic_report"  # 全市文理达线 + 总分十分段
    LINE_REACH = "line_reach"                # 全市达线情况分析（环比）
    SUBJECT_AVG = "subject_avg"              # 区县/学校均分（三四五六门+九科）
    ASSIGN_GRADE = "assign_grade"            # 再选科目 ABCDE
    RANK_BUCKET = "rank_bucket"              # 高分位次桶
    CONTRIBUTION = "contribution"            # 切线贡献分
    COMBO_REACH = "combo_reach"              # 选科组合达线
    ELITE_ROSTER = "elite_roster"            # 脱敏高分名单
    SCORE_BAND = "score_band"                # 总分十分段 / 学科五分段
    SUBJECT_RESEARCH = "subject_research"    # 学科教研分析报告（一校×一场）
    DIFFICULTY_CURVE = "difficulty_curve"    # 全卷/小题难度曲线（得分率）


class Audience(str, Enum):
    """报告受众——决定叙事密度与术语。"""

    PRINCIPAL = "principal"          # 校长 / 教务：宏观 + 排名 + 预警
    GRADE_HEAD = "grade_head"        # 年级主任：班级对比 + 临界生规模
    HEAD_TEACHER = "head_teacher"    # 班主任：本班名单 + 进退步
    SUBJECT_TEACHER = "subject_teacher"  # 任课教师：单科分数段
    PARENT = "parent"                # 家长：个体 + 简化术语 + 行动建议
    DEFAULT = "default"              # 未指定时的默认（接近班主任视角）


#: 报告类型 → 中文展示名（报告页角标 / 列表标题共用）
REPORT_TYPE_LABELS: dict[ReportType, str] = {
    ReportType.CLASS_OVERVIEW: "班级总览报告",
    ReportType.GRADE_COMPARISON: "班级横向对比报告",
    ReportType.SUBJECT_DIAGNOSIS: "科目诊断报告",
    ReportType.STUDENT_PROFILE: "学生学情报告",
    ReportType.TREND_TRACKING: "成绩趋势报告",
    ReportType.TIER_ALERT: "分层预警报告",
    ReportType.GROUP_FEATURE: "群体特征报告",
    ReportType.COMPREHENSIVE: "综合分析报告",
    ReportType.DIAGNOSTIC_REPORT: "结构化诊断报告",
    ReportType.LINE_REACH: "全市达线分析",
    ReportType.SUBJECT_AVG: "均分情况分析",
    ReportType.ASSIGN_GRADE: "选考等级分析",
    ReportType.RANK_BUCKET: "高分位次分析",
    ReportType.CONTRIBUTION: "贡献分分析",
    ReportType.COMBO_REACH: "选科组合达线",
    ReportType.ELITE_ROSTER: "高分名单分析",
    ReportType.SCORE_BAND: "分段统计",
    ReportType.SUBJECT_RESEARCH: "学科教研分析报告",
    ReportType.DIFFICULTY_CURVE: "难度曲线",
}

_KNOWN_TYPE_MARKERS: frozenset[str] = frozenset(
    {rt.value for rt in ReportType} | set(REPORT_TYPE_LABELS.values())
)


def report_type_label(report_type: ReportType | str) -> str:
    """返回报告类型的中文名。"""
    if isinstance(report_type, ReportType):
        return REPORT_TYPE_LABELS.get(report_type, "学情报告")
    raw = str(report_type or "").strip()
    if raw in REPORT_TYPE_LABELS.values():
        return raw
    try:
        return REPORT_TYPE_LABELS.get(ReportType(raw), "学情报告")
    except ValueError:
        return "学情报告"


def strip_report_type_markers(title: str) -> str:
    """去掉标题首尾的类型角标（兼容 ``【class_overview】`` / ``【班级总览报告】``）。"""
    t = str(title or "").strip()
    while True:
        m = re.match(r"^【([^】]+)】\s*", t)
        if not m:
            break
        token = m.group(1).strip()
        if token not in _KNOWN_TYPE_MARKERS:
            break
        t = t[m.end() :].strip()
    m = re.search(r"[【（(]([^】）)]+)[】）)]\s*$", t)
    if m and m.group(1).strip() in _KNOWN_TYPE_MARKERS:
        t = t[: m.start()].strip()
    return t


def format_report_display_title(
    title: str,
    report_type: ReportType | str | None = None,
    *,
    type_label: str | None = None,
) -> str:
    """按「报告名称【报告类型】」拼接列表/预览角标标题。"""
    base = strip_report_type_markers(title)
    label = ""
    raw_label = str(type_label or "").strip()
    if raw_label in REPORT_TYPE_LABELS.values():
        label = raw_label
    elif raw_label:
        converted = report_type_label(raw_label)
        if converted != "学情报告" or raw_label == "学情报告":
            label = converted
    if not label and report_type is not None:
        label = report_type_label(report_type)
    if not label:
        return base or "Report"
    if not base:
        return label
    if label in base:
        return base
    return f"{base}【{label}】"


@dataclass
class ReportSpec:
    """一次报告生成的完整规格。

    ``filters`` 透传给查询/统计层，常见键：``exam_id`` / ``class_name`` /
    ``grade`` / ``subject`` / ``student_name``。``include_charts`` 控制模板
    是否嵌入 ECharts（无网络环境可关闭，回落纯 KPI 文本）。
    """

    report_type: ReportType
    audience: Audience = Audience.DEFAULT
    filters: dict[str, str] = field(default_factory=dict)
    include_charts: bool = True


__all__ = [
    "Audience",
    "REPORT_TYPE_LABELS",
    "ReportSpec",
    "ReportType",
    "format_report_display_title",
    "report_type_label",
    "strip_report_type_markers",
]
