"""教育四级权限模板与谓词编译单元测试。"""

from __future__ import annotations

from types import SimpleNamespace

from datasource.service.edu_permission import (
    EduScope,
    build_edu_row_predicates,
    merge_edu_scope_into_variables,
    parse_edu_scope,
    validate_edu_scope,
)
from datasource.service.query_permission import apply_permissions_for_execute


def _user(**vars_):
    return SimpleNamespace(
        id=2,
        account="teacher1",
        system_variables=vars_,
    )


def test_parse_edu_scope_from_system_variables():
    u = _user(
        edu_role="teacher",
        school_id="1",
        school_name="南京市第一中学",
        class_names=["高一(1)班", "高一(2)班"],
    )
    scope = parse_edu_scope(u)
    assert scope.edu_role == "teacher"
    assert scope.school_id == "1"
    assert scope.class_names == ["高一(1)班", "高一(2)班"]


def test_validate_teacher_requires_class_names():
    scope = EduScope(edu_role="teacher", school_id="1", class_names=[])
    assert "class_names" in validate_edu_scope(scope)[0]


def test_parse_edu_scope_accepts_string_school_id():
    scope = EduScope.from_dict({"edu_role": "school_admin", "school_id": "SCH001"})
    assert scope.school_id == "SCH001"
    preds = build_edu_row_predicates(_user(edu_role="school_admin", school_id="SCH001"), "pg")
    assert '"school_id" = \'SCH001\'' in preds[0]


def test_build_predicates_school_admin():
    u = _user(edu_role="school_admin", school_id="3")
    preds = build_edu_row_predicates(u, "pg")
    assert len(preds) == 1
    assert '"school_id" = \'3\'' in preds[0]


def test_build_predicates_teacher_multi_class():
    u = _user(edu_role="teacher", school_id="1", class_names=["高一(1)班", "高一(2)班"])
    preds = build_edu_row_predicates(u, "pg")
    assert any('"school_id" = \'1\'' in p for p in preds)
    assert any("IN (" in p and "高一(1)班" in p for p in preds)


def test_build_predicates_student():
    u = _user(edu_role="student", student_id="STU20240002")
    preds = build_edu_row_predicates(u, "pg")
    assert preds == ['"student_id" = \'STU20240002\'']


def test_bureau_admin_no_predicates():
    u = _user(edu_role="bureau_admin")
    assert build_edu_row_predicates(u, "pg") == []


def test_no_edu_role_no_predicates():
    u = _user()
    assert build_edu_row_predicates(u, "pg") == []


def test_merge_edu_scope_preserves_other_variables():
    merged = merge_edu_scope_into_variables(
        {"custom_flag": True},
        EduScope(edu_role="student", student_id="S1"),
    )
    assert merged["custom_flag"] is True
    assert merged["edu_role"] == "student"
    assert merged["student_id"] == "S1"


def test_clear_edu_scope_preserves_other_variables():
    from datasource.service.edu_permission import clear_edu_scope_from_variables

    cleared = clear_edu_scope_from_variables(
        {
            "custom_flag": True,
            "edu_role": "teacher",
            "school_id": "1",
            "class_names": ["高一(1)班"],
        }
    )
    assert cleared == {"custom_flag": True}
    assert build_edu_row_predicates(_user(custom_flag=True), "pg") == []


def test_apply_permissions_merges_edu_predicates(monkeypatch):
    """未配置 ds_rules 时，edu 模板谓词仍应并入 SQL。"""
    u = _user(edu_role="school_admin", school_id="5")

    class FakeQuery:
        def filter(self, *_a, **_kw):
            return self

        def all(self):
            return []

        def first(self):
            return SimpleNamespace(id=1, oid=1)

    class FakeSession:
        def query(self, *_a, **_kw):
            return FakeQuery()

    sql = "SELECT sc.score FROM tb_score sc LIMIT 10"
    merged = apply_permissions_for_execute(FakeSession(), u, 1, "pg", sql)
    assert 'sc."school_id" = \'5\'' in merged


def test_apply_permissions_qualifies_class_on_join_query():
    """多表 JOIN 时 class/school_id 谓词应挂到 tb_score 别名，避免 ambiguous。"""
    from datasource.service.query_permission import qualify_edu_row_predicates

    sql = (
        "SELECT sd.question_no FROM tb_score_detail sd "
        "JOIN tb_score sc ON sd.exam_id = sc.exam_id LIMIT 10"
    )
    preds = ['"school_id" = \'NJYZ\'', '"class" IN (\'高一(2)班\')']
    qualified = qualify_edu_row_predicates(sql, preds, "pg")
    assert qualified == ['sc."school_id" = \'NJYZ\'', 'sc."class" IN (\'高一(2)班\')']


def test_qualify_student_id_on_tb_student_uses_id_column():
    from datasource.service.query_permission import qualify_edu_row_predicates

    sql = "SELECT st.id, sc.score FROM tb_student st JOIN tb_score sc ON sc.student_id = st.id"
    preds = ['"student_id" = \'STU20240002\'']
    qualified = qualify_edu_row_predicates(sql, preds, "pg")
    assert qualified == ['sc."student_id" = \'STU20240002\'']


def test_qualify_school_id_on_detail_only_uses_exists():
    from datasource.service.query_permission import qualify_edu_row_predicates

    sql = "SELECT sd.question_no FROM tb_score_detail sd LIMIT 10"
    preds = ['"school_id" = \'NJYZ\'']
    qualified = qualify_edu_row_predicates(sql, preds, "pg")
    assert len(qualified) == 1
    assert "EXISTS" in qualified[0]
    assert 'sc."school_id" = \'NJYZ\'' in qualified[0]
    assert "sd.\"exam_id\"" in qualified[0]


def test_qualify_student_id_on_detail_only():
    from datasource.service.query_permission import qualify_edu_row_predicates

    sql = "SELECT sd.score FROM tb_score_detail sd LIMIT 10"
    preds = ['"student_id" = \'STU20240002\'']
    qualified = qualify_edu_row_predicates(sql, preds, "pg")
    assert qualified == ['sd."student_id" = \'STU20240002\'']


def test_qualify_drops_school_class_on_dimension_only_sql():
    """只查 tb_exam 等维表时，不得注入裸 school_id/class（否则 column does not exist）。"""
    from datasource.service.query_permission import (
        merge_row_predicates_into_sql,
        qualify_edu_row_predicates,
    )

    sql = "SELECT * FROM tb_exam LIMIT 3"
    preds = ['"school_id" = \'YZZX\'', '"class" IN (\'高三(10)班\')']
    qualified = qualify_edu_row_predicates(sql, preds, "pg")
    assert qualified == []
    merged = merge_row_predicates_into_sql(sql, "pg", qualified)
    assert merged == sql
    assert "class" not in merged
    assert "school_id" not in merged


def test_qualify_keeps_school_class_on_tb_score_sample():
    from datasource.service.query_permission import qualify_edu_row_predicates

    sql = 'SELECT * FROM "tb_score" LIMIT 3'
    preds = ['"school_id" = \'YZZX\'', '"class" IN (\'高三(10)班\')']
    qualified = qualify_edu_row_predicates(sql, preds, "pg")
    assert qualified == [
        'tb_score."school_id" = \'YZZX\'',
        'tb_score."class" IN (\'高三(10)班\')',
    ]
