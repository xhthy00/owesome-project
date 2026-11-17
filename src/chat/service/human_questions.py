"""Ask Human contract adapted from my-cowork/runtime_contract."""

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

__all__ = [
    "HumanQuestionField",
    "HumanQuestionInput",
    "CLARIFICATION_INSTRUCTIONS",
    "clarification_payload",
    "extract_candidates",
]

# 模型常把候选写进 label/question，而不是 options。这些正则负责把它抽出来，
# 保证界面拿到的是可点选的选项，而不是一段四十个学校的正文。
_CANDIDATE_MARKER = re.compile(
    r"(?:(?:候选(?:项|学校|批次|范围)?|可选项?|选项|如下)(?:[是为有含]|包括|含有)?\s*[：:]?"
    r"|(?:包括|例如|比如|如)\s*[：:])\s*"
)
_CLAUSE_SPLIT = re.compile(r"[、,，;；/\n]")
_PARENTHETICAL = re.compile(r"^(?P<name>[^（(]+)[（(](?P<detail>[\s\S]*)[）)]\s*$")
_LABEL_LIMIT = 40
_OPTION_LIMIT = 30
_MAX_OPTIONS = 200


def _form_items(value):
    while isinstance(value, dict) and set(value) == {"item"}:
        value = value["item"]
    return value


def _form_options(value):
    value = _form_items(value)
    if isinstance(value, dict) and "label" in value:
        value = [value]
    if isinstance(value, list):
        return [item["label"] if isinstance(item, dict) and "label" in item else item for item in value]
    return value


def _clean_option(value) -> str:
    text = str(value or "").strip().strip("。；;，,、")
    text = re.sub(r"\s+", " ", text)
    return text[:_OPTION_LIMIT]


def _dedupe(values) -> list[str]:
    options: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        text = _clean_option(value)
        if not text or text in seen:
            continue
        seen.add(text)
        options.append(text)
        if len(options) >= _MAX_OPTIONS:
            break
    return options


def _split_candidates(raw: str) -> list[str]:
    items = [item for item in (part.strip() for part in _CLAUSE_SPLIT.split(raw)) if item]
    # 只有一个条目说明命中的是正文里的“包括…”，不是候选清单。
    return _dedupe(items) if len(items) > 1 else []


def extract_candidates(text: str) -> list[str]:
    """从一句话里抽出“候选含 A、B、C”这类清单；抽不到返回空列表。"""
    if not text:
        return []
    for match in _CANDIDATE_MARKER.finditer(text):
        tail = text[match.end():]
        # 候选只写到句号/换行为止，避免把后面的整段话当成选项。
        tail = re.split(r"[。\n]", tail, maxsplit=1)[0]
        tail = tail.strip().strip("）) ")
        options = _split_candidates(tail)
        if options:
            return options
    return []


def _strip_candidates(text: str) -> str:
    """去掉文案里的候选清单，只留下字段名/问题本身。"""
    parenthetical = _PARENTHETICAL.match(text.strip())
    if parenthetical and extract_candidates(parenthetical.group("detail")):
        return parenthetical.group("name").strip()
    head = _CANDIDATE_MARKER.split(text, maxsplit=1)[0]
    head = head.strip().strip("（(，,、;；:：")
    return head or text.strip()


def _shorten_label(text: str) -> str:
    label = re.sub(r"\s+", " ", (text or "").strip())
    if len(label) <= _LABEL_LIMIT:
        return label
    # 优先在第一个逗号/顿号处截断，保持“学校”“考试批次”这类字段名可读。
    for separator in ("，", ",", "；", ";", "、", "（", "("):
        index = label.find(separator)
        if 0 < index <= _LABEL_LIMIT:
            return label[:index].rstrip()
    return label[: _LABEL_LIMIT - 1].rstrip() + "…"


