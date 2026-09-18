"""Chat 入口的追问闸门：合并 pending、判定、SSE、落库。"""

from __future__ import annotations

import logging
from collections import OrderedDict
from dataclasses import dataclass, field
from time import monotonic
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
    sanitize_llm_slots,
)
from src.agent.education.intent_detect import EduIntent
from src.agent.education.intent_router import ReportRoute, classify_and_extract
from src.agent.education.query_frame import QueryFrame
from src.chat.schemas import ChatRequest
from src.chat.service.agent_runner import (
    EmitCallback,
    _build_shared_constraints,
    _persist_async,
    _RunConstraints,
)
from src.common.core.config import get_settings

logger = logging.getLogger(__name__)

_CATALOG_CACHE_TTL_SECONDS = 60.0
_CATALOG_CACHE_MAX_SIZE = 256
_CATALOG_CACHE: OrderedDict[
    tuple[int, int, int, tuple[tuple[str, str], ...]],
    tuple[float, dict[str, list[str]]],
] = OrderedDict()

_NEW_QUESTION_PREFIXES = (
    "换个问题",
    "换一个问题",
    "新问题",
    "另外问",
    "重新提问",
    "忽略上面",
)


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
    pending_payload: dict[str, Any] | None = None


def _load_pending(
    conversation_id: int | None,
    user_id: int,
    workspace_oid: int,
) -> dict[str, Any] | None:
    if not conversation_id:
        return None
    try:
        from src.chat.crud.agent_run import get_last_agent_run
        from src.chat.crud.chat import get_latest_conversation_record
        from src.common.core.database import get_db_session

        with get_db_session() as session:
            waiting = get_last_agent_run(
                session,
                conversation_id=int(conversation_id),
                user_id=int(user_id),
                workspace_oid=int(workspace_oid),
            )
            if waiting is not None and waiting.pending_payload:
                pending = parse_pending_clarify(waiting.pending_payload)
                if pending is not None:
                    return pending
            rec = get_latest_conversation_record(session, int(conversation_id), int(user_id))
            if rec is None:
                return None
            return parse_pending_clarify(rec.exec_result)
    except Exception as exc:  # noqa: BLE001
        logger.warning("load pending clarify failed: %s", exc)
        return None


def _attach_direction_choices(
    constraints: _RunConstraints,
    question: str,
    route: ReportRoute,
) -> None:
    from src.agent.education.direction_choice import (
        compute_pending_choices,
        da_question_mode,
        log_shadow_choices,
    )

    if da_question_mode() == "off":
        return
    pending = compute_pending_choices(
        question,
        route,
        constraints.edu_scope,
        query_frame=constraints.query_frame,
    )
    if not pending:
        return
    constraints.pending_choices = [item.to_dict() for item in pending]
    if da_question_mode() == "shadow":
        log_shadow_choices(question, pending)


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


