"""ReportOrchestrator / ReportIntentResolver 单元测试。

用 mock 的 execute_sql + resolve_schema 跑端到端编排，验证：
- 意图识别关键词映射 + 受众推断；
- class_overview 完整产出 HTML（含 KPI + 分数段图）；
- 未实现报告类型返回 error 而非抛异常；
- 自定义阈值（及格线 50）影响 pass_rate。
"""

from __future__ import annotations

import asyncio

import pytest

from src.agent.education.config import EducationConfig
from src.agent.education.orchestrator import ReportIntentResolver, ReportOrchestrator
from src.agent.education.report_types import Audience, ReportType
from src.agent.education.schema_mapping import ScoreSchemaMapping, load_schema_from_config


def _run(coro):
    return asyncio.run(coro)


# ---- intent resolver ------------------------------------------------------

def test_intent_resolver_class_overview_with_audience_hint():
    ir = ReportIntentResolver()
    spec = ir.resolve("生成初三1班期中成绩分析报告", audience_hint="head_teacher")
    assert spec.report_type == ReportType.CLASS_OVERVIEW
    assert spec.audience == Audience.HEAD_TEACHER
    assert spec.filters["class_name"] == "初三1班"
    assert spec.filters["exam_name"] == "期中"


def test_intent_resolver_trend_tracking_by_keyword():
    ir = ReportIntentResolver()
    spec = ir.resolve("张三最近三次数学成绩变化")
    assert spec.report_type == ReportType.TREND_TRACKING
    assert spec.filters["subject"] == "数学"


def test_intent_resolver_parent_audience_from_question():
    ir = ReportIntentResolver()
    spec = ir.resolve("给家长看看孩子的学情报告")
    assert spec.report_type == ReportType.STUDENT_PROFILE
    assert spec.audience == Audience.PARENT


def test_intent_resolver_falls_back_to_class_overview():
    ir = ReportIntentResolver()
    spec = ir.resolve("随便看看")
    assert spec.report_type == ReportType.CLASS_OVERVIEW
    assert spec.audience == Audience.DEFAULT


def test_intent_resolver_principal_keyword():
    ir = ReportIntentResolver()
    spec = ir.resolve("校长想看年级各班对比")
    assert spec.report_type == ReportType.GRADE_COMPARISON
    assert spec.audience == Audience.PRINCIPAL


def test_intent_resolver_diagnostic_report():
    ir = ReportIntentResolver()
    spec = ir.resolve("南京市第一中学数学结构化诊断报告")
    assert spec.report_type == ReportType.DIAGNOSTIC_REPORT


def test_intent_resolver_class_overview_not_hijacked_by_diagnostic():
    ir = ReportIntentResolver()
    spec = ir.resolve("生成初三1班数学班级成绩报告")
    assert spec.report_type == ReportType.CLASS_OVERVIEW


# ---- orchestrator ---------------------------------------------------------

def _wide_mapping():
    return ScoreSchemaMapping(
        mode="wide",
        table="student_score",
        subject_columns={"数学": "math"},
        fields={"class_name": "cls"},
    )


def _normalized_mapping():
    return ScoreSchemaMapping(
        mode="normalized",
        tables={"student": "student", "score": "score", "exam": "exam"},
        fields={"score": "score", "subject": "subject", "class_name": "cls"},
    )


def test_orchestrator_class_overview_full_pipeline():
    async def fake_execute(sql):
        assert "SELECT" in sql and "LIMIT 1000" in sql
        return {"columns": ["score"], "rows": [[85], [72], [60], [55], [100], [40], [78]], "row_count": 7}

    async def fake_schema():
        return _wide_mapping()

    orch = ReportOrchestrator(execute_sql=fake_execute, resolve_schema=fake_schema)
    res = _run(orch.run("生成初三1班期中成绩分析报告"))

    assert res.error is None
    assert res.template_name == "education/class_overview.html"
    assert "初三1班" in res.html
    assert "edu-container" in res.html
    assert res.stats["count"] == 7
    assert res.stats["pass_rate"] == pytest.approx(71.43, abs=0.01)
    # 分数段图 option 应嵌入 HTML
    assert "application/json" in res.html