class HumanQuestionField(BaseModel):
    label: str = Field(min_length=1, description="简短的问题或字段名称")
    kind: Literal["single", "multiple", "text"] = Field(description="单选、多选或文本")
    options: list[str] = Field(default_factory=list, description="逐项列出的简短选项，不要放在字段名里")
    required: bool = Field(default=False, description="仅当继续执行必需此信息时设为 true")
    placeholder: str = Field(default="", description="文本输入的简短提示")

    @model_validator(mode="before")
    @classmethod
    def normalize_label(cls, value):
        if isinstance(value, dict) and "label" not in value and "question" in value:
            return {**value, "label": value["question"]}
        return value

    @field_validator("options", mode="before")
    @classmethod
    def normalize_options(cls, value):
        return _form_options(value)

    @model_validator(mode="after")
    def normalize_choices(self):
        """候选补进 options、字段名瘦身，并保证 kind 与 options 一致。"""
        options = _dedupe(self.options)
        label = self.label.strip()
        extracted = extract_candidates(label)
        if extracted:
            options = _dedupe([*extracted, *options])
            label = _strip_candidates(label)
        if self.kind in ("single", "multiple") and not options:
            self.kind = "text"
        elif self.kind == "text" and options:
            # 有候选就不该让用户手填；单选是更保守的默认。
            self.kind = "single"
        self.label = _shorten_label(label) or "请补充信息"
        self.options = options[: _MAX_OPTIONS]
        return self


class HumanQuestionInput(BaseModel):
    question: str = Field(min_length=1, description="简述需要确认的事项")
    options: list[str] | None = Field(default=None, description="单个问题的建议选项；多项问题使用 fields")
    fields: list[HumanQuestionField] | None = Field(default=None, description="合并本次必要问题，可单选、多选或填文本")

    @field_validator("fields", mode="before")
    @classmethod
    def normalize_fields(cls, value):
        value = _form_items(value)
        return [value] if isinstance(value, dict) and ("label" in value or "question" in value) else value

    @field_validator("options", mode="before")
    @classmethod
    def normalize_options(cls, value):
        return _form_options(value)

    @model_validator(mode="after")
    def normalize_form(self):
        question = re.sub(r"\s+", " ", self.question or "").strip()
        if not self.fields and not self.options:
            # 候选写在 question 里：转成一个选择题，别让界面只剩一个文本框。
            extracted = extract_candidates(question)
            if extracted:
                self.fields = [HumanQuestionField(
                    label="请选择", kind="single", options=extracted, required=True,
                )]
                stripped = _strip_candidates(question)
                question = stripped if stripped and stripped != question else "请确认后继续"
        self.question = question[:200] or "请补充信息"
        return self


CLARIFICATION_INSTRUCTIONS = (
    "\n需要用户补充关键信息或消除会改变执行方向的歧义时，调用 ask_human，"
    "用表单等待回复，再继续原任务；不要只在正文追问，不要把等待回复当作任务完成。"
    "用 question 简述事项，用 fields 合并本次必要问题。kind 为 single、multiple 或 text，"
    "已查得候选学校、考试批次等时，必须把候选逐项放入对应字段的 options，使用 single 或 multiple。"
    "label 只写简短字段名，不要拼入候选，也不要把候选写进 question。"
    "只有没有可供选择的候选时才用 text。"
    "只把继续执行必需的信息设为 required=true，偏好等可选信息设为 false。"
    "已有信息足够或可通过工具查明时直接执行，"
    "不为常规实现选择和可选偏好反复提问；不编造考试、学校等候选项。"
    "规划和查询前先核对关键对象、时间或批次、指标口径及比较基准；只核对本次任务实际需要的条件。"
    "沿用用户当前说明及历史中已确认的条件；不把“最近一次”、任意一个候选或自定统计口径当作用户已确认。"
    "查询返回多个候选不等于已经选定；用户未明确要求全部或最新且无法唯一确定时，调用 ask_human。"
    "工具未真正中断等待时，不要在正文中声称已经发起追问或正在等待用户回复。"
)


def clarification_payload(pending, original_question):
    """Expose form data through the existing SSE contract, including old checkpoints."""
    value = pending.value
    question = str(value["question"])
    fields = value.get("fields") or []
    options = value.get("options") or []
    try:
        # 旧检查点里可能还存着“候选写进 label”的原始表单，读出来时再归一化一次。
        form = HumanQuestionInput.model_validate({"question": question, "options": options, "fields": fields})
        question = form.question
        options = form.options or []
        fields = [field.model_dump() for field in form.fields or []]
    except Exception:  # pragma: no cover - 兼容历史上任何不合规的持久化表单
        pass
    return {
        "clarify": True, "prompt": question,
        "question_id": pending.id, "interrupt_id": pending.id, "status": "pending",
        "fields": fields, "options": options,
        "missing": ["scope"], "filled": {}, "original_question": original_question,
    }
