"""报告意图路由：先判 needs_report，再（若需要）选 ReportType。

主路径：LLM 分类；失败则规则兜底。默认立场：拿不准不出报告（事实问走 SQL）。
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Any

from src.agent.education.report_types import REPORT_TYPE_LABELS, ReportType
from src.agent.util.json_parser import parse_json_tolerant

logger = logging.getLogger(__name__)

#: 明确「要出报告」的意图词（fallback 用；拿不准默认不出报告）
_EXPLICIT_REPORT_HINTS = (
    "报告",
    "学情分析",
    "学情报告",
    "诊断报告",
    "总览",
    "概览",
    "横向对比",
    "横向分析",
    "横向多维",
    "多维对比",
    "多维分析",
    "群体特征",
    "预警",
    "综合分析",
    "综合报告",
    "结构化诊断",
    "详细分析",
    "科目诊断",
    "学科诊断",
    "成绩趋势",
    "趋势报告",
    "形成报告",
    "分析报告",
    "个人画像",
    "学生画像",
    "个体画像",
)

#: 报告类型一句话定义（供 LLM prompt）
_REPORT_TYPE_DEFS: dict[ReportType, str] = {
    ReportType.CLASS_OVERVIEW: "单班成绩总览（KPI/分数段），非各班横向、非小题诊断",
    ReportType.GRADE_COMPARISON: "全校各班横向对比（均分/小题/知识点分班对比），禁止缩成单班",
    ReportType.SUBJECT_DIAGNOSIS: "单科详细诊断（小题/知识点），通常带班级或学校范围",
    ReportType.STUDENT_PROFILE: "单个学生学情/个人画像/个人报告/知识点加强",
    ReportType.TREND_TRACKING: "成绩趋势/走势/进退步折线（非综合九维）",
    ReportType.TIER_ALERT: "临界生/退步/偏科分层预警",
    ReportType.GROUP_FEATURE: "明确「群体特征/按X群体」的分维画像，不是「班级横向对比」",
    ReportType.COMPREHENSIVE: "班级多次/历次考试综合复盘（九维）；各校/全市不是本类型",
    ReportType.DIAGNOSTIC_REPORT: "全市/各校文理达线与总分十分段（物理类/历史类），不是班级综合、不是单科小题诊断",
    ReportType.LINE_REACH: "全市达线情况分析（人数/率 + 较上场环比），不是结构化诊断",
    ReportType.SUBJECT_AVG: "区县/学校均分情况（三四五六门+各科），不是班级总览",
    ReportType.ASSIGN_GRADE: "再选科目 ABCDE 等级人数与率",
    ReportType.RANK_BUCKET: "物理/历史高分位次桶（前10/20/50/…）",
    ReportType.CONTRIBUTION: "预测线贡献分（切线生各科均值）",
    ReportType.COMBO_REACH: "理科选科组合特控/本科达线",
    ReportType.ELITE_ROSTER: "理前100/文前30脱敏高分名单",
    ReportType.SCORE_BAND: "各区县/各类校总分十分段与学科五分段（人数/比例/累计）",
    ReportType.DIFFICULTY_CURVE: "单科难度曲线（十分段×得分率），非十分段、非科目诊断",
    ReportType.SUBJECT_RESEARCH: "一校一场学科教研分析（全市校际+层级均分+拖后腿小题），不是科目诊断",
}

#: 兜底关键词表（仅在 needs_report=true 时用于选型）
_FALLBACK_KEYWORDS: list[tuple[ReportType, tuple[str, ...]]] = [
    (
        ReportType.SUBJECT_RESEARCH,
        (
            "学科教研分析报告",
            "学科教研分析报",
            "教研分析报告",
            "教研分析报",
            "教科院分析报告",
            "教科院分析报",
            "教科院学科分析",
        ),
    ),
    (
        ReportType.LINE_REACH,
        ("达线情况", "达线分析", "达线报告", "达线环比", "预测线分析"),
    ),
    (ReportType.SCORE_BAND, ("十分段", "10分段", "五分段", "5分段", "分段统计")),
    (ReportType.DIFFICULTY_CURVE, ("难度曲线", "难度分析", "试题质量", "试卷得分率", "试题得分率")),
    (
        ReportType.COMPREHENSIVE,
        (
            "综合分析报告",
            "综合报告",
            "综合分析",
            "多次考试",
            "三次考试",
            "两次考试",
            "纵向分析",
            "所有考试",
            "全部考试",
            "历次考试",
            "各次考试",
        ),
    ),
    (ReportType.TIER_ALERT, ("预警", "临界生", "退步生", "偏科", "分层")),
    (ReportType.TREND_TRACKING, ("趋势", "变化", "历次成绩", "走势", "进退步")),
    (
        ReportType.STUDENT_PROFILE,
        ("学生个体", "个人报告", "个人画像", "学生画像", "个体画像", "该生", "这名学生"),
    ),
    (
        ReportType.GROUP_FEATURE,
        ("群体特征", "群体对比特征", "对比特征", "按班级群体", "群体特征报告"),
    ),
    (
        ReportType.GRADE_COMPARISON,
        (
            "年级对比",
            "各班对比",
            "班级对比",
            "年级排名",
            "班级排名",
            "各个班级",
            "各班级",
            "横向对比",
            "横向分析",
            "横向多维",
            "多维对比",
        ),
    ),
    (
        ReportType.SUBJECT_DIAGNOSIS,
        (
            "科目诊断",
            "科目分析",
            "学科诊断",
            "小题",
            "逐题",
            "知识点",
            "详细分析",
        ),
    ),
    (
        ReportType.CLASS_OVERVIEW,
        (
            "班级总览",
            "成绩总览",
            "总览报告",
            "成绩概览",
            "班级成绩",
            "班级报告",
            "班级分析",
            "期中分析",
            "期末分析",
        ),
    ),
]

_POSITIVE_HINTS: dict[ReportType, tuple[str, ...]] = {
    ReportType.CLASS_OVERVIEW: (
        "成绩总览",
        "班级总览",
        "总览报告",
        "成绩概览",
        "班级成绩",
        "班级报告",
    ),
    ReportType.GRADE_COMPARISON: (
        "横向对比",
        "横向分析",
        "横向多维",
        "班级横向",
        "各班对比",
        "各班横向",
        "各个班级",
        "各班级",
        "年级对比",
        "班级排名",
        "年级排名",
        "多维对比",
    ),
    ReportType.SUBJECT_DIAGNOSIS: (
        "科目诊断",
        "学科诊断",
        "详细分析",
        "小题",
        "逐题",
        "知识点",
    ),
    ReportType.STUDENT_PROFILE: (
        "该生",
        "这名学生",
        "个人报告",
        "个人画像",
        "学生画像",
        "个体画像",
        "学号",
        "学生个体",
    ),
    ReportType.TREND_TRACKING: (
        "成绩趋势",
        "趋势报告",
        "成绩走势",
        "进退步",
        "折线",
        "变化",
    ),
    ReportType.TIER_ALERT: ("临界生", "分层预警", "退步生", "偏科预警", "预警报告"),
    ReportType.GROUP_FEATURE: (
        "群体特征",
        "群体对比特征",
        "按班级群体",
        "群体特征报告",
        "群体对比分析",
        "对比特征",
    ),
    ReportType.COMPREHENSIVE: ("综合分析", "综合报告", "综合复盘", "所有考试", "历次考试"),
    ReportType.DIAGNOSTIC_REPORT: ("全市", "各校", "各学校", "结构化诊断", "区县诊断", "质量检测"),
    ReportType.LINE_REACH: ("达线情况", "达线分析", "达线报告", "达线环比", "预测线分析"),
    ReportType.SUBJECT_AVG: ("均分情况", "各科均分", "三门总均分", "六门总均分"),
    ReportType.ASSIGN_GRADE: ("ABCDE", "选考等级", "等级赋分"),
    ReportType.RANK_BUCKET: ("位次情况", "高分位次"),
    ReportType.CONTRIBUTION: ("贡献分",),
    ReportType.COMBO_REACH: ("选科组合达线", "各选择组合达线"),
    ReportType.ELITE_ROSTER: ("理前100", "文前30", "冲刺清北", "冲刺南大"),
    ReportType.SCORE_BAND: ("十分段", "10分段", "五分段", "5分段", "分段统计"),
    ReportType.SUBJECT_RESEARCH: (
        "学科教研分析报告",
        "学科教研分析报",
        "教研分析报告",
        "教研分析报",
        "教科院分析报告",
        "教科院分析报",
        "教科院学科分析",
    ),
    ReportType.DIFFICULTY_CURVE: ("难度曲线", "难度分析", "试题质量", "试卷得分率", "试题得分率"),
}

_NEGATIVE_HINTS: dict[ReportType, tuple[str, ...]] = {
    ReportType.GROUP_FEATURE: ("横向对比", "横向分析", "班级横向", "各班横向", "各个班级"),
    ReportType.CLASS_OVERVIEW: ("横向", "各班", "小题", "知识点", "预警", "群体特征"),
    ReportType.GRADE_COMPARISON: ("群体特征", "按班级群体", "临界生", "总览", "与全市", "全市均分"),
    ReportType.DIAGNOSTIC_REPORT: (
        "达线", "预测线", "分数线", "均分情况", "ABCDE", "位次", "贡献分",
        "十分段", "10分段", "五分段", "5分段", "分段统计",
    ),
    ReportType.SUBJECT_DIAGNOSIS: (
        "横向对比",
        "各个班级",
        "群体特征",
        "综合分析",
        "个人画像",
        "学生画像",
        "个体画像",
        "难度曲线",
        "难度分析",
        "试卷得分率",
        "试题得分率",
        "教研分析",
        "教科院分析",
    ),
}

_TIE_BREAK: dict[ReportType, int] = {
    ReportType.SUBJECT_RESEARCH: 93,
    ReportType.LINE_REACH: 92,
    ReportType.SUBJECT_AVG: 91,
    ReportType.ASSIGN_GRADE: 90,
    ReportType.RANK_BUCKET: 89,
    ReportType.CONTRIBUTION: 88,
    ReportType.COMBO_REACH: 87,
    ReportType.ELITE_ROSTER: 86,
    ReportType.SCORE_BAND: 93,
    ReportType.DIFFICULTY_CURVE: 94,
    ReportType.DIAGNOSTIC_REPORT: 85,
    ReportType.STUDENT_PROFILE: 85,
    ReportType.COMPREHENSIVE: 80,
    ReportType.TREND_TRACKING: 75,
    ReportType.TIER_ALERT: 70,
    ReportType.GRADE_COMPARISON: 65,
    ReportType.GROUP_FEATURE: 55,
    ReportType.SUBJECT_DIAGNOSIS: 50,
    ReportType.CLASS_OVERVIEW: 40,
}

_CLASS_COMPARE_HINTS = (
    "横向对比",
    "横向分析",
    "横向多维",
    "班级横向",
    "各班对比",
    "各班横向",
    "各个班级",
    "各班级",
    "年级对比",
)
_EXPLICIT_GROUP_HINTS = (
    "群体特征",
    "群体对比特征",
    "按班级群体",
    "群体特征报告",
    "群体对比分析",
)


@dataclass(frozen=True)
class ReportRoute:
    """一次意图路由结果。"""

    needs_report: bool
    report_type: ReportType | None = None
    confidence: float = 0.0
    reason: str = ""
    source: str = "fallback"  # llm | fallback | hard

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["report_type"] = self.report_type.value if self.report_type else None
        return d


def _has_explicit_report_intent(question: str) -> bool:
    q = question or ""
    return any(h in q for h in _EXPLICIT_REPORT_HINTS)


def _is_parent_child_xueqing(question: str) -> bool:
    q = question or ""
    return "学情报告" in q and any(
        h in q for h in ("孩子", "家长", "该生", "学生", "个人", "这名")
    )


def _bureau_report_type(question: str) -> ReportType | None:
    from src.agent.education.query_parse import (
        is_assign_grade_report_query,
        is_combo_reach_report_query,
        is_contribution_report_query,
        is_elite_roster_report_query,
        is_rank_bucket_report_query,
        is_score_band_report_query,
        is_subject_avg_report_query,
    )

    q = (question or "").strip()
    if is_combo_reach_report_query(q):
        return ReportType.COMBO_REACH
    if is_elite_roster_report_query(q):
        return ReportType.ELITE_ROSTER
    if is_contribution_report_query(q):
        return ReportType.CONTRIBUTION
    if is_score_band_report_query(q):
        return ReportType.SCORE_BAND
    if is_rank_bucket_report_query(q):
        return ReportType.RANK_BUCKET
    if is_assign_grade_report_query(q):
        return ReportType.ASSIGN_GRADE
    if is_subject_avg_report_query(q):
        return ReportType.SUBJECT_AVG
    return None


def _candidate_pool(question: str) -> list[ReportType]:
    """规则硬约束：仅在需要报告时收缩候选类型。"""
    from src.agent.education.query_parse import (
        extract_school_target,
        is_all_schools_scope_query,
        is_citywide_analysis_query,
        is_difficulty_curve_report_query,
        is_individual_student_analysis_query,
        is_item_difficulty_curve_query,
        is_line_reach_query,
        is_line_reach_report_query,
        is_multi_exam_class_analysis_query,
        is_school_vs_city_avg_query,
        is_school_vs_school_type_avg_query,
        is_score_threshold_fact_query,
        is_structured_diagnostic_query,
        is_subject_research_report_query,
        is_tier_alert_query,
        is_trend_tracking_query,
    )

    q = (question or "").strip()
    all_types = list(ReportType)

    if is_item_difficulty_curve_query(q):
        return []
    if is_difficulty_curve_report_query(q):
        return [ReportType.DIFFICULTY_CURVE]
    bureau = _bureau_report_type(q)
    if bureau is not None:
        return [bureau]
    if is_line_reach_report_query(q):
        return [ReportType.LINE_REACH]
    if is_subject_research_report_query(q):
        return [ReportType.SUBJECT_RESEARCH]
    if is_line_reach_query(q):
        return []
    if is_score_threshold_fact_query(q):
        return []
    if is_school_vs_city_avg_query(q):
        return []
    if is_school_vs_school_type_avg_query(q):
        return []
    if is_citywide_analysis_query(q) or is_structured_diagnostic_query(q):
        return [ReportType.DIAGNOSTIC_REPORT]
    if is_individual_student_analysis_query(q):
        return [ReportType.STUDENT_PROFILE]

    candidates = set(all_types)
    candidates.discard(ReportType.DIAGNOSTIC_REPORT)
    candidates.discard(ReportType.LINE_REACH)
    candidates.discard(ReportType.SUBJECT_RESEARCH)
    for rt in (
        ReportType.SUBJECT_AVG,
        ReportType.ASSIGN_GRADE,
        ReportType.RANK_BUCKET,
        ReportType.CONTRIBUTION,
        ReportType.COMBO_REACH,
        ReportType.ELITE_ROSTER,
        ReportType.SCORE_BAND,
        ReportType.DIFFICULTY_CURVE,
    ):
        candidates.discard(rt)

    has_class_compare = any(h in q for h in _CLASS_COMPARE_HINTS)
    has_explicit_group = any(h in q for h in _EXPLICIT_GROUP_HINTS)

    if not extract_school_target(q):
        candidates.discard(ReportType.GROUP_FEATURE)
        if not has_class_compare:
            candidates.discard(ReportType.GRADE_COMPARISON)
    elif "全市" in q and not has_class_compare:
        candidates.discard(ReportType.GRADE_COMPARISON)

    if has_class_compare and not has_explicit_group:
        candidates.discard(ReportType.GROUP_FEATURE)

    if is_tier_alert_query(q):
        candidates.add(ReportType.TIER_ALERT)
    if is_trend_tracking_query(q):
        candidates.add(ReportType.TREND_TRACKING)
    if is_multi_exam_class_analysis_query(q):
        pass
    if is_all_schools_scope_query(q):
        candidates.discard(ReportType.COMPREHENSIVE)
        candidates.discard(ReportType.CLASS_OVERVIEW)
        candidates.discard(ReportType.STUDENT_PROFILE)

    if not candidates:
        return [ReportType.CLASS_OVERVIEW]
    return sorted(candidates, key=lambda t: (-_TIE_BREAK.get(t, 0), t.value))


def _score_type(question: str, rt: ReportType) -> float:
    q = question or ""
    score = 0.0
    for h in _POSITIVE_HINTS.get(rt, ()):
        if h in q:
            score += 2.0 if len(h) >= 4 else 1.0
    for h in _NEGATIVE_HINTS.get(rt, ()):
        if h in q:
            score -= 2.5 if len(h) >= 4 else 1.5
    if rt == ReportType.GRADE_COMPARISON and "学情" in q and any(
        h in q for h in _CLASS_COMPARE_HINTS
    ):
        score += 1.5
    return score


def _pick_report_type(question: str, pool: list[ReportType]) -> tuple[ReportType, float, str]:
    """在已确认 needs_report=true 时，从候选中选类型。"""
    from src.agent.education.query_parse import (
        extract_student_target,
        is_class_overview_query,
        is_group_feature_query,
        is_multi_exam_class_analysis_query,
        is_school_class_comparison_query,
        is_school_exam_report_query,
        is_tier_alert_query,
        is_trend_tracking_query,
    )

    q = question or ""
    if len(pool) == 1:
        return pool[0], 0.95, "硬约束唯一候选"

    if _is_parent_child_xueqing(q) and ReportType.STUDENT_PROFILE in pool:
        return ReportType.STUDENT_PROFILE, 0.85, "家长/个体学情关键词"
    if extract_student_target(q) and any(
        h in q
        for h in (
            "知识点",
            "成绩分析",
            "学情",
            "薄弱",
            "加强",
            "分析报告",
            "个人画像",
            "学生画像",
            "个体画像",
            "画像",
        )
    ):
        if ReportType.STUDENT_PROFILE in pool:
            return ReportType.STUDENT_PROFILE, 0.85, "学生目标+学情关键词"

    detector_boost: list[tuple[ReportType, float, str]] = []
    if is_school_class_comparison_query(q) and ReportType.GRADE_COMPARISON in pool:
        detector_boost.append((ReportType.GRADE_COMPARISON, 5.0, "班级横向对比探测器"))
    if is_group_feature_query(q) and ReportType.GROUP_FEATURE in pool:
        detector_boost.append((ReportType.GROUP_FEATURE, 5.0, "群体特征探测器"))
    if is_class_overview_query(q) and ReportType.CLASS_OVERVIEW in pool:
        detector_boost.append((ReportType.CLASS_OVERVIEW, 5.0, "班级总览探测器"))
    if is_tier_alert_query(q) and ReportType.TIER_ALERT in pool:
        detector_boost.append((ReportType.TIER_ALERT, 5.0, "分层预警探测器"))
    if is_trend_tracking_query(q) and ReportType.TREND_TRACKING in pool:
        detector_boost.append((ReportType.TREND_TRACKING, 5.0, "趋势探测器"))
    if is_multi_exam_class_analysis_query(q) and ReportType.COMPREHENSIVE in pool:
        detector_boost.append((ReportType.COMPREHENSIVE, 5.0, "多场综合探测器"))
    if is_school_exam_report_query(q) and ReportType.SUBJECT_DIAGNOSIS in pool:
        detector_boost.append((ReportType.SUBJECT_DIAGNOSIS, 2.0, "学校科目报告探测器"))

    best_rt = pool[0]
    best_score = float("-inf")
    best_reason = "打分兜底"
    scored: list[tuple[ReportType, float, str]] = []
    for rt in pool:
        s = _score_type(q, rt)
        reason = "打分兜底"
        for drt, boost, why in detector_boost:
            if drt == rt:
                s += boost
                reason = why
        scored.append((rt, s, reason))
        if s > best_score:
            best_score = s
            best_rt = rt
            best_reason = reason

    if best_score <= 0:
        for rt, keywords in _FALLBACK_KEYWORDS:
            if rt in pool and any(k in q for k in keywords):
                return rt, 0.6, f"关键词回落:{keywords[0]}"
        # 有报告意图但无类型信号：保守用 class_overview
        rt = (
            ReportType.CLASS_OVERVIEW
            if ReportType.CLASS_OVERVIEW in pool
            else pool[0]
        )
        return rt, 0.45, "有报告意图但类型不明"

    tied = [rt for rt, s, _ in scored if abs(s - best_score) < 1e-9]
    if len(tied) > 1:
        best_rt = max(tied, key=lambda t: (_TIE_BREAK.get(t, 0), t.value))

    conf = 0.55 if best_score < 3 else min(0.9, 0.55 + best_score * 0.05)
    return best_rt, round(conf, 3), best_reason or f"打分最高({best_score:.1f})"


def fallback_classify_report_intent(question: str) -> ReportRoute:
    """LLM 失败时的规则兜底：无明确报告意图 → needs_report=false。"""
    from src.agent.education.query_parse import (
        is_citywide_analysis_query,
        is_class_overview_query,
        is_difficulty_curve_report_query,
        is_group_feature_query,
        is_individual_student_analysis_query,
        is_item_difficulty_curve_query,
        is_knowledge_cohort_gap_query,
        is_line_reach_query,
        is_line_reach_report_query,
        is_multi_exam_class_analysis_query,
        is_school_class_comparison_query,
        is_school_exam_report_query,
        is_school_vs_city_avg_query,
        is_school_vs_school_type_avg_query,
        is_score_threshold_fact_query,
        is_structured_diagnostic_query,
        is_subject_research_report_query,
        is_tier_alert_query,
        is_trend_tracking_query,
    )

    q = (question or "").strip()
    if not q:
        return ReportRoute(
            needs_report=False,
            report_type=None,
            confidence=0.2,
            reason="空问句不出报告",
            source="fallback",
        )

    if is_item_difficulty_curve_query(q):
        return ReportRoute(
            needs_report=False,
            report_type=None,
            confidence=0.95,
            reason="单题难度曲线走事实问工具",
            source="hard",
        )
    if is_difficulty_curve_report_query(q):
        return ReportRoute(
            needs_report=True,
            report_type=ReportType.DIFFICULTY_CURVE,
            confidence=0.95,
            reason="硬约束整卷难度曲线",
            source="hard",
        )

    bureau = _bureau_report_type(q)
    if bureau is not None:
        return ReportRoute(
            needs_report=True,
            report_type=bureau,
            confidence=0.95,
            reason="硬约束局端基础分析",
            source="hard",
        )
    if is_line_reach_report_query(q):
        return ReportRoute(
            needs_report=True,
            report_type=ReportType.LINE_REACH,
            confidence=0.95,
            reason="硬约束全市达线情况分析",
            source="hard",
        )
    if is_subject_research_report_query(q):
        return ReportRoute(
            needs_report=True,
            report_type=ReportType.SUBJECT_RESEARCH,
            confidence=0.95,
            reason="硬约束学科教研分析报告",
            source="hard",
        )
    if is_line_reach_query(q):
        return ReportRoute(
            needs_report=False,
            report_type=None,
            confidence=0.95,
            reason="达线/预测线走指标表事实查询",
            source="hard",
        )
    if is_score_threshold_fact_query(q):
        return ReportRoute(
            needs_report=False,
            report_type=None,
            confidence=0.95,
            reason="分数阈值/分段人数走 overview 事实查询",
            source="hard",
        )
    if is_school_vs_city_avg_query(q):
        return ReportRoute(
            needs_report=False,
            report_type=None,
            confidence=0.95,
            reason="学校均分与全市比较走 overview 事实查询",
            source="hard",
        )
    if is_school_vs_school_type_avg_query(q):
        return ReportRoute(
            needs_report=False,
            report_type=None,
            confidence=0.95,
            reason="学校均分与引领/支撑/发展校比较走 overview 事实查询",
            source="hard",
        )

    # 硬约束：全市/结构化/具名学生学情 → 需要报告
    if is_citywide_analysis_query(q) or is_structured_diagnostic_query(q):
        return ReportRoute(
            needs_report=True,
            report_type=ReportType.DIAGNOSTIC_REPORT,
            confidence=0.95,
            reason="硬约束全市/结构化诊断",
            source="hard",
        )
    if is_individual_student_analysis_query(q):
        return ReportRoute(
            needs_report=True,
            report_type=ReportType.STUDENT_PROFILE,
            confidence=0.95,
            reason="硬约束个体学生分析",
            source="hard",
        )
    if is_knowledge_cohort_gap_query(q):
        return ReportRoute(
            needs_report=True,
            report_type=ReportType.SUBJECT_DIAGNOSIS,
            confidence=0.95,
            reason="硬约束知识点分层对比（后十 vs 中位组）",
            source="hard",
        )

    wants_report = _has_explicit_report_intent(q) or any(
        (
            is_class_overview_query(q),
            is_school_class_comparison_query(q),
            is_group_feature_query(q),
            is_tier_alert_query(q),
            is_trend_tracking_query(q),
            is_multi_exam_class_analysis_query(q),
            is_school_exam_report_query(q),
            _is_parent_child_xueqing(q),
            any(h in q for h in _CLASS_COMPARE_HINTS),
            any(h in q for h in _EXPLICIT_GROUP_HINTS),
            ("成绩" in q and "变化" in q),
            ("成绩" in q and "趋势" in q),
            ("成绩" in q and "走势" in q),
        )
    )

    if not wants_report:
        return ReportRoute(
            needs_report=False,
            report_type=None,
            confidence=0.7,
            reason="无明确报告意图，事实查询",
            source="fallback",
        )

    pool = _candidate_pool(q)
    rt, conf, reason = _pick_report_type(q, pool)
    source = "hard" if len(pool) == 1 else "fallback"
    return ReportRoute(
        needs_report=True,
        report_type=rt,
        confidence=conf,
        reason=reason,
        source=source,
    )


def should_use_deterministic_report_plan(question: str, route: ReportRoute) -> bool:
    """是否跳过 Planner LLM，直接用 plan_items_for_route。"""
    from src.agent.education.query_parse import (
        is_citywide_analysis_query,
        is_class_overview_query,
        is_group_feature_query,
        is_individual_student_analysis_query,
        is_knowledge_cohort_gap_query,
        is_line_reach_report_query,
        is_multi_exam_class_analysis_query,
        is_school_class_comparison_query,
        is_school_exam_report_query,
        is_structured_diagnostic_query,
        is_subject_research_report_query,
        is_tier_alert_query,
        is_trend_tracking_query,
    )

    # 事实问：确定性走事实计划，避免 Planner 乱拆报告
    if not route.needs_report:
        return True
    q = (question or "").strip()
    if route.source == "hard":
        return True
    if any(
        (
            is_citywide_analysis_query(q),
            is_individual_student_analysis_query(q),
            is_knowledge_cohort_gap_query(q),
            is_multi_exam_class_analysis_query(q),
            is_tier_alert_query(q),
            is_class_overview_query(q),
            is_school_class_comparison_query(q),
            is_group_feature_query(q),
            is_school_exam_report_query(q),
            is_trend_tracking_query(q),
            is_structured_diagnostic_query(q),
            is_line_reach_report_query(q),
            is_subject_research_report_query(q),
            _is_parent_child_xueqing(q),
        )
    ):
        return True
    if route.report_type is not None and route.report_type != ReportType.CLASS_OVERVIEW:
        return True
    return False


def _build_classify_prompt(question: str, candidates: list[ReportType]) -> list[dict[str, str]]:
    lines = []
    for rt in candidates:
        label = REPORT_TYPE_LABELS.get(rt, rt.value)
        desc = _REPORT_TYPE_DEFS.get(rt, "")
        lines.append(f"- `{rt.value}`（{label}）：{desc}")
    catalog = "\n".join(lines)
    system = (
        "你是教育学情意图分类器。先判断用户是否需要生成 HTML 学情报告，再（若需要）选类型。\n"
        "只输出一个 JSON 对象，不要 Markdown：\n"
        '{"needs_report":true或false,"report_type":"<枚举或null>",'
        '"confidence":0.0到1.0,"reason":"一句话"}\n'
        "规则：\n"
        "- 事实查询（谁最高分、多少人、均分多少、排名第几、是谁、达线人数/率）→ needs_report=false，"
        "report_type=null\n"
        "- 点名学校对比引领校/支撑校/发展校的均分或单科 → needs_report=false，"
        "用 overview.xxlb，禁止 JOIN tb_school 算均分\n"
        "- 达线/预测线人数或率（未要求分析报告）→ needs_report=false\n"
        "- 全市/各区达线情况、达线分析/报告、环比 → line_reach，禁止出结构化诊断报告\n"
        "- 点了班级、具体学校、或引领校/支撑校/发展校时，达线问句 needs_report=false，"
        "不是 line_reach（全市达线报告是全体学校合计）\n"
        "- 明确要报告/总览/诊断/横向对比/群体特征/预警/学情分析报告/个人画像 → needs_report=true，"
        "并从候选中选 report_type\n"
        "- 具名学生（学号/姓名）+ 个人画像/学情/个人报告 → student_profile，"
        "不是 subject_diagnosis\n"
        "- 「班级横向对比 / 各班横向」→ grade_comparison，不是 group_feature；"
        "仅「比较分析」且对比全市时不要选 grade_comparison\n"
        "- 仅当明确「群体特征/按班级群体」时选 group_feature\n"
        "- 点名第N题/单选N/小题N 的难度曲线 → needs_report=false，"
        "不是科目诊断、不是十分段，仍调难度曲线工具\n"
        "- 整卷难度曲线/难度分析/试卷得分率分析（未点名具体题号）→ difficulty_curve 报告，"
        "不是十分段人数、不是科目诊断\n"
        "- 「各校/各学校/各高中」+考试分析且未点名班级 → diagnostic_report，"
        "禁止 comprehensive，禁止把「高三1月」理解成「高三(1)班」\n"
        "- 拿不准时 needs_report=false（宁可只回答，不强行出报告）\n"
    )
    user = f"候选报告类型（仅 needs_report=true 时选用）：\n{catalog}\n\n用户问题：{question}"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _coerce_bool(raw: Any, default: bool = False) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return bool(raw)
    s = str(raw or "").strip().lower()
    if s in {"true", "1", "yes", "y", "是"}:
        return True
    if s in {"false", "0", "no", "n", "否", "null", "none", ""}:
        return False
    return default


def _parse_llm_route(raw: str, candidates: list[ReportType]) -> ReportRoute | None:
    parsed = parse_json_tolerant(raw)
    if not isinstance(parsed, dict):
        return None

    needs = _coerce_bool(parsed.get("needs_report"), default=False)
    try:
        conf = float(parsed.get("confidence") or 0.7)
    except (TypeError, ValueError):
        conf = 0.7
    conf = max(0.0, min(1.0, conf))
    reason = str(parsed.get("reason") or "LLM 分类").strip()[:200]

    if not needs:
        return ReportRoute(
            needs_report=False,
            report_type=None,
            confidence=conf,
            reason=reason,
            source="llm",
        )

    rt_raw = str(parsed.get("report_type") or "").strip()
    if not rt_raw or rt_raw.lower() in {"null", "none"}:
        return None
    try:
        rt = ReportType(rt_raw)
    except ValueError:
        for k, v in REPORT_TYPE_LABELS.items():
            if rt_raw == v or rt_raw == k.value:
                rt = k
                break
        else:
            return None
    if rt not in candidates:
        return None
    return ReportRoute(
        needs_report=True,
        report_type=rt,
        confidence=conf,
        reason=reason,
        source="llm",
    )


async def classify_report_intent(
    question: str,
    llm_client: Any | None = None,
) -> ReportRoute:
    """异步分类：优先 LLM，失败则规则兜底。"""
    from src.agent.education.query_parse import (
        is_citywide_analysis_query,
        is_difficulty_curve_report_query,
        is_individual_student_analysis_query,
        is_item_difficulty_curve_query,
        is_knowledge_cohort_gap_query,
        is_line_reach_query,
        is_line_reach_report_query,
        is_school_vs_city_avg_query,
        is_school_vs_school_type_avg_query,
        is_score_threshold_fact_query,
        is_structured_diagnostic_query,
        is_subject_research_report_query,
    )

    q = (question or "").strip()
    if is_item_difficulty_curve_query(q):
        return ReportRoute(
            needs_report=False,
            report_type=None,
            confidence=0.95,
            reason="单题难度曲线走事实问工具",
            source="hard",
        )
    if is_difficulty_curve_report_query(q):
        return ReportRoute(
            needs_report=True,
            report_type=ReportType.DIFFICULTY_CURVE,
            confidence=0.95,
            reason="硬约束整卷难度曲线",
            source="hard",
        )
    bureau = _bureau_report_type(q)
    if bureau is not None:
        return ReportRoute(
            needs_report=True,
            report_type=bureau,
            confidence=0.95,
            reason="硬约束局端基础分析",
            source="hard",
        )
    if is_line_reach_report_query(q):
        return ReportRoute(
            needs_report=True,
            report_type=ReportType.LINE_REACH,
            confidence=0.95,
            reason="硬约束全市达线情况分析",
            source="hard",
        )
    if is_subject_research_report_query(q):
        return ReportRoute(
            needs_report=True,
            report_type=ReportType.SUBJECT_RESEARCH,
            confidence=0.95,
            reason="硬约束学科教研分析报告",
            source="hard",
        )
    if is_line_reach_query(q):
        return ReportRoute(
            needs_report=False,
            report_type=None,
            confidence=0.95,
            reason="达线/预测线走指标表事实查询",
            source="hard",
        )
    if is_score_threshold_fact_query(q):
        return ReportRoute(
            needs_report=False,
            report_type=None,
            confidence=0.95,
            reason="分数阈值/分段人数走 overview 事实查询",
            source="hard",
        )
    if is_school_vs_city_avg_query(q):
        return ReportRoute(
            needs_report=False,
            report_type=None,
            confidence=0.95,
            reason="学校均分与全市比较走 overview 事实查询",
            source="hard",
        )
    if is_school_vs_school_type_avg_query(q):
        return ReportRoute(
            needs_report=False,
            report_type=None,
            confidence=0.95,
            reason="学校均分与引领/支撑/发展校比较走 overview 事实查询",
            source="hard",
        )
    if is_knowledge_cohort_gap_query(q):
        return ReportRoute(
            needs_report=True,
            report_type=ReportType.SUBJECT_DIAGNOSIS,
            confidence=0.95,
            reason="硬约束知识点分层对比（后十 vs 中位组）",
            source="hard",
        )

    pool = _candidate_pool(q)

    # 硬约束单候选且问题带报告意图 → 直接报告
    if len(pool) == 1 and (
        _has_explicit_report_intent(q)
        or pool[0]
        in {
            ReportType.DIAGNOSTIC_REPORT,
            ReportType.STUDENT_PROFILE,
            ReportType.LINE_REACH,
            ReportType.SUBJECT_RESEARCH,
        }
    ):
        if (
            is_citywide_analysis_query(q)
            or is_structured_diagnostic_query(q)
            or is_individual_student_analysis_query(q)
            or is_line_reach_report_query(q)
            or is_subject_research_report_query(q)
        ):
            return ReportRoute(
                needs_report=True,
                report_type=pool[0],
                confidence=0.95,
                reason="硬约束唯一候选",
                source="hard",
            )

    if llm_client is not None and hasattr(llm_client, "chat"):
        try:
            messages = _build_classify_prompt(q, pool)
            raw = await llm_client.chat(messages)
            route = _parse_llm_route(str(raw or ""), pool)
            if route is not None:
                return route
            logger.warning("intent LLM returned invalid payload; fallback")
        except Exception as e:  # noqa: BLE001
            logger.warning("intent LLM classify failed: %s", e)

    return fallback_classify_report_intent(q)


def classify_report_intent_sync(question: str) -> ReportRoute:
    """同步入口（无 LLM）：规则兜底。"""
    return fallback_classify_report_intent(question)


EXPECTED_PLAN_TOOLS: dict[ReportType, frozenset[str]] = {
    ReportType.CLASS_OVERVIEW: frozenset({"build_class_overview_report_data_tool"}),
    ReportType.GRADE_COMPARISON: frozenset(
        {"build_subject_diagnosis_sections_tool", "禁止传 class_name"}
    ),
    ReportType.SUBJECT_DIAGNOSIS: frozenset(
        {
            "build_subject_diagnosis_sections_tool",
            "fetch_subject_diagnosis_data_tool",
            "education/subject_diagnosis.html",
        }
    ),
    ReportType.STUDENT_PROFILE: frozenset(
        {
            "build_student_subject_diagnosis_tool",
            "build_student_exam_report_data_tool",
        }
    ),
    ReportType.TREND_TRACKING: frozenset({"build_trend_tracking_report_data_tool"}),
    ReportType.TIER_ALERT: frozenset({"build_tier_alert_report_data_tool"}),
    ReportType.GROUP_FEATURE: frozenset({"build_group_feature_report_data_tool"}),
    ReportType.COMPREHENSIVE: frozenset({"build_comprehensive_report_data_tool"}),
    ReportType.DIAGNOSTIC_REPORT: frozenset({"build_diagnostic_report_data_tool"}),
    ReportType.LINE_REACH: frozenset({"build_line_reach_report_data_tool"}),
    ReportType.SUBJECT_RESEARCH: frozenset({"build_subject_research_report_data_tool"}),
    ReportType.SUBJECT_AVG: frozenset({"build_subject_avg_report_data_tool"}),
    ReportType.ASSIGN_GRADE: frozenset({"build_assign_grade_report_data_tool"}),
    ReportType.RANK_BUCKET: frozenset({"build_rank_bucket_report_data_tool"}),
    ReportType.CONTRIBUTION: frozenset({"build_contribution_report_data_tool"}),
    ReportType.COMBO_REACH: frozenset({"build_combo_reach_report_data_tool"}),
    ReportType.ELITE_ROSTER: frozenset({"build_elite_roster_report_data_tool"}),
    ReportType.SCORE_BAND: frozenset({"build_score_band_report_data_tool"}),
}


def plan_items_for_route(route: ReportRoute, question: str) -> list[dict[str, str]]:
    """按路由生成计划：无报告 → 事实查询；有报告 → 按类型 builder。"""
    from src.agent.education.query_parse import is_knowledge_cohort_gap_query
    from src.agent.expand.planner import build_knowledge_cohort_plan_items

    if is_knowledge_cohort_gap_query(question or ""):
        return build_knowledge_cohort_plan_items(question)
    if not route.needs_report:
        from src.agent.expand.planner import build_fact_query_plan_items

        return build_fact_query_plan_items(question)
    rt = route.report_type or ReportType.CLASS_OVERVIEW
    return plan_items_for_report_type(rt, question)


def plan_items_for_report_type(
    report_type: ReportType | str | None,
    question: str,
) -> list[dict[str, str]]:
    """按报告类型生成确定性 Team 计划（调用方须已确认 needs_report）。"""
    from src.agent.education.query_parse import (
        is_citywide_analysis_query,
        is_knowledge_cohort_gap_query,
    )
    from src.agent.expand.planner import (
        build_citywide_team_plan_items,
        build_class_overview_plan_items,
        build_comprehensive_class_plan_items,
        build_fact_query_plan_items,
        build_group_feature_plan_items,
        build_individual_student_exam_plan_items,
        build_knowledge_cohort_plan_items,
        build_line_reach_plan_items,
        build_school_class_comparison_plan_items,
        build_school_subject_report_plan_items,
        build_subject_research_plan_items,
        build_tier_alert_plan_items,
        build_trend_tracking_plan_items,
    )

    q = question or ""
    if is_knowledge_cohort_gap_query(q):
        return build_knowledge_cohort_plan_items(q)
    if report_type is None:
        return build_fact_query_plan_items(q)

    if isinstance(report_type, str):
        try:
            rt = ReportType(report_type)
        except ValueError:
            rt = ReportType.CLASS_OVERVIEW
    else:
        rt = report_type

    if rt == ReportType.CLASS_OVERVIEW:
        return build_class_overview_plan_items(q)
    if rt == ReportType.GRADE_COMPARISON:
        return build_school_class_comparison_plan_items(q)
    if rt == ReportType.SUBJECT_DIAGNOSIS:
        return build_school_subject_report_plan_items(q)
    if rt == ReportType.STUDENT_PROFILE:
        return build_individual_student_exam_plan_items(q)
    if rt == ReportType.TREND_TRACKING:
        return build_trend_tracking_plan_items(q)
    if rt == ReportType.TIER_ALERT:
        return build_tier_alert_plan_items(q)
    if rt == ReportType.GROUP_FEATURE:
        return build_group_feature_plan_items(q)
    if rt == ReportType.COMPREHENSIVE:
        return build_comprehensive_class_plan_items(q)
    if rt == ReportType.DIAGNOSTIC_REPORT:
        if is_citywide_analysis_query(q):
            return build_citywide_team_plan_items(q)
        return build_school_subject_report_plan_items(q)
    if rt == ReportType.LINE_REACH:
        return build_line_reach_plan_items(q)
    if rt == ReportType.DIFFICULTY_CURVE:
        from src.agent.expand.planner import build_difficulty_curve_plan_items

        return build_difficulty_curve_plan_items(q)
    if rt == ReportType.SUBJECT_RESEARCH:
        return build_subject_research_plan_items(q)
    tool_by_rt = {
        ReportType.SUBJECT_AVG: "build_subject_avg_report_data_tool",
        ReportType.ASSIGN_GRADE: "build_assign_grade_report_data_tool",
        ReportType.RANK_BUCKET: "build_rank_bucket_report_data_tool",
        ReportType.CONTRIBUTION: "build_contribution_report_data_tool",
        ReportType.COMBO_REACH: "build_combo_reach_report_data_tool",
        ReportType.ELITE_ROSTER: "build_elite_roster_report_data_tool",
        ReportType.SCORE_BAND: "build_score_band_report_data_tool",
    }
    tool = tool_by_rt.get(rt)
    if tool:
        from src.agent.expand.planner import build_bureau_plan_items

        return build_bureau_plan_items(tool, q)
    return build_fact_query_plan_items(q)


def plan_matches_report_type(
    plan_items: list[dict[str, str]] | None,
    report_type: ReportType | None,
) -> bool:
    """当前计划是否已包含该报告类型的关键工具。"""
    blob = " ".join(str(it.get("sub_task") or "") for it in (plan_items or []))
    # 知识点分层对比优先：计划已含专用工具即视为匹配
    if "compare_knowledge_cohort_tool" in blob:
        return True
    if report_type is None:
        return "build_" not in blob or "禁止" in blob
    expected = EXPECTED_PLAN_TOOLS.get(report_type)
    if not expected:
        return True
    if report_type == ReportType.GRADE_COMPARISON:
        if "class_name=" in blob and "禁止传 class_name" not in blob:
            return False
        return all(tok in blob for tok in expected)
    if report_type == ReportType.SUBJECT_DIAGNOSIS:
        if "禁止传 class_name" in blob:
            return False
        return any(tok in blob for tok in expected)
    return any(tok in blob for tok in expected)


def plan_is_fact_query(plan_items: list[dict[str, str]] | None) -> bool:
    """计划是否为无报告工具的事实查询。"""
    blob = " ".join(str(it.get("sub_task") or "") for it in (plan_items or []))
    if any(
        tok in blob
        for tok in (
            "build_class_overview_report_data_tool",
            "build_subject_diagnosis_sections_tool",
            "build_group_feature_report_data_tool",
            "build_student_subject_diagnosis_tool",
            "build_student_exam_report_data_tool",
            "build_tier_alert_report_data_tool",
            "build_comprehensive_report_data_tool",
            "build_diagnostic_report_data_tool",
            "build_trend_tracking_report_data_tool",
            "build_line_reach_report_data_tool",
            "build_subject_research_report_data_tool",
            "build_subject_avg_report_data_tool",
            "build_assign_grade_report_data_tool",
            "build_rank_bucket_report_data_tool",
            "build_contribution_report_data_tool",
            "build_combo_reach_report_data_tool",
            "build_elite_roster_report_data_tool",
            "build_score_band_report_data_tool",
            "compare_knowledge_cohort_tool",
            "render_html_report",
        )
    ):
        return False
    return True


def coerce_plan_to_route(
    question: str,
    plan_items: list[dict[str, str]],
    route: ReportRoute,
) -> list[dict[str, str]]:
    """若计划与路由不一致，替换为对应确定性计划。"""
    from src.agent.education.query_parse import is_knowledge_cohort_gap_query

    q = (question or "").strip()
    if is_knowledge_cohort_gap_query(q):
        blob = " ".join(str(it.get("sub_task") or "") for it in (plan_items or []))
        if "compare_knowledge_cohort_tool" in blob:
            return plan_items
        logger.info("intent coerce: knowledge_cohort_gap → compare_knowledge_cohort_tool plan")
        return plan_items_for_route(route, question)

    if not route.needs_report:
        from src.agent.education.query_parse import is_item_difficulty_curve_query

        if is_item_difficulty_curve_query(q):
            blob = " ".join(str(it.get("sub_task") or "") for it in (plan_items or []))
            if "build_difficulty_curve_report_data_tool" in blob:
                return plan_items
            logger.info("intent coerce: item difficulty curve → fact tool plan")
            return plan_items_for_route(route, question)
        if plan_is_fact_query(plan_items):
            return plan_items
        logger.info("intent coerce: needs_report=false → fact query plan")
        return plan_items_for_route(route, question)

    if route.report_type and plan_matches_report_type(plan_items, route.report_type):
        return plan_items
    logger.info(
        "intent coerce: plan mismatch for %s (%s) → rebuild",
        route.report_type.value if route.report_type else "none",
        route.source,
    )
    return plan_items_for_route(route, question)


__all__ = [
    "EXPECTED_PLAN_TOOLS",
    "ReportRoute",
    "classify_report_intent",
    "classify_report_intent_sync",
    "coerce_plan_to_route",
    "fallback_classify_report_intent",
    "plan_is_fact_query",
    "plan_items_for_report_type",
    "plan_items_for_route",
    "plan_matches_report_type",
    "should_use_deterministic_report_plan",
]