def test_orchestrator_custom_threshold_changes_pass_rate():
    async def fake_execute(sql):
        return {"columns": ["score"], "rows": [[55], [60], [70]], "row_count": 3}

    async def fake_schema():
        return _wide_mapping()

    cfg = EducationConfig(pass_threshold=50, excellent_threshold=85)
    orch = ReportOrchestrator(execute_sql=fake_execute, resolve_schema=fake_schema, config=cfg)
    res = _run(orch.run("初三1班期中分析"))
    # 50 分及格线下，55/60/70 全部及格
    assert res.stats["pass_rate"] == 100.0


def test_orchestrator_normalized_mode_builds_join_sql():
    captured = {}

    async def fake_execute(sql):
        captured["sql"] = sql
        return {"columns": ["score"], "rows": [[80], [90]], "row_count": 2}

    async def fake_schema():
        return _normalized_mapping()

    orch = ReportOrchestrator(execute_sql=fake_execute, resolve_schema=fake_schema)
    res = _run(orch.run("数学科目诊断"))
    assert "FROM score" in captured["sql"]
    assert "subject = '数学'" in captured["sql"]
    assert res.stats["count"] == 2


def test_orchestrator_all_report_types_have_templates():
    """9 类 ReportType 全部已实现模板，编排器不再有'未实现'空缺。"""
    from src.agent.education.report_types import ReportSpec
    from src.agent.education.templates import select_report_template

    async def fake_execute(sql):
        return {"columns": ["score"], "rows": [[80]], "row_count": 1}

    async def fake_schema():
        return _wide_mapping()

    orch = ReportOrchestrator(execute_sql=fake_execute, resolve_schema=fake_schema)
    for rt in ReportType:
        info = select_report_template(rt, Audience.DEFAULT)
        assert info["template_name"], f"{rt.value} 缺模板"
        # 每种类型都能跑通（不抛异常）
        original = orch.intent_resolver.resolve
        orch.intent_resolver.resolve = lambda q, audience_hint=None: ReportSpec(
            report_type=rt, audience=Audience.DEFAULT, filters={"class_name": "初三1班"}
        )
        try:
            res = _run(orch.run("测试"))
        finally:
            orch.intent_resolver.resolve = original
        assert res.error is None, f"{rt.value} 编排失败: {res.error}"


def test_orchestrator_execute_sql_failure_does_not_raise():
    async def fake_execute(sql):
        raise RuntimeError("db down")

    async def fake_schema():
        return _wide_mapping()

    orch = ReportOrchestrator(execute_sql=fake_execute, resolve_schema=fake_schema)
    res = _run(orch.run("初三1班期中分析"))
    assert res.html == ""
    assert res.error == "db down"


def test_orchestrator_run_spec_skips_intent_and_uses_filters():
    """run_spec 直接使用 ReportSpec，不经自然语言意图解析。"""
    from src.agent.education.report_types import ReportSpec

    captured = {}

    async def fake_execute(sql):
        captured["sql"] = sql
        return {"columns": ["score"], "rows": [[85], [72], [60]], "row_count": 3}

    async def fake_schema():
        return _wide_mapping()

    orch = ReportOrchestrator(execute_sql=fake_execute, resolve_schema=fake_schema)
    spec = ReportSpec(
        report_type=ReportType.CLASS_OVERVIEW,
        audience=Audience.HEAD_TEACHER,
        filters={"class_name": "高一(3)班", "exam_name": "期末"},
    )
    res = _run(orch.run_spec(spec))
    assert res.error is None
    assert res.template_name == "education/class_overview.html"
    assert res.spec.report_type == ReportType.CLASS_OVERVIEW
    assert "高一(3)班" in res.html
    assert "高一(3)班" in captured["sql"]


def test_orchestrator_run_delegates_to_run_spec_same_result():
    """run() 委托 run_spec 后，与直接构造同语义 spec 的结果一致。"""
    from src.agent.education.report_types import ReportSpec

    async def fake_execute(sql):
        return {"columns": ["score"], "rows": [[80], [70]], "row_count": 2}

    async def fake_schema():
        return _wide_mapping()

    orch = ReportOrchestrator(execute_sql=fake_execute, resolve_schema=fake_schema)
    via_run = _run(orch.run("生成初三1班期中成绩分析报告", audience_hint="head_teacher"))
    spec = ReportSpec(
        report_type=ReportType.CLASS_OVERVIEW,
        audience=Audience.HEAD_TEACHER,
        filters={"class_name": "初三1班", "exam_name": "期中"},
    )
    via_spec = _run(orch.run_spec(spec))
    assert via_run.error is None and via_spec.error is None
    assert via_run.template_name == via_spec.template_name
    assert via_run.stats["count"] == via_spec.stats["count"]
    assert via_run.spec.report_type == via_spec.spec.report_type


