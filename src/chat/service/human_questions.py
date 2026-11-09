"""Ask Human contract adapted from my-cowork/runtime_contract."""

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


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


class HumanQuestionField(BaseModel):
    label: str = Field(min_length=1, description="简短的问题或字段名称")
    kind: Literal["single", "multiple", "text"] = Field(description="单选、多选或文本")
    options: list[str] = Field(default_factory=list, description="选择题的简短选项；界面另外支持自己填写")
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


CLARIFICATION_INSTRUCTIONS = (
    "\n需要用户补充关键信息或消除会改变执行方向的歧义时，调用 ask_human，"
    "用表单等待回复，再继续原任务；不要只在正文追问，不要把等待回复当作任务完成。"
    "用 question 简述事项，用 fields 合并本次必要问题。kind 为 single、multiple 或 text，"
    "选择题提供 options。只把继续执行必需的信息设为 required=true，偏好等可选信息设为 false。"
    "界面提供“自己填写”，不必重复添加此选项。已有信息足够或可通过工具查明时直接执行，"
    "不为常规实现选择和可选偏好反复提问；不编造考试、学校等候选项。"
    "规划和查询前先核对关键对象、时间或批次、指标口径及比较基准；只核对本次任务实际需要的条件。"
    "沿用用户当前说明及历史中已确认的条件；不把“最近一次”、任意一个候选或自定统计口径当作用户已确认。"
    "查询返回多个候选不等于已经选定；用户未明确要求全部或最新且无法唯一确定时，调用 ask_human。"
    "工具未真正中断等待时，不要在正文中声称已经发起追问或正在等待用户回复。"
)


def clarification_payload(pending, original_question):
    """Expose form data through the existing SSE contract, including old checkpoints."""
    value = pending.value
    return {
        "clarify": True, "prompt": str(value["question"]),
        "question_id": pending.id, "interrupt_id": pending.id, "status": "pending",
        "fields": value.get("fields") or [], "options": value.get("options") or [],
        "missing": ["scope"], "filled": {}, "original_question": original_question,
    }
