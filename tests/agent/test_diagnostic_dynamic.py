"""区域结构化诊断：多场考试动态性（S3）组装。"""

from src.agent.education.aggregation import prepare_score_rows_for_kpi
from src.agent.education.diagnostic_report import (
    _exam_avg_trend_from_rows,
    _student_progress_from_rows,
    build_diagnostic_data,
)


def _multi_exam_rows():
    rows = []
    for exam, base in [("一模", 100.0), ("二模", 120.0)]:
        for i, bump in enumerate([0.0, -8.0, 15.0]):
            rows.append(
                {
                    "exam_name": exam,
                    "student_id": f"S{i}",
                    "student_name": f"学生{i}",
                    "score": base + bump,
                    "exam_score": 150,
                    "subject": "数学",
                    "class": "高一1班",
                    "school_name": "扬州中学",
                }
            )
    return rows


def test_student_progress_from_multi_exam():
    rows = _multi_exam_rows()
    prog = _student_progress_from_rows(rows)
    assert len(prog) == 3
    by_name = {p["name"]: p["delta"] for p in prog}
    assert by_name["学生0"] == 20.0  # 120-100
    assert by_name["学生1"] == 20.0  # 112-92
    assert by_name["学生2"] == 20.0  # 135-115


def test_diagnostic_s3_uses_progress_not_exam_avg_only():
    """S3 需要学生 delta；仅传考试均分趋势时须另算 progress。"""
    rows = _multi_exam_rows()
    kpi = prepare_score_rows_for_kpi(rows)
    assert len({r.get("exam_name") for r in kpi}) == 1  # KPI 被收敛为单场

    trend = _exam_avg_trend_from_rows(rows)
    assert len(trend) == 2
    progress = _student_progress_from_rows(rows)

    data = build_diagnostic_data(
        kpi,
        trend_records=trend,
        progress_records=progress,
        scope_label="扬州中学",
        exam_name="一模、二模",
        subject_name="数学",
    )
    assert "暂无多次考试数据" not in data["DYNAMIC_INSIGHT"]
    assert "2" in data["DYNAMIC_INSIGHT"]
    assert data["GENERAL_TREND_CHART"]
    assert data["TREND_LINE_CHART"] or data["PROGRESS_REGRESS_TABLE"]
    assert "trend_line" in data["GENERAL_TREND_CHART"] or "line" in data["GENERAL_TREND_CHART"]


def test_diagnostic_s2_regression_matches_s3():
    """多场时 S2 退步生人数与 S3 退步人数一致。"""
    import re

    rows = _multi_exam_rows()
    # 让部分学生明显退步：二模比一模低约 20+
    for r in rows:
        if r["exam_name"] == "二模" and r["student_name"] in ("学生0", "学生1"):
            r["score"] = float(r["score"]) - 40

    kpi = prepare_score_rows_for_kpi(rows)
    trend = _exam_avg_trend_from_rows(rows)
    progress = _student_progress_from_rows(rows)
    data = build_diagnostic_data(
        kpi,
        trend_records=trend,
        progress_records=progress,
        scope_label="扬州中学",
        exam_name="一模、二模",
        subject_name="数学",
    )
    m_s2 = re.search(r"退步生\s*(\d+)\s*人", data["AT_RISK_SUMMARY"])
    m_s3 = re.search(r"退步\s*(\d+)\s*人", data["DYNAMIC_INSIGHT"])
    assert m_s2 and m_s3
    assert int(m_s2.group(1)) == int(m_s3.group(1))
    assert int(m_s3.group(1)) >= 1
