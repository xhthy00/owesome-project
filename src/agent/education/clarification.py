"""追问闸门：规则抽槽与候选，LLM 判定要不要问。"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Mapping

from src.agent.education.report_types import ReportType
from src.agent.util.json_parser import parse_json_tolerant

logger = logging.getLogger(__name__)

ChatFn = Callable[[list[dict[str, str]]], Awaitable[str]]

SLOT_EXAM = "exam_name"
SLOT_CLASS = "class_name"
SLOT_STUDENT = "student_id"
SLOT_SUBJECT = "subject_name"
SLOT_SCHOOL = "school_name"
SLOT_SCOPE = "scope"

_GENERIC_EXAM_TOKENS = frozenset({"期中", "期末", "月考", "摸底", "模拟", "单元测验"})
_SCOPE_OPTIONS = ["全市", "全校", "指定班级"]
_SCOPE_MARKERS = ("全市", "全校", "各班", "各班级", "本校", "我校")
_VAGUE_OVERALL_HINTS = ("整体情况", "整体", "分析一下", "情况如何", "考得怎么样")
_NEW_QUESTION_HINTS = ("报告", "分析", "总览", "诊断", "预警", "画像")
_NEW_QUESTION_SWITCH = ("另外", "换一个", "不要这个", "新的问题", "重新问")
_HARD_SLOTS = frozenset({SLOT_EXAM, SLOT_CLASS, SLOT_SCOPE})
_BUREAU_TYPES = frozenset(
    {
        ReportType.LINE_REACH,
        ReportType.SUBJECT_AVG,
        ReportType.ASSIGN_GRADE,
        ReportType.RANK_BUCKET,
        ReportType.CONTRIBUTION,
        ReportType.COMBO_REACH,
        ReportType.ELITE_ROSTER,
        ReportType.SCORE_BAND,
        ReportType.DIAGNOSTIC_REPORT,
    }
)
_CLASS_REQUIRED_TYPES = frozenset(
    {
        ReportType.CLASS_OVERVIEW,
        ReportType.TIER_ALERT,
        ReportType.COMPREHENSIVE,
    }
)
_SUBJECT_REQUIRED_TYPES = frozenset(
    {
        ReportType.SUBJECT_DIAGNOSIS,
        ReportType.DIFFICULTY_CURVE,
    }
)
_PROMPT_BY_SLOT = {
    SLOT_SCOPE: "请确认分析范围：全市、全校，还是某个班级？",
    SLOT_EXAM: "要继续分析需要确认考试名称。请直接回复考试专名（不要回复「本次考试」）。",
    SLOT_CLASS: "请指定班级，例如「高三(10)班」。",
    SLOT_STUDENT: "请指定学生学号，例如「学生001」。",
    SLOT_SUBJECT: "请点名科目，例如「数学」。未点名科目不默认语文。",
    SLOT_SCHOOL: "请指定学校。本报告按一校生成，不默认全市、不循环多校。",
}


def _prompt_for_slot(slot: str, filled: Mapping[str, str] | None = None) -> str:
    filled_map = dict(filled or {})
    if slot == SLOT_SCHOOL:
        cls = str(filled_map.get(SLOT_CLASS) or "").strip()
        if cls:
            return f"请确认是哪所学校的{cls}？同名班级可能出现在多所学校。"
    return _PROMPT_BY_SLOT.get(slot, "请补充分析范围后再试。")


_SLOT_LABEL = {
    SLOT_SCOPE: "范围",
    SLOT_EXAM: "考试",
    SLOT_CLASS: "班级",
    SLOT_STUDENT: "学生",
    SLOT_SUBJECT: "科目",
    SLOT_SCHOOL: "学校",
}
_LABEL_TO_SLOT = {v: k for k, v in _SLOT_LABEL.items()}
_SUPPLEMENT_RE = re.compile(r"补充：([^：:=]+)[=：:]([^。]+)")
_JUDGE_SYSTEM = (
    "你是教育学情追问判定器。根据已抽取槽位判断是否必须向用户追问才能避免猜范围。\n"
    "只输出一个 JSON 对象，不要 Markdown：\n"
    '{"need_clarify":true或false,"slot":"<候选之一或null>","prompt":"一句中文追问或空串"}\n'
    "规则：\n"
    "- need_clarify=false：已能安全作答或出报告（用户已说全市/全校/各班、"
    "权限已唯一绑定、事实短问不依赖班级）\n"
    "- need_clarify=true：slot 必须来自候选列表，一轮只问一个；"
    "prompt 一句中文，不要提 SQL/工具/Agent\n"
    "- 禁止编造不在问句、不在权限里的班级/学校/考试名\n"
    "- 已有班级但未点名学校、且权限未唯一绑校 → 必须追问 school_name"
    "（多校可有同名班，不能默认某一所）\n"
    "- 达线/分数线/预测线未点考试专名且候选含 exam_name → 必须追问 exam_name，"
    "禁止默认最近一场或某一场考试\n"
    "- 拿不准且候选非空 → need_clarify=true\n"
)


@dataclass
class ClarificationNeed:
    """一次追问。"""

    prompt: str
    missing: list[str]
    options: list[str] = field(default_factory=list)
    filled: dict[str, str] = field(default_factory=dict)
    original_question: str = ""
    report_type: str | None = None

    def to_payload(self) -> dict[str, Any]:
        slot = self.missing[0] if self.missing else SLOT_SCOPE
        return {
            "clarify": True,
            "prompt": self.prompt,
            "missing": list(self.missing),
            "options": {slot: list(self.options)},
            "filled": dict(self.filled),
            "original_question": self.original_question,
            "report_type": self.report_type,
        }


def _route_type(route: Any) -> ReportType | None:
    if route is None:
        return None
    raw: Any = route
    if hasattr(route, "report_type"):
        raw = getattr(route, "report_type")
    elif isinstance(route, Mapping):
        raw = route.get("report_type")
    if isinstance(raw, ReportType):
        return raw
    if raw:
        try:
            return ReportType(str(raw).strip())
        except ValueError:
            return None
    return None


def _needs_report(route: Any) -> bool:
    if route is None:
        return False
    if hasattr(route, "needs_report"):
        return bool(getattr(route, "needs_report"))
    if isinstance(route, Mapping):
        return bool(route.get("needs_report"))
    return False


def _edu(edu_scope: Mapping[str, Any] | None) -> dict[str, Any]:
    return dict(edu_scope) if isinstance(edu_scope, Mapping) else {}


def _unique_bound_classes(edu_scope: Mapping[str, Any] | None) -> list[str]:
    edu = _edu(edu_scope)
    raw = edu.get("class_names")
    if not isinstance(raw, list):
        return []
    return [str(x).strip() for x in raw if str(x).strip()]


def _bound_school(edu_scope: Mapping[str, Any] | None) -> str:
    edu = _edu(edu_scope)
    return (str(edu.get("school_name") or "").strip()) or (
        str(edu.get("school_id") or "").strip()
    )


def _exam_missing(question: str, filled: Mapping[str, str]) -> bool:
    from src.agent.education.query_parse import extract_exam_name_hint, is_vague_exam_name

    hint = str(filled.get(SLOT_EXAM) or "").strip() or (extract_exam_name_hint(question) or "")
    if not hint or is_vague_exam_name(hint) or hint in _GENERIC_EXAM_TOKENS:
        return True
    return False


def _exam_needed(question: str, route: Any) -> bool:
    """报告、达线、单场分数统计等按场次出数的问句，未点考试就不能默选一场。"""
    from src.agent.education.query_parse import (
        is_line_reach_query,
        is_oral_score_inquiry,
        is_score_stat_query,
        is_subject_strength_query,
        refers_to_unspecified_exam,
    )

    if _needs_report(route) or _route_type(route) is not None:
        return True
    if is_vague_overall_query(question):
        return True
    return (
        is_line_reach_query(question)
        or is_score_stat_query(question)
        or is_oral_score_inquiry(question)
        or is_subject_strength_query(question)
        or refers_to_unspecified_exam(question)
    )


def _first_hard_slot(candidates: list[str]) -> str | None:
    """exam/class/scope 必须追问，LLM 不能否决。"""
    for slot in candidates:
        if slot in _HARD_SLOTS:
            return slot
    return None


def _has_wide_scope(question: str) -> bool:
    from src.agent.education.query_parse import (
        is_all_schools_scope_query,
        is_citywide_analysis_query,
        is_school_class_comparison_query,
    )

    q = question or ""
    if any(m in q for m in _SCOPE_MARKERS):
        return True
    return bool(
        is_citywide_analysis_query(q)
        or is_all_schools_scope_query(q)
        or is_school_class_comparison_query(q)
    )


def is_vague_overall_query(question: str) -> bool:
    """事实向的模糊整体分析：未给全市/全校/班级。"""
    from src.agent.education.query_parse import extract_class_target

    q = (question or "").strip()
    if not q or extract_class_target(q) or _has_wide_scope(q):
        return False
    return any(h in q for h in _VAGUE_OVERALL_HINTS)


def extract_filled_slots(
    question: str,
    edu_scope: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """从问句与权限抽出已填槽（不编造）。"""
    from src.agent.education.orchestrator import _extract_subject
    from src.agent.education.query_parse import (
        extract_class_target,
        extract_exam_name_hint,
        extract_school_target,
        extract_student_target,
        is_citywide_analysis_query,
        is_vague_exam_name,
    )

    q = question or ""
    edu = _edu(edu_scope)
    role = str(edu.get("edu_role") or "").strip()
    filled: dict[str, str] = {}

    school = extract_school_target(q) or ""
    if role in ("teacher", "school_admin") and not is_citywide_analysis_query(q):
        bound = _bound_school(edu)
        if bound:
            school = bound
    if school:
        filled[SLOT_SCHOOL] = school

    cls = extract_class_target(q) or ""
    bound_classes = _unique_bound_classes(edu)
    if not cls and len(bound_classes) == 1:
        cls = bound_classes[0]
    if not cls and bound_classes:
        from src.agent.education.query_parse import extract_bare_class_number

        num = extract_bare_class_number(q)
        if num:
            hits = [c for c in bound_classes if f"({num})班" in c or c.endswith(f"{num}班")]
            if len(hits) == 1:
                cls = hits[0]
    if cls:
        filled[SLOT_CLASS] = cls

    student = extract_student_target(q) or ""
    if role == "student" and edu.get("student_id"):
        student = str(edu.get("student_id")).strip() or student
    if student:
        filled[SLOT_STUDENT] = student

    exam = extract_exam_name_hint(q) or ""
    if exam and not is_vague_exam_name(exam) and exam not in _GENERIC_EXAM_TOKENS:
        filled[SLOT_EXAM] = exam

    subject = (_extract_subject(q) or "").strip()
    if subject:
        filled[SLOT_SUBJECT] = subject

    scope = _scope_from_text(q)
    if scope:
        filled[SLOT_SCOPE] = scope

    for slot, value in _supplements_from_text(q).items():
        if value:
            filled[slot] = value
    return filled


def _supplements_from_text(question: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for m in _SUPPLEMENT_RE.finditer(question or ""):
        slot = _LABEL_TO_SLOT.get(m.group(1).strip())
        value = (m.group(2) or "").strip()
        if slot and value:
            out[slot] = value
    return out


def _scope_from_text(text: str) -> str:
    t = text or ""
    if "全市" in t:
        return "全市"
    if "全校" in t or "本校" in t or "我校" in t:
        return "全校"
    if any(h in t for h in ("各班", "各班级", "指定班级")):
        return "指定班级"
    return ""


def candidate_missing_slots(
    route: Any,
    question: str,
    filled: Mapping[str, str],
    edu_scope: Mapping[str, Any] | None = None,
) -> list[str]:
    """若不问就可能猜错的槽，按优先级去重。"""
    from src.agent.education.query_parse import (
        has_class_alias,
        is_bureau_report_query,
        is_citywide_analysis_query,
        is_class_weak_subject_query,
        is_line_reach_query,
        is_oral_score_inquiry,
        is_school_class_comparison_query,
        is_score_stat_query,
        is_subject_research_report_query,
        is_subject_strength_query,
    )

    q = question or ""
    rt = _route_type(route)
    needs = _needs_report(route)
    filled_map = dict(filled)
    edu = _edu(edu_scope)
    role = str(edu.get("edu_role") or "").strip()
    out: list[str] = []

    def add(slot: str) -> None:
        if slot and slot not in out:
            out.append(slot)

    if is_vague_overall_query(q) and not needs:
        add(SLOT_SCOPE)

    skip_class = bool(
        rt in _BUREAU_TYPES
        or rt == ReportType.GRADE_COMPARISON
        or is_citywide_analysis_query(q)
        or is_school_class_comparison_query(q)
        or is_bureau_report_query(q)
        or _has_wide_scope(q)
    )

    exam_needed = _exam_needed(q, route)
    if exam_needed and _exam_missing(q, filled_map):
        add(SLOT_EXAM)

    fact_needs_class = (
        not skip_class
        and not filled_map.get(SLOT_CLASS)
        and (
            is_score_stat_query(q)
            or is_oral_score_inquiry(q)
            or is_line_reach_query(q)
            or is_subject_strength_query(q)
            or has_class_alias(q)
        )
        and (
            has_class_alias(q)
            or (role == "teacher" and len(_unique_bound_classes(edu)) > 1)
        )
    )
    class_needed = (
        rt in _CLASS_REQUIRED_TYPES
        or (rt is None and is_class_weak_subject_query(q))
        or fact_needs_class
    )
    if class_needed and not skip_class and not filled_map.get(SLOT_CLASS):
        add(SLOT_CLASS)

    if rt == ReportType.STUDENT_PROFILE and not filled_map.get(SLOT_STUDENT):
        add(SLOT_STUDENT)

    if rt in _SUBJECT_REQUIRED_TYPES and not filled_map.get(SLOT_SUBJECT):
        add(SLOT_SUBJECT)

    research = rt == ReportType.SUBJECT_RESEARCH or is_subject_research_report_query(q)
    if research and not filled_map.get(SLOT_SCHOOL):
        bound = _bound_school(edu)
        if not bound or role not in ("teacher", "school_admin"):
            add(SLOT_SCHOOL)

    if class_needed and not skip_class and not filled_map.get(SLOT_SCHOOL):
        bound = _bound_school(edu)
        if not bound or role not in ("teacher", "school_admin", "student"):
            add(SLOT_SCHOOL)

    return out


def fallback_clarification(
    question: str,
    candidates: list[str],
    filled: Mapping[str, str],
    *,
    report_type: str | None = None,
    options: list[str] | None = None,
) -> ClarificationNeed | None:
    """LLM 失败时取候选第一个槽。"""
    if not candidates:
        return None
    slot = candidates[0]
    opts = list(options or [])
    if slot == SLOT_SCOPE and not opts:
        opts = list(_SCOPE_OPTIONS)
    return ClarificationNeed(
        prompt=_prompt_for_slot(slot, filled),
        missing=[slot],
        options=opts,
        filled=dict(filled),
        original_question=question,
        report_type=report_type,
    )


def default_options_for_slot(slot: str) -> list[str]:
    if slot == SLOT_SCOPE:
        return list(_SCOPE_OPTIONS)
    return []


def _parse_judge(raw: str, candidates: list[str]) -> tuple[bool, str | None, str]:
    parsed = parse_json_tolerant(raw)
    if not isinstance(parsed, dict):
        raise ValueError("judge json is not an object")
    need_raw = parsed.get("need_clarify")
    if isinstance(need_raw, bool):
        need = need_raw
    else:
        need = str(need_raw or "").strip().lower() in {"true", "1", "yes", "y", "是"}
    slot = str(parsed.get("slot") or "").strip() or None
    if slot in {"null", "none", "None"}:
        slot = None
    prompt = str(parsed.get("prompt") or "").strip()
    if not need:
        return False, None, ""
    if slot not in candidates:
        raise ValueError(f"slot {slot!r} not in candidates")
    return True, slot, prompt


def build_judge_messages(
    question: str,
    *,
    needs_report: bool,
    report_type: str | None,
    filled: Mapping[str, str],
    candidates: list[str],
    edu_scope: Mapping[str, Any] | None = None,
) -> list[dict[str, str]]:
    edu = _edu(edu_scope)
    role = str(edu.get("edu_role") or "") or "unknown"
    n_class = len(_unique_bound_classes(edu))
    school = _bound_school(edu) or "无"
    filled_txt = ", ".join(f"{k}={v}" for k, v in filled.items()) or "无"
    user = (
        f"用户问题：{question}\n"
        f"needs_report={str(needs_report).lower()} report_type={report_type or 'null'}\n"
        f"已填槽：{filled_txt}\n"
        f"候选缺失槽：{', '.join(candidates)}\n"
        f"权限：角色={role} 绑定学校={school} 绑定班级数={n_class}\n"
    )
    return [
        {"role": "system", "content": _JUDGE_SYSTEM},
        {"role": "user", "content": user},
    ]


async def judge_clarification(
    question: str,
    *,
    route: Any = None,
    filled: Mapping[str, str] | None = None,
    candidates: list[str] | None = None,
    edu_scope: Mapping[str, Any] | None = None,
    chat_fn: ChatFn | None = None,
    options_by_slot: Mapping[str, list[str]] | None = None,
) -> ClarificationNeed | None:
    """判定是否追问。候选为空则放行；LLM 失败回落规则。"""
    filled_map = dict(filled or extract_filled_slots(question, edu_scope))
    cand = list(candidates if candidates is not None else candidate_missing_slots(
        route, question, filled_map, edu_scope
    ))
    rt = _route_type(route)
    rt_value = rt.value if rt else None
    if not cand:
        return None

    def _need_for(slot: str, prompt: str) -> ClarificationNeed:
        opts: list[str] = []
        if options_by_slot and slot in options_by_slot:
            opts = list(options_by_slot.get(slot) or [])
        elif slot == SLOT_SCOPE:
            opts = list(_SCOPE_OPTIONS)
        return ClarificationNeed(
            prompt=prompt,
            missing=[slot],
            options=opts,
            filled=filled_map,
            original_question=question,
            report_type=rt_value,
        )

    if chat_fn is None:
        return fallback_clarification(question, cand, filled_map, report_type=rt_value)

    messages = build_judge_messages(
        question,
        needs_report=_needs_report(route),
        report_type=rt_value,
        filled=filled_map,
        candidates=cand,
        edu_scope=edu_scope,
    )
    try:
        raw = await chat_fn(messages)
        need, slot, prompt = _parse_judge(raw, cand)
    except Exception as exc:  # noqa: BLE001
        logger.warning("clarification judge failed, fallback rules: %s", exc)
        return fallback_clarification(question, cand, filled_map, report_type=rt_value)
    if not need or not slot:
        hard = _first_hard_slot(cand)
        if hard:
            return fallback_clarification(
                question, [hard], filled_map, report_type=rt_value
            )
        return None
    if not prompt:
        prompt = _prompt_for_slot(slot, filled_map)
    return _need_for(slot, prompt)


def _fills_missing(pending: Mapping[str, Any], user_text: str) -> str:
    missing = ""
    raw_missing = pending.get("missing") or []
    if isinstance(raw_missing, list) and raw_missing:
        missing = str(raw_missing[0])
    text = (user_text or "").strip()
    options = pending.get("options") if isinstance(pending.get("options"), dict) else {}
    slot_opts = list(options.get(missing) or []) if missing else []
    if text in slot_opts:
        return text
    extracted = extract_filled_slots(text, None)
    if missing == SLOT_SCOPE:
        return _scope_from_text(text) or (text if text in _SCOPE_OPTIONS else "")
    if missing and extracted.get(missing):
        return extracted[missing]
    if missing == SLOT_CLASS and extracted.get(SLOT_CLASS):
        return extracted[SLOT_CLASS]
    return ""


def _looks_like_new_question(pending: Mapping[str, Any], user_text: str) -> bool:
    text = (user_text or "").strip()
    if not text:
        return False
    if _fills_missing(pending, text):
        return False
    if any(h in text for h in _NEW_QUESTION_SWITCH):
        return True
    if len(text) < 24:
        return False
    if not any(h in text for h in _NEW_QUESTION_HINTS):
        return False
    from src.agent.education.query_parse import extract_class_target, extract_school_target

    return bool(extract_class_target(text) and extract_school_target(text))


def merge_clarification_reply(pending: Mapping[str, Any], user_text: str) -> str:
    """把用户补充拼回原问；新问题则丢弃 pending。"""
    text = (user_text or "").strip()
    original = str(pending.get("original_question") or "").strip()
    if not pending.get("clarify") or not original:
        return text
    if _looks_like_new_question(pending, text):
        return text
    missing = ""
    raw_missing = pending.get("missing") or []
    if isinstance(raw_missing, list) and raw_missing:
        missing = str(raw_missing[0])
    value = _fills_missing(pending, text) or text
    label = _SLOT_LABEL.get(missing, missing or "补充")
    return f"{original}。补充：{label}={value}"


def parse_pending_clarify(exec_result: Any) -> dict[str, Any] | None:
    """从落库 exec_result 读 pending 追问。"""
    import json

    data = exec_result
    if isinstance(data, str) and data.strip():
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            return None
    if isinstance(data, dict) and data.get("clarify"):
        return data
    return None


__all__ = [
    "ClarificationNeed",
    "SLOT_CLASS",
    "SLOT_EXAM",
    "SLOT_SCHOOL",
    "SLOT_SCOPE",
    "SLOT_STUDENT",
    "SLOT_SUBJECT",
    "candidate_missing_slots",
    "default_options_for_slot",
    "extract_filled_slots",
    "fallback_clarification",
    "is_vague_overall_query",
    "judge_clarification",
    "merge_clarification_reply",
    "parse_pending_clarify",
]
