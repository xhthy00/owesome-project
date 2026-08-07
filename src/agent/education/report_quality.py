"""报告 HTML 质量检测：空壳 / 无考试 / 成绩全破折号。"""

from __future__ import annotations

import re

__all__ = [
    "report_html_empty_exam_signals",
    "report_html_has_all_dash_score_table",
    "report_html_is_sparse",
    "report_html_quality_issues",
]

_EMPTY_BODY_SIGNALS = (
    "tb_score）未查到匹配记录",
    "成绩表（tb_score）未查到",
    "KPI 将显示为空",
    "KPI 与分数段分布为空",
    "成绩为空——KPI",
    "HTML 为空",
)

_ZERO_EXAM_RE = re.compile(
    r"共分析\s*(?:<strong>)?\s*0\s*(?:</strong>)?\s*次考试",
    re.IGNORECASE,
)
_DASH_KPI_RE = re.compile(r"""class=["']value["']>\s*-\s*</div>""")
_TD_DASH_RE = re.compile(r"<td[^>]*>\s*-\s*</td>", re.IGNORECASE)
_TD_ANY_RE = re.compile(r"<td\b", re.IGNORECASE)


def report_html_is_sparse(html: str) -> bool:
    """空壳 / KPI 未填充的报告。"""
    text = (html or "").strip()
    if not text:
        return True
    if any(s in text for s in _EMPTY_BODY_SIGNALS):
        has_body = (
            text.count("<tr") >= 8
            or "archive-card" in text
            or "edu-diag-chip" in text
            or "advice-list" in text
        )
        if not has_body:
            return True
    dash_kpi = len(_DASH_KPI_RE.findall(text))
    if dash_kpi >= 3 and text.count("<tr") < 5:
        return True
    return False


def report_html_empty_exam_signals(html: str) -> bool:
    """学情报告写明「0 次考试」等无考试信号。"""
    return bool(_ZERO_EXAM_RE.search(html or ""))


def report_html_has_all_dash_score_table(html: str) -> bool:
    """成绩汇总表存在但单元格几乎全是「-」。"""
    text = html or ""
    if "历次考试成绩汇总" not in text and "SCORE_SUMMARY" not in text:
        # 模板已渲染时看 edu-table 内破折号占比
        pass
    td_total = len(_TD_ANY_RE.findall(text))
    if td_total < 6:
        return False
    td_dash = len(_TD_DASH_RE.findall(text))
    return td_dash >= 6 and td_dash / max(td_total, 1) >= 0.7


def report_html_quality_issues(html: str) -> list[str]:
    """返回空报告相关问题标签（空列表表示未见明显空壳）。"""
    issues: list[str] = []
    if not (html or "").strip():
        issues.append("empty_html")
        return issues
    if report_html_is_sparse(html):
        issues.append("sparse_html")
    if report_html_empty_exam_signals(html):
        issues.append("zero_exams")
    if report_html_has_all_dash_score_table(html):
        issues.append("all_dash_scores")
    return issues
