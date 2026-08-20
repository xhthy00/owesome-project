"""ECharts option 生成——报告内嵌图表的配置 JSON。

设计取舍：后端只产出 ECharts ``option`` JSON 字符串，由模板用
``<div id="..."></div>`` + ``echarts.init(...).setOption(JSON.parse(...))`` 渲染。
不引入 Python 图表库，图表在浏览器端绘制，HTML 自包含可下载。

支持 4 类报告常用图：

- ``score_distribution``：分数段柱状图（含及格线参考线）；
- ``subject_radar``：各科均分雷达图；
- ``class_compare_bar``：班级均分横向对比柱图；
- ``subject_bar``：各科及格率/均分柱图；
- ``knowledge_bar``：知识点得分率横向柱图（``categories`` + ``values``）。
"""

from __future__ import annotations

import json
from typing import Any, Callable

_CHART_BUILDERS: dict[str, Callable[[dict[str, Any], str], dict[str, Any]]] = {}

# LLM / Charter 常用别名 → 按 data 结构再解析为具体 builder
_CHART_TYPE_ALIASES: dict[str, str] = {
    "column": "bar",
    "histogram": "bar",
    "hbar": "horizontal_bar",
    "horizontal": "horizontal_bar",
    "line": "trend_line",
    "radar": "subject_radar",
}

SUPPORTED_CHART_TYPES = (
    "score_distribution",
    "subject_radar",
    "class_compare_bar",
    "subject_bar",
    "knowledge_bar",
    "trend_line",
    "group_compare_bar",
    "pie",
    "correlation_bar",
    "progress_regress_bar",
    "subject_extreme_bar",
    "trajectory_line",
    "heatmap",
    "ability_radar",
    "question_type_bar",
    "scatter",
)


def _register_builder(name: str, fn: Callable[[dict[str, Any], str], dict[str, Any]]) -> None:
    _CHART_BUILDERS[name] = fn


def resolve_chart_type(chart_type: str, data: dict[str, Any], title: str = "") -> str:
    """将 bar/column/line 等别名解析为具体 chart_type。"""
    ct = (chart_type or "").strip().lower()
    ct = _CHART_TYPE_ALIASES.get(ct, ct)
    if ct in _CHART_BUILDERS:
        return ct

    d = data or {}
    title_s = title or ""

    if ct == "bar":
        if d.get("segments"):
            return "score_distribution"
        if d.get("categories") and d.get("values"):
            return "knowledge_bar"
        if d.get("classes") and d.get("values"):
            return "class_compare_bar"
        if d.get("groups") and d.get("metrics"):
            return "group_compare_bar"
        if d.get("items"):
            return "progress_regress_bar"
        if d.get("subjects") and (d.get("metrics") or d.get("values")):
            return "subject_bar"
        if "知识点" in title_s:
            return "knowledge_bar"
        if "班级" in title_s or "对比" in title_s:
            return "class_compare_bar"
        if "分数段" in title_s or "分布" in title_s:
            return "score_distribution"
        return "subject_bar"

    if ct in ("horizontal_bar", "bar_horizontal"):
        if d.get("categories") and d.get("values"):
            return "knowledge_bar"
        return "class_compare_bar"

    return ct


def _normalize_chart_data(chart_type: str, data: dict[str, Any]) -> dict[str, Any]:
    """兼容 LLM 简写 data 结构。"""
    d = dict(data or {})
    if chart_type == "subject_bar":
        if d.get("subjects") and d.get("values") and not d.get("metrics"):
            d["metrics"] = [{"name": d.get("series_name") or "数值", "values": list(d["values"])}]
    if chart_type == "knowledge_bar":
        if d.get("subjects") and not d.get("categories"):
            d["categories"] = list(d["subjects"])
    if chart_type == "class_compare_bar":
        if d.get("categories") and not d.get("classes"):
            d["classes"] = list(d["categories"])
    return d


