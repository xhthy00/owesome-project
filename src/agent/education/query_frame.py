"""教育问句的内部语义帧。

把筛选实体、答案维度和时间窗口分开表达；对外仍可适配成历史 ``filled`` 字典。
"""

from __future__ import annotations

import re
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


class EntityRole(str, Enum):
    FILTER = "filter"
    LOOKUP = "lookup"


class EntitySource(str, Enum):
    RULE = "rule"
    LLM = "llm"
    PERMISSION = "permission"
    HISTORY = "history"


class EntityStatus(str, Enum):
    RAW = "raw"
    GROUNDED = "grounded"
    AMBIGUOUS = "ambiguous"
    REJECTED = "rejected"


_SLOT_TO_KIND = {
    "exam_name": "exam",
    "class_name": "class",
    "school_name": "school",
    "subject_name": "subject",
    "student_id": "student",
}
_KIND_TO_SLOT = {value: key for key, value in _SLOT_TO_KIND.items()}
_SOURCE_PRIORITY = {
    EntitySource.HISTORY: 10,
    EntitySource.PERMISSION: 20,
    EntitySource.RULE: 30,
    EntitySource.LLM: 40,
}
_CN_NUMBERS = {
    "一": 1,
    "二": 2,
    "两": 2,
    "俩": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}
_RECENT_WINDOW_RE = re.compile(
    r"(?P<evidence>(?:最近|近|过去)\s*(?P<count>[0-9一二三四五六七八九十两俩几]+)"
    r"\s*(?:次|场)(?:考试|测试|检测)?)"
)
_ALL_WINDOW_RE = re.compile(r"(历次考试|所有考试|全部考试|各次考试|每场考试|多次考试)")
_ACTIVE_CONSTRAINTS: ContextVar[Mapping[str, Any] | None] = ContextVar(
    "edu_active_constraints",
    default=None,
)
_TARGET_BY_KIND = {
    "exam": "target_exam",
    "school": "target_school",
    "subject": "target_subject",
    "student": "target_student",
}
_LOOKUP_PATTERNS = {
    "school": re.compile(
        r"(?:哪几所|哪些|哪所|几所|多少所)(?:学校|中学)|(?:学校|中学)(?:都)?有哪些"
    ),
    "class": re.compile(
        r"(?:哪几个|哪些|哪个|几个|几)(?:班级|班)|(?:班级|班)(?:都)?有哪些"
    ),
    "student": re.compile(r"(?:哪些|哪个|哪位|多少名)(?:学生|同学)|谁"),
    "subject": re.compile(r"(?:哪些|哪个|哪门|什么)(?:学科|科目)|(?:学科|科目)(?:都)?有哪些"),
    "exam": re.compile(r"(?:哪几次|哪些|哪次)(?:考试|测试|检测)"),
}


def _coerce_enum(enum_type: type[Enum], value: Any, default: Enum) -> Any:
    try:
        return enum_type(str(value or ""))
    except ValueError:
        return default


def _parse_count(raw: str) -> int | None:
    text = str(raw or "").strip()
    if text.isdigit():
        value = int(text)
        return value if value > 0 else None
    if text == "几":
        return None
    if text in _CN_NUMBERS:
        return _CN_NUMBERS[text]
    if len(text) == 2 and text.startswith("十") and text[1] in _CN_NUMBERS:
        return 10 + _CN_NUMBERS[text[1]]
    if len(text) == 2 and text.endswith("十") and text[0] in _CN_NUMBERS:
        return _CN_NUMBERS[text[0]] * 10
    if (
        len(text) == 3
        and text[1] == "十"
        and text[0] in _CN_NUMBERS
        and text[2] in _CN_NUMBERS
    ):
        return _CN_NUMBERS[text[0]] * 10 + _CN_NUMBERS[text[2]]
    return None


