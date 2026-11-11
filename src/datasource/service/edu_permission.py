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
_SCHOOL_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_SCHOOL_CODE_PREFIX_RE = re.compile(r"^[A-Za-z]\d{2}")
_SCHOOL_CATALOG_LIMIT = 800


@dataclass
class EduScope:
    """用户教育数据范围（存于 system_variables）。"""

    edu_role: str = ""
    school_id: str = ""
    school_name: str = ""
    class_names: list[str] = field(default_factory=list)
    student_id: str = ""
    remark: str = ""

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
        if self.remark:
            out["edu_remark"] = self.remark
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
            remark=str(data.get("edu_remark") or data.get("remark") or "").strip()[:80],
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
    edu_keys = {"edu_role", "school_id", "school_name", "class_names", "student_id", "edu_remark", "remark"}
    for k in edu_keys:
        base.pop(k, None)
    base.update(scope.to_dict())
    return base


def clear_edu_scope_from_variables(existing: dict[str, Any] | None) -> dict[str, Any]:
    """从 system_variables 移除教育权限字段，保留其他自定义变量。"""
    base = dict(existing) if isinstance(existing, dict) else {}
    for k in ("edu_role", "school_id", "school_name", "class_names", "student_id", "edu_remark", "remark"):
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


def looks_like_school_token(value: str) -> bool:
    """密文学校 ID（gz_/GZ_ 等），不含中文。"""
    tok = str(value or "").strip()
    if not tok or re.search(r"[\u4e00-\u9fff]", tok):
        return False
    return bool(_SCHOOL_TOKEN_RE.fullmatch(tok))


def edu_school_display_label(school_id: str, name: str = "", s_name: str = "") -> str:
    """权限页展示名：优先 s_name（B04安宜中学），密文回落到 id。"""
    for cand in (s_name, name):
        text = str(cand or "").strip()
        if text and not looks_like_school_token(text):
            return text
    return str(school_id or "").strip()


def _school_name_key(value: str) -> str:
    text = str(value or "").strip().lower()
    return _SCHOOL_CODE_PREFIX_RE.sub("", text)


def _catalog_item(school_id: str, name: str, s_name: str) -> dict[str, str]:
    sid = str(school_id or "").strip()
    nm = str(name or "").strip()
    sn = str(s_name or "").strip()
    return {
        "id": sid,
        "name": nm,
        "s_name": sn,
        "label": edu_school_display_label(sid, nm, sn),
    }


def apply_school_catalog(scope: EduScope, catalog: list[dict[str, Any]]) -> str | None:
    """把明文校名解析成密文 school_id；school_name 存展示名。失败返回错误文案。"""
    sid = str(scope.school_id or "").strip()
    sname = str(scope.school_name or "").strip()
    if not sid and not sname:
        return None

    items = [r for r in catalog if isinstance(r, dict) and str(r.get("id") or "").strip()]
    by_id = {str(r["id"]).strip(): r for r in items}

    if looks_like_school_token(sid):
        hit = by_id.get(sid)
        scope.school_id = sid
        if hit:
            scope.school_name = str(hit.get("label") or sname or sid).strip()
        elif sname and not looks_like_school_token(sname):
            scope.school_name = sname
        return None

    query = sid if sid and not looks_like_school_token(sid) else sname
    if not query:
        return None
    if not items:
        return "无法从数据源解析学校名称，请填写密文学校 ID"

    hit, err = match_edu_school(items, query)
    if err:
        return err
    if hit is None:
        return f"未找到学校：{query}"
    scope.school_id = str(hit.get("id") or "").strip()
    scope.school_name = str(hit.get("label") or query).strip()
    return None


def match_edu_school(
    catalog: list[dict[str, Any]],
    query: str,
) -> tuple[dict[str, Any] | None, str | None]:
    """按 id / 展示名精确或包含匹配一所学校。"""
    q = str(query or "").strip()
    if not q:
        return None, None
    for row in catalog:
        if str(row.get("id") or "").strip() == q:
            return row, None
    qk = _school_name_key(q)
    exact: list[dict[str, Any]] = []
    contains: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in catalog:
        rid = str(row.get("id") or "").strip()
        if not rid or rid in seen:
            continue
        labels = [
            str(row.get("s_name") or "").strip(),
            str(row.get("name") or "").strip(),
            str(row.get("label") or "").strip(),
        ]
        keys = [_school_name_key(x) for x in labels if x]
        if q in labels or (qk and qk in keys):
            exact.append(row)
            seen.add(rid)
        elif qk and len(qk) >= 2 and any(qk in k for k in keys):
            contains.append(row)
            seen.add(rid)
    if len(exact) == 1:
        return exact[0], None
    if len(exact) > 1:
        return None, f"匹配到多所学校：{q}"
    if len(contains) == 1:
        return contains[0], None
    if len(contains) > 1:
        return None, f"匹配到多所学校：{q}"
    return None, None


def _rows_as_dicts(result: Any) -> list[dict[str, Any]]:
    if not isinstance(result, dict):
        return []
    cols = [str(c).strip().lower() for c in (result.get("columns") or [])]
    out: list[dict[str, Any]] = []
    for row in result.get("rows") or []:
        if isinstance(row, dict):
            out.append({str(k).strip().lower(): v for k, v in row.items()})
            continue
        if isinstance(row, (list, tuple)):
            item: dict[str, Any] = {}
            for i, col in enumerate(cols):
                if i < len(row):
                    item[col] = row[i]
            if item:
                out.append(item)
    return out


