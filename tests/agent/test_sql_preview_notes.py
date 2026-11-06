from src.agent.education.sql_lint import lint_edu_sql_blocks
from src.agent.education.summary_context import (
    SQL_RESULT_FULL_TABLE_ROWS,
    SQL_RESULT_PREVIEW_ROWS,
    SQL_RESULT_ROSTER_FULL_ROWS,
    append_keepable_result_tables,
    format_omitted_detail_note,
    format_result_markdown_table,
    format_sql_result_authority_notes,
    looks_like_keepable_result_table,
    sql_result_preview_limit,
)


def test_preview_limit_shows_full_table_when_small():
    assert sql_result_preview_limit(0) == 0
    assert sql_result_preview_limit(20) == 20
    assert sql_result_preview_limit(51) == 51
    assert sql_result_preview_limit(SQL_RESULT_FULL_TABLE_ROWS) == SQL_RESULT_FULL_TABLE_ROWS
    assert sql_result_preview_limit(SQL_RESULT_FULL_TABLE_ROWS + 1) == SQL_RESULT_PREVIEW_ROWS
    assert sql_result_preview_limit(200) == SQL_RESULT_PREVIEW_ROWS


def test_truncated_notes_forbid_preview_distribution():
    note = format_sql_result_authority_notes(
        sql="SELECT * FROM tb_score_overview",
        row_count=51,
        sample_shown=20,
    )
    assert "GROUP BY" in note
    assert "外推" in note
    assert "心算" in note
    assert "前 20 行" in note
    assert "末位分" in note
    assert "选科组合" in note


def test_full_midsize_table_notes_still_forbid_hand_count():
    note = format_sql_result_authority_notes(
        sql="SELECT * FROM tb_score_overview",
        row_count=51,
        sample_shown=51,
    )
    assert "GROUP BY" in note
    assert "心算" in note
    assert "外推" not in note


def test_tiny_table_notes_skip_distribution_warning():
    note = format_sql_result_authority_notes(
        sql="SELECT * FROM tb_score_overview",
        row_count=3,
        sample_shown=3,
    )
    assert "GROUP BY" not in note
    assert "参考人数" in note


def test_keepable_rules_unchanged_for_student_roster():
    cols = ["xm", "zf6m", "hxzh"]
    assert looks_like_keepable_result_table(cols, row_count=12, sql="SELECT xm, zf6m")
    assert not looks_like_keepable_result_table(cols, row_count=13, sql="SELECT xm, zf6m")
    assert not looks_like_keepable_result_table(cols, row_count=51, sql="SELECT xm, zf6m")
    assert looks_like_keepable_result_table(
        cols, row_count=155, sql="SELECT xm, zf6m", question="需 155 人完整名单"
    )
    assert not looks_like_keepable_result_table(
        cols,
        row_count=SQL_RESULT_ROSTER_FULL_ROWS + 1,
        sql="SELECT xm, zf6m",
        question="完整名单",
    )


def test_preview_limit_full_roster_when_asked():
    assert sql_result_preview_limit(155) == SQL_RESULT_PREVIEW_ROWS
    assert sql_result_preview_limit(155, question="需 155 人完整名单") == 155
    assert sql_result_preview_limit(300, question="完整名单") == SQL_RESULT_ROSTER_FULL_ROWS


def test_keepable_agg_table_still_accepted():
    cols = ["学科", "均分", "全市排名"]
    assert looks_like_keepable_result_table(cols, row_count=9, sql="SELECT subject, avg")
    assert looks_like_keepable_result_table(cols, row_count=40, sql="SELECT subject, avg")
    assert not looks_like_keepable_result_table(cols, row_count=41, sql="SELECT subject, avg")