def test_orchestrator_grade_comparison_fills_chart_and_table():
    """班级横向对比必须填充 CLASS_COMPARE_CHART / CLASS_RANKING_TABLE。"""
    from src.agent.education.report_types import ReportSpec

    async def fake_execute(sql):
        return {
            "columns": ["score", "exam_score", "class", "school_name", "district", "subject", "student_id"],
            "rows": [
                [90, 150, "高三(1)班", "扬州中学", "广陵区", "数学", "s1"],
                [80, 150, "高三(1)班", "扬州中学", "广陵区", "数学", "s2"],
                [70, 150, "高三(2)班", "扬州中学", "广陵区", "数学", "s3"],
                [60, 150, "高三(2)班", "扬州中学", "广陵区", "数学", "s4"],
                [95, 150, "高三(3)班", "扬州中学", "广陵区", "数学", "s5"],
            ],
            "row_count": 5,
        }

    async def fake_schema():
        return _config_edu_mapping()

    orch = ReportOrchestrator(execute_sql=fake_execute, resolve_schema=fake_schema)
    spec = ReportSpec(
        report_type=ReportType.GRADE_COMPARISON,
        audience=Audience.GRADE_HEAD,
        filters={
            "school_name": "扬州中学",
            "exam_name": "统一考试",
            "subject": "数学",
        },
    )
    res = _run(orch.run_spec(spec))
    assert res.error is None
    assert res.template_name == "education/grade_comparison.html"
    assert "classCompareData" in res.html or "高三" in res.html
    assert "CLASS_COMPARE_CHART" not in res.html  # placeholder replaced
    assert "各班级均分对比" in res.html
    assert "高三(1)班" in res.html
    assert "高三(3)班" in res.html


def _multi_exam_score_rows():
    """两场考试 × 两名学生 × 数学，供学生画像/趋势/综合。"""
    return {
        "columns": [
            "score", "exam_score", "class", "school_name", "district",
            "subject", "student_id", "student_name", "exam_name",
        ],
        "rows": [
            [90, 150, "高一(1)班", "扬州中学", "广陵区", "数学", "s1", "张三", "期中"],
            [80, 150, "高一(1)班", "扬州中学", "广陵区", "数学", "s2", "李四", "期中"],
            [100, 150, "高一(1)班", "扬州中学", "广陵区", "数学", "s1", "张三", "期末"],
            [70, 150, "高一(1)班", "扬州中学", "广陵区", "数学", "s2", "李四", "期末"],
            [85, 150, "高一(1)班", "扬州中学", "广陵区", "语文", "s1", "张三", "期中"],
            [75, 150, "高一(1)班", "扬州中学", "广陵区", "语文", "s2", "李四", "期中"],
            [88, 150, "高一(1)班", "扬州中学", "广陵区", "语文", "s1", "张三", "期末"],
            [65, 150, "高一(1)班", "扬州中学", "广陵区", "语文", "s2", "李四", "期末"],
        ],
        "row_count": 8,
    }


def test_orchestrator_student_profile_fills_tables():
    from src.agent.education.report_types import ReportSpec

    async def fake_execute(sql):
        return _multi_exam_score_rows()

    async def fake_schema():
        return _config_edu_mapping()

    orch = ReportOrchestrator(execute_sql=fake_execute, resolve_schema=fake_schema)
    spec = ReportSpec(
        report_type=ReportType.STUDENT_PROFILE,
        filters={
            "student_name": "张三",
            "class_name": "高一(1)班",
            "subject": "数学",
        },
    )
    res = _run(orch.run_spec(spec))
    assert res.error is None
    assert "张三" in res.html
    assert "SCORE_SUMMARY_TABLE" not in res.html
    assert "期中" in res.html or "期末" in res.html
    assert "ASSESSMENT" not in res.html


def test_orchestrator_trend_tracking_fills_chart():
    from src.agent.education.report_types import ReportSpec

    async def fake_execute(sql):
        return _multi_exam_score_rows()

    async def fake_schema():
        return _config_edu_mapping()

    orch = ReportOrchestrator(execute_sql=fake_execute, resolve_schema=fake_schema)
    spec = ReportSpec(
        report_type=ReportType.TREND_TRACKING,
        filters={"class_name": "高一(1)班", "subject": "数学", "school_name": "扬州中学"},
    )
    res = _run(orch.run_spec(spec))
    assert res.error is None
    assert "TREND_CHART" not in res.html
    assert "期中" in res.html or "期末" in res.html


