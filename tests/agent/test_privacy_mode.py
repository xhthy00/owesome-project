"""教育匿名脱敏展示开关。"""

from __future__ import annotations

from src.agent.education.privacy_mode import (
    apply_result_privacy,
    apply_sql_privacy,
    clear_anonymize_display_cache,
    filter_display_fields,
    overlay_schema_fields,
    overlay_table_comments,
    privacy_sql_instruction,
    set_anonymize_display_cached,
)


def setup_function() -> None:
    set_anonymize_display_cached(True)


def teardown_function() -> None:
    clear_anonymize_display_cache()


def test_apply_sql_privacy_rewrites_when_anonymize_on():
    sql, fixes = apply_sql_privacy("SELECT sch.s_name, t.xm FROM tb_school sch, t")
    assert "s_name" not in sql.lower().split("from")[0] or "sch.name" in sql
    assert "t.student_id" in sql
    assert "xm→student_id" in fixes
    assert "school_s_name→name" in fixes


def test_apply_sql_privacy_passthrough_when_anonymize_off():
    set_anonymize_display_cached(False)
    sql = "SELECT sch.s_name, t.xm, t.xh FROM tb_school sch, t"
    out, fixes = apply_sql_privacy(sql)
    assert out == sql
    assert fixes == []


def test_apply_result_privacy_strips_when_anonymize_on():
    result = {
        "columns": ["xm", "xh", "s_name", "sfzh", "score"],
        "rows": [
            {"xm": "张三", "xh": "2024001", "s_name": "扬州中学", "sfzh": "1", "score": 90}
        ],
    }
    out = apply_result_privacy(result)
    assert out is not None
    keys = {str(c).lower() for c in out["columns"]}
    assert "xm" not in keys
    assert "xh" not in keys
    assert "s_name" not in keys
    assert "sfzh" not in keys
    assert "score" in keys


def test_apply_result_privacy_keeps_name_when_anonymize_off():
    set_anonymize_display_cached(False)
    result = {
        "columns": ["xm", "xh", "s_name", "sfzh", "score"],
        "rows": [
            {"xm": "张三", "xh": "2024001", "s_name": "扬州中学", "sfzh": "1", "score": 90}
        ],
    }
    out = apply_result_privacy(result)
    assert out is not None
    keys = {str(c).lower() for c in out["columns"]}
    assert "xm" in keys
    assert "xh" in keys
    assert "s_name" in keys
    assert "sfzh" not in keys
    assert out["rows"][0]["xm"] == "张三"


def test_filter_display_fields_hides_pii_when_on():
    fields = [
        {"name": "id"},
        {"name": "xm"},
        {"name": "xh"},
        {"name": "s_name"},
        {"name": "sfzh"},
        {"name": "name"},
    ]
    names = {f["name"] for f in filter_display_fields(fields, "tb_school")}
    assert names == {"id", "name"}


def test_filter_display_fields_reveals_when_off():
    set_anonymize_display_cached(False)
    fields = [
        {"name": "id"},
        {"name": "xm"},
        {"name": "xh"},
        {"name": "s_name"},
        {"name": "sfzh"},
        {"name": "name"},
    ]
    names = {f["name"] for f in filter_display_fields(fields, "tb_school")}
    assert names == {"id", "xm", "xh", "s_name", "name"}


def test_overlay_schema_when_off():
    set_anonymize_display_cached(False)
    fields = overlay_schema_fields({"school_name": "sch.name", "student_id": "sc.student_id"})
    assert fields["school_name"] == "COALESCE(sch.s_name, sch.name)"
    comments = overlay_table_comments({"tb_school": "学校维度表"})
    assert "关闭匿名脱敏" in comments["tb_school"]


def test_privacy_sql_instruction_switches():
    assert "禁止" in privacy_sql_instruction()
    assert "s_name" in privacy_sql_instruction()
    set_anonymize_display_cached(False)
    text = privacy_sql_instruction()
    assert "已关闭匿名脱敏" in text
    assert "sfzh" in text
