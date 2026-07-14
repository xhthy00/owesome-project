"""教育学情领域工具与模板单元测试。

覆盖 Phase 1 落地的 4 个工具 + 统计/图表/模板选择/数据归一化纯函数：
- ``compute_score_stats`` 边界（空数据、单值、分数段边界）；
- ``resolve_score_schema`` 宽表/分表/mock schema；
- ``build_chart_option_tool`` 4 类图表 + 未知类型兜底；
- ``select_report_template_tool`` 已实现/未实现类型；
- ``normalize_rows`` 宽表拆行 vs 分表直映；
- 模板通过 ``render_html_report`` 渲染含 ECharts JSON 占位符。
"""

from __future__ import annotations

import asyncio

import pytest

from src.agent.education.config import EducationConfig, load_config
from src.agent.education.data_adapter import normalize_rows
from src.agent.education.report_types import Audience, ReportType
from src.agent.education.schema_mapping import (
    ScoreSchemaMapping,
    infer_normalized_mapping,
    infer_wide_mapping,
    load_schema_from_config,
    validate_mapping_against_schema,
)
from src.agent.education.stats import compute_score_stats
from src.agent.education.templates import select_report_template
from src.agent.education.tools import (
    build_chart_option_tool,
    build_comprehensive_report_data_tool,
    compute_rankings_tool,
    compute_score_stats_tool,
    identify_at_risk_students_tool,
    resolve_score_schema,
    select_report_template_tool,
)
from src.agent.resource.tool import business as biz
from src.agent.resource.tool.business import render_html_report


def _run(coro):
    return asyncio.run(coro)


# ---- stats ----------------------------------------------------------------

def test_compute_score_stats_empty_returns_placeholder():
    cfg = EducationConfig()
    stats = compute_score_stats([], cfg)
    assert stats["count"] == 0
    assert stats["avg"] is None
    assert stats["pass_rate"] is None
    assert stats["segments"] == [] or all(s["count"] == 0 for s in stats["segments"])


def test_compute_score_stats_basic_rates_and_segments():
    cfg = EducationConfig(pass_threshold=60, excellent_threshold=85)
    stats = compute_score_stats([100, 90, 84, 70, 60, 55, 40], cfg, full_score=100)
    assert stats["count"] == 7
    assert stats["avg"] == pytest.approx(71.29, abs=0.1)
    assert stats["pass_rate"] == pytest.approx(71.43, abs=0.01)
    assert stats["excellent_rate"] == pytest.approx(28.57, abs=0.01)
    assert stats["min"] == 40
    assert stats["max"] == 100
    # 7 人应被分数段完整覆盖
    assert sum(s["count"] for s in stats["segments"]) == 7


def test_compute_score_stats_custom_thresholds_override():
    cfg = EducationConfig(pass_threshold=50, excellent_threshold=90)
    stats = compute_score_stats([55, 60, 89, 95], cfg)
    assert stats["pass_rate"] == 100.0
    assert stats["excellent_rate"] == 25.0


def test_compute_score_stats_with_dynamic_full_score_150():
    cfg = EducationConfig(pass_ratio=0.6, excellent_ratio=0.85)
    stats = compute_score_stats([91, 100, 120], cfg, full_score=150)
    assert stats["pass_rate"] == 100.0
    assert stats["full_score"] == 150
    stats2 = compute_score_stats([85, 91, 100], cfg, full_score=150)
    assert stats2["pass_rate"] == pytest.approx(66.67, abs=0.1)


def test_compute_score_stats_tool_reads_exam_score_column():
    result = _run(
        compute_score_stats_tool.execute(
            rows=[[91, 150], [100, 150], [120, 150]],
            columns=["score", "exam_score"],
            score_field="score",
            full_score_field="exam_score",
        )
    )
    assert result.data["full_score"] == 150
    assert result.data["pass_rate"] == 100.0
    assert not result.data.get("warnings")


def test_compute_score_stats_tool_warns_without_exam_score():
    result = _run(
        compute_score_stats_tool.execute(
            rows=[[60], [70]],
            columns=["score"],
            score_field="score",
        )
    )
    assert result.data["full_score"] == 100
    assert result.data.get("warnings")


# ---- config ---------------------------------------------------------------

def test_load_config_env_overrides(monkeypatch):
    monkeypatch.setenv("EDU_PASS_THRESHOLD", "50")
    monkeypatch.setenv("EDU_EXCELLENT_THRESHOLD", "90")
    cfg = load_config()
    assert cfg.pass_threshold == 50.0
    assert cfg.excellent_threshold == 90.0


def test_load_config_default_when_no_env():
    cfg = load_config()
    assert cfg.pass_threshold == 60.0
    assert cfg.excellent_threshold == 85.0


# ---- schema mapping -------------------------------------------------------

def test_infer_wide_mapping_finds_subject_columns():
    fields = [
        {"name": "id", "comment": "学号"},
        {"name": "name", "comment": "姓名"},
        {"name": "cls", "comment": "班级"},
        {"name": "math", "comment": "数学成绩"},
        {"name": "chinese", "comment": "语文成绩"},
    ]
    m = infer_wide_mapping("student_score", fields)
    assert m.mode == "wide"
    assert m.subject_columns.get("数学") == "math"
    assert m.subject_columns.get("语文") == "chinese"
    assert m.fields["student_name"] == "name"
    assert m.fields["class_name"] == "cls"


def test_infer_normalized_mapping_requires_student_and_score_tables():
    schema = [
        {"name": "student", "comment": "学生", "fields": [{"name": "id"}, {"name": "name"}]},
        {"name": "score", "comment": "成绩", "fields": [{"name": "subject"}, {"name": "score"}]},
    ]
    m = infer_normalized_mapping(schema)
    assert m is not None
    assert m.mode == "normalized"
    assert m.tables["student"] == "student"
    assert m.tables["score"] == "score"
    assert m.fields["subject"] == "subject"


def test_infer_normalized_mapping_returns_none_when_missing_score_table():
    schema = [{"name": "student", "comment": "学生", "fields": []}]
    assert infer_normalized_mapping(schema) is None


# ---- data adapter ---------------------------------------------------------

def test_normalize_rows_wide_splits_one_row_per_subject():
    mapping = ScoreSchemaMapping(
        mode="wide",
        table="student_score",
        subject_columns={"数学": "math", "语文": "chinese"},
        fields={"student_id": "id", "student_name": "name", "class_name": "cls"},
    )
    rows = [{"id": 1, "name": "张三", "cls": "初三1班", "math": 85, "chinese": 78}]
    out = normalize_rows(rows, mapping)
    assert len(out) == 2
    subjects = {r.subject for r in out}
    assert subjects == {"数学", "语文"}
    assert all(r.student_name == "张三" for r in out)


def test_normalize_rows_normalized_maps_directly():
    mapping = ScoreSchemaMapping(
        mode="normalized",
        tables={"student": "student", "score": "score"},
        fields={"student_id": "sid", "student_name": "sname", "subject": "subject", "score": "score"},
    )
    rows = [{"sid": 1, "sname": "李四", "subject": "数学", "score": 92}]
    out = normalize_rows(rows, mapping)
    assert len(out) == 1
    assert out[0].student_name == "李四"
    assert out[0].score == 92.0
    assert out[0].subject == "数学"


# ---- tools: compute_score_stats_tool --------------------------------------

def test_compute_score_stats_tool_with_scores_list():
    result = _run(compute_score_stats_tool.execute(scores=[80, 70, 60, 50]))
    assert result.data["count"] == 4
    assert result.data["pass_rate"] == 75.0
    assert "及格率" in result.content


def test_compute_score_stats_tool_with_rows_and_columns():
    result = _run(
        compute_score_stats_tool.execute(
            rows=[["张三", 85], ["李四", 55], ["王五", 60]],
            columns=["name", "score"],
            score_field="score",
        )
    )
    assert result.data["count"] == 3
    assert result.data["pass_rate"] == pytest.approx(66.67, abs=0.01)


def test_compute_score_stats_tool_with_exec_result():
    result = _run(
        compute_score_stats_tool.execute(
            exec_result={
                "columns": ["score", "exam_score"],
                "rows": [[91, 150], [100, 150]],
            },
        )
    )
    assert result.data["count"] == 2
    assert result.data["full_score"] == 150


def test_compute_score_stats_tool_uses_full_last_exec_result_over_preview():
    full_rows = [[80 + i, 150] for i in range(40)]
    preview_rows = full_rows[:20]
    result = _run(
        compute_score_stats_tool.execute(
            exec_result={
                "columns": ["score", "exam_score"],
                "rows": preview_rows,
                "row_count": 20,
            },
            tool_runtime_ctx={
                "last_exec_result": {
                    "columns": ["score", "exam_score"],
                    "rows": full_rows,
                    "row_count": 40,
                }
            },
        )
    )
    assert result.data["count"] == 40
    assert "共 40 人" in result.content


def test_compute_score_stats_tool_ignores_short_scores_when_full_exec_available():
    full_rows = [[80 + i, 150] for i in range(40)]
    short_scores = [float(r[0]) for r in full_rows[:20]]
    result = _run(
        compute_score_stats_tool.execute(
            scores=short_scores,
            tool_runtime_ctx={
                "last_exec_result": {
                    "columns": ["score", "exam_score"],
                    "rows": full_rows,
                    "row_count": 40,
                }
            },
        )
    )
    assert result.data["count"] == 40


