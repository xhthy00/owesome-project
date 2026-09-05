"""summary_context 单元测试。"""

from __future__ import annotations

from src.agent.education.summary_context import (
    collect_education_artifacts,
    format_education_pipeline_footer,
    format_tool_expert_sub_task_block,
)
from src.chat.service.agent_runner import _format_sub_tasks_block


def test_collect_education_artifacts_from_fetch_tool():
    agg = collect_education_artifacts(
        tool_calls=[
            {
                "tool": "fetch_subject_diagnosis_data_tool",
                "success": True,
                "data": {
                    "item_rows": [{"question_no": 1}] * 12,
                    "knowledge_rows": [{"knowledge_name": "函数"}] * 8,
                    "score_rows": [{"score": 90}] * 24,
                },
            }
        ],
        reports=[{"title": "数学诊断报告", "html": "<html></html>"}],
    )
    assert agg["item_count"] == 12
    assert agg["knowledge_count"] == 8
    assert agg["has_diagnosis_report"] is True
    assert "数学诊断报告" in agg["report_titles"]


def test_format_education_pipeline_footer_forbids_missing_data_claim():
    footer = format_education_pipeline_footer(
        {
            "sub_tasks": [
                {
                    "tool_calls": [
                        {
                            "tool": "fetch_subject_diagnosis_data_tool",
                            "success": True,
                            "data": {
                                "item_rows": [1, 2],
                                "knowledge_rows": [1, 2, 3],
                            },
                        }
                    ],
                    "reports": [{"title": "数学诊断报告"}],
                }
            ]
        }
    )
    assert "小题级诊断数据：**已获取**" in footer
    assert "知识点级诊断数据：**已获取**" in footer
    assert "禁止" in footer


def test_format_sub_tasks_block_includes_education_footer():
    class _FakeReply:
        content = "报告已渲染"

    class _FakeState:
        last_sql = ""
        last_exec_result = None
        tool_calls = [
            {
                "tool": "fetch_subject_diagnosis_data_tool",
                "success": True,
                "data": {
                    "item_rows": [{"question_no": i} for i in range(5)],
                    "knowledge_rows": [{"knowledge_name": "函数"}],
                },
            },
            {
                "tool": "build_subject_diagnosis_sections_tool",
                "success": True,
                "data": {"output_type": "html", "title": "数学诊断报告", "html": "<p>x</p>"},
            },
        ]
        reports = [{"title": "数学诊断报告"}]

    class _FakePhase:
        is_success = True
        fail_reason = ""
        reply = _FakeReply()
        state = _FakeState()

    block = _format_sub_tasks_block(
        [("fetch 并渲染报告", _FakePhase())],
        report_data={
            "sub_tasks": [
                {
                    "tool_calls": _FakeState().tool_calls,
                    "reports": _FakeState().reports,
                }
            ]
        },
    )
    assert "小题明细：5 题" in block
    assert "数学诊断报告" in block
    assert "教育学情产出摘要" in block
    assert "禁止" in block


def test_format_tool_expert_sub_task_block():
    text = format_tool_expert_sub_task_block(
        tool_calls=[
            {
                "tool": "build_subject_diagnosis_sections_tool",
                "success": True,
                "data": {"output_type": "html", "title": "数学诊断报告"},
            }
        ],
        reports=[],
        final_answer="完成",
    )
    assert "数学诊断报告" in text


