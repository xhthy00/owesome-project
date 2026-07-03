"""query_parse 与 student_exam 数据组装测试。"""

from src.agent.education.query_parse import (
    extract_school_target,
    extract_student_target,
    extract_upstream_participant_count,
    report_matches_school,
    report_matches_student,
    report_participant_count_conflicts,
    student_matches,
)
from src.agent.education.student_exam import build_student_exam_data


def _sample_class_records():
    exams = ["一模", "二模", "三模"]
    records = []
    for i, exam in enumerate(exams):
        for sid, (yw, sx, yy, total) in [
            ("学生001", (120, 99, 130, 349)),
            ("学生009", (90, 110, 95, 295)),
        ]:
            records.append({
                "exam": exam,
                "student": sid,
                "subjects": {"语文": yw + i, "数学": sx, "英语": yy + i},
                "total": total + i * 5,
            })
    return records


def test_extract_student_target_from_quoted_name():
    assert extract_student_target('分析"学生001"这几次考试的成绩') == "学生001"


def test_extract_school_target_from_full_name():
    q = "帮我分析南京市第一中学在江苏省高一上学期数学期末质量检测的成绩"
    assert extract_school_target(q) == "南京市第一中学"


def test_extract_school_target_from_quoted_name():
    assert extract_school_target('分析「北京市第四中学」数学成绩') == "北京市第四中学"


def test_extract_school_target_returns_none_when_absent():
    assert extract_school_target("查询全校数学平均分") is None


def test_student_matches_aliases():
    assert student_matches("学生 001", "学生001")
    assert student_matches("001", "学生001")


def test_report_matches_student_filters_wrong_student():
    assert report_matches_student("学生001 报告", "<h1>学生001</h1>", "学生001")
    assert not report_matches_student("学生009 报告", "<h1>学生009</h1>", "学生001")


def test_report_matches_school_filters_wrong_scope():
    html = "<span>南京市第一中学</span><p>参考人数 24 人</p>"
    assert report_matches_school("南京市第一中学数学诊断", html, "南京市第一中学")
    wrong = "<span>高一(1)班+高一(2)班</span><p>参考人数 40 人</p>"
    assert not report_matches_school("高一数学学科诊断报告", wrong, "南京市第一中学")


def test_extract_upstream_participant_count_from_final_answer():
    count = extract_upstream_participant_count({
        "sub_tasks": [
            {
                "sub_task_agent": "DataAnalyst",
                "final_answer": "共 24 人，均分 78",
                "exec_result": {"columns": ["学生"], "rows": [], "row_count": 0},
            }
        ]
    })
    assert count == 24


def test_report_participant_count_conflicts_detects_mismatch():
    html = "<p>参考人数 40 人</p>"
    assert report_participant_count_conflicts(html, 24)
    assert not report_participant_count_conflicts("<p>参考人数 24 人</p>", 24)


def test_build_student_exam_data_rich_sections():
    records = _sample_class_records()
    data = build_student_exam_data(records, "学生001", exam_order=["一模", "二模", "三模"], class_name="初三1班")
    assert "学生001" in data["REPORT_TITLE"]
    assert "一、总体成绩概览" not in data["REPORT_TITLE"]  # title is report name
    assert "349" in data["SCORE_SUMMARY_TABLE"] or "354" in data["SCORE_SUMMARY_TABLE"]
    assert data["SUBJECT_ANALYSIS_HTML"]
    assert "五、总体结论" not in data["ASSESSMENT"]  # assessment is content not header
    assert data["TOTAL_TREND_CHART"]
    assert data["CLASS_DIFF_TABLE"]


def test_build_student_exam_only_one_student_in_title():
    records = _sample_class_records()
    data = build_student_exam_data(records, "学生001", exam_order=["一模", "二模", "三模"])
    assert "学生009" not in data["REPORT_TITLE"]
