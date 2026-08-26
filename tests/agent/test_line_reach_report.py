"""全市达线分析：SUM 聚合、环比增减、无上场不崩。"""

from __future__ import annotations

import asyncio

from src.agent.education.line_reach_report import (
    build_line_reach_report_data,
    choose_exam_with_llm,
    exam_batch_select_sql,
    ordered_exam_names,
    parse_focus_lines,
    parse_llm_exam_choice,
    pick_exam_for_question,
    pick_previous_exam,
    resolve_current_exam,
    sum_reach,
    unique_candidates,
)
from src.agent.education.orchestrator import ReportOrchestrator
from src.agent.education.report_types import Audience, ReportSpec, ReportType
from src.agent.education.schema_mapping import ScoreSchemaMapping
from src.agent.education.templates import select_report_template


def _row(**kwargs):
    base = {
        "exam_name": "1月期末",
        "track": "物理类",
        "district": "邗江区",
        "school_id": "S1",
        "line_name": "本科线",
        "candidates": 100,
        "reached_count": 60,
        "reach_rate": 60.0,
    }
    base.update(kwargs)
    return base


def test_sum_reach_recomputes_rate_not_avg():
    rows = [
        _row(school_id="A", candidates=1, reached_count=1, reach_rate=100.0),
        _row(school_id="B", candidates=99, reached_count=0, reach_rate=0.0),
    ]
    cand, hit, rate = sum_reach(rows)
    assert cand == 100
    assert hit == 1
    assert rate == 1.0


def test_unique_candidates_not_multiplied_by_line_types():
    rows = [
        _row(line_name="特控线", reached_count=20),
        _row(line_name="本科线", reached_count=60),
        _row(line_name="985线", reached_count=5),
    ]
    assert unique_candidates(rows) == 100
    _, hit_bk, rate_bk = sum_reach([r for r in rows if r["line_name"] == "本科线"])
    assert hit_bk == 60
    assert rate_bk == 60.0


def test_mom_delta_people_and_rate():
    curr = [
        _row(line_name="本科线", reached_count=70, reach_rate=70.0),
        _row(line_name="特控线", reached_count=30, reach_rate=30.0),
    ]
    prev = [
        _row(exam_name="11月期中", line_name="本科线", reached_count=60, reach_rate=60.0),
        _row(exam_name="11月期中", line_name="特控线", reached_count=40, reach_rate=40.0),
    ]
    data = build_line_reach_report_data(
        curr,
        prev,
        exam_name="1月期末",
        prev_exam_name="11月期中",
    )
    assert "+10 人" in data["DELTA_TABLE"]
    assert "-10 人" in data["DELTA_TABLE"]
    assert data["PREV_EXAM_NAME"] == "11月期中"
    assert "11月期中" in data["GENERAL_INSIGHT"]
    assert data["_stats"]["count"] == 100
    assert "本科线本次" in data["SCHOOL_TABLE"]
    assert "本科线上场" in data["SCHOOL_TABLE"]
    assert "本科线增减" in data["SCHOOL_TABLE"]
    assert "+10 人" in data["SCHOOL_TABLE"]
    assert "-10 人" in data["SCHOOL_TABLE"]


def test_school_reach_delta_vs_previous_exam():
    extra_lines = ("特控线", "211线", "985线", "清北线", "南大线")
    curr = [
        _row(school_id="A01", candidates=80, reached_count=50),
        _row(school_id="A02", candidates=120, reached_count=70, district="广陵区"),
    ]
    prev = [
        _row(exam_name="11月期中", school_id="A01", candidates=100, reached_count=60),
        _row(exam_name="11月期中", school_id="A02", candidates=90, reached_count=50, district="广陵区"),
        _row(exam_name="11月期中", school_id="A03", candidates=40, reached_count=20, district="江都区"),
    ]
    for line_name in extra_lines:
        curr.append(_row(school_id="A01", line_name=line_name, candidates=80, reached_count=10))
        prev.append(
            _row(
                exam_name="11月期中",
                school_id="A01",
                line_name=line_name,
                candidates=100,
                reached_count=12,
            )
        )
    data = build_line_reach_report_data(
        curr,
        prev,
        exam_name="1月期末",
        prev_exam_name="11月期中",
    )
    table = data["SCHOOL_TABLE"]
    for line_name in ("本科线", "特控线", "211线", "985线", "清北线", "南大线"):
        assert f"{line_name}本次" in table
        assert f"{line_name}上场" in table
        assert f"{line_name}增减" in table
    assert "A01" in table
    assert "A02" in table
    assert "A03" in table
    assert "-10 人" in table
    assert "+20 人" in table
    assert "-20 人" in table
    assert "达线人数下降较多的学校" in data["GENERAL_INSIGHT"]
    assert "A01" in data["GENERAL_INSIGHT"] or "A03" in data["GENERAL_INSIGHT"]
    kpi = data["KPI_GRID"]
    assert "211线达线人数" in kpi
    assert "985线达线人数" in kpi
    assert "清北线达线人数" in kpi
    assert "南大线达线人数" in kpi


