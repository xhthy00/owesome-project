"""从自然语言问题中抽取学生/考试等过滤条件。"""

from __future__ import annotations

import re

_STUDENT_PATTERNS = (
    re.compile(r"[「\"'](学生\s*\d+)[」\"']"),
    re.compile(r"(学生\s*\d+)"),
    re.compile(r"[「\"']([\u4e00-\u9fff]{2,4})[」\"']"),
)


def normalize_student_key(name: str) -> str:
    return re.sub(r"\s+", "", str(name or "")).lower()


def extract_student_target(question: str) -> str | None:
    """从问题中抽取目标学生标识（如「学生001」）。"""
    q = (question or "").strip()
    if not q:
        return None
    for pat in _STUDENT_PATTERNS:
        m = pat.search(q)
        if m:
            return re.sub(r"\s+", "", m.group(1))
    return None


def student_matches(record_name: str, target: str) -> bool:
    """判断记录中的学生名是否匹配目标学生。"""
    if not target:
        return True
    rn = normalize_student_key(record_name)
    tn = normalize_student_key(target)
    if rn == tn:
        return True
    if tn in rn or rn in tn:
        return True
    # 学生001 vs 001
    rn_digits = re.sub(r"[^\d]", "", rn)
    tn_digits = re.sub(r"[^\d]", "", tn)
    if rn_digits and tn_digits and rn_digits == tn_digits:
        return True
    return False


def report_matches_student(title: str, html: str, target: str) -> bool:
    """报告标题/HTML 是否属于目标学生（用于过滤偏离报告）。"""
    if not target:
        return True
    blob = f"{title}\n{html[:4000]}"
    tn = normalize_student_key(target)
    # 提取 blob 中的学生标识
    candidates = set(re.findall(r"学生\s*\d+", blob, flags=re.I))
    candidates |= {re.sub(r"\s+", "", c) for c in candidates}
    if not candidates:
        return True
    for c in candidates:
        cn = normalize_student_key(c)
        if student_matches(cn, tn):
            return True
    return False


__all__ = [
    "extract_student_target",
    "normalize_student_key",
    "report_matches_student",
    "student_matches",
]
