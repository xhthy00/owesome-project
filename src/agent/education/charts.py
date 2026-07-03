"""ECharts option 生成——报告内嵌图表的配置 JSON。

设计取舍：后端只产出 ECharts ``option`` JSON 字符串，由模板用
``<div id="..."></div>`` + ``echarts.init(...).setOption(JSON.parse(...))`` 渲染。
不引入 Python 图表库，图表在浏览器端绘制，HTML 自包含可下载。

支持 4 类报告常用图：

- ``score_distribution``：分数段柱状图（含及格线参考线）；
- ``subject_radar``：各科均分雷达图；
- ``class_compare_bar``：班级均分横向对比柱图；
- ``subject_bar``：各科及格率/均分柱图。
"""

from __future__ import annotations

import json
from typing import Any


def build_chart_option(
    chart_type: str,
    data: dict[str, Any],
    title: str = "",
) -> str:
    """返回 ECharts option 的 JSON 字符串。

    Args:
        chart_type: 见模块 docstring 的 5 类。
        data: 图表数据，结构因 chart_type 而异（见各 builder）。
        title: 图表标题。

    Returns:
        JSON 字符串；未知 chart_type 返回空串（调用方按"无图表"处理）。
    """
    builder = {
        "score_distribution": _score_distribution,
        "subject_radar": _subject_radar,
        "class_compare_bar": _class_compare_bar,
        "subject_bar": _subject_bar,
        "trend_line": _trend_line,
        "group_compare_bar": _group_compare_bar,
        "pie": _pie,
        "correlation_bar": _correlation_bar,
        "progress_regress_bar": _progress_regress_bar,
        "subject_extreme_bar": _subject_extreme_bar,
        "trajectory_line": _trajectory_line,
    }.get(chart_type)
    if builder is None:
        return ""
    option = builder(data, title)
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
    indicators = [{"name": name, "max": float(data.get("full_score", 100))}
                  for name in (data.get("subjects") or [])]
    values = list(data.get("values") or [])
    return {
        "title": {"text": title, "left": "center", "textStyle": {"fontSize": 14}},
        "tooltip": {},
        "radar": {"indicator": indicators},
        "series": [{
            "type": "radar",
            "data": [{"value": values, "name": data.get("series_name", "均分")}],
            "areaStyle": {"opacity": 0.2},
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
    """分组对比柱图（如男/女生均分、各分组均分）。"""
    groups = data.get("groups") or []
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
        "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"}},
        "legend": {"top": 24},
        "grid": {"left": "8%", "right": "8%", "bottom": "12%", "containLabel": True},
        "xAxis": {"type": "category", "data": groups},
        "yAxis": {"type": "value", "name": "分数"},
        "series": series,
    }


def _pie(data: dict[str, Any], title: str) -> dict[str, Any]:
    """通用饼图：data={"items":[{"name","value","color?"},...]}。"""
    items = data.get("items") or []
    pie_data = [
        {"name": it.get("name", ""), "value": it.get("value", 0),
         **({"itemStyle": {"color": it["color"]}} if it.get("color") else {})}
        for it in items
    ]
    return {
        "title": {"text": title, "left": "center", "textStyle": {"fontSize": 14}},
        "tooltip": {"trigger": "item", "formatter": "{b}: {c} 人 ({d}%)"},
        "legend": {"top": 24},
        "series": [{
            "type": "pie",
            "radius": ["40%", "70%"],
            "label": {"show": True, "formatter": "{b}: {c}人"},
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


__all__ = ["build_chart_option"]
