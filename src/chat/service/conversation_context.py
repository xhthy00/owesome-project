"""同一会话多轮：结构化槽位继承 + 近几轮问答短摘要。"""

from __future__ import annotations

import logging
import re
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
TURN_STANDALONE = "standalone"
TURN_FOLLOW_UP = "follow_up"
TURN_RETRY = "retry"

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
_NEW_QUESTION_PREFIXES = (
    "换个问题",
    "换一个问题",
    "新问题",
    "另外问",
    "重新提问",
    "忽略上面",
)
_FOLLOW_UP_HINTS = (
    "那",
    "那么",
    "这个",
    "该",
    "上述",
    "前面",
    "上一",
    "再看",
    "再查",
    "继续",
    "换成",
    "改成",
    "呢",
)


@dataclass
class TurnSnippet:
    question: str
    summary: str


@dataclass
class TurnContext:
    inherited: dict[str, str] = field(default_factory=dict)
    brief: str = ""
    latest_record_id: int | None = None


@dataclass
class ResolvedTurnContext:
    """本轮唯一执行语义；历史只用于生成它，不直接成为下游任务。"""

    turn_type: str
    original_question: str
    resolved_question: str
    parent_record_id: int | None = None
    inherited: dict[str, str] = field(default_factory=dict)
    context_capsule: str = ""


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
    # 本句明确缩小到学校/班级/学生时，不能继续携带上一轮的全市/全校范围。
    # 反过来，本句明确给出宽范围时，也不能残留上一轮的更窄对象。
    cur_scope = str((current or {}).get(SLOT_SCOPE) or "").strip()
    if cur_scope == "全市":
        out.pop(SLOT_SCHOOL, None)
        out.pop(SLOT_CLASS, None)
        out.pop(SLOT_STUDENT, None)
    elif cur_scope == "全校":
        out.pop(SLOT_CLASS, None)
        out.pop(SLOT_STUDENT, None)
    elif any(str((current or {}).get(slot) or "").strip() for slot in (
        SLOT_SCHOOL,
        SLOT_CLASS,
        SLOT_STUDENT,
    )):
        out.pop(SLOT_SCOPE, None)
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


def is_explicit_new_question(question: str) -> bool:
    normalized = str(question or "").strip().lower()
    return normalized.startswith(_NEW_QUESTION_PREFIXES)


def is_follow_up_question(question: str) -> bool:
    """保守识别省略/指代追问；完整问题默认视为新任务。"""
    q = str(question or "").strip()
    if not q or is_explicit_new_question(q):
        return False
    if any(hint in q for hint in _FOLLOW_UP_HINTS):
        return True
    from src.agent.education.query_parse import (
        refers_to_unspecified_exam,
        wants_full_student_roster,
    )

    if refers_to_unspecified_exam(q):
        return True
    if wants_full_student_roster(q):
        return True
    return len(q) <= 12 and bool(re.search(r"(多少|谁|如何|怎样|排名|均分|及格|优秀|最高|最低)", q))


def slots_from_record(question: str, exec_result: Any, edu_scope: Mapping[str, Any] | None) -> dict[str, str]:
    from src.agent.education.clarification import SLOT_SCHOOL
    from src.agent.education.query_parse import peel_rhetorical_school_suffix

    slots = extract_filled_slots(question or "", edu_scope)
    pending = parse_pending_clarify(exec_result)
    blob = pending if pending else (exec_result if isinstance(exec_result, dict) else None)
    if blob and isinstance(blob.get("filled"), dict):
        for key, raw in blob["filled"].items():
            val = str(raw or "").strip()
            if not val:
                continue
            if str(key) == SLOT_SCHOOL:
                val = peel_rhetorical_school_suffix(val)
                if not val:
                    continue
            slots[str(key)] = val
    return {k: v for k, v in slots.items() if str(v or "").strip()}


def context_from_records(
    records: list[Any],
    edu_scope: Mapping[str, Any] | None = None,
) -> TurnContext:
    """纯函数：从记录列表构建继承槽与摘要（供单测，不打库）。"""
    inherited: dict[str, str] = {}
    snippets: list[TurnSnippet] = []
    latest_record_id = None
    for rec in records:
        if getattr(rec, "is_success", True) is False:
            continue
        raw_id = getattr(rec, "id", None)
        latest_record_id = int(raw_id) if raw_id is not None else latest_record_id
        question = str(getattr(rec, "question", "") or "")
        resolved_question = str(getattr(rec, "resolved_question", "") or question)
        exec_result = getattr(rec, "exec_result", None)
        inherited.update(slots_from_record(resolved_question, exec_result, edu_scope))
        pending = parse_pending_clarify(exec_result)
        if pending:
            continue
        summary = (
            str(getattr(rec, "context_summary", None) or "").strip()
            or str(getattr(rec, "summary", None) or "").strip()
            or str(getattr(rec, "sql_answer", None) or "").strip()
            or str(getattr(rec, "reasoning", None) or "").strip()
        )
        snippets.append(TurnSnippet(question=question, summary=summary))
    return TurnContext(
        inherited=inherited,
        brief=build_conversation_brief(snippets),
        latest_record_id=latest_record_id,
    )


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