def test_sql_offset_authority_notes_forbid_page_as_class_size():
    from src.agent.education.summary_context import (
        extract_stats_authority_block,
        format_sql_result_authority_notes,
        sql_looks_paginated,
        sql_looks_row_capped,
    )

    assert sql_looks_paginated("SELECT * FROM t LIMIT 20 OFFSET 40")
    assert not sql_looks_paginated("SELECT * FROM t LIMIT 3")
    assert sql_looks_row_capped("SELECT * FROM t LIMIT 20")
    assert sql_looks_row_capped("SELECT * FROM t LIMIT 20 OFFSET 40")
    assert not sql_looks_row_capped("SELECT COUNT(*) FROM t")
    notes = format_sql_result_authority_notes(
        sql="SELECT * FROM tb_score ORDER BY id LIMIT 20 OFFSET 40",
        row_count=12,
        sample_shown=12,
    )
    assert "OFFSET" in notes
    assert "禁止" in notes
    assert "12" in notes

    limit_notes = format_sql_result_authority_notes(
        sql="SELECT student_id, score FROM tb_score WHERE class_name='高三(7)班' LIMIT 20",
        row_count=20,
        sample_shown=20,
    )
    assert "LIMIT" in limit_notes
    assert "禁止" in limit_notes
    assert "权威行数" not in limit_notes

    stats = extract_stats_authority_block(
        [
            {
                "tool": "compute_score_stats_tool",
                "success": True,
                "content": "成绩统计完成：共 52 人，均分 108.5，满分 150，及格线 45，优秀线 75，及格率 100%，优秀率 96.15%，标准差 21.73。",
                "data": {
                    "count": 52,
                    "avg": 108.5,
                    "full_score": 150,
                    "pass_line": 45,
                    "excellent_line": 75,
                    "pass_rate": 100.0,
                    "excellent_rate": 96.15,
                },
            }
        ]
    )
    assert "count=52" in stats
    assert "及格线=45" in stats
    assert "报告权威 KPI" in stats


def test_format_sub_tasks_block_flags_offset_sample():
    class _FakeReply:
        content = "全班参考人数 52 人，卷面满分 150，及格线 45，优秀线 75。"

    class _FakeState:
        last_sql = (
            "SELECT student_id, score FROM tb_score "
            "WHERE class='高三(10)班' ORDER BY student_id LIMIT 20 OFFSET 40"
        )
        last_exec_result = {
            "columns": ["student_id", "score"],
            "rows": [{"student_id": f"s{i}", "score": 80 + i} for i in range(12)],
            "row_count": 12,
        }
        tool_calls = [
            {
                "tool": "compute_score_stats_tool",
                "success": True,
                "content": "成绩统计完成：共 52 人",
                "data": {
                    "count": 52,
                    "avg": 108.5,
                    "full_score": 150.0,
                    "pass_line": 45.0,
                    "excellent_line": 75.0,
                    "pass_rate": 100.0,
                    "excellent_rate": 96.15,
                },
            }
        ]
        reports = []

    class _FakePhase:
        is_success = True
        fail_reason = ""
        reply = _FakeReply()
        state = _FakeState()

    block = _format_sub_tasks_block([("班级明细", _FakePhase())])
    assert "OFFSET" in block
    assert "禁止当班级总人数" in block or "禁止" in block
    assert "count=52" in block
    assert "参考人数 52" in block
    assert "明细样例已省略" in block
    assert "报告权威 KPI" in block or "权威" in block
    assert "| student_id |" not in block


def test_truncate_keeping_kpi_lines_preserves_pass_line():
    from src.agent.education.summary_context import truncate_keeping_kpi_lines

    long = "前言\n" + ("x" * 1500) + "\n参考人数 52 人\n及格线 45\n优秀线 75\n"
    out = truncate_keeping_kpi_lines(long, limit=800)
    assert "参考人数 52" in out
    assert "及格线 45" in out


def test_reconcile_summary_kpis_rewrites_wrong_pass_line():
    from src.agent.education.summary_context import reconcile_summary_kpis

    draft = (
        "邗江中学高三(1)班本次地理考试班级总览报告已生成。\n"
        "本次考试参考人数为 20 人，考试科目为地理，卷面满分 100 分，"
        "及格线 60.0 分、优秀线 85.0 分。"
    )
    stats = {
        "count": 20,
        "pass_line": 70.0,
        "excellent_line": 85.0,
        "full_score": 100,
    }
    out = reconcile_summary_kpis(draft, stats)
    assert "及格线 70.0" in out
    assert "及格线 60" not in out
    assert "优秀线 85.0" in out
    assert "参考人数为 20 人" in out


