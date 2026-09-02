"""问句越权快拒：点名了权限范围外的班/校时，在 Planner / 工具之前拒绝。

不改 SQL 行权限。抽不出班名/校名时放行（交给后续 WHERE 注入）。
「全校 / 全市」等对比口径不当成外班/外校。
账号只有 school_id、没有中文校名时，用 tb_school.s_name 对齐后再判。
"""

from __future__ import annotations

import re
from typing import Any

from src.agent.education.query_parse import (
    extract_class_targets,
    extract_school_targets,
    normalize_fullwidth_parentheses,
)

OUT_OF_SCOPE_MESSAGE = "当前数据无权限访问"

_SCHOOL_SCOPED_ROLES = frozenset({"teacher", "school_admin"})
_SCHOOL_CODE_RE = re.compile(r"^[A-Za-z]\d{2}")
_SCHOOL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_YIZHONG_RE = re.compile(r"([\u4e00-\u9fff]{2,12}一中)")
_YIZHONG_TAIL_RE = re.compile(
    r"([\u4e00-\u9fff]{2,4}(?:市|区|县)[\u4e00-\u9fff]{0,4}一中)$"
)
_SPLIT_RE = re.compile(r"[和与跟及对]")
_COMPOUND_SPLIT_RE = re.compile(r"[和与跟及]")
_VERB_PREFIXES = (
    "帮我分析",
    "帮我",
    "分析",
    "查询",
    "统计",
    "查看",
    "了解",
    "生成",
    "本校",
    "我校",
)
_SCOPE_LABELS = frozenset(
    {
        "全校",
        "本校",
        "我校",
        "全市",
        "市均",
        "各校",
        "年级",
        "各班",
        "全年级",
        "本班",
        "本班级",
        "全域",
        "市域",
        "全区县",
        "各区县",
        "全区",
        "全县",
        "各区",
        "各县",
    }
)


def out_of_scope_question(
    edu_scope: dict[str, Any] | None,
    question: str,
    *,
    school_labels: list[str] | None = None,
    datasource_id: int | None = None,
    workspace_oid: int | None = None,
) -> str | None:
    """点名越权班/校时返回拒绝文案，否则 None。"""
    edu = edu_scope if isinstance(edu_scope, dict) else {}
    role = str(edu.get("edu_role") or "").strip()
    if role not in _SCHOOL_SCOPED_ROLES:
        return None
    q = (question or "").strip()
    if not q:
        return None
    labels = [str(x).strip() for x in (school_labels or []) if str(x).strip()]
    bound_name = str(edu.get("school_name") or "").strip()
    bound_id = str(edu.get("school_id") or "").strip()
    asked_schools = _asked_school_names(q)
    if (
        not labels
        and bound_id
        and (not bound_name or _looks_like_school_id(bound_name))
        and asked_schools
    ):
        labels = lookup_bound_school_labels(
            bound_id, datasource_id=datasource_id, workspace_oid=workspace_oid
        )
    if role == "teacher" and _class_out_of_scope(edu, q):
        return OUT_OF_SCOPE_MESSAGE
    if _school_out_of_scope(edu, q, extra_labels=labels, asked_schools=asked_schools):
        return OUT_OF_SCOPE_MESSAGE
    return None


def out_of_scope_named(
    edu_scope: dict[str, Any] | None,
    *parts: Any,
    school_labels: list[str] | None = None,
    datasource_id: int | None = None,
    workspace_oid: int | None = None,
) -> str | None:
    """把分析工具 filters / 班级列表拼成文本再校验。"""
    q = " ".join(str(p).strip() for p in parts if p is not None and str(p).strip())
    return out_of_scope_question(
        edu_scope,
        q,
        school_labels=school_labels,
        datasource_id=datasource_id,
        workspace_oid=workspace_oid,
    )


def lookup_bound_school_labels(
    school_id: str,
    *,
    datasource_id: int | None = None,
    workspace_oid: int | None = None,
) -> list[str]:
    """用 tb_school 把权限校 ID 还原成展示名（如 A01扬州中学）。失败返回空。"""
    sid = str(school_id or "").strip()
    if not sid or not _SCHOOL_ID_RE.fullmatch(sid) or not datasource_id:
        return []
    try:
        from src.agent.resource.tool.business import _load_datasource
        from src.datasource.db.db import execute_sql

        db_type, config, _name = _load_datasource(int(datasource_id), workspace_oid)
        lit = sid.replace("'", "''")
        ok, _msg, result = execute_sql(
            db_type,
            config,
            "SELECT id, name, s_name FROM tb_school "
            f"WHERE id = '{lit}' OR name = '{lit}' LIMIT 5",
        )
        if not ok or not isinstance(result, dict):
            return []
        labels: list[str] = []
        for row in result.get("rows") or []:
            vals = _row_values(row)
            for raw in vals:
                tok = str(raw or "").strip()
                if tok and tok not in labels and not _looks_like_school_id(tok):
                    labels.append(tok)
                elif tok and _SCHOOL_CODE_RE.match(tok) and tok not in labels:
                    labels.append(tok)
        return labels
    except Exception:
        return []


