"""真实试卷 PDF 解析器。

从试卷 PDF 中提取题干和子问文本，用于填充 ``tb_exam_question.content``。
"""

from __future__ import annotations

import re
from typing import Any


def read_pdf_text(path: str) -> str:
    """使用 pymupdf (fitz) 读取 PDF 全部文本，按页拼接返回一个字符串。"""
    import fitz

    text_parts: list[str] = []
    with fitz.open(path) as doc:
        for page in doc:
            text_parts.append(page.get_text())
    return "\n".join(text_parts)


class PaperParser:
    """试卷文本解析器，按题号切分并提取子问。"""

    # 行首题号：如 "1．" / "15." / "15．"。
    # 允许「题号．年份/编号」（如 15．2014 年、7．1883 年）——年份为 4 位数字。
    # 仅当后面是「短.短小数」「百分比」「选项标记 A.」等典型数据形态时才拒绝：
    #   - \d+ → 紧跟数字（无论位数，含 17.2 / 15.2014 等；后者年份模式特殊处理见下）
    #   - 区分小数（17.2）与年份（15.2014）：通过查看 `.` 后紧跟的数字个数——
    #     1-2 位数字视为小数（数据），3+ 位数字视为年份/编号（题号边界）。
    # 此外，英语 PDF 中部分题号是裸数字（"36" 单独一行），由单独的 _BARE_QNO_PATTERN 在
    # _parse_questions 内补识别。
    _QUESTION_PATTERN = re.compile(
        r"^\s*(\d{1,2})[\.．](?!\d{1,2}(?![年\d])|\d?\s*[%）)）])",
        re.MULTILINE,
    )
    # 裸题号：单独一行的纯数字（英语 36-40 / 51-55 题号被排版成独立行）。
    _BARE_QNO_PATTERN = re.compile(r"^\s*(\d{1,2})\s*$")
    # 子问：如 "（1）" / "(1)" / "（12）" / "(12)"，要求出现在行首或空白后
    _SUB_QUESTION_PATTERN = re.compile(
        r"(?:^|\n|\s)[（(](\d+)[）)]",
    )
    # 页眉页脚等噪声行
    _NOISE_PATTERNS = [
        re.compile(r"第\s*\d+\s*页"),
        re.compile(r"试卷第\s*\d+\s*页"),
        re.compile(r"共\s*\d+\s*页"),
        re.compile(r"^\s*注意事项[:：]?"),
        re.compile(r"^\s*高三\w*试卷\s*$"),
    ]

    def __init__(self) -> None:
        self.questions: dict[str, dict[str, Any]] = {}

    def load_pdf(self, path: str) -> None:
        """读取 PDF 并解析。"""
        text = read_pdf_text(path)
        self.feed_text(text)

    @staticmethod
    def _remove_precautions(text: str) -> str:
        """删除试卷开头的注意事项段落。

        从 "注意事项：" 开始，删除到以下任一停止条件：
        - 下一个空行（真实排版中"注意事项"段以空行结尾）
        - 下一个中文大题序号（一、 二、 三、 等）
        - 段头 "(本大题" / "（本大题"
        - 文件末尾

        兼容 PDF 把"注意事项"拆成单字行（如 ``注`` ``意`` ``事`` ``项`` 各自独立成行）
        ——先用 ``_collect_precautions_block`` 把这种拆字段合并并匹配起始位置。
        """
        # 先把"注 意 事 项"等被拆字的标题合并成一个连续字符串，便于后续正则匹配。
        # 注意：单字之间可能隔着空行（PDF 排版偶尔出现）。
        text = PaperParser._merge_split_precautions_header(text)

        pattern = re.compile(
            r"(注意事项|考生注意)[:：]?.*?(?=^\s*$|"
            r"^[一二三四五六七八九十]+[、.]|"
            r"^第[IVX]+卷|^第\s*\d+\s*页|^第[一二三四五六七八九十]+部分|"
            r"^[（(]本大题|\Z)",
            re.MULTILINE | re.DOTALL,
        )
        return pattern.sub("", text)

    @staticmethod
    def _merge_split_precautions_header(text: str) -> str:
        """把单字拆行的 ``注 意 事 项`` 合并为 ``注意事项``，便于被 ``_remove_precautions`` 匹配。"""
        # 匹配：单字 + 空白 + 单字 + 空白 + 单字 + 空白 + 单字（每个单字是汉字）
        split_chars = ("注", "意", "事", "项")
        pattern = re.compile(
            r"^\s*" + r"\s*\n\s*".join(split_chars) + r"\s*$",
            re.MULTILINE,
        )
        # 合并成一个连续行
        return pattern.sub("注意事项：", text)

    def feed_text(self, text: str) -> None:
        """解析试卷全文。"""
        text = self._remove_precautions(text)
        self.questions = self._parse_questions(text)

    def _is_noise_line(self, line: str) -> bool:
        """判断一行是否为页眉页脚等噪声。"""
        for pattern in self._NOISE_PATTERNS:
            if pattern.search(line):
                return True
        return False

    @staticmethod
    def _strip_mid_precautions_block(text: str) -> str:
        """删除错排在题目中间的"注意事项"段（含 1.~5. 编号列表）。

        物理/地理 PDF 偶尔把"注意事项"段错排到题目 3 之后。该段内含
        ``1．... 2．... 3．... 4．... 5．...`` 编号列表，会被误识为题号边界。

        同时一并删除注意事项段**之后**的所有"裸数字行 + 图表坐标标签行"——
        这些都是图像坐标轴标签，不是题号。

        两阶段处理：
        1. 第一段：删除"注意事项"段直到下一个页头（"第N页" / "高三xx试卷"）。
        2. 第二段：删除页头后的图表坐标/公式符号碎片（连续多行短字符如 ``2sin / cm / 2 / y / t``），
           直到第一个真正题号（带 ``.`` 或 ``．``）。
        """
        first_pass = re.compile(
            r"^[ \t]*注意事项[：:]?[^\n]*\n"
            r"(?:[^\n]*\n)*?"
            r"(?=\s*第\s*\d+\s*页|\s*[一二三四五六七八九十]?高中?\w*试卷\s*$)",
            re.MULTILINE,
        )
        text = first_pass.sub("", text)

        # 第二段：页头 + 紧跟的若干"裸短行"（非题号边界）→ 直到第一个真正题号。
        # 物理 PDF 中图表元素常被解析为多行短字符（"2sin" / "cm" / "2" / "y" / "t"），无
        # `.` 或 `．`，裸数字行也是。把它们一并删除。
        second_pass = re.compile(
            r"(?:^[ \t]*第\s*\d+\s*页[^\n]*\n|"
            r"^[ \t]*[一二三四五六七八九十]?高中?\w*试卷\s*$\n?)"
            # 允许紧跟若干"裸行"（非题号、非大题标题）。题号边界要求 \d+[．.] 或
            # 大题中文序号。
            r"(?:\s*[^\s\n]*\s*\n)*?"
            r"(?=\s*\d+[．.][^\n]*\n|\s*[（(]本大题|\s*[一二三四五六七八九十]+、)",
            re.MULTILINE,
        )
        text = second_pass.sub("", text)
        return text

    def _parse_questions(self, text: str) -> dict[str, dict[str, Any]]:
        """按题号边界切分试卷文本。

        返回 ``{question_no: {"full": str, "sub_questions": {sub_no: str}}}``。
        """
        # 预处理：把被错排在题目中间的"注意事项"段（含 1.~5. 编号列表）当作噪声删除。
        # 这种情况出现在物理/地理等 PDF：原本应在试卷开头的"注意事项"被排版到题目3 之后。
        text = self._strip_mid_precautions_block(text)
        lines = text.splitlines()
        # 先按行切分成块：每个块以行首题号开始，直到下一题号或文件结束
        blocks: list[tuple[str, list[str]]] = []
        current_no: str | None = None
        current_lines: list[str] = []
        # 上一行是否为"空白"或"题号行"——决定裸题号是否可触发。
        # 避免在题干中间误识别短裸数字行（如物理公式 "2sin / cm / 2 / y / t" 中的 "2"）。
        prev_was_block_start = True  # 初始为 True 以便接受第一个裸题号

        for raw_line in lines:
            line = raw_line.strip()
            if not line:
                # 空行视作题块之间分隔符，标志下一行若是裸数字可视为题号。
                prev_was_block_start = True
                continue
            if self._is_noise_line(line):
                prev_was_block_start = True
                continue
            m = self._QUESTION_PATTERN.match(line)
            if m:
                # 保存上一块
                if current_no is not None:
                    blocks.append((current_no, current_lines))
                qno = m.group(1)
                # 过滤明显是页码/图表编号的伪题号（>60 视为非题号；0 也是噪声）。
                # 上限放宽到 60 以容纳英语试卷（最大题号 55）。
                try:
                    n = int(qno)
                except ValueError:
                    continue
                if n == 0 or n > 60:
                    continue
                current_no = qno
                # 去掉行首题号，保留剩余内容
                rest = line[m.end() :].strip()
                current_lines = [rest] if rest else []
                # 注意：题号行紧跟的数学公式碎片行（"2" "i" 等）不应被视为新题号，
                # 因此题号边界**不**设 prev_was_block_start=True（依赖空行/页头识别）。
            else:
                # 命名章节：xls 字面量题号（如英语 "语法填空"），
                # 行首匹配 _DEFAULT_NAMED_SECTIONS；或行内包含 _DEFAULT_NAMED_SECTION_ANCHORS。
                matched_named = None
                for name in _DEFAULT_NAMED_SECTIONS:
                    if line.startswith(name):
                        matched_named = name
                        break
                if matched_named is None:
                    for name, anchor in _DEFAULT_NAMED_SECTION_ANCHORS.items():
                        if anchor in line:
                            matched_named = name
                            break
                if matched_named and matched_named != current_no:
                    if current_no is not None:
                        blocks.append((current_no, current_lines))
                    rest = line
                    if _DEFAULT_NAMED_SECTIONS.get(matched_named):
                        # 行首匹配时去掉前缀
                        kw = _DEFAULT_NAMED_SECTIONS[matched_named]
                        if rest.startswith(kw):
                            rest = rest[len(kw):].strip()
                    current_no = matched_named
                    current_lines = [rest] if rest else []
                    prev_was_block_start = True
                    continue
                # 裸题号：英语 PDF 中部分题号被排版成独立数字行（如 "36" 单独一行）。
                # 关键约束：仅当上一行是空行/页头/题号边界（prev_was_block_start）时才
                # 视为题号，避免题干中间短裸数字行误识别。
                # 例外：当裸题号紧跟在前一个题号 N 之后且 bn == N+1（顺序递增）时，
                # 也视为题号——例如英语 36-40 紧跟 35 题选项（无空行）。
                bm = self._BARE_QNO_PATTERN.match(line)
                accept_bare = False
                if bm and prev_was_block_start:
                    accept_bare = True
                elif bm and current_no is not None:
                    try:
                        prev_n = int(current_no)
                        bn_int = int(bm.group(1))
                        if bn_int == prev_n + 1:
                            accept_bare = True
                    except (ValueError, TypeError):
                        pass
                if bm and accept_bare:
                    bn = int(bm.group(1))
                    # 同样放宽到 ≤ 60 容纳英语 51-55。
                    if 0 < bn <= 60 and str(bn) != current_no:
                        if current_no is not None:
                            blocks.append((current_no, current_lines))
                        current_no = bm.group(1)
                        current_lines = []
                        prev_was_block_start = True
                        continue
                if current_no is not None:
                    current_lines.append(line)
                    # 题干追加行不再属于题块开头。
                    prev_was_block_start = False

        if current_no is not None:
            blocks.append((current_no, current_lines))

        questions: dict[str, dict[str, Any]] = {}
        for no, block_lines in blocks:
            full_text = "\n".join(block_lines)
            sub_questions = self._extract_sub_questions(block_lines)
            questions[no] = {
                "full": full_text,
                "sub_questions": sub_questions,
            }
        return questions

    def _extract_sub_questions(self, lines: list[str]) -> dict[str, str]:
        """从题块文本中提取子问。"""
        full_text = "\n".join(lines)
        matches = list(self._SUB_QUESTION_PATTERN.finditer(full_text))
        if not matches:
            return {}

        sub_questions: dict[str, str] = {}
        for i, m in enumerate(matches):
            sub_no = m.group(1)
            start = m.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(full_text)
            sub_text = full_text[start:end].strip()
            sub_questions[sub_no] = sub_text
        return sub_questions

    def build_content(self, question_no: str, sub_no: str | None = None) -> str | None:
        """构建指定题号/子问的 content 文本。

        - ``sub_no=None``：返回大题完整文本。
        - ``sub_no="1"``：返回 ``完整题干 + "\\n" + 子问1文本``。
        - 子问找不到时 fallback 返回完整题干。
        - 题号找不到时返回 ``None``。
        """
        question = self.questions.get(question_no)
        if question is None:
            return None

        full_text = question["full"]
        if sub_no is None:
            return full_text

        sub_text = question["sub_questions"].get(sub_no)
        if sub_text is None:
            return full_text

        return f"{full_text}\n{sub_text}"