def test_compute_score_stats_tool_auto_score_field_from_dict_rows():
    result = _run(
        compute_score_stats_tool.execute(
            rows=[{"name": "a", "score": 80}, {"name": "b", "score": 70}],
        )
    )
    assert result.data["count"] == 2


def test_compute_score_stats_tool_missing_input_returns_error_not_raises():
    result = _run(compute_score_stats_tool.execute())
    assert "error" in result.data
    assert "失败" in result.content


def test_compute_score_stats_tool_bad_score_field():
    result = _run(
        compute_score_stats_tool.execute(
            rows=[[1, 2]],
            columns=["a", "b"],
            score_field="missing",
        )
    )
    assert result.data["error"] == "score_field not found"


# ---- tools: build_chart_option_tool ---------------------------------------

def test_build_chart_option_tool_score_distribution():
    stats = compute_score_stats([85, 60, 55, 100], EducationConfig())
    result = _run(
        build_chart_option_tool.execute(
            chart_type="score_distribution",
            data={"segments": stats["segments"], "pass_rate": stats["pass_rate"]},
            title="分数段",
        )
    )
    assert result.data["option"]
    assert "score_distribution" in result.data["chart_type"]
    # option 应是合法 JSON
    import json
    parsed = json.loads(result.data["option"])
    assert parsed["series"][0]["type"] == "bar"


def test_build_chart_option_tool_bar_alias_resolves_to_knowledge_bar():
    result = _run(
        build_chart_option_tool.execute(
            chart_type="bar",
            data={"categories": ["集合", "函数"], "values": [80.0, 55.0]},
            title="知识点得分率",
        )
    )
    assert result.data["option"]
    assert result.data["chart_type"] == "knowledge_bar"


def test_build_chart_option_tool_unknown_type_returns_empty_option():
    result = _run(build_chart_option_tool.execute(chart_type="nope", data={}))
    assert result.data["option"] == ""
    assert "error" in result.data


# ---- tools: select_report_template_tool -----------------------------------

def test_select_report_template_tool_implemented_type():
    result = _run(select_report_template_tool.execute(report_type="class_overview"))
    assert result.data["template_name"] == "education/class_overview.html"
    assert "REPORT_TITLE" in result.data["data_keys"]


def test_select_report_template_tool_unknown_report_type():
    # 全部 9 类 ReportType 已实现；未知类型应返回 error
    result = _run(select_report_template_tool.execute(report_type="bogus"))
    assert result.data["error"] == "unknown report_type"


def test_select_report_template_tool_unknown_report_type():
    result = _run(select_report_template_tool.execute(report_type="bogus"))
    assert result.data["error"] == "unknown report_type"


# ---- templates registry ---------------------------------------------------

def test_select_report_template_parent_audience_suffix():
    info = select_report_template(ReportType.CLASS_OVERVIEW, Audience.PARENT)
    # Phase 1 未实现 parent 变体文件，但路径应含 _parent 后缀
    assert "_parent" in info["template_name"]


# ---- tools: resolve_score_schema ------------------------------------------

def test_resolve_score_schema_wide_fallback(monkeypatch):
    monkeypatch.setattr(
        "src.agent.education.tools.load_schema_from_config",
        lambda *_a, **_kw: None,
    )
    schema = [
        {"name": "student_score", "comment": "学生考试成绩", "fields": [
            {"name": "id", "comment": "学号"},
            {"name": "name", "comment": "姓名"},
            {"name": "math", "comment": "数学成绩"},
        ]},
    ]

    def fake_load(datasource_id, workspace_oid=None):
        return "pg", {}, "ds-test"

    def fake_schema(db_type, config):
        return schema

    monkeypatch.setattr(biz, "_load_datasource", fake_load)
    monkeypatch.setattr("src.datasource.db.db.get_schema_info", fake_schema)

    result = _run(resolve_score_schema.execute(datasource_id=1, question="初三1班成绩"))
    assert result.data["mode"] == "wide"
    assert result.data["table"] == "student_score"
    assert result.data["subject_columns"].get("数学") == "math"


def test_resolve_score_schema_normalized_preferred(monkeypatch):
    monkeypatch.setattr(
        "src.agent.education.tools.load_schema_from_config",
        lambda *_a, **_kw: None,
    )
    schema = [
        {"name": "student", "comment": "学生", "fields": [{"name": "id"}, {"name": "name"}]},
        {"name": "score", "comment": "成绩", "fields": [{"name": "subject"}, {"name": "score"}]},
        {"name": "exam", "comment": "考试", "fields": [{"name": "id"}, {"name": "exam_name"}]},
    ]

    def fake_load(datasource_id, workspace_oid=None):
        return "pg", {}, "ds-test"

    monkeypatch.setattr(biz, "_load_datasource", fake_load)
    monkeypatch.setattr("src.datasource.db.db.get_schema_info", lambda *_a, **_kw: schema)

    result = _run(resolve_score_schema.execute(datasource_id=1))
    assert result.data["mode"] == "normalized"
    assert result.data["tables"]["student"] == "student"


def test_resolve_score_schema_datasource_failure_returns_error(monkeypatch):
    def boom(datasource_id, workspace_oid=None):
        raise ValueError("datasource not found")

    monkeypatch.setattr(biz, "_load_datasource", boom)
    result = _run(resolve_score_schema.execute(datasource_id=999))
    assert result.data["error"]


def test_load_schema_from_config_returns_tb_mapping():
    bundle = load_schema_from_config()
    assert bundle is not None
    assert bundle.mapping.source == "config_edu"
    assert bundle.mapping.tables["score"] == "tb_score"
    assert bundle.mapping.fields["full_score"] == "sc.exam_score"
    assert bundle.meta.pass_ratio == 0.6


def test_validate_mapping_against_schema_warns_missing_tables():
    bundle = load_schema_from_config()
    assert bundle is not None
    warnings = validate_mapping_against_schema(
        bundle.mapping,
        [{"name": "student"}, {"name": "score"}],
    )
    assert any("tb_score" in w or "tb_school" in w for w in warnings)


def test_resolve_score_schema_config_preferred_over_infer(monkeypatch):
    schema = [
        {"name": "student", "comment": "学生", "fields": [{"name": "id"}]},
        {"name": "score", "comment": "成绩", "fields": [{"name": "score"}]},
    ]

    def fake_load(datasource_id, workspace_oid=None):
        return "pg", {}, "ds-test"

    monkeypatch.setattr(biz, "_load_datasource", fake_load)
    monkeypatch.setattr("src.datasource.db.db.get_schema_info", lambda *_a, **_kw: schema)

    result = _run(resolve_score_schema.execute(datasource_id=1))
    assert result.data["source"] == "config_edu"
    assert result.data["tables"]["score"] == "tb_score"
    assert result.data["tables"]["school"] == "tb_school"


def test_resolve_score_schema_config_warns_missing_table(monkeypatch):
    schema = [{"name": "tb_score", "fields": []}]

    def fake_load(datasource_id, workspace_oid=None):
        return "pg", {}, "ds-test"

    monkeypatch.setattr(biz, "_load_datasource", fake_load)
    monkeypatch.setattr("src.datasource.db.db.get_schema_info", lambda *_a, **_kw: schema)

    result = _run(resolve_score_schema.execute(datasource_id=1))
    assert result.data["source"] == "config_edu"
    assert result.data.get("warnings")
    assert "tb_school" in result.content or any("tb_school" in w for w in result.data["warnings"])


# ---- template render via render_html_report -------------------------------

def test_class_overview_template_renders_with_chart_json():
    data = {
        "REPORT_TITLE": "初三1班期中成绩分析报告",
        "REPORT_SUBTITLE": "班级学情总览",
        "REPORT_TIME": "2026-07-01",
        "CLASS_NAME": "初三1班", "EXAM_NAME": "期中考试",
        "TOTAL_COUNT": "45", "AVG_SCORE": "78.5", "PASS_RATE": "82.2",
        "EXCELLENT_RATE": "24.4", "STDEV": "12.3",
        "SCORE_DIST_CHART": '{"xAxis":{"type":"category","data":["0-60"]},"yAxis":{"type":"value"},"series":[{"type":"bar","data":[3]}]}',
        "SUBJECT_RADAR_CHART": '{"radar":{"indicator":[{"name":"数学","max":100}]},"series":[{"type":"radar","data":[{"value":[78]}]}]}',
        "SUBJECT_BREAKDOWN": "<table class=\"edu-table\"><tr><th>科目</th></tr></table>",
        "RANK_INFO": "<p>年级第 3 / 8</p>",
        "SUMMARY": "<p>稳健</p>",
        "RECOMMENDATIONS": "<ul><li>加强数学</li></ul>",
    }
    result = _run(
        render_html_report.execute(
            datasource_id=1,
            template_name="education/class_overview.html",
            data=data,
            title="班级总览",
        )
    )
    assert result.data["mode"] == "template"
    html = result.data["html"]
    assert "初三1班" in html
    assert "edu-container" in html
    # ECharts JSON 应原样嵌入 <script type="application/json">
    assert "application/json" in html
    assert "0-60" in html


def test_education_subdir_template_path_traversal_rejected():
    # 模板名仅允许 [A-Za-z0-9_./-]；含 .. 仍应被 _resolve 的 relative_to 拦截
    result = _run(
        render_html_report.execute(
            datasource_id=1,
            template_name="education/../../config/education.json",
            html="<p>fallback</p>",
        )
    )
    # 路径越界 → 模板失败 → 因提供了 inline html 回退到 inline
    assert result.data["mode"] == "inline"


# ---- Phase 2: compute_rankings / identify_at_risk_students -----------------

