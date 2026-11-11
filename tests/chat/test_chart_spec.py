from src.chat.service.chart_spec import (
    adapt_legacy_chart_config,
    build_tool_chart_spec,
    classify_intent,
    profile_result,
    recommend_chart_spec,
    spec_to_echarts_option,
    validate_chart_spec,
)

REGIONS = ["市直", "广陵", "邗江", "江都", "宝应", "高邮", "仪征"]
COMPARE_COLUMNS = ["区县", "3月均分", "5月均分"]
COMPARE_ROWS = [
    [name, 90 - index * 3, 88 - index * 3] for index, name in enumerate(REGIONS)
]


def test_profile_marks_time_measure_and_rank():
    profiles = profile_result(
        ["考试", "学校", "均分", "排名"],
        [["3月", "A", 88.4, 1], ["5月", "B", 86.1, 2]],
    )
    by_name = {item["name"]: item for item in profiles}
    assert by_name["考试"]["is_time"] is True
    assert by_name["均分"]["role"] == "measure"
    assert by_name["排名"]["role"] == "dimension"
    assert by_name["排名"]["is_rank"] is True


def test_classify_intent_prefers_specific_phrases():
    assert classify_intent("各区县数学均分对比") == "compare"
    assert classify_intent("近两次考试数学均分走势") == "trend"
    assert classify_intent("数学均分前 10 的学校") == "ranking"
    assert classify_intent("列出临界生名单") == "detail"
    assert classify_intent("分数段分布") == "distribution"


def test_compare_produces_grouped_column_without_rows_in_spec():
    spec = recommend_chart_spec(
        question="对比各区县 3 月与 5 月数学均分",
        columns=COMPARE_COLUMNS,
        rows=COMPARE_ROWS,
        result_index=2,
    )
    assert spec is not None
    assert spec["type"] == "groupedColumn"
    assert spec["encoding"] == {"y": ["3月均分", "5月均分"], "x": "区县"}
    assert "rows" not in spec["dataset"]
    assert spec["source"]["resultIndex"] == 2
    assert validate_chart_spec(spec, COMPARE_COLUMNS, COMPARE_ROWS) == []


def test_trend_produces_line_and_sorts_nothing():
    columns = ["月份", "均分"]
    rows = [["2026-03", 78.6], ["2026-04", 77.2], ["2026-05", 76.2]]
    spec = recommend_chart_spec(question="数学均分走势", columns=columns, rows=rows)
    assert spec is not None
    assert spec["type"] == "line"
    assert spec["encoding"]["x"] == "月份"
    assert spec["options"]["showLabel"] is False


def test_ranking_uses_measure_as_bar_length_not_rank_column():
    columns = ["学校", "排名", "均分"]
    rows = [["A", 1, 106.8], ["B", 2, 100.9], ["C", 3, 96.4]]
    spec = recommend_chart_spec(question="各校数学均分前 3", columns=columns, rows=rows)
    assert spec is not None
    assert spec["type"] == "bar"
    assert spec["encoding"]["y"] == ["均分"]
    assert spec["transform"]["sort"] == {"by": "均分", "order": "desc"}


def test_composition_donut_for_small_non_negative_slices():
    columns = ["分数段", "人数"]
    rows = [["140+", 8], ["120-140", 22], ["100-120", 31], ["80-100", 24]]
    spec = recommend_chart_spec(question="分数段人数占比", columns=columns, rows=rows)
    assert spec is not None
    assert spec["type"] == "donut"
    assert spec["encoding"]["y"] == ["人数"]


def test_composition_falls_back_to_bar_for_too_many_slices():
    columns = ["学校", "人数"]
    rows = [[f"S{index}", index + 1] for index in range(8)]
    spec = recommend_chart_spec(question="各校人数占比", columns=columns, rows=rows)
    assert spec is not None
    assert spec["type"] == "bar"


