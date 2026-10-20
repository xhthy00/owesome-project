"""成绩库：列出 tb_score_overview，并按科目查看小题得分。"""

from __future__ import annotations

import re
from typing import Any

from datasource.db.db import WriteDbSession
from datasource.service.edu_permission import EduScope

# 再选科目展示赋分，其余展示原始分。键是试卷科目名。
SCORE_SUBJECTS: dict[str, str] = {
    "语文": "yw",
    "数学": "sx",
    "英语": "yy",
    "物理": "wl",
    "化学": "hxzh",
    "生物": "swzh",
    "历史": "ls",
    "政治": "zzzh",
    "地理": "dlzh",
}
_SCORE_COLUMNS = (
    "exam_batch_id",
    "exam_name",
    "xx",
    "bj",
    "xkkm",
    "sfzh",
    "xm",
    "anon_stu_id",
    "zf6m",
    *SCORE_SUBJECTS.values(),
)
_SCORE_NUMBERS = ("zf6m", *SCORE_SUBJECTS.values())
# 六门选考未选时库里常被写成 0，展示时按选考科目清空。语数英必考，不在此列。
_SUBJECT_MARKS = {
    "物理": ("物",),
    "化学": ("化",),
    "生物": ("生",),
    "历史": ("史", "历"),
    "政治": ("政",),
    "地理": ("地",),
}
_ELECTIVE_MARKS = {SCORE_SUBJECTS[name]: marks for name, marks in _SUBJECT_MARKS.items()}
_GRADES = ("高三", "高二", "高一", "初三", "初二", "初一")
_GRADE_RE = re.compile("|".join(_GRADES))

__all__ = [
    "SCORE_SUBJECTS",
    "build_filter_options",
    "elective_score_for_display",
    "grade_of_class",
    "list_filter_options",
    "list_score_detail",
    "list_scores",
    "resolve_score_access",
    "score_list_filters",
]


def elective_score_for_display(column: str, score: float | None, xkkm: str) -> float | None:
    """物理、历史、化学、生物、政治、地理未出现在选考科目里时不展示分数。选了该科则保留 0 分。"""
    marks = _ELECTIVE_MARKS.get(column)
    if not marks or score is None:
        return score
    text = (xkkm or "").strip()
    if not text:
        return score
    if any(mark in text for mark in marks):
        return score
    return None


def resolve_score_access(scope: EduScope, *, privileged: bool) -> str:
    """返回 ``all`` 或 ``school``。教师、学生和无教育角色的普通账号不能看成绩库。"""
    if privileged or scope.edu_role == "bureau_admin":
        return "all"
    if scope.edu_role == "school_admin":
        if not (scope.school_id or "").strip():
            raise ValueError("校管理员未配置 school_id")
        return "school"
    if not scope.edu_role:
        raise ValueError("未配置教育角色，无法查看成绩库")
    raise ValueError("仅局端管理员和校管理员可查看成绩库")


def grade_of_class(class_name: str) -> str:
    matched = _GRADE_RE.search(class_name or "")
    return matched.group(0) if matched else ""


def score_list_filters(
    *,
    access: str,
    school_name: str = "",
    exam_batch_id: int | None = None,
    school: str = "",
    grade: str = "",
    class_name: str = "",
    track: str = "",
    subject: str = "",
    student: str = "",
) -> tuple[str, list[Any]]:
    where = "1=1"
    params: list[Any] = []
    if exam_batch_id is not None:
        where += " AND exam_batch_id = %s"
        params.append(int(exam_batch_id))
    if access == "school":
        where += " AND xx = %s"
        params.append(school_name)
    picked_school = school.strip()[:80]
    if picked_school:
        where += " AND xx = %s"
        params.append(picked_school)
    picked_grade = grade.strip()
    if picked_grade in _GRADES:
        where += " AND bj LIKE %s"
        params.append(f"{picked_grade}%")
    picked_class = class_name.strip()[:40]
    if picked_class:
        where += " AND bj = %s"
        params.append(picked_class)
    picked_track = track.strip()[:40]
    if picked_track:
        where += " AND xkkm = %s"
        params.append(picked_track)
    marks = _SUBJECT_MARKS.get(subject.strip())
    if marks:
        where += " AND (" + " OR ".join("xkkm LIKE %s" for _ in marks) + ")"
        params.extend(f"%{mark}%" for mark in marks)
    text = student.strip()[:40]
    if text:
        where += " AND (xm ILIKE %s OR sfzh ILIKE %s)"
        like = f"%{text}%"
        params.extend((like, like))
    return where, params


