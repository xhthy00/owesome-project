"""意图识别 ↔ 确定性格径对齐回归。

保证 ReportIntentResolver 与 intent_router / Planner dispatch 同源，
避免「认出了 A 报告、计划却走 B」或关键词误伤。
"""

from __future__ import annotations

import pytest

from src.agent.education.intent_router import (
    classify_report_intent,
    classify_report_intent_sync,
    should_use_deterministic_report_plan,
)
from src.agent.education.orchestrator import ReportIntentResolver
from src.agent.education.query_parse import (
    extract_exam_name_hint,
    extract_school_target,
    extract_student_target,
    is_citywide_analysis_query,
    is_class_overview_query,
    is_group_feature_query,
    is_individual_student_analysis_query,
    is_school_class_comparison_query,
    is_school_exam_report_query,
    is_tier_alert_query,
)
from src.agent.education.report_types import ReportType
from src.agent.expand.planner import (
    build_class_overview_plan_items,
    build_comprehensive_class_plan_items,
    build_group_feature_plan_items,
    build_school_class_comparison_plan_items,
    build_school_subject_report_plan_items,
    build_tier_alert_plan_items,
    coerce_plan_items_if_needed,
)


def _route(question: str) -> str:
    """与 agent_runner 对齐：确定性 dispatch 标签，否则 llm。"""
    route = classify_report_intent_sync(question)
    if not route.needs_report:
        return "fact"
    if not should_use_deterministic_report_plan(question, route):
        return "llm"
    mapping = {
        ReportType.DIAGNOSTIC_REPORT: "citywide"
        if is_citywide_analysis_query(question)
        else "diagnostic",
        ReportType.STUDENT_PROFILE: "individual",
        ReportType.COMPREHENSIVE: "multi-exam",
        ReportType.TIER_ALERT: "tier-alert",
        ReportType.GRADE_COMPARISON: "class-comparison",
        ReportType.GROUP_FEATURE: "group-feature",
        ReportType.CLASS_OVERVIEW: "class-overview",
        ReportType.SUBJECT_DIAGNOSIS: "school-exam",
        ReportType.TREND_TRACKING: "trend-tracking",
    }
    return mapping.get(route.report_type, "llm")


@pytest.mark.parametrize(
    "question,report_type,route",
    [
        (
            "扬州中学高三(11)班所有数学考试成绩分析",
            ReportType.COMPREHENSIVE,
            "multi-exam",
        ),
        (
            "扬州中学在连淮扬镇数学考试中各个班级的横向多维对比分析",
            ReportType.GRADE_COMPARISON,
            "class-comparison",
        ),
        (
            "帮我分析全市的江苏省高一上学期数学期末质量检测成绩，形成详细报告",
            ReportType.DIAGNOSTIC_REPORT,
            "citywide",
        ),
        (
            "分析学生001这几次考试的成绩",
            ReportType.STUDENT_PROFILE,
            "individual",
        ),
        (
            "查询学生编号为：STU20240003，江苏省高一上学期数学期末质量检测成绩分析，哪些知识点需要加强",
            ReportType.STUDENT_PROFILE,
            "individual",
        ),
        (
            "扬州中学高三(11)班数学成绩详细分析",
            ReportType.SUBJECT_DIAGNOSIS,
            "school-exam",
        ),
        (
            "分析扬州中学在连淮扬镇考试的数学成绩，多维分析形成报告",
            ReportType.SUBJECT_DIAGNOSIS,
            "school-exam",
        ),
        (
            "初三1班三次考试综合分析报告",
            ReportType.COMPREHENSIVE,
            "multi-exam",
        ),
        (
            "高三(10)班班级成绩总览",
            ReportType.CLASS_OVERVIEW,
            "class-overview",
        ),
        (
            "扬州中学高三(10)班连淮扬镇数学成绩总览",
            ReportType.CLASS_OVERVIEW,
            "class-overview",
        ),
        (
            "临界生预警报告",
            ReportType.TIER_ALERT,
            "tier-alert",
        ),
        (
            "扬州中学高三(10)班数学临界生预警报告",
            ReportType.TIER_ALERT,
            "tier-alert",
        ),
        (
            "扬州中学连淮扬镇数学考试按班级群体对比特征",
            ReportType.GROUP_FEATURE,
            "group-feature",
        ),
        (
            "历次成绩趋势分析",
            ReportType.TREND_TRACKING,
            "trend-tracking",
        ),
        (
            "扬州中学高三(9)班数学诊断报告",
            ReportType.SUBJECT_DIAGNOSIS,
            "school-exam",
        ),
        (
            "南京市第一中学数学结构化诊断报告",
            ReportType.DIAGNOSTIC_REPORT,
            "diagnostic",
        ),
        (
            "帮我分析全市数学详细分析",
            ReportType.DIAGNOSTIC_REPORT,
            "citywide",
        ),
        (
            "扬州中学数学详细分析",
            ReportType.SUBJECT_DIAGNOSIS,
            "school-exam",
        ),
    ],
)
def test_intent_aligned_with_plan_route(question, report_type, route):
    spec = ReportIntentResolver().resolve(question)
    assert spec.report_type == report_type
    assert _route(question) == route
    sync = classify_report_intent_sync(question)
    assert sync.needs_report is True
    assert sync.report_type == report_type