def test_append_replaces_truncated_wide_class_table():
    summary = (
        "全校 19 个班级分学科均分与排名（均为在籍生群体）\n\n"
        "| 指标 | 数值 |\n"
        "| --- | --- |\n"
        "| 班级数 | 19 |\n\n"
        "| 班级 | 人数 | 语文均分 | 数学均分 | 英语均分 | 物理均分 |\n"
        "| --- | --- | --- | --- | --- | --- |\n"
        "| 高三(1)班 | 48 | 110 | 98 | 105 | 61 |\n"
        "| 高三(2)班 | 47 | 109 | 97 | 104 | 60 |\n"
        "| 高三(12)班\n"
    )
    cols = ["bj", "n", "avg_yw", "avg_sx", "avg_yy", "avg_wl"]
    rows = [[f"高三({i})班", 48, 110, 98, 105, 61] for i in range(1, 20)]
    out = append_keepable_result_tables(
        summary,
        [{"columns": cols, "rows": rows, "row_count": 19, "sql": "SELECT bj GROUP BY bj"}],
        question="对比本校各班成绩",
    )
    assert "查询结果" in out
    assert "高三(19)班" in out
    assert "| 指标 | 数值 |" in out
    assert "班级数" in out
    assert not out.rstrip().endswith("| 高三(12)班")
    assert "| 高三(12)班\n" not in out + "\n"


def test_keepable_markdown_no_longer_caps_at_30():
    cols = ["学科", "均分"]
    rows = [[f"科{i}", i] for i in range(40)]
    table = format_result_markdown_table(cols, rows)
    assert table.count("\n") >= 41
    assert "科39" in table


def test_omitted_detail_note_truncated_vs_full():
    truncated = format_omitted_detail_note(
        row_count=100, sample_shown=20, fact_answer=True
    )
    assert "明细已省略" in truncated
    assert "观察窗仅前 20 行" in truncated
    assert "GROUP BY" in truncated
    assert "末位分" in truncated
    full = format_omitted_detail_note(row_count=51, sample_shown=51, fact_answer=True)
    assert "明细已省略" in full
    assert "观察窗" not in full
    assert "样本统计" in full


def _weak_subject_lint(sql: str) -> list[str]:
    return [
        b
        for b in lint_edu_sql_blocks(sql)
        if "弱科" in b or "未选考不得进该科排名池" in b
    ]


def test_lint_blocks_weak_rank_sentinel():
    sql = (
        "SELECT p.xm FROM tb_score_overview p "
        "LEFT JOIN (SELECT student_id, RANK() OVER (ORDER BY zzzh DESC) r "
        "FROM tb_score_overview WHERE xsxz='在籍生' AND zzzh > 0) pol "
        "ON pol.student_id = p.student_id "
        "WHERE p.xsxz='在籍生' AND COALESCE(pol.r, 99999) > 150"
    )
    blocked = _weak_subject_lint(sql)
    assert blocked
    assert "哨兵" in blocked[0]


def test_lint_blocks_elective_rank_including_zero():
    sql = (
        "SELECT xm, RANK() OVER (ORDER BY zzzh DESC) AS rk_zz "
        "FROM tb_score_overview WHERE xsxz='在籍生'"
    )
    blocked = _weak_subject_lint(sql)
    assert blocked
    assert "zzzh" in blocked[0]


def test_lint_allows_taken_subject_rank_without_sentinel():
    sql = (
        "WITH chem AS ("
        " SELECT student_id, RANK() OVER (ORDER BY hxzh DESC) AS r "
        " FROM tb_score_overview "
        " WHERE xsxz='在籍生' AND hxzh > 0"
        ") "
        "SELECT p.xm FROM tb_score_overview p "
        "JOIN chem c ON c.student_id = p.student_id "
        "WHERE p.xsxz='在籍生' AND p.hxzh > 0 AND c.r > 150"
    )
    assert _weak_subject_lint(sql) == []


def test_lint_allows_display_score_coalesce():
    sql = (
        "SELECT COALESCE(ov.hxzh, sc.score) AS score "
        "FROM tb_score sc JOIN tb_score_overview ov ON ov.student_id = sc.student_id "
        "WHERE ov.xsxz='在籍生'"
    )
    assert _weak_subject_lint(sql) == []
