"""按现有教育报表规则选择报告类型。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from src.agent.education.report_types import ReportType

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


def _is_score_stat_fact(question: str) -> bool:
    """单场均分/均衡等事实问，不能因考试名含「期末」被收成全市诊断报告。"""
    from src.agent.education.query_parse import is_score_stat_query

    q = question or ""
    if not is_score_stat_query(q):
        return False
    return not _has_explicit_report_intent(q)


def _is_rank_fact(question: str) -> bool:
    """单场排名/第几/哪个班第一，未点名报告时走事实查询。"""
    from src.agent.education.query_parse import is_rank_query

    q = question or ""
    if not is_rank_query(q):
        return False
    return not _has_explicit_report_intent(q)


def _is_contribution_weak_fact(question: str) -> bool:
    from src.agent.education.query_parse import is_contribution_weak_query

    return is_contribution_weak_query(question or "")


def _is_subject_strength_fact(question: str) -> bool:
    """优势/薄弱学科按各科均分全市排名，不是本校各科互比、不是班际报告。"""
    from src.agent.education.query_parse import is_subject_strength_query

    return is_subject_strength_query(question or "")


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
    if _is_contribution_weak_fact(q):
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
    if _is_score_stat_fact(q):
        return []
    if _is_subject_strength_fact(q):
        return []
    if _is_rank_fact(q):
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
        ReportType.LINE_SHIFT,
        ReportType.ABSENCE,
        ReportType.CLASS_LINE_REACH,
        ReportType.ELITE_RANK_SHIFT,
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
        is_class_weak_subject_query,
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

    if _is_contribution_weak_fact(q):
        return ReportRoute(
            needs_report=False,
            report_type=None,
            confidence=0.95,
            reason="贡献分薄弱学科按差值口径事实查询",
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
    if _is_score_stat_fact(q):
        return ReportRoute(
            needs_report=False,
            report_type=None,
            confidence=0.95,
            reason="单场均分/均衡等走 overview 事实查询",
            source="hard",
        )
    if _is_subject_strength_fact(q):
        return ReportRoute(
            needs_report=False,
            report_type=None,
            confidence=0.95,
            reason="优势/薄弱学科按各科均分全市排名",
            source="hard",
        )
    if _is_rank_fact(q):
        return ReportRoute(
            needs_report=False,
            report_type=None,
            confidence=0.95,
            reason="单场排名/第几走事实查询",
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
    if is_class_weak_subject_query(q):
        return ReportRoute(
            needs_report=True,
            report_type=ReportType.SUBJECT_DIAGNOSIS,
            confidence=0.95,
            reason="硬约束班级薄弱学科（同校同科班际对比）",
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


def _hard_route(question: str) -> ReportRoute | None:
    """硬规则短路：命中返回确定路由，未命中返回 ``None`` 交给 LLM。

    抽出来是为了让槽位抽取能拿到"路由已定"这个事实——DB-GPT 的 Intent 检测
    不因为分类走了捷径就跳过抽槽，硬路由只作为 hint 注入。
    """
    from src.agent.education.query_parse import (
        is_citywide_analysis_query,
        is_class_weak_subject_query,
        is_difficulty_curve_report_query,
        is_individual_student_analysis_query,
        is_item_difficulty_curve_query,
        is_knowledge_cohort_gap_query,
        is_line_reach_query,
        is_line_reach_report_query,
        is_school_class_comparison_query,
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
    if _is_contribution_weak_fact(q):
        return ReportRoute(
            needs_report=False,
            report_type=None,
            confidence=0.95,
            reason="贡献分薄弱学科按差值口径事实查询",
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
    if _is_score_stat_fact(q):
        return ReportRoute(
            needs_report=False,
            report_type=None,
            confidence=0.95,
            reason="单场均分/均衡等走 overview 事实查询",
            source="hard",
        )
    if _is_subject_strength_fact(q):
        return ReportRoute(
            needs_report=False,
            report_type=None,
            confidence=0.95,
            reason="优势/薄弱学科按各科均分全市排名",
            source="hard",
        )
    if _is_rank_fact(q):
        return ReportRoute(
            needs_report=False,
            report_type=None,
            confidence=0.95,
            reason="单场排名/第几走事实查询",
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
    if is_class_weak_subject_query(q):
        return ReportRoute(
            needs_report=True,
            report_type=ReportType.SUBJECT_DIAGNOSIS,
            confidence=0.95,
            reason="硬约束班级薄弱学科（同校同科班际对比）",
            source="hard",
        )
    if is_school_class_comparison_query(q):
        return ReportRoute(
            needs_report=True,
            report_type=ReportType.GRADE_COMPARISON,
            confidence=0.95,
            reason="硬约束班级横向对比",
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

    return None


def classify_report_intent_sync(question: str) -> ReportRoute:
    """同步入口（无 LLM）：规则兜底。"""
    return fallback_classify_report_intent(question)
