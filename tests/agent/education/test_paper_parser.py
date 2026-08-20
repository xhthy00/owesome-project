from src.agent.education.paper_parser import (
    PaperParser,
    build_question_content,
    read_pdf_text,
)

SAMPLE_PAPER = """数学试卷第1页（共6 页）
高三数学试卷
注意事项：
1．答题前，先将自己的姓名、准考证号填写在试卷和答题卡上。
2．选择题使用2B铅笔...

1．设集合 A={x|x>0}, B=...，则 A∩B=...
2．已知复数 z=...，则 |z|=...
...
15．（13分）已知函数 f(x)=...
（1）求 f(x) 的单调区间；
（2）若 a>0，求 f(x) 在 [0,a] 上的最大值；
（3）证明：当 x>0 时，... .
16．（12分）已知数列 ...
(1) 求通项公式；
(2) 求前 n 项和。
"""


def test_parse_text_blocks():
    parser = PaperParser()
    parser.feed_text(SAMPLE_PAPER)

    # 应识别题号 1、2、15、16
    assert "1" in parser.questions
    assert "2" in parser.questions
    assert "15" in parser.questions
    assert "16" in parser.questions

    # 题号 1 是大题/单选，无子问
    assert parser.questions["1"]["sub_questions"] == {}

    # 题号 15 应拆出 3 个中文括号子问
    subs_15 = parser.questions["15"]["sub_questions"]
    assert set(subs_15.keys()) == {"1", "2", "3"}
    assert "求 f(x) 的单调区间" in subs_15["1"]
    assert "最大值" in subs_15["2"]
    assert "证明" in subs_15["3"]

    # 题号 16 应拆出 2 个半角括号子问
    subs_16 = parser.questions["16"]["sub_questions"]
    assert set(subs_16.keys()) == {"1", "2"}
    assert "通项公式" in subs_16["1"]
    assert "前 n 项和" in subs_16["2"]


def test_build_content_main():
    parser = PaperParser()
    parser.feed_text(SAMPLE_PAPER)

    content = parser.build_content("15")
    assert content is not None
    assert "已知函数 f(x)" in content
    assert "求 f(x) 的单调区间" in content
    # 大题 content 应包含完整题干和全部子问
    assert "（1）" in content or "(1)" in content


def test_build_content_sub():
    parser = PaperParser()
    parser.feed_text(SAMPLE_PAPER)

    content = parser.build_content("15", sub_no="2")
    assert content is not None
    assert "已知函数 f(x)" in content  # 完整题干
    assert "若 a>0" in content        # 子问 2
    # 子题 content = 完整题干 + "\n" + 对应子问文本；
    # 验证子问 2 在末尾被追加了一次即可
    assert content.endswith("（2）若 a>0，求 f(x) 在 [0,a] 上的最大值；")


def test_build_content_missing_fallback():
    parser = PaperParser()
    parser.feed_text(SAMPLE_PAPER)

    # 题号 99 不存在
    assert parser.build_content("99") is None
    # 子问不存在时 fallback 返回完整题干
    assert parser.build_content("15", sub_no="99") == parser.questions["15"]["full"]


def test_read_pdf_uses_fitz(monkeypatch):
    """验证 read_pdf_text 会调用 fitz.open。"""
    calls = []

    class FakePage:
        def get_text(self) -> str:
            return "fake page text"

    class FakeDoc:
        def __iter__(self):
            return iter([FakePage(), FakePage()])

        def __enter__(self):
            return self

        def __exit__(self, *args, **kwargs):
            return False

    def fake_open(path: str):
        calls.append(path)
        return FakeDoc()

    monkeypatch.setattr("fitz.open", fake_open)

    result = read_pdf_text("/fake/path/paper.pdf")
    assert len(calls) == 1
    assert calls[0] == "/fake/path/paper.pdf"
    assert "fake page text" in result


def test_build_question_content_with_parser():
    parser = PaperParser()
    parser.feed_text(SAMPLE_PAPER)

    # 子题
    content = build_question_content(parser, "15_2", main_no="15")
    assert "若 a>0" in content

    # 大题
    content = build_question_content(parser, "15", main_no=None)
    assert "已知函数 f(x)" in content


def test_build_question_content_with_none_parser():
    assert build_question_content(None, "15_1", "15") == "暂无"


def test_build_question_content_missing():
    parser = PaperParser()
    parser.feed_text(SAMPLE_PAPER)
    assert build_question_content(parser, "99", None) == "暂无"


def test_precautions_removed():
    """注意事项里的编号不应被误认为题目。"""
    text = """数学试卷
注意事项：
1．答题前，先将自己的姓名、准考证号填写在试卷和答题卡上。
2．选择题使用2B铅笔填涂答题卡。

3．第3题内容
4．第4题内容
"""
    parser = PaperParser()
    parser.feed_text(text)
    assert "1" not in parser.questions
    assert "2" not in parser.questions
    assert "3" in parser.questions
    assert "4" in parser.questions