def test_reconcile_summary_kpis_noop_without_stats():
    from src.agent.education.summary_context import reconcile_summary_kpis

    draft = "及格线 60.0 分，优秀线 85.0 分。"
    assert reconcile_summary_kpis(draft, None) == draft
    assert reconcile_summary_kpis(draft, {}) == draft


def test_reconcile_summary_kpis_rewrites_pass_rate_and_headcount():
    from src.agent.education.summary_context import reconcile_summary_kpis

    draft = (
        "年级参考人数20人，年级均分110.24，及格率91.07%，优秀率13.15%，"
        "标准差15.91，及格线 60 分。"
    )
    out = reconcile_summary_kpis(
        draft,
        {
            "count": 829,
            "avg": 110.24,
            "pass_rate": 99.16,
            "excellent_rate": 94.45,
            "stdev": 15.91,
            "pass_line": 90.0,
            "excellent_line": 127.5,
            "full_score": 150,
        },
    )
    assert "参考人数829人" in out or "参考人数 829 人" in out or "参考人数829" in out
    assert "829" in out
    assert "20人" not in out and "20 人" not in out
    assert "及格率99.16%" in out or "及格率 99.16%" in out
    assert "优秀率94.45%" in out or "优秀率 94.45%" in out
    assert "及格线 90.0" in out
    assert "91.07" not in out
    assert "13.15" not in out


def test_reconcile_summary_kpis_fixes_headcount_label():
    from src.agent.education.summary_context import reconcile_summary_kpis

    draft = "参考人数为 12 人，及格线 90 分、优秀线 127.5 分。"
    out = reconcile_summary_kpis(
        draft,
        {"count": 52, "pass_line": 45.0, "excellent_line": 75.0},
    )
    assert "参考人数为 52 人" in out
    assert "及格线 45.0" in out
    assert "优秀线 75.0" in out


def test_extract_stats_prefers_report_html_over_preview_stats():
    from src.agent.education.summary_context import extract_stats_authority_data

    html = (
        '<div class="edu-kpi"><div class="label">参考人数</div>'
        '<div class="value">829</div></div>'
        '<div class="edu-kpi"><div class="label">平均分</div>'
        '<div class="value">110.24</div></div>'
        '<div class="edu-kpi"><div class="label">及格率</div>'
        '<div class="value">99.16%</div></div>'
        '<div class="edu-kpi"><div class="label">优秀率</div>'
        '<div class="value">94.45%</div></div>'
    )
    stats = extract_stats_authority_data(
        [
            {
                "tool": "compute_score_stats_tool",
                "success": True,
                "content": "成绩统计完成：共 20 人",
                "data": {
                    "count": 20,
                    "avg": 108.0,
                    "pass_rate": 80.0,
                    "excellent_rate": 10.0,
                },
            }
        ],
        reports=[{"title": "班级横向对比", "html": html}],
    )
    assert stats is not None
    assert stats["count"] == 829
    assert stats["pass_rate"] == 99.16
    assert stats["excellent_rate"] == 94.45
    assert stats["avg"] == 110.24


def test_extract_stats_authority_block_uses_report_kpi_title():
    from src.agent.education.summary_context import extract_stats_authority_block

    stats = extract_stats_authority_block(
        [
            {
                "tool": "compute_score_stats_tool",
                "success": True,
                "content": "成绩统计完成：共 52 人，均分 108.5，满分 150，及格线 45，优秀线 75，及格率 100%，优秀率 96.15%，标准差 21.73。",
                "data": {
                    "count": 52,
                    "avg": 108.5,
                    "full_score": 150,
                    "pass_line": 45,
                    "excellent_line": 75,
                    "pass_rate": 100.0,
                    "excellent_rate": 96.15,
                },
            }
        ]
    )
    assert "报告权威 KPI" in stats
    assert "count=52" in stats
    assert "及格线=45" in stats