def score_list_order() -> str:
    """同一考试内按学校、年级、班号、学号排列，避免学号把不同班级穿插在一起。"""
    grade_case = " ".join(
        f"WHEN left(bj, 2) = '{grade}' THEN {index}" for index, grade in enumerate(_GRADES, start=1)
    )
    return (
        "exam_batch_id DESC NULLS LAST, xx, "
        f"CASE {grade_case} ELSE 99 END, "
        "NULLIF(substring(bj from '([0-9]+)'), '')::int NULLS LAST, "
        "bj, sfzh NULLS LAST, xm"
    )


def build_filter_options(rows: list[tuple]) -> dict[str, Any]:
    schools: list[str] = []
    classes: list[dict[str, str]] = []
    tracks: list[str] = []
    seen_school: set[str] = set()
    seen_class: set[tuple[str, str]] = set()
    seen_track: set[str] = set()
    grades: set[str] = set()
    for raw in rows:
        school = _text(raw[0] if len(raw) > 0 else "")
        class_name = _text(raw[1] if len(raw) > 1 else "")
        track = _text(raw[2] if len(raw) > 2 else "")
        if school and school not in seen_school:
            seen_school.add(school)
            schools.append(school)
        grade = grade_of_class(class_name)
        key = (school, class_name)
        if class_name and key not in seen_class:
            seen_class.add(key)
            classes.append({"xx": school, "bj": class_name, "grade": grade})
            if grade:
                grades.add(grade)
        if track and track not in seen_track:
            seen_track.add(track)
            tracks.append(track)
    return {
        "schools": sorted(schools),
        "grades": [item for item in _GRADES if item in grades],
        "classes": sorted(classes, key=lambda item: (item["xx"], item["grade"], item["bj"])),
        "tracks": sorted(tracks),
    }


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _row_dict(row: tuple) -> dict[str, Any]:
    raw = dict(zip(_SCORE_COLUMNS, row, strict=True))
    item: dict[str, Any] = {
        "exam_batch_id": None if raw["exam_batch_id"] is None else int(raw["exam_batch_id"]),
        "exam_name": _text(raw["exam_name"]),
        "xx": _text(raw["xx"]),
        "bj": _text(raw["bj"]),
        "xkkm": _text(raw["xkkm"]),
        "sfzh": _text(raw["sfzh"]),
        "xm": _text(raw["xm"]),
        "anon_stu_id": _text(raw["anon_stu_id"]),
    }
    xkkm = item["xkkm"]
    for key in _SCORE_NUMBERS:
        item[key] = elective_score_for_display(key, _num(raw[key]), xkkm)
    return item


def _one(session: Any, sql: str, params: tuple) -> tuple | None:
    ok, msg, data = session.execute_query(sql, params)
    if not ok:
        raise ValueError(msg or "查询失败")
    rows = (data or {}).get("rows") or []
    if not rows:
        return None
    row = rows[0]
    return tuple(row.values()) if isinstance(row, dict) else tuple(row)


def _all(session: Any, sql: str, params: tuple) -> list[tuple]:
    ok, msg, data = session.execute_query(sql, params)
    if not ok:
        raise ValueError(msg or "查询失败")
    rows = []
    for row in (data or {}).get("rows") or []:
        rows.append(tuple(row.values()) if isinstance(row, dict) else tuple(row))
    return rows


def list_filter_options(
    db_type: str,
    config: dict[str, Any],
    *,
    access: str,
    school_id: str = "",
    exam_batch_id: int | None = None,
) -> dict[str, Any]:
    with WriteDbSession(db_type, config) as session:
        school_name = ""
        if access == "school":
            school_name = _school_name(session, school_id)
            if not school_name:
                raise ValueError("未找到本校学校名称，无法列出筛选项")
        where, params = score_list_filters(
            access=access,
            school_name=school_name,
            exam_batch_id=exam_batch_id,
        )
        rows = _all(
            session,
            "SELECT DISTINCT xx, bj, xkkm FROM tb_score_overview "
            f"WHERE {where}",
            tuple(params),
        )
    return build_filter_options(rows)