def _catalog_datasource_ids(
    session: Any,
    workspace_oid: int | None,
    requested: int | None,
) -> list[int]:
    from datasource.crud import crud_datasource

    ids: list[int] = []
    if requested is not None:
        ids.append(int(requested))
    oid = int(workspace_oid) if workspace_oid is not None else None
    for ds in crud_datasource.get_datasources(session, skip=0, limit=100, oid=oid):
        ds_id = int(ds.id)
        if ds_id not in ids:
            ids.append(ds_id)
    return ids


def _query_tb_school_catalog(datasource_id: int, workspace_oid: int | None) -> list[dict[str, str]]:
    from src.agent.resource.tool.business import _load_datasource
    from src.datasource.db.db import execute_sql

    db_type, config, _name = _load_datasource(int(datasource_id), workspace_oid)
    ok, _msg, result = execute_sql(
        db_type,
        config,
        "SELECT id, name, s_name FROM tb_school "
        f"ORDER BY COALESCE(NULLIF(s_name, ''), name, id) LIMIT {_SCHOOL_CATALOG_LIMIT}",
    )
    if not ok:
        return []
    items: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in _rows_as_dicts(result):
        item = _catalog_item(
            str(row.get("id") or ""),
            str(row.get("name") or ""),
            str(row.get("s_name") or ""),
        )
        sid = item["id"]
        if not sid or sid in seen:
            continue
        seen.add(sid)
        items.append(item)
    return items


def list_edu_school_catalog(
    session: Any,
    datasource_id: int | None = None,
    workspace_oid: int | None = None,
) -> tuple[int | None, list[dict[str, str]]]:
    """管理员权限页用的学校目录（id + 明文展示名）。不走行级权限过滤。"""
    last_id: int | None = None
    for ds_id in _catalog_datasource_ids(session, workspace_oid, datasource_id):
        last_id = ds_id
        try:
            items = _query_tb_school_catalog(ds_id, workspace_oid)
        except Exception:
            continue
        if items:
            return ds_id, items
    return last_id, []


def lookup_school_display_name(
    session: Any,
    school_id: str,
    workspace_oid: int | None = None,
    datasource_id: int | None = None,
) -> str:
    """按密文 school_id 取 tb_school 明文展示名；找不到返回空串。"""
    sid = str(school_id or "").strip()
    if not sid or not looks_like_school_token(sid):
        return ""
    lit = sid.replace("'", "''")
    for ds_id in _catalog_datasource_ids(session, workspace_oid, datasource_id):
        try:
            from src.agent.resource.tool.business import _load_datasource
            from src.datasource.db.db import execute_sql

            db_type, config, _name = _load_datasource(int(ds_id), workspace_oid)
            ok, _msg, result = execute_sql(
                db_type,
                config,
                "SELECT id, name, s_name FROM tb_school "
                f"WHERE id = '{lit}' OR name = '{lit}' LIMIT 1",
            )
        except Exception:
            continue
        if not ok:
            continue
        rows = _rows_as_dicts(result)
        if not rows:
            continue
        row = rows[0]
        label = edu_school_display_label(
            str(row.get("id") or sid),
            str(row.get("name") or ""),
            str(row.get("s_name") or ""),
        )
        if label and not looks_like_school_token(label):
            return label
    return ""


def resolve_scope_school(
    session: Any,
    scope: EduScope,
    datasource_id: int | None = None,
    workspace_oid: int | None = None,
) -> str | None:
    """写入前把明文校名解析为密文 school_id。"""
    _ds_id, catalog = list_edu_school_catalog(session, datasource_id, workspace_oid)
    return apply_school_catalog(scope, catalog)


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
        "edu_remark": scope.remark or None,
    }


def edu_scope_dict_for_user_id(user_id: int) -> dict[str, Any]:
    """按 user_id 读教育范围；读库失败时返回空（调用方应放行，不拦截）。"""
    if not user_id:
        return {}
    try:
        from src.common.core.database import get_db_session
        from src.system.crud.crud_user import get_user_by_id

        with get_db_session() as session:
            user = get_user_by_id(session, int(user_id))
            variables = getattr(user, "system_variables", None) or {}
            if isinstance(variables, dict) and variables.get("edu_auth_v2"):
                return {"edu_role": "v2"}  # SQL verifier, rather than legacy name regex, owns the scope.
            return edu_scope_summary(user)
    except Exception:
        return {}


__all__ = [
    "EduScope",
    "apply_school_catalog",
    "build_edu_row_predicates",
    "clear_edu_scope_from_variables",
    "edu_school_display_label",
    "edu_scope_dict_for_user_id",
    "edu_scope_summary",
    "list_edu_roles",
    "list_edu_school_catalog",
    "load_edu_permission_config",
    "looks_like_school_token",
    "lookup_school_display_name",
    "match_edu_school",
    "merge_edu_scope_into_variables",
    "parse_edu_scope",
    "resolve_scope_school",
    "resolve_template_value",
    "validate_edu_scope",
]