@dataclass
class EntityMention:
    kind: str
    value: str = ""
    role: EntityRole = EntityRole.FILTER
    source: EntitySource = EntitySource.RULE
    evidence: str = ""
    confidence: float = 1.0
    status: EntityStatus = EntityStatus.RAW
    candidates: list[str] = field(default_factory=list)
    reason: str = ""

    @property
    def slot(self) -> str | None:
        return _KIND_TO_SLOT.get(self.kind)

    @property
    def has_evidence(self) -> bool:
        return bool(self.evidence.strip())

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "value": self.value,
            "role": self.role.value,
            "source": self.source.value,
            "evidence": self.evidence,
            "confidence": self.confidence,
            "status": self.status.value,
            "candidates": list(self.candidates),
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "EntityMention":
        try:
            confidence = float(raw.get("confidence", 0.7))
        except (TypeError, ValueError):
            confidence = 0.7
        return cls(
            kind=str(raw.get("kind") or "").strip(),
            value=str(raw.get("value") or "").strip(),
            role=_coerce_enum(EntityRole, raw.get("role"), EntityRole.FILTER),
            source=_coerce_enum(EntitySource, raw.get("source"), EntitySource.LLM),
            evidence=str(raw.get("evidence") or "").strip(),
            confidence=max(0.0, min(1.0, confidence)),
            status=_coerce_enum(EntityStatus, raw.get("status"), EntityStatus.RAW),
            candidates=[
                str(item).strip()
                for item in (raw.get("candidates") or [])
                if str(item).strip()
            ],
            reason=str(raw.get("reason") or "").strip(),
        )


@dataclass
class ExamWindow:
    mode: str
    count: int | None = None
    evidence: str = ""
    source: EntitySource = EntitySource.RULE

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "count": self.count,
            "evidence": self.evidence,
            "source": self.source.value,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ExamWindow | None":
        mode = str(raw.get("mode") or "").strip()
        if mode not in {"recent", "all", "named"}:
            return None
        count_raw = raw.get("count")
        try:
            count = int(count_raw) if count_raw not in (None, "") else None
        except (TypeError, ValueError):
            count = None
        return cls(
            mode=mode,
            count=count if count and count > 0 else None,
            evidence=str(raw.get("evidence") or "").strip(),
            source=_coerce_enum(EntitySource, raw.get("source"), EntitySource.LLM),
        )


@dataclass
class QueryFrame:
    mentions: list[EntityMention] = field(default_factory=list)
    scope: str = ""
    answer_dimensions: list[str] = field(default_factory=list)
    exam_window: ExamWindow | None = None
    diagnostics: list[str] = field(default_factory=list)

    def add_mention(self, mention: EntityMention) -> None:
        if mention.kind and (mention.value or mention.role is EntityRole.LOOKUP):
            self.mentions.append(mention)

    def filter_mentions(self, kind: str | None = None) -> list[EntityMention]:
        return [
            item
            for item in self.mentions
            if item.role is EntityRole.FILTER
            and item.status is not EntityStatus.REJECTED
            and (kind is None or item.kind == kind)
        ]

    def lookup(self, kind: str) -> bool:
        return kind in self.answer_dimensions or any(
            item.kind == kind
            and item.role is EntityRole.LOOKUP
            and item.status is not EntityStatus.REJECTED
            for item in self.mentions
        )

    def selected_filter(self, kind: str) -> EntityMention | None:
        items = self.filter_mentions(kind)
        if not items:
            return None
        grounded = [item for item in items if item.status is EntityStatus.GROUNDED]
        pool = grounded or items
        values = {item.value for item in pool if item.value}
        if len(values) > 1:
            return None
        return max(
            pool,
            key=lambda item: (
                _SOURCE_PRIORITY.get(item.source, 0),
                item.confidence,
                bool(item.evidence),
            ),
        )

    def to_filled(
        self,
        *,
        grounded_only: bool = False,
        allow_evidenced_raw: bool = True,
    ) -> dict[str, str]:
        out: dict[str, str] = {}
        for kind, slot in _KIND_TO_SLOT.items():
            mention = self.selected_filter(kind)
            if mention is None:
                continue
            accepted = mention.status is EntityStatus.GROUNDED
            if not grounded_only:
                accepted = mention.status is not EntityStatus.REJECTED
            elif allow_evidenced_raw and mention.status is EntityStatus.RAW:
                accepted = mention.has_evidence or mention.source is EntitySource.PERMISSION
            if accepted and mention.value:
                out[slot] = mention.value
        if self.scope:
            out["scope"] = self.scope
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "mentions": [item.to_dict() for item in self.mentions],
            "scope": self.scope,
            "answer_dimensions": list(self.answer_dimensions),
            "exam_window": self.exam_window.to_dict() if self.exam_window else None,
            "diagnostics": list(self.diagnostics),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any] | None) -> "QueryFrame":
        data = dict(raw or {})
        mentions = [
            EntityMention.from_dict(item)
            for item in (data.get("mentions") or [])
            if isinstance(item, Mapping)
        ]
        window_raw = data.get("exam_window")
        return cls(
            mentions=mentions,
            scope=str(data.get("scope") or "").strip(),
            answer_dimensions=[
                str(item).strip()
                for item in (data.get("answer_dimensions") or [])
                if str(item).strip()
            ],
            exam_window=(
                ExamWindow.from_dict(window_raw) if isinstance(window_raw, Mapping) else None
            ),
            diagnostics=[
                str(item).strip()
                for item in (data.get("diagnostics") or [])
                if str(item).strip()
            ],
        )


