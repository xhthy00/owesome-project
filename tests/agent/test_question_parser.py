from src.agent.education.question_parser import parse_questions_from_headers


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