def test_compute_rankings_tool_orders_and_percentile():
    result = _run(
        compute_rankings_tool.execute(
            items=[
                {"name": "初三1班", "value": 78.5},
                {"name": "初三2班", "value": 82.1},
                {"name": "初三3班", "value": 78.5},
            ],
            value_key="value",
            name_key="name",
        )
    )
    ranking = result.data["ranking"]
    assert ranking[0]["name"] == "初三2班"
    assert ranking[0]["rank"] == 1
    assert ranking[0]["percentile"] == 100.0
    # 同值并列：1 班与 3 班均 78.5 → rank=2
    assert ranking[1]["rank"] == 2
    assert ranking[2]["rank"] == 2
    assert sum(s.get("value", 0) for s in ranking) == pytest.approx(239.1, abs=0.1)


def test_compute_rankings_tool_empty_returns_error():
    result = _run(compute_rankings_tool.execute(items=[]))
    assert result.data["error"] == "empty items"


def test_compute_rankings_tool_single_item_percentile_100():
    result = _run(compute_rankings_tool.execute(items=[{"name": "a", "value": 10}]))
    assert result.data["ranking"][0]["percentile"] == 100.0
    assert result.data["ranking"][0]["rank"] == 1


def test_identify_at_risk_students_tool_categories():
    result = _run(
        identify_at_risk_students_tool.execute(
            students=[
                {"name": "张三", "subject": "数学", "score": 58, "prev_score": 80},
                {"name": "张三", "subject": "语文", "score": 92},
                {"name": "李四", "subject": "数学", "score": 72},
            ]
        )
    )
    # 58 分处于 [55, 65) → 临界生
    assert any(s["name"] == "张三" for s in result.data["critical"])
    # 80 → 58 降幅 -22 ≤ -10 → 大幅退步
    assert any(s["name"] == "张三" for s in result.data["regression"])
    # 张三 92 vs 58，分差 34 且低分 < 60 → 偏科
    assert any(s["name"] == "张三" for s in result.data["imbalanced"])


def test_identify_at_risk_students_tool_empty_returns_error():
    result = _run(identify_at_risk_students_tool.execute(students=[]))
    assert result.data["error"] == "empty students"


def test_identify_at_risk_students_tool_custom_thresholds():
    # 把退步阈值放宽到 -30，则 -22 不再算大幅退步
    result = _run(
        identify_at_risk_students_tool.execute(
            students=[{"name": "王五", "subject": "数学", "score": 58, "prev_score": 80}],
            regression_threshold=-30,
        )
    )
    assert result.data["regression"] == []


def test_build_tier_alert_report_data_tool_renders_and_scales_pass_line():
    from src.agent.education.tools import build_tier_alert_report_data_tool

    # 150 分卷：及格线 90；88 落在 [85, 95) → 临界生；勿按默认 60 误判
    result = _run(
        build_tier_alert_report_data_tool.execute(
            students=[
                {"name": "临界生甲", "subject": "数学", "score": 88},
                {"name": "退步乙", "subject": "数学", "score": 70, "prev_score": 95},
                {"name": "偏科丙", "subject": "数学", "score": 50},
                {"name": "偏科丙", "subject": "语文", "score": 95},
            ],
            score_rows=[
                {"name": "临界生甲", "subject": "数学", "score": 88, "exam_score": 150},
            ],
            class_name="高三(10)班",
            school_name="扬州中学",
            subject_name="数学",
            exam_name="联考",
            full_score=150,
            render=True,
        )
    )
    assert result.is_final is True
    assert result.data.get("output_type") == "html"
    html = result.data.get("html") or ""
    assert "分层预警" in html
    assert "临界生甲" in html
    assert "退步乙" in html
    assert "偏科丙" in html


def test_build_tier_alert_report_data_tool_from_upstream_report_data():
    from src.agent.education.tools import build_tier_alert_report_data_tool

    report_data = {
        "sub_tasks": [
            {
                "sub_task_agent": "DataAnalyst",
                "exec_result": {
                    "columns": ["姓名", "score", "exam_score", "prev_score", "subject"],
                    "rows": [
                        ["张三", 58, 100, 80, "数学"],
                        ["李四", 92, 100, None, "数学"],
                    ],
                },
            }
        ]
    }
    result = _run(
        build_tier_alert_report_data_tool.execute(
            class_name="初三1班",
            subject_name="数学",
            render=False,
            report_data=report_data,
        )
    )
    assert "error" not in (result.data or {})
    assert result.data["CRITICAL_COUNT"] == "1"
    assert any(s["name"] == "张三" for s in result.data["critical"])
    assert any(s["name"] == "张三" for s in result.data["regression"])


# ---- Phase 2: trend_line chart + new templates ----------------------------

def test_build_chart_option_tool_trend_line():
    result = _run(
        build_chart_option_tool.execute(
            chart_type="trend_line",
            data={"x_labels": ["月考1", "期中"], "series": [{"name": "数学", "values": [78, 72]}], "pass_line": 60},
            title="数学趋势",
        )
    )
    import json
    parsed = json.loads(result.data["option"])
    assert parsed["series"][0]["type"] == "line"
    assert parsed["series"][0]["markLine"]["data"][0]["yAxis"] == 60


def test_select_report_template_tool_phase2_types():
    for rt in ["student_profile", "trend_tracking", "tier_alert"]:
        result = _run(select_report_template_tool.execute(report_type=rt))
        assert result.data["template_name"].startswith("education/")
        assert result.data["data_keys"]


def test_student_subject_diagnosis_template_renders():
    from src.agent.resource.tool.business import _render_template_html

    data = {
        "REPORT_TITLE": "STU20240003 数学学情分析报告",
        "REPORT_SUBTITLE": "南京市第一中学 高一(1)班",
        "REPORT_TIME": "2026-07-10",
        "STUDENT_NAME": "STU20240003",
        "SUBJECT_NAME": "数学",
        "CLASS_NAME": "高一(1)班",
        "EXAM_NAME": "期末质量检测",
        "TOTAL_SCORE": "91",
        "FULL_SCORE": "100",
        "CLASS_RANK": "3",
        "GRADE_RANK": "12",
        "ABILITY_INSIGHT": "<p>「综合应用」层级需重点突破。</p>",
        "ABILITY_TIER_CHART": '{"radar":{"indicator":[{"name":"基础知识","max":100}]},"series":[{"type":"radar","data":[{"value":[85]}]}]}',
        "ABILITY_TIER_TABLE": "<table class='edu-table'><tr><th>层级</th></tr></table>",
        "QUESTION_TYPE_CHART": '{"xAxis":{"type":"category","data":["选择"]},"series":[{"type":"bar","data":[80]}]}',
        "QUESTION_TYPE_TABLE": "<table class='edu-table'><tr><th>题型</th></tr></table>",
        "KNOWLEDGE_CHART": '{"xAxis":{"type":"category","data":["集合"]},"series":[{"type":"bar","data":[90]}]}',
        "ITEM_TABLE": "<table class='edu-table'><tr><th>题号</th></tr></table>",
        "KNOWLEDGE_TABLE": "<table class='edu-table'><tr><th>知识点</th></tr></table>",
        "WEAK_KNOWLEDGE_LIST": "分段函数",
        "SUMMARY": "<p>个人诊断</p>",
        "RECOMMENDATIONS": "<p>加强练习</p>",
    }
    html = _render_template_html("education/student_subject_diagnosis.html", data)
    assert "能力画像" in html
    assert "各科目表现" not in html
    assert "历次成绩趋势" not in html
    assert "STU20240003" in html
    assert ".edu-rec-card" in html
    assert ".edu-rec-priority" in html


def test_student_profile_template_renders():
    data = {
        "REPORT_TITLE": "张三学情报告", "REPORT_SUBTITLE": "个体分析",
        "REPORT_TIME": "2026-07-01", "STUDENT_NAME": "张三",
        "CLASS_NAME": "初三1班", "EXAM_NAME": "期中考试",
        "TOTAL_SCORE": "650", "CLASS_RANK": "5", "GRADE_RANK": "32",
        "SUBJECT_RADAR_CHART": '{"radar":{"indicator":[{"name":"数学","max":100}]},"series":[{"type":"radar","data":[{"value":[78]}]}]}',
        "SUBJECT_TABLE": "<table class=\"edu-table\"><tr><th>科目</th></tr></table>",
        "TREND_LINE_CHART": '{"xAxis":{"type":"category","data":["月考1"]},"yAxis":{"type":"value"},"series":[{"type":"line","data":[78]}]}',
        "SUMMARY": "<p>数学薄弱</p>", "RECOMMENDATIONS": "<ul><li>补数学</li></ul>",
    }
    result = _run(
        render_html_report.execute(
            datasource_id=1,
            template_name="education/student_profile.html",
            data=data,
            title="个体报告",
        )
    )
    assert result.data["mode"] == "template"
    assert "张三" in result.data["html"]


def test_trend_tracking_template_renders():
    data = {
        "REPORT_TITLE": "张三数学趋势", "REPORT_SUBTITLE": "历次追踪",
        "REPORT_TIME": "2026-07-01", "TARGET_NAME": "张三", "SUBJECT_NAME": "数学",
        "TREND_CHART": '{"xAxis":{"type":"category","data":["月考1","期中"]},"yAxis":{"type":"value"},"series":[{"type":"line","data":[78,72]}]}',
        "TREND_TABLE": "<table class=\"edu-table\"><tr><th>考试</th></tr></table>",
        "CHANGE_INFO": "<p>退步 6 分</p>",
        "SUMMARY": "<p>波动</p>", "RECOMMENDATIONS": "<p>加强</p>",
    }
    result = _run(
        render_html_report.execute(
            datasource_id=1, template_name="education/trend_tracking.html", data=data, title="趋势报告"
        )
    )
    assert "张三" in result.data["html"]