def test_no_previous_exam_still_renders():
    data = build_line_reach_report_data(
        [_row()],
        None,
        exam_name="1月期末",
    )
    assert data["PREV_EXAM_NAME"] == "—"
    assert "暂无上场" in data["GENERAL_INSIGHT"]
    assert "本科线" in data["KPI_GRID"]


def test_parse_focus_lines_985_211_qingbei():
    q = "2026届1月各地区985、211、清华北大达线情况"
    assert parse_focus_lines(q) == ["211线", "985线", "清北线"]


def test_report_follows_asked_lines_and_districts():
    curr = [
        _row(line_name="本科线", reached_count=70, district="邗江区"),
        _row(line_name="特控线", reached_count=30, district="邗江区"),
        _row(line_name="211线", reached_count=12, district="邗江区"),
        _row(line_name="985线", reached_count=5, district="邗江区"),
        _row(line_name="清北线", reached_count=1, district="邗江区"),
        _row(
            line_name="985线",
            reached_count=3,
            district="广陵区",
            school_id="S2",
        ),
        _row(
            line_name="211线",
            reached_count=8,
            district="广陵区",
            school_id="S2",
        ),
        _row(
            line_name="清北线",
            reached_count=0,
            district="广陵区",
            school_id="S2",
        ),
    ]
    data = build_line_reach_report_data(
        curr,
        None,
        exam_name="2026届高三1月期末",
        question="2026届1月各地区985、211、清华北大达线情况",
    )
    assert data["SCOPE"] == "各地区"
    assert "985" in data["REPORT_TITLE"]
    assert "211" in data["REPORT_TITLE"]
    assert "清北" in data["REPORT_TITLE"]
    assert "985线" in data["KPI_GRID"]
    assert "211线" in data["KPI_GRID"]
    assert "清北线" in data["KPI_GRID"]
    assert "特控线" not in data["KPI_GRID"]
    assert "本科线" not in data["KPI_GRID"]
    assert "特控线" not in data["DELTA_TABLE"]
    assert "邗江区" in data["DISTRICT_TABLE"]
    assert "广陵区" in data["DISTRICT_TABLE"]
    assert "S1" in data["SCHOOL_TABLE"]
    assert "S2" in data["SCHOOL_TABLE"]
    assert "985线人数" in data["DISTRICT_TABLE"]
    chart = str(data["DISTRICT_CHART"] or "")
    assert "985" in chart and "211" in chart and "清北" in chart


def test_pick_exam_month_allows_space_not_latest():
    """「3 月」中间有空格时，不得落到列表最后一场（高二6月期末）。"""
    names = [
        "2026届高三11月期中",
        "2026届高三1月期末",
        "2026届高三3月",
        "2026届高三5月模拟",
        "2027届高二6月期末",
    ]

    def boom(_messages):
        raise AssertionError("3月已能唯一命中，不应再调 LLM，也不应取最新一场")

    q = "扬州中学 3 月学科教研分析报告"
    assert pick_exam_for_question(names, question=q, chat_fn=boom) == "2026届高三3月"
    assert pick_exam_for_question(
        names, question="扬州中学3月学科教研分析报告", chat_fn=boom
    ) == "2026届高三3月"
    assert pick_exam_for_question(names, question="1 月达线", chat_fn=boom) == (
        "2026届高三1月期末"
    )


def test_pick_exam_keeps_asked_cohort_not_latest():
    names = [
        "2026届高三11月期中",
        "2026届高三1月期末",
        "2026届高三5月模拟",
        "2027届高二6月期末",
    ]
    q = "2026届1月各地区985、211、清华北大达线情况"

    def boom(_messages):
        raise AssertionError("届+月已能唯一命中，不应再调 LLM")

    assert pick_exam_for_question(names, question=q, chat_fn=boom) == "2026届高三1月期末"
    assert pick_exam_for_question(names, question=q, hint="期末", chat_fn=boom) == (
        "2026届高三1月期末"
    )
    names = ["2026届高三11月期中", "2026届高三1月期末"]
    assert pick_previous_exam(names, "2026届高三1月期末") == "2026届高三11月期中"
    assert pick_previous_exam(names, "2026届高三11月期中") is None
    assert pick_previous_exam(["一场"], "一场") is None
    assert resolve_current_exam(names, "") == "2026届高三1月期末"
    assert resolve_current_exam(names, "2026届高三1月期末") == "2026届高三1月期末"


