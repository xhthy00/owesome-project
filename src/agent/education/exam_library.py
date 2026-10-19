"""试卷库：列表、可见范围、试卷与题目维护。

可见范围与离线端一致：全市统考、本校学校自建、本人教师自建。
平台管理员和市教研员可以看到全部试卷。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from datasource.db.db import WriteDbSession

EXAM_TYPES = ("city", "school", "teacher")
EXAM_TYPE_LABELS = {"city": "全市统考", "school": "学校自建", "teacher": "教师自建"}
_TYPE_ALIASES = {
    "city": "city",
    "school": "school",
    "teacher": "teacher",
    "全市统考": "city",
    "学校自建": "school",
    "教师自建": "teacher",
}

__all__ = [
    "EXAM_TYPE_LABELS",
    "ExamActor",
    "build_actor",
    "create_exam",
    "delete_exam",
    "exam_visible",
    "get_exam",
    "list_exams",
    "normalize_ownership",
    "normalize_question_rows",
    "save_questions",
    "set_question_links",
]


@dataclass(frozen=True)
class ExamActor:
    see_all: bool
    school_id: str
    owner_id: str
    edu_role: str


def build_actor(user: Any, scope: Any) -> ExamActor:
    from system.authz import is_platform_admin

    role = str(getattr(scope, "edu_role", "") or "").strip()
    return ExamActor(
        see_all=bool(is_platform_admin(user) or role == "bureau_admin"),
        school_id=str(getattr(scope, "school_id", "") or "").strip(),
        owner_id=str(getattr(user, "account", "") or "").strip(),
        edu_role=role,
    )


def exam_kind(exam: dict[str, Any]) -> str:
    kind = str(exam.get("exam_type") or "").strip()
    return kind if kind in EXAM_TYPES else "city"


def exam_visible(exam: dict[str, Any], actor: ExamActor) -> bool:
    if actor.see_all:
        return True
    kind = exam_kind(exam)
    school_id = str(exam.get("school_id") or "")
    owner_id = str(exam.get("owner_id") or "")
    if kind == "city":
        return True
    if kind == "school":
        return bool(actor.school_id) and school_id == actor.school_id
    return (
        bool(actor.school_id)
        and bool(actor.owner_id)
        and school_id == actor.school_id
        and owner_id == actor.owner_id
    )


def normalize_exam_type(value: str | None) -> str:
    kind = _TYPE_ALIASES.get(str(value or "").strip())
    if not kind:
        raise ValueError("试卷类型无效")
    return kind


def normalize_ownership(
    exam_type: str | None,
    school_id: str | None,
    owner_id: str | None,
    actor: ExamActor,
) -> tuple[str, str | None, str | None]:
    kind = normalize_exam_type(exam_type)
    if kind == "city":
        return "city", None, None
    school = str(school_id or actor.school_id or "").strip() or None
    if not school:
        raise ValueError("学校自建和教师自建需要填写学校")
    if kind == "school":
        return "school", school, None
    owner = str(owner_id or actor.owner_id or "").strip() or None
    if not owner:
        raise ValueError("教师自建需要所属教师")
    return "teacher", school, owner


def normalize_links(links: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []
    for item in links or []:
        try:
            knowledge_id = int(item.get("knowledge_id"))
        except (TypeError, ValueError):
            raise ValueError("知识点无效") from None
        if knowledge_id <= 0 or any(row["knowledge_id"] == knowledge_id for row in seen):
            continue
        weight = item.get("weight")
        parsed = None
        if weight is not None and weight != "":
            try:
                parsed = float(weight)
            except (TypeError, ValueError):
                raise ValueError("知识点权重无效") from None
        seen.append({"knowledge_id": knowledge_id, "weight": parsed})
    if not seen:
        return []
    if any(row["weight"] is None or row["weight"] <= 0 for row in seen):
        avg = round(1 / len(seen), 4)
        return [{"knowledge_id": row["knowledge_id"], "weight": avg} for row in seen]
    total = sum(float(row["weight"]) for row in seen)
    return [
        {"knowledge_id": row["knowledge_id"], "weight": round(float(row["weight"]) / total, 4)}
        for row in seen
    ]


def normalize_question_rows(rows: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen_no: set[str] = set()
    for raw in rows or []:
        question_no = str(raw.get("question_no") or "").strip()
        if not question_no:
            raise ValueError("题号不能为空")
        if len(question_no) > 16:
            raise ValueError(f"题号过长：{question_no}")
        if question_no in seen_no:
            raise ValueError(f"题号重复：{question_no}")
        seen_no.add(question_no)
        try:
            score = float(raw.get("question_score") if raw.get("question_score") is not None else 0)
        except (TypeError, ValueError):
            raise ValueError(f"第 {question_no} 题分值无效") from None
        if score < 0:
            raise ValueError(f"第 {question_no} 题分值不能为负")
        question_id = raw.get("id")
        try:
            parsed_id = int(question_id) if question_id else None
        except (TypeError, ValueError):
            raise ValueError(f"第 {question_no} 题编号无效") from None
        question_type = str(raw.get("question_type") or "").strip()[:32] or None
        content = raw.get("content")
        out.append(
            {
                "id": parsed_id,
                "question_no": question_no,
                "question_type": question_type,
                "question_score": score,
                "content": None if content is None else str(content),
                "knowledge_links": normalize_links(raw.get("knowledge_links") or []),
            }
        )
    return out


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


def _must_write(session: Any, sql: str, params: tuple) -> int:
    ok, msg, data = session.execute_write(sql, params)
    if not ok:
        text = msg or "写入失败"
        if "uk_exam_batch_subject" in text:
            raise ValueError("同一考试批次下该科目试卷已存在")
        raise ValueError(text)
    return int((data or {}).get("row_count") or 0)


def _visibility_sql(actor: ExamActor, alias: str = "") -> tuple[str, tuple]:
    prefix = f"{alias}." if alias else ""
    sql = (
        f"(%s OR COALESCE(NULLIF({prefix}exam_type, ''), 'city') = 'city' "
        f"OR ({prefix}exam_type = 'school' AND %s <> '' AND {prefix}school_id = %s) "
        f"OR ({prefix}exam_type = 'teacher' AND %s <> '' AND {prefix}school_id = %s "
        f"AND {prefix}owner_id = %s))"
    )
    params = (
        actor.see_all,
        actor.school_id,
        actor.school_id,
        actor.school_id,
        actor.school_id,
        actor.owner_id,
    )
    return sql, params


def _parse_time(value: str | None) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date().isoformat()
    except ValueError:
        raise ValueError("考试时间格式应为 YYYY-MM-DD") from None


def _exam_payload(data: dict[str, Any], actor: ExamActor) -> dict[str, Any]:
    name = str(data.get("exam_name") or "").strip()
    if not name:
        raise ValueError("试卷名称不能为空")
    if len(name) > 200:
        raise ValueError("试卷名称过长")
    subject = str(data.get("subject") or "").strip()
    if not subject:
        raise ValueError("请选择科目")
    try:
        score = float(data.get("exam_score"))
    except (TypeError, ValueError):
        raise ValueError("卷面满分无效") from None
    if score <= 0:
        raise ValueError("卷面满分必须大于 0")
    kind, school_id, owner_id = normalize_ownership(
        data.get("exam_type"),
        data.get("school_id"),
        data.get("owner_id"),
        actor,
    )
    return {
        "exam_name": name,
        "exam_time": _parse_time(data.get("exam_time")),
        "subject": subject[:32],
        "exam_score": score,
        "exam_type": kind,
        "school_id": school_id,
        "owner_id": owner_id,
    }


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _date_text(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)[:10]


def _exam_dict(row: tuple) -> dict[str, Any]:
    kind = row[5] if row[5] in EXAM_TYPES else "city"
    return {
        "id": int(row[0]),
        "exam_name": row[1] or "",
        "exam_time": _date_text(row[2]),
        "subject": row[3] or "",
        "exam_score": None if row[4] is None else float(row[4]),
        "exam_type": kind,
        "exam_type_label": EXAM_TYPE_LABELS[kind],
        "school_id": _text(row[6]),
        "owner_id": _text(row[7]),
        "exam_batch_id": None if row[8] is None else int(row[8]),
        "question_count": int(row[9] or 0),
    }


_EXAM_COLUMNS = (
    "e.id, e.exam_name, e.exam_time, e.subject, e.exam_score, e.exam_type, "
    "e.school_id, e.owner_id, e.exam_batch_id, "
    "(SELECT COUNT(*) FROM tb_exam_question q WHERE q.exam_id = e.id)"
)


def list_exams(
    db_type: str,
    config: dict[str, Any],
    actor: ExamActor,
    *,
    keyword: str = "",
    exam_type: str = "",
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    page = max(1, int(page))
    page_size = min(100, max(1, int(page_size)))
    where, params = _visibility_sql(actor, "e")
    values: list[Any] = list(params)
    text = keyword.strip()
    if text:
        where += " AND e.exam_name ILIKE %s"
        values.append(f"%{text}%")
    if exam_type.strip():
        where += " AND e.exam_type = %s"
        values.append(normalize_exam_type(exam_type))
    with WriteDbSession(db_type, config) as session:
        total_row = _one(session, f"SELECT COUNT(*) FROM tb_exam e WHERE {where}", tuple(values))
        total = int(total_row[0]) if total_row else 0
        rows = _all(
            session,
            f"SELECT {_EXAM_COLUMNS} FROM tb_exam e WHERE {where} "
            "ORDER BY e.exam_time DESC NULLS LAST, e.id DESC LIMIT %s OFFSET %s",
            tuple([*values, page_size, (page - 1) * page_size]),
        )
    return {
        "items": [_exam_dict(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def _load_exam(session: Any, exam_id: int) -> dict[str, Any] | None:
    row = _one(session, f"SELECT {_EXAM_COLUMNS} FROM tb_exam e WHERE e.id = %s", (exam_id,))
    return _exam_dict(row) if row else None


def _score_counts(session: Any, exam_id: int) -> tuple[int, int]:
    score = _one(session, "SELECT COUNT(*) FROM tb_score WHERE exam_id = %s", (exam_id,))
    detail = _one(session, "SELECT COUNT(*) FROM tb_score_detail WHERE exam_id = %s", (exam_id,))
    return int(score[0] if score else 0), int(detail[0] if detail else 0)


def _question_rows(session: Any, exam_id: int) -> list[dict[str, Any]]:
    rows = _all(
        session,
        "SELECT id, question_no, question_type, question_score, content "
        "FROM tb_exam_question WHERE exam_id = %s ORDER BY id",
        (exam_id,),
    )
    questions = [
        {
            "id": int(row[0]),
            "question_no": "" if row[1] is None else str(row[1]),
            "question_type": row[2] or None,
            "question_score": None if row[3] is None else float(row[3]),
            "content": row[4] or "",
            "knowledge_links": [],
        }
        for row in rows
    ]
    if not questions:
        return questions
    ids = [item["id"] for item in questions]
    links = _all(
        session,
        "SELECT eqk.question_id, eqk.knowledge_id, eqk.weight, k.knowledge_name "
        "FROM tb_exam_question_knowledge eqk "
        "LEFT JOIN tb_knowledge k ON k.id = eqk.knowledge_id "
        "WHERE eqk.question_id = ANY(%s) ORDER BY eqk.question_id, eqk.knowledge_id",
        (ids,),
    )
    grouped: dict[int, list[dict[str, Any]]] = {}
    for link in links:
        grouped.setdefault(int(link[0]), []).append(
            {
                "knowledge_id": int(link[1]),
                "weight": None if link[2] is None else float(link[2]),
                "knowledge_name": link[3] or "",
            }
        )
    for item in questions:
        item["knowledge_links"] = grouped.get(item["id"], [])
    return questions


def _knowledge_options(session: Any, subject: str) -> list[dict[str, Any]]:
    if not subject:
        return []
    rows = _all(
        session,
        "SELECT id, knowledge_name FROM tb_knowledge WHERE subject = %s "
        "ORDER BY id LIMIT 500",
        (subject,),
    )
    return [{"id": int(row[0]), "knowledge_name": row[1] or ""} for row in rows]


def get_exam(
    db_type: str,
    config: dict[str, Any],
    actor: ExamActor,
    exam_id: int,
) -> dict[str, Any] | None:
    with WriteDbSession(db_type, config) as session:
        exam = _load_exam(session, int(exam_id))
        if exam is None or not exam_visible(exam, actor):
            return None
        score_count, detail_count = _score_counts(session, exam["id"])
        exam["score_count"] = score_count
        exam["detail_count"] = detail_count
        return {
            "exam": exam,
            "questions": _question_rows(session, exam["id"]),
            "knowledge_options": _knowledge_options(session, exam["subject"]),
        }


def create_exam(
    db_type: str,
    config: dict[str, Any],
    actor: ExamActor,
    data: dict[str, Any],
) -> dict[str, Any]:
    payload = _exam_payload(data, actor)
    with WriteDbSession(db_type, config) as session:
        created = _one(
            session,
            "INSERT INTO tb_exam "
            "(exam_name, exam_time, subject, exam_score, exam_type, school_id, owner_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (
                payload["exam_name"],
                payload["exam_time"],
                payload["subject"],
                payload["exam_score"],
                payload["exam_type"],
                payload["school_id"],
                payload["owner_id"],
            ),
        )
        if created is None:
            raise ValueError("创建试卷失败")
        exam = _load_exam(session, int(created[0]))
        session.commit()
    if exam is None:
        raise ValueError("创建试卷失败")
    return exam


def update_exam(
    db_type: str,
    config: dict[str, Any],
    actor: ExamActor,
    exam_id: int,
    data: dict[str, Any],
) -> dict[str, Any] | None:
    payload = _exam_payload(data, actor)
    with WriteDbSession(db_type, config) as session:
        current = _load_exam(session, int(exam_id))
        if current is None or not exam_visible(current, actor):
            return None
        _must_write(
            session,
            "UPDATE tb_exam SET exam_name = %s, exam_time = %s, subject = %s, "
            "exam_score = %s, exam_type = %s, school_id = %s, owner_id = %s WHERE id = %s",
            (
                payload["exam_name"],
                payload["exam_time"],
                payload["subject"],
                payload["exam_score"],
                payload["exam_type"],
                payload["school_id"],
                payload["owner_id"],
                int(exam_id),
            ),
        )
        _must_write(
            session,
            "UPDATE tb_exam_question SET exam_name = %s WHERE exam_id = %s",
            (payload["exam_name"], int(exam_id)),
        )
        exam = _load_exam(session, int(exam_id))
        session.commit()
    if exam is None:
        raise ValueError("更新试卷失败")
    return exam


def delete_exam(
    db_type: str,
    config: dict[str, Any],
    actor: ExamActor,
    exam_id: int,
) -> bool:
    with WriteDbSession(db_type, config) as session:
        current = _load_exam(session, int(exam_id))
        if current is None or not exam_visible(current, actor):
            return False
        score_count, detail_count = _score_counts(session, int(exam_id))
        if score_count or detail_count:
            raise ValueError(
                f"该试卷已有 {score_count} 条成绩、{detail_count} 条小题分，无法删除"
            )
        _must_write(
            session,
            "DELETE FROM tb_exam_question_knowledge WHERE question_id IN "
            "(SELECT id FROM tb_exam_question WHERE exam_id = %s)",
            (int(exam_id),),
        )
        _must_write(session, "DELETE FROM tb_exam_question WHERE exam_id = %s", (int(exam_id),))
        _must_write(session, "DELETE FROM tb_exam WHERE id = %s", (int(exam_id),))
        session.commit()
    return True


def _replace_links(session: Any, question_id: int, links: list[dict[str, Any]]) -> None:
    ids = [item["knowledge_id"] for item in links]
    if ids:
        found = _all(
            session,
            "SELECT id FROM tb_knowledge WHERE id = ANY(%s)",
            (ids,),
        )
        found_ids = {int(row[0]) for row in found}
        missing = [item for item in ids if item not in found_ids]
        if missing:
            raise ValueError("有知识点不存在，请重新选择")
    _must_write(
        session,
        "DELETE FROM tb_exam_question_knowledge WHERE question_id = %s",
        (question_id,),
    )
    for item in links:
        _must_write(
            session,
            "INSERT INTO tb_exam_question_knowledge (question_id, knowledge_id, weight) "
            "VALUES (%s, %s, %s)",
            (question_id, item["knowledge_id"], item["weight"]),
        )


def save_questions(
    db_type: str,
    config: dict[str, Any],
    actor: ExamActor,
    exam_id: int,
    rows: list[dict[str, Any]],
) -> dict[str, Any] | None:
    prepared = normalize_question_rows(rows)
    with WriteDbSession(db_type, config) as session:
        exam = _load_exam(session, int(exam_id))
        if exam is None or not exam_visible(exam, actor):
            return None
        existing = {
            int(row[0])
            for row in _all(
                session,
                "SELECT id FROM tb_exam_question WHERE exam_id = %s",
                (int(exam_id),),
            )
        }
        incoming = {item["id"] for item in prepared if item["id"]}
        unknown = incoming - existing
        if unknown:
            raise ValueError("包含不属于该试卷的题目")
        removed = existing - incoming
        if removed:
            _must_write(
                session,
                "DELETE FROM tb_exam_question_knowledge WHERE question_id = ANY(%s)",
                (list(removed),),
            )
            _must_write(
                session,
                "DELETE FROM tb_exam_question WHERE exam_id = %s AND id = ANY(%s)",
                (int(exam_id), list(removed)),
            )
        kept = [item for item in prepared if item["id"]]
        if kept:
            _must_write(
                session,
                "UPDATE tb_exam_question SET question_no = '#' || id::text "
                "WHERE exam_id = %s AND id = ANY(%s)",
                (int(exam_id), [item["id"] for item in kept]),
            )
        saved_ids: list[int] = []
        for item in prepared:
            if item["id"]:
                _must_write(
                    session,
                    "UPDATE tb_exam_question SET question_no = %s, question_type = %s, "
                    "question_score = %s, content = %s, exam_name = %s WHERE id = %s",
                    (
                        item["question_no"],
                        item["question_type"],
                        item["question_score"],
                        item["content"],
                        exam["exam_name"],
                        item["id"],
                    ),
                )
                question_id = int(item["id"])
            else:
                created = _one(
                    session,
                    "INSERT INTO tb_exam_question "
                    "(exam_name, question_no, content, question_score, exam_id, question_type) "
                    "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                    (
                        exam["exam_name"],
                        item["question_no"],
                        item["content"],
                        item["question_score"],
                        int(exam_id),
                        item["question_type"],
                    ),
                )
                if created is None:
                    raise ValueError(f"第 {item['question_no']} 题保存失败")
                question_id = int(created[0])
            _replace_links(session, question_id, item["knowledge_links"])
            saved_ids.append(question_id)
        questions = _question_rows(session, int(exam_id))
        session.commit()
    return {"question_ids": saved_ids, "questions": questions}


def set_question_links(
    db_type: str,
    config: dict[str, Any],
    actor: ExamActor,
    question_id: int,
    links: list[dict[str, Any]],
) -> bool:
    prepared = normalize_links(links)
    with WriteDbSession(db_type, config) as session:
        row = _one(
            session,
            "SELECT exam_id FROM tb_exam_question WHERE id = %s",
            (int(question_id),),
        )
        if row is None or row[0] is None:
            return False
        exam = _load_exam(session, int(row[0]))
        if exam is None or not exam_visible(exam, actor):
            return False
        _replace_links(session, int(question_id), prepared)
        session.commit()
    return True