def test_orchestrator_tier_alert_fills_counts():
    from src.agent.education.report_types import ReportSpec

    async def fake_execute(sql):
        return {
            "columns": [
                "score", "exam_score", "class", "school_name", "district",
                "subject", "student_id", "student_name", "exam_name",
            ],
            "rows": [
                [58, 100, "高一(1)班", "扬州中学", "", "数学", "s1", "甲", "期中"],
                [62, 100, "高一(1)班", "扬州中学", "", "数学", "s2", "乙", "期中"],
                [90, 100, "高一(1)班", "扬州中学", "", "数学", "s3", "丙", "期中"],
                [40, 100, "高一(1)班", "扬州中学", "", "语文", "s3", "丙", "期中"],
            ],
            "row_count": 4,
        }

    async def fake_schema():
        return _config_edu_mapping()

    orch = ReportOrchestrator(execute_sql=fake_execute, resolve_schema=fake_schema)
    spec = ReportSpec(
        report_type=ReportType.TIER_ALERT,
        filters={"class_name": "高一(1)班", "exam_name": "期中"},
    )
    res = _run(orch.run_spec(spec))
    assert res.error is None
    assert "CRITICAL_COUNT" not in res.html
    assert "临界生" in res.html


def test_orchestrator_group_feature_fills_table():
    from src.agent.education.report_types import ReportSpec

    async def fake_execute(sql):
        return {
            "columns": [
                "score", "exam_score", "class", "school_name", "district",
                "subject", "student_id", "student_name", "exam_name",
            ],
            "rows": [
                [90, 150, "高三(1)班", "扬州中学", "广陵区", "数学", "s1", "a", "统一"],
                [70, 150, "高三(2)班", "扬州中学", "广陵区", "数学", "s2", "b", "统一"],
                [95, 150, "高三(3)班", "扬州中学", "广陵区", "数学", "s3", "c", "统一"],
            ],
            "row_count": 3,
        }

    async def fake_schema():
        return _config_edu_mapping()

    orch = ReportOrchestrator(execute_sql=fake_execute, resolve_schema=fake_schema)
    spec = ReportSpec(
        report_type=ReportType.GROUP_FEATURE,
        filters={"school_name": "扬州中学", "subject": "数学", "exam_name": "统一"},
    )
    res = _run(orch.run_spec(spec))
    assert res.error is None
    assert "GROUP_TABLE" not in res.html
    assert "高三(1)班" in res.html


def test_orchestrator_comprehensive_fills_overview():
    from src.agent.education.report_types import ReportSpec

    async def fake_execute(sql):
        return _multi_exam_score_rows()

    async def fake_schema():
        return _config_edu_mapping()

    orch = ReportOrchestrator(execute_sql=fake_execute, resolve_schema=fake_schema)
    spec = ReportSpec(
        report_type=ReportType.COMPREHENSIVE,
        filters={"class_name": "高一(1)班"},
    )
    res = _run(orch.run_spec(spec))
    assert res.error is None
    assert "期中" in res.html or "期末" in res.html


def test_orchestrator_class_overview_rank_info():
    from src.agent.education.report_types import ReportSpec

    async def fake_execute(sql):
        return {
            "columns": [
                "score", "exam_score", "class", "school_name", "district",
                "subject", "student_id", "student_name", "exam_name",
            ],
            "rows": [
                [90, 150, "高三(1)班", "扬州中学", "", "数学", "s1", "a", "统一"],
                [80, 150, "高三(1)班", "扬州中学", "", "数学", "s2", "b", "统一"],
                [70, 150, "高三(2)班", "扬州中学", "", "数学", "s3", "c", "统一"],
                [60, 150, "高三(2)班", "扬州中学", "", "数学", "s4", "d", "统一"],
                [95, 150, "高三(3)班", "扬州中学", "", "数学", "s5", "e", "统一"],
            ],
            "row_count": 5,
        }

    async def fake_schema():
        return _config_edu_mapping()

    orch = ReportOrchestrator(execute_sql=fake_execute, resolve_schema=fake_schema)
    spec = ReportSpec(
        report_type=ReportType.CLASS_OVERVIEW,
        filters={
            "class_name": "高三(1)班",
            "school_name": "扬州中学",
            "subject": "数学",
            "exam_name": "统一",
        },
    )
    res = _run(orch.run_spec(spec))
    assert res.error is None
    assert "RANK_INFO" not in res.html
    assert "年级" in res.html or "对比范围" in res.html or "均分" in res.html


