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
    "create_exam_batch",
    "delete_exam",
    "delete_exam_batch",
    "exam_visible",
    "get_exam",
    "list_exam_batches",
    "list_exams",
    "normalize_batch_fields",
    "normalize_ownership",
    "normalize_question_rows",
    "save_questions",
    "set_question_links",
    "update_exam_batch",
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
        shares = _one_decimal_shares([1.0] * len(seen))
    else:
        shares = _one_decimal_shares([float(row["weight"]) for row in seen])
    return [
        {"knowledge_id": row["knowledge_id"], "weight": shares[index]}
        for index, row in enumerate(seen)
    ]


def _one_decimal_shares(weights: list[float]) -> list[float]:
    """把权重收成一位小数，并保证合计为 1。"""
    count = len(weights)
    if not count:
        return []
    total = sum(weights)
    raw = [value / total * 10 for value in weights] if total > 0 else [10 / count] * count
    floors = [int(value) for value in raw]
    leftover = 10 - sum(floors)
    order = sorted(range(count), key=lambda index: (raw[index] - floors[index], -index), reverse=True)
    for step in range(max(leftover, 0)):
        floors[order[step % count]] += 1
    while sum(floors) > 10:
        index = max(range(count), key=lambda item: floors[item])
        if floors[index] <= 0:
            break
        floors[index] -= 1
    return [item / 10 for item in floors]


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


def _raise_db(msg: str | None, fallback: str) -> None:
    text = msg or fallback
    if "uk_exam_batch_subject" in text:
        raise ValueError("同一考试批次下该科目试卷已存在")
    if "uk_exam_batch_batch_name" in text:
        raise ValueError("批次名称已存在")
    raise ValueError(text)


def _one(session: Any, sql: str, params: tuple) -> tuple | None:
    ok, msg, data = session.execute_query(sql, params)
    if not ok:
        _raise_db(msg, "查询失败")
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
        _raise_db(msg, "写入失败")
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


