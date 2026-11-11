"""ChartSpec：问答结果的图表协议与确定性选型。

设计原则：
- 只消费 ``query_semantic`` 已返回的 columns / rows，不额外查数；
- 后端只产出语义化 spec（类型、编码、排序、参考线），前端负责渲染；
- 规则选型是兜底，模型给出的 hint 只有在列/类型校验通过后才会被采用；
- 不把 rows 重复塞进 SSE：默认用 ``source.resultIndex`` 让前端关联已有结果。

P0 支持的类型：kpi、column、bar、groupedColumn、stackedColumn、line、area、
donut、pie、table。高级图形（scatter / heatmap / radar / bullet / funnel /
waterfall / sankey / treemap）在 P1/P2 扩展，不在本模块的校验白名单内。
"""

from __future__ import annotations

import math
import re
from typing import Any, Sequence

__all__ = [
    "SUPPORTED_CHART_TYPES",
    "classify_intent",
    "profile_result",
    "recommend_chart_spec",
    "validate_chart_spec",
    "build_tool_chart_spec",
    "spec_to_echarts_option",
    "adapt_legacy_chart_config",
]

SUPPORTED_CHART_TYPES = (
    "kpi",
    "column",
    "bar",
    "groupedColumn",
    "stackedColumn",
    "line",
    "area",
    "donut",
    "pie",
    "table",
)

_TYPE_ALIASES = {
    "histogram": "column",
    "hbar": "bar",
    "horizontal": "bar",
    "horizontal_bar": "bar",
    "grouped": "groupedColumn",
    "grouped_bar": "groupedColumn",
    "stacked": "stackedColumn",
    "stacked_bar": "stackedColumn",
    "pie_chart": "pie",
    "donut_chart": "donut",
    "line_chart": "line",
    "area_chart": "area",
}

_TIME_PATTERN = re.compile(
    r"(时间|日期|月份|月度|场次|考试|期次|学年|学期|period|date|month|term|year)",
    re.IGNORECASE,
)
_RATE_PATTERN = re.compile(r"(率|占比|比例|百分比|rate|ratio|pct|percent)", re.IGNORECASE)
_RANK_PATTERN = re.compile(r"(排名|名次|位次|rank|rk)", re.IGNORECASE)
_ID_PATTERN = re.compile(r"(^|_)(id|code|no|sn)$|(编号|代码|批次|学号|考号|序号)$", re.IGNORECASE)
_COUNT_PATTERN = re.compile(
    r"(人数|考生|参考|数量|学校数|班级数|校数|班数|count|num)", re.IGNORECASE
)
_SCORE_PATTERN = re.compile(r"(均分|平均分|得分|分数|成绩|score|avg)", re.IGNORECASE)

_PALETTE = ["#2563eb", "#60a5fa", "#7c3aed", "#d97706", "#059669", "#dc2626", "#0891b2", "#94a3b8"]


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _to_number(value: Any) -> float | None:
    """把结果单元格转成有限数值；日期/文本返回 None。"""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    text = str(value).strip().replace(",", "").replace("，", "").replace("％", "%")
    if not text or text in {"-", "—", "–", "无", "null", "None"}:
        return None
    if text.endswith("%"):
        text = text[:-1].strip()
    if not re.fullmatch(r"[+-]?\d+(?:\.\d+)?", text):
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _is_rate(name: str) -> bool:
    return bool(_RATE_PATTERN.search(name))


def _is_rank(name: str) -> bool:
    return bool(_RANK_PATTERN.search(name)) and not _is_rate(name)


def _is_id(name: str) -> bool:
    return bool(_ID_PATTERN.search(name))


def _is_count(name: str) -> bool:
    return bool(_COUNT_PATTERN.search(name))


def _is_score(name: str) -> bool:
    return bool(_SCORE_PATTERN.search(name))


def _is_time(name: str) -> bool:
    return bool(_TIME_PATTERN.search(name))


def _unit_for(name: str) -> str:
    if _is_rate(name):
        return "%"
    if _is_score(name):
        return "分"
    if _is_count(name):
        return "人"
    return ""


