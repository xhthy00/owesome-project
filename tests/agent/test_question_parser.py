from src.agent.education.question_parser import (
    align_detail_question_columns,
    parse_questions_from_headers,
)


def test_parent_total_column_is_dropped_when_sub_questions_exist():
    headers = [
        "12（5.0分）",
        "15_1（6.0分）",
        "15_2（7.0分）",
        "15（13.0分）",
        "16-1（5.0分）",
        "16（5.0分）",
        "单选1（5.0分）",
    ]
    nos = [item["question_no"] for item in parse_questions_from_headers(headers)]
    assert nos == ["12", "15_1", "15_2", "16-1", "单选1"]


def test_standalone_question_total_is_kept():
    nos = [item["question_no"] for item in parse_questions_from_headers(["15（13.0分）"])]
    assert nos == ["15"]


def _cols(*pairs: tuple[str, float]) -> list[dict]:
    return [{"question_no": no, "question_score": score} for no, score in pairs]


def test_rollup_column_is_dropped_when_paper_has_the_leaves():
    questions = _cols(("20-1_20-1-1", 1), ("20-1_20-1-2", 1), ("20-1", 2), ("21-4", 1))
    paper = {"20-1_20-1-1", "20-1_20-1-2", "21-4"}
    kept, rename = align_detail_question_columns(questions, paper)
    assert [item["question_no"] for item in kept] == ["20-1_20-1-1", "20-1_20-1-2", "21-4"]
    assert "20-1" not in rename


def test_leaf_columns_are_dropped_when_paper_keeps_the_total():
    questions = _cols(("12-1-2-4_1", 6), ("12-1-2-4_2", 6), ("12-1-2-4", 12), ("12-5", 3))
    paper = {"12-1-2-4", "12-5"}
    kept, _rename = align_detail_question_columns(questions, paper)
    assert [item["question_no"] for item in kept] == ["12-1-2-4", "12-5"]


def test_merged_header_uses_the_question_number_after_the_comma():
    leaf = "24-2①（2），24-2②_24-2①（2）-1"
    parent = "24-2①（2），24-2②"
    questions = _cols((leaf, 1), ("24-2①（2），24-2②_24-2②", 1), (parent, 2))
    paper = {"24-2②_24-2①（2）-1", "24-2②_24-2②"}
    kept, rename = align_detail_question_columns(questions, paper)
    assert [item["question_no"] for item in kept] == ["24-2②_24-2①（2）-1", "24-2②_24-2②"]
    assert rename[leaf] == "24-2②_24-2①（2）-1"
    assert parent not in rename


def test_comma_inside_parentheses_stays_in_the_question_number():
    questions = _cols(("21-3（1，2）", 3),)
    kept, rename = align_detail_question_columns(questions, {"21-3（1，2）"})
    assert kept[0]["question_no"] == "21-3（1，2）"
    assert rename["21-3（1，2）"] == "21-3（1，2）"


def test_prefix_is_kept_when_scores_are_not_a_total():
    questions = _cols(("15_1", 6), ("15", 13))
    kept, _rename = align_detail_question_columns(questions, {"15_1", "15"})
    assert [item["question_no"] for item in kept] == ["15_1", "15"]


def test_bare_number_is_not_dropped_when_it_is_not_the_sub_total():
    nos = [
        item["question_no"]
        for item in parse_questions_from_headers(["19-1（12.0分）", "19（8.0分）"])
    ]
    assert nos == ["19-1", "19"]


def test_bare_number_maps_to_the_unmatched_paper_question_with_the_same_score():
    questions = _cols(("19-1", 12), ("19", 8))
    kept, rename = align_detail_question_columns(
        questions,
        {"19-1", "19-2"},
        {"19-1": 12.0, "19-2": 8.0},
    )
    assert [item["question_no"] for item in kept] == ["19-1", "19-2"]
    assert rename["19"] == "19-2"
