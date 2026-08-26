"""运行时行列权限：schema 裁剪与执行前 SQL 合并。"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Iterable, Optional

from sqlalchemy import and_
from sqlalchemy.orm import Session

from datasource.crud import crud_datasource
from datasource.models.datasource import CoreField, CoreTable
from datasource.models.permission import DsPermission, DsRule
from system.authz import bypasses_column_visibility, bypasses_data_row_column_scope
from system.models.user import SysUser

logger = logging.getLogger(__name__)

_FIELD_SAFE = re.compile(r"^[A-Za-z_\u4e00-\u9fff][A-Za-z0-9_\u4e00-\u9fff]*$")
_OPS = {"=", "!=", ">", "<", ">=", "<=", "like", "in"}
_SCHOOL_CODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_OVERVIEW_SQL_RE = re.compile(r"\btb_score_overview\b|\bscore_overview\b", re.I)
_INDICATOR_SQL_RE = re.compile(r"\btb_score_indicator\b|\bscore_indicator\b", re.I)
_IND_SID_EQ_RE = re.compile(
    r"(?P<col>(?:[A-Za-z_][\w]*\.)?(?:\"school_id\"|`school_id`|\bschool_id\b))\s*=\s*'(?P<val>[^']*)'",
    re.I,
)
_IND_SID_IN_RE = re.compile(
    r"(?P<col>(?:[A-Za-z_][\w]*\.)?(?:\"school_id\"|`school_id`|\bschool_id\b))\s+IN\s*\((?P<body>[^)]*)\)",
    re.I,
)
_XX_EQ_RE = re.compile(
    r"(?P<col>(?:[A-Za-z_][\w]*\.)?(?:\"xx\"|`xx`|\bxx\b))\s*=\s*'(?P<val>[^']*)'",
    re.I,
)
_XX_IN_RE = re.compile(
    r"(?P<col>(?:[A-Za-z_][\w]*\.)?(?:\"xx\"|`xx`|\bxx\b))\s+IN\s*\((?P<body>[^)]*)\)",
    re.I,
)
_SQL_LIT_RE = re.compile(r"'([^']*)'")


def _norm_user_id(raw: Any) -> Optional[int]:
    try:
        if raw is None:
            return None
        if isinstance(raw, bool):
            return None
        if isinstance(raw, int):
            return raw
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return None


def _column_permission_entry_is_hidden(item: Any) -> bool:
    """列权限为「拒绝列表」：显式 ``enable is True`` 表示该条为白名单可见；否则带 field 即视为隐藏。

    兼容仅配置 ``field_name``/``field_id`` 而省略 ``enable`` 的旧数据（与 data-rules 保存体一致）。"""
    if not isinstance(item, dict):
        return False
    if item.get("enable") is True:
        return False
    fn = item.get("field_name")
    fid = item.get("field_id")
    has_name = isinstance(fn, str) and bool(fn.strip())
    has_id = _norm_user_id(fid) is not None
    return bool(has_name or has_id)


def _active_permission_ids_for_user(session: Session, user_id: int, ds_id: int) -> set[int]:
    """命中规则组 ``user_list`` 后，收集关联的 ``DsPermission.id``。

    仅包含 ``rule.oid`` 与数据源 ``ds_id`` 所在工作空间一致之规则，避免跨空间
    规则误命中。
    """
    ds = crud_datasource.get_datasource_by_id(session, ds_id)
    if ds is None:
        return set()
    ds_oid = int(ds.oid)
    out: set[int] = set()
    for rule in session.query(DsRule).all():
        if getattr(rule, "enable", True) is False:
            continue
        rule_oid = int(getattr(rule, "oid", 1) or 1)
        if rule_oid != ds_oid:
            continue
        try:
            users = json.loads(rule.user_list or "[]")
        except json.JSONDecodeError:
            continue
        hit = False
        for u in users:
            if _norm_user_id(u) == user_id:
                hit = True
                break
        if not hit:
            continue
        try:
            pids = json.loads(rule.permission_list or "[]")
        except json.JSONDecodeError:
            continue
        for pid in pids:
            ni = _norm_user_id(pid)
            if ni is not None:
                out.add(ni)
    return out


def _load_expression_tree(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return {}
        try:
            v = json.loads(s)
            return v if isinstance(v, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _sql_literal(value: str, db_type: str) -> str:
    """把用户配置的值转成 SQL 字面量（仅用于行权限条件）。"""
    s = value if isinstance(value, str) else str(value)
    s = s.replace("'", "''")
    return "'" + s + "'"


def compile_row_expression_tree(expression_tree: dict[str, Any], table_name: str, db_type: str) -> Optional[str]:
    """将前端保存的 ``{relation, conditions:[{field,op,value}]}`` 编译为布尔 SQL 片段（表名限定列）。"""
    conds = expression_tree.get("conditions")
    if not isinstance(conds, list) or not conds:
        return None
    relation = (expression_tree.get("relation") or "and").lower()
    if relation not in ("and", "or"):
        relation = "and"
    q = "`" if db_type == "mysql" else '"'
    parts: list[str] = []
    for c in conds:
        if not isinstance(c, dict):
            continue
        field = (c.get("field") or "").strip()
        if not field or not _FIELD_SAFE.match(field):
            continue
        op = (c.get("op") or "=").strip().lower()
        if op not in _OPS:
            continue
        val = c.get("value")
        if val is None:
            val = ""
        qual_col = f"{q}{table_name}{q}.{q}{field}{q}"
        if op == "in":
            items: list[str] = []
            if isinstance(val, list):
                items = [str(v).strip() for v in val if str(v).strip()]
            elif isinstance(val, str) and val.strip():
                items = [p.strip() for p in val.replace("|", ",").split(",") if p.strip()]
            if not items:
                continue
            lits = ", ".join(_sql_literal(v, db_type) for v in items)
            parts.append(f"{qual_col} IN ({lits})")
        elif op == "like":
            lit = _sql_literal(str(val), db_type)
            parts.append(f"{qual_col} LIKE {lit}")
        else:
            lit = _sql_literal(str(val), db_type)
            parts.append(f"{qual_col} {op.upper()} {lit}")
    if not parts:
        return None
    joiner = f" {relation.upper()} "
    return "(" + joiner.join(parts) + ")"


def collect_row_predicate_sqls(
    session: Session,
    user_id: int,
    ds_id: int,
    table_names: Iterable[str],
    db_type: str,
) -> list[str]:
    """对给定物理表名集合，返回应 AND 到最终 SQL 的谓词列表（已带表前缀）。"""
    active = _active_permission_ids_for_user(session, user_id, ds_id)
    if not active:
        return []
    names = {n for n in table_names if n}
    if not names:
        return []
    names_lc = {str(n).lower() for n in names if n}
    if not names_lc:
        return []
    core_name_by_id = {
        int(t.id): t.table_name
        for t in session.query(CoreTable.id, CoreTable.table_name).filter(CoreTable.ds_id == ds_id).all()
    }
    perms = (
        session.query(DsPermission)
        .filter(
            DsPermission.id.in_(active),
            DsPermission.ds_id == ds_id,
            DsPermission.type == "row",
        )
        .all()
    )
    preds: list[str] = []
    for p in perms:
        tname = (p.table_name or "").strip() or core_name_by_id.get(int(p.table_id or 0), "")
        if not tname or tname.lower() not in names_lc:
            continue
        tree = _load_expression_tree(p.expression_tree)
        frag = compile_row_expression_tree(tree, tname, db_type)
        if frag:
            preds.append(frag)
    return preds


def apply_column_permissions_to_schema_tables(
    session: Session,
    user_id: int,
    ds_id: int,
    tables: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """按列权限从 schema 表结构中移除不可见字段（原地拷贝，不修改入参对象引用内的子结构可浅拷贝）。"""
    active = _active_permission_ids_for_user(session, user_id, ds_id)
    if not active:
        return tables
    core_tables = session.query(CoreTable).filter(CoreTable.ds_id == ds_id).all()
    name_to_table = {t.table_name: t for t in core_tables}
    core_name_by_id = {int(t.id): t.table_name for t in core_tables}
    table_ids = [t.id for t in core_tables]
    all_fields: list[CoreField] = []
    if table_ids:
        all_fields = (
            session.query(CoreField)
            .filter(and_(CoreField.ds_id == ds_id, CoreField.table_id.in_(table_ids)))
            .all()
        )
    fields_by_table: dict[int, list[CoreField]] = {}
    for f in all_fields:
        fields_by_table.setdefault(f.table_id, []).append(f)

    out_tables: list[dict[str, Any]] = []
    for t in tables:
        tcopy = dict(t)
        tname = tcopy.get("name") or ""
        ct = name_to_table.get(tname)
        if not ct:
            out_tables.append(tcopy)
            continue
        perms = [
            p
            for p in session.query(DsPermission)
            .filter(
                DsPermission.id.in_(active),
                DsPermission.ds_id == ds_id,
                DsPermission.type == "column",
            )
            .all()
            if (
                ((p.table_name or "").strip() or core_name_by_id.get(int(p.table_id or 0), "")).lower()
                == str(ct.table_name).lower()
            )
        ]
        if not perms:
            out_tables.append(tcopy)
            continue
        hidden_field_ids: set[int] = set()
        hidden_field_names: set[str] = set()
        for p in perms:
            try:
                plist = json.loads(p.permissions or "[]")
            except json.JSONDecodeError:
                continue
            if not isinstance(plist, list):
                continue
            for item in plist:
                if not isinstance(item, dict):
                    continue
                if not _column_permission_entry_is_hidden(item):
                    continue
                fid = _norm_user_id(item.get("field_id"))
                if fid is not None:
                    hidden_field_ids.add(fid)
                fname = item.get("field_name")
                if isinstance(fname, str) and fname.strip():
                    hidden_field_names.add(fname.strip().lower())
        if not hidden_field_ids and not hidden_field_names:
            out_tables.append(tcopy)
            continue
        id_by_name = {}
        for cf in fields_by_table.get(ct.id, []):
            id_by_name[cf.field_name] = cf.id
        new_fields = []
        for fld in tcopy.get("fields") or []:
            fname = fld.get("name")
            fn_key = str(fname).strip().lower() if fname is not None else ""
            if fn_key and fn_key in hidden_field_names:
                continue
            fid = id_by_name.get(fname)
            if fid is not None and fid in hidden_field_ids:
                continue
            new_fields.append(fld)
        tcopy["fields"] = new_fields
        out_tables.append(tcopy)
    return out_tables


def collect_hidden_column_names_for_logical_table(
    session: Session,
    user_id: int,
    ds_id: int,
    table_name: str,
) -> set[str]:
    """指定逻辑表上列权限配置为不可见(enable=False)的列名（小写，用于比对）。"""
    active = _active_permission_ids_for_user(session, user_id, ds_id)
    if not active:
        return set()
    tn = str(table_name or "").strip().lower()
    if not tn:
        return set()
    core_tables = session.query(CoreTable).filter(CoreTable.ds_id == ds_id).all()
    core_name_by_id = {int(t.id): t.table_name for t in core_tables}
    name_to_table = {str(t.table_name).strip().lower(): t for t in core_tables}
    ct = name_to_table.get(tn)
    if not ct:
        return set()
    perms = [
        p
        for p in session.query(DsPermission)
        .filter(
            DsPermission.id.in_(active),
            DsPermission.ds_id == ds_id,
            DsPermission.type == "column",
        )
        .all()
        if (
            ((p.table_name or "").strip() or core_name_by_id.get(int(p.table_id or 0), "")).lower()
            == str(ct.table_name).lower()
        )
    ]
    if not perms:
        return set()
    hidden_field_ids: set[int] = set()
    hidden_field_names: set[str] = set()
    for p in perms:
        try:
            plist = json.loads(p.permissions or "[]")
        except json.JSONDecodeError:
            continue
        if not isinstance(plist, list):
            continue
        for item in plist:
            if not isinstance(item, dict):
                continue
            if not _column_permission_entry_is_hidden(item):
                continue
            fid = _norm_user_id(item.get("field_id"))
            if fid is not None:
                hidden_field_ids.add(fid)
            fname = item.get("field_name")
            if isinstance(fname, str) and fname.strip():
                hidden_field_names.add(fname.strip().lower())
    out: set[str] = set(hidden_field_names)
    if hidden_field_ids:
        q = session.query(CoreField).filter(CoreField.table_id == ct.id, CoreField.id.in_(list(hidden_field_ids)))
        for cf in q.all():
            out.add(cf.field_name.strip().lower())
    return out


def merge_hidden_column_names_for_sql_tables(
    session: Session,
    user_id: int,
    ds_id: int,
    db_type: str,
    sql: str,
) -> set[str]:
    """合并当前 SQL 所引用各逻辑表上的隐藏列名（小写）。"""
    merged: set[str] = set()
    for t in tables_referenced_in_sql(sql, db_type):
        merged |= collect_hidden_column_names_for_logical_table(session, user_id, ds_id, t)
    return merged


def _fallback_text_column_hits(sql: str, forbidden_lower: set[str]) -> list[str]:
    """sqlglot 未识别列引用时的兜底：禁止列名是否出现在 SQL 文本中（大小写不敏感）。"""
    if not forbidden_lower or not sql.strip():
        return []
    compact = sql.lower()
    out: list[str] = []
    seen: set[str] = set()
    for low in forbidden_lower:
        if not low or low not in compact:
            continue
        if low in seen:
            continue
        seen.add(low)
        out.append(low)
    return out


def column_identifiers_referencing_forbidden(
    sql: str, db_type: str, forbidden_lower: set[str]
) -> list[str]:
    """从 SQL AST 中找出出现在列引用位置且命中 forbidden（小写集合）的列名（原始大小写各一次）。"""
    if not forbidden_lower or not sql.strip():
        return []
    try:
        from sqlglot import exp, parse_one
    except ImportError:
        return []
    dialect = "mysql" if db_type == "mysql" else "postgres"
    try:
        tree = parse_one(sql, dialect=dialect)
    except Exception:
        return []
    found: list[str] = []
    seen_lower: set[str] = set()
    for col in tree.find_all(exp.Column):
        raw = col.name
        if raw is None:
            continue
        disp = str(raw).strip('"').strip("`").strip()
        key = disp.lower()
        if key in forbidden_lower and key not in seen_lower:
            seen_lower.add(key)
            found.append(disp)
    return found


def validate_sql_column_permissions(
    session: Session,
    user: SysUser | None,
    ds_id: int,
    db_type: str,
    sql: str,
) -> Optional[str]:
    """若用户在 SQL 中引用对其隐藏的列，返回中文错误说明；否则 None。

    普通成员受列级隐藏约束；平台管理员与任一工作空间管理员豁免（见
    ``bypasses_column_visibility``）。行级合并仍仅平台管理员豁免（见
    ``bypasses_data_row_column_scope``）。"""
    if user is None or bypasses_column_visibility(session, user):
        return None
    forbidden = merge_hidden_column_names_for_sql_tables(session, user.id, ds_id, db_type, sql)
    if not forbidden:
        return None
    hits = column_identifiers_referencing_forbidden(sql, db_type, forbidden)
    if not hits:
        hits = _fallback_text_column_hits(sql, forbidden)
    if not hits:
        return None
    cols = "、".join(hits)
    return (
        f"列权限限制：SQL 中仍引用了对你隐藏的列：{cols}。"
        "请删除或改写这些列（仅使用 schema 中可见的字段），勿在表达式、ORDER BY、聚合参数中使用被隐藏的列名。"
    )


def filter_exec_result_by_column_permissions(
    session: Session,
    user: SysUser | None,
    ds_id: int,
    db_type: str,
    sql: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    """从查询结果中移除当前用户在该 SQL 涉及表上不可见的列（例如 SELECT * 时兜底）。"""
    if user is None or bypasses_column_visibility(session, user):
        return result
    if not isinstance(result, dict):
        return result
    cols = result.get("columns")
    rows = result.get("rows")
    if cols is None or rows is None:
        return result
    forbidden = merge_hidden_column_names_for_sql_tables(session, user.id, ds_id, db_type, sql)
    if not forbidden:
        return result
    col_list = list(cols)
    keep_idx = [i for i, c in enumerate(col_list) if str(c).strip().lower() not in forbidden]
    if len(keep_idx) == len(col_list):
        return result
    new_cols = [col_list[i] for i in keep_idx]
    new_rows: list[Any] = []
    for row in rows:
        if isinstance(row, dict):
            new_rows.append({k: v for k, v in row.items() if str(k).strip().lower() not in forbidden})
        elif isinstance(row, (list, tuple)):
            new_rows.append([row[i] for i in keep_idx])
        else:
            new_rows.append(row)
    out = dict(result)
    out["columns"] = new_cols
    out["rows"] = new_rows
    if "row_count" in out:
        out["row_count"] = len(new_rows)
    return out


_EDU_SCOPE_FIELDS = ("school_id", "class", "student_id")
_SCORE_TABLE_NAMES = frozenset({"tb_score", "score"})
_DETAIL_TABLE_NAMES = frozenset({"tb_score_detail", "score_detail"})
_STUDENT_TABLE_NAMES = frozenset({"tb_student", "student"})
_INDICATOR_TABLE_NAMES = frozenset({"tb_score_indicator", "score_indicator"})
_OVERVIEW_TABLE_NAMES = frozenset({"tb_score_overview", "score_overview"})
_FRACTION_BAR_TABLE_NAMES = frozenset({"tb_fraction_bar", "fraction_bar"})


def _table_alias_name(table: Any) -> str:
    from sqlglot import exp

    alias_node = table.args.get("alias")
    if alias_node is not None:
        inner = alias_node.this if hasattr(alias_node, "this") else alias_node
        if isinstance(inner, exp.Identifier):
            return inner.name
        return str(inner)
    return table.name or ""


def _find_table_aliases(sql: str, table_names: frozenset[str], db_type: str) -> list[str]:
    try:
        from sqlglot import exp, parse_one
    except ImportError:
        return []

    dialect = "mysql" if db_type == "mysql" else "postgres"
    try:
        tree = parse_one(sql, dialect=dialect)
    except Exception:
        return []

    aliases: list[str] = []
    for table in tree.find_all(exp.Table):
        name = (table.name or "").strip().lower()
        alias = _table_alias_name(table)
        if name in table_names and alias:
            aliases.append(alias)
    return aliases


def _is_school_code_literal(value: str) -> bool:
    n = (value or "").strip()
    if not n or any("\u4e00" <= ch <= "\u9fff" for ch in n):
        return False
    return bool(_SCHOOL_CODE_RE.fullmatch(n))


def expand_overview_xx_school_code_literals(sql: str, db_type: str = "pg") -> str:
    """overview.xx 是学校明文：把 xx='GZ_…' 扩成同时匹配 tb_school.s_name。"""
    text = strip_city_scope_xx_filters(sql or "", db_type)
    if not _OVERVIEW_SQL_RE.search(text):
        return text
    q = "`" if db_type == "mysql" else '"'

    def _sname_subq(body: str) -> str:
        return (
            f"SELECT COALESCE({q}s_name{q}, {q}name{q}) FROM tb_school "
            f"WHERE {q}id{q} IN ({body}) OR {q}name{q} IN ({body})"
        )

    def _repl_in(m: re.Match[str]) -> str:
        body = m.group("body")
        if re.search(r"\bSELECT\b", body, re.I):
            return m.group(0)
        lits = _SQL_LIT_RE.findall(body)
        if not lits or not all(_is_school_code_literal(v) for v in lits):
            return m.group(0)
        col = m.group("col")
        return f"({col} IN ({body}) OR {col} IN ({_sname_subq(body)}))"

    def _repl_eq(m: re.Match[str]) -> str:
        val = m.group("val")
        if not _is_school_code_literal(val):
            return m.group(0)
        col = m.group("col")
        lit = "'" + val.replace("'", "''") + "'"
        return f"({col} = {lit} OR {col} IN ({_sname_subq(lit)}))"

    text = _XX_IN_RE.sub(_repl_in, text)
    return _XX_EQ_RE.sub(_repl_eq, text)


def expand_indicator_school_code_literals(sql: str, db_type: str = "pg") -> str:
    """indicator 的 school_id/school_name 存学校明文：把 school_id='GZ_…' 扩成匹配 tb_school.s_name。"""
    text = sql or ""
    if not _INDICATOR_SQL_RE.search(text):
        return text
    if _find_table_aliases(text, _SCORE_TABLE_NAMES, db_type):
        return text
    if _find_table_aliases(text, _OVERVIEW_TABLE_NAMES, db_type):
        return text
    q = "`" if db_type == "mysql" else '"'

    def _sname_subq(body: str) -> str:
        return (
            f"SELECT COALESCE({q}s_name{q}, {q}name{q}) FROM tb_school "
            f"WHERE {q}id{q} IN ({body}) OR {q}name{q} IN ({body})"
        )

    def _name_col(col: str) -> str:
        return re.sub(r"school_id", "school_name", col, count=1, flags=re.I)

    def _repl_in(m: re.Match[str]) -> str:
        body = m.group("body")
        if re.search(r"\bSELECT\b", body, re.I):
            return m.group(0)
        lits = _SQL_LIT_RE.findall(body)
        if not lits or not all(_is_school_code_literal(v) for v in lits):
            return m.group(0)
        col = m.group("col")
        subq = _sname_subq(body)
        return f"({col} IN ({subq}) OR {_name_col(col)} IN ({subq}))"

    def _repl_eq(m: re.Match[str]) -> str:
        val = m.group("val")
        if not _is_school_code_literal(val):
            return m.group(0)
        col = m.group("col")
        lit = "'" + val.replace("'", "''") + "'"
        subq = _sname_subq(lit)
        return f"({col} IN ({subq}) OR {_name_col(col)} IN ({subq}))"

    text = _IND_SID_IN_RE.sub(_repl_in, text)
    return _IND_SID_EQ_RE.sub(_repl_eq, text)


_CITY_SCOPE_LABELS = frozenset({"全市", "市均", "city"})
_IDENTITY_COLS = frozenset({"xm", "xh", "ksh", "sfzh", "anon_stu_id", "student_id"})
_SCHOOL_GRAIN_COLS = frozenset({"xx", "school_id", "school_name"})
_CLASS_GRAIN_COLS = frozenset({"bj", "class"})
_METRIC_DIM_COLS = frozenset({
    "dq", "district", "exam_name", "xkkm", "xsxz", "xxlb", "line_name",
})
_METRIC_TABLE_NAMES = (
    _OVERVIEW_TABLE_NAMES | _SCORE_TABLE_NAMES | _INDICATOR_TABLE_NAMES
)


def _sql_dialect(db_type: str) -> str:
    return "mysql" if db_type == "mysql" else "postgres"


def _unwrap_alias(node: Any) -> Any:
    from sqlglot import exp

    return node.this if isinstance(node, exp.Alias) else node


def _projection_is_const_or_agg(node: Any) -> bool:
    """投影是字面量或聚合（含 ROUND/CAST 包裹），不含学生明文列。"""
    from sqlglot import exp

    node = _unwrap_alias(node)
    if node is None:
        return False
    if isinstance(node, exp.Literal):
        return True
    if isinstance(node, exp.AggFunc):
        return True
    if isinstance(node, (exp.Paren, exp.Cast, exp.Round)):
        return _projection_is_const_or_agg(node.this)
    if isinstance(node, (exp.Anonymous, exp.Func, exp.Coalesce)):
        args = [
            c
            for c in node.iter_expressions()
            if not isinstance(c, exp.DataType)
        ]
        return bool(args) and all(_projection_is_const_or_agg(c) for c in args)
    return False


def _select_uses_metric_table(select: Any) -> bool:
    from sqlglot import exp

    for table in select.find_all(exp.Table):
        name = (table.name or "").strip().lower()
        if name in _METRIC_TABLE_NAMES:
            return True
    return False


def _group_by_column_names(select: Any) -> set[str]:
    from sqlglot import exp

    group = select.args.get("group")
    if group is None:
        return set()
    names: set[str] = set()
    exprs = group.expressions if hasattr(group, "expressions") else []
    for item in exprs or []:
        node = _unwrap_alias(item)
        if isinstance(node, exp.Column):
            n = (node.name or "").strip().lower()
            if n:
                names.add(n)
    return names


def _bare_projection_columns(select: Any) -> set[str]:
    """非聚合投影里的列名；无法识别的投影记为 __other__。"""
    from sqlglot import exp

    names: set[str] = set()
    for e in select.expressions or []:
        if _projection_is_const_or_agg(e):
            continue
        inner = _unwrap_alias(e)
        if isinstance(inner, exp.Column):
            n = (inner.name or "").strip().lower()
            names.add(n or "__other__")
        else:
            names.add("__other__")
    return names


def _select_has_aggregate(select: Any) -> bool:
    from sqlglot import exp

    return any(e.find(exp.AggFunc) for e in (select.expressions or []))


def _is_citywide_metric_select(select: Any) -> bool:
    """全市/区县合计：无学生列、无按校/按班粒度。点名学校或 GROUP BY xx 不算。"""
    from sqlglot import exp

    if not _select_uses_metric_table(select):
        return False
    exprs = list(select.expressions or [])
    if not exprs or not _select_has_aggregate(select):
        return False
    if any(isinstance(_unwrap_alias(e), exp.Star) for e in exprs):
        return False
    top_cols = _bare_projection_columns(select)
    if "__other__" in top_cols:
        return False
    blocked = _IDENTITY_COLS | _SCHOOL_GRAIN_COLS | _CLASS_GRAIN_COLS
    if top_cols & blocked:
        return False
    if top_cols - _METRIC_DIM_COLS:
        return False
    gcols = _group_by_column_names(select)
    if gcols & blocked:
        return False
    if gcols - _METRIC_DIM_COLS:
        return False
    where_cols = _where_column_names(select)
    if where_cols & _SCHOOL_GRAIN_COLS:
        return False
    return True


def _where_column_names(select: Any) -> set[str]:
    from sqlglot import exp

    where = select.args.get("where")
    if where is None:
        return set()
    names: set[str] = set()
    for col in where.find_all(exp.Column):
        n = (col.name or "").strip().lower()
        if n:
            names.add(n)
    return names


def _scope_literal(select: Any) -> str:
    from sqlglot import exp

    exprs = list(select.expressions or [])
    if not exprs:
        return ""
    first = _unwrap_alias(exprs[0])
    if isinstance(first, exp.Literal):
        return str(first.this or "")
    return ""


def _is_xx_predicate(node: Any) -> bool:
    from sqlglot import exp

    if isinstance(node, exp.Paren):
        return _is_xx_predicate(node.this)
    if isinstance(node, (exp.EQ, exp.NEQ, exp.Like, exp.ILike, exp.In)):
        left = node.this
        return isinstance(left, exp.Column) and (left.name or "").lower() == "xx"
    return False


def _drop_xx_clauses(node: Any) -> Any:
    from sqlglot import exp

    if node is None:
        return None
    if isinstance(node, exp.Paren):
        inner = _drop_xx_clauses(node.this)
        return None if inner is None else exp.Paren(this=inner)
    if isinstance(node, (exp.And, exp.Or)):
        left = _drop_xx_clauses(node.this)
        right = _drop_xx_clauses(node.expression)
        if left is None:
            return right
        if right is None:
            return left
        return node.__class__(this=left, expression=right)
    if _is_xx_predicate(node):
        return None
    return node


def _strip_xx_from_select(select: Any) -> Any:
    where = select.args.get("where")
    if where is None:
        return select
    new_cond = _drop_xx_clauses(where.this)
    if new_cond is None:
        select.set("where", None)
    else:
        where.set("this", new_cond)
    return select


def _walk_selects(tree: Any, fn: Any) -> Any:
    from sqlglot import exp

    if isinstance(tree, (exp.Limit, exp.Order, exp.Offset)):
        tree.set("this", _walk_selects(tree.this, fn))
        return tree
    if isinstance(tree, exp.Union):
        tree.set("this", _walk_selects(tree.this, fn))
        tree.set("expression", _walk_selects(tree.expression, fn))
        return tree
    if isinstance(tree, exp.Subquery):
        tree.set("this", _walk_selects(tree.this, fn))
        return tree
    if isinstance(tree, exp.Select):
        return fn(tree)
    return tree


def strip_city_scope_xx_filters(sql: str, db_type: str = "pg") -> str:
    """UNION 中 scope='全市' 的分支去掉 xx 条件，避免全市均分被收成该校。"""
    text = sql or ""
    if not _OVERVIEW_SQL_RE.search(text):
        return text
    if not re.search(r"\bUNION\b", text, re.I):
        return text
    try:
        from sqlglot import parse_one
    except ImportError:
        return text
    dialect = _sql_dialect(db_type)
    try:
        tree = parse_one(text, dialect=dialect)
    except Exception:
        return text

    def _on_select(select: Any) -> Any:
        if _scope_literal(select) in _CITY_SCOPE_LABELS:
            return _strip_xx_from_select(select)
        return select

    new_tree = _walk_selects(tree, _on_select)
    try:
        return new_tree.sql(dialect=dialect)
    except Exception:
        return text


def _pred_targets_overview_school(pred: str) -> bool:
    return bool(re.search(r'\bxx\b|"xx"|`xx`|school_id|school_name', pred, re.I))


def _pred_targets_overview_class(pred: str) -> bool:
    return bool(re.search(r'\bbj\b|"bj"|`bj`|"class"|`class`', pred, re.I))


def _pred_targets_overview_student(pred: str) -> bool:
    return bool(re.search(r"anon_stu_id|student_id", pred, re.I))


def _preds_for_overview_select(select: Any, predicates: list[str]) -> list[str]:
    """全市/区县指标不注入本校/本班；明细与点名他校仍注入。"""
    if not _is_citywide_metric_select(select):
        return predicates
    out: list[str] = []
    for p in predicates:
        if _pred_targets_overview_school(p):
            continue
        if _pred_targets_overview_class(p):
            continue
        if _pred_targets_overview_student(p):
            continue
        out.append(p)
    return out


def _combine_predicate_tree(predicates: list[str], dialect: str) -> Any:
    from sqlglot import exp, parse_one

    combined: Optional[Any] = None
    for p in predicates:
        try:
            frag = parse_one(p, dialect=dialect)
        except Exception:
            continue
        combined = frag if combined is None else exp.And(this=combined, expression=frag)
    return combined


def _edu_scope_column(
    field: str,
    *,
    score_aliases: list[str],
    detail_aliases: list[str],
    student_aliases: list[str],
    indicator_aliases: list[str],
    overview_aliases: list[str],
    db_type: str,
) -> Optional[str]:
    """教育权限列映射：school_id/class 优先 tb_score；总览表为 xx/bj/anon_stu_id。

    tb_score_indicator 挂 school_id（无明文校名时）或 school_name；class/student_id 不能挂在该表。
    """
    q = "`" if db_type == "mysql" else '"'
    if field == "school_id":
        if indicator_aliases:
            return f"{indicator_aliases[0]}.{q}school_id{q}"
        if score_aliases:
            return f"{score_aliases[0]}.{q}school_id{q}"
        if overview_aliases:
            return f"{overview_aliases[0]}.{q}xx{q}"
        return None
    if field == "class":
        if score_aliases:
            return f"{score_aliases[0]}.{q}class{q}"
        if overview_aliases:
            return f"{overview_aliases[0]}.{q}bj{q}"
        return None
    if field == "student_id":
        if score_aliases:
            return f"{score_aliases[0]}.{q}student_id{q}"
        if detail_aliases:
            return f"{detail_aliases[0]}.{q}student_id{q}"
        if student_aliases:
            return f"{student_aliases[0]}.{q}id{q}"
        if overview_aliases:
            return f"{overview_aliases[0]}.{q}anon_stu_id{q}"
        return None
    return None


def _exists_score_guard(sd_alias: str, qualified_inner: str, db_type: str) -> str:
    """仅 FROM tb_score_detail 时，经 EXISTS 关联 tb_score 施加 school_id/class 谓词。"""
    q = "`" if db_type == "mysql" else '"'
    sc = "sc"
    return (
        f"EXISTS (SELECT 1 FROM tb_score {sc} "
        f"WHERE {sc}.{q}exam_id{q} = {sd_alias}.{q}exam_id{q} "
        f"AND {sc}.{q}student_id{q} = {sd_alias}.{q}student_id{q} "
        f"AND {qualified_inner})"
    )


def qualify_edu_row_predicates(
    sql: str,
    predicates: list[str],
    db_type: str,
    school_name: str = "",
) -> list[str]:
    """为教育权限谓词补全正确表别名，避免 sd.school_id / st.student_id 等错误引用。

    若 SQL 未涉及成绩事实表（如只查 tb_exam / tb_fraction_bar），无法安全挂
    school_id/class：丢弃该谓词，避免把裸列注入维表导致 ``column "class" does not exist``。
    tb_score_overview 用 xx/bj/anon_stu_id；tb_score_indicator 有明文校名时挂
    school_name LIKE，否则挂 school_id。
    """
    if not predicates:
        return predicates

    score_aliases = _find_table_aliases(sql, _SCORE_TABLE_NAMES, db_type)
    detail_aliases = _find_table_aliases(sql, _DETAIL_TABLE_NAMES, db_type)
    student_aliases = _find_table_aliases(sql, _STUDENT_TABLE_NAMES, db_type)
    indicator_aliases = _find_table_aliases(sql, _INDICATOR_TABLE_NAMES, db_type)
    overview_aliases = _find_table_aliases(sql, _OVERVIEW_TABLE_NAMES, db_type)
    fraction_aliases = _find_table_aliases(sql, _FRACTION_BAR_TABLE_NAMES, db_type)
    q = "`" if db_type == "mysql" else '"'
    out: list[str] = []
    fact_aliases = score_aliases or detail_aliases or student_aliases or overview_aliases
    indicator_only = bool(indicator_aliases) and not fact_aliases
    fraction_only = bool(fraction_aliases) and not fact_aliases and not indicator_aliases

    for pred in predicates:
        new_pred = pred
        drop = False
        for field in _EDU_SCOPE_FIELDS:
            if field not in pred:
                continue
            if fraction_only:
                drop = True
                break
            if indicator_only and field == "student_id":
                new_pred = "1 = 0"
                break
            if indicator_only and field == "class":
                drop = True
                continue
            name = (school_name or "").strip()
            if indicator_only and field == "school_id" and name:
                alias = indicator_aliases[0]
                lit = name.replace("'", "''")
                new_pred = f"{alias}.{q}school_name{q} LIKE '%{lit}%'"
                break
            col = _edu_scope_column(
                field,
                score_aliases=score_aliases,
                detail_aliases=detail_aliases,
                student_aliases=student_aliases,
                indicator_aliases=indicator_aliases,
                overview_aliases=overview_aliases,
                db_type=db_type,
            )
            pattern = rf'(?<!\.){re.escape(q)}{field}{re.escape(q)}'
            if col:
                new_pred = re.sub(pattern, col, new_pred)
            elif field in ("school_id", "class") and detail_aliases and not score_aliases:
                inner = re.sub(pattern, f"sc.{q}{field}{q}", pred)
                new_pred = _exists_score_guard(detail_aliases[0], inner, db_type)
                break
            else:
                # 无成绩表可挂载：丢弃，勿保留裸 "class"/"school_id"
                drop = True
                break
        if not drop:
            out.append(new_pred)
    return out


def merge_row_predicates_into_sql(sql: str, db_type: str, predicates: list[str]) -> str:
    """将谓词并入各 SELECT 叶子。UNION 按支合并，避免全市支被本校 xx 收成该校。"""
    if not predicates or not sql.strip():
        return sql
    try:
        from sqlglot import parse_one
    except ImportError:
        logger.warning("sqlglot missing, skip row permission merge")
        return sql

    dialect = _sql_dialect(db_type)
    try:
        tree = parse_one(sql, dialect=dialect)
    except Exception as e:
        logger.warning("row merge parse failed: %s", e)
        return sql

    def _on_select(select: Any) -> Any:
        leaf_preds = _preds_for_overview_select(select, predicates)
        combined = _combine_predicate_tree(leaf_preds, dialect)
        if combined is None:
            return select
        return select.where(combined, append=True)

    try:
        new_tree = _walk_selects(tree, _on_select)
        return new_tree.sql(dialect=dialect)
    except Exception as e:
        logger.warning("row merge apply failed: %s", e)
        return sql


def tables_referenced_in_sql(sql: str, db_type: str) -> list[str]:
    """从 SQL 中抽取简单表名（不含 schema 前缀的裸名；用于行权限匹配）。"""
    try:
        from sqlglot import exp, parse_one
    except ImportError:
        return []

    dialect = "mysql" if db_type == "mysql" else "postgres"
    try:
        tree = parse_one(sql, dialect=dialect)
    except Exception:
        return []

    names: set[str] = set()

    def _name_from_table(t: exp.Expression) -> None:
        if isinstance(t, exp.Table):
            n = t.name
            if n:
                names.add(n)

    for t in tree.find_all(exp.Table):
        _name_from_table(t)
    return sorted(names)


def apply_permissions_for_execute(
    session: Session,
    user: SysUser | None,
    ds_id: int,
    db_type: str,
    sql: str,
    tables_hint: Optional[list[str]] = None,
) -> str:
    """执行前合并行权限。显式引用被隐藏列的 SQL 由 ``validate_sql_column_permissions`` 拦截；``SELECT *`` 等由 ``filter_exec_result_by_column_permissions`` 兜底裁剪结果列。"""
    sql = strip_city_scope_xx_filters(sql, db_type)
    if user is None or bypasses_data_row_column_scope(user):
        sql = expand_overview_xx_school_code_literals(sql, db_type)
        return expand_indicator_school_code_literals(sql, db_type)
    names = list(tables_hint or []) or tables_referenced_in_sql(sql, db_type)
    preds = collect_row_predicate_sqls(session, user.id, ds_id, names, db_type)
    from datasource.service.edu_permission import build_edu_row_predicates, parse_edu_scope

    scope_name = parse_edu_scope(user).school_name
    edu_preds = qualify_edu_row_predicates(
        sql,
        build_edu_row_predicates(user, db_type),
        db_type,
        school_name=scope_name,
    )
    preds.extend(edu_preds)
    merged = merge_row_predicates_into_sql(sql, db_type, preds) if preds else sql
    merged = expand_overview_xx_school_code_literals(merged, db_type)
    return expand_indicator_school_code_literals(merged, db_type)


def schema_tables_for_user(
    session: Session,
    user: SysUser | None,
    ds_id: int,
    raw_tables: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """对 ``get_schema_info`` 结果做列裁剪；普通成员裁剪，平台/空间管理员豁免。"""
    if user is None or bypasses_column_visibility(session, user):
        return raw_tables
    return apply_column_permissions_to_schema_tables(session, user.id, ds_id, raw_tables)