_FULLWIDTH_DIGIT_RE = re.compile(r"[０-９]")
_CIRCLED_DIGIT_RE = re.compile(r"[①-⑩]")


def _to_ascii_digit(token: str) -> str | None:
    """把全角数字或圆圈数字转换为 ASCII 数字。

    例如 ``"１"`` → ``"1"``，``"①"`` → ``"1"``；非数字返回 ``None``。
    """
    if not token:
        return None
    # 圆圈数字
    cm = _CIRCLED_DIGIT_RE.match(token)
    if cm:
        return str("①②③④⑤⑥⑦⑧⑨⑩".index(cm.group(0)) + 1)
    # 全角数字
    fm = _FULLWIDTH_DIGIT_RE.match(token)
    if fm:
        return str(ord(token) - ord("０"))
    return None


# 默认命名章节：行首匹配。xls 字面量题号与 PDF 章节标题字面一致时使用。
_DEFAULT_NAMED_SECTIONS: dict[str, str] = {
    # 英语写作题：实际 PDF 中无这些字面词，仅在行首时用作默认；主要靠锚点。
}
# 默认命名章节锚点：行内包含匹配。用于 xls 字面量与 PDF 章节标题不一致的情况。
_DEFAULT_NAMED_SECTION_ANCHORS: dict[str, str] = {
    "语法填空": "在空白处填入1 个适当的单词或括号内单词的正确形式",
    "应用文写作": "假定你是",
    "读后续写": "阅读下面材料，根据其内容和所给段落开头语续写两段",
    # 语文 PDF 中"三、写作（60 分）"作为写作题章节标题
    "写作": "三、写作",
}