def build_chart_option(
    chart_type: str,
    data: dict[str, Any],
    title: str = "",
) -> str:
    """返回 ECharts option 的 JSON 字符串。

    Args:
        chart_type: 见模块 docstring；支持别名 ``bar`` / ``column`` / ``line`` / ``radar``。
        data: 图表数据，结构因 chart_type 而异（见各 builder）。
        title: 图表标题（别名解析时作辅助判断）。

    Returns:
        JSON 字符串；未知 chart_type 返回空串（调用方按"无图表"处理）。
    """
    resolved = resolve_chart_type(chart_type, data or {}, title)
    normalized = _normalize_chart_data(resolved, data or {})
    builder = _CHART_BUILDERS.get(resolved)
    if builder is None:
        return ""
    option = builder(normalized, title)
    return json.dumps(option, ensure_ascii=False)


def _score_distribution(data: dict[str, Any], title: str) -> dict[str, Any]:
    segments = data.get("segments") or []
    labels = [s.get("label", "") for s in segments]
    counts = [int(s.get("count", 0)) for s in segments]
    pass_rate = data.get("pass_rate")
    return {
        "title": {"text": title, "left": "center", "textStyle": {"fontSize": 14}},
        "tooltip": {"trigger": "axis"},
        "grid": {"left": "8%", "right": "8%", "bottom": "10%", "containLabel": True},
        "xAxis": {"type": "category", "data": labels, "name": "分数段"},
        "yAxis": {"type": "value", "name": "人数"},
        "series": [{
            "type": "bar",
            "data": counts,
            "itemStyle": {"color": "#3b82f6"},
            "label": {"show": True, "position": "top"},
            "markLine": {
                "data": [{"xAxis": "60-70"}]
            } if pass_rate is not None else [],
        }],
    }


def _subject_radar(data: dict[str, Any], title: str) -> dict[str, Any]:
    subjects = list(data.get("subjects") or [])
    default_max = float(data.get("full_score", 100) or 100)
    maxes = data.get("maxes")
    if isinstance(maxes, list) and len(maxes) == len(subjects):
        indicators = [
            {"name": name, "max": float(m if m is not None else default_max)}
            for name, m in zip(subjects, maxes)
        ]
    else:
        indicators = [{"name": name, "max": default_max} for name in subjects]
    values = list(data.get("values") or [])
    return {
        "title": {"text": title, "left": "center", "textStyle": {"fontSize": 14}},
        "tooltip": {},
        "radar": {"indicator": indicators},
        "series": [{
            "type": "radar",
            "data": [{"value": values, "name": data.get("series_name", "均分")}],
            "areaStyle": {"opacity": 0.25},
        }],
    }


def _class_compare_bar(data: dict[str, Any], title: str) -> dict[str, Any]:
    classes = data.get("classes") or []
    values = list(data.get("values") or [])
    return {
        "title": {"text": title, "left": "center", "textStyle": {"fontSize": 14}},
        "tooltip": {"trigger": "axis"},
        "grid": {"left": "15%", "right": "8%", "bottom": "8%", "containLabel": True},
        "xAxis": {"type": "value", "name": "均分"},
        "yAxis": {"type": "category", "data": classes},
        "series": [{
            "type": "bar",
            "data": values,
            "itemStyle": {"color": "#10b981"},
            "label": {"show": True, "position": "right"},
        }],
    }


def _subject_bar(data: dict[str, Any], title: str) -> dict[str, Any]:
    subjects = data.get("subjects") or []
    metrics = data.get("metrics") or []
    series = []
    for metric in metrics:
        series.append({
            "type": "bar",
            "name": metric.get("name", ""),
            "data": list(metric.get("values") or []),
            "label": {"show": True, "position": "top"},
        })
    return {
        "title": {"text": title, "left": "center", "textStyle": {"fontSize": 14}},
        "tooltip": {"trigger": "axis"},
        "legend": {"top": 24},
        "grid": {"left": "8%", "right": "8%", "bottom": "12%", "containLabel": True},
        "xAxis": {"type": "category", "data": subjects},
        "yAxis": {"type": "value"},
        "series": series,
    }


def _knowledge_bar(data: dict[str, Any], title: str) -> dict[str, Any]:
    """知识点得分率横向柱图：data={"categories":[...], "values":[...]}。"""
    categories = data.get("categories") or data.get("subjects") or []
    values = list(data.get("values") or [])
    return {
        "title": {"text": title, "left": "center", "textStyle": {"fontSize": 14}},
        "tooltip": {"trigger": "axis", "valueFormatter": "{c}%"},
        "grid": {"left": "22%", "right": "10%", "bottom": "8%", "containLabel": True},
        "xAxis": {"type": "value", "name": "得分率(%)", "max": 100},
        "yAxis": {"type": "category", "data": categories},
        "series": [{
            "type": "bar",
            "data": values,
            "itemStyle": {"color": "#1677ff"},
            "label": {"show": True, "position": "right", "formatter": "{c}%"},
        }],
    }


