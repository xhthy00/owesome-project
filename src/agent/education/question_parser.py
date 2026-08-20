"""小题分表头解析器。

将 Excel 表头转换为题目标定义，支持单选题、多选题、填空题、大题及其子题。
"""

from __future__ import annotations

import re
from typing import Any

__all__ = ["parse_question_header", "question_type_from_label", "parse_questions_from_headers"]


def parse_question_header(value: Any) -> tuple[str, float] | None:
    """解析题目表头，返回 (label, score)。

    支持全角/半角括号，例如：
        - "单选1（5.0分）" -> ("单选1", 5.0)
        - "15_1（6.0分）" -> ("15_1", 6.0)
        - "15（13.0分）" -> ("15", 13.0)
    """
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value:
        return None
    m = re.match(r"^(.*?)[（(]\s*([0-9.]+)\s*分\s*[）)]$", value)
    if not m:
        return None
    label = m.group(1).strip()
    if not label:
        return None
    try:
        score = float(m.group(2))
    except ValueError:
        return None
    return label, score


def question_type_from_label(label: str) -> str:
    """根据题目标签判断题型。

    由于 xls 表头无法区分填空题与解答题，非单选/多选统一归为"解答题"。
    """
    if "单选" in label:
        return "单选题"
    if "多选" in label:
        return "多选题"
    return "解答题"


def _split_main_sub(label: str) -> tuple[bool, str | None]:
    """判断 label 是否为大题下的子题，并返回大题号。

    支持下划线与连字符两种分隔符：
        - ``15_1`` / ``18-1`` -> main_no="15" / "18"
        - 单选/多选/纯大题 -> (False, None)
    """
    # 只对纯题号带分隔符的形式识别为子题，避免 "1-B" 这类答题卡代码被误判
    m = re.match(r"^(\d+)[_\-](\d+)$", label)
    if m and m.group(1) and m.group(2):
        return True, m.group(1)
    return False, None


def parse_questions_from_headers(headers: list[Any]) -> list[dict[str, Any]]:
    """从表头列表中解析题目定义。

    返回的每个 dict 包含：
        - question_no: str
        - question_score: float
        - question_type: str
        - is_sub: bool
        - main_no: str | None
        - col_idx: int
    """
    questions: list[dict[str, Any]] = []
    for col_idx, value in enumerate(headers):
        parsed = parse_question_header(value)
        if parsed is None:
            continue
        label, score = parsed
        if "答案" in label:
            continue
        is_sub, main_no = _split_main_sub(label)
        # 过滤掉看起来像子题但缺主号/子号的非法表头（"15_" / "_1" / "18-"）
        if re.match(r"^[_\-]\d+$|^\d+[_\-]$", label):
            continue
        questions.append(
            {
                "question_no": label,
                "question_score": score,
                "question_type": question_type_from_label(label),
                "is_sub": is_sub,
                "main_no": main_no,
                "col_idx": col_idx,
            }
        )
    return questions