def test_extract_exec_authority_ignores_limit_preview():
    from src.agent.education.summary_context import (
        extract_exec_authority_data,
        extract_stats_authority_data,
    )

    assert (
        extract_exec_authority_data(
            sql="SELECT student_id, score FROM t LIMIT 20",
            row_count=20,
            columns=["student_id", "score"],
        )
        is None
    )
    auth = extract_exec_authority_data(
        sql="SELECT student_id, score FROM t",
        row_count=829,
        columns=["student_id", "score"],
    )
    assert auth == {"count": 829}

    # 纯 SQL：无报告时也能用 uncapped execute_sql 行数纠错
    stats = extract_stats_authority_data(
        [
            {
                "tool": "execute_sql",
                "success": True,
                "data": {
                    "sql": "SELECT student_id, score FROM tb_score WHERE class='x'",
                    "columns": ["student_id", "score"],
                    "row_count": 829,
                    "sql_row_capped": False,
                },
            }
        ]
    )
    assert stats is not None
    assert stats["count"] == 829


def test_reconcile_loose_headcount_and_scrub_without_stats():
    from src.agent.education.summary_context import (
        reconcile_answer_with_artifacts,
        reconcile_summary_kpis,
        scrub_preview_headcount_claims,
    )

    draft = "本次共 20 人参考，20 名学生达标，所查看的 20 名表现中等。"
    out = reconcile_summary_kpis(draft, {"count": 829})
    assert "829" in out
    assert "共 20 人" not in out
    assert "20 名学生" not in out

    scrubbed = scrub_preview_headcount_claims("年级参考人数20人，均分尚可。")
    assert "20人" not in scrubbed
    assert "预览" in scrubbed or "全量" in scrubbed

    # 无权威时走 scrub；有 execute_sql 全量时改写
    fixed = reconcile_answer_with_artifacts(
        "参考人数为 20 人，及格率 80%。",
        tool_calls=[
            {
                "tool": "execute_sql",
                "success": True,
                "data": {
                    "sql": "SELECT student_id, score FROM t",
                    "columns": ["student_id", "score"],
                    "rows": [],
                    "row_count": 829,
                    "sql_row_capped": False,
                },
            }
        ],
    )
    assert "829" in fixed
    assert "20 人" not in fixed


def test_audit_and_reconcile_markdown_kpi_table():
    from src.agent.education.summary_context import (
        audit_summary_kpi_claims,
        reconcile_summary_kpis,
    )

    draft = (
        "## 关键指标\n"
        "| 指标 | 数值 |\n"
        "| --- | --- |\n"
        "| 参考人数 | 20 人 |\n"
        "| 均分 | 108.5 |\n"
        "| 及格率 | 91.07% |\n"
        "| 优秀率 | 13.15% |\n"
        "| 卷面满分 | 100 |\n"
        "| 及格线 | 60 |\n"
        "| 标准差 | 12.3 |\n"
    )
    stats = {
        "count": 829,
        "avg": 110.24,
        "pass_rate": 99.16,
        "excellent_rate": 94.45,
        "full_score": 150,
        "pass_line": 90.0,
        "stdev": 15.91,
    }
    conflicts = audit_summary_kpi_claims(draft, stats)
    fields = {c.field for c in conflicts}
    assert "count" in fields
    assert "pass_rate" in fields
    assert "avg" in fields

    out = reconcile_summary_kpis(draft, stats)
    assert "| 参考人数 | 829" in out
    assert "| 均分 | 110.24 |" in out
    assert "| 及格率 | 99.16% |" in out
    assert "| 优秀率 | 94.45% |" in out
    assert "| 卷面满分 | 150" in out
    assert "| 及格线 | 90.0" in out
    assert "| 标准差 | 15.91 |" in out
    assert not audit_summary_kpi_claims(out, stats)


