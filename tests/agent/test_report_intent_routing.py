"""意图识别 ↔ 确定性格径对齐回归。

保证 ReportIntentResolver 与 intent_router / Planner dispatch 同源，
避免「认出了 A 报告、计划却走 B」或关键词误伤。
"""

from __future__ import annotations

import pytest

from src.agent.education.intent_router import classify_report_intent_sync
from src.agent.education.orchestrator import ReportIntentResolver
from src.agent.education.query_parse import (
    extract_exam_name_hint,
    extract_student_target,
    is_citywide_analysis_query,
    is_individual_student_analysis_query,
    is_line_reach_query,
    is_line_reach_report_query,
    is_school_class_comparison_query,
    is_school_exam_report_query,
)
from src.agent.education.report_types import ReportType


@pytest.mark.parametrize(
    "question,report_type",
    [
        (
            "扬州中学高三(11)班所有数学考试成绩分析",
            ReportType.COMPREHENSIVE,
        ),
        (
            "扬州中学在连淮扬镇数学考试中各个班级的横向多维对比分析",
            ReportType.GRADE_COMPARISON,
        ),
        (
            "帮我分析全市的江苏省高一上学期数学期末质量检测成绩，形成详细报告",
            ReportType.DIAGNOSTIC_REPORT,
        ),
        (
            "分析学生001这几次考试的成绩",
            ReportType.STUDENT_PROFILE,
        ),
        (
            "查询学生编号为：STU20240003，江苏省高一上学期数学期末质量检测成绩分析，哪些知识点需要加强",
            ReportType.STUDENT_PROFILE,
        ),
        (
            "扬州中学高三(11)班数学成绩详细分析",
            ReportType.SUBJECT_DIAGNOSIS,
        ),
        (
            "分析扬州中学在连淮扬镇考试的数学成绩，多维分析形成报告",
            ReportType.SUBJECT_DIAGNOSIS,
        ),
        (
            "初三1班三次考试综合分析报告",
            ReportType.COMPREHENSIVE,
        ),
        (
            "高三(10)班班级成绩总览",
            ReportType.CLASS_OVERVIEW,
        ),
        (
            "扬州中学高三(10)班连淮扬镇数学成绩总览",
            ReportType.CLASS_OVERVIEW,
        ),
        (
            "临界生预警报告",
            ReportType.TIER_ALERT,
        ),
        (
            "扬州中学高三(10)班数学临界生预警报告",
            ReportType.TIER_ALERT,
        ),
        (
            "扬州中学连淮扬镇数学考试按班级群体对比特征",
            ReportType.GROUP_FEATURE,
        ),
        (
            "历次成绩趋势分析",
            ReportType.TREND_TRACKING,
        ),
        (
            "扬州中学高三(9)班数学诊断报告",
            ReportType.SUBJECT_DIAGNOSIS,
        ),
        (
            "南京市第一中学数学结构化诊断报告",
            ReportType.DIAGNOSTIC_REPORT,
        ),
        (
            "帮我分析全市数学详细分析",
            ReportType.DIAGNOSTIC_REPORT,
        ),
        (
            "2026届高三1月各校考试分析",
            ReportType.DIAGNOSTIC_REPORT,
        ),
        (
            "扬州中学数学详细分析",
            ReportType.SUBJECT_DIAGNOSIS,
        ),
    ],
)
def test_report_resolver_matches_classifier(question, report_type):
    spec = ReportIntentResolver().resolve(question)
    assert spec.report_type == report_type
    sync = classify_report_intent_sync(question)
    assert sync.needs_report is True
    assert sync.report_type == report_type


def test_bare_xueqing_report_not_student_profile():
    spec = ReportIntentResolver().resolve("学情报告")
    assert spec.report_type != ReportType.STUDENT_PROFILE


def test_parent_child_xueqing_is_student_profile():
    spec = ReportIntentResolver().resolve("给家长看看孩子的学情报告")
    assert spec.report_type == ReportType.STUDENT_PROFILE


def test_negated_geban_not_class_comparison():
    q = "不要看各班级，只要扬州中学全校数学报告"
    assert is_school_class_comparison_query(q) is False
    assert is_school_exam_report_query(q) is True
    assert ReportIntentResolver().resolve(q).report_type == ReportType.SUBJECT_DIAGNOSIS


def test_exam_hint_not_polluted_by_ban_prefix():
    assert extract_exam_name_hint("扬州中学高三(11)班所有数学考试成绩分析") is None
    assert extract_exam_name_hint("扬州中学在连淮扬镇数学考试中各个班级对比") == "连淮扬镇"
    # 「班的XXX考试」：任意考试简称都应抽出，禁止因吃到「班」前缀而丢弃
    assert extract_exam_name_hint("某校高三（10）班的宁镇扬联考班级分析") == "宁镇扬联考"
    assert extract_exam_name_hint("某校高三(10)班苏北调研数学考试详细分析") == "苏北调研"
    assert extract_exam_name_hint("扬州中学高三（10）班的连淮扬镇考试班级分析") == "连淮扬镇"
    # 「班+考试简称+科目+总览」可无「考试」二字
    assert extract_exam_name_hint("扬州中学高三(10)班连淮扬镇数学成绩总览") == "连淮扬镇"
    assert extract_exam_name_hint("某校高三(8)班宁镇扬联考英语成绩概览") == "宁镇扬联考"