def test_orchestrator_config_edu_sql_includes_exam_name_and_student():
    captured = {}

    async def fake_execute(sql):
        captured["sql"] = sql
        return {
            "columns": ["score", "exam_score"],
            "rows": [[120, 150]],
            "row_count": 1,
        }

    async def fake_schema():
        return _config_edu_mapping()

    from src.agent.education.report_types import ReportSpec

    orch = ReportOrchestrator(execute_sql=fake_execute, resolve_schema=fake_schema)
    _run(
        orch.run_spec(
            ReportSpec(
                report_type=ReportType.STUDENT_PROFILE,
                filters={"class_name": "高一(1)班", "student_name": "张三"},
            )
        )
    )
    sql = captured["sql"]
    assert "exam_name" in sql
    assert "student_id AS student_name" in sql or "student_name" in sql
    assert "st.name" not in sql
    assert "LIMIT 5000" in sql


def test_orchestrator_locked_class_overrides_question_class():
    """Phase 4 权限联动：班主任只能看本班，问题里写别班也被锁定到本班。"""
    captured = {}

    async def fake_execute(sql):
        captured["sql"] = sql
        return {"columns": ["score"], "rows": [[80]], "row_count": 1}

    async def fake_schema():
        return _wide_mapping()

    orch = ReportOrchestrator(execute_sql=fake_execute, resolve_schema=fake_schema)
    # 问题里写"初三2班"，但 locked_class 锁定到"初三1班"
    res = _run(orch.run("生成初三2班期中成绩分析报告", locked_class="初三1班"))
    assert "初三1班" in res.html
    assert "初三2班" not in res.html
    assert "初三1班" in captured["sql"]
    assert "初三2班" not in captured["sql"]


def _config_edu_mapping() -> ScoreSchemaMapping:
    bundle = load_schema_from_config()
    assert bundle is not None
    return bundle.mapping


def test_orchestrator_config_edu_sql_includes_school_and_exam_score():
    captured = {}

    async def fake_execute(sql):
        captured["sql"] = sql
        return {
            "columns": ["score", "exam_score"],
            "rows": [[120, 150], [135, 150]],
            "row_count": 2,
        }

    async def fake_schema():
        return _config_edu_mapping()

    orch = ReportOrchestrator(execute_sql=fake_execute, resolve_schema=fake_schema)
    res = _run(
        orch.run("分析【南京市第一中学】【高一(1)班】数学成绩")
    )
    sql = captured["sql"]
    assert "tb_school" in sql
    assert "sch.name" in sql
    assert "exam_score" in sql
    assert "南京市第一中学" in sql
    assert res.stats["full_score"] == 150
    assert res.stats["pass_rate"] == 100.0


def test_orchestrator_subject_diagnosis_includes_item_table():
    calls: list[str] = []

    async def fake_execute(sql):
        calls.append(sql)
        if "tb_knowledge" in sql and "knowledge_name" in sql and "GROUP BY" in sql:
            return {
                "columns": ["knowledge_name", "question_count", "score_rate"],
                "rows": [["函数", 3, 55.0], ["集合", 2, 82.0]],
                "row_count": 2,
            }
        if "tb_score_detail" in sql:
            return {
                "columns": ["question_no", "knowledge_name", "full_score", "avg_score", "score_rate"],
                "rows": [[1, "集合", 5, 4.0, 80.0]],
                "row_count": 1,
            }
        return {
            "columns": ["score", "exam_score"],
            "rows": [[120, 150]],
            "row_count": 1,
        }

    async def fake_schema():
        return _config_edu_mapping()

    orch = ReportOrchestrator(execute_sql=fake_execute, resolve_schema=fake_schema)
    res = _run(orch.run("南京市第一中学数学科目诊断，细化到每一小题"))
    assert any("tb_score_detail" in s for s in calls)
    assert any("knowledge_name" in s and "GROUP BY" in s for s in calls)
    assert any("tb_exam_question_knowledge" in s for s in calls)
    assert any("w_norm" in s for s in calls)
    assert "知识点" in res.html
    assert "函数" in res.html
    assert "需加强" in res.html or "薄弱" in res.html
