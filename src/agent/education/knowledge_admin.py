"""知识点管理：列表、级联筛选、手工维护、Excel 导入与内容版本。

``knowledge_name`` 与 ``knowledge_content`` 始终写成同一个值。
导入按 ``knowledge_no`` 覆盖更新，不改 ``ability_level``。
"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from typing import Any, Protocol

import openpyxl

HEADER_MAP = {
    "学段": "stage",
    "年级": "grade",
    "学科科目": "subject",
    "学科编码": "subject_no",
    "大概念": "module",
    "大概念编码": "module_no",
    "重要概念": "chapter",
    "重要概念编码": "chapter_no",
    "次位概念": "secondary",
    "次位概念编码": "secondary_no",
    "知识点名称": "knowledge_name",
    "知识点编码": "knowledge_no",
}

FIELD_LABELS = {value: key for key, value in HEADER_MAP.items()}

LIMITS = {
    "stage": 32,
    "grade": 32,
    "subject": 32,
    "subject_no": 16,
    "module": 64,
    "module_no": 16,
    "chapter": 128,
    "chapter_no": 16,
    "secondary": 128,
    "secondary_no": 16,
    "knowledge_name": 255,
    "knowledge_no": 16,
}

REQUIRED_FIELDS = (
    "stage",
    "grade",
    "subject",
    "subject_no",
    "module",
    "module_no",
    "chapter",
    "chapter_no",
    "knowledge_name",
    "knowledge_no",
)

OPTIONAL_FIELDS = ("secondary", "secondary_no")

CASCADE_LEVELS = ("stage", "grade", "subject", "module", "chapter", "secondary")

_ALLOWED_ROLES = frozenset({"bureau_admin", "school_admin", "teacher"})

_LIST_COLUMNS = (
    "id",
    "knowledge_no",
    "stage",
    "grade",
    "subject",
    "subject_no",
    "module",
    "module_no",
    "chapter",
    "chapter_no",
    "secondary",
    "secondary_no",
    "knowledge_name",
    "knowledge_content",
)

_WRITE_COLUMNS = (
    "stage",
    "grade",
    "subject",
    "subject_no",
    "module",
    "module_no",
    "chapter",
    "chapter_no",
    "secondary",
    "secondary_no",
    "knowledge_no",
    "knowledge_name",
    "knowledge_content",
)


class KnowledgeDb(Protocol):
    def query(self, sql: str, params: tuple | list = ()) -> list[dict[str, Any]]: ...

    def write(self, sql: str, params: tuple | list = ()) -> int: ...


@dataclass
class RowError:
    row: int
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {"row": self.row, "message": self.message}


@dataclass
class KnowledgeRow:
    row_no: int
    values: dict[str, str | None]

    @property
    def knowledge_no(self) -> str:
        return str(self.values["knowledge_no"] or "")


def knowledge_access_error(edu_role: str, *, is_platform_admin: bool) -> str | None:
    """平台管理员、局端、校管理员和教师可维护知识点。"""
    if is_platform_admin:
        return None
    role = (edu_role or "").strip()
    if role in _ALLOWED_ROLES:
        return None
    if role == "student":
        return "学生无权管理知识点"
    return "未配置教育角色，无法管理知识点"


def cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value != value:  # NaN
            return ""
        if value.is_integer():
            return str(int(value))
        return format(value, "f").rstrip("0").rstrip(".")
    text = str(value).strip()
    if len(text) > 2 and text.endswith(".0") and text[:-2].isdigit():
        return text[:-2]
    return text


def parse_knowledge_xlsx(data: bytes) -> tuple[list[KnowledgeRow], list[RowError]]:
    """解析导入模板。空行跳过；文件内重复的知识点编码记为错误行。"""
    try:
        workbook = openpyxl.load_workbook(BytesIO(data), data_only=True)
    except Exception as exc:  # noqa: BLE001
        raise ValueError("无法读取 Excel，请上传 .xlsx 模板") from exc
    sheet = workbook.worksheets[0] if workbook.worksheets else None
    if sheet is None:
        raise ValueError("Excel 没有工作表")

    header_row_no = _find_header_row(sheet)
    header_cells = list(next(sheet.iter_rows(min_row=header_row_no, max_row=header_row_no, values_only=True)))
    columns: dict[str, int] = {}
    for index, cell in enumerate(header_cells):
        name = cell_text(cell)
        field_name = HEADER_MAP.get(name)
        if field_name and field_name not in columns:
            columns[field_name] = index
    missing = [FIELD_LABELS[name] for name in HEADER_MAP.values() if name not in columns]
    if missing:
        raise ValueError("模板缺少列：" + "、".join(missing))

    rows: list[KnowledgeRow] = []
    errors: list[RowError] = []
    seen: dict[str, int] = {}
    for row_no, raw in enumerate(
        sheet.iter_rows(min_row=header_row_no + 1, values_only=True),
        start=header_row_no + 1,
    ):
        values = {
            field_name: cell_text(raw[index] if index < len(raw) else None)
            for field_name, index in columns.items()
        }
        if not any(values.values()):
            continue
        try:
            normalized = normalize_fields(values)
        except ValueError as exc:
            errors.append(RowError(row_no, str(exc)))
            continue
        knowledge_no = str(normalized["knowledge_no"])
        previous = seen.get(knowledge_no)
        if previous is not None:
            errors.append(RowError(row_no, f"知识点编码与第 {previous} 行重复"))
            continue
        seen[knowledge_no] = row_no
        rows.append(KnowledgeRow(row_no=row_no, values=normalized))
    if not rows and not errors:
        raise ValueError("未解析到有效知识点数据")
    return rows, errors


def normalize_fields(raw: dict[str, Any]) -> dict[str, str | None]:
    """校验并整理一行。知识点内容与名称相同；次位概念可空。"""
    cleaned: dict[str, str | None] = {}
    for name in REQUIRED_FIELDS:
        text = cell_text(raw.get(name)).strip()
        if not text:
            raise ValueError(f"{FIELD_LABELS[name]}不能为空")
        if len(text) > LIMITS[name]:
            raise ValueError(f"{FIELD_LABELS[name]}不能超过 {LIMITS[name]} 个字符")
        cleaned[name] = text
    for name in OPTIONAL_FIELDS:
        text = cell_text(raw.get(name)).strip()
        if text and len(text) > LIMITS[name]:
            raise ValueError(f"{FIELD_LABELS[name]}不能超过 {LIMITS[name]} 个字符")
        cleaned[name] = text or None
    cleaned["knowledge_content"] = cleaned["knowledge_name"]
    return cleaned


def list_knowledge(
    db: KnowledgeDb,
    *,
    page: int,
    page_size: int,
    stage: str = "",
    grade: str = "",
    subject: str = "",
    module: str = "",
    chapter: str = "",
    secondary: str = "",
    keyword: str = "",
) -> dict[str, Any]:
    page_size = min(100, max(1, int(page_size)))
    page = max(1, int(page))
    where, params = _filters(
        stage=stage,
        grade=grade,
        subject=subject,
        module=module,
        chapter=chapter,
        secondary=secondary,
        keyword=keyword,
    )
    count_sql = f"SELECT COUNT(*) AS cnt FROM tb_knowledge{where}"
    total = int(db.query(count_sql, params)[0]["cnt"])
    if total == 0:
        page = 1
    else:
        page = min(page, (total + page_size - 1) // page_size)
    offset = (page - 1) * page_size
    cols = ", ".join(_LIST_COLUMNS)
    list_sql = (
        f"SELECT {cols} FROM tb_knowledge{where} "
        "ORDER BY stage, grade, subject, module, chapter, secondary NULLS LAST, "
        "knowledge_no, id LIMIT %s OFFSET %s"
    )
    items = db.query(list_sql, [*params, page_size, offset])
    return {"items": items, "total": total, "page": page, "page_size": page_size}


def cascade_options(db: KnowledgeDb, level: str, parents: dict[str, str]) -> list[str]:
    if level not in CASCADE_LEVELS:
        raise ValueError("级联层级无效")
    index = CASCADE_LEVELS.index(level)
    clauses: list[str] = []
    params: list[str] = []
    for parent in CASCADE_LEVELS[:index]:
        value = (parents.get(parent) or "").strip()
        if not value:
            raise ValueError(f"请先选择{FIELD_LABELS[parent]}")
        clauses.append(f"{parent} = %s")
        params.append(value)
    clauses.append(f"{level} IS NOT NULL AND btrim({level}::text) <> ''")
    where = " WHERE " + " AND ".join(clauses)
    rows = db.query(
        f"SELECT DISTINCT {level} AS v FROM tb_knowledge{where} ORDER BY {level}",
        params,
    )
    return [str(row["v"]).strip() for row in rows if str(row.get("v") or "").strip()]


def create_knowledge(db: KnowledgeDb, raw: dict[str, Any], operator: str) -> dict[str, Any]:
    values = normalize_fields(raw)
    knowledge_no = values["knowledge_no"]
    existing = db.query("SELECT id FROM tb_knowledge WHERE knowledge_no = %s", (knowledge_no,))
    if existing:
        raise ValueError("知识点编码已存在")
    cols = ", ".join(_WRITE_COLUMNS)
    placeholders = ", ".join(["%s"] * len(_WRITE_COLUMNS))
    db.write(
        f"INSERT INTO tb_knowledge ({cols}) VALUES ({placeholders})",
        tuple(values[name] for name in _WRITE_COLUMNS),
    )
    created = db.query(
        f"SELECT {', '.join(_LIST_COLUMNS)} FROM tb_knowledge WHERE knowledge_no = %s",
        (knowledge_no,),
    )
    if not created:
        raise ValueError("新增知识点后未找到记录")
    row = created[0]
    _append_version(db, int(row["id"]), row["knowledge_content"], "create", "manual", operator)
    return row


def update_knowledge(
    db: KnowledgeDb, knowledge_id: int, raw: dict[str, Any], operator: str
) -> dict[str, Any]:
    values = normalize_fields(raw)
    current_rows = db.query(
        "SELECT id, knowledge_name, knowledge_content FROM tb_knowledge WHERE id = %s",
        (knowledge_id,),
    )
    if not current_rows:
        raise ValueError("知识点不存在")
    current = current_rows[0]
    clash = db.query(
        "SELECT id FROM tb_knowledge WHERE knowledge_no = %s AND id <> %s",
        (values["knowledge_no"], knowledge_id),
    )
    if clash:
        raise ValueError("知识点编码已存在")
    assignments = ", ".join(f"{name} = %s" for name in _WRITE_COLUMNS)
    db.write(
        f"UPDATE tb_knowledge SET {assignments} WHERE id = %s",
        tuple(values[name] for name in _WRITE_COLUMNS) + (knowledge_id,),
    )
    new_name = values["knowledge_name"]
    if new_name != current.get("knowledge_name") or new_name != current.get("knowledge_content"):
        _append_version(db, knowledge_id, new_name, "update", "manual", operator)
    updated = db.query(
        f"SELECT {', '.join(_LIST_COLUMNS)} FROM tb_knowledge WHERE id = %s",
        (knowledge_id,),
    )
    return updated[0]


def delete_knowledge(db: KnowledgeDb, knowledge_id: int, operator: str) -> None:
    rows = db.query(
        "SELECT id, knowledge_content FROM tb_knowledge WHERE id = %s",
        (knowledge_id,),
    )
    if not rows:
        raise ValueError("知识点不存在")
    refs = db.query(
        "SELECT COUNT(*) AS cnt FROM tb_exam_question_knowledge WHERE knowledge_id = %s",
        (knowledge_id,),
    )
    count = int(refs[0]["cnt"]) if refs else 0
    if count > 0:
        raise ValueError(f"该知识点已被 {count} 道题目引用，无法删除")
    _append_version(db, knowledge_id, rows[0].get("knowledge_content"), "delete", "manual", operator)
    db.write("DELETE FROM tb_knowledge WHERE id = %s", (knowledge_id,))


def list_versions(db: KnowledgeDb, knowledge_id: int) -> list[dict[str, Any]]:
    exists = db.query("SELECT id FROM tb_knowledge WHERE id = %s", (knowledge_id,))
    if not exists:
        raise ValueError("知识点不存在")
    return db.query(
        """
        SELECT id, knowledge_id, version_no, knowledge_content, change_type,
               operator, source, operated_at
        FROM tb_knowledge_version
        WHERE knowledge_id = %s
        ORDER BY version_no DESC
        """,
        (knowledge_id,),
    )


def import_knowledge_rows(
    db: KnowledgeDb, rows: list[KnowledgeRow], operator: str
) -> tuple[int, int]:
    """按知识点编码新增或覆盖，并各记一条 import 版本。返回 (inserted, updated)。"""
    if not rows:
        return 0, 0
    numbers = [row.knowledge_no for row in rows]
    found = db.query(
        "SELECT id, knowledge_no FROM tb_knowledge WHERE knowledge_no = ANY(%s)",
        (numbers,),
    )
    by_no = {str(item["knowledge_no"]): int(item["id"]) for item in found}
    inserted = 0
    updated = 0
    for row in rows:
        values = row.values
        knowledge_id = by_no.get(row.knowledge_no)
        if knowledge_id is None:
            cols = ", ".join(_WRITE_COLUMNS)
            placeholders = ", ".join(["%s"] * len(_WRITE_COLUMNS))
            db.write(
                f"INSERT INTO tb_knowledge ({cols}) VALUES ({placeholders})",
                tuple(values[name] for name in _WRITE_COLUMNS),
            )
            created = db.query(
                "SELECT id FROM tb_knowledge WHERE knowledge_no = %s",
                (row.knowledge_no,),
            )
            knowledge_id = int(created[0]["id"])
            by_no[row.knowledge_no] = knowledge_id
            inserted += 1
        else:
            assignments = ", ".join(f"{name} = %s" for name in _WRITE_COLUMNS)
            db.write(
                f"UPDATE tb_knowledge SET {assignments} WHERE id = %s",
                tuple(values[name] for name in _WRITE_COLUMNS) + (knowledge_id,),
            )
            updated += 1
        _append_version(
            db, knowledge_id, values["knowledge_content"], "import", "import", operator
        )
    return inserted, updated


def _append_version(
    db: KnowledgeDb,
    knowledge_id: int,
    content: str | None,
    change_type: str,
    source: str,
    operator: str,
) -> None:
    max_rows = db.query(
        "SELECT COALESCE(MAX(version_no), 0) AS v FROM tb_knowledge_version WHERE knowledge_id = %s",
        (knowledge_id,),
    )
    next_no = int(max_rows[0]["v"]) + 1 if max_rows else 1
    db.write(
        """
        INSERT INTO tb_knowledge_version
            (knowledge_id, version_no, knowledge_content, change_type, operator, source)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (knowledge_id, next_no, content, change_type, _operator(operator), source),
    )


