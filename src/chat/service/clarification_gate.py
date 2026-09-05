"""Chat 入口的追问闸门：合并 pending、判定、SSE、落库。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from src.agent.education.clarification import (
    SLOT_CLASS,
    SLOT_EXAM,
    SLOT_SCHOOL,
    SLOT_STUDENT,
    SLOT_SUBJECT,
    ClarificationNeed,
    candidate_missing_slots,
    default_options_for_slot,
    extract_filled_slots,
    fallback_clarification,
    judge_clarification,
    merge_clarification_reply,
    parse_pending_clarify,
)
from src.agent.education.intent_router import ReportRoute, classify_report_intent
from src.chat.schemas import ChatRequest
from src.chat.service.agent_runner import (
    EmitCallback,
    _build_shared_constraints,
    _persist_async,
    _RunConstraints,
)

logger = logging.getLogger(__name__)


@dataclass
class ClarifyTurnResult:
    """闸门结果：halted 表示本轮只追问、不再跑 Agent。"""

    halted: bool
    record_id: int = 0
    constraints: _RunConstraints | None = None
    filled: dict[str, str] = field(default_factory=dict)
    route: ReportRoute | None = None
    effective_question: str = ""
    persist_question: str = ""


def _load_pending(conversation_id: int | None, user_id: int) -> dict[str, Any] | None:
    if not conversation_id:
        return None
    try:
        from src.chat.crud.chat import get_latest_conversation_record
        from src.common.core.database import get_db_session

        with get_db_session() as session:
            rec = get_latest_conversation_record(session, int(conversation_id), int(user_id))
            if rec is None:
                return None
            return parse_pending_clarify(rec.exec_result)
    except Exception as exc:  # noqa: BLE001
        logger.warning("load pending clarify failed: %s", exc)
        return None


def _apply_filled(constraints: _RunConstraints, filled: dict[str, str]) -> None:
    if filled.get(SLOT_SCHOOL):
        constraints.target_school = filled[SLOT_SCHOOL]
    if filled.get(SLOT_STUDENT):
        constraints.target_student = filled[SLOT_STUDENT]
    if filled.get(SLOT_CLASS):
        constraints.target_classes = [filled[SLOT_CLASS]]
    if filled.get(SLOT_EXAM):
        constraints.target_exam = filled[SLOT_EXAM]
    if filled.get(SLOT_SUBJECT):
        constraints.target_subject = filled[SLOT_SUBJECT]
    try:
        from src.agent.education.entity_resolve import bound_literals

        constraints.bound_literals = bound_literals(filled)
    except Exception:  # noqa: BLE001
        pass


def _chat_fn(llm_client: Any):
    async def _run(messages: list[dict[str, str]]) -> str:
        return await llm_client.chat(messages)

    return _run


async def _load_options_for_slot(
    slot: str,
    *,
    datasource_id: int,
    workspace_oid: int,
    user_id: int,
    filled: dict[str, str],
) -> list[str]:
    defaults = default_options_for_slot(slot)
    if defaults:
        return defaults
    try:
        from datasource.service.edu_permission import EduScope, edu_scope_dict_for_user_id
        from src.agent.education.api import _build_orchestrator, _load_meta_options

        orch = _build_orchestrator(datasource_id, workspace_oid, user_id=user_id)
        edu = edu_scope_dict_for_user_id(user_id)
        options = await _load_meta_options(
            orch,
            school_name=filled.get(SLOT_SCHOOL) or None,
            exam_name=filled.get("exam_name") or None,
            class_name=filled.get(SLOT_CLASS) or None,
            subject=filled.get("subject_name") or None,
            edu_scope=EduScope.from_dict(edu),
        )
        key = {
            "exam_name": "exams",
            "class_name": "classes",
            "school_name": "schools",
            "subject_name": "subjects",
        }.get(slot)
        if not key:
            return []
        raw = options.get(key) or []
        out: list[str] = []
        for item in raw:
            s = str(item or "").strip()
            if s and s not in out:
                out.append(s)
            if len(out) >= 12:
                break
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("load clarify options failed: %s", exc)
        return []


async def _load_entity_catalog(
    *,
    datasource_id: int,
    workspace_oid: int,
    user_id: int,
    filled: dict[str, str],
) -> dict[str, list[str]]:
    try:
        from datasource.service.edu_permission import EduScope, edu_scope_dict_for_user_id
        from src.agent.education.api import _build_orchestrator, _load_meta_options

        orch = _build_orchestrator(datasource_id, workspace_oid, user_id=user_id)
        edu = edu_scope_dict_for_user_id(user_id)
        return await _load_meta_options(
            orch,
            school_name=filled.get(SLOT_SCHOOL) or None,
            exam_name=filled.get(SLOT_EXAM) or None,
            class_name=filled.get(SLOT_CLASS) or None,
            subject=filled.get(SLOT_SUBJECT) or None,
            edu_scope=EduScope.from_dict(edu),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("load entity catalog failed: %s", exc)
        return {}


async def _link_or_clarify(
    *,
    request: ChatRequest,
    current_user_id: int,
    emit: EmitCallback,
    persist: bool,
    workspace_oid: int,
    constraints: _RunConstraints,
    filled: dict[str, str],
    route: ReportRoute,
    persist_question: str,
    effective_question: str,
) -> ClarifyTurnResult | None:
    """值链接：唯一命中写入规范名；0/N 转追问。目录失败则放行。"""
    from src.chat.service.conversation_context import (
        apply_inherited_supplements,
        extra_inherited_slots,
    )

    catalog = await _load_entity_catalog(
        datasource_id=int(request.datasource_id),
        workspace_oid=workspace_oid,
        user_id=current_user_id,
        filled=filled,
    )
    if not catalog:
        return None
    from src.agent.education.entity_resolve import resolve_entities

    result = resolve_entities(filled, catalog, edu_scope=constraints.edu_scope)
    if result.clarify_slot:
        need = fallback_clarification(
            persist_question,
            [result.clarify_slot],
            result.bound or filled,
            report_type=route.report_type.value if route.report_type else None,
            options=result.options,
        )
        if need is None:
            return None
        return await _emit_and_persist_clarify(
            request=request,
            current_user_id=current_user_id,
            emit=emit,
            persist=persist,
            workspace_oid=workspace_oid,
            need=need,
            constraints=constraints,
            filled=dict(result.bound or filled),
            route=route,
            persist_question=persist_question,
        )
    changed = {
        k: v
        for k, v in result.bound.items()
        if v and str(filled.get(k) or "").strip() != v
    }
    filled.update(result.bound)
    _apply_filled(constraints, filled)
    extra = extra_inherited_slots({}, changed)
    if extra:
        patched = apply_inherited_supplements(effective_question, extra)
        persist_patched = apply_inherited_supplements(persist_question, extra)
        return ClarifyTurnResult(
            halted=False,
            constraints=constraints,
            filled=filled,
            route=route,
            effective_question=patched,
            persist_question=persist_patched,
        )
    return None


async def maybe_clarify_turn(
    *,
    request: ChatRequest,
    current_user_id: int,
    emit: EmitCallback,
    llm_client: Any,
    persist: bool = True,
    workspace_oid: int = 1,
) -> ClarifyTurnResult:
    """合并 pending → 分类 → 抽槽 → LLM 判定。需追问则 SSE + 落库并 halted。"""
    from src.chat.service.conversation_context import (
        apply_inherited_supplements,
        extra_inherited_slots,
        load_turn_context,
        merge_inherited_slots,
    )

    pending = _load_pending(request.conversation_id, current_user_id)
    if pending:
        request.question = merge_clarification_reply(pending, request.question)
    persist_question = request.question

    constraints = _build_shared_constraints(request.question, current_user_id)
    constraints.report_audience = request.report_audience
    constraints.user_utterance = persist_question

    route = await classify_report_intent(request.question, llm_client)
    constraints.report_route = route.to_dict()

    edu = constraints.edu_scope or {}
    current_filled = extract_filled_slots(request.question, edu)
    turn_ctx = load_turn_context(request.conversation_id, current_user_id, edu)
    filled = merge_inherited_slots(current_filled, turn_ctx.inherited, request.question)
    extra = extra_inherited_slots(current_filled, filled)
    effective_question = apply_inherited_supplements(request.question, extra)
    if turn_ctx.brief:
        constraints.conversation_brief = turn_ctx.brief
    _apply_filled(constraints, filled)
    candidates = candidate_missing_slots(route, request.question, filled, edu)

    def _pass(*, halted: bool, record_id: int = 0) -> ClarifyTurnResult:
        return ClarifyTurnResult(
            halted=halted,
            record_id=record_id,
            constraints=constraints,
            filled=filled,
            route=route,
            effective_question=effective_question,
            persist_question=persist_question,
        )

    if not candidates:
        linked = await _link_or_clarify(
            request=request,
            current_user_id=current_user_id,
            emit=emit,
            persist=persist,
            workspace_oid=workspace_oid,
            constraints=constraints,
            filled=filled,
            route=route,
            persist_question=persist_question,
            effective_question=effective_question,
        )
        if linked is not None:
            return linked
        return _pass(halted=False)

    need = await judge_clarification(
        request.question,
        route=route,
        filled=filled,
        candidates=candidates,
        edu_scope=edu,
        chat_fn=_chat_fn(llm_client),
    )
    if need is None:
        linked = await _link_or_clarify(
            request=request,
            current_user_id=current_user_id,
            emit=emit,
            persist=persist,
            workspace_oid=workspace_oid,
            constraints=constraints,
            filled=filled,
            route=route,
            persist_question=persist_question,
            effective_question=effective_question,
        )
        if linked is not None:
            return linked
        return _pass(halted=False)

    opts = await _load_options_for_slot(
        need.missing[0],
        datasource_id=int(request.datasource_id),
        workspace_oid=workspace_oid,
        user_id=current_user_id,
        filled=filled,
    )
    if opts:
        need.options = opts
    halted = await _emit_and_persist_clarify(
        request=request,
        current_user_id=current_user_id,
        emit=emit,
        persist=persist,
        workspace_oid=workspace_oid,
        need=need,
        constraints=constraints,
        filled=filled,
        route=route,
        persist_question=persist_question,
    )
    halted.effective_question = effective_question
    halted.persist_question = persist_question
    return halted


async def _emit_and_persist_clarify(
    *,
    request: ChatRequest,
    current_user_id: int,
    emit: EmitCallback,
    persist: bool,
    workspace_oid: int,
    need: ClarificationNeed,
    constraints: _RunConstraints,
    filled: dict[str, str],
    route: ReportRoute,
    persist_question: str,
) -> ClarifyTurnResult:
    payload = need.to_payload()
    await emit("clarify", payload)
    await emit("summary", {"content": need.prompt})
    record_id = 0
    if persist:
        record_id = await _persist_async(
            request=request,
            current_user_id=current_user_id,
            question=persist_question,
            sql="",
            sql_error=None,
            exec_result=payload,
            is_success=True,
            reasoning=need.prompt,
            steps=[],
            chart_type="table",
            chart_config=None,
            agent_mode=request.agent_mode,
            summary=need.prompt,
            workspace_oid=workspace_oid,
        )
    return ClarifyTurnResult(
        halted=True,
        record_id=record_id,
        constraints=constraints,
        filled=filled,
        route=route,
        persist_question=persist_question,
    )


__all__ = ["ClarifyTurnResult", "maybe_clarify_turn"]