def profile_result(
    columns: Sequence[str], rows: Sequence[Sequence[Any]]
) -> list[dict[str, Any]]:
    """逐列画像：时间 / 数值 / 比率 / 名次 / 类别基数。"""
    profiles: list[dict[str, Any]] = []
    for index, raw_name in enumerate(columns):
        name = _text(raw_name)
        values = [row[index] if index < len(row) else None for row in rows]
        non_empty = [value for value in values if value not in (None, "")]
        numbers = [_to_number(value) for value in non_empty]
        numeric = bool(non_empty) and all(number is not None for number in numbers)
        distinct = len({_text(value) for value in non_empty})
        id_like = _is_id(name)
        time_like = _is_time(name) and not id_like
        rate = _is_rate(name)
        rank = _is_rank(name)
        profiles.append(
            {
                "name": name,
                "label": name,
                "numeric": numeric,
                "cardinality": distinct,
                "has_null": len(non_empty) < len(values),
                "is_time": time_like,
                "is_rate": rate,
                "is_rank": rank,
                "is_id": id_like,
                "is_count": _is_count(name),
                "is_score": _is_score(name),
                "role": (
                    "dimension"
                    if id_like
                    else "time"
                    if time_like
                    else "measure"
                    if numeric and not rank
                    else "dimension"
                ),
                "type": (
                    "string"
                    if id_like
                    else "time"
                    if time_like
                    else "percent"
                    if rate and numeric
                    else "number"
                    if numeric
                    else "string"
                ),
            }
        )
    return profiles


def classify_intent(question: str) -> str:
    """规则识别问题意图；只用于选图，不触发报告。"""
    text = (question or "").strip().lower()
    if not text:
        return "auto"
    if re.search(r"(趋势|走势|变化|近\s*\d*\s*次|历次|两场|同比|环比|上升|下滑|下降)", text):
        return "trend"
    if re.search(r"(占比|比例|构成|结构|组成|份额)", text):
        return "composition"
    if re.search(r"(分布|分数段|分段|集中度|波动|离散|标准差)", text):
        return "distribution"
    if re.search(r"(相关|关系|影响|关联)", text):
        return "correlation"
    if re.search(r"(前\s*\d+|后\s*\d+|top\s*\d+|排名|名次|位次|最好|最差|最高|最低)", text):
        return "ranking"
    if re.search(r"(名单|清单|逐条|明细|列出|哪些)", text):
        return "detail"
    if re.search(r"(达标|完成率|目标|临界|距离.*还差|差多少)", text):
        return "target"
    if re.search(r"(对比|比较|差距|谁高|谁低|横向)", text):
        return "compare"
    return "auto"