def test_reconcile_dual_narrative_and_md_table():
    from src.agent.education.summary_context import (
        audit_summary_kpi_claims,
        reconcile_answer_with_artifacts_detailed,
    )

    draft = (
        "年级及格率91.07%，表现尚可。\n"
        "| 及格率 | 91.07% |\n"
        "| 均分 | 100 |\n"
    )
    stats = {
        "count": 829,
        "avg": 110.24,
        "pass_rate": 99.16,
        "excellent_rate": 94.45,
    }
    # 通过 tool_calls 注入权威（compute_score_stats）
    out, conflicts = reconcile_answer_with_artifacts_detailed(
        draft,
        tool_calls=[
            {
                "tool": "compute_score_stats_tool",
                "success": True,
                "content": "成绩统计完成",
                "data": stats,
            }
        ],
    )
    assert any(c.field == "pass_rate" for c in conflicts)
    assert "91.07" not in out
    assert "及格率99.16%" in out or "及格率 99.16%" in out
    assert "| 及格率 | 99.16% |" in out
    assert "| 均分 | 110.24 |" in out
    assert not audit_summary_kpi_claims(out, stats)


def test_scrub_residual_conflicting_rate_token():
    from src.agent.education.summary_context import (
        KpiClaimConflict,
        scrub_residual_conflicting_values,
    )

    # 标签已对齐，但叙述残留「约 91.07」
    draft = "及格率 99.16%，约 91.07 的旧口径不再采用。"
    stats = {"pass_rate": 99.16, "count": 829}
    # 模拟审计阶段仍看到旧声明（在改写前）
    conflicts = [
        KpiClaimConflict(field="pass_rate", claimed=91.07, authority=99.16, span="及格率91.07%"),
    ]
    out = scrub_residual_conflicting_values(draft, conflicts, stats)
    assert "91.07" not in out
    assert "99.16" in out


def test_audit_skips_missing_authority_fields():
    from src.agent.education.summary_context import audit_summary_kpi_claims

    draft = "及格率 80%，均分 100，参考人数为 50 人。"
    # 仅有 count，不应对 pass_rate/avg 误报
    conflicts = audit_summary_kpi_claims(draft, {"count": 50})
    assert conflicts == []

    conflicts2 = audit_summary_kpi_claims(draft, None)
    assert conflicts2 == []


def test_reconcile_detailed_no_stats_scrubs_preview():
    from src.agent.education.summary_context import reconcile_answer_with_artifacts_detailed

    out, conflicts = reconcile_answer_with_artifacts_detailed(
        "年级参考人数20人，均分尚可。",
        tool_calls=[],
    )
    assert conflicts == []
    assert "20人" not in out


def test_scrub_implementation_details_drops_table_and_sql():
    from src.agent.education.summary_context import (
        append_keepable_result_tables,
        scrub_implementation_details,
    )

    raw = (
        "物理相对全市最好。\n"
        "统计依据：2026届高三1月期末、tb_score_overview、在籍生、"
        "AVG FILTER WHERE col>0。\n"
        "```sql\nSELECT xx FROM tb_score_overview\n```"
    )
    out = scrub_implementation_details(raw)
    assert "tb_score_overview" not in out
    assert "AVG FILTER" not in out
    assert "SELECT" not in out
    assert "物理相对全市最好" in out
    assert "2026届高三1月期末" in out
    assert "在籍生" in out
    assert "已排除未选考" in out

    kept = append_keepable_result_tables(
        out,
        [{
            "columns": ["subject", "avg_score", "city_rank"],
            "rows": [{"subject": "物理", "avg_score": 82.1, "city_rank": 8}],
            "row_count": 1,
            "sql": "SELECT subject FROM tb_score_overview LIMIT 1000",
        }],
    )
    kept = scrub_implementation_details(kept)
    assert "| 学科 | 均分 | 全市排名 |" in kept
    assert "tb_score_overview" not in kept


