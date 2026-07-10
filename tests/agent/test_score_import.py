"""成绩 Excel 导入单元测试。"""

from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import Workbook

from common.router import register_routers
from datasource.service.edu_permission import EduScope
from src.agent.education.score_import import (
    ImportErrorRow,
    ParsedRow,
    ResolvedRow,
    _build_detail_upsert_sql,
    _build_score_upsert_sql,
    _check_row_scope,
    _resolve_exam,
    assert_import_role_allowed,
    import_result_to_dict,
    parse_excel,
    preview_import,
    template_path,
    validate_and_resolve,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _mock_dimensions(
    exams_by_name=None,
    exams_by_id=None,
    students_by_id=None,
    schools_by_id=None,
    questions_by_key=None,
    questions_by_id=None,
    student_has_school_id=False,
):
    return (
        exams_by_name or {},
        exams_by_id or {},
        students_by_id or {},
        schools_by_id or {},
        questions_by_key or {},
        questions_by_id or {},
        student_has_school_id,
    )


def _make_total_xlsx(rows: list[tuple], *, legacy: bool = False) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "成绩录入"
    if legacy:
        ws.append(["试卷名称*", "学号*", "班级*", "总分*"])
    else:
        ws.append(["学校编号*", "试卷编号*", "试卷名称*", "学号*", "班级*", "总分*"])
    for row in rows:
        ws.append(list(row))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _make_detail_xlsx(rows: list[tuple], *, legacy: bool = False) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "小题分明细"
    if legacy:
        ws.append(["试卷名称*", "学号*", "题号*", "得分*", "班级*"])
    else:
        ws.append(
            ["学校编号*", "试卷编号*", "试卷名称*", "学号*", "题目编号*", "题号*", "题目满分*", "得分*", "班级*"]
        )
    for row in rows:
        ws.append(list(row))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_parse_excel_total_new_template():
    data = _make_total_xlsx([
        ("NJYZ", 2, "英语模拟卷", "STU001", "高三(10)班", 127.7),
    ])
    rows = parse_excel(data, "total")
    assert rows[0].school_id == "NJYZ"
    assert rows[0].exam_id == "2"
    assert rows[0].score == 127.7


def test_parse_excel_total_skips_empty_rows():
    data = _make_total_xlsx([
        ("NJYZ", 2, "试卷A", "STU001", "高一(1)班", 80),
        (None, None, None, None, None, None),
        ("NJYZ", 2, "试卷A", "STU002", "高一(1)班", 90),
    ])
    rows = parse_excel(data, "total")
    assert len(rows) == 2
    assert rows[0].student_id == "STU001"


def test_parse_excel_detail_new_template():
    data = _make_detail_xlsx([
        ("NJYZ", 2, "英语模拟卷", "STU001", 201, 1, 1.5, 1.5, "高三(10)班"),
    ])
    rows = parse_excel(data, "detail")
    assert rows[0].question_id == "201"
    assert rows[0].question_no == 1
    assert rows[0].question_score == 1.5


def test_parse_excel_legacy_total():
    data = _make_total_xlsx([("试卷A", "STU001", "高一(1)班", 80)], legacy=True)
    rows = parse_excel(data, "total")
    assert rows[0].exam_name == "试卷A"
    assert not rows[0].exam_id


def test_parse_real_template_files():
    total_path = _PROJECT_ROOT / "脱敏成绩_仅总分.xlsx"
    detail_path = _PROJECT_ROOT / "脱敏成绩_小题分明细.xlsx"
    if total_path.is_file():
        rows = parse_excel(total_path.read_bytes(), "total")
        assert len(rows) >= 2
        assert rows[0].school_id == "NJYZ"
        assert rows[0].exam_id == "2"
    if detail_path.is_file():
        rows = parse_excel(detail_path.read_bytes(), "detail")
        assert len(rows) >= 3
        assert rows[0].question_id


def test_resolve_exam_by_id():
    exam, err = _resolve_exam(
        ParsedRow(2, "英语卷", "S1", "C1", exam_id="2"),
        {"2": {"id": "2", "exam_name": "英语卷", "subject_name": "英语", "exam_score": 150, "exam_time": None}},
        {},
    )
    assert err is None
    assert exam is not None


def test_resolve_exam_id_name_mismatch():
    _, err = _resolve_exam(
        ParsedRow(2, "错误名称", "S1", "C1", exam_id="2"),
        {"2": {"id": "2", "exam_name": "英语卷", "subject_name": "英语", "exam_score": 150, "exam_time": None}},
        {},
    )
    assert err is not None


def test_normalize_exam_row_subject_alias():
    from src.agent.education.score_import import _normalize_exam_row

    assert _normalize_exam_row({"id": "2", "subject": "英语"})["subject_name"] == "英语"
    assert _normalize_exam_row({"id": "2", "subject_name": "数学"})["subject_name"] == "数学"


def test_validate_fills_subject_from_exam(monkeypatch):
    monkeypatch.setattr(
        "src.agent.education.score_import._load_dimensions",
        lambda *a, **kw: _mock_dimensions(
            exams_by_id={"2": {"id": "2", "exam_name": "英语卷", "subject_name": "英语", "exam_score": 150, "exam_time": None}},
            exams_by_name={"英语卷": [{"id": "2", "exam_name": "英语卷", "subject_name": "英语", "exam_score": 150, "exam_time": None}]},
            students_by_id={"STU001": {"id": "STU001"}},
            schools_by_id={"NJYZ": {"id": "NJYZ"}},
        ),
    )
    rows = [
        ParsedRow(
            2, "英语卷", "STU001", "高三(10)班", score=120.0,
            school_id="NJYZ", exam_id="2",
        )
    ]
    scope = EduScope(edu_role="bureau_admin")
    resolved, errors = validate_and_resolve(rows, "total", scope, "pg", {})
    assert not errors
    assert resolved[0].subject_name == "英语"


def test_assert_import_role_allowed_blocks_student():
    err = assert_import_role_allowed(EduScope(edu_role="student"))
    assert err is not None


def test_check_row_scope_teacher_class():
    scope = EduScope(edu_role="teacher", school_id="1", class_names=["高一(1)班"])
    assert _check_row_scope(scope, "高一(2)班", "1") is not None
    assert _check_row_scope(scope, "高一(1)班", "1") is None


def test_validate_student_not_found_marks_create(monkeypatch):
    monkeypatch.setattr(
        "src.agent.education.score_import._load_dimensions",
        lambda *a, **kw: _mock_dimensions(
            exams_by_name={"试卷A": [{"id": "E1", "exam_name": "试卷A", "subject_name": "数学", "exam_score": 100, "exam_time": None}]},
            exams_by_id={"E1": {"id": "E1", "exam_name": "试卷A", "subject_name": "数学", "exam_score": 100, "exam_time": None}},
            schools_by_id={"1": {"id": "1"}},
        ),
    )
    rows = [ParsedRow(2, "试卷A", "STU999", "高一(1)班", score=80.0, school_id="1", exam_id="E1")]
    scope = EduScope(edu_role="school_admin", school_id="1")
    resolved, errors = validate_and_resolve(rows, "total", scope, "pg", {})
    assert not errors
    assert len(resolved) == 1
    assert resolved[0].create_student is True
    assert resolved[0].student_id == "STU999"


def test_validate_new_template_school_from_excel(monkeypatch):
    monkeypatch.setattr(
        "src.agent.education.score_import._load_dimensions",
        lambda *a, **kw: _mock_dimensions(
            exams_by_id={"2": {"id": "2", "exam_name": "英语卷", "subject_name": "英语", "exam_score": 150, "exam_time": None}},
            exams_by_name={"英语卷": [{"id": "2", "exam_name": "英语卷", "subject_name": "英语", "exam_score": 150, "exam_time": None}]},
            students_by_id={"STU001": {"id": "STU001"}},
            schools_by_id={"NJYZ": {"id": "NJYZ"}},
        ),
    )
    rows = [
        ParsedRow(
            2, "英语卷", "STU001", "高三(10)班", score=120.0,
            school_id="NJYZ", exam_id="2",
        )
    ]
    scope = EduScope(edu_role="bureau_admin")
    resolved, errors = validate_and_resolve(rows, "total", scope, "pg", {})
    assert len(resolved) == 1
    assert resolved[0].school_id == "NJYZ"
    assert not errors


def test_validate_score_exceeds_exam_score(monkeypatch):
    monkeypatch.setattr(
        "src.agent.education.score_import._load_dimensions",
        lambda *a, **kw: _mock_dimensions(
            exams_by_name={"试卷A": [{"id": "E1", "exam_name": "试卷A", "subject_name": "数学", "exam_score": 100, "exam_time": None}]},
            students_by_id={"STU001": {"id": "STU001"}},
        ),
    )
    rows = [ParsedRow(2, "试卷A", "STU001", "高一(1)班", score=150.0)]
    scope = EduScope(edu_role="school_admin", school_id="1")
    resolved, errors = validate_and_resolve(rows, "total", scope, "pg", {})
    assert not resolved
    assert any("满分" in e.message for e in errors)


def test_build_upsert_sql_pg():
    sql = _build_score_upsert_sql("pg", "tb_score")
    assert "ON CONFLICT (exam_id, student_id)" in sql
    assert "tb_score" in sql
    detail_sql = _build_detail_upsert_sql("pg", "tb_score_detail")
    assert "ON CONFLICT (exam_id, student_id, question_no)" in detail_sql
    assert "tb_score_detail" in detail_sql


def test_resolve_write_target_only_score_tables():
    from src.agent.education.score_import import _resolve_write_target

    role, cols, conflict = _resolve_write_target("total")
    assert role == "score"
    assert "exam_id" in cols and "score" in cols
    assert conflict == ("exam_id", "student_id")

    role, cols, conflict = _resolve_write_target("detail")
    assert role == "score_detail"
    assert "question_no" in cols
    assert conflict == ("exam_id", "student_id", "question_no")


def test_preview_import_with_mocks(monkeypatch):
    data = _make_total_xlsx([("NJYZ", 2, "试卷A", "STU001", "高一(1)班", 80)], legacy=False)
    monkeypatch.setattr(
        "src.agent.education.score_import._load_dimensions",
        lambda *a, **kw: _mock_dimensions(
            exams_by_id={"2": {"id": "2", "exam_name": "试卷A", "subject_name": "数学", "exam_score": 100, "exam_time": None}},
            exams_by_name={"试卷A": [{"id": "2", "exam_name": "试卷A", "subject_name": "数学", "exam_score": 100, "exam_time": None}]},
            students_by_id={"STU001": {"id": "STU001"}},
            schools_by_id={"NJYZ": {"id": "NJYZ"}},
        ),
    )
    scope = EduScope(edu_role="bureau_admin")
    result = preview_import(data, "total", scope, "pg", {})
    assert result.valid_rows == 1
    assert not result.error_rows


def test_import_scores_rejects_on_constraint_check(monkeypatch):
    data = _make_total_xlsx([("NJYZ", 2, "试卷A", "STU001", "高一(1)班", 80)], legacy=False)
    monkeypatch.setattr(
        "src.agent.education.score_import._load_dimensions",
        lambda *a, **kw: _mock_dimensions(
            exams_by_id={"2": {"id": "2", "exam_name": "试卷A", "subject_name": "数学", "exam_score": 100, "exam_time": None}},
            students_by_id={"STU001": {"id": "STU001"}},
            schools_by_id={"NJYZ": {"id": "NJYZ"}},
        ),
    )
    monkeypatch.setattr(
        "src.agent.education.score_import._check_unique_constraint",
        lambda *a, **kw: (False, "缺少唯一约束"),
    )
    from src.agent.education.score_import import import_scores

    scope = EduScope(edu_role="bureau_admin")
    result = import_scores(data, "total", scope, "pg", {})
    assert result.error_rows
    assert "唯一约束" in result.error_rows[0].message


def _auth_client(monkeypatch) -> TestClient:
    from src.agent.resource.tool import business as biz
    from system.api.system import get_current_user
    from system.schemas import UserResponse
    from system.workspace_scope import get_workspace_oid

    monkeypatch.setattr(
        "src.agent.education.api.assert_datasource_accessible",
        lambda *a, **kw: SimpleNamespace(id=1, oid=1),
    )
    monkeypatch.setattr(biz, "_load_datasource", lambda *a, **kw: ("pg", {}, "ds"))
    monkeypatch.setattr(
        "system.crud.crud_user.get_user_by_id",
        lambda session, uid: SimpleNamespace(
            id=uid,
            system_variables={"edu_role": "bureau_admin"},
        ),
    )

    app = FastAPI()
    register_routers(app)
    app.dependency_overrides[get_current_user] = lambda: UserResponse(
        id=1, account="admin", name="Admin", email=None, oid=1,
        status=1, language="zh-CN", origin=0, create_time=0,
    )
    app.dependency_overrides[get_workspace_oid] = lambda: 1
    return TestClient(app)


def test_score_import_preview_api(monkeypatch):
    data = _make_total_xlsx([("NJYZ", 2, "试卷A", "STU001", "高一(1)班", 80)], legacy=False)
    monkeypatch.setattr(
        "src.agent.education.score_import._load_dimensions",
        lambda *a, **kw: _mock_dimensions(
            exams_by_id={"2": {"id": "2", "exam_name": "试卷A", "subject_name": "数学", "exam_score": 100, "exam_time": None}},
            students_by_id={"STU001": {"id": "STU001"}},
            schools_by_id={"NJYZ": {"id": "NJYZ"}},
        ),
    )
    client = _auth_client(monkeypatch)
    r = client.post(
        "/api/v1/education/score-import/preview",
        data={"datasource_id": "1", "import_type": "total"},
        files={"file": ("scores.xlsx", data, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert r.status_code == 200
    assert r.json()["data"]["valid_rows"] == 1


def test_score_import_execute_api_errors(monkeypatch):
    data = _make_total_xlsx([("NJYZ", 2, "试卷A", "STU001", "高一(1)班", 80)], legacy=False)
    monkeypatch.setattr(
        "src.agent.education.score_import.import_scores",
        lambda *a, **kw: __import__(
            "src.agent.education.score_import", fromlist=["ImportResult"]
        ).ImportResult(
            total_rows=1,
            valid_rows=0,
            error_rows=[ImportErrorRow(2, "学号", "不存在")],
        ),
    )
    client = _auth_client(monkeypatch)
    r = client.post(
        "/api/v1/education/score-import/execute",
        data={"datasource_id": "1", "import_type": "total"},
        files={"file": ("scores.xlsx", data, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert r.status_code == 200
    assert r.json()["code"] == 400


def test_write_rows_parallel_uses_batch_for_small(monkeypatch):
    from src.agent.education import score_import as si

    calls: list[int] = []

    def fake_batch(db_type, config, table, write_cols, conflict_cols, rows):
        calls.append(len(rows))
        return True, "Success", len(rows), rows[0].row_num if rows else 0

    monkeypatch.setattr(si, "_write_rows_batch", fake_batch)
    rows = [
        ResolvedRow(
            i, "试卷A", f"S{i}", "高一(1)班", 1.0,
            "E1", "1", "数学", 100.0, None, question_no=i, question_id=str(i),
        )
        for i in range(1, 11)
    ]
    ok, msg, total, _ = si._write_rows_parallel("pg", {}, "tb_score_detail", ["exam_id"], ("exam_id",), rows)
    assert ok and total == 10
    assert calls == [10]  # 低于阈值，单批


def test_write_rows_parallel_chunks(monkeypatch):
    from src.agent.education import score_import as si

    monkeypatch.setattr(si, "_PARALLEL_THRESHOLD", 5)
    monkeypatch.setattr(si, "_BATCH_SIZE", 3)
    monkeypatch.setattr(si, "_PARALLEL_WORKERS", 2)
    calls: list[int] = []

    def fake_batch(db_type, config, table, write_cols, conflict_cols, rows):
        calls.append(len(rows))
        return True, "Success", len(rows), rows[0].row_num if rows else 0

    monkeypatch.setattr(si, "_write_rows_batch", fake_batch)
    rows = [
        ResolvedRow(
            i, "试卷A", f"S{i}", "高一(1)班", 1.0,
            "E1", "1", "数学", 100.0, None, question_no=i, question_id=str(i),
        )
        for i in range(1, 9)
    ]
    ok, msg, total, _ = si._write_rows_parallel("pg", {}, "tb_score_detail", ["exam_id"], ("exam_id",), rows)
    assert ok and total == 8
    assert sorted(calls) == [2, 3, 3]


def test_template_path_exists():
    assert template_path("total").name == "脱敏成绩_仅总分.xlsx"