def _trend_line(data: dict[str, Any], title: str) -> dict[str, Any]:
    x_labels = data.get("x_labels") or []
    series = []
    for s in data.get("series") or []:
        series.append({
            "type": "line",
            "name": s.get("name", ""),
            "data": list(s.get("values") or []),
            "smooth": True,
            "label": {"show": True, "position": "top"},
            "markLine": {
                "data": [{"yAxis": data.get("pass_line", 60), "name": "及格线"}]
            } if data.get("pass_line") is not None else [],
        })
    return {
        "title": {"text": title, "left": "center", "textStyle": {"fontSize": 14}},
        "tooltip": {"trigger": "axis"},
        "legend": {"top": 24},
        "grid": {"left": "8%", "right": "8%", "bottom": "12%", "containLabel": True},
        "xAxis": {"type": "category", "data": x_labels, "name": "考试"},
        "yAxis": {"type": "value", "name": "分数"},
        "series": series,
    }


def _group_compare_bar(data: dict[str, Any], title: str) -> dict[str, Any]:
    """分组对比柱图（如男/女生均分、各班级得分率）。"""
    groups = data.get("groups") or []
    metrics = data.get("metrics") or []
    y_name = data.get("y_name") or "分数"
    y_max = data.get("y_max")
    series = []
    for metric in metrics:
        series.append({
            "type": "bar",
            "name": metric.get("name", ""),
            "data": list(metric.get("values") or []),
            "label": {"show": True, "position": "top"},
        })
    y_axis: dict[str, Any] = {"type": "value", "name": y_name}
    if y_max is not None:
        y_axis["max"] = y_max
    return {
        "title": {"text": title, "left": "center", "textStyle": {"fontSize": 14}},
        "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"}},
        "legend": {"top": 24},
        "grid": {"left": "8%", "right": "8%", "bottom": "12%", "containLabel": True},
        "xAxis": {"type": "category", "data": groups},
        "yAxis": y_axis,
        "series": series,
    }


def _pie(data: dict[str, Any], title: str) -> dict[str, Any]:
    """通用饼图：data={"items":[{"name","value","color?"},...]}。

    图例置底；0 值扇区不画外侧标签/引导线，避免与图例挤在左上角重叠。
    """
    items = data.get("items") or []
    pie_data: list[dict[str, Any]] = []
    for it in items:
        try:
            value = float(it.get("value") or 0)
        except (TypeError, ValueError):
            value = 0.0
        entry: dict[str, Any] = {
            "name": str(it.get("name") or ""),
            "value": value,
            "label": {"show": value > 0},
            "labelLine": {"show": value > 0},
        }
        if it.get("color"):
            entry["itemStyle"] = {"color": it["color"]}
        pie_data.append(entry)
    return {
        "title": {
            "text": title,
            "left": "center",
            "top": 0,
            "textStyle": {"fontSize": 14},
        },
        "tooltip": {"trigger": "item", "formatter": "{b}: {c} 人 ({d}%)"},
        "legend": {
            "orient": "horizontal",
            "bottom": 0,
            "left": "center",
            "itemGap": 16,
        },
        "series": [{
            "type": "pie",
            "radius": ["36%", "58%"],
            "center": ["50%", "48%"],
            "avoidLabelOverlap": True,
            "label": {
                "show": True,
                "formatter": "{b}: {c}人",
                "position": "outside",
            },
            "labelLine": {"show": True, "length": 14, "length2": 10},
            "data": pie_data,
        }],
    }


def _correlation_bar(data: dict[str, Any], title: str) -> dict[str, Any]:
    """相关性柱图：data={"subjects":[...], "series":[{"name","values":[r,...]}]}。"""
    subjects = data.get("subjects") or []
    series = []
    for s in data.get("series") or []:
        series.append({
            "type": "bar",
            "name": s.get("name", ""),
            "data": list(s.get("values") or []),
            "label": {"show": True, "position": "top", "formatter": "{c}"},
        })
    return {
        "title": {"text": title, "left": "center", "textStyle": {"fontSize": 14}},
        "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"}},
        "legend": {"top": 24},
        "grid": {"left": "8%", "right": "8%", "bottom": "12%", "containLabel": True},
        "xAxis": {"type": "category", "data": subjects},
        "yAxis": {"type": "value", "name": "相关系数 r", "min": -1, "max": 1},
        "series": series,
    }


