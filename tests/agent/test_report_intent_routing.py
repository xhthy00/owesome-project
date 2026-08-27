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
    extract_school_type_target,
    extract_student_target,
    is_citywide_analysis_query,
    is_class_overview_query,
    is_group_feature_query,
    is_individual_student_analysis_query,
    is_line_reach_query,
    is_line_reach_report_query,
    is_school_class_comparison_query,
    is_school_exam_report_query,
    is_school_vs_city_avg_query,
    is_school_vs_school_type_avg_query,
    is_tier_alert_query,
)
from src.agent.education.report_types import ReportType
from src.agent.expand.planner import (
    build_class_overview_plan_items,
    build_comprehensive_class_plan_items,
    build_fact_query_plan_items,
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
        ReportType.LINE_REACH: "line_reach",
        ReportType.SUBJECT_RESEARCH: "subject_research",
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


def test_citywide_district_compare_line_reach_is_report():
    q = "各区特控线达线分析"
    assert is_line_reach_report_query(q) is True
    assert _route(q) == "line_reach"
    route = classify_report_intent_sync(q)
    assert route.needs_report is True
    assert route.report_type == ReportType.LINE_REACH


def test_citywide_line_reach_is_report_not_diagnostic():
    q = "全市2026届高三1月期末达线情况"
    assert is_line_reach_query(q) is True
    assert is_line_reach_report_query(q) is True
    assert is_citywide_analysis_query(q) is False
    assert _route(q) == "line_reach"
    route = classify_report_intent_sync(q)
    assert route.needs_report is True
    assert route.report_type == ReportType.LINE_REACH
    llm = _FakeLlm(
        '{"needs_report":true,"report_type":"diagnostic_report",'
        '"confidence":0.99,"reason":"全市"}'
    )
    llm_route = _run(classify_report_intent(q, llm))
    assert llm_route.needs_report is True
    assert llm_route.report_type == ReportType.LINE_REACH
    spec = ReportIntentResolver().resolve(q)
    assert spec.report_type == ReportType.LINE_REACH
    assert spec.filters.get("question") == q
    assert not spec.filters.get("exam_name")
    wrong = [
        {"sub_task": "查询全市成绩明细", "sub_task_agent": "DataAnalyst"},
        {
            "sub_task": "调 build_diagnostic_report_data_tool(scope_label=全市, render=true)",
            "sub_task_agent": "ToolExpert",
        },
    ]
    fixed = coerce_plan_items_if_needed(q, wrong)
    blob = " ".join(p["sub_task"] for p in fixed)
    assert "调 build_diagnostic_report_data_tool" not in blob
    assert "build_line_reach_report_data_tool" in blob
    assert "禁止" in blob
    assert "tb_score_overview" in blob
    assert is_citywide_analysis_query(
        "帮我分析全市的江苏省高一上学期数学期末质量检测成绩，形成详细报告"
    ) is True


def test_narrow_line_reach_count_is_fact():
    q = "邗江区物理类本科线达线人数"
    assert is_line_reach_query(q) is True
    assert is_line_reach_report_query(q) is False
    assert _route(q) == "fact"
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


def test_exam_hint_cohort_month_batch():
    """「2026届高三3月」应抽为批次简称，供 LIKE 对齐。"""
    q = "扬州市2026届高三3月广陵区本科线达线人数和达线率"
    assert extract_exam_name_hint(q) == "2026届高三3月"
    blob = build_fact_query_plan_items(q)[0]["sub_task"]
    assert "区县【广陵区】" in blob
    assert "考试【2026届高三3月】" in blob
    assert "区县【月" not in blob
    assert "LIKE '%广陵%'" in blob or "district LIKE" in blob
    assert "禁止把「N月」" in blob or "拼进区县" in blob


def test_class_school_line_reach_is_fact_not_citywide_report():
    q = "我想了解扬州中学高三(18)班现有的考试达到特招线的情况"
    assert is_line_reach_query(q) is True
    assert is_line_reach_report_query(q) is False
    assert _route(q) == "fact"
    route = classify_report_intent_sync(q)
    assert route.needs_report is False
    assert route.report_type is None
    plans = build_fact_query_plan_items(q)
    blob = plans[0]["sub_task"]
    assert "build_line_reach_report_data_tool" not in blob
    assert "tb_score_overview" in blob
    assert "tb_fraction_bar" in blob
    assert "zf6m" in blob
    assert "zf4m" in blob  # 提示里应明确禁止
    assert "禁止" in blob and "zf4m" in blob
    assert "xkkm" in blob or "物理类" in blob
    assert "扬州中学" in blob
    assert "高三(18)班" in blob
    wrong = [
        {
            "sub_task": "调 build_line_reach_report_data_tool(render=true)",
            "sub_task_agent": "ToolExpert",
        },
    ]
    fixed = coerce_plan_items_if_needed(q, wrong)
    fixed_blob = " ".join(p["sub_task"] for p in fixed)
    assert "调 build_line_reach_report_data_tool" not in fixed_blob
    assert "tb_score_overview" in fixed_blob


def test_class_nanda_line_reach_plan_forces_zf6m_and_track_split():
    q = "2026届高三3月扬州中学高三(1)班南大达线情况"
    assert is_line_reach_query(q) is True
    assert is_line_reach_report_query(q) is False
    blob = build_fact_query_plan_items(q)[0]["sub_task"]
    assert "zf6m" in blob
    assert "禁止" in blob
    assert "zf4m" in blob
    assert "文理混报" in blob or "只报该方向" in blob
    assert "tb_score_indicator" in blob and "禁止" in blob
    assert "高三(1)班" in blob


@pytest.mark.parametrize(
    "question",
    [
        "扬州中学特招线情况",
        "邗江区达线情况",
    ],
)
def test_school_or_district_line_reach_is_fact(question: str):
    assert is_line_reach_query(question) is True
    assert is_line_reach_report_query(question) is False
    assert _route(question) == "fact"
    route = classify_report_intent_sync(question)
    assert route.needs_report is False
    assert route.report_type is None
    blob = build_fact_query_plan_items(question)[0]["sub_task"]
    assert "build_line_reach_report_data_tool" not in blob
    assert "tb_score_indicator" in blob
    assert "禁止套用全市达线" in blob


def test_leading_school_citywide_line_reach_is_fact_not_city_report():
    q = "2026届高三1月期末全市引领校达线情况"
    assert extract_school_type_target(q) == "引领"
    assert extract_school_type_target("全市达线情况") is None
    assert is_line_reach_query(q) is True
    assert is_line_reach_report_query(q) is False
    assert _route(q) == "fact"
    route = classify_report_intent_sync(q)
    assert route.needs_report is False
    assert route.report_type is None
    blob = build_fact_query_plan_items(q)[0]["sub_task"]
    assert "build_line_reach_report_data_tool" not in blob
    assert "tb_school" in blob
    assert "type" in blob
    assert "引领" in blob
    assert "tb_score_indicator" in blob
    assert "禁止套用全市达线" in blob
    wrong = [
        {
            "sub_task": "调 build_line_reach_report_data_tool(render=true)",
            "sub_task_agent": "ToolExpert",
        },
    ]
    fixed = coerce_plan_items_if_needed(q, wrong)
    fixed_blob = " ".join(p["sub_task"] for p in fixed)
    assert "调 build_line_reach_report_data_tool" not in fixed_blob
    assert "tb_score_indicator" in fixed_blob
    assert "tb_school" in fixed_blob


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


def test_score_band_report_vs_threshold_fact():
    from src.agent.education.query_parse import is_score_band_report_query
    from src.templates.sql_gen_prompt import resolve_edu_sql_intent

    q_report = "各地区总分十分段情况分析"
    assert is_score_band_report_query(q_report) is True
    route = classify_report_intent_sync(q_report)
    assert route.needs_report is True
    assert route.report_type == ReportType.SCORE_BAND
    assert _route("高三(1)班分数段情况") != "score_band"

    q_fact = "邗江物理类600分以上多少人"
    assert classify_report_intent_sync(q_fact).needs_report is False
    blob = build_fact_query_plan_items(q_fact)[0]["sub_task"]
    assert "tb_score_overview" in blob
    assert "zf6m" in blob
    assert "hxzh" in blob
    assert resolve_edu_sql_intent(q_fact) == "score_band"

    q_chem = "全市化学86到90分多少人"
    assert classify_report_intent_sync(q_chem).needs_report is False
    assert resolve_edu_sql_intent(q_chem) == "score_band"

    q10 = "2026届高三1月各区县总分10分段统计"
    assert is_score_band_report_query(q10) is True
    route10 = classify_report_intent_sync(q10)
    assert route10.needs_report is True
    assert route10.report_type == ReportType.SCORE_BAND
    from src.agent.education.intent_router import plan_items_for_route

    blob10 = " ".join(p["sub_task"] for p in plan_items_for_route(route10, q10))
    assert "build_score_band_report_data_tool" in blob10

    q_sch = "2026届高三1月扬州中学总分10分段分布情况"
    assert extract_school_target(q_sch) == "扬州中学"
    assert is_score_band_report_query(q_sch) is False
    route_sch = classify_report_intent_sync(q_sch)
    assert route_sch.needs_report is False
    blob_sch = build_fact_query_plan_items(q_sch)[0]["sub_task"]
    assert "扬州中学" in blob_sch
    assert "各区县 HTML" in blob_sch or "禁止出全市" in blob_sch
    assert "build_score_band_report_data_tool" not in blob_sch


def test_school_vs_city_avg_is_fact_not_class_compare():
    """点名学校均分 vs 全市是事实问，不能落到班级横向对比。"""
    from src.agent.education.orchestrator import _extract_subject
    from src.templates.sql_gen_prompt import resolve_edu_sql_intent

    q = "2026届高三1月扬州中学物理类均分与全市的比较分析"
    assert extract_school_target(q) == "扬州中学"
    assert _extract_subject(q) is None
    assert is_school_vs_city_avg_query(q) is True
    assert is_school_class_comparison_query(q) is False
    assert is_school_exam_report_query(q) is False
    route = classify_report_intent_sync(q)
    assert route.needs_report is False
    assert route.report_type is None
    assert _route(q) == "fact"
    item = build_fact_query_plan_items(q)[0]
    blob = item["sub_task"]
    assert item["sub_task_agent"] == "DataAnalyst"
    assert "扬州中学" in blob
    assert "zf6m" in blob
    assert "xx LIKE" in blob
    assert "GZ_" in blob
    assert "班级横向" in blob
    assert "build_subject_diagnosis" not in blob
    assert "科目【物理】" not in blob
    assert "选科【物理类】" in blob
    assert resolve_edu_sql_intent(q) == "overview_avg"

    q_end = "2026届高三1月期末扬州中学物理类均分与全市的对比"
    assert is_school_vs_city_avg_query(q_end) is True
    assert "xx LIKE" in build_fact_query_plan_items(q_end)[0]["sub_task"]

    q_own = "2026届高三1月期末本校物理类均分与全市的对比"
    assert is_school_vs_city_avg_query(q_own) is True
    assert is_citywide_analysis_query(q_own) is False
    route_own = classify_report_intent_sync(q_own)
    assert route_own.needs_report is False
    assert _route(q_own) == "fact"

    assert _extract_subject("扬州中学物理成绩分析") == "物理"
    cmp_q = "扬州中学高二数学班级横向对比学情分析"
    assert is_school_vs_city_avg_query(cmp_q) is False
    assert is_school_class_comparison_query(cmp_q) is True

    llm = _FakeLlm(
        '{"needs_report":true,"report_type":"grade_comparison",'
        '"confidence":0.9,"reason":"比较分析"}'
    )
    route_llm = _run(classify_report_intent(q, llm))
    assert route_llm.needs_report is False
    assert route_llm.report_type is None
    assert route_llm.source == "hard"


def test_subject_research_routing_isolation():
    from src.agent.education.intent_router import plan_items_for_route
    from src.agent.education.query_parse import is_subject_research_report_query

    hit = "扬州中学 3 月学科教研分析报告"
    assert is_subject_research_report_query(hit) is True
    route = classify_report_intent_sync(hit)
    assert route.needs_report is True
    assert route.report_type == ReportType.SUBJECT_RESEARCH
    assert route.source == "hard"
    assert _route(hit) == "subject_research"
    spec = ReportIntentResolver().resolve(hit)
    assert spec.report_type == ReportType.SUBJECT_RESEARCH
    assert spec.filters.get("school_name") == "扬州中学"
    assert "class_name" not in spec.filters
    blob = " ".join(p["sub_task"] for p in plan_items_for_route(route, hit))
    assert "调 build_subject_research_report_data_tool" in blob
    assert "调 build_subject_diagnosis_sections_tool" not in blob

    math_q = "扬州中学数学教研分析报告"
    spec_m = ReportIntentResolver().resolve(math_q)
    assert spec_m.report_type == ReportType.SUBJECT_RESEARCH
    assert spec_m.filters.get("subject") == "数学"

    ask = "给我出一份学科教研分析报告"
    assert classify_report_intent_sync(ask).report_type == ReportType.SUBJECT_RESEARCH

    assert classify_report_intent_sync("扬州中学 3 月数学科目诊断").report_type == (
        ReportType.SUBJECT_DIAGNOSIS
    )
    assert classify_report_intent_sync("扬州中学 3 月数学学科诊断").report_type == (
        ReportType.SUBJECT_DIAGNOSIS
    )
    assert classify_report_intent_sync("扬州中学 3 月数学分析报告").report_type == (
        ReportType.SUBJECT_DIAGNOSIS
    )
    assert classify_report_intent_sync("高三(5)班临界生预警").report_type == (
        ReportType.TIER_ALERT
    )
    assert classify_report_intent_sync("全市理前100分析").report_type == (
        ReportType.ELITE_ROSTER
    )
    assert classify_report_intent_sync("贡献分分析").report_type == ReportType.CONTRIBUTION
    assert is_subject_research_report_query("八校分析报告") is False
    assert classify_report_intent_sync("八校分析报告").report_type != (
        ReportType.SUBJECT_RESEARCH
    )


def test_subject_research_bypasses_llm():
    q = "扬州中学教科院分析报告"
    llm = _FakeLlm(
        '{"needs_report":true,"report_type":"subject_diagnosis",'
        '"confidence":0.99,"reason":"分析报告"}'
    )
    route = _run(classify_report_intent(q, llm))
    assert route.report_type == ReportType.SUBJECT_RESEARCH
    assert route.source == "hard"


def test_school_vs_leading_subject_avg_is_fact_uses_xxlb():
    """学校对比引领校单科均分：学生加权 AVG(yw)+xxlb，禁止 JOIN tb_school。"""
    q = "2026届高三3月扬州中学对比引领校语文单科"
    assert extract_school_target(q) == "扬州中学"
    assert extract_school_type_target(q) == "引领"
    assert is_school_vs_school_type_avg_query(q) is True
    assert is_school_vs_city_avg_query(q) is False
    assert is_line_reach_query(q) is False
    route = classify_report_intent_sync(q)
    assert route.needs_report is False
    assert _route(q) == "fact"
    blob = build_fact_query_plan_items(q)[0]["sub_task"]
    assert "yw" in blob
    assert "xxlb" in blob
    assert "在籍生" in blob
    assert "JOIN tb_school" in blob and "禁止" in blob
    assert "GROUP BY xx" in blob
    assert "yw > 0" in blob
    assert "缺考" in blob
    assert "build_line_reach_report_data_tool" not in blob
    from src.agent.education.prompt_context import build_education_sql_hint_text
    from src.templates.sql_gen_prompt import education_sql_training_block_for_intent

    hint = build_education_sql_hint_text(q)
    assert "xxlb" in hint
    assert "JOIN tb_school" in hint
    assert "达线" not in hint
    assert "yw > 0" in hint
    shot = education_sql_training_block_for_intent("overview_avg")
    assert "yw > 0" in shot
    wrong = [
        {
            "sub_task": "调 build_subject_avg_report_data_tool(render=true)",
            "sub_task_agent": "ToolExpert",
        },
    ]
    fixed = coerce_plan_items_if_needed(q, wrong)
    fixed_blob = " ".join(p["sub_task"] for p in fixed)
    assert "调 build_subject_avg_report_data_tool" not in fixed_blob
    assert "xxlb" in fixed_blob