def test_single_row_is_kpi_not_bar():
    spec = recommend_chart_spec(
        question="全市高三 3 月数学平均分是多少",
        columns=["考试", "均分"],
        rows=[["3月", 78.6]],
    )
    assert spec is not None
    assert spec["type"] == "kpi"


def test_detail_intent_keeps_table():
    spec = recommend_chart_spec(
        question="列出临界生名单",
        columns=["学校", "学生", "分数"],
        rows=[["A", "S1", 58], ["B", "S2", 57]],
    )
    assert spec is not None
    assert spec["type"] == "table"


def test_validation_rejects_missing_column_and_non_numeric_y():
    spec = recommend_chart_spec(
        question="对比各区县 3 月与 5 月数学均分",
        columns=COMPARE_COLUMNS,
        rows=COMPARE_ROWS,
    )
    assert spec is not None
    bad = {**spec, "encoding": {"x": "不存在", "y": ["区县"]}}
    errors = validate_chart_spec(bad, COMPARE_COLUMNS, COMPARE_ROWS)
    assert any("x 列不存在" in item for item in errors)
    assert any("不是数值" in item for item in errors)


def test_tool_chart_spec_accepts_valid_hint_and_rejects_missing_y():
    spec, error = build_tool_chart_spec(
        kind="bar",
        x_column="区县",
        y_columns=["3月均分"],
        columns=COMPARE_COLUMNS,
        rows=COMPARE_ROWS,
    )
    assert error == ""
    assert spec is not None and spec["type"] == "bar"

    missing, error = build_tool_chart_spec(
        kind="bar",
        x_column="不存在",
        y_columns=["3月均分"],
        columns=COMPARE_COLUMNS,
        rows=COMPARE_ROWS,
    )
    assert missing is None
    assert error


def test_spec_to_echarts_option_builds_series():
    spec = recommend_chart_spec(
        question="对比各区县 3 月与 5 月数学均分",
        columns=COMPARE_COLUMNS,
        rows=COMPARE_ROWS,
    )
    assert spec is not None
    option = spec_to_echarts_option(spec, COMPARE_ROWS, COMPARE_COLUMNS)
    assert option["xAxis"]["data"][0] == "市直"
    assert len(option["series"]) == 2
    assert option["series"][0]["type"] == "bar"


def test_legacy_echarts_option_converts_to_spec():
    legacy = {
        "xAxis": {"type": "category", "data": ["A", "B"]},
        "series": [{"name": "均分", "type": "bar", "data": [90, 80]}],
        "title": {"text": "各校均分"},
    }
    spec = adapt_legacy_chart_config("bar", legacy)
    assert spec is not None
    assert spec["type"] == "column"
    assert spec["dataset"]["rows"] == [["A", 90], ["B", 80]]
    assert validate_chart_spec(spec, ["维度", "均分"], spec["dataset"]["rows"]) == []


def test_id_columns_are_dimensions_not_measures():
    profiles = profile_result(
        ["exam_batch_id", "class_name", "recorded_count", "pass_rate"],
        [[2, "高三(1)班", 52, 100], [2, "高三(2)班", 48, 98]],
    )
    by_name = {item["name"]: item for item in profiles}
    assert by_name["exam_batch_id"]["is_id"] is True
    assert by_name["exam_batch_id"]["role"] == "dimension"
    assert by_name["recorded_count"]["role"] == "measure"
    assert by_name["pass_rate"]["role"] == "measure"


def test_id_column_cannot_be_used_as_chart_y():
    spec = {
        "kind": "spec",
        "version": 1,
        "type": "column",
        "dataset": {"columns": []},
        "encoding": {"x": "class_name", "y": ["exam_batch_id"]},
    }
    errors = validate_chart_spec(
        spec,
        ["exam_batch_id", "class_name", "recorded_count"],
        [[2, "高三(1)班", 52]],
    )
    assert any("标识列" in item for item in errors)