def _row_values(row: Any) -> list[Any]:
    if isinstance(row, dict):
        return [row.get("id"), row.get("name"), row.get("s_name")]
    if isinstance(row, (list, tuple)):
        return list(row)
    return []


def _looks_like_school_id(value: str) -> bool:
    tok = str(value or "").strip()
    if not tok or re.search(r"[\u4e00-\u9fff]", tok):
        return False
    return bool(_SCHOOL_ID_RE.fullmatch(tok))


def _class_out_of_scope(edu: dict[str, Any], question: str) -> bool:
    raw = edu.get("class_names")
    allowed = [str(x).strip() for x in raw] if isinstance(raw, list) else []
    allowed = [x for x in allowed if x]
    if not allowed:
        return False
    for asked in extract_class_targets(question):
        if asked in _SCOPE_LABELS:
            continue
        if not _class_in_scope(asked, allowed):
            return True
    return False


def _asked_school_names(question: str) -> list[str]:
    """问句里点名的学校：中学/学校后缀 + 「扬州市一中」这类一中简称。"""
    seen: list[str] = []
    for tok in extract_school_targets(question):
        for part in _expand_compound_school(tok):
            if part and part not in seen and part not in _SCOPE_LABELS:
                seen.append(part)
    blob = re.sub(r"\s+", "", question or "")
    for m in _YIZHONG_RE.finditer(blob):
        tok = _clean_yizhong(m.group(1))
        if tok and tok not in seen and tok not in _SCOPE_LABELS:
            seen.append(tok)
    return seen


def _expand_compound_school(token: str) -> list[str]:
    """「新华中学和扬州中学」这类并列校名拆开，避免整段被子串当成本校。"""
    t = (token or "").strip()
    if not t:
        return []
    parts = [p.strip() for p in _COMPOUND_SPLIT_RE.split(t) if p.strip()]
    return parts if len(parts) > 1 else [t]


def _clean_yizhong(token: str) -> str | None:
    t = _SPLIT_RE.split(token or "")[-1]
    for prefix in _VERB_PREFIXES:
        if t.startswith(prefix):
            t = t[len(prefix):]
    m = _YIZHONG_TAIL_RE.search(t)
    if m:
        return m.group(1)
    if t.endswith("一中") and 4 <= len(t) <= 8:
        return t
    return None


def _school_out_of_scope(
    edu: dict[str, Any],
    question: str,
    *,
    extra_labels: list[str] | None = None,
    asked_schools: list[str] | None = None,
) -> bool:
    bound_name = str(edu.get("school_name") or "").strip()
    bound_id = str(edu.get("school_id") or "").strip()
    labels: list[str] = []
    if bound_name:
        labels.append(bound_name)
    for lab in extra_labels or []:
        tok = str(lab or "").strip()
        if tok and tok not in labels:
            labels.append(tok)
    if not labels and not bound_id:
        return False
    bound_aliases: set[str] = set()
    for lab in labels:
        bound_aliases |= _school_aliases(lab)
    if bound_id:
        bound_aliases.add(_norm_school(bound_id))
    bound_aliases.discard("")
    names = asked_schools if asked_schools is not None else _asked_school_names(question)
    for asked in names:
        if asked in _SCOPE_LABELS:
            continue
        if not _school_matches_bound(asked, bound_aliases):
            return True
    return False


def _school_matches_bound(asked: str, bound_aliases: set[str]) -> bool:
    asked_set = _school_aliases(asked)
    if asked_set & bound_aliases:
        return True
    for a in asked_set:
        for b in bound_aliases:
            if a and b and min(len(a), len(b)) >= 4 and (a in b or b in a):
                return True
    return False


def _norm_class(name: str) -> str:
    text = normalize_fullwidth_parentheses(name or "")
    text = re.sub(r"\s+", "", text)
    return text.replace("(", "").replace(")", "")


def _class_in_scope(asked: str, allowed: list[str]) -> bool:
    a = _norm_class(asked)
    if not a:
        return True
    norms = [_norm_class(x) for x in allowed]
    if a in norms:
        return True
    for n in norms:
        if n and (a in n or n in a) and min(len(a), len(n)) >= 4:
            return True
    return False


def _norm_school(name: str) -> str:
    return re.sub(r"\s+", "", str(name or ""))


def _school_aliases(name: str) -> set[str]:
    n = _norm_school(name)
    if not n:
        return set()
    out = {n}
    if _SCHOOL_CODE_RE.match(n) and len(n) > 3:
        out.add(n[3:])
        n = n[3:]
    if n.endswith("第一中学") and len(n) > 4:
        out.add(n[:-4] + "一中")
    elif n.endswith("一中") and len(n) > 2:
        out.add(n[:-2] + "第一中学")
    return {x for x in out if x}


__all__ = [
    "OUT_OF_SCOPE_MESSAGE",
    "lookup_bound_school_labels",
    "out_of_scope_named",
    "out_of_scope_question",
]