def _progress_regress_bar(data: dict[str, Any], title: str) -> dict[str, Any]:
    """进步/退步学生横向柱图：data={"items":[{"name","value","color?"},...]}，
    value 正为进步、负为退步，按 value 降序展示。"""
    items = list(data.get("items") or [])
    items.sort(key=lambda x: x.get("value", 0), reverse=True)
    names = [it.get("name", "") for it in items]
    values = [
        {"value": it.get("value", 0),
         **({"itemStyle": {"color": it["color"]}} if it.get("color") else {})}
        for it in items
    ]
    return {
        "title": {"text": title, "left": "center", "textStyle": {"fontSize": 14}},
        "tooltip": {"trigger": "axis", "valueSuffix": " 分"},
        "grid": {"left": "18%", "right": "8%", "bottom": "8%", "containLabel": True},
        "xAxis": {"type": "value", "name": "总分变化"},
        "yAxis": {"type": "category", "data": names},
        "series": [{"type": "bar", "data": values, "label": {"show": True, "position": "right"}}],
    }


def _subject_extreme_bar(data: dict[str, Any], title: str) -> dict[str, Any]:
    """单科进步/退步之最横向柱图：data={"items":[{"name","value","color?"},...]}。"""
    items = list(data.get("items") or [])
    names = [it.get("name", "") for it in items]
    values = [
        {"value": it.get("value", 0),
         **({"itemStyle": {"color": it["color"]}} if it.get("color") else {})}
        for it in items
    ]
    return {
        "title": {"text": title, "left": "center", "textStyle": {"fontSize": 14}},
        "tooltip": {"trigger": "axis", "valueSuffix": " 分"},
        "grid": {"left": "20%", "right": "8%", "bottom": "8%", "containLabel": True},
        "xAxis": {"type": "value", "name": "分数变化"},
        "yAxis": {"type": "category", "data": names},
        "series": [{"type": "bar", "data": values, "label": {"show": True, "position": "right"}}],
    }


def _trajectory_line(data: dict[str, Any], title: str) -> dict[str, Any]:
    """全体学生总分轨迹多线图：data={"x_labels":[...],"series":[{"name","values":[...],"visible?"},...]}。"""
    x_labels = data.get("x_labels") or []
    series = []
    for s in data.get("series") or []:
        item = {
            "type": "line",
            "name": s.get("name", ""),
            "data": list(s.get("values") or []),
            "showSymbol": True,
        }
        if "visible" in s:
            item["silent"] = False
        series.append(item)
    return {
        "title": {"text": title, "left": "center", "textStyle": {"fontSize": 14}},
        "tooltip": {"trigger": "axis", "valueSuffix": " 分"},
        "legend": {"type": "scroll", "top": 24, "layout": "vertical", "right": 0},
        "grid": {"left": "5%", "right": "18%", "bottom": "8%", "containLabel": True},
        "xAxis": {"type": "category", "data": x_labels},
        "yAxis": {"type": "value", "name": "总分"},
        "series": series,
    }


def _heatmap(data: dict[str, Any], title: str) -> dict[str, Any]:
    """交叉分析热力图：data 含 rows/cols/matrix；可选 min/max（默认 0–100）。"""
    rows = data.get("rows") or []
    cols = data.get("cols") or []
    matrix = data.get("matrix") or []
    heat_data = []
    for i, row_vals in enumerate(matrix):
        for j, val in enumerate(row_vals):
            if val is not None:
                heat_data.append([j, i, val])
    try:
        vmax = float(data.get("max") if data.get("max") is not None else 100)
    except (TypeError, ValueError):
        vmax = 100.0
    try:
        vmin = float(data.get("min") if data.get("min") is not None else 0)
    except (TypeError, ValueError):
        vmin = 0.0
    return {
        "title": {"text": title, "left": "center", "textStyle": {"fontSize": 14}},
        "tooltip": {"position": "top", "trigger": "item"},
        "grid": {"left": "12%", "right": "8%", "bottom": "15%", "containLabel": True},
        "xAxis": {"type": "category", "data": cols, "splitArea": {"show": True}},
        "yAxis": {"type": "category", "data": rows, "splitArea": {"show": True}},
        "visualMap": {
            "min": vmin,
            "max": vmax,
            "calculable": True,
            "orient": "horizontal",
            "left": "center",
            "bottom": "0%",
        },
        "series": [{
            "name": str(data.get("series_name") or "得分率"),
            "type": "heatmap",
            "data": heat_data,
            "label": {"show": True, "fontSize": 11},
        }],
    }


