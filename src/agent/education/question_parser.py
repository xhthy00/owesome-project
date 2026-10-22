"""小题分表头解析器。

将 Excel 表头转换为题目标定义，支持单选题、多选题、填空题、大题及其子题。
"""

from __future__ import annotations

import re
from typing import Any

__all__ = [
    "align_detail_question_columns",
    "parse_question_header",
    "parse_questions_from_headers",
    "question_type_from_label",
    "without_parent_total_columns",
]


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
    return without_parent_total_columns(questions)


def without_parent_total_columns(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同一大题既有小题列又有总分列，且分值相等时，去掉总分列。

    例如 ``15_1``、``15_2`` 合计 13 分，同时出现的 ``15（13.0分）`` 是总分。
    ``19（8.0分）`` 旁边虽有 ``19-1``，但 8 分不等于 12 分，这是另一道题，必须保留。
    """
    children: dict[str, list[dict[str, Any]]] = {}
    for item in questions:
        main_no = str(item.get("main_no") or "")
        if item.get("is_sub") and main_no:
            children.setdefault(main_no, []).append(item)
    if not children:
        return questions
    scores = {
        str(item.get("question_no") or ""): float(item.get("question_score") or 0)
        for item in questions
    }
    drop = {
        main_no
        for main_no, kids in children.items()
        if main_no in scores
        and round(sum(float(kid.get("question_score") or 0) for kid in kids), 2) == round(scores[main_no], 2)
    }
    return [item for item in questions if str(item.get("question_no") or "") not in drop]


def _top_level_tail(label: str) -> str:
    """取最外层顿号后的题号。括号里的顿号属于题号本身，例如 ``21-3（1，2）``。"""
    depth = 0
    last = -1
    for index, char in enumerate(label):
        if char in "（(":
            depth += 1
        elif char in "）)":
            depth = max(0, depth - 1)
        elif char == "，" and depth == 0:
            last = index
    if last < 0:
        return label
    return label[last + 1 :].strip()


def _paper_question_key(label: str, paper_nos: set[str]) -> str | None:
    if label in paper_nos:
        return label
    tail = _top_level_tail(label)
    if tail != label and tail in paper_nos:
        return tail
    return None


def _rollup_groups(questions: list[dict[str, Any]]) -> dict[str, list[str]]:
    """表头里「前缀_小题」且前缀列的分值等于这些小题之和时，前缀列是总分。"""
    scores = {
        str(item.get("question_no") or ""): float(item.get("question_score") or 0)
        for item in questions
    }
    present = set(scores)
    buckets: dict[str, list[str]] = {}
    for label in scores:
        owners = [parent for parent in present if parent != label and label.startswith(parent + "_")]
        matched = re.match(r"^(\d+)[-_](\d+)$", label)
        if matched and matched.group(1) in present and matched.group(1) != label:
            owners.append(matched.group(1))
        if not owners:
            continue
        parent = max(owners, key=len)
        kids = buckets.setdefault(parent, [])
        if label not in kids:
            kids.append(label)
    groups: dict[str, list[str]] = {}
    for parent, kids in buckets.items():
        child_sum = round(sum(scores[kid] for kid in kids), 2)
        if child_sum == round(scores[parent], 2):
            groups[parent] = kids
    return groups


def _same_score_sibling(
    label: str,
    score: float,
    paper_scores: dict[str, float],
    claimed: set[str],
) -> str | None:
    """表头写成 ``19``、试卷写成 ``19-2``，两边分值相同且只有一个未占用题号时对齐。"""
    if not re.fullmatch(r"\d+", label):
        return None
    candidates = [
        number
        for number, paper_score in paper_scores.items()
        if number not in claimed
        and re.fullmatch(rf"{label}-\d+", number)
        and round(paper_score, 2) == round(score, 2)
    ]
    if len(candidates) == 1:
        return candidates[0]
    return None


def align_detail_question_columns(
    questions: list[dict[str, Any]],
    paper_nos: set[str],
    paper_scores: dict[str, float] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """按试卷题号留下可计分的一层，并返回原题号到试卷题号的映射。

    小题分经常同时给出某一层的合计和它下面的小题。试卷总分是固定的，
    只保存其中一层。试卷里有小题时去掉合计；试卷里只有合计时去掉小题。
    """
    if not paper_nos:
        identity = {
            str(item.get("question_no") or ""): str(item.get("question_no") or "")
            for item in questions
        }
        return [dict(item) for item in questions], identity

    drop: set[str] = set()
    for parent, kids in _rollup_groups(questions).items():
        child_in_paper = any(_paper_question_key(kid, paper_nos) for kid in kids)
        parent_in_paper = _paper_question_key(parent, paper_nos) is not None
        if child_in_paper:
            drop.add(parent)
        elif parent_in_paper:
            drop.update(kids)

    claimed = {
        key
        for item in questions
        if (number := str(item.get("question_no") or "")) and number not in drop
        if (key := _paper_question_key(number, paper_nos))
    }
    kept: list[dict[str, Any]] = []
    rename: dict[str, str] = {}
    for item in questions:
        number = str(item.get("question_no") or "")
        if not number or number in drop:
            continue
        key = _paper_question_key(number, paper_nos)
        if key is None and paper_scores is not None:
            key = _same_score_sibling(number, float(item.get("question_score") or 0), paper_scores, claimed)
            if key:
                claimed.add(key)
        key = key or number
        renamed = dict(item)
        renamed["question_no"] = key
        kept.append(renamed)
        rename[number] = key
    return kept, rename
