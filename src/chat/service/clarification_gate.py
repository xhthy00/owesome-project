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
    sanitize_llm_slots,
)
from src.agent.education.intent_detect import EduIntent
from src.agent.education.intent_router import ReportRoute, classify_and_extract
from src.chat.schemas import ChatRequest
from src.chat.service.agent_runner import (
    EmitCallback,
    _build_shared_constraints,
    _persist_async,
    _RunConstraints,
)

logger = logging.getLogger(__name__)

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
    """合并后兜底：剥口语「学校」尾巴，避免拉选项时 LIKE 命中 0 行。"""
    from src.agent.education.query_parse import peel_rhetorical_school_suffix

    out = dict(filled or {})
    school = str(out.get(SLOT_SCHOOL) or "").strip()
    if school:
        peeled = peel_rhetorical_school_suffix(school)
        if peeled:
            out[SLOT_SCHOOL] = peeled
        else:
            out.pop(SLOT_SCHOOL, None)
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
    intent: EduIntent | None = None,
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
            effective_question=effective_question,
            intent=intent,
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

    # LLM 抽槽优先，规则抽槽兜底并集：规则还负责权限派生的槽（绑定学校 / 唯一班级 /
    # 学生本人学号），那些 LLM 看不到；问句里明说的值以 LLM 为准。
    current_filled = extract_filled_slots(request.question, edu)
    current_filled.update(sanitize_llm_slots(intent.slots))
    if should_inherit:
        turn_ctx = load_turn_context(request.conversation_id, current_user_id, edu)
        filled = merge_inherited_slots(
            current_filled, turn_ctx.inherited, request.question
        )
    else:
        filled = dict(current_filled)
    filled = _canonicalize_filled_slots(filled)
    extra = extra_inherited_slots(current_filled, filled)
    # user_input 是 LLM 补全后的完整指令；「补充：标签=值」标记必须保留——
    # 下游 sub_task 与工具会对 user_question 做正则抽取，纯改写会打断这条链路。
    # 有确定性继承槽时保留用户原问，避免 LLM user_input 中的模糊/缩写值覆盖真值。
    base_question = request.question
    if not extra and intent.ok and intent.user_input:
        base_question = intent.user_input
    effective_question = apply_inherited_supplements(base_question, extra)
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
            intent=intent,
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
