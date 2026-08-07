"""report_quality 与 edu_e2e 判分单元测试（不调 LLM）。"""

from src.agent.edu_e2e_eval import score_case, summarize_results
from src.agent.education.report_quality import (
    report_html_empty_exam_signals,
    report_html_has_all_dash_score_table,
    report_html_is_sparse,
    report_html_quality_issues,
)


def test_report_html_zero_exams_and_dashes():
    html = (
        '<div class="insight">共分析 <strong>0</strong> 次考试（）。'
        "总分在 -–- 区间波动</div>"
        '<table class="edu-table"><tr><th>考试</th><th>总分</th></tr>'
        "<tr><td>-</td><td>-</td></tr>"
        "<tr><td>-</td><td>-</td></tr>"
        "<tr><td>-</td><td>-</td></tr>"
        "</table>"
    )
    assert report_html_empty_exam_signals(html) is True
    assert report_html_has_all_dash_score_table(html) is True
    issues = report_html_quality_issues(html)
    assert "zero_exams" in issues
    assert "all_dash_scores" in issues


def test_report_html_sparse_kpi_dashes():
    html = (
        '<div class="value">-</div><div class="value">-</div>'
        '<div class="value">-</div><p>空</p>'
    )
    assert report_html_is_sparse(html) is True


def test_score_case_fact_rejects_formal_report():
    case = {
        "id": "f1",
        "expect_needs_report": False,
        "expect_report_type": None,
    }
    events = [
        {
            "event": "report",
            "data": {
                "report_type": "subject_diagnosis",
                "report_type_label": "科目诊断报告",
                "html": "<h1>数学诊断</h1><table><tr><td>1</td></tr></table>" * 3,
            },
        },
        {"event": "summary", "data": {"content": "最高分是 135"}},
    ]
    route = {"needs_report": False, "report_type": None}
    scored = score_case(case, events=events, route=route)
    assert scored["route_ok"] is True
    assert scored["pass"] is False
    assert any(str(x).startswith("unexpected_report") for x in scored["fail_reasons"])


def test_score_case_student_profile_empty():
    case = {
        "id": "s1",
        "expect_needs_report": True,
        "expect_report_type": "student_profile",
        "forbid_tools": ["build_subject_diagnosis_sections_tool"],
    }
    events = [
        {
            "event": "report",
            "data": {
                "report_type": "student_profile",
                "html": "共分析 <strong>0</strong> 次考试（）。",
            },
        }
    ]
    route = {"needs_report": True, "report_type": "student_profile"}
    scored = score_case(case, events=events, route=route)
    assert scored["route_ok"] is True
    assert scored["pass"] is False
    assert any("zero_exams" in str(x) for x in scored["fail_reasons"])


def test_summarize_results_by_category():
    results = [
        {
            "category": "fact_lookup",
            "pass": True,
            "route_ok": True,
            "e2e_ok": True,
            "fail_reasons": [],
        },
        {
            "category": "fact_lookup",
            "pass": False,
            "route_ok": True,
            "e2e_ok": False,
            "fail_reasons": ["no_answer"],
        },
        {
            "category": "student_profile",
            "pass": False,
            "route_ok": False,
            "e2e_ok": False,
            "fail_reasons": ["wrong_report_type:got='x'"],
        },
    ]
    summary = summarize_results(results)
    assert summary["total"] == 3
    assert summary["pass"] == 1
    assert summary["categories"]["fact_lookup"]["pass"] == 1
    assert "wrong_report_type" in summary["fail_reason_totals"]