def _xls_no_to_pdf_no(question_no: str) -> str:
    """把 xls 表头里的题号映射为 PDF 里的题号。

    支持的 xls 题号形态：

    - ``单选N`` / ``多选N`` → ``N``
    - ``N`` / ``N_M`` / ``N-M`` → ``N``
    - ``N（M）`` / ``N（M）K`` → ``N``（全角括号内编号被剥离；圆圈数字 ①② 也视为子问
      标记，但它们不出现在 PDF 大题号边界上，仅作为多级子问）
    - ``N（M）K_N`` / ``N（M）K-N`` → 剥离括号段后剩下的题号仍以数字开头 → ``N``
    - 其它原样返回
    """
    # 先把可能出现的全角数字转 ASCII
    s = _FULLWIDTH_DIGIT_RE.sub(lambda m: str(ord(m.group(0)) - ord("０")), question_no)

    # 匹配主号：开头为可选的单选/多选 + 数字 + 可选的全角括号段（其中可有数字/圆圈数字）
    m = re.match(
        r"^(?:单选|多选)?(\d+)(?:（[^）]*）)?",
        s,
    )
    if m:
        return m.group(1)
    return question_no


def build_question_content(
    parser: PaperParser | None,
    question_no: str,
    main_no: str | None,
) -> str:
    """根据题目信息构建 content。

    - 如果 ``parser`` 为 ``None``，返回 ``"暂无"``。
    - 子题识别：xls 题号形如 ``15_1`` / ``18-1`` / ``20（1）_1`` / ``14（1）①`` 等，
      提取出 PDF 主号 + PDF 子问号。
    - 大题/单选/多选/填空：直接调用 ``question_no`` 构建。
    - ``main_no``：兜底大题号。当 ``question_no`` 拆分后主号无法命中（如题号格式
      错位）时，使用 ``main_no`` 作为大题号重新查询。
    - ``build_content`` 返回 ``None`` 时 fallback 到 ``"暂无"``。

    PDF 子问号约定：``（1）`` → ``"1"``，``（1）①`` → ``"1"``（仅取第一级括号内编号）。
    xls 多级结构 ``20（1）_1`` 拆出主号 ``20`` + 子问 ``1``；``23（1）①_1`` 拆出
    主号 ``23`` + 子问 ``"1"``（粗粒度近似到 ``（1）``）。
    """
    if parser is None:
        return "暂无"

    pdf_no, xls_sub = _split_xls_main_and_sub(question_no)

    # 子题：先按 xls 拆出的子问号去 PDF 找
    if xls_sub is not None:
        content = parser.build_content(pdf_no, xls_sub)
        if content is None and main_no:
            content = parser.build_content(_xls_no_to_pdf_no(main_no), xls_sub)
        return content if content is not None else "暂无"

    content = parser.build_content(pdf_no)
    if content is None and main_no:
        content = parser.build_content(_xls_no_to_pdf_no(main_no))
    return content if content is not None else "暂无"