def test_bare_xueqing_report_not_student_profile():
    spec = ReportIntentResolver().resolve("学情报告")
    assert spec.report_type != ReportType.STUDENT_PROFILE
    assert _route("学情报告") == "llm"


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


def test_planner_uses_extracted_exam_not_placeholder():
    from src.agent.expand.planner import build_school_subject_report_plan_items

    q = "某中学高三（8）班的宁镇扬联考班级分析"
    plans = build_school_subject_report_plan_items(q)
    blob = " ".join(p["sub_task"] for p in plans)
    assert "宁镇扬联考" in blob
    assert "exam_name=宁镇扬联考" in blob
    assert "exam_name=本次考试" not in blob
    assert "【本次考试】" not in blob


def test_chinese_student_name_extracted():
    assert extract_student_target("分析学生张三学情报告") == "学生张三"
    assert is_individual_student_analysis_query("分析学生张三学情报告") is True
    assert ReportIntentResolver().resolve("分析学生张三学情报告").report_type == (
        ReportType.STUDENT_PROFILE
    )


def test_school_class_comparison_plan_forbids_class_name():
    q = "扬州中学在连淮扬镇数学考试中各个班级的横向多维对比分析"
    plans = build_school_class_comparison_plan_items(q)
    blob = " ".join(p["sub_task"] for p in plans)
    assert "禁止传 class_name" in blob
    assert "class_name=高三" not in blob
    assert extract_school_target(q) == "扬州中学"


def test_named_class_school_exam_plan_includes_class_name():
    q = "扬州中学高三(11)班数学成绩详细分析"
    plans = build_school_subject_report_plan_items(q)
    blob = " ".join(p["sub_task"] for p in plans)
    assert "class_name=高三(11)班" in blob
    assert "禁止传 class_name" not in blob


def test_multi_exam_plan_tool():
    q = "扬州中学高三(11)班所有数学考试成绩分析"
    plans = build_comprehensive_class_plan_items(q)
    assert len(plans) == 2
    assert "build_comprehensive_report_data_tool" in plans[1]["sub_task"]


def test_tier_alert_plan_tool():
    q = "扬州中学高三(10)班数学临界生预警报告"
    assert is_tier_alert_query(q) is True
    assert is_school_exam_report_query(q) is False
    plans = build_tier_alert_plan_items(q)
    assert len(plans) == 2
    assert "build_tier_alert_report_data_tool" in plans[1]["sub_task"]
    assert "build_subject_diagnosis_sections_tool" in plans[1]["sub_task"]
    blob = " ".join(p["sub_task"] for p in plans)
    assert "class_name=高三(10)班" in blob
    assert "subject_name=数学" in blob
    assert "school_name=扬州中学" in blob


