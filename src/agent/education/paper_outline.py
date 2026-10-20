"""从试卷正文生成题号、题型和分值。

题号规则来自三场真实考试的卷面结构与小题分表头：
选择题加「单选 / 多选」前缀并保留印刷题号；数学、物理解答题同时保留大题与小题；
英语语法填空和两篇写作各合成一列。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

__all__ = [
    "DEFAULT_FULL_SCORE",
    "SUBJECTS",
    "OutlineQuestion",
    "PaperOutline",
    "build_paper_outline",
]

SUBJECTS = ("语文", "数学", "英语", "物理", "化学", "生物", "政治", "历史", "地理")

DEFAULT_FULL_SCORE = {
    "语文": 150.0,
    "数学": 150.0,
    "英语": 150.0,
    "物理": 100.0,
    "化学": 100.0,
    "生物": 100.0,
    "政治": 100.0,
    "历史": 100.0,
    "地理": 100.0,
}

_HEADER_RE = re.compile(
    r"(?m)^\s*("
    r"第[一二三四五六七八九十]+部分[：:]?[^\n]{0,30}"
    r"|第[一二三四五六七八九十]+节[（(][^\n]{0,48}"
    r"|[一二三四五六七八九十]+、[^\n]{0,40}"
    r"|(?:单项选择题|多项选择题|非选择题|填空题|解答题)[：:（(][^\n]{0,40}"
    r"|第[ⅠⅡⅢⅣ]+卷[（(][^\n]{0,40}"
    r"|[（(][一二三四五六七八九十]+[）)]"
    r"(?:阅读|名篇名句默写|语言文字运用)[^\n]{0,40}"
    r")"
)
_QUESTION_RE = re.compile(r"(?m)^\s*(\d{1,2})[\.．、]\s*")
_BARE_QUESTION_RE = re.compile(r"(?m)^\s*(\d{1,2})\s*$")
_SUB_RE = re.compile(r"(?m)^\s*[（(]\s*(\d+)\s*[）)]")
_SCORE_RE = re.compile(
    r"[（(]\s*(?:本[大小]?题)?(?:满分)?\s*(\d+(?:\.\d+)?)\s*分\s*[）)]"
)
_PER_SCORE_RE = re.compile(r"每[小]?题\s*(\d+(?:\.\d+)?)\s*分")
_SECTION_SCORE_RE = re.compile(r"(?:满分|共计|共)\s*(\d+(?:\.\d+)?)\s*分")
_CIRCLES = "①②③④⑤⑥⑦⑧⑨⑩"
_CONTENT_LIMIT = 8000


@dataclass
class OutlineQuestion:
    question_no: str
    question_type: str
    question_score: float
    content: str
    is_parent: bool = False


@dataclass
class PaperOutline:
    subject: str
    exam_score: float
    questions: list[OutlineQuestion] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def leaf_score_sum(self) -> float:
        total = sum(q.question_score for q in self.questions if not q.is_parent)
        return round(total, 2)

    def as_dict(self) -> dict:
        return {
            "subject": self.subject,
            "exam_score": self.exam_score,
            "leaf_score_sum": self.leaf_score_sum,
            "warnings": list(self.warnings),
            "questions": [
                {
                    "question_no": q.question_no,
                    "question_type": q.question_type,
                    "question_score": q.question_score,
                    "content": q.content,
                }
                for q in self.questions
            ],
        }


def build_paper_outline(subject: str, text: str) -> PaperOutline:
    """按科目骨架把试卷正文切成题目。"""
    name = (subject or "").strip()
    if name not in SUBJECTS:
        raise ValueError(f"科目须为 {'、'.join(SUBJECTS)}")
    outline = PaperOutline(subject=name, exam_score=DEFAULT_FULL_SCORE[name])
    body = _crop_preamble(text or "")
    sections = _split_sections(body)
    if not sections:
        outline.warnings.append("未识别到大题标题，已按题号逐条列出")
        sections = [("", body, "")]
    writing_index = 0
    for header, chunk, part in sections:
        kind = _classify(header, chunk, part, name)
        if kind == "write_section" and "部分" in header:
            continue
        if kind in {"grammar", "write_apply", "write_continue", "write_chinese"}:
            outline.questions.append(_section_question(kind, header, chunk, writing_index))
            if kind in {"write_apply", "write_continue"}:
                writing_index += 1
            continue
        if kind == "write_section":
            writing_index += 1
            label = "应用文写作" if writing_index == 1 else "读后续写"
            if "续写" in (header + chunk[:180]):
                label = "读后续写"
            score = _section_score(header, chunk) or (15.0 if label == "应用文写作" else 25.0)
            outline.questions.append(OutlineQuestion(label, "写作", score, _clip(chunk)))
            continue
        seven = _seven_choose_five(header, chunk)
        if seven:
            pairs, suffix = seven
            taken = {qno for qno, _text in pairs}
            prefix = _strip_seven_block(chunk)
            if prefix and len(prefix) < len(chunk.strip()):
                earlier_per = _per_score(header + "\n" + prefix[:120])
                for qno, qtext in _split_questions(
                    prefix, allow_bare=kind in {"single", "multi"}
                ):
                    if qno in taken:
                        continue
                    outline.questions.extend(
                        _emit_question(name, kind, qno, qtext, earlier_per)
                    )
            per = _per_score(header + "\n" + chunk[:160])
            for qno, qtext in pairs:
                outline.questions.extend(_emit_question(name, "single", qno, qtext, per))
            if suffix:
                for qno, qtext in _split_questions(
                    suffix, allow_bare=kind in {"single", "multi"}
                ):
                    if qno in taken:
                        continue
                    outline.questions.extend(_emit_question(name, kind, qno, qtext, per))
            continue
        per = _per_score(header + "\n" + chunk[:120])
        found = _split_questions(chunk, allow_bare=kind in {"single", "multi"})
        found = _with_leading_passage(header, chunk, found)
        found = _pick_contiguous(found, _declared_count(header))
        if kind in {"single", "multi", "mixed"}:
            found = _move_group_leads(found)
        found = _cut_stuck_sections(found)
        if kind in {"single", "multi", "fill"}:
            found = sorted(found, key=lambda item: int(item[0]))
        for qno, qtext in found:
            outline.questions.extend(
                _emit_question(name, kind, qno, qtext, per)
            )
    outline.questions = _lift_embedded_seven(outline.questions)
    if not outline.questions:
        outline.warnings.append("没有识别到题目")
    return outline


def _emit_question(
    subject: str,
    kind: str,
    qno: str,
    qtext: str,
    per: float | None,
) -> list[OutlineQuestion]:
    choice = _choice_kind(qtext) if kind == "mixed" else None
    effective = choice or kind
    if effective == "single":
        return [_row(f"单选{qno}", "单选题", per or _first_score(qtext) or 0, qtext)]
    if effective == "multi":
        return [_row(f"多选{qno}", "多选题", per or _first_score(qtext) or 0, qtext)]
    if effective == "fill":
        return [_row(qno, "填空题", per or _first_score(qtext) or 0, qtext)]
    stem, subs = _split_subs(qtext)
    if not subs:
        score = _first_score(qtext) or per or 0
        return [_row(qno, "解答题", score, qtext)]
    style = _sub_style(subject)
    if style == "chem":
        return _emit_chem(qno, subs, stem)
    if style == "bio":
        return _emit_bio(qno, subs, stem)
    if style == "underscore_parent":
        return _emit_parent(qno, stem, subs)
    return _emit_hyphen(qno, subs, stem)


def _emit_parent(qno: str, stem: str, subs: list[tuple[str, str]]) -> list[OutlineQuestion]:
    """大题题干并入第一条小题，不再单独出一行大题。分值只认小题正文。"""
    rows: list[OutlineQuestion] = []
    for sub_no, sub_text in subs:
        rows.append(
            _row(
                f"{qno}_{sub_no}",
                "解答题",
                _first_score(sub_text) or 0,
                _with_stem(stem, sub_text, rows),
            )
        )
    return rows


def _emit_hyphen(
    qno: str,
    subs: list[tuple[str, str]],
    stem: str = "",
) -> list[OutlineQuestion]:
    rows: list[OutlineQuestion] = []
    for sub_no, sub_text in subs:
        rows.append(
            _row(
                f"{qno}-{sub_no}",
                "解答题",
                _first_score(sub_text) or 0,
                _with_stem(stem, sub_text, rows),
            )
        )
    return rows


def _with_stem(stem: str, text: str, rows: list[OutlineQuestion]) -> str:
    if rows or not stem:
        return text
    return f"{stem}\n{text}"


def _emit_chem(
    qno: str,
    subs: list[tuple[str, str]],
    stem: str = "",
) -> list[OutlineQuestion]:
    rows: list[OutlineQuestion] = []
    for sub_no, sub_text in subs:
        circles = [ch for ch in _CIRCLES if ch in sub_text]
        parts = _split_circles(sub_text) if len(circles) >= 2 else []
        head = sub_text.split(circles[0], 1)[0] if circles else ""
        blank_is_before_circles = "▲" in head and not any("▲" in piece for _mark, piece in parts)
        if parts and not blank_is_before_circles:
            for mark, piece in parts:
                rows.append(
                    _row(
                        f"{qno}（{sub_no}）{mark}",
                        "解答题",
                        _first_score(piece) or 0,
                        _with_stem(stem, piece, rows),
                    )
                )
        else:
            rows.append(
                _row(
                    f"{qno}（{sub_no}）",
                    "解答题",
                    _first_score(sub_text) or 0,
                    _with_stem(stem, sub_text, rows),
                )
            )
    return rows


def _emit_bio(
    qno: str,
    subs: list[tuple[str, str]],
    stem: str = "",
) -> list[OutlineQuestion]:
    rows: list[OutlineQuestion] = []
    for sub_no, sub_text in subs:
        circles = [ch for ch in _CIRCLES if ch in sub_text]
        if len(circles) >= 2:
            for mark, piece in _split_circles(sub_text):
                rows.append(
                    _row(
                        f"{qno}（{sub_no}）{mark}",
                        "解答题",
                        _first_score(piece) or 0,
                        _with_stem(stem, piece, rows),
                    )
                )
        else:
            rows.append(
                _row(
                    f"{qno}（{sub_no}）",
                    "解答题",
                    _first_score(sub_text) or 0,
                    _with_stem(stem, sub_text, rows),
                )
            )
    return rows


def _section_question(kind: str, header: str, chunk: str, writing_index: int) -> OutlineQuestion:
    score = _section_score(header, chunk)
    source = _first_lines(chunk) if kind == "write_chinese" else chunk
    text = _clip(source)
    if kind == "grammar":
        return OutlineQuestion("语法填空", "填空题", score or 15.0, text)
    if kind == "write_chinese":
        return OutlineQuestion("写作", "写作", score or 60.0, text)
    if kind == "write_apply":
        return OutlineQuestion("应用文写作", "写作", score or 15.0, text)
    if kind == "write_continue":
        return OutlineQuestion("读后续写", "写作", score or 25.0, text)
    label = "应用文写作" if writing_index == 0 else "读后续写"
    return OutlineQuestion(label, "写作", score or 15.0, text)


def _classify(header: str, chunk: str, part: str, subject: str) -> str:
    blob = f"{header}\n{chunk[:240]}\n{part}"
    if subject == "语文" and "写作" in header:
        return "write_chinese"
    if "续写" in blob and ("写作" in part or "写作" in header or "节" in header):
        return "write_continue"
    if "写作" in part or (subject == "英语" and "写作" in header):
        if "节" in header:
            return "write_section"
        return "write_apply" if "应用文" in blob or "假定你是" in blob else "write_section"
    if "空白处填入" in blob or "语法填空" in blob:
        return "grammar"
    if "多项" in header or "多选" in header:
        return "multi"
    if "填空" in header and "语法" not in header:
        return "fill"
    if "非选择" in header or "解答" in header:
        return "subjective"
    if ("单项" in header or "选择题" in header) and "非选择" not in header:
        return "single"
    if any(token in part for token in ("听力", "阅读", "语言运用")):
        return "single"
    return "mixed"


def _choice_kind(text: str) -> str | None:
    if re.search(r"不止一|多项选择|多选题|两项以上|不正确的两项", text):
        return "multi"
    if re.search(r"一项是|最符合题意|四个选项|下列.*选项", text):
        return "single"
    return None


def _sub_style(subject: str) -> str:
    if subject in {"数学", "物理"}:
        return "underscore_parent"
    if subject == "化学":
        return "chem"
    if subject == "生物":
        return "bio"
    return "hyphen"


def _crop_preamble(text: str) -> str:
    match = re.search(
        r"(?m)^(?:"
        r"第[一二三四五六七八九十]+部分"
        r"|[一二三四五六七八九十]+、"
        r"|单项选择题|多项选择题"
        r")",
        text,
    )
    if match:
        return text[match.start() :]
    return text


def _split_sections(text: str) -> list[tuple[str, str, str]]:
    matches = list(_HEADER_RE.finditer(text))
    if not matches:
        return []
    sections: list[tuple[str, str, str]] = []
    part = ""
    for index, match in enumerate(matches):
        header = match.group(1).strip()
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        chunk = text[start:end].strip()
        if header.startswith("第") and "部分" in header[:6]:
            part = header
        sections.append((header, chunk, part))
    return sections


def _split_questions(body: str, *, allow_bare: bool = False) -> list[tuple[str, str]]:
    dotted = [m for m in _QUESTION_RE.finditer(body) if 0 < int(m.group(1)) <= 70]
    dotted_nos = {str(int(m.group(1))) for m in dotted}
    starts = [(m.start(), m.end(), str(int(m.group(1)))) for m in dotted]
    if allow_bare:
        starts.extend(_continuing_bare(body, dotted_nos))
    starts.sort(key=lambda item: item[0])
    rows: list[tuple[str, str]] = []
    seen: set[str] = set()
    for index, (_start, end_no, qno) in enumerate(starts):
        if qno in seen:
            continue
        piece_end = starts[index + 1][0] if index + 1 < len(starts) else len(body)
        text = body[end_no:piece_end].strip()
        if _is_instruction(text):
            continue
        seen.add(qno)
        rows.append((qno, text))
    return rows


_COUNT_RE = re.compile(r"(?:共|包括)\s*(\d+)\s*[小]?题")


def _declared_count(header: str) -> int | None:
    match = _COUNT_RE.search(header or "")
    if not match:
        return None
    count = int(match.group(1))
    if count <= 0 or count > 60:
        return None
    return count


def _continuing_bare(body: str, dotted_nos: set[str]) -> list[tuple[int, int, str]]:
    """连续出现的裸编号才是下一组题，例如七选五的 36 到 40。单独一行的 2 不是。"""
    candidates: list[tuple[int, int, int]] = []
    for match in _BARE_QUESTION_RE.finditer(body):
        number = int(match.group(1))
        if str(number) in dotted_nos or not (0 < number <= 70):
            continue
        candidates.append((match.start(), match.end(), number))
    if not candidates:
        return []
    runs: list[list[tuple[int, int, int]]] = [[candidates[0]]]
    for item in candidates[1:]:
        if item[2] == runs[-1][-1][2] + 1:
            runs[-1].append(item)
        else:
            runs.append([item])
    kept: list[tuple[int, int, str]] = []
    for run in runs:
        if len(run) < 2:
            continue
        kept.extend((start, end, str(number)) for start, end, number in run)
    return kept


def _pick_contiguous(
    found: list[tuple[str, str]],
    count: int | None,
) -> list[tuple[str, str]]:
    """留下连续题号。大题声明了题量时，优先取长度正好等于题量的那一段。"""
    if len(found) <= 1:
        return found
    numbers = sorted({int(qno) for qno, _text in found})
    runs: list[list[int]] = [[numbers[0]]]
    for number in numbers[1:]:
        if number == runs[-1][-1] + 1:
            runs[-1].append(number)
        elif number != runs[-1][-1]:
            runs.append([number])
    chosen_run = runs[0]
    if count:
        exact = [run for run in runs if len(run) == count]
        if exact:
            chosen_run = exact[-1]
        elif len(runs) > 1:
            chosen_run = max(runs, key=lambda run: (len(run), run[0]))
    elif len(runs) > 1:
        longest = max(len(run) for run in runs)
        if longest < 3:
            return found
        chosen_run = max(runs, key=lambda run: (len(run), run[0]))
    chosen = set(chosen_run)
    return [item for item in found if int(item[0]) in chosen]


def _is_instruction(text: str) -> bool:
    head = (text or "")[:40]
    return head.startswith(("作答", "答题", "考生")) or "必须用2B" in head or "必须用 2B" in head


def _split_subs(text: str) -> tuple[str, list[tuple[str, str]]]:
    matches = list(_SUB_RE.finditer(text))
    if not matches:
        return text.strip(), []
    stem = text[: matches[0].start()].strip()
    subs: list[tuple[str, str]] = []
    last = 0
    kept = []
    for match in matches:
        number = int(match.group(1))
        if number <= last:
            continue
        last = number
        kept.append(match)
    for index, match in enumerate(kept):
        end = kept[index + 1].start() if index + 1 < len(kept) else len(text)
        subs.append((match.group(1), text[match.start() : end].strip()))
    return stem, subs


def _split_circles(text: str) -> list[tuple[str, str]]:
    positions = [index for index, ch in enumerate(text) if ch in _CIRCLES]
    parts: list[tuple[str, str]] = []
    for index, pos in enumerate(positions):
        end = positions[index + 1] if index + 1 < len(positions) else len(text)
        parts.append((text[pos], text[pos:end].strip()))
    return parts


def _per_score(text: str) -> float | None:
    match = _PER_SCORE_RE.search(text)
    if not match:
        return None
    return float(match.group(1))


def _section_score(header: str, chunk: str) -> float | None:
    blob = header + "\n" + chunk[:200]
    scores = [float(item) for item in _SECTION_SCORE_RE.findall(blob)]
    if not scores:
        return _first_score(blob)
    return max(scores)


def _first_score(text: str) -> float | None:
    match = _SCORE_RE.search(text or "")
    if not match:
        return None
    return float(match.group(1))


def _first_lines(text: str, limit: int = 4) -> str:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return "\n".join(lines[:limit])


def _is_fragment(line: str) -> bool:
    """公式文字层常把一个符号单独成行。不含汉字的短行属于这种碎片。"""
    if line.startswith("|") or re.match(r"^[A-D][．.]", line):
        return False
    if len(line) <= 2:
        return True
    return len(line) <= 16 and re.search(r"[\u4e00-\u9fff]", line) is None


_PAGE_MARK_RE = re.compile(
    r"[\u4e00-\u9fffA-Za-z0-9]*试卷\s*第\s*\d+\s*页\s*[（(]\s*共\s*\d+\s*页\s*[）)]"
)
_LISTEN_CUE_RE = re.compile(
    r"听第\s*\d+\s*段材料[，,]\s*回答第\s*\d+\s*至\s*\d+\s*题[。．.]?"
)


def _clean_stem(text: str) -> str:
    """去掉页眉页码、听力引导语，以及标在汉字下面的着重号。"""
    text = _PAGE_MARK_RE.sub("", text)
    text = _LISTEN_CUE_RE.sub("", text)
    previous = None
    while previous != text:
        previous = text
        text = re.sub(
            r"([\u4e00-\u9fff])．[ \t]*(?=[\u4e00-\u9fff][ \t]*．)",
            r"\1",
            text,
        )
        text = re.sub(r"([\u4e00-\u9fff])．[ \t]*(?=[{（(A-Za-z])", r"\1", text)
    return _tidy_math(text)


def _tidy_math(text: str) -> str:
    """公式块之间的空隙不稳定，收成印刷时的紧排。"""
    text = re.sub(r"[ \t]+([=<>≤≥≠≈∈∉∪∩⋅·|+\-−×÷，,])", r"\1", text)
    text = re.sub(r"([=<>≤≥≠≈∈∉∪∩⋅·|+\-−×÷（(\[{])[ \t]+", r"\1", text)
    text = re.sub(r"(?<=\d),[ \t]+(?=\d)", ",", text)
    text = re.sub(r"(\d)[ \t]+([A-Za-z])(?![A-Za-z．.])", r"\1\2", text)
    text = re.sub(r"(?<![A-Za-z])([A-Za-z])[ \t]+(\d)", r"\1\2", text)
    text = re.sub(r"([A-Za-zₐₑₕᵢⱼₖₗₘₙₒₚᵣₛₜᵤᵥₓ₀-₉])[ \t]+([（(])", r"\1\2", text)
    text = re.sub(r"([，、])[ \t]+", r"\1", text)
    text = re.sub(r"([\u4e00-\u9fff])[ \t]+(?=[A-Za-z0-9（(])", r"\1", text)
    text = re.sub(r"([A-Za-z0-9）)}\]])[ \t]+(?=[\u4e00-\u9fff])", r"\1", text)
    text = re.sub(r"[（(][ \t]+[）)]", lambda m: "（）" if m.group(0).startswith("（") else "()", text)
    text = re.sub(r"([A-D])．[ \t]+", r"\1．", text)
    text = re.sub(r"([0-9A-Za-zπ√])//([0-9A-Za-zπ√])", r"\1/\2", text)
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def _unwrap_fragments(text: str) -> str:
    rows: list[str] = []
    buf = ""
    for raw in (text or "").splitlines():
        line = _clean_stem(raw.strip())
        if not line:
            continue
        if not buf:
            buf = line
        elif _is_fragment(line):
            buf = f"{buf} {line}"
        else:
            rows.append(buf)
            buf = line
    if buf:
        rows.append(buf)
    return "\n".join(rows)


_OPTION_D_RE = re.compile(r"(?:^|\s)D[\.．]")


def _trim_following_passage(text: str) -> str:
    """选择题的 D 项之后如果还拖着一整篇阅读，那是下一组题的原文。"""
    lines = text.splitlines()
    option_at = None
    for index, line in enumerate(lines):
        if _OPTION_D_RE.search(line):
            option_at = index
            break
    if option_at is None:
        return text
    rest = lines[option_at + 1 :]
    extra_options: list[str] = []
    index = 0
    while index < len(rest):
        line = rest[index]
        if re.match(r"\s*[E-G][.．、]", line):
            extra_options.append(line)
            index += 1
            continue
        if not line.strip():
            index += 1
            continue
        break
    tail = "\n".join(rest[index:]).strip()
    if len(tail) < 80 or _SEVEN_CUE_RE.search(tail):
        return text
    return "\n".join([*lines[: option_at + 1], *extra_options]).strip()


_GROUP_LEAD_RE = re.compile(r"据此\s*回答\s*(\d+)\s*[～~\-－到至]\s*(\d+)\s*题")
_STUCK_SECTION_RE = re.compile(
    r"[（(][一二三四五六七八九十]+[）)]\s*(?:阅读|名篇名句默写|语言文字运用)"
)
_RANGE_CUE_RE = re.compile(r"完成\s*(\d+)\s*[～~\-－到至]\s*(\d+)\s*题")


_SEVEN_CUE_RE = re.compile(r"多余选项|七选五")
_BLANK_MARK_RE = re.compile(
    r"(?:_{2,}|—{2,}|–{2,}|－{2,})\s*(\d{1,2})\s*(?:_{2,}|—{2,}|–{2,}|－{2,})"
)
_LOOSE_NO_RE = re.compile(r"(?<!\d)(\d{2})(?!\d|\.)")
_OPTION_HEAD_RE = re.compile(r"(?m)^([A-G])[.．、]\s*")


def _seven_choose_five(
    header: str, chunk: str
) -> tuple[list[tuple[str, str]], str] | None:
    """七选五的题号在空格里，选项 A–G 共用，不能按「36.」逐题去拆。"""
    if _SEVEN_CUE_RE.search(f"{header}\n{chunk}") is None:
        return None
    split = _split_shared_options(chunk)
    if split is None:
        return None
    passage, options, suffix = split
    numbers = _blank_number_run(passage)
    if len(numbers) < 3:
        return None
    pairs = [
        (str(number), _blank_snippet(passage, number, options))
        for number in numbers
    ]
    return pairs, suffix


def _split_shared_options(text: str) -> tuple[str, str, str] | None:
    matches = list(_OPTION_HEAD_RE.finditer(text))
    start = None
    letters: list[str] = []
    g_at = None
    for match in matches:
        letter = match.group(1)
        if start is None or letter <= letters[-1]:
            if g_at is not None:
                break
            start = match if letter == "A" else None
            letters = [letter] if letter == "A" else []
            g_at = None
            continue
        letters.append(letter)
        if letter == "G":
            g_at = match
            break
    if start is None or g_at is None:
        return None
    rest = text[g_at.end() :]
    next_question = _QUESTION_RE.search(rest)
    stop = g_at.end() + next_question.start() if next_question else len(text)
    options = _PAGE_MARK_RE.sub("", text[start.start() : stop]).strip()
    passage = text[: start.start()].strip()
    suffix = text[stop:].strip()
    if not passage or not options:
        return None
    return passage, options, suffix


def _blank_number_run(passage: str) -> list[int]:
    marked = [int(item) for item in _BLANK_MARK_RE.findall(passage)]
    for numbers in (
        marked,
        [int(item) for item in _BARE_QUESTION_RE.findall(passage)],
        [int(item) for item in _QUESTION_RE.findall(passage)],
        [int(item) for item in _LOOSE_NO_RE.findall(passage)],
    ):
        run = _consecutive_run(numbers)
        if len(run) >= 3:
            return run
    return []


def _consecutive_run(numbers: list[int]) -> list[int]:
    best: list[int] = []
    current: list[int] = []
    for number in numbers:
        if not current or number == current[-1] + 1:
            current.append(number)
        elif number == current[-1]:
            continue
        else:
            if len(current) > len(best):
                best = current
            current = [number]
    if len(current) > len(best):
        best = current
    return best


def _blank_snippet(passage: str, number: int, options: str) -> str:
    match = re.search(rf"(?<!\d){number}(?!\d)", passage)
    if match is None:
        return options
    start = max(0, match.start() - 160)
    end = min(len(passage), match.end() + 80)
    return f"{passage[start:end].strip()}\n{options}".strip()


def _strip_seven_block(text: str) -> str:
    match = re.search(r"(?m)^.*多余选项.*$", text)
    if match is None:
        return text
    start = match.start()
    head = text[:start]
    for token in ("第二节", "阅读下面短文"):
        pos = head.rfind(token)
        if pos >= 0:
            start = pos
            break
    return text[:start].strip()


def _lift_embedded_seven(questions: list[OutlineQuestion]) -> list[OutlineQuestion]:
    """上一题把七选五整段吞进去时，按空格题号拆出来。"""
    result: list[OutlineQuestion] = []
    existing = {question.question_no for question in questions}
    for question in questions:
        located = _seven_choose_five("", question.content)
        if not located:
            result.append(question)
            continue
        piece = located[0]
        labels = [f"单选{qno}" for qno, _text in piece]
        if all(label in existing for label in labels):
            result.append(question)
            continue
        kept = _strip_seven_block(question.content)
        if kept:
            result.append(
                OutlineQuestion(
                    question.question_no,
                    question.question_type,
                    question.question_score,
                    kept,
                )
            )
        per = _per_score(question.content[:200])
        for qno, qtext in piece:
            label = f"单选{qno}"
            if label in existing:
                continue
            result.extend(_emit_question("英语", "single", qno, qtext, per))
            existing.add(label)
    return result


def _with_leading_passage(
    header: str,
    chunk: str,
    found: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    """「（三）阅读Ⅲ」整节的文言文属于第10题，不能留在第9题后面。"""
    if not found or not re.search(r"阅读[ⅢⅣⅤⅥⅦⅧⅨ]|名篇名句默写", header or ""):
        return found
    match = _QUESTION_RE.search(chunk)
    if match is None or match.start() <= 0:
        return found
    lead = chunk[: match.start()].strip()
    cue = _RANGE_CUE_RE.search(lead)
    if cue is None or _SUB_RE.search(lead):
        return found
    target = str(int(cue.group(1)))
    updated = []
    for qno, text in found:
        if qno == target and lead[:24] not in text:
            text = f"{lead}\n{text}".strip()
        updated.append((qno, text))
    return updated


def _cut_stuck_sections(found: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """上一题的分值后面如果还粘着「（三）阅读Ⅲ」，那是下一组的材料。"""
    texts = {qno: text for qno, text in found}
    order = [qno for qno, _text in found]
    for qno in order:
        match = _STUCK_SECTION_RE.search(texts[qno])
        if match is None or match.start() == 0:
            continue
        head = texts[qno][: match.start()].strip()
        tail = texts[qno][match.start() :].strip()
        if not head:
            continue
        cue = _RANGE_CUE_RE.search(tail)
        target = str(int(cue.group(1))) if cue else _next_qno(order, qno)
        if target is None or target not in texts or target == qno:
            continue
        texts[qno] = head
        if tail[:24] not in texts[target]:
            texts[target] = f"{tail}\n{texts[target]}".strip()
    return [(qno, texts[qno]) for qno in order]


def _next_qno(order: list[str], qno: str) -> str | None:
    if qno not in order:
        return None
    index = order.index(qno)
    if index + 1 >= len(order):
        return None
    return order[index + 1]


def _move_group_leads(found: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """「据此回答4～5题」写在第3题后面，其实是下一组的材料。"""
    texts = {qno: text for qno, text in found}
    for qno, _text in found:
        lines = texts[qno].splitlines()
        option_at = next(
            (i for i in range(len(lines) - 1, -1, -1) if re.search(r"D[．.]", lines[i])),
            None,
        )
        if option_at is None:
            continue
        head = lines[:option_at]
        option_line, glued = _peel_glued_option(lines[option_at])
        tail = ([glued] if glued else []) + lines[option_at + 1 :]
        match = _GROUP_LEAD_RE.search("\n".join(tail))
        if match is None:
            continue
        target = str(int(match.group(1)))
        if target not in texts or target == qno:
            continue
        prose = [line.strip() for line in tail if line.strip() and not _is_tail_debris(line)]
        texts[qno] = "\n".join([*head, option_line]).strip()
        if prose:
            texts[target] = "\n".join([*prose, texts[target]]).strip()
    return [(qno, texts[qno]) for qno, _text in found]


def _peel_glued_option(line: str) -> tuple[str, str]:
    match = _GROUP_LEAD_RE.search(line)
    option = re.search(r"D[．.][\u4e00-\u9fff]{1,12}", line)
    if match is None or option is None or option.end() >= match.start():
        return line, ""
    return line[: option.end()].rstrip(), line[option.end() :].strip()


def _is_tail_debris(line: str) -> bool:
    text = line.strip()
    if not text or _PAGE_MARK_RE.search(text) or _is_chart_furniture(text):
        return True
    if re.fullmatch(r"[\u4e00-\u9fff]", text):
        return True
    if re.search(r"[，。；：、]", text) or "据此" in text or "回答" in text:
        return False
    if re.search(r"表\s*\d|万人|％|%|增长率", text):
        return False
    if re.search(r"\d+\.\d+", text) and re.search(r"[\u4e00-\u9fff甲乙丙丁]", text):
        return False
    return len(re.findall(r"[\u4e00-\u9fff]", text)) < 6


def _is_chart_furniture(text: str) -> bool:
    if text in {"图例", "纬度"}:
        return True
    if re.fullmatch(r"图\s*\d+", text):
        return True
    if re.search(r"[，。；：]", text):
        return False
    return re.fullmatch(r"[/°\dA-Za-z纬度月\s．.]+", text) is not None and (
        "°" in text or "纬" in text or "/" in text or text.endswith("月")
    )


def _is_figure_debris(line: str) -> bool:
    """插图上的字母、刻度和图号。选项本身、带等号的公式留下。"""
    text = line.strip()
    if not text or text.startswith("|") or re.match(r"^[A-D][．.]", text):
        return False
    if re.search(r"[=＝<>≤≥≠≈∈∪∩+×÷^，。；、▲]", text):
        return False
    if re.search(r"\d\s*/\s*\d|π\s*/", text):
        return False
    if len(re.findall(r"[A-Za-z]{3,}", text)) >= 2 or re.fullmatch(r"\d{1,2}", text):
        return False
    if re.fullmatch(r"图\s*\d+(?:\s*图\s*\d+)*", text):
        return True
    if re.fullmatch(r"(?:题\s*\d+\s*图(?:\s*[-－]\s*\d+)?)+", text):
        return True
    if text == "手机" or re.fullmatch(r"时间[（(]\s*s\s*[）)]", text):
        return True
    if re.fullmatch(r"[\u4e00-\u9fff]", text):
        return True
    if re.search(r"[\u4e00-\u9fff]", text):
        return False
    return len(text) <= 40


def _drop_figure_noise(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    kept: list[str] = []
    for line in lines:
        if _is_chart_furniture(line) or re.fullmatch(r"(?:题\s*\d+\s*图(?:\s*[-－]\s*\d+)?)+", line):
            continue
        if _is_figure_debris(line) and kept and re.match(r"^[A-D][．.]", kept[-1]):
            continue
        kept.append(line)
    while kept and _is_figure_debris(kept[-1]):
        kept.pop()
    return "\n".join(kept)


def _clip(text: str) -> str:
    stripped = _drop_figure_noise(_PAGE_MARK_RE.sub("", text or ""))
    cleaned = _trim_following_passage(_clean_stem(_unwrap_fragments(stripped)))
    if len(cleaned) <= _CONTENT_LIMIT:
        return cleaned
    return cleaned[:_CONTENT_LIMIT]


def _row(
    question_no: str,
    question_type: str,
    score: float,
    content: str,
    *,
    is_parent: bool = False,
) -> OutlineQuestion:
    return OutlineQuestion(
        question_no=question_no[:16],
        question_type=question_type,
        question_score=round(float(score or 0), 2),
        content=_clip(content),
        is_parent=is_parent,
    )