def _split_xls_main_and_sub(question_no: str) -> tuple[str, str | None]:
    """从 xls 题号拆出 PDF 主号 + 子问号。

    返回 ``(pdf_main_no, sub_no_or_None)``。

    子问识别按以下优先级：

    1. ``_N`` / ``-N`` 后缀 → sub = N（如 ``15_1`` / ``18-1``）
    2. ``（M）_N`` / ``（M）-N`` 后缀 → 主号 = 数字M前的部分，子问 = N（如 ``20（1）_1``）
    3. ``（M）K`` 形式（K 为圆圈数字或数字）→ 主号 = 数字M前的部分，子问 = M
       （如 ``14（1）①`` → (14, "1")）
    """
    s = _FULLWIDTH_DIGIT_RE.sub(
        lambda m: str(ord(m.group(0)) - ord("０")), question_no
    )
    # 情况 1：尾随 _N / -N（允许主号带全角括号段，如 "20（1）_1"）
    m = re.match(r"^(\d+(?:（[^）]+）)?)[_\-](\d+)$", s)
    if m:
        # 主号还得再剥离尾随的全角括号段，回到裸数字
        raw_main = m.group(1)
        main_digits = re.match(r"^(\d+)", raw_main)
        pdf_main = main_digits.group(1) if main_digits else raw_main
        return pdf_main, m.group(2)
    # 情况 1b：尾随 _N / -N 但主号含圆圈数字（如 "23（1）①_1"）
    m = re.match(r"^(\d+（[^）]+）[①-⑩])[_\-](\d+)$", s)
    if m:
        # 取括号内的第一个数字作为 sub
        inner_match = re.search(r"（([^）]+)）", m.group(1))
        if inner_match:
            inner = inner_match.group(1)
            sub_match = re.match(r"^(\d+)", inner)
            if sub_match:
                return m.group(1).split("（")[0], sub_match.group(1)
    # 情况 2：含「（…）」且括号内含圆圈数字或数字（无 _N / -N 后缀）
    # 例：14（1）① / 23（2）①
    m = re.match(r"^(\d+)（([^）]+)）[①-⑩]$", s)
    if m:
        # sub 取括号内的第一个数字
        inner = m.group(2)
        sub_match = re.match(r"^(\d+)", inner)
        if sub_match:
            return m.group(1), sub_match.group(1)
        # 括号内只有圆圈数字：转 ASCII
        cd = _to_ascii_digit(inner.strip())
        if cd is not None:
            return m.group(1), cd
    return _xls_no_to_pdf_no(question_no), None


__all__ = ["read_pdf_text", "PaperParser", "build_question_content", "_xls_no_to_pdf_no"]