def test_group_feature_plan_not_class_comparison():
    q = "扬州中学连淮扬镇数学考试按班级群体对比特征"
    assert is_group_feature_query(q) is True
    assert is_school_class_comparison_query(q) is False
    assert is_school_exam_report_query(q) is False
    plans = build_group_feature_plan_items(q)
    assert len(plans) == 2
    assert "build_group_feature_report_data_tool" in plans[1]["sub_task"]
    assert "dimension=class" in plans[1]["sub_task"]
    assert "school_name=扬州中学" in plans[1]["sub_task"]
    # 既有「各班横向」仍走班级横向对比
    cmp_q = "扬州中学在连淮扬镇数学考试中各个班级的横向多维对比分析"
    assert is_group_feature_query(cmp_q) is False
    assert is_school_class_comparison_query(cmp_q) is True


def test_class_horizontal_xueqing_not_group_feature():
    """「班级横向对比学情分析」必须是班级横向对比，不能落到群体特征。"""
    q = "扬州中学高二数学班级横向对比学情分析"
    assert is_group_feature_query(q) is False
    assert is_school_class_comparison_query(q) is True
    assert ReportIntentResolver().resolve(q).report_type == ReportType.GRADE_COMPARISON
    assert _route(q) == "class-comparison"
    # LLM 若误拆成群体特征，coerce 须纠正
    wrong = [
        {"sub_task": "查成绩", "sub_task_agent": "DataAnalyst"},
        {
            "sub_task": "调 build_group_feature_report_data_tool(school_name=扬州中学, dimension=class)",
            "sub_task_agent": "ToolExpert",
        },
    ]
    fixed = coerce_plan_items_if_needed(q, wrong)
    blob = " ".join(p["sub_task"] for p in fixed)
    assert "build_subject_diagnosis_sections_tool" in blob
    assert "build_group_feature_report_data_tool" not in blob


def test_class_overview_plan_not_subject_diagnosis():
    q = "扬州中学高三(10)班连淮扬镇数学成绩总览"
    assert is_class_overview_query(q) is True
    assert is_school_exam_report_query(q) is False
    plans = build_class_overview_plan_items(q)
    assert len(plans) == 2
    assert "build_class_overview_report_data_tool" in plans[1]["sub_task"]
    blob = " ".join(p["sub_task"] for p in plans)
    assert "class_name=高三(10)班" in blob
    assert "subject_name=数学" in blob
    assert "连淮扬镇" in blob
    assert "exam_name=连淮扬镇" in blob
    assert "【问题中的考试】" not in blob
    # 「详细分析」仍走学校科目诊断
    detail_q = "扬州中学高三(9)班数学诊断报告"
    assert is_class_overview_query(detail_q) is False
    assert is_school_exam_report_query(detail_q) is True


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


def test_llm_classify_grade_comparison_plan():
    from src.agent.education.intent_router import plan_items_for_route

    q = "扬州中学高二数学班级横向对比学情分析"
    llm = _FakeLlm(
        '{"needs_report":true,"report_type":"grade_comparison",'
        '"confidence":0.91,"reason":"横向对比"}'
    )
    route = _run(classify_report_intent(q, llm))
    assert route.needs_report is True
    assert route.report_type == ReportType.GRADE_COMPARISON
    assert route.source == "llm"
    plans = plan_items_for_route(route, q)
    blob = " ".join(p["sub_task"] for p in plans)
    assert "build_subject_diagnosis_sections_tool" in blob
    assert "build_group_feature_report_data_tool" not in blob
    assert ReportIntentResolver().resolve(q, route=route).report_type == (
        ReportType.GRADE_COMPARISON
    )


def test_llm_classify_group_feature_plan():
    from src.agent.education.intent_router import plan_items_for_route

    q = "扬州中学连淮扬镇数学考试按班级群体对比特征"
    llm = _FakeLlm(
        '{"needs_report":true,"report_type":"group_feature",'
        '"confidence":0.88,"reason":"明确群体特征"}'
    )
    route = _run(classify_report_intent(q, llm))
    assert route.needs_report is True
    assert route.report_type == ReportType.GROUP_FEATURE
    plans = plan_items_for_route(route, q)
    blob = " ".join(p["sub_task"] for p in plans)
    assert "build_group_feature_report_data_tool" in blob


