"""Scope-aware 学情总览：权限层级、筛选、图表与提示的纯函数。

本模块不执行 SQL，也不生成权限条件；数据范围和谓词由 ``ReportScope`` 下推。
目标是把“当前账号能看什么”变成一组稳定的 payload 字段，前端不再按角色
硬编码空态或图表维度。
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

__all__ = [
    "LEVEL_ORDER",
    "derive_level",
    "dimension_for_level",
    "scope_payload",
    "notice",
    "class_chart_rows",
    "district_chart_rows",
    "school_chart_rows",
    "student_chart_rows",
    "line_stats_for_totals",
    "subject_averages",
    "score_bands",
]

LEVEL_ORDER = ("city", "district", "school", "grade", "class", "student")

_LEGACY_LEVEL = {
    "bureau_admin": "city",
    "school_admin": "school",
    "teacher": "class",
    "student": "student",
}

_SUBJECT_LABELS = {
    "yw": "语文",
    "sx": "数学",
    "yy": "英语",
    "wl": "物理",
    "hx": "化学",
    "sw": "生物",
    "zz": "政治",
    "ls": "历史",
    "dl": "地理",
    "hxzh": "化学赋分",
    "swzh": "生物赋分",
    "zzzh": "政治赋分",
    "dlzh": "地理赋分",
}


def derive_level(report_scope: Any, legacy_scope: Any = None) -> str:
    """从 v2 ``ReportScope`` 或旧 ``EduScope`` 推导总览层级。

    多层级授权取最高层级（市 > 区县 > 学校 > 年级 > 班级 > 学生），
    因为高层级已经覆盖低层级范围；页面只展示一个主视角。
    """
    levels = set(getattr(report_scope, "levels", ()) or ())
    if getattr(report_scope, "citywide", False):
        levels.add("city")
    for level in LEVEL_ORDER:
        if level in levels:
            return level
    role = str(getattr(legacy_scope, "edu_role", "") or "").strip()
    return _LEGACY_LEVEL.get(role, "")


def dimension_for_level(level: str) -> str:
    """主图维度：排下一层单位，而不是永远排学校。"""
    return {
        "city": "district",
        "district": "school",
        "school": "class",
        "grade": "class",
        "class": "student",
        "student": "exam",
    }.get(level, "")


def notice(code: str, message: str = "") -> dict[str, str]:
    """稳定提示码；前端负责最终文案，避免把权限/数据原因混成一句空态。"""
    return {"code": code, "message": message}


def scope_payload(report_scope: Any, level: str) -> dict[str, Any]:
    """返回给前端的范围摘要，不包含凭据或未授权目录。"""
    if report_scope is None:
        return {"label": "", "levels": [level] if level else [], "capabilities": []}
    label = str(report_scope.scope_label() or "")
    if level == "class" and report_scope.classes:
        label = (
            report_scope.classes[0]
            if len(report_scope.classes) == 1
            else f"{len(report_scope.classes)} 个班级"
        )
    elif level == "grade" and report_scope.cohorts:
        label = f"{label} {report_scope.cohorts[0]}".strip()
    if level == "school" and len(report_scope.schools) > 1:
        label = f"{len(report_scope.schools)} 所学校"
    if level == "student" and report_scope.students:
        label = "本人"
    return {
        "label": label,
        "levels": sorted(level for level in LEVEL_ORDER if level in set(report_scope.levels)),
        "schools": list(report_scope.schools),
        "districts": list(report_scope.districts),
        "cohorts": list(report_scope.cohorts),
        "classes": list(report_scope.classes),
        "capabilities": sorted(report_scope.capabilities),
        "multi_school": len(report_scope.schools) > 1,
        "multi_class": len(report_scope.classes) > 1,
    }


def _line_key(line: Mapping[str, Any]) -> str:
    key = str(line.get("line_key") or "").strip()
    if key:
        return key
    return "|".join(
        str(line.get(name) or "")
        for name in ("track", "line_name", "threshold")
    )


def _line_label(line: Mapping[str, Any]) -> str:
    return str(line.get("label") or line.get("line_name") or "分数线")


def _line_stat(line: Mapping[str, Any], reached: int, candidates: int) -> dict[str, Any]:
    rate = round(reached / candidates * 100, 2) if candidates else 0.0
    return {
        "line_key": _line_key(line),
        "line_name": str(line.get("line_name") or ""),
        "label": _line_label(line),
        "threshold": line.get("threshold"),
        "track": str(line.get("track") or ""),
        "reached": int(reached),
        "rate": rate,
    }


def line_stats_for_totals(
    bars: Iterable[Mapping[str, Any]],
    totals: Iterable[Mapping[str, Any]],
) -> tuple[int, list[dict[str, Any]]]:
    """按总分和选科计算达线人数/率；用于班级学生级总览。

    同一分数线在物理/历史两轨各有一行时，合并成一张卡（与达线看板一致），
    避免班级页出现两个同名“特控线”卡片。
    """
    from src.agent.education.line_reach import _display_bars, _line_public_fields

    raw = [dict(bar) for bar in bars if isinstance(bar, Mapping)]
    rows = [row for row in totals if isinstance(row, Mapping)]
    candidates = len(rows)
    display_bars, groups = _display_bars(raw, collapse=True)
    out: list[dict[str, Any]] = []
    for display_bar, indexes in zip(display_bars, groups):
        reached = 0
        for row in rows:
            total = row.get("total")
            if total is None:
                continue
            for index in indexes:
                bar = raw[index]
                threshold = bar.get("threshold")
                if threshold is None:
                    continue
                track = str(bar.get("track") or "")
                try:
                    if track and str(row.get("track") or "") != track:
                        continue
                    if float(total) >= float(threshold):
                        reached += 1
                        break
                except (TypeError, ValueError):
                    continue
        public = _line_public_fields(display_bar, display_bars)
        public["reached"] = reached
        public["rate"] = round(reached / candidates * 100, 2) if candidates else 0.0
        out.append(public)
    return candidates, out


def _row_with_line(
    row: Mapping[str, Any],
    *,
    id_field: str,
    label_field: str,
) -> dict[str, Any]:
    return {
        "id": str(row.get(id_field) or row.get(label_field) or ""),
        "label": str(row.get(label_field) or row.get(id_field) or ""),
        "candidates": int(row.get("candidates") or 0),
        "by_line": list(row.get("by_line") or []),
    }


def district_chart_rows(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """市级：区县为排名单位。"""
    return [
        _row_with_line(row, id_field="district", label_field="district")
        for row in payload.get("districts") or []
        if isinstance(row, Mapping)
    ]


def school_chart_rows(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """区县级：学校为排名单位。"""
    rows: list[dict[str, Any]] = []
    for district in payload.get("districts") or []:
        if not isinstance(district, Mapping):
            continue
        for school in district.get("schools") or []:
            if isinstance(school, Mapping):
                rows.append(
                    _row_with_line(
                        school, id_field="school_id", label_field="school_name"
                    )
                )
    return rows


def class_chart_rows(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """学校/年级：班级为排名单位（复用学校聚合 payload 的 schools 数组）。"""
    rows: list[dict[str, Any]] = []
    for district in payload.get("districts") or []:
        if not isinstance(district, Mapping):
            continue
        for item in district.get("schools") or []:
            if isinstance(item, Mapping):
                label = str(item.get("school_name") or item.get("school_id") or "")
                rows.append(
                    {
                        "id": label,
                        "label": label,
                        "candidates": int(item.get("candidates") or 0),
                        "by_line": list(item.get("by_line") or []),
                    }
                )
    return rows


def student_chart_rows(students: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """班级：学生为排名单位；按总分降序，只返回匿名标识和科目分。"""
    subject_keys = ("yw", "sx", "yy", "wl", "hx", "sw", "zz", "ls", "dl")
    rows = [row for row in students if isinstance(row, Mapping)]
    rows.sort(key=lambda row: float(row.get("total") or 0), reverse=True)
    out: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        out.append(
            {
                "id": str(row.get("student_id") or ""),
                "label": str(row.get("student_id") or ""),
                "class_name": str(row.get("class_name") or ""),
                "total": row.get("total"),
                "rank": index,
                "track": str(row.get("track") or ""),
                "subjects": {
                    key: row.get(key)
                    for key in subject_keys
                    if row.get(key) is not None
                },
            }
        )
    return out


def subject_averages(
    students: Iterable[Mapping[str, Any]],
    columns: Iterable[str],
) -> list[dict[str, Any]]:
    """按科目列计算班级均分；只使用已发布、非空的数值列。"""
    rows = [row for row in students if isinstance(row, Mapping)]
    out: list[dict[str, Any]] = []
    for column in columns:
        name = str(column or "").strip()
        if not name:
            continue
        values: list[float] = []
        for row in rows:
            value = row.get(name)
            if value is None:
                continue
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if number > 0:
                values.append(number)
        if not values:
            continue
        out.append(
            {
                "key": name,
                "label": _SUBJECT_LABELS.get(name.lower(), name),
                "avg": round(sum(values) / len(values), 2),
                "count": len(values),
            }
        )
    return out


def score_bands(
    students: Iterable[Mapping[str, Any]],
    config: Any,
    *,
    full_score: float | None = None,
) -> dict[str, Any]:
    """班级总分分数段；复用报告 KPI 的阈值与分段配置。"""
    from src.agent.education.stats import compute_score_stats

    totals: list[float] = []
    for row in students:
        if not isinstance(row, Mapping):
            continue
        try:
            value = float(row.get("total"))
        except (TypeError, ValueError):
            continue
        if value > 0:
            totals.append(value)
    stats = compute_score_stats(totals, config, full_score)
    return {
        "segments": stats.get("segments") or [],
        "count": stats.get("count") or 0,
        "avg": stats.get("avg"),
        "max": stats.get("max"),
        "min": stats.get("min"),
        "pass_rate": stats.get("pass_rate"),
        "excellent_rate": stats.get("excellent_rate"),
    }
