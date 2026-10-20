"""LLM 意图检测：一次调用产出 intent + slots + ask_user + user_input。

对标 DB-GPT ``IntentDetectionResponse`` / ``IntentRecognitionAgent``：

- 槽位由 **LLM 从问句与历史中语义抽取**，而不是正则/关键词命中；
- 抽不到的槽位输出空串，由上层的必填矩阵决定是否追问（等价于 DB-GPT
  ``IntentRecognitionAction.run()`` 里那段"遍历 slots，任一为空即 ask_user"）；
- ``ask_user`` 追问文案与 ``user_input``（补全后的完整指令）也由 LLM 产出；
- 用户补充后的下一轮走 ``retry`` 分支：把上一轮 intent 原样带上，只做增量合并。

本模块只负责"把模型的话读成结构"，不做任何权限过滤、实体对齐或缺槽判定——
那些留给 :mod:`src.chat.service.clarification_gate` 的后处理链。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Mapping

from src.agent.core.profile import ProfileConfig
from src.agent.education.clarification import (
    REQUIRED_SLOTS_BY_REPORT,
    SLOT_CLASS,
    SLOT_EXAM,
    SLOT_SCHOOL,
    SLOT_SCOPE,
    SLOT_STUDENT,
    SLOT_SUBJECT,
)
from src.agent.education.report_types import REPORT_TYPE_LABELS, ReportType
from src.agent.util.json_parser import parse_json_tolerant

logger = logging.getLogger(__name__)

#: 意图定义里声明的全部槽位，顺序即提示词里的展示顺序。
SLOT_KEYS: tuple[str, ...] = (
    SLOT_EXAM,
    SLOT_CLASS,
    SLOT_SCHOOL,
    SLOT_SUBJECT,
    SLOT_STUDENT,
    SLOT_SCOPE,
)

#: 槽位语义说明。这里是 LLM 抽槽相对正则的核心增量——把"什么算有效值"
#: 讲清楚，模型才不会把「本次考试」「我们班」这类泛称当成填好了。
_SLOT_DEFS: dict[str, str] = {
    SLOT_EXAM: (
        "考试专名，例如「2026届高三第一次联考」「2025秋期末」。"
        "「本次考试」「这场考试」「期中」「月考」「模考」这类泛称**不是**有效值，留空。"
    ),
    SLOT_CLASS: (
        "库内班级专名，例如「高三(10)班」。"
        "「我们班」「三班」「这个班」这类口语指代**不是**有效值，留空。"
    ),
    SLOT_SCHOOL: "学校专名，例如「扬州中学」「扬大附中」「邗江中学」。",
    SLOT_SUBJECT: "单个科目名：语文/数学/英语/物理/历史/化学/生物/政治/地理。",
    SLOT_STUDENT: "学生学号或姓名，例如「学生001」「STU2024001」。",
    SLOT_SCOPE: (
        "分析范围，只能是「全市」「全校」「指定班级」之一。"
        "用户点名了某一所具体学校（即 school_name 有值）就算「全校」；"
        "点名了具体班级就算「指定班级」；出现「全市」「各区县」「各校」才算「全市」。"
    ),
}

#: 意图识别的身份卡。``retry_*`` 一套对应 DB-GPT 的补充轮语义——用户回答追问后
#: 走的是"增量合并上一轮意图"，而不是"重新识别一遍"。
INTENT_PROFILE = ProfileConfig(
    name="EduIntent",
    role="教育学情意图识别专家",
    goal=(
        "先判断用户是否需要生成 HTML 学情报告并选出意图类型，"
        "再从用户问题与历史对话中抽取槽位值。"
    ),
    constraints=[
        "严格按下面给出的意图定义输出，不要自行发明意图或槽位属性。",
        "从用户问题和历史对话中提取槽位值；取不到就输出空字符串。",
        "只取有效值部分，不要带上定语、辅助描述或量词。",
        "用户没提供的槽位值必须为空字符串，禁止填「用户未提供」「未知」「无」这类无效内容。",
        "不管是否取到值，都要输出全部槽位属性。",
        "若必填槽位仍有空缺，在 ask_user 里写一句中文追问，一次只问最关键的那一个；"
        "不缺则 ask_user 输出空字符串。",
        "user_input 输出把已知槽位补全后的完整任务描述，用用户的语言。",
    ],
    retry_goal=(
        "把用户本轮的补充提取出来，合并进最近一轮已识别的意图信息里，"
        "按同样的格式输出合并后的完整意图。"
    ),
    retry_constraints=[
        "下面给出了上一轮已经识别出的意图与槽位，请把用户本轮的补充增量合并进去。",
        "用户没有明确要求修改时，不要改动上一轮已有的意图与槽位值，只填补空缺。",
        "只取有效值部分，不要带上定语或辅助描述。",
        "取不到的槽位仍输出空字符串，禁止填「用户未提供」这类无效内容。",
        "不管是否取到值，都要输出全部槽位属性，不要丢失上一轮已有的属性。",
        "若合并后必填槽位仍有空缺，继续在 ask_user 里追问；不缺则输出空字符串。",
        "user_input 输出合并后的完整任务描述。",
    ],
)

#: 给 ``chat_with_schema`` 的结构化输出约定。report_type 允许空串表示"无报告"，
#: 不用 null——部分网关对 nullable 字段支持不佳。
INTENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["slots"],
    "properties": {
        "needs_report": {"type": "boolean"},
        "report_type": {"type": "string"},
        "confidence": {"type": "number"},
        "reason": {"type": "string"},
        "slots": {
            "type": "object",
            "properties": {key: {"type": "string"} for key in SLOT_KEYS},
        },
        "mentions": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["kind", "value", "role", "evidence"],
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["exam", "class", "school", "subject", "student"],
                    },
                    "value": {"type": "string"},
                    "role": {"type": "string", "enum": ["filter", "lookup"]},
                    "evidence": {"type": "string"},
                    "confidence": {"type": "number"},
                },
            },
        },
        "answer_dimensions": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": ["exam", "class", "school", "subject", "student"],
            },
        },
        "exam_window": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["recent", "all", "named"]},
                "count": {"type": ["integer", "null"]},
                "evidence": {"type": "string"},
            },
        },
        "ask_user": {"type": "string"},
        "user_input": {"type": "string"},
    },
    "additionalProperties": True,
}


@dataclass
class EduIntent:
    """一次意图识别结果。字段对齐 DB-GPT ``IntentDetectionResponse``。"""

    slots: dict[str, str] = field(default_factory=dict)
    mentions: list[dict[str, Any]] = field(default_factory=list)
    answer_dimensions: list[str] = field(default_factory=list)
    exam_window: dict[str, Any] | None = None
    needs_report: bool = False
    report_type: str | None = None
    confidence: float = 0.0
    reason: str = ""
    ask_user: str = ""
    user_input: str = ""
    #: ``llm`` 表示模型产出；``none`` 表示未调用或调用失败，上层应回落规则抽槽。
    source: str = "none"

    @property
    def ok(self) -> bool:
        return self.source == "llm"

    def to_dict(self) -> dict[str, Any]:
        """落进 pending_payload 的快照，下一轮作为 ``prev_intent`` 回灌。"""
        return {
            "needs_report": self.needs_report,
            "report_type": self.report_type or "",
            "slots": {key: self.slots.get(key, "") for key in SLOT_KEYS},
            "mentions": [dict(item) for item in self.mentions],
            "answer_dimensions": list(self.answer_dimensions),
            "exam_window": dict(self.exam_window) if self.exam_window else None,
            "ask_user": self.ask_user,
            "user_input": self.user_input,
        }


def render_intent_definitions(candidates: list[ReportType] | None = None) -> str:
    """把 ReportType + 必填槽矩阵渲染成 DB-GPT 式意图定义块。"""
    types = list(candidates) if candidates else list(ReportType)
    # 临界生/缺考/班级达线率与贡献分对照/尖子生排名变化报告只在分析工具里生成，不进入对话意图。
    analysis_only = (
        ReportType.LINE_SHIFT,
        ReportType.ABSENCE,
        ReportType.CLASS_LINE_REACH,
        ReportType.ELITE_RANK_SHIFT,
    )
    types = [rt for rt in types if rt not in analysis_only]
    lines: list[str] = []
    for rt in types:
        label = REPORT_TYPE_LABELS.get(rt, rt.value)
        required = REQUIRED_SLOTS_BY_REPORT.get(rt, ())
        need = "、".join(required) if required else "无"
        lines.append(f"- `{rt.value}`（{label}）必填槽位：{need}")
    return "\n".join(lines)


def _render_slot_defs() -> str:
    return "\n".join(f"- `{key}`：{_SLOT_DEFS[key]}" for key in SLOT_KEYS)


def _render_history(history: list[Mapping[str, str]] | None) -> str:
    if not history:
        return "（无）"
    lines: list[str] = []
    for item in history:
        role = str(item.get("role") or "user")
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        speaker = "assistant" if role == "assistant" else "user"
        lines.append(f"{speaker}: {content}")
    return "\n".join(lines) or "（无）"


def _render_scope(edu_scope: Mapping[str, Any] | None) -> str:
    edu = dict(edu_scope) if isinstance(edu_scope, Mapping) else {}
    role = str(edu.get("edu_role") or "").strip() or "unknown"
    school = str(edu.get("school_name") or edu.get("school_id") or "").strip() or "无"
    classes = edu.get("class_names")
    n_class = len(classes) if isinstance(classes, list) else 0
    return f"角色={role} 绑定学校={school} 绑定班级数={n_class}"


def build_intent_messages(
    question: str,
    *,
    candidates: list[ReportType] | None = None,
    route_hint: Any = None,
    history: list[Mapping[str, str]] | None = None,
    edu_scope: Mapping[str, Any] | None = None,
    prev_intent: Mapping[str, Any] | None = None,
    type_catalog: str | None = None,
    routing_rules: str | None = None,
) -> list[dict[str, str]]:
    """构造意图识别 prompt。``prev_intent`` 非空即走 DB-GPT 的 retry 语义。

    ``type_catalog`` / ``routing_rules`` 由 :mod:`src.agent.education.intent_router`
    注入，让这一次调用同时承担分类与抽槽，避免多打一次 LLM。
    """
    is_retry = bool(prev_intent)
    catalog = type_catalog or render_intent_definitions(candidates)
    rules = routing_rules or (
        "- 事实型问句（谁最高分、多少人、均分多少、排名第几、达线人数/率、优势或薄弱学科）"
        "→ needs_report=false，report_type 输出空串\n"
        "- 拿不准时 needs_report=false\n"
    )
    system = (
        INTENT_PROFILE.render_system_prompt({"is_retry_chat": is_retry})
        + "\n\n只输出一个 JSON 对象，不要 Markdown、不要解释：\n"
        '{"needs_report":true或false,"report_type":"<枚举值或空串>",'
        '"confidence":0.0到1.0,"reason":"一句话",'
        '"slots":{"exam_name":"","class_name":"","school_name":"",'
        '"subject_name":"","student_id":"","scope":""},'
        '"mentions":[{"kind":"school","value":"扬州中学","role":"filter",'
        '"evidence":"扬州中学","confidence":0.95}],'
        '"answer_dimensions":[],"exam_window":null,'
        '"ask_user":"","user_input":""}\n\n'
        "语义抽取规则：\n"
        "- mentions 只放问句中有原文 evidence 的实体；role=filter 表示查询条件，"
        "role=lookup 表示用户要查询出的答案维度。\n"
        "- 「哪几所学校/哪个班/哪些学生」是 lookup，不得写入对应 slots。\n"
        "- 「近4次考试/最近几场/历次考试」写入 exam_window，"
        "不是 exam_name；近4次应为 {\"mode\":\"recent\",\"count\":4,"
        "\"evidence\":\"近4次考试\"}。\n"
        "- slots 仅保留 filter 实体的兼容值；lookup 和 window 不得写入 slots。\n"
        "- 示例：「扬州中学近4次考试」=school filter+recent window；"
        "「近4次考试哪几所学校增长」=school lookup+recent window、无 school filter；"
        "「扬州中学哪几个班增长」=school filter+class lookup。\n\n"
        f"槽位定义：\n{_render_slot_defs()}\n\n"
        f"意图（报告类型）候选：\n{catalog}\n\n"
        f"意图判定规则：\n{rules}"
    )

    parts: list[str] = []
    if route_hint:
        parts.append(f"规则预判路由（可参考，不必盲从）：{route_hint}")
    parts.append(f"用户权限范围：{_render_scope(edu_scope)}")
    parts.append(f"历史对话：\n{_render_history(history)}")
    if is_retry:
        parts.append(
            "上一轮已识别的意图与槽位（需增量合并，不要丢失）：\n"
            + json.dumps(dict(prev_intent or {}), ensure_ascii=False)
        )
    parts.append(f"用户本轮问题：{question}")
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "\n\n".join(parts)},
    ]


def _coerce_bool(raw: Any) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return bool(raw)
    return str(raw or "").strip().lower() in {"true", "1", "yes", "y", "是"}


def _coerce_report_type(raw: Any) -> str | None:
    value = str(raw or "").strip()
    if not value or value.lower() in {"null", "none"}:
        return None
    try:
        rt = ReportType(value)
    except ValueError:
        rt = None
    if rt in (
        ReportType.LINE_SHIFT,
        ReportType.ABSENCE,
        ReportType.CLASS_LINE_REACH,
        ReportType.ELITE_RANK_SHIFT,
    ):
        return None
    if rt is not None:
        return rt.value
    for item, label in REPORT_TYPE_LABELS.items():
        if value == label and item not in (
            ReportType.LINE_SHIFT,
            ReportType.ABSENCE,
            ReportType.CLASS_LINE_REACH,
            ReportType.ELITE_RANK_SHIFT,
        ):
            return item.value
    return None


def parse_intent_payload(payload: Any) -> EduIntent:
    """把模型输出（dict 或 JSON 文本）读成 :class:`EduIntent`。"""
    data = payload
    if not isinstance(data, dict):
        data = parse_json_tolerant(str(payload))
    if not isinstance(data, dict):
        raise ValueError("intent payload is not a JSON object")

    raw_slots = data.get("slots")
    slots: dict[str, str] = {}
    if isinstance(raw_slots, Mapping):
        for key in SLOT_KEYS:
            value = str(raw_slots.get(key) or "").strip()
            if value:
                slots[key] = value

    mentions: list[dict[str, Any]] = []
    for raw in data.get("mentions") or []:
        if not isinstance(raw, Mapping):
            continue
        kind = str(raw.get("kind") or "").strip()
        role = str(raw.get("role") or "").strip()
        value = str(raw.get("value") or "").strip()
        evidence = str(raw.get("evidence") or "").strip()
        if kind not in {"exam", "class", "school", "subject", "student"}:
            continue
        if role not in {"filter", "lookup"}:
            continue
        try:
            mention_confidence = float(raw.get("confidence") or 0.7)
        except (TypeError, ValueError):
            mention_confidence = 0.7
        mentions.append(
            {
                "kind": kind,
                "value": value,
                "role": role,
                "evidence": evidence,
                "confidence": max(0.0, min(1.0, mention_confidence)),
            }
        )

    answer_dimensions = [
        str(item).strip()
        for item in (data.get("answer_dimensions") or [])
        if str(item).strip() in {"exam", "class", "school", "subject", "student"}
    ]
    raw_window = data.get("exam_window")
    exam_window: dict[str, Any] | None = None
    if isinstance(raw_window, Mapping):
        mode = str(raw_window.get("mode") or "").strip()
        if mode in {"recent", "all", "named"}:
            count_raw = raw_window.get("count")
            try:
                count = int(count_raw) if count_raw not in (None, "") else None
            except (TypeError, ValueError):
                count = None
            exam_window = {
                "mode": mode,
                "count": count if count and count > 0 else None,
                "evidence": str(raw_window.get("evidence") or "").strip(),
            }

    try:
        confidence = float(data.get("confidence") or 0.7)
    except (TypeError, ValueError):
        confidence = 0.7

    return EduIntent(
        slots=slots,
        mentions=mentions,
        answer_dimensions=answer_dimensions,
        exam_window=exam_window,
        needs_report=_coerce_bool(data.get("needs_report")),
        report_type=_coerce_report_type(data.get("report_type")),
        confidence=max(0.0, min(1.0, confidence)),
        reason=str(data.get("reason") or "").strip()[:200],
        ask_user=str(data.get("ask_user") or "").strip(),
        user_input=str(data.get("user_input") or "").strip(),
        source="llm",
    )


async def detect_edu_intent(
    question: str,
    llm_client: Any,
    *,
    candidates: list[ReportType] | None = None,
    route_hint: Any = None,
    history: list[Mapping[str, str]] | None = None,
    edu_scope: Mapping[str, Any] | None = None,
    prev_intent: Mapping[str, Any] | None = None,
    type_catalog: str | None = None,
    routing_rules: str | None = None,
) -> EduIntent:
    """LLM 抽槽。任何失败都返回 ``source="none"``，由上层回落规则抽槽。"""
    q = (question or "").strip()
    if not q or llm_client is None:
        return EduIntent()

    messages = build_intent_messages(
        q,
        candidates=candidates,
        route_hint=route_hint,
        history=history,
        edu_scope=edu_scope,
        prev_intent=prev_intent,
        type_catalog=type_catalog,
        routing_rules=routing_rules,
    )

    # 结构化输出优先：意图 JSON 一旦解析失败就只能整体回落规则，代价比较高。
    chat_with_schema = getattr(llm_client, "chat_with_schema", None)
    if callable(chat_with_schema):
        try:
            payload = await chat_with_schema(messages, INTENT_SCHEMA)
            if isinstance(payload, dict):
                return parse_intent_payload(payload)
        except Exception as exc:  # noqa: BLE001
            logger.info("intent structured output unavailable, fallback chat: %s", exc)

    chat = getattr(llm_client, "chat", None)
    if not callable(chat):
        return EduIntent()
    try:
        raw = await chat(messages)
        return parse_intent_payload(str(raw or ""))
    except Exception as exc:  # noqa: BLE001
        logger.warning("intent detection failed, fallback to rule slots: %s", exc)
        return EduIntent()


__all__ = [
    "INTENT_PROFILE",
    "INTENT_SCHEMA",
    "SLOT_KEYS",
    "EduIntent",
    "build_intent_messages",
    "detect_edu_intent",
    "parse_intent_payload",
    "render_intent_definitions",
]