def test_llm_classify_fact_query_no_report():
    from src.agent.education.intent_router import plan_items_for_route

    q = "高二(6)班数学成绩最好的学生是谁"
    llm = _FakeLlm(
        '{"needs_report":false,"report_type":null,"confidence":0.9,"reason":"事实查询"}'
    )
    route = _run(classify_report_intent(q, llm))
    assert route.needs_report is False
    assert route.report_type is None
    plans = plan_items_for_route(route, q)
    assert len(plans) == 1
    assert plans[0]["sub_task_agent"] == "DataAnalyst"
    assert "build_" not in plans[0]["sub_task"] or "禁止" in plans[0]["sub_task"]
    blob = plans[0]["sub_task"]
    assert "student_id" in blob
    assert "姓名" not in blob or "禁止" in blob
    assert "禁止" in blob and ("xm" in blob or "姓名" in blob)


def test_llm_failure_no_report_word_is_fact():
    q = "高二(6)班数学均分多少"
    route = _run(classify_report_intent(q, _FakeLlm(RuntimeError("boom"))))
    assert route.needs_report is False
    assert route.report_type is None


def test_llm_failure_falls_back_to_valid_type():
    q = "扬州中学高二数学班级横向对比学情分析"
    route = _run(classify_report_intent(q, _FakeLlm(RuntimeError("boom"))))
    assert route.needs_report is True
    assert route.report_type == ReportType.GRADE_COMPARISON
    assert route.source in {"fallback", "hard"}

    bad_json = _run(classify_report_intent(q, _FakeLlm("not-json{{{")))
    assert bad_json.report_type == ReportType.GRADE_COMPARISON

    illegal = _run(
        classify_report_intent(
            q,
            _FakeLlm(
                '{"needs_report":true,"report_type":"not_a_type","confidence":1}'
            ),
        )
    )
    assert illegal.report_type == ReportType.GRADE_COMPARISON


def test_hard_constraint_excludes_group_feature_from_llm():
    """含横向对比且无群体特征时，即使 LLM 想选 group_feature 也会因候选收缩而失败→兜底。"""
    q = "扬州中学高二数学班级横向对比学情分析"
    llm = _FakeLlm(
        '{"needs_report":true,"report_type":"group_feature",'
        '"confidence":0.99,"reason":"误选"}'
    )
    route = _run(classify_report_intent(q, llm))
    assert route.needs_report is True
    assert route.report_type == ReportType.GRADE_COMPARISON


def test_coerce_fact_query_rejects_report_plan():
    q = "高二(6)班数学成绩最好的学生是谁"
    wrong = [
        {"sub_task": "查成绩", "sub_task_agent": "DataAnalyst"},
        {
            "sub_task": "调 build_class_overview_report_data_tool(class_name=高二(6)班, render=true)",
            "sub_task_agent": "ToolExpert",
        },
    ]
    fixed = coerce_plan_items_if_needed(q, wrong)
    assert len(fixed) == 1
    assert "build_class_overview" not in fixed[0]["sub_task"]
    assert _route(q) == "fact"


def test_resolver_planner_dispatch_consistent():
    from src.agent.education.intent_router import plan_items_for_route

    cases = [
        "扬州中学高二数学班级横向对比学情分析",
        "扬州中学连淮扬镇数学考试按班级群体对比特征",
        "扬州中学高三(10)班连淮扬镇数学成绩总览",
        "临界生预警报告",
    ]
    for q in cases:
        route = classify_report_intent_sync(q)
        assert route.needs_report is True
        spec = ReportIntentResolver().resolve(q)
        assert spec.report_type == route.report_type
        plans = plan_items_for_route(route, q)
        assert plans
        blob = " ".join(p["sub_task"] for p in plans)
        assert blob.strip()
