"""教育权限与问数范围约束合并测试。"""

from __future__ import annotations

from src.agent.education.query_parse import (
    build_edu_aware_constraints,
    extract_school_target,
    format_scope_constraints,
)


def test_extract_school_target_ignores_province_exam_label():
    q = "帮我查询江苏省高一上学期数学期末质量检测的成绩，形成详细的分析报告"
    assert extract_school_target(q) is None


def test_build_edu_aware_constraints_teacher_uses_bound_school():
    edu = {
        "edu_role": "teacher",
        "edu_role_label": "班级（老师）",
        "school_id": "SCH001",
        "school_name": "南京市第一中学",
        "class_names": ["高一(1)班", "高一(2)班"],
    }
    q = "帮我查询江苏省高一上学期数学期末质量检测的成绩，形成详细的分析报告"
    ctx = build_edu_aware_constraints(q, edu, required_keywords=["江苏省", "数学"])
    assert ctx["target_school"] == "南京市第一中学"
    assert ctx["target_classes"] == ["高一(1)班", "高一(2)班"]
    assert ctx["edu_scope"]["edu_role"] == "teacher"


def test_format_scope_constraints_warns_against_province_as_school():
    text = format_scope_constraints(
        {
            "edu_scope": {
                "edu_role": "teacher",
                "edu_role_label": "班级（老师）",
                "school_name": "南京市第一中学",
                "class_names": ["高一(1)班"],
            },
            "target_classes": ["高一(1)班", "高一(2)班"],
            "target_school": "南京市第一中学",
        }
    )
    assert "南京市第一中学" in text
    assert "江苏省" in text
    assert "禁止" in text


def test_format_scope_constraints_school_id_only_distinguishes_from_sch_name():
    text = format_scope_constraints(
        {
            "edu_scope": {
                "edu_role": "teacher",
                "edu_role_label": "班级（老师）",
                "school_id": "YZZX",
                "class_names": ["高三(10)班"],
            },
            "target_classes": ["高三(10)班"],
            "target_school": "YZZX",
        }
    )
    assert "权限绑定学校ID=YZZX" in text
    assert "sc.school_id" in text
    assert "sch.name" in text


def test_build_edu_aware_constraints_falls_back_to_school_id():
    edu = {
        "edu_role": "teacher",
        "school_id": "YZZX",
        "school_name": "",
        "class_names": ["高三(10)班"],
    }
    ctx = build_edu_aware_constraints(
        "2026年江苏省高三数学第一次模拟考试试卷 成绩分析",
        edu,
    )
    assert ctx["target_school"] == "YZZX"
    assert ctx["target_classes"] == ["高三(10)班"]