def _ability_radar(data: dict[str, Any], title: str) -> dict[str, Any]:
    """能力层级雷达：data={"levels":[...], "values":[...]}。"""
    levels = data.get("levels") or []
    values = data.get("values") or []
    return {
        "title": {"text": title, "left": "center", "textStyle": {"fontSize": 14}},
        "tooltip": {},
        "radar": {
            "indicator": [{"name": lv, "max": 100} for lv in levels],
        },
        "series": [{
            "type": "radar",
            "data": [{"value": values, "name": "得分率"}],
        }],
    }


def _question_type_bar(data: dict[str, Any], title: str) -> dict[str, Any]:
    """题型得分率柱图：data={"categories":[...], "values":[...]}。"""
    categories = data.get("categories") or []
    values = data.get("values") or []
    return {
        "title": {"text": title, "left": "center", "textStyle": {"fontSize": 14}},
        "tooltip": {"trigger": "axis", "valueSuffix": "%"},
        "grid": {"left": "8%", "right": "8%", "bottom": "12%", "containLabel": True},
        "xAxis": {"type": "category", "data": categories},
        "yAxis": {"type": "value", "name": "得分率(%)", "max": 100},
        "series": [{"type": "bar", "data": values, "label": {"show": True, "position": "top"}}],
    }


def _scatter(data: dict[str, Any], title: str) -> dict[str, Any]:
    """散点图。data: series=[{name, data:[[x,y],...]}], 可选 x_name/y_name。"""
    series_in = list(data.get("series") or [])
    series = []
    for s in series_in:
        if not isinstance(s, dict):
            continue
        series.append(
            {
                "name": str(s.get("name") or ""),
                "type": "scatter",
                "symbolSize": int(s.get("symbolSize") or 12),
                "data": list(s.get("data") or []),
                "emphasis": {"focus": "series"},
            }
        )
    if not series and data.get("points"):
        series = [
            {
                "name": str(data.get("name") or "数据"),
                "type": "scatter",
                "symbolSize": 12,
                "data": [[p.get("x"), p.get("y")] for p in data["points"] if isinstance(p, dict)],
            }
        ]
    return {
        "title": {"text": title, "left": "center"},
        "tooltip": {"trigger": "item"},
        "legend": {"bottom": 0},
        "grid": {"left": "10%", "right": "8%", "bottom": "14%", "containLabel": True},
        "xAxis": {
            "type": "value",
            "name": str(data.get("x_name") or ""),
            "scale": True,
        },
        "yAxis": {
            "type": "value",
            "name": str(data.get("y_name") or ""),
            "scale": True,
        },
        "series": series,
    }


_register_builder("score_distribution", _score_distribution)
_register_builder("subject_radar", _subject_radar)
_register_builder("class_compare_bar", _class_compare_bar)
_register_builder("subject_bar", _subject_bar)
_register_builder("knowledge_bar", _knowledge_bar)
_register_builder("trend_line", _trend_line)
_register_builder("group_compare_bar", _group_compare_bar)
_register_builder("pie", _pie)
_register_builder("correlation_bar", _correlation_bar)
_register_builder("progress_regress_bar", _progress_regress_bar)
_register_builder("subject_extreme_bar", _subject_extreme_bar)
_register_builder("trajectory_line", _trajectory_line)
_register_builder("heatmap", _heatmap)
_register_builder("ability_radar", _ability_radar)
_register_builder("question_type_bar", _question_type_bar)
_register_builder("scatter", _scatter)


__all__ = ["SUPPORTED_CHART_TYPES", "build_chart_option", "resolve_chart_type"]
