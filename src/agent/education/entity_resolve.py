"""值链接：把问句里的考试/校/班/区县对齐到 peek 目录真值。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Mapping

from src.agent.education.clarification import (
    SLOT_CLASS,
    SLOT_EXAM,
    SLOT_SCHOOL,
)
from src.agent.education.query_frame import EntitySource, EntityStatus, QueryFrame
from src.agent.education.query_parse import (
    _is_exam_window_fragment,
    extract_district_target,
    is_interrogative_school_name,
    normalize_fullwidth_parentheses,
    split_conjoined_school_names,
)

_SCHOOL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_SCHOOL_CODE_PREFIX_RE = re.compile(r"^[A-Za-z]\d{2}")
_SCHOOL_LINK_TAILS = (
    "高级中学",
    "实验中学",
    "附属中学",
    "中学",
    "学校",
    "学院",
    "大学",
    "附中",
    "分校",
)
_SCHOOL_BOUND_ROLES = frozenset({"teacher", "school_admin", "student"})


def _looks_like_school_id(value: str) -> bool:
    tok = str(value or "").strip()
    if not tok or re.search(r"[\u4e00-\u9fff]", tok):
        return False
    return bool(_SCHOOL_ID_RE.fullmatch(tok))


def _is_permission_school_id(hint: str, edu: Mapping[str, Any]) -> bool:
    """权限已绑的 school_id，不是要拿去对中文校名目录的名字。"""
    if not _looks_like_school_id(hint):
        return False
    role = str(edu.get("edu_role") or "").strip()
    perm_id = str(edu.get("school_id") or "").strip()
    if role not in _SCHOOL_BOUND_ROLES or not perm_id:
        return False
    return hint == perm_id


@dataclass
class EntityResolveResult:
    """0 个 → clarify；1 个 → bound；多个 → clarify 带 options。"""

    bound: dict[str, str] = field(default_factory=dict)
    clarify_slot: str | None = None
    options: list[str] = field(default_factory=list)
    reason: str = ""
    frame: QueryFrame | None = None


def normalize_class_name(name: str) -> str:
    """高三2班 / 全角括号 / 补零 → 高三(2)班。"""
    text = normalize_fullwidth_parentheses(str(name or "").strip())
    text = re.sub(r"\((\d{2,})\)班", lambda m: f"({int(m.group(1))})班", text)
    return text


def _strip_month_district(name: str) -> str:
    n = str(name or "").strip()
    return re.sub(r"^月", "", n)


def link_values(hint: str, candidates: list[str]) -> list[str]:
    """精确 → 包含 → 规范化后相等。"""
    want = str(hint or "").strip()
    pool = [str(c).strip() for c in candidates if str(c).strip()]
    if not want or not pool:
        return []
    exact = [c for c in pool if c == want]
    if exact:
        return exact
    want_cls = normalize_class_name(want)
    class_hits = [c for c in pool if normalize_class_name(c) == want_cls]
    if class_hits:
        return class_hits
    contains = [c for c in pool if want in c or c in want]
    if contains:
        return contains
    want_n = _strip_month_district(want)
    if want_n != want:
        return link_values(want_n, pool)
    return []


def _school_link_stem(name: str) -> str:
    """宝楠中学 / B05宝楠学校 → 宝楠。只剥校码和校名后缀，不按城市名模糊。"""
    n = re.sub(r"\s+", "", str(name or ""))
    n = _SCHOOL_CODE_PREFIX_RE.sub("", n)
    for tail in _SCHOOL_LINK_TAILS:
        if n.endswith(tail) and len(n) > len(tail):
            return n[: -len(tail)]
    return n


def link_school_values(hint: str, candidates: list[str]) -> list[str]:
    """校名对齐：先走通用链接，再按「中学/学校」等同词干补一次。"""
    hits = link_values(hint, candidates)
    if hits:
        return hits
    want = _school_link_stem(hint)
    if len(want) < 2:
        return []
    return [c for c in candidates if str(c).strip() and _school_link_stem(c) == want]


def link_multi_school_values(hint: str, candidates: list[str]) -> list[str]:
    """「A中学和B中学」分别对齐目录。对不上或不足两所时返回空。"""
    want = str(hint or "").strip()
    pool = [str(c).strip() for c in candidates if str(c).strip()]
    if not want or not pool:
        return []
    parts = split_conjoined_school_names(want)
    if len(parts) < 2:
        contained = [c for c in pool if c != want and c in want]
        return contained if len(contained) >= 2 else []
    hits: list[str] = []
    for part in parts:
        linked = link_school_values(part, pool)
        if len(linked) != 1:
            return []
        if linked[0] not in hits:
            hits.append(linked[0])
    return hits if len(hits) >= 2 else []


def _similar_candidates(hint: str, candidates: list[str], *, limit: int = 12) -> list[str]:
    """零命中时只给相近候选，避免把全量目录误当成用户选择题。"""
    want = re.sub(r"\s+", "", str(hint or ""))
    scored: list[tuple[float, str]] = []
    for candidate in candidates:
        value = str(candidate or "").strip()
        if not value:
            continue
        score = SequenceMatcher(None, want, re.sub(r"\s+", "", value)).ratio()
        if score >= 0.35:
            scored.append((score, value))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [value for _, value in scored[:limit]]


def resolve_entities(
    filled: Mapping[str, str],
    peek: Mapping[str, Any] | None,
    *,
    edu_scope: Mapping[str, Any] | None = None,
    district_hint: str = "",
) -> EntityResolveResult:
    """对已填槽做目录对齐。peek 缺目录时只规范化，不追问。"""
    catalogs = dict(peek or {})
    bound: dict[str, str] = {
        k: str(v).strip() for k, v in (filled or {}).items() if str(v).strip()
    }
    edu = dict(edu_scope) if isinstance(edu_scope, Mapping) else {}

    exam_cands = list(catalogs.get("exam_names") or catalogs.get("exams") or [])
    school_cands = list(catalogs.get("schools") or catalogs.get("school_names") or [])
    class_cands = list(catalogs.get("classes") or catalogs.get("class_names") or [])
    if not class_cands and isinstance(edu.get("class_names"), list):
        class_cands = [str(x).strip() for x in edu["class_names"] if str(x).strip()]
    district_cands = list(catalogs.get("districts") or [])

    exam_hint = bound.get(SLOT_EXAM) or ""
    if exam_hint and exam_cands:
        hits = link_values(exam_hint, exam_cands)
        if len(hits) == 1:
            bound[SLOT_EXAM] = hits[0]
        elif len(hits) == 0:
            return EntityResolveResult(
                bound=bound,
                clarify_slot=SLOT_EXAM,
                options=exam_cands[:12],
                reason="考试名未对齐目录",
            )
        else:
            return EntityResolveResult(
                bound=bound,
                clarify_slot=SLOT_EXAM,
                options=hits[:12],
                reason="考试名对应多场",
            )

    school_hint = bound.get(SLOT_SCHOOL) or ""
    if school_hint and (
        is_interrogative_school_name(school_hint) or _is_exam_window_fragment(school_hint)
    ):
        bound.pop(SLOT_SCHOOL, None)
        school_hint = ""
    # 教师/校管账号只有脱敏 school_id 时，filled 会带 GZ_…；peek 目录是中文 s_name，
    # 对不上不等于「用户没说哪所学校」。权限已唯一绑校，不再追问。
    if school_hint and not _is_permission_school_id(school_hint, edu) and school_cands:
        multi = link_multi_school_values(school_hint, school_cands)
        if len(multi) >= 2:
            # 用户点名多校对比：不要逼选一所，也不要把拼接名写进单校槽。
            bound.pop(SLOT_SCHOOL, None)
        else:
            hits = link_school_values(school_hint, school_cands)
            if len(hits) == 1:
                bound[SLOT_SCHOOL] = hits[0]
            elif len(hits) > 1:
                return EntityResolveResult(
                    bound=bound,
                    clarify_slot=SLOT_SCHOOL,
                    options=hits[:12],
                    reason="学校名对应多所",
                )
            elif len(hits) == 0:
                return EntityResolveResult(
                    bound=bound,
                    clarify_slot=SLOT_SCHOOL,
                    options=school_cands[:12],
                    reason="学校名未对齐目录",
                )

    class_hint = bound.get(SLOT_CLASS) or ""
    if class_hint:
        bound[SLOT_CLASS] = normalize_class_name(class_hint)
        if class_cands:
            hits = link_values(bound[SLOT_CLASS], class_cands)
            if len(hits) == 1:
                bound[SLOT_CLASS] = normalize_class_name(hits[0])
            elif len(hits) > 1:
                return EntityResolveResult(
                    bound=bound,
                    clarify_slot=SLOT_CLASS,
                    options=[normalize_class_name(h) for h in hits[:12]],
                    reason="班级对应多个",
                )
            elif len(hits) == 0:
                return EntityResolveResult(
                    bound=bound,
                    clarify_slot=SLOT_CLASS,
                    options=[normalize_class_name(c) for c in class_cands[:12]],
                    reason="班级未对齐目录",
                )

    district = _strip_month_district(
        district_hint or extract_district_target(" ".join(str(v) for v in bound.values())) or ""
    )
    if district and district_cands:
        hits = link_values(district, district_cands)
        if len(hits) == 1:
            bound["district"] = hits[0]
    elif district and not district.startswith("月"):
        bound["district"] = district

    return EntityResolveResult(bound=bound)


def resolve_query_frame(
    frame: QueryFrame,
    peek: Mapping[str, Any] | None,
    *,
    edu_scope: Mapping[str, Any] | None = None,
) -> EntityResolveResult:
    """按实体来源对语义帧做目录落地，低可信残片零命中时静默丢弃。"""
    filled = frame.to_filled(grounded_only=False)
    result = resolve_entities(filled, peek, edu_scope=edu_scope)
    if result.clarify_slot:
        kind_by_slot = {
            SLOT_EXAM: "exam",
            SLOT_SCHOOL: "school",
            SLOT_CLASS: "class",
        }
        kind = kind_by_slot.get(result.clarify_slot)
        mention = frame.selected_filter(kind) if kind else None
        if mention and not mention.has_evidence and mention.source is EntitySource.RULE:
            mention.status = EntityStatus.REJECTED
            mention.reason = result.reason or "低可信规则候选未命中目录"
            frame.diagnostics.append(f"{kind}:catalog_miss_rejected")
            rerun = resolve_entities(
                frame.to_filled(grounded_only=False),
                peek,
                edu_scope=edu_scope,
            )
            rerun.frame = frame
            return rerun
        if mention:
            mention.status = EntityStatus.AMBIGUOUS
            result.options = _similar_candidates(mention.value, result.options)
            mention.candidates = list(result.options)
            mention.reason = result.reason
        result.frame = frame
        return result

    catalogs = dict(peek or {})
    catalog_by_kind = {
        "exam": list(catalogs.get("exam_names") or catalogs.get("exams") or []),
        "school": list(catalogs.get("schools") or catalogs.get("school_names") or []),
        "class": list(catalogs.get("classes") or catalogs.get("class_names") or []),
    }
    slot_by_kind = {"exam": SLOT_EXAM, "school": SLOT_SCHOOL, "class": SLOT_CLASS}
    for kind, candidates in catalog_by_kind.items():
        mention = frame.selected_filter(kind)
        if mention is None or not candidates:
            continue
        bound_value = str(result.bound.get(slot_by_kind[kind]) or "").strip()
        if bound_value:
            mention.value = bound_value
            mention.status = EntityStatus.GROUNDED
            mention.reason = "目录唯一命中"
        elif not mention.has_evidence and mention.source is EntitySource.RULE:
            mention.status = EntityStatus.REJECTED
            mention.reason = "低可信规则候选未进入目录绑定"
    result.bound = frame.to_filled(grounded_only=True)
    result.frame = frame
    return result


def bound_literals(bound: Mapping[str, str]) -> list[str]:
    """供 SQL 护栏比对的已绑定字面量。"""
    out: list[str] = []
    for key in (SLOT_EXAM, SLOT_SCHOOL, SLOT_CLASS, "district"):
        val = str((bound or {}).get(key) or "").strip()
        if val:
            out.append(val)
    return out


__all__ = [
    "EntityResolveResult",
    "bound_literals",
    "link_multi_school_values",
    "link_school_values",
    "link_values",
    "normalize_class_name",
    "resolve_entities",
    "resolve_query_frame",
]