def test_tier_alert_template_renders():
    data = {
        "REPORT_TITLE": "分层预警报告", "REPORT_SUBTITLE": "初三1班",
        "REPORT_TIME": "2026-07-01", "SCOPE": "初三1班", "EXAM_NAME": "期中考试",
        "CRITICAL_COUNT": "3", "REGRESSION_COUNT": "2", "IMBALANCED_COUNT": "1",
        "CRITICAL_TABLE": "<table class=\"edu-table\"><tr><th>姓名</th></tr></table>",
        "REGRESSION_TABLE": "<p>无</p>", "IMBALANCED_TABLE": "<p>无</p>",
        "SUMMARY": "<p>需关注</p>", "RECOMMENDATIONS": "<p>分层辅导</p>",
    }
    result = _run(
        render_html_report.execute(
            datasource_id=1, template_name="education/tier_alert.html", data=data, title="预警报告"
        )
    )
    assert "分层预警" in result.data["html"]
    assert "临界生" in result.data["html"]


def test_student_profile_parent_template_renders():
    data = {
        "REPORT_TITLE": "张三学情报告", "REPORT_SUBTITLE": "家长版",
        "REPORT_TIME": "2026-07-01", "STUDENT_NAME": "张三",
        "CLASS_NAME": "初三1班", "EXAM_NAME": "期中考试",
        "TOTAL_SCORE": "650", "CLASS_RANK": "5", "GRADE_RANK": "32",
        "SUBJECT_RADAR_CHART": '{}', "SUBJECT_TABLE": "<p>分科</p>",
        "TREND_LINE_CHART": '{}',
        "SUMMARY": "<p>孩子数学需加强</p>", "RECOMMENDATIONS": "<p>每日练习</p>",
    }
    result = _run(
        render_html_report.execute(
            datasource_id=1,
            template_name="education/student_profile_parent.html",
            data=data, title="家长版",
        )
    )
    assert "老师的话" in result.data["html"]
    assert "家庭配合建议" in result.data["html"]


def test_group_feature_template_renders():
    data = {
        "REPORT_TITLE": "男女生数学对比报告", "REPORT_SUBTITLE": "群体特征",
        "REPORT_TIME": "2026-07-01", "SCOPE": "初三1班", "EXAM_NAME": "期中考试",
        "GROUP_DIMENSION": "性别",
        "GROUP_COMPARE_CHART": '{"xAxis":{"type":"category","data":["男","女"]},"yAxis":{"type":"value"},"series":[{"type":"bar","data":[78,82]}]}',
        "GROUP_TABLE": "<table class=\"edu-table\"><tr><th>组</th><th>均分</th></tr><tr><td>男</td><td>78</td></tr></table>",
        "DIFF_INFO": "<p>女生均分高 4 分</p>",
        "SUMMARY": "<p>性别差异在可接受范围</p>",
        "RECOMMENDATIONS": "<p>关注男生薄弱项</p>",
    }
    result = _run(
        render_html_report.execute(
            datasource_id=1, template_name="education/group_feature.html", data=data, title="群体特征"
        )
    )
    assert "男女生数学对比" in result.data["html"]
    assert "分组对比" in result.data["html"]


def test_build_chart_option_tool_group_compare_bar():
    result = _run(
        build_chart_option_tool.execute(
            chart_type="group_compare_bar",
            data={"groups": ["男", "女"], "metrics": [{"name": "均分", "values": [78, 82]}]},
            title="性别对比",
        )
    )
    import json
    parsed = json.loads(result.data["option"])
    assert parsed["series"][0]["type"] == "bar"
    assert parsed["xAxis"]["data"] == ["男", "女"]


def test_select_report_template_tool_group_feature_now_implemented():
    result = _run(select_report_template_tool.execute(report_type="group_feature"))
    assert result.data["template_name"] == "education/group_feature.html"
    assert "GROUP_COMPARE_CHART" in result.data["data_keys"]


def test_select_report_template_tool_comprehensive():
    result = _run(select_report_template_tool.execute(report_type="comprehensive"))
    assert result.data["template_name"] == "education/comprehensive.html"
    keys = result.data["data_keys"]
    assert "COVER_TITLE" in keys
    assert "STUDENT_ARCHIVE_TABLE" in keys
    assert "CORRELATION_CHART" in keys
    assert "TRAJECTORY_CHART" in keys


# ---- 综合报告图表类型 ------------------------------------------------------


def test_build_chart_option_tool_pie():
    result = _run(
        build_chart_option_tool.execute(
            chart_type="pie",
            data={"items": [{"name": "进步", "value": 10, "color": "#2ecc71"},
                            {"name": "退步", "value": 5, "color": "#e74c3c"}]},
            title="趋势分布",
        )
    )
    import json
    parsed = json.loads(result.data["option"])
    assert parsed["series"][0]["type"] == "pie"
    assert parsed["series"][0]["data"][0]["name"] == "进步"


def test_build_chart_option_tool_correlation_bar():
    result = _run(
        build_chart_option_tool.execute(
            chart_type="correlation_bar",
            data={"subjects": ["语文", "数学", "英语"],
                  "series": [{"name": "第一次", "values": [0.56, -0.22, 0.76]}]},
            title="相关性",
        )
    )
    import json
    parsed = json.loads(result.data["option"])
    assert parsed["yAxis"]["min"] == -1
    assert parsed["series"][0]["data"] == [0.56, -0.22, 0.76]


def test_build_chart_option_tool_progress_regress_bar():
    result = _run(
        build_chart_option_tool.execute(
            chart_type="progress_regress_bar",
            data={"items": [{"name": "s1", "value": 30, "color": "#2ecc71"},
                            {"name": "s2", "value": -20, "color": "#e74c3c"}]},
            title="进退步",
        )
    )
    import json
    parsed = json.loads(result.data["option"])
    assert parsed["series"][0]["type"] == "bar"
    # 降序：s1(+30) 在前
    assert parsed["yAxis"]["data"][0] == "s1"


def test_build_chart_option_tool_trajectory_line():
    result = _run(
        build_chart_option_tool.execute(
            chart_type="trajectory_line",
            data={"x_labels": ["一", "二", "三"],
                  "series": [{"name": "s1", "values": [300, 310, 320]}]},
            title="总分轨迹",
        )
    )
    import json
    parsed = json.loads(result.data["option"])
    assert parsed["series"][0]["type"] == "line"
    assert parsed["xAxis"]["data"] == ["一", "二", "三"]


# ---- 综合统计纯函数 --------------------------------------------------------


def test_pearson_r_basic_and_degenerate():
    from src.agent.education.stats import pearson_r
    assert pearson_r([1, 2, 3], [2, 4, 6]) == 1.0
    assert pearson_r([1, 2, 3], [6, 4, 2]) == -1.0
    assert pearson_r([1], [2]) is None
    assert pearson_r([1, 1, 1], [1, 2, 3]) is None  # 零方差


def test_compute_correlations_returns_chart_shape():
    from src.agent.education.stats import compute_correlations
    records = [
        {"exam": "一", "student": "a", "subjects": {"语文": 80, "数学": 90}, "total": 170},
        {"exam": "一", "student": "b", "subjects": {"语文": 60, "数学": 95}, "total": 155},
        {"exam": "二", "student": "a", "subjects": {"语文": 85, "数学": 88}, "total": 173},
        {"exam": "二", "student": "b", "subjects": {"语文": 55, "数学": 99}, "total": 154},
    ]
    out = compute_correlations(records)
    assert out["exams"] == ["一", "二"]
    assert out["subjects"] == ["语文", "数学"]
    assert len(out["series"]) == 2
    assert out["series"][0]["name"] == "一"


def test_compute_level_distribution():
    from src.agent.education.stats import compute_level_distribution
    cfg = load_config()
    # 满分 300（三科×100），A≥255, B≥210, C≥180, D<180
    items = compute_level_distribution([260, 220, 190, 170], cfg, full_score=300)
    by_name = {it["name"]: it["value"] for it in items}
    assert by_name["A (优秀)"] == 1
    assert by_name["D (待提升)"] == 1


def test_compute_trend_distribution():
    from src.agent.education.stats import compute_trend_distribution
    deltas = [{"name": "a", "delta": 20}, {"name": "b", "delta": -15}, {"name": "c", "delta": 2}]
    out = compute_trend_distribution(deltas)
    assert len(out["progress"]) == 1
    assert len(out["regress"]) == 1
    assert len(out["stable"]) == 1
    assert sum(it["value"] for it in out["items"]) == 3


def test_compute_top_progress_regress():
    from src.agent.education.stats import compute_top_progress_regress
    deltas = [{"name": f"s{i}", "delta": d} for i, d in enumerate([30, 20, -5, -10, -25, 15])]
    out = compute_top_progress_regress(deltas, top_n=2)
    assert out["progress"][0]["name"] == "s0"
    assert out["regress"][0]["name"] == "s4"  # 退步最严重在前


def test_compute_imbalance_degree():
    from src.agent.education.stats import compute_imbalance_degree
    students = [
        {"name": "a", "subjects": {"语文": 130, "数学": 70, "英语": 125}},  # 强偏科
        {"name": "b", "subjects": {"语文": 100, "数学": 100, "英语": 100}},  # 均衡
    ]
    out = compute_imbalance_degree(students, top_n=5, min_degree=0.0)
    assert out[0]["name"] == "a"
    assert out[0]["strong_subject"] == "语文"
    assert out[0]["weak_subject"] == "数学"


