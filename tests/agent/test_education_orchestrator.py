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
    """Phase 4：7 类 ReportType 全部已实现模板，编排器不再有'未实现'空缺。"""
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
    assert "知识点" in res.html
    assert "函数" in res.html
    assert "需加强" in res.html or "薄弱" in res.html