def test_previous_exam_follows_exam_time_not_id():
    """3月若先入库（id 更小），也不能当成 1月期末的上场。"""
    sql = exam_batch_select_sql()
    assert "exam_time" in sql
    names = ordered_exam_names(
        [
            {"id": 1, "batch_name": "2026届高三3月", "exam_time": "2026-03-01"},
            {"id": 2, "batch_name": "2026届高三1月期末", "exam_time": "2026-01-15"},
            {"id": 3, "batch_name": "2026届高三11月期中", "exam_time": "2025-11-20"},
        ]
    )
    assert names == [
        "2026届高三11月期中",
        "2026届高三1月期末",
        "2026届高三3月",
    ]
    assert pick_previous_exam(names, "2026届高三1月期末") == "2026届高三11月期中"
    assert pick_previous_exam(names, "2026届高三1月期末") != "2026届高三3月"


def test_pick_exam_uses_catalog_name_in_question():
    names = [
        "2026届高三11月期中",
        "2026届高三1月期末",
        "2027届高二6月期末",
    ]
    q = "全市2026届高三1月期末达线情况"

    def boom(_messages):
        raise AssertionError("目录已命中，不应再调 LLM")

    assert pick_exam_for_question(names, question=q, hint="期末", chat_fn=boom) == (
        "2026届高三1月期末"
    )
    prev = pick_previous_exam(names, "2026届高三1月期末")
    assert prev == "2026届高三11月期中"


def test_parse_llm_exam_choice():
    names = [
        "2026届高三11月期中",
        "2026届高三1月期末",
        "2027届高二6月期末",
    ]
    assert parse_llm_exam_choice(
        '{"exam_name":"2026届高三1月期末"}', names
    ) == "2026届高三1月期末"
    assert parse_llm_exam_choice('{"exam_name":"期末"}', names) is None
    assert parse_llm_exam_choice('{"exam_name":"不存在的考试"}', names) is None


def test_choose_exam_with_llm_uses_batch_list():
    names = [
        "2026届高三11月期中",
        "2026届高三1月期末",
        "2027届高二6月期末",
    ]

    def chat(messages):
        blob = messages[1]["content"]
        assert "2026届高三1月期末" in blob
        assert "2027届高二6月期末" in blob
        return '{"exam_name":"2026届高三1月期末"}'

    q = "全市期末达线情况，要高三1月那场"
    assert choose_exam_with_llm(q, names, chat_fn=chat) == "2026届高三1月期末"
    assert pick_exam_for_question(names, question=q, hint="期末", chat_fn=chat) == (
        "2026届高三1月期末"
    )


def test_select_report_template_line_reach():
    info = select_report_template(ReportType.LINE_REACH)
    assert info["template_name"] == "education/line_reach.html"
    assert "DELTA_TABLE" in info["data_keys"]
    assert "PREV_EXAM_NAME" in info["data_keys"]


def test_orchestrator_line_reach_reads_indicator_not_overview():
    sqls: list[str] = []

    async def fake_execute(sql: str):
        sqls.append(sql)
        if "tb_exam_batch" in sql:
            return {
                "columns": ["id", "batch_name", "exam_time"],
                "rows": [
                    [2, "1月期末", "2026-01-15"],
                    [1, "11月期中", "2025-11-20"],
                ],
            }
        if "tb_score_indicator" in sql:
            exam = "1月期末" if "1月期末" in sql else "11月期中"
            hit = 70 if exam == "1月期末" else 60
            return {
                "columns": [
                    "exam_name",
                    "track",
                    "district",
                    "school_id",
                    "school_name",
                    "line_code",
                    "line_name",
                    "threshold",
                    "candidates",
                    "reached_count",
                    "reach_rate",
                ],
                "rows": [[
                    exam, "物理类", "邗江区", "S1", "S1", "bk", "本科线",
                    400, 100, hit, hit,
                ]],
            }
        raise AssertionError(f"unexpected sql: {sql}")

    async def fake_schema():
        return ScoreSchemaMapping(mode="wide", table="tb_score", source="config_edu")

    orch = ReportOrchestrator(execute_sql=fake_execute, resolve_schema=fake_schema)
    spec = ReportSpec(
        report_type=ReportType.LINE_REACH,
        audience=Audience.PRINCIPAL,
        filters={"exam_name": "1月期末", "scope": "全市"},
    )
    res = asyncio.run(orch.run_spec(spec))
    assert res.error is None
    assert res.template_name == "education/line_reach.html"
    blob = "\n".join(sqls)
    assert "tb_score_indicator" in blob
    assert "tb_exam_batch" in blob
    assert "exam_time" in blob
    assert "11月期中" in res.html or "上场" in res.html
    assert "tb_score_overview" not in blob
    assert "tb_score " not in blob
    assert "+10 人" in res.html
    assert "全市达线分析" in res.html