def test_compute_subject_extremes():
    from src.agent.education.stats import compute_subject_extremes
    deltas = [
        {"name": "a", "subject": "英语", "delta": 25},
        {"name": "b", "subject": "英语", "delta": 18},
        {"name": "c", "subject": "数学", "delta": -22},
        {"name": "d", "subject": "数学", "delta": -15},
    ]
    out = compute_subject_extremes(deltas, top_n=2)
    assert out["progress"][0]["name"] == "a"
    assert out["regress"][0]["name"] == "c"


# ---- 综合报告数据组装工具 --------------------------------------------------


def _sample_records():
    return [
        {"exam": "第一次", "student": "s1", "subjects": {"语文": 80, "数学": 90, "英语": 70}, "total": 240},
        {"exam": "第一次", "student": "s2", "subjects": {"语文": 95, "数学": 60, "英语": 85}, "total": 240},
        {"exam": "第二次", "student": "s1", "subjects": {"语文": 85, "数学": 88, "英语": 75}, "total": 248},
        {"exam": "第二次", "student": "s2", "subjects": {"语文": 92, "数学": 55, "英语": 90}, "total": 237},
        {"exam": "第三次", "student": "s1", "subjects": {"语文": 90, "数学": 85, "英语": 80}, "total": 255},
        {"exam": "第三次", "student": "s2", "subjects": {"语文": 88, "数学": 50, "英语": 95}, "total": 233},
    ]


def test_build_comprehensive_report_data_tool_records_input():
    # render=False 检查 data 字典；render=True 由下一用例验证 HTML 载荷
    result = _run(
        build_comprehensive_report_data_tool.execute(
            records=_sample_records(),
            exam_order=["第一次", "第二次", "第三次"],
            class_name="初三1班",
            full_score=100,
            render=False,
        )
    )
    data = result.data
    assert data["COVER_TITLE"] == "初三1班综合分析报告"
    assert "第一次" in data["COVER_SUBTITLE"]
    # 核心图表字段非空（偏科/单科之最图在小样本下可能为空，属合理）
    for key in ("SUBJECT_TREND_CHART", "SUBJECT_COMPARE_CHART", "CORRELATION_CHART",
                "TREND_DIST_CHART", "LEVEL_DIST_CHART", "PROGRESS_REGRESS_CHART",
                "TRAJECTORY_CHART"):
        assert data[key], f"{key} should be non-empty"
    # 学生档案表含 s1
    assert "<strong>s1</strong>" in data["STUDENT_ARCHIVE_TABLE"]
    # KPI 网格含 stat-card
    assert "stat-card" in data["OVERVIEW_KPI_GRID"]


def test_build_comprehensive_report_data_tool_long_table_input():
    # 长表 rows：每行一次考试一名学生一科一分数
    rows = [
        ["第一次", "s1", "语文", 80], ["第一次", "s1", "数学", 90], ["第一次", "s1", "英语", 70],
        ["第一次", "s2", "语文", 95], ["第一次", "s2", "数学", 60], ["第一次", "s2", "英语", 85],
        ["第三次", "s1", "语文", 90], ["第三次", "s1", "数学", 85], ["第三次", "s1", "英语", 80],
        ["第三次", "s2", "语文", 88], ["第三次", "s2", "数学", 50], ["第三次", "s2", "英语", 95],
    ]
    result = _run(
        build_comprehensive_report_data_tool.execute(
            rows=rows,
            columns=["exam", "student", "subject", "score"],
            exam_field="exam",
            student_field="student",
            subject_field="subject",
            score_field="score",
            class_name="初三1班",
            full_score=100,
            render=False,
        )
    )
    data = result.data
    # total 由各科求和
    assert "stat-card" in data["OVERVIEW_KPI_GRID"]
    assert "<strong>s1</strong>" in data["STUDENT_ARCHIVE_TABLE"]


def test_build_comprehensive_report_data_tool_renders_html_payload():
    """render=True（默认）：工具直接返回 HTML 上报载荷，无需 LLM 再调 render_html_report。"""
    result = _run(
        build_comprehensive_report_data_tool.execute(
            records=_sample_records(),
            exam_order=["第一次", "第二次", "第三次"],
            class_name="初三1班",
            full_score=100,
        )
    )
    data = result.data
    assert data["output_type"] == "html"
    assert data["mode"] == "template"
    html = data["html"]
    assert "初三1班综合分析报告" in html
    assert "一、班级整体概览" in html
    assert "九、每位学生详细档案" in html
    assert "echarts" in html
    assert "<strong>s1</strong>" in html


def test_build_comprehensive_report_uses_full_last_exec_over_preview():
    """LLM 只抄 preview 20 行时，应改用 last_exec_result 全量（52 人 × 4 考 = 208）。"""
    exams = ["摸底", "调研", "联考", "一模"]
    full_rows = [
        [exams[e], f"学生{s:03d}", "数学", 80 + (s + e) % 40]
        for e in range(4)
        for s in range(1, 53)
    ]
    preview_records = [
        {"exam": "摸底", "student": f"学生{s:03d}", "subjects": {"数学": 80.0}, "total": 80.0}
        for s in range(1, 21)
    ]
    result = _run(
        build_comprehensive_report_data_tool.execute(
            records=preview_records,
            exam_order=exams,
            class_name="高三(10)班",
            full_score=150,
            render=False,
            tool_runtime_ctx={
                "last_exec_result": {
                    "columns": ["exam_name", "student_name", "subject", "score"],
                    "rows": full_rows,
                    "row_count": 208,
                }
            },
        )
    )
    assert "208 条记录" in result.content or "已自动改用完整 SQL" in result.content
    assert "4 次考试" in result.content
    html_data = result.data
    assert "摸底" in html_data["COVER_SUBTITLE"]
    assert "调研" in html_data["COVER_SUBTITLE"]
    assert "联考" in html_data["COVER_SUBTITLE"]
    assert "一模" in html_data["COVER_SUBTITLE"]
    # 档案表应含第 52 位学生，而非仅 preview 的 20 人
    assert "<strong>学生052</strong>" in html_data["STUDENT_ARCHIVE_TABLE"]
    assert "<strong>学生021</strong>" in html_data["STUDENT_ARCHIVE_TABLE"]


def test_comprehensive_template_renders_all_sections():
    """综合报告须经 build_comprehensive_report_data_tool 渲染（禁止 render 手填）。"""
    result = _run(
        build_comprehensive_report_data_tool.execute(
            records=_sample_records(),
            exam_order=["第一次", "第二次", "第三次"],
            class_name="初三1班",
            full_score=100,
            render=True,
        )
    )
    html = result.data["html"]
    assert "初三1班综合分析报告" in html
    assert "一、班级整体概览" in html
    assert "二、各科成绩趋势分析" in html
    assert "三、总分与各科相关性" in html
    assert "四、学生趋势分布与水平分布" in html
    assert "五、进步最快与退步最快" in html
    assert "六、偏科生诊断" in html
    assert "七、单科进步/退步之最" in html
    assert "八、全体学生总分变化轨迹" in html
    assert "九、每位学生详细档案" in html
    assert "echarts" in html  # CDN
    assert "<strong>s1</strong>" in html  # 学生档案
    # 第五节须是学生 TOP，不是班级学段汇总
    assert "学生" in html
    assert "学段" not in html or "首次考试" in html


def test_render_html_report_blocks_comprehensive_handfill():
    """禁止用 render_html_report 手填 comprehensive（会导致档案空/TOP 变班级汇总）。"""
    result = _run(
        render_html_report.execute(
            datasource_id=1,
            template_name="education/comprehensive.html",
            data={"COVER_TITLE": "x", "STUDENT_ARCHIVE_TABLE": ""},
            title="综合报告",
        )
    )
    assert result.data is None or result.data.get("error") == "use_build_comprehensive_report_data_tool"
    assert "build_comprehensive_report_data_tool" in result.content


@pytest.mark.parametrize(
    "template_name,expected_tool",
    [
        ("education/subject_diagnosis.html", "build_subject_diagnosis_sections_tool"),
        ("education/diagnostic_report.html", "build_diagnostic_report_data_tool"),
        ("education/student_exam_analysis.html", "build_student_exam_report_data_tool"),
        ("education/student_subject_diagnosis.html", "build_student_subject_diagnosis_tool"),
    ],
)
def test_render_html_report_blocks_dedicated_edu_templates(template_name, expected_tool):
    result = _run(
        render_html_report.execute(
            datasource_id=1,
            template_name=template_name,
            data={"REPORT_TITLE": "x"},
            title="blocked",
        )
    )
    assert result.data is not None
    assert str(result.data.get("error") or "").startswith("use_build_")
    assert expected_tool in result.content