def test_sub_question_not_misled_by_inline_parens():
    """题干中的 f(1)、（2024）等括号不应被识别为子问。"""
    text = """15．已知函数 f(x)=...，则 f(1) 的值为（    ）
A. 1  B. 2  C. 3  D. 4

16．（2024年高考题）已知数列 ...
（1）求通项公式；
"""
    parser = PaperParser()
    parser.feed_text(text)

    # 第 15 题 inline 的 f(1) 和选项括号都不应生成子问
    assert parser.questions["15"]["sub_questions"] == {}

    # 第 16 题只应识别出真正的子问 1，而不是行首的 （2024）
    subs_16 = parser.questions["16"]["sub_questions"]
    assert set(subs_16.keys()) == {"1"}
    assert "求通项公式" in subs_16["1"]


def test_main_no_fallback():
    """当 question_no 无法直接命中时，main_no 作为兜底大题题号。"""
    parser = PaperParser()
    parser.feed_text(SAMPLE_PAPER)

    # 子题形式，main_no 与拆分出的大题号一致
    content = build_question_content(parser, "15_1", main_no="15")
    assert "已知函数 f(x)" in content  # 完整题干


def test_xls_dash_separator_political():
    """政治卷用 '-' 分隔子题 (18-1, 18-2)，也能正确映射到 PDF 题号。"""
    parser = PaperParser()
    parser.feed_text(SAMPLE_PAPER)

    # SAMPLE_PAPER 里有题号 15，模拟政治卷把同一题写成 "15-1"
    content = build_question_content(parser, "15-1", main_no="15")
    assert "已知函数 f(x)" in content  # 完整题干
    # 子问文本应包含
    assert "（1）" in content or "（2）" in content


def test_no_false_positive_for_decimal_number():
    """题号后跟数字（如 "20.0%" / "18.0 分"）不应被误判为题号。"""
    text = "题干里出现 20.0% 的数据\n18．（13分）这是真正的大题\n（1）这是子问"
    parser = PaperParser()
    parser.feed_text(text)
    # 题号 20 不应被识别；题号 18 应被识别
    assert "20" not in parser.questions
    assert "18" in parser.questions
    assert "这是真正的大题" in parser.questions["18"]["full"]


def test_qno_with_year_not_filtered():
    r"""题号后跟 4 位年份（如 15．2014 年、7．1883 年）应被识别为题号边界。

    修复前正则 ``(?!\d)`` 会拒绝任何题号.数字 模式，把 15．2014 误判为数据。
    """
    text = (
        "15．2014 年出生在澳大利亚的一对姐弟\n"
        "A．选项A\n"
        "B．选项B\n"
        "7．1883 年《申报》社评：自海禁开放\n"
        "C．选项C\n"
    )
    parser = PaperParser()
    parser.feed_text(text)
    assert "15" in parser.questions
    assert "7" in parser.questions
    assert "2014" in parser.questions["15"]["full"]
    assert "1883" in parser.questions["7"]["full"]


def test_bare_qno_recognized():
    """题号被排版成独立数字行（英语 36-40、51-55）也应识别。

    关键上下文：裸题号行必须紧跟空行/页头才被识别——避免题干中间短数字误判。
    """
    text = (
        "35. AR 的优势\n"
        "A. 选项A\n"
        "B. 选项B\n"
        "\n"
        "36\n"
        "To address this issue, ...\n"
        "\n"
        "37\n"
        "This allows you to start.\n"
    )
    parser = PaperParser()
    parser.feed_text(text)
    assert "35" in parser.questions
    assert "36" in parser.questions
    assert "37" in parser.questions
    assert "To address this issue" in parser.questions["36"]["full"]
    assert "This allows you to start" in parser.questions["37"]["full"]


def test_named_section_anchor():
    """命名章节锚点：xls 字面量题号（"语法填空"）对应 PDF 中的非字面章节。

    通过行内包含子串匹配触发。
    """
    text = (
        "50. ending\n"
        "第四节（共两节，满分40 分）\n"
        "第一节（满分15 分）\n"
        "假定你是校历史社团成员李华\n"
        "第四节\n"
        "第二节（满分25 分）\n"
        "阅读下面材料，根据其内容和所给段落开头语续写两段\n"
        "It took only seconds.\n"
    )
    parser = PaperParser()
    parser.feed_text(text)
    # 命名章节应当被加入
    assert "应用文写作" in parser.questions
    assert "读后续写" in parser.questions
    assert "假定你是" in parser.questions["应用文写作"]["full"]
    assert "It took only seconds" in parser.questions["读后续写"]["full"]


def test_split_xls_main_and_sub_fullwidth_paren():
    """xls 题号含全角括号（20（1）_1 / 14（1）①）应正确拆分主号+子问。"""
    from src.agent.education.paper_parser import (
        _split_xls_main_and_sub,
    )

    # 主号带全角括号 + 子问序号
    assert _split_xls_main_and_sub("20（1）_1") == ("20", "1")
    assert _split_xls_main_and_sub("20（3）_4") == ("20", "4")
    # 主号带全角括号 + 圆圈数字后缀
    assert _split_xls_main_and_sub("14（1）①") == ("14", "1")
    assert _split_xls_main_and_sub("23（2）①") == ("23", "2")
    # 主号带全角括号 + 圆圈数字后缀 + 子问下标
    assert _split_xls_main_and_sub("23（1）①_1") == ("23", "1")
    # 基础情形
    assert _split_xls_main_and_sub("15_1") == ("15", "1")
    assert _split_xls_main_and_sub("18-1") == ("18", "1")
    # 大题
    assert _split_xls_main_and_sub("单选15") == ("15", None)
    assert _split_xls_main_and_sub("20（1）") == ("20", None)
