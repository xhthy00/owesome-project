"""教育四级数据权限：从 system_variables 解析范围并按模板编译 SQL 谓词。"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from system.models.user import SysUser

_DEFAULT_CONFIG_PATH = Path("config/education_permission.json")
_FIELD_SAFE = re.compile(r"^[A-Za-z_\u4e00-\u9fff][A-Za-z0-9_\u4e00-\u9fff]*$")
_VALID_ROLES = frozenset({"bureau_admin", "school_admin", "teacher", "student"})
_TEMPLATE_RE = re.compile(r"\$\{user\.(\w+)\}")


@dataclass
class EduScope:
    """用户教育数据范围（存于 system_variables）。"""

    edu_role: str = ""
    school_id: str = ""
    school_name: str = ""
    class_names: list[str] = field(default_factory=list)
    student_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.edu_role:
            out["edu_role"] = self.edu_role
        if self.school_id:
            out["school_id"] = self.school_id
        if self.school_name:
            out["school_name"] = self.school_name
        if self.class_names:
            out["class_names"] = list(self.class_names)
        if self.student_id:
            out["student_id"] = self.student_id
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "EduScope":
        if not isinstance(data, dict):
            return cls()
        role = str(data.get("edu_role") or "").strip()
        school_id_raw = data.get("school_id")
        school_id = str(school_id_raw).strip() if school_id_raw is not None and school_id_raw != "" else ""
        class_names_raw = data.get("class_names")
        class_names: list[str] = []
        if isinstance(class_names_raw, list):
            class_names = [str(x).strip() for x in class_names_raw if str(x).strip()]
        elif isinstance(class_names_raw, str) and class_names_raw.strip():
            class_names = [p.strip() for p in class_names_raw.replace("|", ",").split(",") if p.strip()]
        return cls(
            edu_role=role,
            school_id=school_id,
            school_name=str(data.get("school_name") or "").strip(),
            class_names=class_names,
            student_id=str(data.get("student_id") or "").strip(),
        )


def load_edu_permission_config(path: Path | str | None = None) -> dict[str, Any]:
    """读取 education_permission.json。"""
    cfg_path = Path(path or os.environ.get("EDU_PERMISSION_CONFIG_PATH", _DEFAULT_CONFIG_PATH))
    if not cfg_path.is_file():
        return {"version": "edu-perm-v1", "roles": {}, "column_fields": []}
    with cfg_path.open(encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


def parse_edu_scope(user: SysUser | None) -> EduScope:
    """从用户的 system_variables 解析教育范围。"""
    if user is None:
        return EduScope()
    raw = getattr(user, "system_variables", None)
    if not isinstance(raw, dict):
        return EduScope()
    return EduScope.from_dict(raw)


def merge_edu_scope_into_variables(
    existing: dict[str, Any] | None,
    scope: EduScope,
) -> dict[str, Any]:
    """将 EduScope 合并进 system_variables，保留非教育字段。"""
    base = dict(existing) if isinstance(existing, dict) else {}
    edu_keys = {"edu_role", "school_id", "school_name", "class_names", "student_id"}
    for k in edu_keys:
        base.pop(k, None)
    base.update(scope.to_dict())
    return base


def clear_edu_scope_from_variables(existing: dict[str, Any] | None) -> dict[str, Any]:
    """从 system_variables 移除教育权限字段，保留其他自定义变量。"""
    base = dict(existing) if isinstance(existing, dict) else {}
    for k in ("edu_role", "school_id", "school_name", "class_names", "student_id"):
        base.pop(k, None)
    return base


def validate_edu_scope(scope: EduScope) -> list[str]:
    """校验角色与必填字段，返回错误信息列表（空=通过）。"""
    if not scope.edu_role:
        return ["edu_role 不能为空"]
    if scope.edu_role not in _VALID_ROLES:
        return [f"无效的 edu_role: {scope.edu_role}"]
    cfg = load_edu_permission_config()
    role_cfg = (cfg.get("roles") or {}).get(scope.edu_role) or {}
    required = role_cfg.get("required_fields") or []
    errors: list[str] = []
    for fld in required:
        if fld == "school_id" and not (scope.school_id or "").strip():
            errors.append("school_id 为必填")
        elif fld == "class_names" and not scope.class_names:
            errors.append("class_names 为必填")
        elif fld == "student_id" and not scope.student_id:
            errors.append("student_id 为必填")
    return errors


def list_edu_roles() -> list[dict[str, Any]]:
    """返回四级角色定义（供前端/API）。"""
    cfg = load_edu_permission_config()
    roles_cfg = cfg.get("roles") or {}
    out: list[dict[str, Any]] = []
    for code in ("bureau_admin", "school_admin", "teacher", "student"):
        rc = roles_cfg.get(code) or {}
        out.append(
            {
                "code": code,
                "label": rc.get("label") or code,
                "required_fields": list(rc.get("required_fields") or []),
            }
        )
    return out


def resolve_template_value(template: Any, scope: EduScope) -> Any:
    """替换 ``${user.school_id}`` 等模板变量。"""
    if template is None:
        return None
    if not isinstance(template, str):
        return template
    if not _TEMPLATE_RE.search(template):
        return template

    def _repl(m: re.Match[str]) -> str:
        key = m.group(1)
        if key == "school_id":
            return scope.school_id or ""
        if key == "school_name":
            return scope.school_name
        if key == "class_names":
            return "|".join(scope.class_names)
        if key == "student_id":
            return scope.student_id
        return ""

    return _TEMPLATE_RE.sub(_repl, template)


def _sql_literal(value: str, db_type: str) -> str:
    s = value.replace("'", "''")
    return "'" + s + "'"


def _compile_predicate(field: str, op: str, value: Any, db_type: str) -> Optional[str]:
    """编译单条谓词（无表前缀列名，供最外层 WHERE 合并）。"""
    if not field or not _FIELD_SAFE.match(field):
        return None
    op = (op or "=").strip().lower()
    q = "`" if db_type == "mysql" else '"'
    col = f"{q}{field}{q}"

    if op == "in":
        items: list[str] = []
        if isinstance(value, list):
            items = [str(v).strip() for v in value if str(v).strip()]
        elif isinstance(value, str) and value.strip():
            items = [p.strip() for p in value.replace("|", ",").split(",") if p.strip()]
        if not items:
            return None
        lits = ", ".join(_sql_literal(v, db_type) for v in items)
        return f"{col} IN ({lits})"

    if op not in {"=", "!=", ">", "<", ">=", "<=", "like"}:
        return None
    if value is None or value == "":
        return None
    lit = _sql_literal(str(value), db_type)
    if op == "like":
        return f"{col} LIKE {lit}"
    return f"{col} {op.upper()} {lit}"


def build_edu_row_predicates(user: SysUser | None, db_type: str) -> list[str]:
    """按 edu_role 编译谓词 SQL 片段列表（无表前缀）。"""
    scope = parse_edu_scope(user)
    if not scope.edu_role or scope.edu_role not in _VALID_ROLES:
        return []
    cfg = load_edu_permission_config()
    role_cfg = (cfg.get("roles") or {}).get(scope.edu_role) or {}
    predicates_cfg = role_cfg.get("predicates") or []
    if not predicates_cfg:
        return []

    preds: list[str] = []
    for p in predicates_cfg:
        if not isinstance(p, dict):
            continue
        field = str(p.get("field") or "").strip()
        op = str(p.get("op") or "=").strip().lower()
        raw_val = resolve_template_value(p.get("value"), scope)
        if op == "in" and isinstance(raw_val, str) and "|" in raw_val:
            val: Any = [x.strip() for x in raw_val.split("|") if x.strip()]
        elif op == "in" and scope.class_names and field == "class":
            val = scope.class_names
        else:
            val = raw_val
        frag = _compile_predicate(field, op, val, db_type)
        if frag:
            preds.append(frag)
    return preds


def edu_scope_summary(user: SysUser | None) -> dict[str, Any]:
    """供 /me 与 effective 预览的摘要。"""
    scope = parse_edu_scope(user)
    cfg = load_edu_permission_config()
    role_cfg = (cfg.get("roles") or {}).get(scope.edu_role) or {}
    return {
        "edu_role": scope.edu_role or None,
        "edu_role_label": role_cfg.get("label") or scope.edu_role or None,
        "school_id": scope.school_id or None,
        "school_name": scope.school_name or None,
        "class_names": scope.class_names or None,
        "student_id": scope.student_id or None,
    }


__all__ = [
    "EduScope",
    "build_edu_row_predicates",
    "clear_edu_scope_from_variables",
    "edu_scope_summary",
    "list_edu_roles",
    "load_edu_permission_config",
    "merge_edu_scope_into_variables",
    "parse_edu_scope",
    "resolve_template_value",
    "validate_edu_scope",
]
