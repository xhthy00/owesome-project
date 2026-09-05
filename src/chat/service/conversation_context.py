"""同一会话多轮：结构化槽位继承 + 近几轮问答短摘要。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Mapping

from src.agent.education.clarification import (
    SLOT_CLASS,
    SLOT_EXAM,
    SLOT_SCHOOL,
    SLOT_SCOPE,
    SLOT_STUDENT,
    SLOT_SUBJECT,
    extract_filled_slots,
    parse_pending_clarify,
)

logger = logging.getLogger(__name__)

HISTORY_LIMIT = 8
BRIEF_MAX_TURNS = 4
SUMMARY_MAX_CHARS = 400
QUESTION_MAX_CHARS = 120
BRIEF_MAX_CHARS = 2500

_SLOT_LABEL = {
    SLOT_EXAM: "考试",
    SLOT_CLASS: "班级",
    SLOT_SCHOOL: "学校",
    SLOT_SUBJECT: "科目",
    SLOT_STUDENT: "学生",
    SLOT_SCOPE: "范围",
}

_EXAM_RESET = ("换一场", "另一场", "换个考试", "别的考试", "换考试")
_CLASS_RESET = ("换个班", "另一个班", "别的班")
_SCHOOL_RESET = ("换一所", "另一所", "别的学校")


@dataclass
class TurnSnippet:
    question: str
    summary: str


@dataclass
class TurnContext:
    inherited: dict[str, str] = field(default_factory=dict)
    brief: str = ""


def slots_reset_by_question(question: str) -> set[str]:
    """本句明确要求换范围时，丢掉对应继承槽。"""
    q = question or ""
    reset: set[str] = set()
    if any(h in q for h in _EXAM_RESET):
        reset.add(SLOT_EXAM)
    if any(h in q for h in _CLASS_RESET):
        reset.add(SLOT_CLASS)
    if any(h in q for h in _SCHOOL_RESET):
        reset.add(SLOT_SCHOOL)
    return reset


def _reset_hints_for(slot: str) -> tuple[str, ...]:
    if slot == SLOT_EXAM:
        return _EXAM_RESET
    if slot == SLOT_CLASS:
        return _CLASS_RESET
    if slot == SLOT_SCHOOL:
        return _SCHOOL_RESET
    return ()


def _value_is_reset_fragment(slot: str, value: str) -> bool:
    """「换一场」等重置词被抽成槽值时，不当作本句点名。"""
    v = (value or "").strip()
    if not v:
        return True
    for hint in _reset_hints_for(slot):
        if v == hint or hint in v or v in hint:
            return True
    return False


def merge_inherited_slots(
    current: Mapping[str, str],
    inherited: Mapping[str, str],
    question: str,
) -> dict[str, str]:
    """历史槽填空缺，本句显式点名覆盖；重置词丢掉对应继承。"""
    reset = slots_reset_by_question(question)
    out: dict[str, str] = {}
    for key, raw in (inherited or {}).items():
        val = str(raw or "").strip()
        if not val or key in reset:
            continue
        out[key] = val
    for key, raw in (current or {}).items():
        val = str(raw or "").strip()
        if not val:
            continue
        if key in reset and _value_is_reset_fragment(key, val):
            continue
        out[key] = val
    # 本句点名另一所学校且未点名班级时，上一所学校的班级不能跟着走
    cur_school = str((current or {}).get(SLOT_SCHOOL) or "").strip()
    cur_class = str((current or {}).get(SLOT_CLASS) or "").strip()
    inh_school = str((inherited or {}).get(SLOT_SCHOOL) or "").strip()
    if cur_school and not cur_class and inh_school != cur_school:
        out.pop(SLOT_CLASS, None)
    return out


def extra_inherited_slots(
    current: Mapping[str, str],
    merged: Mapping[str, str],
) -> dict[str, str]:
    """合并结果里、本句尚未点名的槽，供追加「补充：」。"""
    extra: dict[str, str] = {}
    for key, raw in (merged or {}).items():
        val = str(raw or "").strip()
        if not val:
            continue
        if str((current or {}).get(key) or "").strip():
            continue
        extra[key] = val
    return extra


def apply_inherited_supplements(question: str, extra: Mapping[str, str]) -> str:
    """把继承槽拼成现有「补充：标签=值」协议，便于抽槽与工具读 user_question。"""
    q = (question or "").strip()
    parts: list[str] = []
    for slot, label in _SLOT_LABEL.items():
        val = str((extra or {}).get(slot) or "").strip()
        if val:
            parts.append(f"补充：{label}={val}")
    if not parts:
        return q
    if not q:
        return "。".join(parts)
    return q + "。" + "。".join(parts)


def _clip(text: str, limit: int) -> str:
    t = str(text or "").strip().replace("\n", " ")
    if len(t) <= limit:
        return t
    return t[: limit - 1] + "…"


def build_conversation_brief(turns: list[TurnSnippet]) -> str:
    """近几轮用户问 + 短结论；硬顶长度。"""
    picked = [t for t in turns if str(t.question or "").strip()][-BRIEF_MAX_TURNS:]
    lines: list[str] = []
    for item in picked:
        q = _clip(item.question, QUESTION_MAX_CHARS)
        s = _clip(item.summary, SUMMARY_MAX_CHARS)
        if q:
            lines.append(f"用户：{q}")
        if s:
            lines.append(f"助手：{s}")
    text = "\n".join(lines).strip()
    if len(text) > BRIEF_MAX_CHARS:
        text = text[-BRIEF_MAX_CHARS:]
    return text


def slots_from_record(question: str, exec_result: Any, edu_scope: Mapping[str, Any] | None) -> dict[str, str]:
    slots = extract_filled_slots(question or "", edu_scope)
    pending = parse_pending_clarify(exec_result)
    blob = pending if pending else (exec_result if isinstance(exec_result, dict) else None)
    if blob and isinstance(blob.get("filled"), dict):
        for key, raw in blob["filled"].items():
            val = str(raw or "").strip()
            if val:
                slots[str(key)] = val
    return {k: v for k, v in slots.items() if str(v or "").strip()}


def context_from_records(
    records: list[Any],
    edu_scope: Mapping[str, Any] | None = None,
) -> TurnContext:
    """纯函数：从记录列表构建继承槽与摘要（供单测，不打库）。"""
    inherited: dict[str, str] = {}
    snippets: list[TurnSnippet] = []
    for rec in records:
        if getattr(rec, "is_success", True) is False:
            continue
        question = str(getattr(rec, "question", "") or "")
        exec_result = getattr(rec, "exec_result", None)
        inherited.update(slots_from_record(question, exec_result, edu_scope))
        pending = parse_pending_clarify(exec_result)
        if pending:
            continue
        summary = (
            str(getattr(rec, "summary", None) or "").strip()
            or str(getattr(rec, "sql_answer", None) or "").strip()
            or str(getattr(rec, "reasoning", None) or "").strip()
        )
        snippets.append(TurnSnippet(question=question, summary=summary))
    return TurnContext(inherited=inherited, brief=build_conversation_brief(snippets))


def load_turn_context(
    conversation_id: int | None,
    user_id: int,
    edu_scope: Mapping[str, Any] | None = None,
) -> TurnContext:
    """读近几轮轻量记录；无会话或失败则空上下文。"""
    if not conversation_id:
        return TurnContext()
    try:
        from src.chat.crud.chat import list_conversation_turns_for_context
        from src.common.core.database import get_db_session

        with get_db_session() as session:
            records = list_conversation_turns_for_context(
                session,
                int(conversation_id),
                int(user_id),
                limit=HISTORY_LIMIT,
            )
        return context_from_records(records, edu_scope)
    except Exception as exc:  # noqa: BLE001
        logger.warning("load turn context failed: %s", exc)
        return TurnContext()


__all__ = [
    "TurnContext",
    "TurnSnippet",
    "apply_inherited_supplements",
    "build_conversation_brief",
    "context_from_records",
    "extra_inherited_slots",
    "load_turn_context",
    "merge_inherited_slots",
    "slots_from_record",
    "slots_reset_by_question",
]
