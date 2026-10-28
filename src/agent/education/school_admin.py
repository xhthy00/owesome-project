"""学校档案维护。

``tb_school.id`` / ``name`` 是同一枚学校编码，由学校名称按成绩导入的算法生成。
成绩、学生、试卷、达线指标、尖子班和账号都用这枚编码关联；成绩宽表的 ``xx``
存的是学校名称。因此有关联数据时不能改名称、也不能删除。区县、学段、层级只
写在学校档案上，可以改，之后的分析按新值计算。
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Any, Protocol

_SCHOOL_CIPHER_SECRET = b"yz_edu_k1"
_SCHOOL_HEX_START = 12
_SCHOOL_HEX_LEN = 8

SCHOOL_TYPES = ("引领校", "支撑校", "发展校", "其他校", "市报")
SCHOOL_STAGES = ("高中", "初中", "小学")

_ID_DEPENDENCIES = (
    ("tb_score", "成绩明细", "条"),
    ("tb_student", "学生", "条"),
    ("tb_exam_batch", "考试批次", "条"),
    ("tb_exam", "试卷", "条"),
    ("tb_score_indicator", "达线指标", "条"),
    ("tb_elite_class", "尖子班配置", "条"),
)

_FIELD_LABELS = {
    "s_name": "学校名称",
    "district": "区县",
    "stage": "学段",
    "type": "层级",
}

_FIELD_EFFECTS = {
    "s_name": "没有关联数据。保存后学校编码会按新名称重新生成。",
    "district": "之后的区域汇总按新区县计算，已经生成的报告不会改写。",
    "stage": "只改学校档案中的学段。学校编码不包含学段，不会因此重算。",
    "type": "之后的教研分档按新层级计算，已经生成的报告不会改写。",
}

_SCHOOL_COLUMNS = "id, name, s_name, district, stage, type"


class SchoolDb(Protocol):
    def query(self, sql: str, params: tuple | list = ()) -> list[dict[str, Any]]: ...

    def write(self, sql: str, params: tuple | list = ()) -> int: ...


def school_token(s_name: str) -> str:
    """与 ``raw_import._encode_school_token`` 一致的大写 ``GZ_`` 编码。"""
    digest = hmac.new(
        _SCHOOL_CIPHER_SECRET, s_name.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    hex_part = digest[_SCHOOL_HEX_START : _SCHOOL_HEX_START + _SCHOOL_HEX_LEN].upper()
    return f"GZ_{hex_part}"


def normalize_school_fields(raw: dict[str, Any]) -> dict[str, str]:
    s_name = str(raw.get("s_name") or "").strip()
    if not s_name:
        raise ValueError("请填写学校名称")
    if len(s_name) > 64:
        raise ValueError("学校名称不能超过 64 个字符")
    district = str(raw.get("district") or "").strip()
    if not district:
        raise ValueError("请填写区县")
    if len(district) > 64:
        raise ValueError("区县不能超过 64 个字符")
    stage = str(raw.get("stage") or "").strip() or "高中"
    if stage not in SCHOOL_STAGES:
        raise ValueError("学段仅支持高中、初中、小学")
    school_type = str(raw.get("type") or "").strip()
    if school_type not in SCHOOL_TYPES:
        raise ValueError("层级仅支持引领校、支撑校、发展校、其他校、市报")
    return {
        "s_name": s_name,
        "district": district,
        "stage": stage,
        "type": school_type,
    }


def _count(db: SchoolDb, sql: str, params: tuple | list) -> int:
    rows = db.query(sql, params)
    if not rows:
        return 0
    return int(rows[0].get("cnt") or 0)


def _school_row(raw: dict[str, Any]) -> dict[str, str]:
    return {
        "id": str(raw.get("id") or ""),
        "name": str(raw.get("name") or ""),
        "s_name": str(raw.get("s_name") or ""),
        "district": str(raw.get("district") or ""),
        "stage": str(raw.get("stage") or ""),
        "type": str(raw.get("type") or ""),
    }


def get_school(db: SchoolDb, school_id: str) -> dict[str, str]:
    rows = db.query(
        f"SELECT {_SCHOOL_COLUMNS} FROM tb_school WHERE id = %s",
        (school_id,),
    )
    if not rows:
        raise ValueError("学校不存在")
    return _school_row(rows[0])


def _dependency_items(db: SchoolDb, school: dict[str, str], account_count: int) -> list[dict[str, Any]]:
    school_id = school["id"]
    items: list[dict[str, Any]] = []
    for table, label, unit in _ID_DEPENDENCIES:
        count = _count(
            db,
            f"SELECT COUNT(*) AS cnt FROM {table} WHERE school_id = %s",
            (school_id,),
        )
        items.append({"key": table, "label": label, "count": count, "unit": unit})
    overview_count = 0
    if school["s_name"]:
        overview_count = _count(
            db,
            "SELECT COUNT(*) AS cnt FROM tb_score_overview WHERE xx = %s",
            (school["s_name"],),
        )
    items.append(
        {
            "key": "tb_score_overview",
            "label": "成绩宽表",
            "count": overview_count,
            "unit": "条",
        }
    )
    items.append(
        {
            "key": "sys_user",
            "label": "绑定账号",
            "count": int(account_count or 0),
            "unit": "个",
        }
    )
    return items


def _field_changes(current: dict[str, str], proposal: dict[str, str] | None) -> list[dict[str, str]]:
    if proposal is None:
        return []
    changes: list[dict[str, str]] = []
    for field in ("s_name", "district", "stage", "type"):
        before = current[field]
        after = proposal[field]
        if before == after:
            continue
        changes.append(
            {
                "field": field,
                "label": _FIELD_LABELS[field],
                "before": before,
                "after": after,
                "effect": _FIELD_EFFECTS[field],
            }
        )
    return changes


def dependency_message(impact: dict[str, Any], action: str) -> str:
    parts = [
        f"{item['label']} {item['count']} {item['unit']}"
        for item in impact["dependencies"]
        if item["count"]
    ]
    joined = "、".join(parts)
    if action == "delete":
        return f"该学校仍被引用，无法删除：{joined}"
    return f"学校名称已被引用，无法修改：{joined}"


def school_impact(
    db: SchoolDb,
    school_id: str,
    *,
    account_count: int = 0,
    proposal: dict[str, Any] | None = None,
) -> dict[str, Any]:
    current = get_school(db, school_id)
    normalized = normalize_school_fields(proposal) if proposal is not None else None
    dependencies = _dependency_items(db, current, account_count)
    has_dependency = any(item["count"] for item in dependencies)
    changes = _field_changes(current, normalized)
    renaming = any(item["field"] == "s_name" for item in changes)
    if renaming and has_dependency:
        for item in changes:
            if item["field"] == "s_name":
                item["effect"] = "已有关联数据使用当前名称或编码，不能修改学校名称。"
    notes = [
        "学校编码由学校名称生成。成绩、学生、试卷、考试批次、达线指标、尖子班和账号都用这枚编码关联。",
        "成绩宽表里的学校名就是当前学校名称。",
    ]
    if has_dependency:
        notes.append("存在关联数据时，不能删除，也不能修改学校名称。")
    else:
        notes.append("没有关联数据，可以删除。修改学校名称会重新生成学校编码。")
    return {
        "school_id": current["id"],
        "s_name": current["s_name"],
        "can_delete": not has_dependency,
        "can_rename": not has_dependency,
        "dependencies": dependencies,
        "changes": changes,
        "notes": notes,
    }


def list_schools(
    db: SchoolDb,
    *,
    page: int,
    page_size: int,
    keyword: str = "",
    district: str = "",
    school_type: str = "",
) -> dict[str, Any]:
    clauses: list[str] = []
    params: list[Any] = []
    text = keyword.strip()
    if text:
        clauses.append("(s_name ILIKE %s OR district ILIKE %s OR id ILIKE %s)")
        like = f"%{text}%"
        params.extend([like, like, like])
    if district.strip():
        clauses.append("district = %s")
        params.append(district.strip())
    if school_type.strip():
        if school_type.strip() not in SCHOOL_TYPES:
            raise ValueError("层级仅支持引领校、支撑校、发展校、其他校、市报")
        clauses.append("type = %s")
        params.append(school_type.strip())
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    total_rows = db.query(f"SELECT COUNT(*) AS total FROM tb_school{where}", params)
    total = int(total_rows[0]["total"]) if total_rows else 0
    offset = (page - 1) * page_size
    rows = db.query(
        f"SELECT {_SCHOOL_COLUMNS} FROM tb_school{where} "
        "ORDER BY s_name, id LIMIT %s OFFSET %s",
        [*params, page_size, offset],
    )
    return {
        "items": [_school_row(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def school_meta(db: SchoolDb) -> dict[str, Any]:
    rows = db.query(
        "SELECT DISTINCT district AS v FROM tb_school "
        "WHERE district IS NOT NULL AND btrim(district) <> '' ORDER BY district"
    )
    districts = [item for item in (str(row.get("v") or "").strip() for row in rows) if item]
    count_rows = db.query("SELECT type AS v, COUNT(*) AS n FROM tb_school GROUP BY type")
    counts = {
        str(row.get("v") or "").strip(): int(row.get("n") or 0) for row in count_rows
    }
    type_counts = [{"type": name, "count": counts.get(name, 0)} for name in SCHOOL_TYPES]
    known = set(SCHOOL_TYPES)
    total = sum(item["count"] for item in type_counts)
    total += sum(count for name, count in counts.items() if name not in known)
    return {
        "districts": districts,
        "types": list(SCHOOL_TYPES),
        "stages": list(SCHOOL_STAGES),
        "total": total,
        "type_counts": type_counts,
    }


def create_school(db: SchoolDb, raw: dict[str, Any]) -> dict[str, str]:
    values = normalize_school_fields(raw)
    if db.query("SELECT id FROM tb_school WHERE s_name = %s", (values["s_name"],)):
        raise ValueError("学校名称已存在")
    token = school_token(values["s_name"])
    if db.query("SELECT id FROM tb_school WHERE id = %s", (token,)):
        raise ValueError("学校编码与已有学校冲突，请调整学校名称")
    db.write(
        "INSERT INTO tb_school (id, name, s_name, district, stage, type) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (
            token,
            token,
            values["s_name"],
            values["district"],
            values["stage"],
            values["type"],
        ),
    )
    return get_school(db, token)


def update_school(
    db: SchoolDb,
    school_id: str,
    raw: dict[str, Any],
    *,
    account_count: int = 0,
) -> dict[str, str]:
    values = normalize_school_fields(raw)
    impact = school_impact(db, school_id, account_count=account_count, proposal=values)
    renaming = any(item["field"] == "s_name" for item in impact["changes"])
    if renaming and not impact["can_rename"]:
        raise ValueError(dependency_message(impact, "rename"))
    clash = db.query(
        "SELECT id FROM tb_school WHERE s_name = %s AND id <> %s",
        (values["s_name"], school_id),
    )
    if clash:
        raise ValueError("学校名称已存在")
    if renaming:
        token = school_token(values["s_name"])
        occupied = db.query("SELECT id FROM tb_school WHERE id = %s", (token,))
        if occupied and str(occupied[0].get("id") or "") != school_id:
            raise ValueError("学校编码与已有学校冲突，请调整学校名称")
        db.write(
            "UPDATE tb_school SET id = %s, name = %s, s_name = %s, "
            "district = %s, stage = %s, type = %s WHERE id = %s",
            (
                token,
                token,
                values["s_name"],
                values["district"],
                values["stage"],
                values["type"],
                school_id,
            ),
        )
        return get_school(db, token)
    db.write(
        "UPDATE tb_school SET district = %s, stage = %s, type = %s WHERE id = %s",
        (values["district"], values["stage"], values["type"], school_id),
    )
    return get_school(db, school_id)


def delete_school(db: SchoolDb, school_id: str, *, account_count: int = 0) -> None:
    impact = school_impact(db, school_id, account_count=account_count)
    if not impact["can_delete"]:
        raise ValueError(dependency_message(impact, "delete"))
    db.write("DELETE FROM tb_school WHERE id = %s", (school_id,))


__all__ = [
    "SCHOOL_STAGES",
    "SCHOOL_TYPES",
    "create_school",
    "delete_school",
    "dependency_message",
    "get_school",
    "list_schools",
    "normalize_school_fields",
    "school_impact",
    "school_meta",
    "school_token",
    "update_school",
]
