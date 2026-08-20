import pytest

from src.agent.education.question_parser import (
    parse_question_header,
    parse_questions_from_headers,
    question_type_from_label,
)


def test_parse_question_header_with_score():
    assert parse_question_header("单选1（5.0分）") == ("单选1", 5.0)
    assert parse_question_header("多选2（3.0分）") == ("多选2", 3.0)
    assert parse_question_header("15_1（6.0分）") == ("15_1", 6.0)
    assert parse_question_header("15（13.0分）") == ("15", 13.0)
    assert parse_question_header("填空1(2.5分)") == ("填空1", 2.5)
    assert parse_question_header("  16_3 （ 8.0分 ） ") == ("16_3", 8.0)


def test_parse_question_header_invalid():
    assert parse_question_header(None) is None
    assert parse_question_header("") is None
    assert parse_question_header("   ") is None
    assert parse_question_header("单选1") is None
    assert parse_question_header("单选1（分）") is None
    assert parse_question_header("单选1（abc分）") is None
    assert parse_question_header(12345) is None


def test_parse_question_header_empty_label():
    assert parse_question_header("（5.0分）") is None


def test_question_type_from_label():
    assert question_type_from_label("单选1") == "单选题"
    assert question_type_from_label("多选2") == "多选题"
    assert question_type_from_label("15") == "解答题"
    assert question_type_from_label("15_1") == "解答题"
    assert question_type_from_label("填空1") == "解答题"


def test_parse_questions_from_headers_with_main_and_subs():
    headers = ["学号", "15（13.0分）", "15_1（6.0分）", "15_2（7.0分）", "总分"]
    questions = parse_questions_from_headers(headers)
    assert len(questions) == 3

    main = questions[0]
    assert main["question_no"] == "15"
    assert main["question_score"] == 13.0
    assert main["question_type"] == "解答题"
    assert main["is_sub"] is False
    assert main["main_no"] is None
    assert main["col_idx"] == 1

    sub1 = questions[1]
    assert sub1["question_no"] == "15_1"
    assert sub1["question_score"] == 6.0
    assert sub1["is_sub"] is True
    assert sub1["main_no"] == "15"
    assert sub1["col_idx"] == 2

    sub2 = questions[2]
    assert sub2["question_no"] == "15_2"
    assert sub2["question_score"] == 7.0
    assert sub2["is_sub"] is True
    assert sub2["main_no"] == "15"
    assert sub2["col_idx"] == 3


def test_parse_questions_skips_answer_columns():
    headers = ["单选1（5.0分）", "单选1答案", "多选2（6.0分）", "答案解析（2.0分）"]
    questions = parse_questions_from_headers(headers)
    assert len(questions) == 2
    assert questions[0]["question_no"] == "单选1"
    assert questions[1]["question_no"] == "多选2"


def test_parse_questions_score_sum_matches_main():
    headers = ["15（13.0分）", "15_1（6.0分）", "15_2（7.0分）"]
    questions = parse_questions_from_headers(headers)
    main = next(q for q in questions if not q["is_sub"])
    subs = [q for q in questions if q["is_sub"]]
    assert main["question_score"] == pytest.approx(sum(q["question_score"] for q in subs))


def test_parse_questions_from_headers_with_non_string():
    headers = ["15（13.0分）", 12345, None, "15_1（6.0分）"]
    questions = parse_questions_from_headers(headers)
    assert len(questions) == 2
    assert questions[0]["question_no"] == "15"
    assert questions[1]["question_no"] == "15_1"


def test_parse_questions_sub_edge_cases():
    # "15_" / "_1" 不是合法的子题格式（缺主号或子号），应被过滤
    headers = ["15_（5.0分）", "_1（3.0分）", "18-（3.0分）", "-1（3.0分）"]
    questions = parse_questions_from_headers(headers)
    assert len(questions) == 0


def test_parse_questions_dash_separator():
    """政治卷用连字符 '-' 而非下划线 '_'。"""
    headers = ["18-1（5.0分）", "18-2（6.0分）", "19（8.0分）", "19-1（12.0分）"]
    questions = parse_questions_from_headers(headers)
    assert len(questions) == 4
    main19 = next(q for q in questions if q["question_no"] == "19")
    assert main19["is_sub"] is False
    assert main19["main_no"] is None
    sub181 = next(q for q in questions if q["question_no"] == "18-1")
    assert sub181["is_sub"] is True
    assert sub181["main_no"] == "18"
    sub191 = next(q for q in questions if q["question_no"] == "19-1")
    assert sub191["is_sub"] is True
    assert sub191["main_no"] == "19"