def _school_name(session: Any, school_id: str) -> str:
    row = _one(
        session,
        "SELECT s_name FROM tb_school WHERE CAST(id AS TEXT) = %s LIMIT 1",
        (school_id.strip(),),
    )
    if not row:
        return ""
    return _text(row[0])


def list_scores(
    db_type: str,
    config: dict[str, Any],
    *,
    access: str,
    school_id: str = "",
    exam_batch_id: int | None = None,
    school: str = "",
    grade: str = "",
    class_name: str = "",
    track: str = "",
    subject: str = "",
    student: str = "",
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    page = max(1, int(page))
    page_size = min(100, max(1, int(page_size)))
    with WriteDbSession(db_type, config) as session:
        school_name = ""
        if access == "school":
            school_name = _school_name(session, school_id)
            if not school_name:
                raise ValueError("未找到本校学校名称，无法列出成绩")
        where, params = score_list_filters(
            access=access,
            school_name=school_name,
            exam_batch_id=exam_batch_id,
            school=school,
            grade=grade,
            class_name=class_name,
            track=track,
            subject=subject,
            student=student,
        )
        total_row = _one(session, f"SELECT COUNT(*) FROM tb_score_overview WHERE {where}", tuple(params))
        total = int(total_row[0]) if total_row else 0
        cols = ", ".join(_SCORE_COLUMNS)
        rows = _all(
            session,
            f"SELECT {cols} FROM tb_score_overview WHERE {where} "
            f"ORDER BY {score_list_order()} "
            "LIMIT %s OFFSET %s",
            tuple([*params, page_size, (page - 1) * page_size]),
        )
    return {
        "items": [_row_dict(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def list_score_detail(
    db_type: str,
    config: dict[str, Any],
    *,
    access: str,
    school_id: str = "",
    exam_batch_id: int,
    anon_stu_id: str,
    subject: str,
) -> dict[str, Any]:
    subject_name = subject.strip()
    if subject_name not in SCORE_SUBJECTS:
        raise ValueError("未知科目")
    student_id = anon_stu_id.strip()
    if not student_id:
        raise ValueError("缺少学生标识")
    with WriteDbSession(db_type, config) as session:
        school_name = ""
        if access == "school":
            school_name = _school_name(session, school_id)
            if not school_name:
                raise ValueError("未找到本校学校名称，无法查看小题分")
        where, params = score_list_filters(access=access, school_name=school_name)
        overview = _one(
            session,
            "SELECT exam_name, ksh, xm FROM tb_score_overview "
            f"WHERE exam_batch_id = %s AND anon_stu_id = %s AND {where} LIMIT 1",
            (int(exam_batch_id), student_id, *params),
        )
        if overview is None:
            raise ValueError("未找到该生成绩")
        exam = _one(
            session,
            "SELECT id, exam_name FROM tb_exam "
            "WHERE exam_batch_id = %s AND subject = %s ORDER BY id LIMIT 1",
            (int(exam_batch_id), subject_name),
        )
        items: list[dict[str, Any]] = []
        exam_id = None
        paper_name = ""
        if exam is not None:
            exam_id = int(exam[0])
            paper_name = _text(exam[1])
            detail_rows = _all(
                session,
                "SELECT COALESCE(eq.question_no, sd.question_no), "
                "sd.score, COALESCE(eq.question_score, sd.question_score), eq.content "
                "FROM tb_score_detail sd "
                "LEFT JOIN tb_exam_question eq ON eq.id = COALESCE("
                "sd.question_id, ("
                "SELECT q.id FROM tb_exam_question q "
                "WHERE q.exam_id = sd.exam_id AND q.question_no = sd.question_no "
                "ORDER BY q.id LIMIT 1)) "
                "WHERE sd.exam_id = %s AND sd.student_id = %s "
                "ORDER BY eq.id NULLS LAST, sd.question_no",
                (exam_id, student_id),
            )
            items = [
                {
                    "question_no": _text(row[0]),
                    "score": _num(row[1]),
                    "question_score": _num(row[2]),
                    "content": _text(row[3]),
                }
                for row in detail_rows
            ]
    return {
        "subject": subject_name,
        "exam_id": exam_id,
        "exam_name": _text(overview[0]),
        "paper_name": paper_name,
        "ksh": _text(overview[1]),
        "xm": _text(overview[2]),
        "items": items,
    }
