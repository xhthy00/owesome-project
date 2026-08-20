"""学生姓名隐私：xm/姓名列改写与结果剔除。"""

from __future__ import annotations

from src.agent.education.student_privacy import (
    filter_schema_fields,
    rewrite_sql_student_name_cols,
    strip_student_names_from_query_result,
)
from src.agent.expand.planner import build_fact_query_plan_items


def test_filter_schema_hides_xm():
    fields = [
        {"name": "student_id", "type": "text"},
        {"name": "xm", "type": "text", "comment": "姓名"},
        {"name": "score", "type": "numeric"},
        {"name": "姓名", "type": "text"},
    ]
    out = filter_schema_fields(fields)
    names = {f["name"] for f in out}
    assert names == {"student_id", "score"}


def test_rewrite_xm_to_student_id():
    sql = "SELECT t.xm, t.score FROM tb_score t WHERE t.class = '高二(6)班'"
    out, changed = rewrite_sql_student_name_cols(sql)
    assert changed is True
    assert "xm" not in out.lower()
    assert "t.student_id" in out
    # 字符串字面量中的 xm 不改
    lit = "SELECT score FROM t WHERE note = '含xm字样'"
    out2, changed2 = rewrite_sql_student_name_cols(lit)
    assert changed2 is False
    assert out2 == lit


def test_strip_xm_from_result_rows():
    result = {
        "columns": ["xm", "student_id", "score"],
        "rows": [
            {"xm": "褚子皓", "student_id": "STU001", "score": 124.0},
            {"xm": "王艺琪", "student_id": "STU002", "score": 124.0},
        ],
    }
    out = strip_student_names_from_query_result(result)
    assert out is not None
    assert "xm" not in [str(c).lower() for c in out["columns"]]
    assert all("xm" not in r for r in out["rows"])
    assert out["rows"][0]["student_id"] == "STU001"


def test_fact_query_plan_forbids_plaintext_names():
    plans = build_fact_query_plan_items("高二(6)班数学成绩最好的学生是谁")
    blob = plans[0]["sub_task"]
    assert "student_id" in blob
    assert "禁止" in blob
    assert "xm" in blob or "姓名" in blob
    # 不得引导 SELECT 姓名
    assert "student_id、姓名" not in blob
    assert "、姓名、" not in blob
    assert "参考人数" in blob
    assert "学情总判" in blob


def test_fact_query_plan_allows_plaintext_when_anonymize_off():
    from src.agent.education.privacy_mode import (
        clear_anonymize_display_cache,
        set_anonymize_display_cached,
    )

    set_anonymize_display_cached(False)
    try:
        plans = build_fact_query_plan_items("高二(6)班数学成绩最好的学生是谁")
        blob = plans[0]["sub_task"]
        assert "已关闭匿名脱敏" in blob
        assert "xm" in blob
        assert "禁止写中文姓名" not in blob
    finally:
        clear_anonymize_display_cache()


def test_scrub_fact_answer_removes_fake_class_size():
    from src.agent.education.summary_context import scrub_fact_answer_headcount_noise

    text = (
        "高二(6)班数学本次共 5 人参考（权威统计口径），满分 150，"
        "最高分 124.0 分，由 student_id=A 与 B 并列。"
    )
    out = scrub_fact_answer_headcount_noise(text)
    assert "共 5 人参考" not in out
    assert "权威统计口径" not in out
    assert "124.0" in out
    assert "student_id=A" in out


def test_reconcile_fact_answer_ignores_exec_row_count_as_class_size():
    from src.agent.education.summary_context import reconcile_answer_with_artifacts

    text = "高二(6)班数学共 5 人参考，最高分学生为 STU1（124 分）。"
    out = reconcile_answer_with_artifacts(
        text,
        exec_results=[
            {
                "sql": "SELECT student_id, score FROM tb_score WHERE class='高二(6)班' ORDER BY score DESC",
                "row_count": 5,
                "columns": ["student_id", "score"],
                "sql_row_capped": False,
            }
        ],
        fact_answer=True,
    )
    assert "共 5 人参考" not in out
    assert "STU1" in out
    assert "124" in out