def test_comprehensive_progress_and_archive_are_student_level():
    """第五节 TOP 与第九节档案必须是逐人数据。"""
    records = []
    for exam in ("摸底", "一模"):
        for i in range(1, 11):
            score = 80 + i + (10 if exam == "一模" and i <= 3 else 0) - (15 if exam == "一模" and i >= 8 else 0)
            records.append({
                "exam": exam,
                "student": f"学生{i:03d}",
                "subjects": {"数学": float(score)},
                "total": float(score),
            })
    data = _run(
        build_comprehensive_report_data_tool.execute(
            records=records,
            exam_order=["摸底", "一模"],
            class_name="高三(10)班",
            full_score=150,
            render=False,
        )
    ).data
    assert "学生001" in data["PROGRESS_TABLE"] or "学生002" in data["PROGRESS_TABLE"]
    assert "学段" not in data["PROGRESS_TABLE"]
    assert "第一阶段" not in data["PROGRESS_TABLE"]
    assert "<strong>学生010</strong>" in data["STUDENT_ARCHIVE_TABLE"]
    assert "archive-card" in data["STUDENT_ARCHIVE_TABLE"]
    assert data["STUDENT_ARCHIVE_TABLE"].count("archive-card") >= 10


def test_archive_advice_uses_item_insights():
    """有小题得分率时，个性化建议须点名薄弱题/知识点，并覆盖多场考试。"""
    from src.agent.education.comprehensive import build_comprehensive_data

    records = [
        {"exam": "摸底", "student": "STU01", "subjects": {"数学": 100}, "total": 100},
        {"exam": "一模", "student": "STU01", "subjects": {"数学": 90}, "total": 90},
        {"exam": "摸底", "student": "STU02", "subjects": {"数学": 80}, "total": 80},
        {"exam": "一模", "student": "STU02", "subjects": {"数学": 95}, "total": 95},
    ]
    insights = {
        "STU01": {
            "exam_names": ["摸底", "一模"],
            "weak_items": [
                {"question_no": 8, "knowledge_name": "三角函数", "score_rate": 25, "exam_name": "摸底"},
                {"question_no": 12, "knowledge_name": "导数", "score_rate": 20, "exam_name": "一模"},
                {"question_no": 15, "knowledge_name": "立体几何", "score_rate": 35, "exam_name": "一模"},
            ],
            "weak_knowledge": [{"knowledge_name": "导数", "score_rate": 40}],
            "strong_knowledge": [{"knowledge_name": "集合", "score_rate": 95}],
        }
    }
    data = build_comprehensive_data(
        records,
        ["摸底", "一模"],
        class_name="高三(10)班",
        full_score=150,
        student_item_insights=insights,
    )
    arch = data["STUDENT_ARCHIVE_TABLE"]
    assert "archive-card" in arch
    assert "第12题" in arch
    assert "导数" in arch
    assert "小题补漏" in arch
    assert "摸底" in arch
    assert "一模" in arch
    assert "成绩是相对薄弱" not in arch


def test_aggregate_student_item_insights():
    from src.agent.education.comprehensive import aggregate_student_item_insights

    rows = [
        {"student_id": "A", "exam_name": "摸底", "question_no": 1, "knowledge_name": "函数", "score_rate": 40},
        {"student_id": "A", "exam_name": "摸底", "question_no": 2, "knowledge_name": "函数", "score_rate": 90},
        {"student_id": "A", "exam_name": "一模", "question_no": 3, "knowledge_name": "概率", "score_rate": 30},
        {"student_id": "B", "exam_name": "一模", "question_no": 1, "knowledge_name": "函数", "score_rate": 100},
    ]
    out = aggregate_student_item_insights(rows, weak_threshold=60)
    assert "A" in out
    assert len(out["A"]["weak_items"]) == 2
    exams = {str(it.get("exam_name")) for it in out["A"]["weak_items"]}
    assert exams == {"摸底", "一模"}
    assert out["A"]["exam_names"] == ["摸底", "一模"]
    assert out["B"]["weak_items"] == []


# ---- 意图识别：综合报告 ----------------------------------------------------


def test_intent_resolver_comprehensive_keyword():
    from src.agent.education.orchestrator import ReportIntentResolver
    resolver = ReportIntentResolver()
    spec = resolver.resolve("生成初三1班三次考试综合分析报告")
    assert spec.report_type == ReportType.COMPREHENSIVE


def test_intent_resolver_all_math_exams_routes_comprehensive():
    from src.agent.education.orchestrator import ReportIntentResolver
    resolver = ReportIntentResolver()
    spec = resolver.resolve("扬州中学高三(11)班所有数学考试成绩分析")
    assert spec.report_type == ReportType.COMPREHENSIVE


def test_intent_resolver_school_class_comparison_routes_grade_comparison():
    from src.agent.education.orchestrator import ReportIntentResolver
    resolver = ReportIntentResolver()
    spec = resolver.resolve("扬州中学在连淮扬镇数学考试中各个班级的横向多维对比分析")
    assert spec.report_type == ReportType.GRADE_COMPARISON
    assert spec.filters.get("school_name") == "扬州中学"
    assert spec.filters.get("subject") == "数学"
    assert "class_name" not in spec.filters


def test_intent_resolver_comprehensive_overrides_class_overview():
    from src.agent.education.orchestrator import ReportIntentResolver
    resolver = ReportIntentResolver()
    # "综合分析报告"应优先于"班级/期中分析"
    spec = resolver.resolve("初三1班期中综合分析报告")
    assert spec.report_type == ReportType.COMPREHENSIVE


# ---- 综合报告总分一致性回归 ----------------------------------------------


def test_comprehensive_exam_order_mismatch_uses_record_exam_names():
    """exam_order 与记录考试名不一致时，按记录实际考试名取数，总分列不至全 0。"""
    from src.agent.education.comprehensive import build_comprehensive_data
    from src.agent.education.config import load_config
    records = [
        {"exam": "第一次考试", "student": "s1", "subjects": {"语文": 80, "数学": 90, "英语": 70}, "total": 240},
        {"exam": "第二次考试", "student": "s1", "subjects": {"语文": 85, "数学": 88, "英语": 75}, "total": 248},
        {"exam": "第三次考试", "student": "s1", "subjects": {"语文": 90, "数学": 85, "英语": 80}, "total": 255},
    ]
    data = build_comprehensive_data(records, ["第一次", "第二次", "第三次"],
                                    class_name="初三1班", full_score=100, config=load_config())
    arch = data["STUDENT_ARCHIVE_TABLE"]
    assert "240" in arch and "248" in arch and "255" in arch
    # 轨迹图也应用记录考试名
    assert "第一次考试" in data["TRAJECTORY_CHART"]


def test_comprehensive_archive_avg_total_uses_all_exams():
    """S9 均分列应为所有考试总分的平均，而非仅首末两次。"""
    from src.agent.education.comprehensive import build_comprehensive_data
    from src.agent.education.config import load_config
    records = [
        {"exam": "一", "student": "s1", "subjects": {"语文": 80, "数学": 85, "英语": 75}, "total": 240},
        {"exam": "二", "student": "s1", "subjects": {"语文": 83, "数学": 85, "英语": 80}, "total": 248},
        {"exam": "三", "student": "s1", "subjects": {"语文": 85, "数学": 90, "英语": 80}, "total": 255},
    ]
    data = build_comprehensive_data(records, ["一", "二", "三"],
                                    class_name="c", full_score=100, config=load_config())
    arch = data["STUDENT_ARCHIVE_TABLE"]
    # (240+248+255)/3 = 247.67 → 247.7；旧实现 (240+255)/2=247.5
    assert "247.7" in arch
    assert "247.5" not in arch


def test_comprehensive_level_labels_not_all_D():
    """水平等级不应全员 D：无论 full_score 传单科满分、总分满分还是缺省，
    都要产出差异化等级（自适应回退到数据最大总分）。"""
    import re
    from src.agent.education.comprehensive import build_comprehensive_data
    from src.agent.education.config import load_config
    records = [
        {"exam": "一", "student": "s1", "subjects": {"语文": 120, "数学": 130, "英语": 110}, "total": 360},
        {"exam": "一", "student": "s2", "subjects": {"语文": 95, "数学": 140, "英语": 100}, "total": 335},
        {"exam": "一", "student": "s3", "subjects": {"语文": 80, "数学": 90, "英语": 85}, "total": 255},
        {"exam": "二", "student": "s1", "subjects": {"语文": 125, "数学": 135, "英语": 115}, "total": 375},
        {"exam": "二", "student": "s2", "subjects": {"语文": 100, "数学": 138, "英语": 105}, "total": 343},
        {"exam": "二", "student": "s3", "subjects": {"语文": 82, "数学": 92, "英语": 88}, "total": 262},
    ]
    for fs in (150, 450, None):
        data = build_comprehensive_data(records, ["一", "二"], class_name="c",
                                        full_score=fs, config=load_config())
        levels = re.findall(r'class="level-pill"[^>]*>([^<]+)</span>',
                            data["STUDENT_ARCHIVE_TABLE"])
        assert len(levels) == 3
        assert set(levels) != {"D (待提升)"}, f"full_score={fs} 时不应全员 D，实际 {levels}"
        # 等级应至少有 2 种（差异化）
        assert len(set(levels)) >= 2, f"full_score={fs} 等级无差异化：{levels}"


def test_comprehensive_level_full_score_misread_as_total_falls_back():
    """LLM 误把总分满分当 full_score 传入（再乘科目数会远超实际总分）时，
    自适应回退到数据最大总分，避免全员 D。"""
    import re
    from src.agent.education.comprehensive import build_comprehensive_data
    from src.agent.education.config import load_config
    records = [
        {"exam": "一", "student": "s1", "subjects": {"语文": 120, "数学": 130, "英语": 110}, "total": 360},
        {"exam": "一", "student": "s2", "subjects": {"语文": 95, "数学": 140, "英语": 100}, "total": 335},
    ]
    # full_score=450（实际是总分满分），candidate=450*3=1350 > 375*1.3 → 回退 max_total
    data = build_comprehensive_data(records, ["一"], class_name="c",
                                    full_score=450, config=load_config())
    levels = re.findall(r'class="level-pill"[^>]*>([^<]+)</span>',
                        data["STUDENT_ARCHIVE_TABLE"])
    assert set(levels) != {"D (待提升)"}