def _parse_batch_id(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        batch_id = int(value)
    except (TypeError, ValueError):
        raise ValueError("考试批次无效") from None
    if batch_id <= 0:
        raise ValueError("考试批次无效")
    return batch_id


def _require_batch(session: Any, batch_id: int | None) -> str:
    if batch_id is None:
        raise ValueError("请关联考试批次")
    row = _one(session, "SELECT batch_name FROM tb_exam_batch WHERE id = %s", (batch_id,))
    name = "" if row is None else str(row[0] or "").strip()
    if row is None or not name:
        raise ValueError("考试批次不存在")
    return name


def _exam_payload(data: dict[str, Any], actor: ExamActor) -> dict[str, Any]:
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
    batch_id = _parse_batch_id(data.get("exam_batch_id"))
    if batch_id is None:
        raise ValueError("请关联考试批次")
    return {
        "exam_time": _parse_time(data.get("exam_time")),
        "subject": subject[:32],
        "exam_score": score,
        "exam_type": kind,
        "school_id": school_id,
        "owner_id": owner_id,
        "exam_batch_id": batch_id,
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
        "exam_batch_name": _text(row[9]),
        "question_count": int(row[10] or 0),
        "score_count": int(row[11] or 0),
        "detail_count": int(row[12] or 0),
        "question_score_sum": round(float(row[13] or 0), 2),
    }


_EXAM_COLUMNS = (
    "e.id, e.exam_name, e.exam_time, e.subject, e.exam_score, e.exam_type, "
    "e.school_id, e.owner_id, e.exam_batch_id, "
    "(SELECT b.batch_name FROM tb_exam_batch b WHERE b.id = e.exam_batch_id), "
    "(SELECT COUNT(*) FROM tb_exam_question q WHERE q.exam_id = e.id), "
    "(SELECT COUNT(*) FROM tb_score s WHERE s.exam_id = e.id), "
    "(SELECT COUNT(*) FROM tb_score_detail d WHERE d.exam_id = e.id), "
    "(SELECT COALESCE(SUM(q.question_score), 0) FROM tb_exam_question q WHERE q.exam_id = e.id)"
)


def list_exams(
    db_type: str,
    config: dict[str, Any],
    actor: ExamActor,
    *,
    keyword: str = "",
    exam_type: str = "",
    subject: str = "",
    exam_batch_id: int | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    page = max(1, int(page))
    page_size = min(100, max(1, int(page_size)))
    where, params = _visibility_sql(actor, "e")
    values: list[Any] = list(params)
    text = keyword.strip()
    if text:
        where += (
            " AND (e.exam_name ILIKE %s OR EXISTS ("
            "SELECT 1 FROM tb_exam_batch b WHERE b.id = e.exam_batch_id AND b.batch_name ILIKE %s))"
        )
        values.append(f"%{text}%")
        values.append(f"%{text}%")
    if exam_type.strip():
        where += " AND e.exam_type = %s"
        values.append(normalize_exam_type(exam_type))
    if subject.strip():
        where += " AND e.subject = %s"
        values.append(subject.strip())
    if exam_batch_id:
        where += " AND e.exam_batch_id = %s"
        values.append(int(exam_batch_id))
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


def _ensure_question_sort(session: Any) -> None:
    if "sort_no" in _columns(session, "tb_exam_question"):
        return
    _must_write(
        session,
        "ALTER TABLE tb_exam_question ADD COLUMN IF NOT EXISTS sort_no INTEGER",
        (),
    )
    session.commit()


def _question_rows(session: Any, exam_id: int) -> list[dict[str, Any]]:
    _ensure_question_sort(session)
    rows = _all(
        session,
        "SELECT id, question_no, question_type, question_score, content "
        "FROM tb_exam_question WHERE exam_id = %s ORDER BY sort_no NULLS LAST, id",
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
        "SELECT id, knowledge_name, stage, grade, module, chapter, secondary "
        "FROM tb_knowledge WHERE subject = %s "
        "ORDER BY stage, grade, module_no NULLS LAST, module, "
        "chapter_no NULLS LAST, chapter, secondary_no NULLS LAST, secondary NULLS LAST, "
        "knowledge_no NULLS LAST, id",
        (subject,),
    )
    return [
        {
            "id": int(row[0]),
            "knowledge_name": row[1] or "",
            "stage": row[2] or "",
            "grade": row[3] or "",
            "module": row[4] or "",
            "chapter": row[5] or "",
            "secondary": row[6] or "",
        }
        for row in rows
    ]


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
        payload["exam_name"] = _require_batch(session, payload["exam_batch_id"])
        created = _one(
            session,
            "INSERT INTO tb_exam "
            "(exam_name, exam_time, subject, exam_score, exam_type, school_id, owner_id, "
            "exam_batch_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (
                payload["exam_name"],
                payload["exam_time"],
                payload["subject"],
                payload["exam_score"],
                payload["exam_type"],
                payload["school_id"],
                payload["owner_id"],
                payload["exam_batch_id"],
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
        payload["exam_name"] = _require_batch(session, payload["exam_batch_id"])
        _must_write(
            session,
            "UPDATE tb_exam SET exam_name = %s, exam_time = %s, subject = %s, "
            "exam_score = %s, exam_type = %s, school_id = %s, owner_id = %s, "
            "exam_batch_id = %s WHERE id = %s",
            (
                payload["exam_name"],
                payload["exam_time"],
                payload["subject"],
                payload["exam_score"],
                payload["exam_type"],
                payload["school_id"],
                payload["owner_id"],
                payload["exam_batch_id"],
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


_BATCH_NAME_TABLES = {
    "tb_score_overview": "成绩宽表",
    "tb_fraction_bar": "分数线",
    "tb_score_indicator": "达线指标",
    "tb_score_contribution": "贡献度",
}


def normalize_batch_fields(name: Any, exam_time: Any) -> tuple[str, str]:
    text = str(name or "").strip()
    if not text:
        raise ValueError("批次名称不能为空")
    if len(text) > 255:
        raise ValueError("批次名称过长")
    when = _parse_time(exam_time)
    if when is None:
        raise ValueError("请填写考试时间")
    return text, when


def _columns(session: Any, table: str) -> set[str]:
    rows = _all(
        session,
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = %s",
        (table,),
    )
    return {str(row[0]) for row in rows}


def _batch_dict(row: tuple) -> dict[str, Any]:
    return {
        "id": int(row[0]),
        "batch_name": row[1] or "",
        "exam_time": _date_text(row[2]),
        "exam_count": int(row[3] or 0),
    }


def list_exam_batches(db_type: str, config: dict[str, Any]) -> list[dict[str, Any]]:
    with WriteDbSession(db_type, config) as session:
        rows = _all(
            session,
            "SELECT b.id, b.batch_name, b.exam_time, "
            "(SELECT COUNT(*) FROM tb_exam e WHERE e.exam_batch_id = b.id) "
            "FROM tb_exam_batch b ORDER BY b.exam_time DESC NULLS LAST, b.id DESC",
            (),
        )
    return [_batch_dict(row) for row in rows]


def create_exam_batch(db_type: str, config: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    name, when = normalize_batch_fields(data.get("batch_name"), data.get("exam_time"))
    with WriteDbSession(db_type, config) as session:
        created = _one(
            session,
            "INSERT INTO tb_exam_batch (batch_name, exam_time) VALUES (%s, %s) "
            "RETURNING id, batch_name, exam_time",
            (name, when),
        )
        if created is None:
            raise ValueError("创建批次失败")
        session.commit()
    return {
        "id": int(created[0]),
        "batch_name": created[1] or "",
        "exam_time": _date_text(created[2]),
        "exam_count": 0,
    }


def _rename_batch_refs(session: Any, batch_id: int, old_name: str, new_name: str) -> None:
    if not old_name or old_name == new_name:
        return
    _must_write(
        session,
        "UPDATE tb_exam_question q SET exam_name = %s "
        "FROM tb_exam e "
        "WHERE q.exam_id = e.id AND e.exam_batch_id = %s "
        "AND e.exam_name = %s AND q.exam_name = %s",
        (new_name, batch_id, old_name, old_name),
    )
    _must_write(
        session,
        "UPDATE tb_exam SET exam_name = %s WHERE exam_batch_id = %s AND exam_name = %s",
        (new_name, batch_id, old_name),
    )
    for table in _BATCH_NAME_TABLES:
        cols = _columns(session, table)
        if "exam_name" not in cols:
            continue
        if "exam_batch_id" in cols:
            _must_write(
                session,
                f"UPDATE {table} SET exam_name = %s WHERE exam_batch_id = %s OR exam_name = %s",
                (new_name, batch_id, old_name),
            )
        else:
            _must_write(
                session,
                f"UPDATE {table} SET exam_name = %s WHERE exam_name = %s",
                (new_name, old_name),
            )


def _batch_usage(session: Any, batch_id: int, name: str) -> list[str]:
    blocks: list[str] = []
    exam = _one(session, "SELECT COUNT(*) FROM tb_exam WHERE exam_batch_id = %s", (batch_id,))
    if exam and int(exam[0]):
        blocks.append(f"{int(exam[0])} 张试卷")
    for table, label in _BATCH_NAME_TABLES.items():
        cols = _columns(session, table)
        if "exam_batch_id" in cols:
            row = _one(
                session,
                f"SELECT COUNT(*) FROM {table} WHERE exam_batch_id = %s",
                (batch_id,),
            )
        elif "exam_name" in cols:
            row = _one(session, f"SELECT COUNT(*) FROM {table} WHERE exam_name = %s", (name,))
        else:
            continue
        count = int(row[0]) if row else 0
        if count:
            blocks.append(f"{count} 条{label}")
    return blocks


def update_exam_batch(
    db_type: str,
    config: dict[str, Any],
    batch_id: int,
    data: dict[str, Any],
) -> dict[str, Any] | None:
    name, when = normalize_batch_fields(data.get("batch_name"), data.get("exam_time"))
    with WriteDbSession(db_type, config) as session:
        current = _one(
            session,
            "SELECT id, batch_name FROM tb_exam_batch WHERE id = %s",
            (int(batch_id),),
        )
        if current is None:
            return None
        _must_write(
            session,
            "UPDATE tb_exam_batch SET batch_name = %s, exam_time = %s WHERE id = %s",
            (name, when, int(batch_id)),
        )
        _rename_batch_refs(session, int(batch_id), str(current[1] or ""), name)
        row = _one(
            session,
            "SELECT b.id, b.batch_name, b.exam_time, "
            "(SELECT COUNT(*) FROM tb_exam e WHERE e.exam_batch_id = b.id) "
            "FROM tb_exam_batch b WHERE b.id = %s",
            (int(batch_id),),
        )
        session.commit()
    if row is None:
        raise ValueError("更新批次失败")
    return _batch_dict(row)


def delete_exam_batch(db_type: str, config: dict[str, Any], batch_id: int) -> bool:
    with WriteDbSession(db_type, config) as session:
        current = _one(
            session,
            "SELECT id, batch_name FROM tb_exam_batch WHERE id = %s",
            (int(batch_id),),
        )
        if current is None:
            return False
        blocks = _batch_usage(session, int(batch_id), str(current[1] or ""))
        if blocks:
            raise ValueError(f"该批次已有{'、'.join(blocks)}，无法删除")
        _must_write(session, "DELETE FROM tb_exam_batch WHERE id = %s", (int(batch_id),))
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
        _ensure_question_sort(session)
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
        for index, item in enumerate(prepared):
            sort_no = index + 1
            if item["id"]:
                _must_write(
                    session,
                    "UPDATE tb_exam_question SET question_no = %s, question_type = %s, "
                    "question_score = %s, content = %s, exam_name = %s, sort_no = %s "
                    "WHERE id = %s",
                    (
                        item["question_no"],
                        item["question_type"],
                        item["question_score"],
                        item["content"],
                        exam["exam_name"],
                        sort_no,
                        item["id"],
                    ),
                )
                question_id = int(item["id"])
            else:
                created = _one(
                    session,
                    "INSERT INTO tb_exam_question "
                    "(exam_name, question_no, content, question_score, exam_id, "
                    "question_type, sort_no) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
                    (
                        exam["exam_name"],
                        item["question_no"],
                        item["content"],
                        item["question_score"],
                        int(exam_id),
                        item["question_type"],
                        sort_no,
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