def test_chinese_student_name_extracted():
    assert extract_student_target("分析学生张三学情报告") == "学生张三"
    assert is_individual_student_analysis_query("分析学生张三学情报告") is True
    assert ReportIntentResolver().resolve("分析学生张三学情报告").report_type == (
        ReportType.STUDENT_PROFILE
    )


def test_grade_comparison_filters_omit_class_name():
    q = "扬州中学在连淮扬镇数学考试中各个班级的横向多维对比分析"
    spec = ReportIntentResolver().resolve(q)
    assert "class_name" not in spec.filters
    assert spec.filters.get("school_name") == "扬州中学"


def test_normalize_fullwidth_parentheses_for_class():
    from src.agent.education.orchestrator import _extract_class_name
    from src.agent.education.query_parse import normalize_fullwidth_parentheses
    from src.chat.schemas import ChatRequest

    q = "扬州中学高三（10）班在连淮扬镇的数学考试考情分析"
    assert normalize_fullwidth_parentheses(q) == (
        "扬州中学高三(10)班在连淮扬镇的数学考试考情分析"
    )
    assert _extract_class_name(q) == "高三(10)班"
    req = ChatRequest(question=q, datasource_id=1)
    assert req.question == "扬州中学高三(10)班在连淮扬镇的数学考试考情分析"
    assert "（" not in req.question


# ---- FakeLlm 意图路由 ---------------------------------------------------- #


class _FakeLlm:
    def __init__(self, payload: str | Exception):
        self._payload = payload

    async def chat(self, messages: list[dict[str, str]]) -> str:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def _run(coro):
    import asyncio

    return asyncio.run(coro)


def test_citywide_district_compare_line_reach_is_report():
    q = "各区特控线达线分析"
    assert is_line_reach_report_query(q) is True
    route = classify_report_intent_sync(q)
    assert route.needs_report is True
    assert route.report_type == ReportType.LINE_REACH


def test_narrow_line_reach_count_is_fact():
    q = "邗江区物理类本科线达线人数"
    assert is_line_reach_query(q) is True
    assert is_line_reach_report_query(q) is False
    route = classify_report_intent_sync(q)
    assert route.needs_report is False
    assert route.report_type is None


def test_district_after_month_not_swallowed_by_yue():
    """「3月广陵区」不得抽成「月广陵区」。"""
    from src.agent.education.query_parse import extract_district_target

    q = "扬州市2026届高三3月广陵区本科线达线人数和达线率"
    assert extract_district_target(q) == "广陵区"
    assert extract_district_target("邗江区物理类本科线达线人数") == "邗江区"
    assert extract_district_target("扬州市2026届高三3月广陵本科线达线") == "广陵区"


def test_gexiao_avg_compare_without_fenxi_is_not_citywide_diagnostic():
    """「各校平均分横向对比」没有「分析」，不抢全市诊断。"""
    q = "扬州各高中高三3月数学考试各校平均分横向对比"
    assert is_citywide_analysis_query(q) is False


def test_named_district_line_reach_skips_class_school():
    """区县达线率是完整范围：指定考试后不再追问班/校；「地区」不误抽。"""
    from src.agent.education.clarification import (
        SLOT_CLASS,
        SLOT_EXAM,
        SLOT_SCHOOL,
        candidate_missing_slots,
        extract_filled_slots,
    )
    from src.agent.education.intent_router import classify_report_intent_sync
    from src.agent.education.query_parse import (
        extract_district_target,
        is_line_reach_report_query,
        is_named_district_scope,
    )

    assert extract_district_target("宝应地区本科达线率") == "宝应"
    assert extract_district_target("2026届高三1月期末宝应地区本科达线率") == "宝应"
    assert extract_district_target("邗江区本科达线率") == "邗江区"
    assert extract_district_target("宝应县本科达线率") == "宝应县"
    assert extract_district_target("宝应本科达线率") == "宝应区"
    assert extract_district_target("物理类本科达线率") is None
    assert extract_district_target("全市2026届高三1月期末本科达线率") is None
    assert is_named_district_scope("宝应地区本科达线率") is True
    assert is_line_reach_report_query("宝应地区本科达线率") is False

    bureau = {"edu_role": "bureau_admin"}
    q_exam = "宝应地区2026届高三1月期末本科达线率"
    route = classify_report_intent_sync(q_exam)
    assert route.needs_report is False
    filled = extract_filled_slots(q_exam, bureau)
    miss = candidate_missing_slots(route, q_exam, filled, bureau)
    assert SLOT_CLASS not in miss
    assert SLOT_SCHOOL not in miss
    assert SLOT_EXAM not in miss

    q_no_exam = "邗江区本科达线率"
    filled2 = extract_filled_slots(q_no_exam, bureau)
    miss2 = candidate_missing_slots(
        classify_report_intent_sync(q_no_exam), q_no_exam, filled2, bureau
    )
    assert miss2 == [SLOT_EXAM]

    rank_q = "邗江区2026届高三1月期末排名"
    miss_rank = candidate_missing_slots(
        classify_report_intent_sync(rank_q),
        rank_q,
        extract_filled_slots(rank_q, bureau),
        bureau,
    )
    assert SLOT_CLASS in miss_rank or SLOT_SCHOOL in miss_rank


def test_class_overall_analysis_is_school_exam_report_query():
    q = "扬州中学高三(10)班 · 连淮扬镇数学考试 · 班级整体分析"
    assert is_school_exam_report_query(q) is True
