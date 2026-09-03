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
    assert "禁止" in text
    assert "s_name" in text


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


def test_citywide_question_does_not_overwrite_named_school():
    edu = {
        "edu_role": "school_admin",
        "school_name": "南京市第一中学",
        "school_id": "SCH001",
    }
    ctx = build_edu_aware_constraints(
        "2026届高三1月期末扬州中学物理类均分与全市的对比",
        edu,
    )
    assert ctx["target_school"] != "南京市第一中学"
    assert "扬州中学" in (ctx["target_school"] or "")


def test_own_school_vs_city_uses_bound_school():
    edu = {
        "edu_role": "school_admin",
        "school_name": "扬州中学",
        "school_id": "GZ_19D9D68D",
    }
    ctx = build_edu_aware_constraints(
        "2026届高三1月期末本校物理类均分与全市的对比",
        edu,
    )
    assert ctx["target_school"] == "扬州中学"
    edu = {
        "edu_role": "school_admin",
        "school_name": "南京市第一中学",
        "school_id": "SCH001",
    }
    ctx = build_edu_aware_constraints("2026届高三1月期末全市物理类均分", edu)
    assert not ctx.get("target_school")


def test_teacher_gexiao_query_still_binds_own_school():
    """「各校」是报告范围口语，不放开老师的本校过滤。"""
    edu = {
        "edu_role": "teacher",
        "school_name": "南京市第一中学",
        "school_id": "SCH001",
        "class_names": ["高三(1)班"],
    }
    ctx = build_edu_aware_constraints("2026届高三1月各校考试分析", edu)
    assert ctx["target_school"] == "南京市第一中学"
    assert ctx["target_classes"] == ["高三(1)班"]


def test_format_scope_constraints_allows_citywide_metrics():
    text = format_scope_constraints(
        {
            "edu_scope": {
                "edu_role": "school_admin",
                "edu_role_label": "学校（校长）",
                "school_name": "扬州中学",
            },
            "target_school": "扬州中学",
        }
    )
    assert "全市" in text
    assert "GROUP BY xx" in text
    assert "明细" in text
