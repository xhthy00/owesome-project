
from src.agent.education.privacy_mode import (
    clear_anonymize_display_cache,
    privacy_conclusion_brief,
    privacy_conclusion_constraint,
    privacy_student_hint,
    privacy_who_ref,
    set_anonymize_display_cached,
)


def teardown_function() -> None:
    clear_anonymize_display_cache()


def test_privacy_helpers_allow_name_when_anonymize_off() -> None:
    set_anonymize_display_cached(False)
    hint = privacy_student_hint()
    assert "给出姓名" in hint
    assert "tb_score_overview.xm" in hint
    assert "禁止查 st.xm" in hint
    assert "未明确要求「学号」" in hint
    assert "学校编号" in hint
    assert "优先写姓名" in privacy_conclusion_brief()
    assert "不要写学号" in privacy_conclusion_brief()
    assert "不要写学号" in privacy_conclusion_constraint()
    assert privacy_who_ref() == "姓名"
    assert "禁止写中文姓名" not in hint


def test_privacy_sql_instruction_does_not_treat_xh_as_student_id() -> None:
    from src.agent.education.privacy_mode import privacy_sql_instruction

    set_anonymize_display_cached(False)
    off = privacy_sql_instruction()
    assert "xh（学号）" not in off
    assert "anon_stu_id（学号）" not in off
    assert "未明确要求「学号」" in off
    assert "学校编号" in off
    assert "地区编号" in off

    set_anonymize_display_cached(True)
    on = privacy_sql_instruction()
    assert "真实学号" not in on
    assert "未明确要求「学号」" in on
    assert "学校编号" in on


def test_privacy_helpers_hide_name_when_anonymize_on() -> None:
    set_anonymize_display_cached(True)
    hint = privacy_student_hint()
    assert "姓名明文" in hint
    assert "未明确要求「学号」" in hint
    assert "一律写 student_id" not in hint
    assert "输出中文姓名明文" in privacy_conclusion_brief()
    assert "不要写学号" in privacy_conclusion_constraint()
    assert privacy_who_ref() == "学校、班级与分数（勿写学号）"


def test_describe_note_points_to_overview_xm_when_off() -> None:
    from src.agent.education.privacy_mode import privacy_describe_student_note

    set_anonymize_display_cached(False)
    note = privacy_describe_student_note("tb_student")
    assert note is not None
    assert "tb_score_overview.xm" in note
    assert "st.xm" in note
    ov = privacy_describe_student_note("tb_score_overview")
    assert ov is not None
    assert "SELECT `xm`" in ov
    assert "未要求学号" in ov
    assert "学校编号" in ov


def test_overlay_comments_allow_overview_xm_when_off() -> None:
    from src.agent.education.privacy_mode import overlay_table_comments

    set_anonymize_display_cached(False)
    out = overlay_table_comments(
        {
            "tb_score_overview": "学生标识只用 anon_stu_id。禁止 SELECT xm/sfzh/ksh。其余",
            "tb_student": "学生维度表（id 为学号，无姓名字段）",
        }
    )
    assert "禁止 SELECT xm/sfzh/ksh" not in out["tb_score_overview"]
    assert "必须 SELECT xm" in out["tb_score_overview"]
    assert "tb_score_overview.xm" in out["tb_student"]