def test_comprehensive_exam_alias_resolves_third_exam():
    """exam_order 写「三模」、记录为「第三次考试」时，应匹配到真实数据而非全 0 列。"""
    from src.agent.education.comprehensive import build_comprehensive_data
    from src.agent.education.config import load_config
    records = [
        {"exam": "一模", "student": "s1", "subjects": {"语文": 80, "数学": 90, "英语": 70}, "total": 240},
        {"exam": "二模", "student": "s1", "subjects": {"语文": 85, "数学": 88, "英语": 75}, "total": 248},
        {"exam": "第三次考试", "student": "s1", "subjects": {"语文": 90, "数学": 85, "英语": 80}, "total": 255},
    ]
    data = build_comprehensive_data(records, ["一模", "二模", "三模"],
                                    class_name="c", full_score=100, config=load_config())
    arch = data["STUDENT_ARCHIVE_TABLE"]
    assert "255" in arch
    assert "第三次考试" in arch


def test_comprehensive_excludes_empty_exam_placeholders():
    """无科目/总分为 0 的占位考试不应出现在档案表列中。"""
    from src.agent.education.comprehensive import build_comprehensive_data
    from src.agent.education.config import load_config
    records = [
        {"exam": "一模", "student": "s1", "subjects": {"语文": 80, "数学": 90}, "total": 170},
        {"exam": "二模", "student": "s1", "subjects": {"语文": 85, "数学": 88}, "total": 173},
        {"exam": "三模", "student": "s1", "subjects": {}, "total": 0},
    ]
    data = build_comprehensive_data(records, ["一模", "二模", "三模"],
                                    class_name="c", full_score=100, config=load_config())
    arch = data["STUDENT_ARCHIVE_TABLE"]
    assert "三模" not in arch or "0.0" not in arch.split("173")[1][:20]
    assert "171.5" in arch  # (170+173)/2


def test_long_table_total_column_zero_sums_subjects():
    """长表 total 列全为 0 时，仍应按各科分数求和。"""
    rows = [
        ["三模", "s1", "语文", 80, 0],
        ["三模", "s1", "数学", 90, 0],
        ["三模", "s1", "英语", 70, 0],
    ]
    result = _run(
        build_comprehensive_report_data_tool.execute(
            rows=rows,
            columns=["exam", "student", "subject", "score", "total"],
            exam_field="exam",
            student_field="student",
            subject_field="subject",
            score_field="score",
            total_field="total",
            class_name="c",
            full_score=100,
            render=False,
        )
    )
    assert "240" in result.data["STUDENT_ARCHIVE_TABLE"]


def test_build_subject_diagnosis_sections_tool_renders_html():
    """sections 工具默认 render=True，应直接返回 HTML 载荷。"""
    from src.agent.education import tools as edu_tools

    result = _run(
        edu_tools.build_subject_diagnosis_sections_tool.execute(
            item_rows=[
                {
                    "student_id": "S01",
                    "question_no": 1,
                    "knowledge_name": "集合",
                    "score_rate": 30.0,
                    "exam_name": "期末质量检测",
                },
                {
                    "student_id": "S01",
                    "question_no": 2,
                    "knowledge_name": "函数",
                    "score_rate": 90.0,
                    "exam_name": "期末质量检测",
                },
                {
                    "student_id": "S02",
                    "question_no": 1,
                    "knowledge_name": "集合",
                    "score_rate": 80.0,
                    "exam_name": "期末质量检测",
                },
            ],
            knowledge_rows=[
                {"knowledge_name": "集合", "score_rate": 55.0, "question_count": 1},
            ],
            stats={
                "avg": 86,
                "pass_rate": 90,
                "excellent_rate": 20,
                "stdev": 8,
                "full_score": 150,
                "count": 2,
                "segments": [{"label": "90-100", "count": 5, "ratio": 50}],
            },
            score_result={
                "columns": ["student_id", "subject_name", "score", "exam_score"],
                "rows": [
                    ["S01", "数学", 120, 150],
                    ["S02", "数学", 95, 150],
                ],
            },
            school_name="南京市第一中学",
            exam_name="期末质量检测",
            subject_name="数学",
            class_name="高一(2)班",
        )
    )
    assert result.data["output_type"] == "html"
    assert result.is_final is True
    html = result.data["html"]
    assert "集合" in html
    assert "数学" in html
    assert "每位学生详细档案与个性化建议" in html
    assert "archive-card" in html
    assert "S01" in html
    assert "S02" in html
    # 单科诊断：不展示跨科优势/待提升/偏科度
    assert "偏科度" not in html
    assert "待提升" not in html
    assert "优势" not in html or "优势 <b" not in html
    # 建议应来自知识点/小题，而非「数学相对薄弱」空模板
    assert "知识点薄弱" in html or "小题补漏" in html
    assert "短板提升" not in html
    assert "数学相对薄弱" not in html


def test_subject_diagnosis_template_has_student_archive_slot():
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[2]
        / "src/agent/resource/templates/education/subject_diagnosis.html"
    )
    text = path.read_text(encoding="utf-8")
    assert "每位学生详细档案与个性化建议" in text
    assert "{{STUDENT_ARCHIVE_TABLE}}" in text
    assert "archive-grid" in text


def test_subject_diagnosis_archive_hides_cross_subject_meta():
    from src.agent.education.tools import _subject_diagnosis_student_archive

    html = _subject_diagnosis_student_archive(
        score_rows=[
            {"student_id": "A1", "subject_name": "数学", "score": 88, "exam_score": 150},
            {"student_id": "A2", "subject_name": "数学", "score": 110, "exam_score": 150},
        ],
        item_rows=[
            {
                "student_id": "A1",
                "question_no": 3,
                "knowledge_name": "立体几何",
                "score_rate": 25.0,
                "exam_name": "一模",
            },
            {
                "student_id": "A1",
                "question_no": 5,
                "knowledge_name": "概率统计",
                "score_rate": 40.0,
                "exam_name": "一模",
            },
            {
                "student_id": "A2",
                "question_no": 3,
                "knowledge_name": "立体几何",
                "score_rate": 95.0,
                "exam_name": "一模",
            },
        ],
        exam_name="一模",
        subject_name="数学",
        weak_threshold=60.0,
    )
    assert "偏科度" not in html
    assert "待提升" not in html
    assert "archive-card" in html
    assert "立体几何" in html
    assert "知识点薄弱" in html or "小题补漏" in html
    assert "数学相对薄弱" not in html


def test_build_subject_diagnosis_sections_auto_fetch_from_report_data():
    from src.agent.education import tools as edu_tools

    fetch_payload = {
        "item_rows": [{"question_no": 1, "knowledge_name": "集合", "score_rate": 80.0}],
        "knowledge_rows": [{"knowledge_name": "集合", "score_rate": 80.0, "question_count": 1}],
        "score_rows": [{"score": 88.0, "exam_score": 100.0}] * 5,
    }
    report_data = {
        "sub_tasks": [
            {
                "sub_task_agent": "ToolExpert",
                "tool_calls": [
                    {
                        "tool": "fetch_subject_diagnosis_data_tool",
                        "success": True,
                        "data": fetch_payload,
                    }
                ],
            }
        ]
    }
    result = _run(
        edu_tools.build_subject_diagnosis_sections_tool.execute(
            school_name="南京市第一中学",
            exam_name="期末质量检测",
            subject_name="数学",
            render=False,
            report_data=report_data,
        )
    )
    assert "小题 1 条" in result.content
    assert "知识点 1 个" in result.content
    assert "INTERVENTION_SECTION" in result.data


def test_build_subject_diagnosis_sections_ignores_empty_llm_fetch_data():
    """LLM 手抄截断成空 fetch_data / item_rows 时，应回退到上游非空数据。"""
    from src.agent.education import tools as edu_tools

    fetch_payload = {
        "item_rows": [
            {"question_no": 1, "knowledge_name": "函数", "score_rate": 55.0},
        ],
        "knowledge_rows": [
            {"knowledge_name": "函数", "score_rate": 55.0, "question_count": 1},
        ],
        "score_rows": [{"score": 100.0, "exam_score": 150.0}] * 3,
        "score_result": {
            "columns": ["score", "exam_score"],
            "rows": [[100.0, 150.0]] * 3,
        },
    }
    report_data = {
        "sub_tasks": [
            {
                "tool_calls": [
                    {
                        "tool": "fetch_subject_diagnosis_data_tool",
                        "success": True,
                        "data": fetch_payload,
                    }
                ],
            }
        ]
    }
    result = _run(
        edu_tools.build_subject_diagnosis_sections_tool.execute(
            fetch_data={"item_rows": [], "knowledge_rows": [], "score_rows": []},
            item_rows=[],
            knowledge_rows=[],
            school_name="扬州中学",
            exam_name="期末",
            subject_name="数学",
            class_name="高三(11)班",
            render=False,
            report_data=report_data,
        )
    )
    assert "小题 1 条" in result.content
    assert "知识点 1 个" in result.content