def _recent_history(
    conversation_id: int | None,
    user_id: int,
    edu_scope: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    """过滤后的短背景，仅供 Intent 补槽，不重放 SQLBot role history。"""
    if not conversation_id:
        return []
    try:
        from src.chat.service.conversation_context import load_turn_context

        brief = load_turn_context(int(conversation_id), int(user_id), edu_scope).brief
        if brief:
            return [{"role": "system", "content": brief}]
    except Exception as exc:  # noqa: BLE001
        logger.warning("load intent history failed: %s", exc)
    return []


def _canonicalize_filled_slots(filled: dict[str, str]) -> dict[str, str]:
    """合并后兜底：剥口语「学校」尾巴，丢掉「下降的学校」这类描述。"""
    out = dict(filled or {})
    school = str(out.get(SLOT_SCHOOL) or "").strip()
    if school:
        from src.agent.education.query_parse import (
            _is_descriptive_school_phrase,
            is_interrogative_school_name,
            peel_rhetorical_school_suffix,
        )

        peeled = peel_rhetorical_school_suffix(school)
        if (
            not peeled
            or _is_descriptive_school_phrase(peeled)
            or is_interrogative_school_name(peeled)
        ):
            out.pop(SLOT_SCHOOL, None)
        else:
            out[SLOT_SCHOOL] = peeled
    return out


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
    filled = _canonicalize_filled_slots(filled)
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
        # 校名过滤仍空时放开学校条件再拉一次（权限 SQL 仍会收敛）
        if not out and slot == "exam_name" and filled.get(SLOT_SCHOOL):
            options = await _load_meta_options(
                orch,
                school_name=None,
                exam_name=filled.get("exam_name") or None,
                class_name=filled.get(SLOT_CLASS) or None,
                subject=filled.get("subject_name") or None,
                edu_scope=EduScope.from_dict(edu),
            )
            raw = options.get(key) or []
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
    normalized = _canonicalize_filled_slots(filled)
    cache_key = (
        int(datasource_id),
        int(workspace_oid),
        int(user_id),
        tuple(sorted((str(key), str(value)) for key, value in normalized.items() if value)),
    )
    cached = _CATALOG_CACHE.get(cache_key)
    now = monotonic()
    if cached and now - cached[0] < _CATALOG_CACHE_TTL_SECONDS:
        _CATALOG_CACHE.move_to_end(cache_key)
        return {key: list(values) for key, values in cached[1].items()}
    if cached:
        _CATALOG_CACHE.pop(cache_key, None)
    try:
        from datasource.service.edu_permission import EduScope, edu_scope_dict_for_user_id
        from src.agent.education.api import _build_orchestrator, _load_meta_options

        orch = _build_orchestrator(datasource_id, workspace_oid, user_id=user_id)
        edu = edu_scope_dict_for_user_id(user_id)
        result = await _load_meta_options(
            orch,
            school_name=normalized.get(SLOT_SCHOOL) or None,
            exam_name=normalized.get(SLOT_EXAM) or None,
            class_name=normalized.get(SLOT_CLASS) or None,
            subject=normalized.get(SLOT_SUBJECT) or None,
            edu_scope=EduScope.from_dict(edu),
        )
        if result:
            copied = {key: list(values) for key, values in result.items()}
            _CATALOG_CACHE[cache_key] = (now, copied)
            _CATALOG_CACHE.move_to_end(cache_key)
            while len(_CATALOG_CACHE) > _CATALOG_CACHE_MAX_SIZE:
                _CATALOG_CACHE.popitem(last=False)
        return result
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
    intent: EduIntent | None = None,
    query_frame: QueryFrame | None = None,
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
    from src.agent.education.entity_resolve import resolve_entities, resolve_query_frame

    result = (
        resolve_query_frame(query_frame, catalog, edu_scope=constraints.edu_scope)
        if query_frame is not None
        else resolve_entities(filled, catalog, edu_scope=constraints.edu_scope)
    )
    logger.info(
        "edu_entity_resolve outcome=%s slot=%s option_count=%d diagnostics=%s",
        "clarify" if result.clarify_slot else "bound",
        result.clarify_slot or "",
        len(result.options),
        list(result.frame.diagnostics) if result.frame is not None else [],
    )
    if result.frame is not None:
        constraints.query_frame = result.frame.to_dict()
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
            effective_question=effective_question,
            intent=intent,
        )
    changed = {
        k: v
        for k, v in result.bound.items()
        if v and str(filled.get(k) or "").strip() != v
    }
    if query_frame is not None:
        filled.clear()
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
    """合并 pending → 分类 → 抽槽 → 规则追问。需追问则 SSE + 落库并 halted。

    非教育问句且无 pending 时直接放行（通用 BI 不追问）。
    """
    from src.agent.education.prompt_context import is_education_question
    from src.chat.service.conversation_context import (
        apply_inherited_supplements,
        extra_inherited_slots,
        is_follow_up_question,
        load_turn_context,
        merge_inherited_slots,
    )

    is_new_question = str(request.question or "").strip().lower().startswith(
        _NEW_QUESTION_PREFIXES
    )
    pending = (
        None
        if is_new_question
        else _load_pending(request.conversation_id, current_user_id, workspace_oid)
    )
    if pending:
        request.question = merge_clarification_reply(pending, request.question)
    persist_question = request.question

    # 通用 BI：无 pending 且非教育场景 → 不追问
    if not pending and not is_education_question(request.question):
        constraints = _build_shared_constraints(request.question, current_user_id)
        constraints.report_audience = request.report_audience
        constraints.user_utterance = persist_question
        return ClarifyTurnResult(
            halted=False,
            constraints=constraints,
            effective_question=request.question,
            persist_question=persist_question,
        )

    constraints = _build_shared_constraints(request.question, current_user_id)
    constraints.report_audience = request.report_audience
    constraints.user_utterance = persist_question
    constraints.is_retry_chat = bool(pending)
    should_inherit = bool(pending) or is_follow_up_question(persist_question)

    edu = constraints.edu_scope or {}
    route, intent = await classify_and_extract(
        request.question,
        llm_client,
        history=(
            _recent_history(request.conversation_id, current_user_id, edu)
            if should_inherit
            else []
        ),
        edu_scope=edu,
        prev_intent=pending.get("intent") if pending else None,
    )
    constraints.report_route = route.to_dict()

    rule_filled = extract_filled_slots(request.question, edu)
    llm_filled = sanitize_llm_slots(intent.slots)
    legacy_current = {**rule_filled, **llm_filled}
    turn_ctx = (
        load_turn_context(request.conversation_id, current_user_id, edu)
        if should_inherit
        else None
    )
    if should_inherit:
        legacy_filled = merge_inherited_slots(
            legacy_current, turn_ctx.inherited if turn_ctx else {}, request.question
        )
    else:
        legacy_filled = dict(legacy_current)

    from src.agent.education.slot_merge import merge_turn_slots

    current_semantic = merge_turn_slots(
        request.question,
        rule_slots=rule_filled,
        llm_slots=llm_filled,
        llm_mentions=intent.mentions,
        llm_answer_dimensions=intent.answer_dimensions,
        llm_exam_window=intent.exam_window,
        edu_scope=edu,
    )
    semantic = merge_turn_slots(
        request.question,
        rule_slots=rule_filled,
        llm_slots=llm_filled,
        llm_mentions=intent.mentions,
        llm_answer_dimensions=intent.answer_dimensions,
        llm_exam_window=intent.exam_window,
        inherited=turn_ctx.inherited if turn_ctx else None,
        edu_scope=edu,
    )
    slot_mode = get_settings().edu_slot_mode
    if slot_mode == "semantic":
        current_filled = current_semantic.filled
        filled = semantic.filled
    else:
        current_filled = legacy_current
        filled = legacy_filled
        if slot_mode == "shadow" and filled != semantic.filled:
            logger.info(
                "edu_slot_shadow_diff legacy=%s semantic=%s diagnostics=%s",
                sorted(filled),
                sorted(semantic.filled),
                semantic.frame.diagnostics,
            )
    constraints.query_frame = semantic.frame.to_dict()
    filled = _canonicalize_filled_slots(filled)
    extra = extra_inherited_slots(current_filled, filled)
    # user_input 是 LLM 补全后的完整指令；「补充：标签=值」标记必须保留——
    # 下游 sub_task 与工具会对 user_question 做正则抽取，纯改写会打断这条链路。
    # 有确定性继承槽时保留用户原问，避免 LLM user_input 中的模糊/缩写值覆盖真值。
    base_question = request.question
    if slot_mode != "semantic" and not extra and intent.ok and intent.user_input:
        base_question = intent.user_input
    effective_question = apply_inherited_supplements(base_question, extra)
    _apply_filled(constraints, filled)
    candidates = candidate_missing_slots(
        route,
        request.question,
        filled,
        edu,
        frame=semantic.frame if slot_mode == "semantic" else None,
    )

    def _pass(*, halted: bool, record_id: int = 0) -> ClarifyTurnResult:
        if not halted:
            _attach_direction_choices(constraints, persist_question, route)
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
            intent=intent,
            query_frame=semantic.frame if slot_mode == "semantic" else None,
        )
        if linked is not None:
            if not linked.halted and linked.constraints is not None:
                _attach_direction_choices(
                    linked.constraints, persist_question, route
                )
            return linked
        return _pass(halted=False)

    need = await judge_clarification(
        request.question,
        route=route,
        filled=filled,
        candidates=candidates,
        edu_scope=edu,
        llm_ask_user=intent.ask_user,
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
            intent=intent,
            query_frame=semantic.frame if slot_mode == "semantic" else None,
        )
        if linked is not None:
            if not linked.halted and linked.constraints is not None:
                _attach_direction_choices(
                    linked.constraints, persist_question, route
                )
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
        effective_question=effective_question,
        intent=intent,
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
    effective_question: str,
    intent: EduIntent | None = None,
) -> ClarifyTurnResult:
    payload = need.to_payload()
    if constraints.query_frame:
        payload["query_frame"] = dict(constraints.query_frame)
    if intent is not None and intent.ok:
        # 下一轮读回来当 prev_intent，走 DB-GPT 的"增量合并"而不是重新识别。
        payload["intent"] = intent.to_dict()
    await emit("clarify", payload)
    await emit("summary", {"content": need.prompt})
    record_id = 0
    if persist:
        record_id = await _persist_async(
            request=request,
            current_user_id=current_user_id,
            question=persist_question,
            resolved_question=effective_question,
            turn_type="retry" if constraints.is_retry_chat else "standalone",
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
        effective_question=effective_question,
        pending_payload=payload,
    )


__all__ = ["ClarifyTurnResult", "maybe_clarify_turn"]
