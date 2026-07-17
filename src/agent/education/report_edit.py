"""报告建议区（RECOMMENDATIONS）提取与回写。

用于审核流：编辑只改建议正文，不改 KPI/图表/逐人档案。
"""

from __future__ import annotations

import html
import re
from typing import Literal

ReviewStatus = Literal["pending", "approved"]

_SECTION_ATTR = 'data-edu-section="recommendations"'

# 无标记时的标题回退（与各模板 h2 文案对齐；不含「总体结论」）
_HEADING_RE = re.compile(
    r"<h2[^>]*>\s*(?:"
    r"改进建议|教学建议|干预建议|学习建议|家庭配合建议"
    r")\s*</h2>\s*"
    r"|<h3[^>]*>\s*（二）\s*知识点提升与分科备考策略\s*</h3>\s*",
    re.I,
)


def extract_recommendations_text(report_html: str) -> str | None:
    """取出建议区纯文本；无建议区返回 None。"""
    raw = report_html or ""
    inner = _extract_marked_inner(raw)
    if inner is None:
        inner = _extract_heading_following_inner(raw)
    if inner is None:
        return None
    return _html_to_plain(inner)


def replace_recommendations_html(report_html: str, plain_text: str) -> str:
    """用纯文本替换建议区 HTML；找不到建议区时原样返回。"""
    raw = report_html or ""
    new_inner = _plain_to_html(plain_text)
    marked = _replace_marked_inner(raw, new_inner)
    if marked is not None:
        return marked
    headed = _replace_heading_following_inner(raw, new_inner)
    return headed if headed is not None else raw


def has_recommendations_section(report_html: str) -> bool:
    return extract_recommendations_text(report_html) is not None


def _extract_marked_inner(raw: str) -> str | None:
    m = re.search(
        rf'<div[^>]*{_SECTION_ATTR}[^>]*>([\s\S]*?)</div>',
        raw,
        re.I,
    )
    return m.group(1) if m else None


def _replace_marked_inner(raw: str, new_inner: str) -> str | None:
    pattern = re.compile(
        rf'(<div[^>]*{_SECTION_ATTR}[^>]*>)([\s\S]*?)(</div>)',
        re.I,
    )
    if not pattern.search(raw):
        return None
    return pattern.sub(rf"\1{new_inner}\3", raw, count=1)


def _extract_heading_following_inner(raw: str) -> str | None:
    m = _HEADING_RE.search(raw)
    if not m:
        return None
    rest = raw[m.end() :]
    # 取到下一个 h2 / section 结束 / footer 之前的第一块内容容器
    block = re.match(
        r"(?:\s*<(?:div|p|ul|ol)[^>]*>[\s\S]*?</(?:div|p|ul|ol)>)",
        rest,
        re.I,
    )
    if block:
        return block.group(0)
    # 裸 HTML 片段（如 student_exam 直接输出列表）
    next_h2 = re.search(r"<h2\b", rest, re.I)
    next_sec = re.search(r"</section>", rest, re.I)
    end = len(rest)
    if next_h2:
        end = min(end, next_h2.start())
    if next_sec:
        end = min(end, next_sec.start())
    chunk = rest[:end].strip()
    return chunk or None


def _replace_heading_following_inner(raw: str, new_inner: str) -> str | None:
    m = _HEADING_RE.search(raw)
    if not m:
        return None
    rest = raw[m.end() :]
    block = re.match(
        r"(\s*)(<(?:div|p|ul|ol)[^>]*>[\s\S]*?</(?:div|p|ul|ol)>)",
        rest,
        re.I,
    )
    if block:
        start = m.end() + block.start(2)
        end = m.end() + block.end(2)
        wrapped = f'<div {_SECTION_ATTR}>{new_inner}</div>'
        return raw[:start] + wrapped + raw[end:]
    next_h2 = re.search(r"<h2\b", rest, re.I)
    next_sec = re.search(r"</section>", rest, re.I)
    end_rel = len(rest)
    if next_h2:
        end_rel = min(end_rel, next_h2.start())
    if next_sec:
        end_rel = min(end_rel, next_sec.start())
    start = m.end()
    end = m.end() + end_rel
    wrapped = f'\n      <div {_SECTION_ATTR}>{new_inner}</div>\n    '
    return raw[:start] + wrapped + raw[end:]


def _html_to_plain(fragment: str) -> str:
    text = fragment or ""
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</p\s*>", "\n", text, flags=re.I)
    text = re.sub(r"</li\s*>", "\n", text, flags=re.I)
    text = re.sub(r"</h[1-6]\s*>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    lines = [ln.rstrip() for ln in text.replace("\r\n", "\n").split("\n")]
    # 压缩多余空行但保留段落感
    out: list[str] = []
    blank = 0
    for ln in lines:
        if not ln.strip():
            blank += 1
            if blank <= 1 and out:
                out.append("")
            continue
        blank = 0
        out.append(ln.strip())
    return "\n".join(out).strip()


def _plain_to_html(plain: str) -> str:
    text = (plain or "").replace("\r\n", "\n").strip()
    if not text:
        return "<p></p>"
    parts = re.split(r"\n\s*\n", text)
    blocks: list[str] = []
    for part in parts:
        inner = "<br/>".join(html.escape(ln) for ln in part.split("\n"))
        blocks.append(f"<p>{inner}</p>")
    return "\n".join(blocks)


__all__ = [
    "ReviewStatus",
    "extract_recommendations_text",
    "replace_recommendations_html",
    "has_recommendations_section",
]
