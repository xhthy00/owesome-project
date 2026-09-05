"""值链接：把问句里的考试/校/班/区县对齐到 peek 目录真值。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from src.agent.education.clarification import (
    SLOT_CLASS,
    SLOT_EXAM,
    SLOT_SCHOOL,
)
from src.agent.education.query_parse import (
    extract_district_target,
    normalize_fullwidth_parentheses,
)


@dataclass
class EntityResolveResult:
    """0 个 → clarify；1 个 → bound；多个 → clarify 带 options。"""

    bound: dict[str, str] = field(default_factory=dict)
    clarify_slot: str | None = None
    options: list[str] = field(default_factory=list)
    reason: str = ""


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
    if school_hint and school_cands:
        hits = link_values(school_hint, school_cands)
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
    "link_values",
    "normalize_class_name",
    "resolve_entities",
]