def parse_structural_frame(question: str) -> QueryFrame:
    """只解析确定性很高的范围、窗口和疑问目标。"""
    q = str(question or "").strip()
    frame = QueryFrame()
    if "全市" in q or "各区县" in q or "各校" in q or "各学校" in q:
        frame.scope = "全市"
    elif "全校" in q or "本校" in q or "我校" in q:
        frame.scope = "全校"
    elif any(token in q for token in ("各班", "各班级", "指定班级")):
        frame.scope = "指定班级"

    recent = _RECENT_WINDOW_RE.search(q)
    if recent:
        frame.exam_window = ExamWindow(
            mode="recent",
            count=_parse_count(recent.group("count")),
            evidence=recent.group("evidence"),
        )
    else:
        all_window = _ALL_WINDOW_RE.search(q)
        if all_window:
            frame.exam_window = ExamWindow(mode="all", evidence=all_window.group(1))

    for kind, pattern in _LOOKUP_PATTERNS.items():
        match = pattern.search(q)
        if not match:
            continue
        frame.answer_dimensions.append(kind)
        frame.add_mention(
            EntityMention(
                kind=kind,
                role=EntityRole.LOOKUP,
                evidence=match.group(0),
                source=EntitySource.RULE,
            )
        )
    return frame


def slot_kind(slot: str) -> str | None:
    return _SLOT_TO_KIND.get(str(slot or ""))


def push_constraints(constraints: Mapping[str, Any] | None) -> Token:
    """把闸门产出的 constraints 交给 Planner 二次抽槽回落读取。"""
    return _ACTIVE_CONSTRAINTS.set(dict(constraints or {}))


def reset_constraints(token: Token) -> None:
    _ACTIVE_CONSTRAINTS.reset(token)


def active_constraints() -> dict[str, Any]:
    return dict(_ACTIVE_CONSTRAINTS.get() or {})


def _frame_from_constraints(constraints: Mapping[str, Any] | None) -> QueryFrame | None:
    raw = (constraints or {}).get("query_frame")
    if isinstance(raw, dict):
        return QueryFrame.from_dict(raw)
    return None


def constraint_filter_value(kind: str, constraints: Mapping[str, Any] | None = None) -> str:
    """优先读已 grounding 的 target_*，再读语义帧 filter。"""
    data = dict(constraints if constraints is not None else active_constraints())
    if kind == "class":
        classes = data.get("target_classes")
        if isinstance(classes, list) and classes:
            value = str(classes[0]).strip()
            if value:
                return value
    target_key = _TARGET_BY_KIND.get(kind)
    if target_key:
        value = str(data.get(target_key) or "").strip()
        if value:
            return value
    frame = _frame_from_constraints(data)
    if frame is None:
        return ""
    mention = frame.selected_filter(kind)
    return mention.value if mention and mention.value else ""


def constraint_looks_up(kind: str, constraints: Mapping[str, Any] | None = None) -> bool:
    data = dict(constraints if constraints is not None else active_constraints())
    frame = _frame_from_constraints(data)
    return bool(frame and frame.lookup(kind))


def constraint_has_exam_window(constraints: Mapping[str, Any] | None = None) -> bool:
    data = dict(constraints if constraints is not None else active_constraints())
    frame = _frame_from_constraints(data)
    return bool(frame and frame.exam_window and frame.exam_window.mode in {"recent", "all"})


__all__ = [
    "EntityMention",
    "EntityRole",
    "EntitySource",
    "EntityStatus",
    "ExamWindow",
    "QueryFrame",
    "active_constraints",
    "constraint_filter_value",
    "constraint_has_exam_window",
    "constraint_looks_up",
    "parse_structural_frame",
    "push_constraints",
    "reset_constraints",
    "slot_kind",
]
