"""DataAnalyst 口径分叉：声明 pending_choices、校验 question 载荷、落地用户选择。"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

from src.agent.education.report_types import ReportType

logger = logging.getLogger(__name__)

KIND_GROUP_DIMENSION = "group_dimension"
KIND_REPORT_TYPE = "report_type"
KIND_FACT_METRIC = "fact_metric"

_HARD_SLOT_MARKERS = (
    "学校",
    "班级",
    "考试",
    "学生",
    "学号",
    "科目",
    "school_name",
    "class_name",
    "exam_name",
    "student_id",
    "subject_name",
)

_DIMENSION_OPTIONS: tuple[tuple[str, str], ...] = (
    ("class", "按班级"),
    ("subject", "按学科"),
    ("school", "按学校"),
)
_BUREAU_DIMENSION_EXTRA = ("district", "按区县")

_REPORT_OPTIONS_BY_ROLE: dict[str, tuple[tuple[str, str], ...]] = {
    "teacher": (
        ("class_overview", "班级总览"),
        ("tier_alert", "弱科与临界"),
        ("trend_tracking", "成绩趋势"),
    ),
    "school_admin": (
        ("grade_comparison", "各班对比"),
        ("subject_diagnosis", "学科诊断"),
        ("class_overview", "班级总览"),
    ),
    "bureau_admin": (
        ("line_reach", "全市达线"),
        ("subject_avg", "校际均分"),
        ("elite_roster", "高分名单"),
    ),
}

_FACT_OPTIONS_BY_ROLE: dict[str, tuple[tuple[str, str], ...]] = {
    "teacher": (
        ("score_stat", "均分情况"),
        ("line_reach", "达线人数"),
        ("weak_subject", "弱科与排名"),
    ),
    "school_admin": (
        ("score_stat", "各班均分"),
        ("line_reach", "达线人数"),
        ("weak_subject", "弱科与排名"),
    ),
    "bureau_admin": (
        ("score_stat", "校际均分"),
        ("line_reach", "达线人数"),
        ("rank", "高分排名"),
    ),
}

_EXPLICIT_FACT_HINTS = (
    "均分",
    "平均分",
    "及格率",
    "优秀率",
    "最高分",
    "最低分",
    "标准差",
    "达线",
    "上线",
    "特控",
    "清北",
    "985",
    "211",
    "排名",
    "名次",
    "第几",
    "倒数",
    "多少人",
    "是谁",
    "哪几所",
    "哪个班",
    "薄弱",
    "弱科",
    "优势学科",
    "难度曲线",
    "得分率",
)

_EXPLICIT_REPORT_DETECTORS = (
    "is_class_overview_query",
    "is_school_class_comparison_query",
    "is_group_feature_query",
    "is_tier_alert_query",
    "is_trend_tracking_query",
    "is_line_reach_report_query",
    "is_subject_research_report_query",
    "is_structured_diagnostic_query",
    "is_multi_exam_class_analysis_query",
    "is_individual_student_analysis_query",
    "is_difficulty_curve_report_query",
)

_NAMED_SUBJECTS = (
    "数学",
    "语文",
    "英语",
    "物理",
    "化学",
    "生物",
    "政治",
    "历史",
    "地理",
    "科学",
)


@dataclass
class PendingChoice:
    """一次允许弹出的口径选择题。"""

    kind: str
    header: str
    prompt: str
    options: list[dict[str, str]] = field(default_factory=list)
    fallback: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def option_labels(self) -> list[str]:
        return [str(item.get("label") or "").strip() for item in self.options if str(item.get("label") or "").strip()]

    def question_payload(self) -> list[dict[str, Any]]:
        return [
            {
                "question": self.prompt,
                "header": self.header,
                "options": list(self.options),
                "multiple": False,
                "custom": False,
            }
        ]


def pending_choice_from_dict(raw: Mapping[str, Any] | None) -> PendingChoice | None:
    if not isinstance(raw, Mapping):
        return None
    kind = str(raw.get("kind") or "").strip()
    header = str(raw.get("header") or "").strip()
    prompt = str(raw.get("prompt") or "").strip()
    fallback = str(raw.get("fallback") or "").strip()
    options_raw = raw.get("options") or []
    if kind not in {KIND_GROUP_DIMENSION, KIND_REPORT_TYPE, KIND_FACT_METRIC} or not header or not prompt:
        return None
    options: list[dict[str, str]] = []
    for item in options_raw:
        if isinstance(item, str) and item.strip():
            options.append({"label": item.strip()})
            continue
        if not isinstance(item, Mapping):
            continue
        label = str(item.get("label") or "").strip()
        if not label:
            continue
        option = {"label": label}
        value = str(item.get("value") or "").strip()
        if value:
            option["value"] = value
        options.append(option)
    if len(options) < 2:
        return None
    return PendingChoice(
        kind=kind,
        header=header,
        prompt=prompt,
        options=options,
        fallback=fallback,
    )


def load_pending_choices(raw: Any) -> list[PendingChoice]:
    if not isinstance(raw, list):
        return []
    out: list[PendingChoice] = []
    for item in raw:
        if isinstance(item, PendingChoice):
            out.append(item)
            continue
        choice = pending_choice_from_dict(item if isinstance(item, Mapping) else None)
        if choice is not None:
            out.append(choice)
    return out


def da_question_mode() -> str:
    try:
        from src.common.core.config import get_settings

        mode = str(getattr(get_settings(), "edu_da_question_mode", "shadow") or "shadow")
    except Exception:  # noqa: BLE001
        return "shadow"
    if mode in {"off", "shadow", "on"}:
        return mode
    return "shadow"


def dimension_explicit(question: str) -> bool:
    """问句是否点名了群体特征聚合维度（未点名不得伪装成已确定）。"""
    from src.agent.education.query_parse import extract_school_target

    q = (question or "").strip()
    if not q:
        return False
    if any(h in q for h in ("区县", "区域", "城区")):
        return True
    if "年级" in q and "班级" not in q:
        return True
    if any(h in q for h in ("科目", "学科", "各科")) and "班级" not in q:
        return True
    if "学校" in q and "班级" not in q and extract_school_target(q) is None:
        return True
    if any(h in q for h in ("按班级", "按班", "各班", "分班", "按班级群体")):
        return True
    return False


def _edu_role(edu_scope: Mapping[str, Any] | None) -> str:
    if not isinstance(edu_scope, Mapping):
        return ""
    return str(edu_scope.get("edu_role") or "").strip()


def _looks_like_lookup_or_window(query_frame: Mapping[str, Any] | None) -> bool:
    if not isinstance(query_frame, Mapping):
        return False
    dimensions = [
        str(item).strip()
        for item in (query_frame.get("answer_dimensions") or [])
        if str(item).strip()
    ]
    if dimensions:
        return True
    window = query_frame.get("exam_window")
    return isinstance(window, dict) and bool(window.get("mode"))


def _explicit_report_kind(question: str) -> bool:
    from src.agent.education import query_parse as qp

    q = (question or "").strip()
    if not q:
        return False
    for name in _EXPLICIT_REPORT_DETECTORS:
        fn = getattr(qp, name, None)
        if callable(fn) and fn(q):
            return True
    return False


def _route_fields(route: Any) -> tuple[bool, str | None, float, str]:
    if route is None:
        return False, None, 0.0, ""
    if isinstance(route, Mapping):
        needs = bool(route.get("needs_report"))
        raw_type = route.get("report_type")
        confidence = float(route.get("confidence") or 0.0)
        reason = str(route.get("reason") or "")
    else:
        needs = bool(getattr(route, "needs_report", False))
        raw_type = getattr(route, "report_type", None)
        confidence = float(getattr(route, "confidence", 0.0) or 0.0)
        reason = str(getattr(route, "reason", "") or "")
    if isinstance(raw_type, ReportType):
        report_type = raw_type.value
    elif raw_type:
        report_type = str(raw_type).strip() or None
    else:
        report_type = None
    return needs, report_type, confidence, reason


_GENERIC_REPORT_ASKS = (
    "出一份分析报告",
    "出一份报告",
    "生成一份分析报告",
    "生成一份报告",
    "做一份分析报告",
    "出个分析报告",
    "分析报告",
    "学情分析",
    "成绩分析",
    "形成报告",
    "出报告",
    "生成报告",
    "帮我分析",
)


def is_generic_report_ask(question: str) -> bool:
    """要出报告/分析，但没有点名总览/诊断/横向等具体类型。"""
    q = (question or "").strip()
    return any(h in q for h in _GENERIC_REPORT_ASKS)


def _named_subject_analysis(question: str) -> bool:
    """点名单科的分析/报告，按科目诊断执行，不再问类型。"""
    q = (question or "").strip()
    if not q or not any(h in q for h in ("报告", "分析", "诊断")):
        return False
    if "物理类" in q or "历史类" in q:
        blob = q.replace("物理类", "").replace("历史类", "")
    else:
        blob = q
    return any(name in blob for name in _NAMED_SUBJECTS)


def is_report_type_unresolved(question: str, route: Any) -> bool:
    """有报告意图、但问句没有点名具体类型时声明分叉。"""
    needs, _report_type, _confidence, _reason = _route_fields(route)
    if not needs:
        return False
    if _explicit_report_kind(question):
        return False
    if _named_subject_analysis(question):
        return False
    return True


_VAGUE_FACT_HINTS = (
    "看看成绩",
    "看一下成绩",
    "看下成绩",
    "帮我看看",
    "帮我分析",
    "情况如何",
    "考得怎么样",
    "成绩怎么样",
    "分析一下",
    "学情",
    "整体情况",
    "查一下成绩",
    "问一下成绩",
)


def is_fact_metric_unresolved(question: str, route: Any = None) -> bool:
    """自由问答未点名均分/达线/排名等指标时声明分叉。"""
    from src.agent.education.clarification import FactQueryKind, fact_query_kind
    from src.agent.education.query_parse import is_item_difficulty_curve_query

    needs, _report_type, _confidence, _reason = _route_fields(route)
    if needs:
        return False
    q = (question or "").strip()
    if not q or _explicit_report_kind(q):
        return False
    # 「对比本校各班成绩」已点名各班对照，不再问均分/达线/弱科。
    # is_school_class_comparison_query 对「本校」抽不到校名会漏判，这里按字面兜底。
    if ("各班" in q or "各个班级" in q or "各班级" in q) and any(
        h in q for h in ("对比", "对照", "比较", "横向")
    ):
        return False
    if any(h in q for h in _EXPLICIT_FACT_HINTS):
        return False
    if is_item_difficulty_curve_query(q):
        return False
    kind = fact_query_kind(q)
    if kind in {
        FactQueryKind.ITEM_DIAGNOSIS,
        FactQueryKind.SUBJECT_RESEARCH,
        FactQueryKind.CLASS_WEAK_SUBJECT,
        FactQueryKind.SUBJECT_STRENGTH,
        FactQueryKind.CONTRIBUTION_WEAK,
        FactQueryKind.LINE_REACH,
        FactQueryKind.RANK,
    }:
        return False
    if kind in {FactQueryKind.SCORE_STAT, FactQueryKind.VAGUE_OVERALL}:
        return True
    return any(h in q for h in _VAGUE_FACT_HINTS)


def _dimension_choice(edu_scope: Mapping[str, Any] | None) -> PendingChoice:
    if _edu_role(edu_scope) == "bureau_admin":
        pairs = (
            ("class", "按班级"),
            ("school", "按学校"),
            _BUREAU_DIMENSION_EXTRA,
        )
    else:
        pairs = _DIMENSION_OPTIONS
    options = [{"label": label, "value": value} for value, label in pairs]
    return PendingChoice(
        kind=KIND_GROUP_DIMENSION,
        header="聚合维度",
        prompt="请选择群体特征的聚合维度。",
        options=options,
        fallback="class",
    )


def _report_type_choice(edu_scope: Mapping[str, Any] | None) -> PendingChoice:
    role = _edu_role(edu_scope)
    pairs = _REPORT_OPTIONS_BY_ROLE.get(role) or _REPORT_OPTIONS_BY_ROLE["teacher"]
    options = [{"label": label, "value": value} for value, label in pairs]
    return PendingChoice(
        kind=KIND_REPORT_TYPE,
        header="报告类型",
        prompt="请选择要生成的分析报告类型。",
        options=options,
        fallback=ReportType.CLASS_OVERVIEW.value,
    )


def _fact_metric_choice(edu_scope: Mapping[str, Any] | None) -> PendingChoice:
    role = _edu_role(edu_scope)
    pairs = _FACT_OPTIONS_BY_ROLE.get(role) or _FACT_OPTIONS_BY_ROLE["teacher"]
    options = [{"label": label, "value": value} for value, label in pairs]
    return PendingChoice(
        kind=KIND_FACT_METRIC,
        header="问答口径",
        prompt="请选择这次要回答的指标。",
        options=options,
        fallback="score_stat",
    )


def compute_pending_choices(
    question: str,
    route: Any = None,
    edu_scope: Mapping[str, Any] | None = None,
    *,
    query_frame: Mapping[str, Any] | None = None,
) -> list[PendingChoice]:
    """闸门放行后声明本轮允许的口径分叉；最多一条。

    未点名聚合维度的群体特征、未点名报告类型的报告、未点名指标的自由问答，都会声明分叉。
    lookup / 近 N 次考试窗口不问口径。
    """
    from src.agent.education.query_parse import is_group_feature_query

    q = (question or "").strip()
    if not q or da_question_mode() == "off":
        return []
    if is_group_feature_query(q) and not dimension_explicit(q):
        return [_dimension_choice(edu_scope)]
    if _looks_like_lookup_or_window(query_frame):
        return []
    if is_report_type_unresolved(q, route):
        return [_report_type_choice(edu_scope)]
    if is_fact_metric_unresolved(q, route):
        return [_fact_metric_choice(edu_scope)]
    return []


def first_unresolved_choice(
    pending: list[PendingChoice] | list[dict[str, Any]] | None,
    user_choices: Mapping[str, Any] | None = None,
) -> PendingChoice | None:
    choices = dict(user_choices or {})
    for item in load_pending_choices(pending if isinstance(pending, list) else []):
        if item.kind == KIND_GROUP_DIMENSION and (choices.get("dimension") or choices.get(KIND_GROUP_DIMENSION)):
            continue
        if item.kind == KIND_REPORT_TYPE and choices.get(KIND_REPORT_TYPE):
            continue
        if item.kind == KIND_FACT_METRIC and (
            choices.get("fact_metric") or choices.get(KIND_FACT_METRIC)
        ):
            continue
        return item
    return None


def _value_for_label(choice: PendingChoice, answer: str) -> str:
    text = str(answer or "").strip()
    for option in choice.options:
        label = str(option.get("label") or "").strip()
        value = str(option.get("value") or label).strip()
        if text in {label, value}:
            return value
    return choice.fallback or text


def apply_user_choice(choice: PendingChoice, answer: str) -> dict[str, str]:
    value = _value_for_label(choice, answer)
    if choice.kind == KIND_GROUP_DIMENSION:
        return {"dimension": value, KIND_GROUP_DIMENSION: value}
    if choice.kind == KIND_FACT_METRIC:
        return {"fact_metric": value, KIND_FACT_METRIC: value}
    return {KIND_REPORT_TYPE: value}


def apply_fallback_choice(choice: PendingChoice) -> dict[str, str]:
    return apply_user_choice(choice, choice.fallback)


def question_payload_json(choice: PendingChoice) -> str:
    return json.dumps(choice.question_payload(), ensure_ascii=False)


def _parse_raw_questions(payload: Any) -> list[dict[str, Any]]:
    parsed = payload
    if isinstance(payload, str):
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            return []
    if isinstance(parsed, dict):
        parsed = parsed.get("questions")
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, dict)]


def validate_question_payload(
    payload: Any,
    pending: list[PendingChoice] | list[dict[str, Any]] | None,
    *,
    already_asked: bool = False,
    user_choices: Mapping[str, Any] | None = None,
) -> tuple[PendingChoice | None, str]:
    """合法则返回应对齐的 PendingChoice；否则返回错误说明。"""
    if already_asked or (
        user_choices
        and (
            user_choices.get("dimension")
            or user_choices.get(KIND_REPORT_TYPE)
            or user_choices.get("fact_metric")
            or user_choices.get(KIND_FACT_METRIC)
        )
    ):
        return None, "本轮已确认口径，禁止再次调用 question。"
    choice = first_unresolved_choice(pending, user_choices)
    if choice is None:
        return None, "当前没有待确认的分析口径，禁止调用 question。"
    items = _parse_raw_questions(payload)
    if len(items) != 1:
        return None, "question 必须且只能包含 1 道口径选择题。"
    item = items[0]
    header = str(item.get("header") or "").strip()
    blob = f"{header} {item.get('question') or ''}"
    if any(marker in blob for marker in _HARD_SLOT_MARKERS):
        return None, "禁止用 question 询问学校/班级/考试/学生/科目等硬槽。"
    raw_options = item.get("options") or []
    labels: list[str] = []
    for option in raw_options:
        if isinstance(option, str) and option.strip():
            labels.append(option.strip())
        elif isinstance(option, Mapping):
            label = str(option.get("label") or "").strip()
            if label:
                labels.append(label)
    allowed = set(choice.option_labels())
    if not 2 <= len(labels) <= 3:
        return None, "口径选择题必须提供 2～3 个选项。"
    if any(label not in allowed for label in labels):
        return None, f"选项必须来自待确认口径（{ '、'.join(choice.option_labels()) }），不得改写。"
    return choice, ""


def merge_user_choices(
    current: Mapping[str, Any] | None,
    extra: Mapping[str, str] | None,
) -> dict[str, str]:
    out = {
        str(k): str(v)
        for k, v in dict(current or {}).items()
        if str(k).strip() and str(v).strip()
    }
    for key, value in dict(extra or {}).items():
        if str(key).strip() and str(value).strip():
            out[str(key)] = str(value)
    return out


def resolved_dimension(
    user_choices: Mapping[str, Any] | None,
    pending: list[PendingChoice] | list[dict[str, Any]] | None = None,
) -> str | None:
    choices = dict(user_choices or {})
    value = str(choices.get("dimension") or choices.get(KIND_GROUP_DIMENSION) or "").strip()
    if value:
        return value
    item = first_unresolved_choice(pending, choices)
    if item is not None and item.kind == KIND_GROUP_DIMENSION:
        return None
    return None


def resolved_report_type(
    user_choices: Mapping[str, Any] | None,
    pending: list[PendingChoice] | list[dict[str, Any]] | None = None,
) -> str | None:
    choices = dict(user_choices or {})
    value = str(choices.get(KIND_REPORT_TYPE) or "").strip()
    if value:
        return value
    item = first_unresolved_choice(pending, choices)
    if item is not None and item.kind == KIND_REPORT_TYPE:
        return None
    return None


def log_shadow_choices(question: str, pending: list[PendingChoice]) -> None:
    if not pending:
        return
    logger.info(
        "edu_da_question_shadow question=%s kinds=%s",
        (question or "")[:80],
        [item.kind for item in pending],
    )


__all__ = [
    "KIND_GROUP_DIMENSION",
    "KIND_REPORT_TYPE",
    "KIND_FACT_METRIC",
    "PendingChoice",
    "apply_fallback_choice",
    "apply_user_choice",
    "compute_pending_choices",
    "da_question_mode",
    "dimension_explicit",
    "first_unresolved_choice",
    "is_fact_metric_unresolved",
    "is_generic_report_ask",
    "is_report_type_unresolved",
    "load_pending_choices",
    "log_shadow_choices",
    "merge_user_choices",
    "pending_choice_from_dict",
    "question_payload_json",
    "resolved_dimension",
    "resolved_report_type",
    "validate_question_payload",
]