def _measure_columns(profiles: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return [p for p in profiles if p["role"] == "measure" and not p["is_rank"]]


def _dimension_columns(profiles: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    dimensions = [p for p in profiles if p["role"] in {"dimension", "time"}]
    return [p for p in dimensions if not p["is_id"]] or dimensions


def _profile_for(profiles: Sequence[dict[str, Any]], name: str | None) -> dict[str, Any] | None:
    if not name:
        return None
    return next((p for p in profiles if p["name"] == name), None)


def _label(name: str | None) -> str:
    return _text(name) or "数值"


def _spec_columns(profiles: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    columns: list[dict[str, Any]] = []
    for profile in profiles:
        item: dict[str, Any] = {
            "name": profile["name"],
            "label": profile["label"],
            "role": profile["role"],
            "type": profile["type"],
        }
        unit = _unit_for(profile["name"])
        if unit:
            item["unit"] = unit
        columns.append(item)
    return columns


def _title_for(
    *, title: str, profiles: Sequence[dict[str, Any]], x: str | None, y: Sequence[str]
) -> str:
    if title.strip():
        return title.strip()
    y_labels = [_label(name) for name in y]
    if len(y_labels) == 1:
        return f"{y_labels[0]}（按{_label(x)}）" if x else y_labels[0]
    if y_labels:
        joined = "、".join(y_labels[:3])
        return f"{joined}（按{_label(x)}）" if x else joined
    return "查询结果"


def _subtitle_for(profiles: Sequence[dict[str, Any]], y: Sequence[str], row_count: int) -> str:
    unit = next((_unit_for(name) for name in y if _unit_for(name)), "")
    parts = [f"单位：{unit}"] if unit else []
    parts.append(f"共 {row_count} 行")
    return " · ".join(parts)


def _make_spec(
    chart_type: str,
    *,
    profiles: Sequence[dict[str, Any]],
    rows: Sequence[Sequence[Any]],
    x: str | None = None,
    y: Sequence[str] = (),
    series: str | None = None,
    title: str = "",
    result_index: int = 0,
    sort: dict[str, str] | None = None,
    top_n: int | None = None,
    show_label: bool | None = None,
    unit: str = "",
    annotations: dict[str, Any] | None = None,
) -> dict[str, Any]:
    y = [name for name in y if name]
    encoding: dict[str, Any] = {"y": list(y)}
    if x:
        encoding["x"] = x
    if series:
        encoding["series"] = series
    x_profile = _profile_for(profiles, x)
    options: dict[str, Any] = {
        "yStartAtZero": chart_type
        in {"column", "bar", "groupedColumn", "stackedColumn", "area"},
    }
    if show_label is None:
        show_label = bool(
            chart_type in {"column", "bar", "groupedColumn", "stackedColumn", "donut", "pie"}
            and (x_profile is None or x_profile["cardinality"] <= 12)
        )
    options["showLabel"] = show_label
    spec: dict[str, Any] = {
        "kind": "spec",
        "version": 1,
        "type": chart_type,
        "title": _title_for(title=title, profiles=profiles, x=x, y=y),
        "subtitle": _subtitle_for(profiles, y, len(rows)),
        "dataset": {"columns": _spec_columns(profiles), "rowCount": len(rows)},
        "encoding": encoding,
        "options": options,
        "source": {"resultIndex": max(0, int(result_index)), "rowCount": len(rows)},
    }
    if unit:
        spec["unit"] = unit
    transform: dict[str, Any] = {}
    if sort:
        transform["sort"] = sort
    if top_n:
        transform["topN"] = max(1, int(top_n))
    if transform:
        spec["transform"] = transform
    if annotations:
        spec["annotations"] = annotations
    return spec


def _table_spec(
    profiles: Sequence[dict[str, Any]],
    rows: Sequence[Sequence[Any]],
    *,
    result_index: int,
    title: str = "",
) -> dict[str, Any]:
    return _make_spec(
        "table",
        profiles=profiles,
        rows=rows,
        title=title,
        result_index=result_index,
        show_label=False,
    )


def _kpi_spec(
    profiles: Sequence[dict[str, Any]],
    rows: Sequence[Sequence[Any]],
    *,
    result_index: int,
    title: str = "",
) -> dict[str, Any]:
    return _make_spec(
        "kpi",
        profiles=profiles,
        rows=rows,
        title=title,
        result_index=result_index,
        show_label=False,
    )


def _non_negative(profile: dict[str, Any], rows: Sequence[Sequence[Any]]) -> bool:
    index = profile.get("__index__")
    if not isinstance(index, int):
        return True
    values = [
        _to_number(row[index]) if index < len(row) else None
        for row in rows
    ]
    return all(value is None or value >= 0 for value in values)


def _with_indexes(profiles: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{**profile, "__index__": index} for index, profile in enumerate(profiles)]


def _normalize_type(value: Any) -> str | None:
    chart_type = _text(value)
    if not chart_type:
        return None
    chart_type = _TYPE_ALIASES.get(chart_type.lower(), chart_type)
    return chart_type if chart_type in SUPPORTED_CHART_TYPES else None


def _spec_from_hint(
    hint: dict[str, Any] | None,
    *,
    profiles: Sequence[dict[str, Any]],
    columns: Sequence[str],
    rows: Sequence[Sequence[Any]],
    result_index: int,
) -> dict[str, Any] | None:
    if not isinstance(hint, dict):
        return None
    chart_type = _normalize_type(hint.get("type") or hint.get("chart_type"))
    if not chart_type:
        return None
    x = _text(hint.get("x") or hint.get("x_column")) or None
    raw_y = hint.get("y") or hint.get("y_columns") or []
    if isinstance(raw_y, str):
        raw_y = [raw_y]
    y = [_text(name) for name in raw_y if _text(name)]
    series = _text(hint.get("series") or hint.get("series_column")) or None
    if x and x not in columns:
        return None
    if any(name not in columns for name in y):
        return None
    if series and series not in columns:
        return None
    if chart_type not in {"kpi", "table"} and not y:
        return None
    title = _text(hint.get("title"))
    unit = _text(hint.get("unit"))
    top_n = hint.get("top_n")
    return _make_spec(
        chart_type,
        profiles=profiles,
        rows=rows,
        x=x,
        y=y,
        series=series,
        title=title,
        result_index=result_index,
        top_n=top_n if isinstance(top_n, int) and not isinstance(top_n, bool) else None,
        show_label=hint.get("show_label"),
        unit=unit,
    )


def recommend_chart_spec(
    *,
    question: str = "",
    columns: Sequence[str],
    rows: Sequence[Sequence[Any]],
    result_index: int = 0,
    title: str = "",
    hint: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """根据问题意图 + 数据形态选图；返回 None 表示不出图。"""
    if not columns or not rows:
        return None
    profiles = _with_indexes(profile_result(columns, rows))
    hinted = _spec_from_hint(
        hint,
        profiles=profiles,
        columns=columns,
        rows=rows,
        result_index=result_index,
    )
    if hinted and not validate_chart_spec(hinted, columns, rows):
        return hinted

    intent = classify_intent(question)
    measures = _measure_columns(profiles)
    dimensions = _dimension_columns(profiles)
    time_columns = [profile for profile in profiles if profile["is_time"]]

    if not measures:
        return _table_spec(profiles, rows, result_index=result_index, title=title)

    if intent == "detail":
        return _table_spec(profiles, rows, result_index=result_index, title=title)

    if len(rows) == 1:
        return _kpi_spec(profiles, rows, result_index=result_index, title=title)

    x_profile = (
        time_columns[0]
        if intent == "trend" and time_columns
        else dimensions[0]
        if dimensions
        else time_columns[0]
        if time_columns
        else None
    )
    if x_profile is None:
        return _table_spec(profiles, rows, result_index=result_index, title=title)

    x_name = x_profile["name"]
    cardinality = int(x_profile["cardinality"])
    primary = measures[0]

    if intent == "composition":
        if len(measures) == 1 and 3 <= cardinality <= 6 and _non_negative(primary, rows):
            return _make_spec(
                "donut",
                profiles=profiles,
                rows=rows,
                x=x_name,
                y=[primary["name"]],
                title=title,
                result_index=result_index,
                sort={"by": primary["name"], "order": "desc"},
            )
        if len(measures) >= 2 and cardinality <= 12:
            return _make_spec(
                "stackedColumn",
                profiles=profiles,
                rows=rows,
                x=x_name,
                y=[measure["name"] for measure in measures[:3]],
                title=title,
                result_index=result_index,
            )
        return _make_spec(
            "bar",
            profiles=profiles,
            rows=rows,
            x=x_name,
            y=[primary["name"]],
            title=title,
            result_index=result_index,
            sort={"by": primary["name"], "order": "desc"},
        )

    if intent == "trend" and x_profile["is_time"]:
        return _make_spec(
            "line",
            profiles=profiles,
            rows=rows,
            x=x_name,
            y=[measure["name"] for measure in measures[:4]],
            title=title,
            result_index=result_index,
            show_label=False,
        )

    if intent == "ranking":
        return _make_spec(
            "bar",
            profiles=profiles,
            rows=rows,
            x=x_name,
            y=[primary["name"]],
            title=title,
            result_index=result_index,
            sort={"by": primary["name"], "order": "desc"},
            top_n=20 if len(rows) > 20 else None,
        )

    if intent == "distribution":
        return _make_spec(
            "column",
            profiles=profiles,
            rows=rows,
            x=x_name,
            y=[measure["name"] for measure in measures[:2]],
            title=title,
            result_index=result_index,
        )

    if intent == "correlation":
        # 散点图在 P1；P0 不强行用柱状图表达相关性。
        return _table_spec(profiles, rows, result_index=result_index, title=title)

    if len(measures) >= 2 and cardinality <= 12:
        return _make_spec(
            "groupedColumn",
            profiles=profiles,
            rows=rows,
            x=x_name,
            y=[measure["name"] for measure in measures[:3]],
            title=title,
            result_index=result_index,
        )

    if cardinality > 12:
        return _make_spec(
            "bar",
            profiles=profiles,
            rows=rows,
            x=x_name,
            y=[primary["name"]],
            title=title,
            result_index=result_index,
            sort={"by": primary["name"], "order": "desc"},
            top_n=20 if len(rows) > 20 else None,
        )

    return _make_spec(
        "column",
        profiles=profiles,
        rows=rows,
        x=x_name,
        y=[measure["name"] for measure in measures[:2]],
        title=title,
        result_index=result_index,
        sort={"by": primary["name"], "order": "desc"} if intent == "compare" else None,
    )


def validate_chart_spec(
    spec: dict[str, Any] | None,
    columns: Sequence[str],
    rows: Sequence[Sequence[Any]],
) -> list[str]:
    """校验 spec 是否可安全渲染；返回错误列表（空列表表示通过）。"""
    if not isinstance(spec, dict):
        return ["spec 必须是对象"]
    errors: list[str] = []
    if spec.get("version") != 1:
        errors.append("不支持的 spec 版本")
    chart_type = spec.get("type")
    if chart_type not in SUPPORTED_CHART_TYPES:
        errors.append(f"不支持的图表类型: {chart_type}")
        return errors
    if chart_type in {"kpi", "table"}:
        return errors
    encoding = spec.get("encoding") or {}
    x_name = encoding.get("x")
    y_names = encoding.get("y") or []
    if not isinstance(y_names, list) or not y_names:
        errors.append("缺少 y 编码")
        return errors
    if chart_type not in {"donut", "pie"} and not x_name:
        errors.append("缺少 x 编码")
    if x_name and x_name not in columns:
        errors.append(f"x 列不存在: {x_name}")
    for name in y_names:
        if name not in columns:
            errors.append(f"y 列不存在: {name}")
    profiles = _with_indexes(profile_result(columns, rows))
    profile_by_name = {profile["name"]: profile for profile in profiles}
    for name in y_names:
        if name not in columns:
            continue
        profile = profile_by_name.get(name)
        if profile is None or not profile["numeric"]:
            errors.append(f"y 列不是数值: {name}")
        elif profile["is_id"]:
            errors.append(f"y 列是标识列: {name}")
    series = encoding.get("series")
    if series:
        profile = profile_by_name.get(series)
        if profile is None:
            errors.append(f"series 列不存在: {series}")
        elif profile["cardinality"] > 8:
            errors.append("series 基数超过 8")
    source = spec.get("source") or {}
    result_index = source.get("resultIndex", 0)
    if not isinstance(result_index, int) or isinstance(result_index, bool) or result_index < 0:
        errors.append("resultIndex 非法")
    if chart_type in {"donut", "pie"} and len(y_names) != 1:
        errors.append("环形图只允许一个 y 列")
    if chart_type in {"donut", "pie"}:
        profile = profile_by_name.get(y_names[0])
        if profile and not _non_negative(profile, rows):
            errors.append("环形图不允许负值")
    return errors


def build_tool_chart_spec(
    *,
    kind: str,
    x_column: str,
    y_columns: Sequence[str],
    columns: Sequence[str],
    rows: Sequence[Sequence[Any]],
    result_index: int = 0,
    title: str = "",
    unit: str = "",
    series_column: str | None = None,
    top_n: int | None = None,
) -> tuple[dict[str, Any] | None, str]:
    """模型图表工具入口：把工具参数转成 spec，失败返回 (None, 原因)。"""
    if x_column not in columns:
        return None, f"x 列不存在: {x_column}"
    missing = [name for name in y_columns if name not in columns]
    if missing:
        return None, "y 列不存在: " + "、".join(missing)
    if series_column and series_column not in columns:
        return None, f"series 列不存在: {series_column}"
    hint = {
        "type": kind,
        "x": x_column,
        "y": list(y_columns),
        "series": series_column,
        "title": title,
        "unit": unit,
        "top_n": top_n,
    }
    spec = recommend_chart_spec(
        question="",
        columns=columns,
        rows=rows,
        result_index=result_index,
        title=title,
        hint=hint,
    )
    if spec is None:
        return None, "无法根据所选列生成图表，请确认列名存在且 y 列均为数值。"
    errors = validate_chart_spec(spec, columns, rows)
    if errors:
        return None, "图表参数不合法：" + "；".join(errors)
    return spec, ""


def _resolve_rows(
    spec: dict[str, Any], rows: Sequence[Sequence[Any]]
) -> list[list[Any]]:
    dataset_rows = (spec.get("dataset") or {}).get("rows")
    if isinstance(dataset_rows, list) and dataset_rows:
        return [list(row) for row in dataset_rows]
    return [list(row) for row in rows]


def _apply_transform(
    spec: dict[str, Any], columns: Sequence[str], rows: list[list[Any]]
) -> list[list[Any]]:
    transform = spec.get("transform") or {}
    result = list(rows)
    sort = transform.get("sort")
    if isinstance(sort, dict):
        by = sort.get("by")
        order = sort.get("order", "desc")
        if by in columns:
            index = columns.index(by)
            numeric = all(
                _to_number(row[index]) is not None
                for row in result
                if index < len(row)
            )

            def sort_key(row: Sequence[Any]) -> Any:
                if index >= len(row):
                    return 0.0 if numeric else ""
                return (_to_number(row[index]) or 0.0) if numeric else _text(row[index])

            result.sort(key=sort_key, reverse=order != "asc")
    top_n = transform.get("topN")
    if isinstance(top_n, int) and top_n > 0:
        result = result[:top_n]
    return result


def spec_to_echarts_option(
    spec: dict[str, Any] | None,
    rows: Sequence[Sequence[Any]],
    columns: Sequence[str] | None = None,
) -> dict[str, Any]:
    """把 spec 转成旧版前端可用的 ECharts option；仅用于兼容与测试。"""
    if not isinstance(spec, dict):
        return {}
    chart_type = spec.get("type")
    if chart_type in {"kpi", "table", None}:
        return {}
    dataset = spec.get("dataset") or {}
    spec_columns = dataset.get("columns") or []
    names = [item.get("name") for item in spec_columns if isinstance(item, dict)]
    if not names and columns:
        names = list(columns)
    if not names:
        return {}
    raw_rows = _resolve_rows(spec, rows)
    if not raw_rows:
        return {}
    data_rows = _apply_transform(spec, names, raw_rows)
    encoding = spec.get("encoding") or {}
    x_name = encoding.get("x")
    y_names = [name for name in (encoding.get("y") or []) if name in names]
    if not y_names:
        return {}
    option: dict[str, Any] = {
        "color": _PALETTE,
        "tooltip": {"trigger": "axis" if chart_type not in {"donut", "pie"} else "item"},
        "legend": {"top": 0, "type": "scroll"},
        "grid": {"left": 48, "right": 24, "top": 40, "bottom": 36, "containLabel": True},
    }
    title = spec.get("title")
    if title:
        option["title"] = {"text": title, "left": 0, "top": 0, "textStyle": {"fontSize": 14}}

    if chart_type in {"donut", "pie"}:
        y_name = y_names[0]
        y_index = names.index(y_name)
        x_index = names.index(x_name) if x_name in names else None
        values = []
        for row in data_rows:
            value = _to_number(row[y_index]) if y_index < len(row) else None
            name = _text(row[x_index]) if x_index is not None and x_index < len(row) else ""
            if value is not None:
                values.append({"name": name, "value": value})
        option["series"] = [
            {
                "type": "pie",
                "radius": ["42%", "68%"] if chart_type == "donut" else "60%",
                "center": ["50%", "55%"],
                "data": values,
                "label": {"show": True, "formatter": "{b}\n{d}%"},
            }
        ]
        return option

    if x_name not in names:
        return {}
    x_index = names.index(x_name)
    x_labels = [_text(row[x_index]) if x_index < len(row) else "" for row in data_rows]
    series: list[dict[str, Any]] = []
    for y_name in y_names:
        y_index = names.index(y_name)
        values = [
            _to_number(row[y_index]) if y_index < len(row) else None
            for row in data_rows
        ]
        series_type = "line" if chart_type in {"line", "area"} else "bar"
        item: dict[str, Any] = {
            "name": _label(y_name),
            "type": series_type,
            "data": values,
            "barMaxWidth": 42,
        }
        if chart_type == "area":
            item["areaStyle"] = {"opacity": 0.18}
        if chart_type in {"line", "area"}:
            item["symbol"] = "circle"
            item["symbolSize"] = 6
            if (spec.get("options") or {}).get("smooth"):
                item["smooth"] = True
        if chart_type == "stackedColumn":
            item["stack"] = "total"
        if (spec.get("options") or {}).get("showLabel"):
            item["label"] = {"show": True, "position": "top", "fontSize": 10}
        series.append(item)

    annotations = spec.get("annotations") or {}
    reference_lines = annotations.get("referenceLines") or []
    if reference_lines and series:
        series[0]["markLine"] = {
            "silent": True,
            "symbol": "none",
            "data": [
                {
                    "yAxis": item.get("value"),
                    "label": {"formatter": item.get("label", ""), "position": "insideEndTop"},
                }
                for item in reference_lines
                if isinstance(item, dict) and item.get("value") is not None
            ],
        }

    option["series"] = series
    if chart_type == "bar":
        option["xAxis"] = {"type": "value"}
        option["yAxis"] = {"type": "category", "data": x_labels, "inverse": True}
    else:
        option["xAxis"] = {
            "type": "category",
            "data": x_labels,
            "axisLabel": {"hideOverlap": True},
        }
        y_axis: dict[str, Any] = {"type": "value"}
        if (spec.get("options") or {}).get("yStartAtZero"):
            y_axis["min"] = 0
        option["yAxis"] = y_axis
    return option


def adapt_legacy_chart_config(
    chart_type: str | None, chart_config: Any
) -> dict[str, Any] | None:
    """旧 ECharts option → 最小 spec；失败返回 None，由前端启发式兜底。"""
    if not isinstance(chart_config, dict):
        return None
    if chart_config.get("kind") == "spec" or (
        chart_config.get("version") == 1 and chart_config.get("type") in SUPPORTED_CHART_TYPES
    ):
        return chart_config if chart_config.get("type") in SUPPORTED_CHART_TYPES else None
    series = chart_config.get("series")
    if not isinstance(series, list) or not series or not isinstance(series[0], dict):
        return None
    x_axis = chart_config.get("xAxis")
    if isinstance(x_axis, dict):
        x_labels = x_axis.get("data") or []
    elif isinstance(x_axis, list) and x_axis and isinstance(x_axis[0], dict):
        x_labels = x_axis[0].get("data") or []
    else:
        x_labels = []
    first = series[0]
    if isinstance(first.get("data"), list) and first["data"] and isinstance(first["data"][0], dict):
        # pie: data 为 [{name, value}]
        rows = [[item.get("name", ""), item.get("value")] for item in first["data"]]
        resolved_type = "donut" if chart_type in {"pie", "donut"} else "column"
        return {
            "kind": "spec",
            "version": 1,
            "type": resolved_type,
            "title": (chart_config.get("title") or {}).get("text", "")
            if isinstance(chart_config.get("title"), dict)
            else "",
            "dataset": {
                "columns": [
                    {"name": "维度", "label": "维度", "role": "dimension", "type": "string"},
                    {"name": "数值", "label": "数值", "role": "measure", "type": "number"},
                ],
                "rows": rows,
            },
            "encoding": {"x": "维度", "y": ["数值"]},
            "options": {"showLabel": True},
        }
    if not x_labels:
        return None
    names = ["维度"]
    rows: list[list[Any]] = [[label] for label in x_labels]
    for index, item in enumerate(series):
        if not isinstance(item, dict):
            continue
        name = _text(item.get("name")) or f"系列 {index + 1}"
        names.append(name)
        values = item.get("data") if isinstance(item.get("data"), list) else []
        for row_index, row in enumerate(rows):
            row.append(values[row_index] if row_index < len(values) else None)
    series_type = first.get("type")
    resolved = "line" if series_type == "line" else "column"
    if resolved in {"kpi", "table"}:
        return None
    return {
        "kind": "spec",
        "version": 1,
        "type": resolved,
        "title": (chart_config.get("title") or {}).get("text", "")
        if isinstance(chart_config.get("title"), dict)
        else "",
        "dataset": {
            "columns": [
                {"name": name, "label": name, "role": "dimension" if index == 0 else "measure", "type": "string" if index == 0 else "number"}
                for index, name in enumerate(names)
            ],
            "rows": rows,
        },
        "encoding": {"x": "维度", "y": names[1:]},
        "options": {"showLabel": False},
    }