def _operator(operator: str) -> str:
    text = (operator or "system").strip() or "system"
    return text[:64]


def _filters(**raw: str) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    for name in ("stage", "grade", "subject", "module", "chapter", "secondary"):
        value = (raw.get(name) or "").strip()
        if value:
            clauses.append(f"{name} = %s")
            params.append(value)
    keyword = (raw.get("keyword") or "").strip()
    if keyword:
        escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        clauses.append("knowledge_name LIKE %s ESCAPE '\\'")
        params.append(f"%{escaped}%")
    if not clauses:
        return "", params
    return " WHERE " + " AND ".join(clauses), params


def _find_header_row(sheet: Any) -> int:
    for row_no, raw in enumerate(sheet.iter_rows(max_row=5, values_only=True), start=1):
        labels = {cell_text(cell) for cell in raw}
        if "知识点编码" in labels and "知识点名称" in labels:
            return row_no
    raise ValueError("未找到表头，请使用知识点导入模板")


__all__ = [
    "CASCADE_LEVELS",
    "KnowledgeRow",
    "RowError",
    "cascade_options",
    "cell_text",
    "create_knowledge",
    "delete_knowledge",
    "import_knowledge_rows",
    "knowledge_access_error",
    "list_knowledge",
    "list_versions",
    "normalize_fields",
    "parse_knowledge_xlsx",
    "update_knowledge",
]