async def _rewrite_follow_up(
    question: str,
    brief: str,
    llm_client: Any,
) -> str:
    """仅做指代消解；失败时由结构化补槽结果兜底。"""
    if not brief or llm_client is None:
        return question
    messages = [
        {
            "role": "system",
            "content": (
                "你只负责把当前追问改写为一条可独立理解的问题。"
                "历史仅用于补全指代，不得回答问题，不得合并或重复历史任务。"
                "只输出改写后的问题文本。"
            ),
        },
        {
            "role": "user",
            "content": f"【历史背景】\n{brief}\n\n【当前追问】\n{question}",
        },
    ]
    try:
        from src.agent.util.tool_call_parser import strip_think_blocks

        result = await llm_client.chat(messages)
        rewritten = strip_think_blocks(str(result or "")).strip().strip("`").strip()
        if rewritten and len(rewritten) <= 1000:
            return rewritten
    except Exception as exc:  # noqa: BLE001
        logger.warning("rewrite follow-up failed: %s", exc)
    return question


async def resolve_turn_context(
    *,
    question: str,
    conversation_id: int | None,
    user_id: int,
    edu_scope: Mapping[str, Any] | None = None,
    llm_client: Any = None,
    is_retry_chat: bool = False,
    original_question: str | None = None,
    parent_record_id: int | None = None,
) -> ResolvedTurnContext:
    """将本轮输入解析为唯一自包含任务，阻断历史消息直接进入 Agent。"""
    original = str(original_question if original_question is not None else question).strip()
    current = str(question or "").strip()
    if is_retry_chat:
        return ResolvedTurnContext(
            turn_type=TURN_RETRY,
            original_question=original,
            resolved_question=current,
            parent_record_id=parent_record_id,
        )

    if is_explicit_new_question(original):
        return ResolvedTurnContext(
            turn_type=TURN_STANDALONE,
            original_question=original,
            resolved_question=current,
        )

    if not is_follow_up_question(original):
        return ResolvedTurnContext(
            turn_type=TURN_STANDALONE,
            original_question=original,
            resolved_question=current,
        )

    turn_ctx = load_turn_context(conversation_id, user_id, edu_scope)
    if (
        turn_ctx.latest_record_id is None
        and not turn_ctx.brief
        and not turn_ctx.inherited
    ):
        return ResolvedTurnContext(
            turn_type=TURN_STANDALONE,
            original_question=original,
            resolved_question=current,
        )
    current_slots = extract_filled_slots(current, edu_scope)
    merged = merge_inherited_slots(current_slots, turn_ctx.inherited, original)
    extra = extra_inherited_slots(current_slots, merged)
    resolved = apply_inherited_supplements(current, extra)
    # 闸门可能已经用确定性槽位把原问补成自包含问题；此时不能再交给 LLM
    # 二次改写，否则模型推理文本或近似值会覆盖已绑定真值。
    if resolved == current and current == original:
        resolved = await _rewrite_follow_up(current, turn_ctx.brief, llm_client)
    from src.agent.education.query_parse import wants_full_student_roster as _wants_roster

    if _wants_roster(original) and not _wants_roster(resolved):
        resolved = resolved.rstrip("。． ") + "（需完整名单）"
    # 只有无法改写成独立问题时才给单 Agent 一个短背景兜底；正常路径不把历史
    # 继续传给执行 Agent。
    capsule = ""
    if resolved == current and turn_ctx.brief:
        capsule = (
            "以下内容仅用于解释当前问题中的指代，均为已完成历史，禁止重新执行或回答：\n"
            + turn_ctx.brief
        )
    return ResolvedTurnContext(
        turn_type=TURN_FOLLOW_UP,
        original_question=original,
        resolved_question=resolved,
        parent_record_id=turn_ctx.latest_record_id,
        inherited=merged,
        context_capsule=capsule,
    )


__all__ = [
    "ResolvedTurnContext",
    "TURN_FOLLOW_UP",
    "TURN_RETRY",
    "TURN_STANDALONE",
    "TurnContext",
    "TurnSnippet",
    "apply_inherited_supplements",
    "build_conversation_brief",
    "context_from_records",
    "extra_inherited_slots",
    "is_explicit_new_question",
    "is_follow_up_question",
    "load_turn_context",
    "merge_inherited_slots",
    "resolve_turn_context",
    "slots_from_record",
    "slots_reset_by_question",
]