def test_build_subject_diagnosis_sections_uses_last_fetch_data_ctx():
    """同子任务内：未传 fetch_data 时用 tool_runtime_ctx.last_fetch_data。"""
    from src.agent.education import tools as edu_tools

    fetch_payload = {
        "item_rows": [{"question_no": 2, "knowledge_name": "导数", "score_rate": 40.0}],
        "knowledge_rows": [{"knowledge_name": "导数", "score_rate": 40.0, "question_count": 1}],
        "score_rows": [{"score": 90.0, "exam_score": 150.0}] * 2,
        "score_result": {
            "columns": ["score", "exam_score"],
            "rows": [[90.0, 150.0], [90.0, 150.0]],
        },
    }
    result = _run(
        edu_tools.build_subject_diagnosis_sections_tool.execute(
            school_name="扬州中学",
            subject_name="数学",
            class_name="高三(11)班",
            render=True,
            tool_runtime_ctx={"last_fetch_data": fetch_payload},
        )
    )
    assert result.data["output_type"] == "html"
    assert "小题 1 条" in result.content
    html = result.data["html"]
    assert "导数" in html
    assert "-%" not in html or "90" in html  # KPI should be filled


def test_build_subject_diagnosis_sections_includes_intervention_for_school():
    from src.agent.education import tools as edu_tools

    fetch_payload = {
        "item_rows": [
            {"question_type": "填空题", "knowledge_name": "函数", "score_rate": 45.0},
        ],
        "knowledge_rows": [
            {"knowledge_name": "函数", "score_rate": 45.0, "question_count": 2, "ability_level": "applied"},
        ],
        "score_rows": [
            {"class": "高一1班", "score": 92, "exam_score": 150},
            {"class": "高一1班", "score": 88, "exam_score": 150},
            {"class": "高一1班", "score": 85, "exam_score": 150},
            {"class": "高一2班", "score": 55, "exam_score": 150},
            {"class": "高一2班", "score": 58, "exam_score": 150},
            {"class": "高一2班", "score": 48, "exam_score": 150},
        ],
    }
    result = _run(
        edu_tools.build_subject_diagnosis_sections_tool.execute(
            fetch_data=fetch_payload,
            school_name="南京市第一中学",
            exam_name="期末质量检测",
            subject_name="数学",
            render=False,
        )
    )
    assert "INTERVENTION_SECTION" in result.data
    assert "高一2班" in result.data["INTERVENTION_SECTION"]
    assert "函数" in result.data["INTERVENTION_SECTION"]
    assert "CLASS_COMPARE_TABLE" in result.data


def test_report_scope_subtitle_filters_none():
    from src.agent.education.tools import _report_scope_subtitle

    assert _report_scope_subtitle("南京市第一中学", None) == "南京市第一中学"
    assert _report_scope_subtitle("南京市第一中学", "") == "南京市第一中学"


# ---- build_subject_diagnosis_report_tool ----------------------------------


# ---- exam_name_like_candidates / diagnosis query ----------------------------


def test_exam_name_like_candidates_strips_province_prefix():
    from src.agent.education.tools import exam_name_like_candidates

    cands = exam_name_like_candidates("江苏省高一上学期数学期末质量检测")
    assert "江苏省高一上学期数学期末质量检测" in cands
    assert "高一上学期数学期末质量检测" in cands
    assert any("期末" in c for c in cands)


def test_diagnosis_where_clause_pair_uses_correct_exam_id_column():
    from src.agent.education.tools import _diagnosis_sql_bundle, _diagnosis_where_clause_pair

    detail_wc, score_wc = _diagnosis_where_clause_pair(
        school_name="南京市第一中学",
        class_name="高一(2)班",
        subject_name="数学",
        exam_ids=["1"],
        skip_exam_name=True,
    )
    item_sql, _, score_sql, exam_id_sql = _diagnosis_sql_bundle(detail_wc, score_wc, "pg")
    assert "sd.exam_id IN ('1')" in item_sql
    assert "sc.exam_id IN ('1')" in score_sql
    assert "sc.exam_id IN ('1')" in exam_id_sql
    assert "sd.exam_id" not in score_sql


def test_unsafe_school_abbreviation_blocks_exact_match():
    from src.agent.education.tools import _is_unsafe_school_name_filter

    assert _is_unsafe_school_name_filter("南京一中") is True
    assert _is_unsafe_school_name_filter("南京市第一中学") is False


def test_fetch_student_diagnosis_retries_without_school_abbreviation(monkeypatch):
    from src.agent.education import tools as edu_tools

    calls: list[str] = []

    def fake_run(sql, **kwargs):
        calls.append(sql)
        if "sch.name = '南京一中'" in sql:
            return True, "", {"columns": ["score"], "rows": []}, sql
        if "sc.student_id = 'STU20240003'" in sql and "sch.name" not in sql:
            return True, "", {
                "columns": ["question_no", "knowledge_name", "question_type", "full_score", "avg_score", "score_rate"],
                "rows": [["1", "集合", "选择", 5, 4, 80]],
            }, sql
        return True, "", {"columns": ["score"], "rows": [[88]]}, sql

    monkeypatch.setattr(edu_tools, "_run_edu_sql", fake_run)
    bundle = edu_tools._fetch_subject_diagnosis_rows(
        datasource_id=1,
        workspace_oid=1,
        user_id=1,
        db_type="pg",
        student_id="STU20240003",
        subject_name="数学",
        exam_name="期末质量检测",
        school_name="南京一中",
    )
    assert len(bundle["item_rows"]) == 1
    assert any("fallback_student_no_school" in (lg.get("phase") or "") for lg in bundle["sql_logs"])


def test_build_subject_diagnosis_report_tool_renders_html(monkeypatch):
    """一键工具：mock 数据库，验证返回 HTML 载荷且知识点名取自 tb_knowledge。"""
    from src.agent.education import tools as edu_tools

    item_rows = [
        ["1", "集合及其运算", 5, 4.5, 90.0],
        ["16", "分段函数", 5, 2.0, 40.0],
        ["18", "函数的奇偶性与单调性", 5, 3.0, 60.0],
    ]
    knowledge_rows = [
        ["分段函数", 1, 40.0],
        ["函数的奇偶性与单调性", 1, 60.0],
        ["集合及其运算", 1, 90.0],
    ]
    score_rows = [[91, 150], [100, 150], [120, 150]]

    def fake_load_datasource(_ds_id, _ws=None):
        return ("pg", {}, "test-ds")

    def fake_execute_sql(db_type, config, sql):
        if "sd.question_no" in sql:
            return True, "", {"columns": ["question_no", "knowledge_name", "full_score", "avg_score", "score_rate"], "rows": item_rows}
        if "COUNT(DISTINCT sd.question_no)" in sql:
            return True, "", {"columns": ["knowledge_name", "question_count", "score_rate"], "rows": knowledge_rows}
        return True, "", {"columns": ["score", "exam_score"], "rows": score_rows}

    monkeypatch.setattr(biz, "_load_datasource", fake_load_datasource)
    monkeypatch.setattr("src.datasource.db.db.execute_sql", fake_execute_sql)

    result = _run(
        edu_tools.build_subject_diagnosis_report_tool.execute(
            datasource_id=1,
            school_name="南京市第一中学",
            subject_name="数学",
            exam_name="期末质量检测",
        )
    )
    assert result.data["output_type"] == "html"
    html = result.data["html"]
    # 知识点名称来自数据库，不是臆造的
    assert "集合及其运算" in html
    assert "分段函数" in html
    assert "函数的奇偶性与单调性" in html
    # 不应出现数据库中不存在的知识点名
    assert "立体几何" not in html
    assert "解析几何" not in html


def test_build_subject_diagnosis_report_tool_no_data(monkeypatch):
    """查无数据时返回 error，不抛异常。"""
    from src.agent.education import tools as edu_tools

    monkeypatch.setattr(biz, "_load_datasource", lambda *_a, **_kw: ("pg", {}, "ds"))
    monkeypatch.setattr(
        "src.datasource.db.db.execute_sql",
        lambda *_a, **_kw: (True, "", {"columns": [], "rows": []}),
    )

    result = _run(
        edu_tools.build_subject_diagnosis_report_tool.execute(
            datasource_id=1,
            school_name="不存在学校",
            subject_name="数学",
        )
    )
    assert "error" in result.data


def test_aggregate_dimension_tool_accepts_exec_result():
    from src.agent.education import tools as edu_tools

    result = _run(
        edu_tools.aggregate_dimension_tool.execute(
            dimension="class",
            exec_result={
                "columns": ["class", "score", "exam_score"],
                "rows": [
                    ["高一1班", 90, 150],
                    ["高一1班", 85, 150],
                    ["高一2班", 70, 150],
                    ["高一2班", 65, 150],
                ],
            },
        )
    )
    assert "error" not in result.data
    groups = result.data["groups"]
    assert len(groups) == 2
    names = {g["dimension_value"] for g in groups}
    assert "高一1班" in names
    assert "高一2班" in names


def test_aggregate_dimension_tool_rejects_bare_matrix_without_columns():
    from src.agent.education import tools as edu_tools

    result = _run(
        edu_tools.aggregate_dimension_tool.execute(
            dimension="class",
            rows=[[90, "高一1班"], [85, "高一1班"]],
        )
    )
    assert result.data.get("error") == "no rows"
    assert "columns" in result.content


def test_aggregate_dimension_tool_dict_rows():
    from src.agent.education import tools as edu_tools

    result = _run(
        edu_tools.aggregate_dimension_tool.execute(
            dimension="school",
            rows=[
                {"school_name": "一中", "score": 88, "exam_score": 100},
                {"school_name": "二中", "score": 72, "exam_score": 100},
            ],
        )
    )
    assert len(result.data["groups"]) == 2
