"""报告类型 / 受众 / 报告规格。

``ReportType`` 是 7 类学情报告的枚举——Phase 1 落地前 4 类，其余在
后续阶段补齐，但枚举一次性定义齐全，避免后续到处加分支。

``Audience`` 决定同一份数据的叙事风格（校长看宏观排名、家长看个体建议），
模板层据此切换文案密度与术语。
"""

from __future__ import annotations

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


class Audience(str, Enum):
    """报告受众——决定叙事密度与术语。"""

    PRINCIPAL = "principal"          # 校长 / 教务：宏观 + 排名 + 预警
    GRADE_HEAD = "grade_head"        # 年级主任：班级对比 + 临界生规模
    HEAD_TEACHER = "head_teacher"    # 班主任：本班名单 + 进退步
    SUBJECT_TEACHER = "subject_teacher"  # 任课教师：单科分数段
    PARENT = "parent"                # 家长：个体 + 简化术语 + 行动建议
    DEFAULT = "default"              # 未指定时的默认（接近班主任视角）


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


__all__ = ["Audience", "ReportSpec", "ReportType"]
