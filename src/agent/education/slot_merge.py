"""规则、LLM、权限与历史槽位的统一合并策略。"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Mapping

from src.agent.education.query_frame import (
    EntityMention,
    EntityRole,
    EntitySource,
    EntityStatus,
    ExamWindow,
    QueryFrame,
    parse_structural_frame,
    slot_kind,
)

logger = logging.getLogger(__name__)

_SLOT_SCOPE = "scope"
_WINDOW_FRAGMENT_RE = re.compile(r"(?:[次场](?:考试|测试|检测)?|考试|测试|检测)$")
_SCHOOL_SUFFIX_RE = re.compile(r"(?:中学|学校|学院|大学|附中|分校)$")
_INTERROGATIVE_RE = re.compile(r"(?:哪|哪些|哪个|几所|几个|多少|谁|都有哪些|有几)")


@dataclass
class SlotMergeResult:
    frame: QueryFrame
    filled: dict[str, str]


def _permission_values(edu_scope: Mapping[str, Any] | None) -> dict[str, set[str]]:
    edu = dict(edu_scope or {})
    out: dict[str, set[str]] = {
        "school_name": {
            str(edu.get("school_name") or "").strip(),
            str(edu.get("school_id") or "").strip(),
        },
        "student_id": {str(edu.get("student_id") or "").strip()},
        "class_name": {
            str(item).strip() for item in (edu.get("class_names") or []) if str(item).strip()
        },
    }
    return {key: {value for value in values if value} for key, values in out.items()}


def _evidence_for(value: str, question: str) -> str:
    compact = "".join(str(value or "").split())
    compact_q = "".join(str(question or "").split())
    return compact if compact and compact in compact_q else ""


def _evidence_in_question(evidence: str, question: str) -> bool:
    raw = str(evidence or "").strip()
    q = str(question or "")
    if not raw:
        return False
    if raw in q:
        return True
    compact = "".join(raw.split())
    compact_q = "".join(q.split())
    return bool(compact) and compact in compact_q


def _mark_district_school(mention: EntityMention) -> None:
    """白名单区县说法被标成学校时丢掉，后面仍按区县走。"""
    if mention.kind != "school" or mention.role is not EntityRole.FILTER:
        return
    from src.agent.education.query_parse import is_district_phrase

    if not is_district_phrase(mention.value):
        return
    mention.status = EntityStatus.REJECTED
    mention.reason = "区县说法不是校名"


def _add_slot_mentions(
    frame: QueryFrame,
    slots: Mapping[str, Any] | None,
    *,
    question: str,
    source: EntitySource,
    permission_values: Mapping[str, set[str]],
    confidence: float,
) -> None:
    for slot, raw in dict(slots or {}).items():
        value = str(raw or "").strip()
        kind = slot_kind(str(slot))
        if not kind or not value:
            continue
        evidence = _evidence_for(value, question)
        actual_source = source
        if source is EntitySource.RULE and value in permission_values.get(str(slot), set()):
            if not evidence:
                actual_source = EntitySource.PERMISSION
        status = EntityStatus.RAW
        school_reject_reason = ""
        if source is EntitySource.LLM and not evidence:
            status = EntityStatus.REJECTED
        if kind == "school" and _WINDOW_FRAGMENT_RE.fullmatch(value):
            status = EntityStatus.REJECTED
            school_reject_reason = "考试窗口残片不是学校"
        elif kind == "school":
            from src.agent.education.query_parse import (
                _is_descriptive_school_phrase,
                is_interrogative_school_name,
            )

            if _is_descriptive_school_phrase(value) or is_interrogative_school_name(value):
                status = EntityStatus.REJECTED
                school_reject_reason = "描述性学校短语不是校名"
        if status is EntityStatus.REJECTED and not school_reject_reason:
            school_reject_reason = "LLM 值无原文证据"
        mention = EntityMention(
            kind=kind,
            value=value,
            source=actual_source,
            evidence=evidence,
            confidence=confidence,
            status=status,
            reason=school_reject_reason if status is EntityStatus.REJECTED else "",
        )
        _mark_district_school(mention)
        frame.add_mention(mention)


def _add_llm_mentions(
    frame: QueryFrame,
    mentions: list[Mapping[str, Any]] | None,
    *,
    question: str,
) -> None:
    for raw in mentions or []:
        mention = EntityMention.from_dict(raw)
        mention.source = EntitySource.LLM
        if mention.value and not mention.evidence:
            mention.evidence = _evidence_for(mention.value, question)
        if mention.evidence and not _evidence_in_question(mention.evidence, question):
            mention.status = EntityStatus.REJECTED
            mention.reason = "LLM evidence 不在当前问句"
        elif mention.role is EntityRole.FILTER and not mention.evidence:
            mention.status = EntityStatus.REJECTED
            mention.reason = "LLM filter 无原文证据"
        _mark_district_school(mention)
        frame.add_mention(mention)
        if mention.role is EntityRole.LOOKUP and mention.kind not in frame.answer_dimensions:
            frame.answer_dimensions.append(mention.kind)


def _deduplicate(frame: QueryFrame) -> None:
    unique: list[EntityMention] = []
    by_key: dict[tuple[str, str, EntityRole], EntityMention] = {}
    priority = {
        EntitySource.HISTORY: 10,
        EntitySource.PERMISSION: 20,
        EntitySource.RULE: 30,
        EntitySource.LLM: 40,
    }
    for mention in frame.mentions:
        key = (mention.kind, mention.value, mention.role)
        previous = by_key.get(key)
        if previous is None:
            by_key[key] = mention
            unique.append(mention)
            continue
        if priority.get(mention.source, 0) > priority.get(previous.source, 0):
            previous.source = mention.source
        previous.confidence = max(previous.confidence, mention.confidence)
        previous.evidence = previous.evidence or mention.evidence
        if previous.status is EntityStatus.REJECTED and mention.status is not EntityStatus.REJECTED:
            previous.status = mention.status
            previous.reason = mention.reason
    frame.mentions = unique


def _apply_scope_resets(frame: QueryFrame) -> None:
    rejected: set[str] = set()
    if frame.scope == "全市":
        rejected = {"school", "class", "student"}
    elif frame.scope == "全校":
        rejected = {"class", "student"}
    if not rejected:
        return
    for mention in frame.mentions:
        if (
            mention.kind in rejected
            and mention.source in {EntitySource.HISTORY, EntitySource.PERMISSION}
            and mention.role is EntityRole.FILTER
        ):
            mention.status = EntityStatus.REJECTED
            mention.reason = f"{frame.scope}范围清理默认或历史槽"


def _credible_same_dimension_filter(mention: EntityMention, frame: QueryFrame) -> bool:
    value = mention.value
    if mention.source is not EntitySource.RULE:
        return mention.has_evidence
    if mention.kind == "school":
        if _INTERROGATIVE_RE.search(value):
            return False
        from src.agent.education.query_parse import _is_descriptive_school_phrase

        if _is_descriptive_school_phrase(value):
            return False
        if frame.scope == "全市" and value.startswith(("全市", "各校", "各学校")):
            return False
        return bool(_SCHOOL_SUFFIX_RE.search(value))
    if mention.kind == "class":
        return bool(re.search(r"(?:高|初)[一二三]\s*\(?\d+\)?班", value))
    if mention.kind == "student":
        return bool(re.search(r"(?:学生\s*\d+|STU[A-Za-z0-9_-]+)", value, re.I))
    if mention.kind == "exam":
        return bool(re.search(r"(?:\d{4}届|\d{1,2}月|一模|二模|三模|期中|期末)", value))
    return mention.has_evidence


def _reject_structural_false_filters(frame: QueryFrame) -> None:
    if frame.exam_window:
        window_text = frame.exam_window.evidence
        for mention in frame.filter_mentions("exam"):
            if mention.source is EntitySource.RULE and (
                mention.value in window_text
                or mention.evidence in window_text
                or window_text in mention.evidence
            ):
                mention.status = EntityStatus.REJECTED
                mention.reason = "考试窗口不是考试专名"
    for dimension in frame.answer_dimensions:
        for mention in frame.filter_mentions(dimension):
            if _credible_same_dimension_filter(mention, frame):
                continue
            mention.status = EntityStatus.REJECTED
            mention.reason = "答案维度被旧规则误抽为筛选槽"


def _norm_school_name(value: str) -> str:
    from src.agent.education.query_parse import normalize_fullwidth_parentheses

    return normalize_fullwidth_parentheses(re.sub(r"\s+", "", value))


def _collapse_less_specific_schools(frame: QueryFrame) -> None:
    """「扬大附中」被「扬大附中（淮海路）」盖住；全半角同一校名只留一条。"""
    schools = [
        item
        for item in frame.mentions
        if item.kind == "school"
        and item.role is EntityRole.FILTER
        and item.status is not EntityStatus.REJECTED
        and item.value
    ]
    norms = [(item, _norm_school_name(item.value)) for item in schools]
    for item, name in norms:
        if any(
            other is not item and other_name.startswith(name) and len(other_name) > len(name)
            for other, other_name in norms
        ):
            item.status = EntityStatus.REJECTED
            item.reason = "被更具体的校名覆盖"
    priority = {
        EntitySource.HISTORY: 10,
        EntitySource.PERMISSION: 20,
        EntitySource.RULE: 30,
        EntitySource.LLM: 40,
    }
    kept: dict[str, EntityMention] = {}
    for item, name in norms:
        if item.status is EntityStatus.REJECTED or not name:
            continue
        previous = kept.get(name)
        if previous is None:
            kept[name] = item
            continue
        if priority.get(item.source, 0) > priority.get(previous.source, 0):
            previous.status = EntityStatus.REJECTED
            previous.reason = "与同名校名重复"
            kept[name] = item
        else:
            item.status = EntityStatus.REJECTED
            item.reason = "与同名校名重复"


def _mark_conflicts(frame: QueryFrame) -> None:
    for kind in {item.kind for item in frame.filter_mentions()}:
        current = [
            item
            for item in frame.filter_mentions(kind)
            if item.source in {EntitySource.RULE, EntitySource.LLM} and item.has_evidence
        ]
        values = {item.value for item in current if item.value}
        if len(values) <= 1:
            continue
        if kind == "school":
            # 问句点名多校（A和B对比）不是规则/LLM 冲突。
            continue
        for item in current:
            item.status = EntityStatus.AMBIGUOUS
            item.reason = "本轮规则与语义抽取结果冲突"
        frame.diagnostics.append(f"{kind}:conflict")


def merge_turn_slots(
    question: str,
    *,
    rule_slots: Mapping[str, Any] | None,
    llm_slots: Mapping[str, Any] | None = None,
    llm_mentions: list[Mapping[str, Any]] | None = None,
    llm_answer_dimensions: list[str] | None = None,
    llm_exam_window: Mapping[str, Any] | None = None,
    inherited: Mapping[str, Any] | None = None,
    edu_scope: Mapping[str, Any] | None = None,
) -> SlotMergeResult:
    """合并一轮槽位，保留来源、角色与证据。"""
    q = str(question or "")
    frame = parse_structural_frame(q)
    permissions = _permission_values(edu_scope)
    _add_slot_mentions(
        frame,
        inherited,
        question=q,
        source=EntitySource.HISTORY,
        permission_values=permissions,
        confidence=0.5,
    )
    _add_slot_mentions(
        frame,
        rule_slots,
        question=q,
        source=EntitySource.RULE,
        permission_values=permissions,
        confidence=0.8,
    )
    _add_slot_mentions(
        frame,
        llm_slots,
        question=q,
        source=EntitySource.LLM,
        permission_values=permissions,
        confidence=0.85,
    )
    _add_llm_mentions(frame, llm_mentions, question=q)

    for dimension in llm_answer_dimensions or []:
        kind = str(dimension or "").strip()
        if kind in {"school", "class", "student", "exam"} and kind not in frame.answer_dimensions:
            frame.answer_dimensions.append(kind)
    llm_window = ExamWindow.from_dict(llm_exam_window or {})
    if llm_window and (
        not frame.exam_window
        or (llm_window.evidence and llm_window.evidence in q)
    ):
        frame.exam_window = llm_window

    rule_scope = str((rule_slots or {}).get(_SLOT_SCOPE) or "").strip()
    llm_scope = str((llm_slots or {}).get(_SLOT_SCOPE) or "").strip()
    if llm_scope in {"全市", "全校", "指定班级"}:
        frame.scope = llm_scope
    elif rule_scope:
        frame.scope = rule_scope

    _deduplicate(frame)
    _apply_scope_resets(frame)
    _reject_structural_false_filters(frame)
    _mark_conflicts(frame)

    # 当前问句显式提到了某类实体时，不继承上一轮同类实体。
    current_kinds = {
        item.kind
        for item in frame.filter_mentions()
        if item.source in {EntitySource.RULE, EntitySource.LLM} and item.has_evidence
    }
    for mention in frame.mentions:
        if (
            mention.kind in current_kinds
            and mention.source is EntitySource.HISTORY
            and mention.role is EntityRole.FILTER
        ):
            mention.status = EntityStatus.REJECTED
            mention.reason = "被本轮显式实体替换"

    _collapse_less_specific_schools(frame)

    # lookup 是答案维度。只有当前问句确实存在可信 filter 时，才允许二者并存。
    for dimension in list(frame.answer_dimensions):
        explicit_filters = [
            item
            for item in frame.filter_mentions(dimension)
            if item.has_evidence
            and item.status not in {EntityStatus.REJECTED, EntityStatus.AMBIGUOUS}
        ]
        explicit_values = {item.value for item in explicit_filters}
        if len(explicit_values) == 1:
            frame.answer_dimensions.remove(dimension)
            for mention in frame.mentions:
                if mention.kind == dimension and mention.role is EntityRole.LOOKUP:
                    mention.status = EntityStatus.REJECTED
                    mention.reason = "同维度已有本轮显式筛选实体"
            continue
        if explicit_filters:
            continue
        for mention in frame.filter_mentions(dimension):
            mention.status = EntityStatus.REJECTED
            mention.reason = "答案维度不能作为筛选槽"

    filled = frame.to_filled(grounded_only=False)
    if frame.diagnostics:
        logger.info("edu_slot_merge diagnostics=%s", frame.diagnostics)
    return SlotMergeResult(frame=frame, filled=filled)


__all__ = ["SlotMergeResult", "merge_turn_slots"]
