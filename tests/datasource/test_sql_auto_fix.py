"""SQL 自动修正单元测试。"""

from __future__ import annotations

from datasource.service.sql_auto_fix import suggest_sql_fix


def test_fix_st_student_id_from_error():
    sql = (
        "SELECT sc.score FROM tb_score sc "
        "JOIN tb_student st ON sc.student_id = st.student_id LIMIT 10"
    )
    err = 'SQL execution failed: column st.student_id does not exist HINT: Perhaps you meant sc.student_id'
    out = suggest_sql_fix(sql, err, "pg")
    assert out is not None
    fixed, desc = out
    assert "st.id" in fixed
    assert "st.student_id" not in fixed
    assert "tb_student" in desc


def test_fix_sd_school_id_and_add_score_join():
    sql = (
        "SELECT sd.question_no FROM tb_score_detail sd "
        "WHERE sd.school_id = 'NJYZ' LIMIT 10"
    )
    err = 'SQL execution failed: column sd.school_id does not exist'
    out = suggest_sql_fix(sql, err, "pg")
    assert out is not None
    fixed, _ = out
    assert 'sc."school_id"' in fixed or "sc.school_id" in fixed
    assert "JOIN tb_score sc" in fixed


def test_fix_ambiguous_class():
    sql = (
        "SELECT sc.score FROM tb_score sc JOIN tb_score_detail sd "
        "ON sd.student_id = sc.student_id WHERE \"class\" = '高一(2)班'"
    )
    err = 'column reference "class" is ambiguous'
    out = suggest_sql_fix(sql, err, "pg")
    assert out is not None
    fixed, _ = out
    assert 'sc."class"' in fixed


def test_fix_missing_sd_reference():
    sql = (
        "SELECT sc.score FROM tb_score sc "
        "WHERE sc.subject_name = '数学' AND sd.exam_id IN ('1')"
    )
    err = 'missing FROM-clause entry for table "sd"'
    out = suggest_sql_fix(sql, err, "pg")
    assert out is not None
    fixed, _ = out
    assert "sc.exam_id IN ('1')" in fixed
    assert "sd.exam_id" not in fixed


def test_no_fix_for_unknown_error():
    sql = "SELECT 1"
    assert suggest_sql_fix(sql, "syntax error at foo", "pg") is None
