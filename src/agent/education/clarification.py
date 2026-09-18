"""追问闸门：规则抽槽与候选，LLM 判定要不要问。"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from src.agent.education.report_types import ReportType
from src.agent.util.json_parser import parse_json_tolerant

logger = logging.getLogger(__name__)

SLOT_EXAM = "exam_name"
SLOT_CLASS = "class_name"
SLOT_STUDENT = "student_id"
SLOT_SUBJECT = "subject_name"
SLOT_SCHOOL = "school_name"
SLOT_SCOPE = "scope"

_GENERIC_EXAM_TOKENS = frozenset(
    {
        "期中",
        "期末",
        "月考",
        "摸底",
        "模拟",
        "单元测验",
        "期中考试",
        "期末考试",
        "月考考试",
        "模考",
        "联考",
        "统考",
    }
)
_SCOPE_OPTIONS = ["全市", "全校", "指定班级"]
_SCOPE_MARKERS = ("全市", "全校", "各班", "各班级", "本校", "我校")
_VAGUE_OVERALL_HINTS = (
    "整体情况",
    "整体",
    "分析一下",
    "情况如何",
    "考得怎么样",
    "看一下成绩",
    "看看成绩",
    "看下成绩",
    "出份报告",
    "出个报告",
    "生成报告",
    "学情",
    "考情",
    "成绩怎么样",
    "分析成绩",
    "帮我看看",
    "帮我分析",
)
_NEW_QUESTION_HINTS = ("报告", "分析", "总览", "诊断", "预警", "画像")
_NEW_QUESTION_SWITCH = ("另外", "换一个", "不要这个", "新的问题", "重新问")
_HARD_SLOTS = frozenset({SLOT_EXAM, SLOT_CLASS, SLOT_SCOPE, SLOT_SCHOOL, SLOT_SUBJECT, SLOT_STUDENT})
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
        ReportType.GROUP_FEATURE,
    }
)
_SUBJECT_REQUIRED_TYPES = frozenset(
    {
        ReportType.SUBJECT_DIAGNOSIS,
        ReportType.DIFFICULTY_CURVE,
        ReportType.SUBJECT_RESEARCH,
    }
)

#: 报告类型 → 必填槽（规则矩阵，少漏判优先）
REQUIRED_SLOTS_BY_REPORT: dict[ReportType, tuple[str, ...]] = {
    ReportType.CLASS_OVERVIEW: (SLOT_EXAM, SLOT_CLASS),
    ReportType.GRADE_COMPARISON: (SLOT_EXAM,),
    ReportType.SUBJECT_DIAGNOSIS: (SLOT_EXAM, SLOT_SUBJECT, SLOT_CLASS),
    ReportType.STUDENT_PROFILE: (SLOT_STUDENT,),
    ReportType.TREND_TRACKING: (SLOT_EXAM,),
    ReportType.TIER_ALERT: (SLOT_EXAM, SLOT_CLASS),
    ReportType.GROUP_FEATURE: (SLOT_EXAM, SLOT_CLASS),
    ReportType.COMPREHENSIVE: (SLOT_EXAM, SLOT_CLASS),
    ReportType.DIAGNOSTIC_REPORT: (SLOT_EXAM,),
    ReportType.LINE_REACH: (SLOT_EXAM,),
    ReportType.SUBJECT_AVG: (SLOT_EXAM,),
    ReportType.ASSIGN_GRADE: (SLOT_EXAM,),
    ReportType.RANK_BUCKET: (SLOT_EXAM,),
    ReportType.CONTRIBUTION: (SLOT_EXAM,),
    ReportType.COMBO_REACH: (SLOT_EXAM,),
    ReportType.ELITE_ROSTER: (SLOT_EXAM,),
    ReportType.SCORE_BAND: (SLOT_EXAM,),
    ReportType.SUBJECT_RESEARCH: (SLOT_EXAM, SLOT_SUBJECT, SLOT_SCHOOL),
    ReportType.DIFFICULTY_CURVE: (SLOT_EXAM, SLOT_SUBJECT),
}


class FactQueryKind(str, Enum):
    """事实问口径——不出报告时的「意图」，用来查必填槽。

    报告有 ``REQUIRED_SLOTS_BY_REPORT``，事实问以前只有一堆分散的启发式分支，
    缺哪个槽全看正则命中顺序。这个枚举把事实问也拉回「意图 → 必填槽」查表。
    """

    ITEM_DIAGNOSIS = "item_diagnosis"          # 小题/知识点失分
    SUBJECT_RESEARCH = "subject_research"      # 一校一场学科教研
    CLASS_WEAK_SUBJECT = "class_weak_subject"  # 指定班级薄弱学科
    SUBJECT_STRENGTH = "subject_strength"      # 优势/薄弱学科（各科均分排名）
    CONTRIBUTION_WEAK = "contribution_weak"    # 贡献分差值薄弱学科
    LINE_REACH = "line_reach"                  # 达线/预测线人数或率
    SCORE_STAT = "score_stat"                  # 单场均分/最高分/口语分数问
    RANK = "rank"                              # 单场排名（班级/学校全市名次）
    VAGUE_OVERALL = "vague_overall"            # 模糊整体（帮我看看/学情如何）
    UNSPECIFIED_EXAM = "unspecified_exam"      # 用「本次/这场」指代但没给专名


#: 事实问口径 → 必填槽。「必填」指口径本身按这个维度出数，不给就得猜。
#: 用户已给宽范围（全市/各校）或权限已唯一绑定时，由 ``candidate_missing_slots``
#: 的豁免层减掉，不在本表里开口子。
REQUIRED_SLOTS_BY_FACT: dict[FactQueryKind, tuple[str, ...]] = {
    FactQueryKind.ITEM_DIAGNOSIS: (SLOT_EXAM, SLOT_SUBJECT, SLOT_SCHOOL, SLOT_CLASS),
    FactQueryKind.SUBJECT_RESEARCH: (SLOT_EXAM, SLOT_SUBJECT, SLOT_SCHOOL),
    FactQueryKind.CLASS_WEAK_SUBJECT: (SLOT_EXAM, SLOT_SCHOOL, SLOT_CLASS),
    FactQueryKind.SUBJECT_STRENGTH: (SLOT_EXAM, SLOT_SCHOOL, SLOT_CLASS),
    FactQueryKind.CONTRIBUTION_WEAK: (SLOT_EXAM, SLOT_SCHOOL),
    FactQueryKind.LINE_REACH: (SLOT_EXAM, SLOT_SCHOOL, SLOT_CLASS),
    FactQueryKind.SCORE_STAT: (SLOT_EXAM, SLOT_SCHOOL, SLOT_CLASS),
    FactQueryKind.RANK: (SLOT_EXAM, SLOT_SCHOOL, SLOT_CLASS),
    FactQueryKind.VAGUE_OVERALL: (SLOT_EXAM, SLOT_SCHOOL, SLOT_CLASS),
    FactQueryKind.UNSPECIFIED_EXAM: (SLOT_EXAM,),
}
_PROMPT_BY_SLOT = {
    SLOT_SCOPE: "请确认分析范围：全市、全校，还是某个班级？",
    SLOT_EXAM: "要继续分析需要确认考试名称。请直接回复考试专名（不要回复「本次考试」）。",
    SLOT_CLASS: "请指定班级，例如「高三(10)班」。",
    SLOT_STUDENT: "请指定学生学号，例如「学生001」。",
    SLOT_SUBJECT: "请点名科目，例如「数学」。未点名科目不默认语文。",
    SLOT_SCHOOL: "请指定学校。本报告按一校生成，不默认全市、不循环多校。",
}


def _prompt_for_slot(
    slot: str,
    filled: Mapping[str, str] | None = None,
    question: str = "",
) -> str:
    filled_map = dict(filled or {})
    if slot == SLOT_SCHOOL:
        cls = str(filled_map.get(SLOT_CLASS) or "").strip()
        named = str(filled_map.get(SLOT_SCHOOL) or "").strip()
        if cls:
            return f"请确认是哪所学校的{cls}？同名班级可能出现在多所学校。"
        if named:
            return f"目录里没有与「{named}」对应的学校，请从下面选择，或改用库中校名。"
    if slot == SLOT_EXAM:
        q = question or ""
        from src.agent.education.query_parse import is_citywide_analysis_query, is_rank_query

        # 全市班级排名未点名届/年级时，考试追问需点明「届别+年级」，避免 Agent 默选某一届。
        if (
            is_rank_query(q)
            and "班" in q
            and (is_citywide_analysis_query(q) or "全市" in q)
            and not re.search(r"\d{4}届|高[一二三]|初[一二三]", q)
        ):
            return (
                "全市班级排名需确认考试名称（含届别与年级）。"
                "请直接回复考试专名，例如「2026届高三1月期末」。"
            )
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
    "- 达线/分数线/预测线/排名/名次未点考试专名且候选含 exam_name → 必须追问 exam_name，"
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


_ITEM_LOSS_MARKERS = ("小题", "逐题", "知识点")
_ITEM_LOSS_HINTS = ("失分", "扣分", "得分率", "正确率", "错得", "掌握", "薄弱")


def is_item_loss_query(question: str) -> bool:
    """小题/知识点失分事实问：得分率只在「一场×一科×一个范围」内才有意义。"""
    from src.agent.education.query_parse import (
        is_individual_student_analysis_query,
        is_item_difficulty_curve_query,
        is_knowledge_cohort_gap_query,
    )

    q = (question or "").strip()
    if not q or not any(m in q for m in _ITEM_LOSS_MARKERS):
        return False
    if not any(h in q for h in _ITEM_LOSS_HINTS):
        return False
    if is_item_difficulty_curve_query(q) or is_knowledge_cohort_gap_query(q):
        return False
    return not is_individual_student_analysis_query(q)


def fact_query_kind(question: str) -> FactQueryKind | None:
    """事实问归到哪个口径。按特异性从高到低取第一个命中的。"""
    from src.agent.education.query_parse import (
        is_class_weak_subject_query,
        is_contribution_weak_query,
        is_line_reach_query,
        is_oral_score_inquiry,
        is_rank_query,
        is_score_stat_query,
        is_subject_research_report_query,
        is_subject_strength_query,
        is_top_student_lookup_query,
        refers_to_unspecified_exam,
    )

    q = (question or "").strip()
    if not q:
        return None
    if is_item_loss_query(q):
        return FactQueryKind.ITEM_DIAGNOSIS
    if is_subject_research_report_query(q):
        return FactQueryKind.SUBJECT_RESEARCH
    if is_class_weak_subject_query(q):
        return FactQueryKind.CLASS_WEAK_SUBJECT
    if is_contribution_weak_query(q):
        return FactQueryKind.CONTRIBUTION_WEAK
    if is_subject_strength_query(q):
        return FactQueryKind.SUBJECT_STRENGTH
    if is_line_reach_query(q):
        return FactQueryKind.LINE_REACH
    if is_rank_query(q) or is_top_student_lookup_query(q):
        return FactQueryKind.RANK
    if is_score_stat_query(q) or is_oral_score_inquiry(q):
        return FactQueryKind.SCORE_STAT
    if is_vague_overall_query(q):
        return FactQueryKind.VAGUE_OVERALL
    if refers_to_unspecified_exam(q):
        return FactQueryKind.UNSPECIFIED_EXAM
    return None


def _first_hard_slot(candidates: list[str]) -> str | None:
    """按优先级取硬槽；候选非空时规则必追问，LLM 不能否决。"""
    # 班级从属于学校：两边都缺时先问学校，班级选项才能按校过滤。
    priority = (
        SLOT_EXAM,
        SLOT_SCOPE,
        SLOT_SCHOOL,
        SLOT_CLASS,
        SLOT_SUBJECT,
        SLOT_STUDENT,
    )
    for slot in priority:
        if slot in candidates:
            return slot
    return candidates[0] if candidates else None


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
        extract_school_targets,
        extract_student_target,
        is_citywide_analysis_query,
        is_vague_exam_name,
    )

    q = question or ""
    edu = _edu(edu_scope)
    role = str(edu.get("edu_role") or "").strip()
    filled: dict[str, str] = {}

    named_schools = extract_school_targets(q)
    school = named_schools[0] if len(named_schools) == 1 else ""
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


#: LLM 违反「取不到就留空」约束时最常吐的占位值。
_INVALID_SLOT_VALUES = frozenset(
    {
        "无",
        "未知",
        "未提供",
        "用户未提供",
        "未指定",
        "不详",
        "空",
        "null",
        "none",
        "n/a",
        "na",
        "-",
        "待定",
        "全部",
        "所有",
        "不限",
    }
)


def sanitize_llm_slots(slots: Mapping[str, Any] | None) -> dict[str, str]:
    """过滤 LLM 抽出来的槽位值，只留下能直接拿去查库的有效值。

    对应 DB-GPT「无法获取到槽位值则输出空，不要填『用户未提供』这类无效信息」
    与「只取有效值部分」两条约束——模型不总是遵守，所以这里按规则再兜一层。
    """
    from src.agent.education.orchestrator import _extract_subject
    from src.agent.education.query_parse import (
        extract_class_target,
        has_class_alias,
        is_vague_exam_name,
        peel_rhetorical_school_suffix,
    )

    out: dict[str, str] = {}
    for slot, raw in dict(slots or {}).items():
        value = str(raw or "").strip().strip("「」\"'")
        if not value or value.lower() in _INVALID_SLOT_VALUES:
            continue
        if slot == SLOT_EXAM and (is_vague_exam_name(value) or value in _GENERIC_EXAM_TOKENS):
            continue
        if slot == SLOT_CLASS and has_class_alias(value) and not extract_class_target(value):
            continue
        if slot == SLOT_SUBJECT and not _extract_subject(value):
            continue
        if slot == SLOT_SCOPE and value not in _SCOPE_OPTIONS:
            value = _scope_from_text(value)
            if not value:
                continue
        if slot == SLOT_SCHOOL:
            from src.agent.education.query_parse import (
                _is_descriptive_school_phrase,
                _is_exam_window_fragment,
                _is_request_speech_school_name,
                _strip_school_ask_fillers,
                is_interrogative_school_name,
            )

            value = _strip_school_ask_fillers(peel_rhetorical_school_suffix(value))
            if (
                not value
                or _is_request_speech_school_name(value)
                or _is_descriptive_school_phrase(value)
                or is_interrogative_school_name(value)
                or _is_exam_window_fragment(value)
            ):
                continue
        out[slot] = value
    return out


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


def _candidate_missing_slots_legacy(
    route: Any,
    question: str,
    filled: Mapping[str, str],
    edu_scope: Mapping[str, Any] | None = None,
) -> list[str]:
    """若不问就可能猜错的槽，按优先级去重。

    报告走 ``REQUIRED_SLOTS_BY_REPORT``，事实问走 ``REQUIRED_SLOTS_BY_FACT``，
    两条路径同构：查表得必填槽，再由豁免层（宽范围 / 点名学校 / 权限唯一绑定）减掉。
    """
    from src.agent.education.query_parse import (
        extract_class_target,
        extract_school_targets,
        has_class_alias,
        is_bureau_report_query,
        is_citywide_analysis_query,
        is_citywide_nth_class_lookup_query,
        is_named_district_scope,
        is_recent_n_exam_window,
        is_school_class_comparison_query,
        is_which_class_lookup_query,
        is_which_school_lookup_query,
    )

    q = question or ""
    rt = _route_type(route)
    needs = _needs_report(route)
    filled_map = dict(filled)
    edu = _edu(edu_scope)
    role = str(edu.get("edu_role") or "").strip()
    out: list[str] = []

    def add(slot: str) -> None:
        if slot and slot not in out and not filled_map.get(slot):
            out.append(slot)

    def slot_filled(slot: str) -> bool:
        if slot == SLOT_EXAM:
            return not _exam_missing(q, filled_map)
        return bool(str(filled_map.get(slot) or "").strip())

    # 1) 报告类型矩阵（主路径，少漏判）
    if rt is not None:
        for slot in REQUIRED_SLOTS_BY_REPORT.get(rt, ()):
            if slot == SLOT_EXAM:
                if _exam_missing(q, filled_map):
                    add(SLOT_EXAM)
            elif not slot_filled(slot):
                add(slot)
        # 班报告未绑校且问句有班：防同名班
        if (
            SLOT_CLASS in REQUIRED_SLOTS_BY_REPORT.get(rt, ())
            and filled_map.get(SLOT_CLASS)
            and not filled_map.get(SLOT_SCHOOL)
        ):
            bound = _bound_school(edu)
            if not bound or role not in ("teacher", "school_admin", "student"):
                add(SLOT_SCHOOL)

    # 2) 模糊整体：未给范围时追问 scope（报告题仍强制考试）
    if is_vague_overall_query(q) and not filled_map.get(SLOT_SCOPE) and not _has_wide_scope(q):
        if not needs and rt is None:
            add(SLOT_SCOPE)
        if _exam_missing(q, filled_map):
            add(SLOT_EXAM)

    skip_class = bool(
        rt in _BUREAU_TYPES
        or rt == ReportType.GRADE_COMPARISON
        or is_citywide_analysis_query(q)
        or is_school_class_comparison_query(q)
        or is_bureau_report_query(q)
        or _has_wide_scope(q)
    )

    # 3) 事实问必填槽矩阵
    if rt is None:
        # 要报告但类型未定：场次仍不能默选一场
        if needs and _exam_missing(q, filled_map):
            add(SLOT_EXAM)

        bound_classes = _unique_bound_classes(edu)
        # 校级事实问（优势薄弱/均分/达线）有班才下班级排名，没班走学校排名。
        # 「学校的/我校/本校」或校长已绑校，都是校级范围，不要再逼问班级。
        named_schools = extract_school_targets(q)
        school_level_wording = any(h in q for h in ("本校", "我校", "全校", "学校的"))
        school_scoped = (not has_class_alias(q)) and (
            bool(named_schools)
            or school_level_wording
            or (
                role == "school_admin"
                and bool(_bound_school(edu))
                and not extract_class_target(q)
            )
        )
        kind = fact_query_kind(q)
        # 达线已点名邗江区/宝应地区：范围是区县，不再追问班/校。不并入
        # _has_wide_scope，以免排名/均分等其它口径被放开。
        district_scoped = (
            kind == FactQueryKind.LINE_REACH
            and is_named_district_scope(q)
            and not has_class_alias(q)
        )
        for slot in REQUIRED_SLOTS_BY_FACT.get(kind, ()):
            if slot == SLOT_EXAM:
                if _exam_missing(q, filled_map):
                    if kind == FactQueryKind.LINE_REACH and is_recent_n_exam_window(q):
                        continue
                    add(SLOT_EXAM)
            elif slot == SLOT_CLASS:
                # 老师只绑 1 班：静默继承，不追问；学生只看自己
                if skip_class or school_scoped or district_scoped or role == "student":
                    continue
                if is_which_class_lookup_query(q):
                    continue
                # 哪几所学校是答案：不再追问班级
                if is_which_school_lookup_query(q):
                    continue
                if not (role == "teacher" and len(bound_classes) == 1):
                    add(SLOT_CLASS)
            elif slot == SLOT_SCHOOL:
                if kind == FactQueryKind.CONTRIBUTION_WEAK:
                    bound = _bound_school(edu)
                    if not bound or role not in ("teacher", "school_admin", "student"):
                        add(SLOT_SCHOOL)
                    continue
                if skip_class or district_scoped:
                    continue
                # 全市第N是哪个班：学校是答案，不是筛选范围
                if is_citywide_nth_class_lookup_query(q):
                    continue
                if is_which_school_lookup_query(q):
                    continue
                # 「A中学和B中学」已点名多校，不再逼选一所
                if len(named_schools) >= 2:
                    continue
                bound = _bound_school(edu)
                if not bound or role not in ("teacher", "school_admin", "student"):
                    add(SLOT_SCHOOL)
            else:
                add(slot)

    # 4) 有路由时的班级补充（矩阵未覆盖的别名场景）
    if rt is not None and not skip_class and not filled_map.get(SLOT_CLASS):
        if has_class_alias(q) or (
            role == "teacher" and len(_unique_bound_classes(edu)) > 1 and rt in _CLASS_REQUIRED_TYPES
        ):
            if not (role == "teacher" and len(_unique_bound_classes(edu)) == 1):
                add(SLOT_CLASS)

    if rt in _SUBJECT_REQUIRED_TYPES and not filled_map.get(SLOT_SUBJECT):
        add(SLOT_SUBJECT)

    if rt == ReportType.STUDENT_PROFILE and not filled_map.get(SLOT_STUDENT):
        add(SLOT_STUDENT)

    # 要问班、又没绑校时，学校必须进候选，由 _first_hard_slot 优先追问。
    if SLOT_CLASS in out and not filled_map.get(SLOT_SCHOOL):
        bound = _bound_school(edu)
        if not bound or role not in ("teacher", "school_admin", "student"):
            add(SLOT_SCHOOL)

    return out


class RequiredSlotResolver:
    """在旧必填矩阵上统一应用 lookup/window 语义策略。"""

    _SINGLE_EXAM_FACTS = frozenset(
        {
            FactQueryKind.ITEM_DIAGNOSIS,
            FactQueryKind.SUBJECT_RESEARCH,
        }
    )
    _SINGLE_EXAM_REPORTS = frozenset(
        {
            ReportType.SUBJECT_DIAGNOSIS,
            ReportType.DIFFICULTY_CURVE,
            ReportType.SUBJECT_RESEARCH,
        }
    )

    @classmethod
    def resolve(
        cls,
        route: Any,
        question: str,
        filled: Mapping[str, str],
        edu_scope: Mapping[str, Any] | None = None,
        *,
        frame: Any = None,
    ) -> list[str]:
        missing = _candidate_missing_slots_legacy(route, question, filled, edu_scope)
        if frame is None:
            return missing

        if frame.lookup("school"):
            missing = [slot for slot in missing if slot not in {SLOT_SCHOOL, SLOT_CLASS}]
        if frame.lookup("class"):
            missing = [slot for slot in missing if slot != SLOT_CLASS]
        if frame.lookup("student"):
            missing = [slot for slot in missing if slot != SLOT_STUDENT]
        if frame.lookup("subject"):
            missing = [slot for slot in missing if slot != SLOT_SUBJECT]
        if frame.lookup("exam"):
            missing = [slot for slot in missing if slot != SLOT_EXAM]

        rt = _route_type(route)
        kind = fact_query_kind(question)
        single_exam = kind in cls._SINGLE_EXAM_FACTS or rt in cls._SINGLE_EXAM_REPORTS
        if frame.exam_window and not single_exam:
            missing = [slot for slot in missing if slot != SLOT_EXAM]
        return missing


def candidate_missing_slots(
    route: Any,
    question: str,
    filled: Mapping[str, str],
    edu_scope: Mapping[str, Any] | None = None,
    *,
    frame: Any = None,
) -> list[str]:
    """返回真正阻断执行的缺槽；语义帧存在时识别答案维度和考试窗口。"""
    return RequiredSlotResolver.resolve(
        route,
        question,
        filled,
        edu_scope,
        frame=frame,
    )


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
    slot = _first_hard_slot(candidates) or candidates[0]
    opts = list(options or [])
    if slot == SLOT_SCOPE and not opts:
        opts = list(_SCOPE_OPTIONS)
    return ClarificationNeed(
        prompt=_prompt_for_slot(slot, filled, question),
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


_SLOT_ASK_HINTS = {
    SLOT_SCOPE: ("范围", "全市", "全校"),
    SLOT_EXAM: ("考试", "哪场", "哪次"),
    SLOT_CLASS: ("班级", "哪个班", "哪班"),
    SLOT_STUDENT: ("学生", "学号"),
    SLOT_SUBJECT: ("科目", "学科", "哪门"),
    SLOT_SCHOOL: ("学校", "哪所", "哪校"),
}


def _ask_matches_slot(ask: str, slot: str) -> bool:
    """LLM 追问文案必须在问当前槽，否则丢弃，避免芯片和气泡各问一件事。"""
    text = (ask or "").strip()
    if not text:
        return False
    hints = _SLOT_ASK_HINTS.get(slot)
    if not hints:
        label = _SLOT_LABEL.get(slot, "")
        return bool(label) and label in text
    return any(h in text for h in hints)


async def judge_clarification(
    question: str,
    *,
    route: Any = None,
    filled: Mapping[str, str] | None = None,
    candidates: list[str] | None = None,
    edu_scope: Mapping[str, Any] | None = None,
    llm_ask_user: str | None = None,
    options_by_slot: Mapping[str, list[str]] | None = None,
) -> ClarificationNeed | None:
    """规则决定是否追问；追问文案仅在问同一槽时才采用 LLM 的 ``ask_user``。

    候选非空时必返回 ClarificationNeed（硬槽优先）——LLM 不能取消追问，
    这一点与 DB-GPT ``IntentRecognitionAction`` 的空槽循环同构。
    """
    filled_map = dict(filled or extract_filled_slots(question, edu_scope))
    cand = list(
        candidates
        if candidates is not None
        else candidate_missing_slots(route, question, filled_map, edu_scope)
    )
    rt = _route_type(route)
    rt_value = rt.value if rt else None
    if not cand:
        return None

    slot = _first_hard_slot(cand) or cand[0]
    opts: list[str] = []
    if options_by_slot and slot in options_by_slot:
        opts = list(options_by_slot.get(slot) or [])
    elif slot == SLOT_SCOPE:
        opts = list(_SCOPE_OPTIONS)
    prompt = _prompt_for_slot(slot, filled_map, question)
    ask = (llm_ask_user or "").strip().strip('"').strip("'")
    if ask and len(ask) <= 120 and _ask_matches_slot(ask, slot):
        prompt = ask

    return ClarificationNeed(
        prompt=prompt,
        missing=[slot],
        options=opts,
        filled=filled_map,
        original_question=question,
        report_type=rt_value,
    )


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
    "FactQueryKind",
    "REQUIRED_SLOTS_BY_FACT",
    "REQUIRED_SLOTS_BY_REPORT",
    "RequiredSlotResolver",
    "SLOT_CLASS",
    "SLOT_EXAM",
    "SLOT_SCHOOL",
    "SLOT_SCOPE",
    "SLOT_STUDENT",
    "SLOT_SUBJECT",
    "candidate_missing_slots",
    "default_options_for_slot",
    "extract_filled_slots",
    "fact_query_kind",
    "fallback_clarification",
    "is_item_loss_query",
    "is_vague_overall_query",
    "judge_clarification",
    "merge_clarification_reply",
    "parse_pending_clarify",
    "sanitize_llm_slots",
]