def test_keepable_subject_rank_table_stays_in_summary():
    """各科全市排名小表应进总结；学生 LIMIT 预览表不应进。"""
    from src.agent.education.summary_context import (
        append_keepable_result_tables,
        looks_like_keepable_result_table,
    )

    cols = ["subject", "avg_score", "city_rank"]
    rows = [
        {"subject": "数学", "avg_score": 112.3, "city_rank": 2},
        {"subject": "语文", "avg_score": 108.1, "city_rank": 7},
    ]
    assert looks_like_keepable_result_table(
        cols, row_count=2, sql="SELECT ... LIMIT 1000"
    )
    out = append_keepable_result_tables(
        "新华中学优势学科是数学。",
        [{"columns": cols, "rows": rows, "row_count": 2, "sql": "SELECT ... LIMIT 1000"}],
    )
    assert "| 学科 | 均分 | 全市排名 |" in out
    assert "数学" in out and "112.3" in out
    assert "查询结果" in out

    same = append_keepable_result_tables(out, [
        {"columns": cols, "rows": rows, "row_count": 2, "sql": "SELECT ... LIMIT 1000"}
    ])
    assert same.count("| 数学 |") == 1

    student_cols = ["student_id", "score"]
    student_sql = "SELECT student_id, score FROM tb_score LIMIT 20"
    assert looks_like_keepable_result_table(
        student_cols, row_count=20, sql=student_sql
    ) is False
    skipped = append_keepable_result_tables(
        "该班成绩已查出。",
        [{
            "columns": student_cols,
            "rows": [{"student_id": f"s{i}", "score": 80} for i in range(20)],
            "row_count": 20,
            "sql": student_sql,
        }],
    )
    assert "| student_id |" not in skipped


def test_format_sub_tasks_block_keeps_subject_rank_table():
    class _FakeReply:
        content = "优势学科是数学。"

    class _FakeState:
        last_sql = (
            "SELECT subject, avg_score, city_rank FROM school_rank LIMIT 1000"
        )
        last_exec_result = {
            "columns": ["subject", "avg_score", "city_rank"],
            "rows": [
                {"subject": "数学", "avg_score": 112.3, "city_rank": 2},
                {"subject": "语文", "avg_score": 108.1, "city_rank": 7},
            ],
            "row_count": 2,
        }
        tool_calls = []
        reports = []

    class _FakePhase:
        is_success = True
        fail_reason = ""
        reply = _FakeReply()
        state = _FakeState()

    block = _format_sub_tasks_block(
        [("各科全市排名", _FakePhase())],
        fact_answer=True,
    )
    assert "| 学科 | 均分 | 全市排名 |" in block
    assert "须原样保留" in block
    assert "明细已省略" not in block


def test_truncate_keeping_kpi_lines_preserves_markdown_table():
    from src.agent.education.summary_context import truncate_keeping_kpi_lines

    table = (
        "| subject | city_rank |\n"
        "| --- | --- |\n"
        "| 数学 | 2 |\n"
        "| 语文 | 7 |\n"
    )
    long = "前言\n" + ("x" * 1500) + "\n" + table + "参考人数 52 人\n"
    out = truncate_keeping_kpi_lines(long, limit=800)
    assert "| 数学 | 2 |" in out
    assert "参考人数 52" in out


def test_relabel_result_table_headers_is_chinese_only_on_header():
    from src.agent.education.summary_context import (
        display_column_label,
        relabel_result_table_headers,
        result_table_already_in_text,
    )

    assert display_column_label("scope") == "范围"
    assert display_column_label("avg_score") == "均分"
    assert display_column_label("ref_count") == "参考人数"
    assert display_column_label("pass_rate") == "及格率"

    raw = (
        "新华中学各科均分如下。\n"
        "| scope | subject | avg_score | ref_count | pass_rate |\n"
        "| --- | --- | --- | --- | --- |\n"
        "| 新华中学 | 语文 | 96.14 | 839 | 98.2 |\n"
    )
    out = relabel_result_table_headers(raw)
    assert "| 范围 | 学科 | 均分 | 参考人数 | 及格率 |" in out
    assert "| 新华中学 | 语文 | 96.14 | 839 | 98.2 |" in out
    assert "avg_score" not in out
    assert result_table_already_in_text(
        ["scope", "subject", "avg_score"],
        out,
    )
