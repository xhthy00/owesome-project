"""权限分层学情总览 API。

页面只保留一个入口，由服务端根据当前账号的 ``ReportScope`` 返回视角：
市级看区县、区县看学校、学校/年级看班级、班级看学生。客户端不传 level，
也不传 group_by；筛选值不能扩大授权范围。
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query

from common.exceptions.base import ForbiddenException
from common.schemas.response import success_response
from src.agent.education import config_store
from src.agent.education.api import (
    _build_orchestrator,
    _exam_line_tool_context,
    _fetch_dicts,
    _first_col,
    _ident,
    _load_line_reach_bar_rows,
    _load_report_scope,
    _overview_agg_sql,
    _probe_columns,
    _sql_quote,
    _track_case_sql,
    _track_sql_predicate,
)
from src.agent.education.line_reach import (
    filter_fraction_bars,
    line_code_of,
    normalize_fraction_bars,
    payload_from_school_agg,
)
from src.agent.education.line_reach_report import _parse_exam_time
from src.agent.education.overview import (
    class_chart_rows,
    derive_level,
    dimension_for_level,
    district_chart_rows,
    line_stats_for_totals,
    notice,
    school_chart_rows,
    scope_payload,
    score_bands,
    student_chart_rows,
    subject_averages,
)
from system.api.auth_deps import get_current_user
from system.schemas import UserResponse
from system.workspace_scope import get_workspace_oid

router = APIRouter(prefix="/education", tags=["education"])

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
}


def _subject_columns(ov_cols: list[str]) -> list[str]:
    wanted = ("yw", "sx", "yy", "wl", "hx", "sw", "zz", "ls", "dl")
    lower = {str(col).lower(): col for col in ov_cols}
    return [lower[name] for name in wanted if name in lower]


def _subject_options(columns: list[str]) -> list[dict[str, str]]:
    return [
        {"key": column, "label": _SUBJECT_LABELS.get(str(column).lower(), column)}
        for column in columns
    ]


def _total_full_score(ov_cols: list[str]) -> float | None:
    """总览宽表的总分列对应的满分；只用于分数段比例，不改变达线口径。"""
    total_col = _first_col(ov_cols, ("zf6m", "zf4m", "zf3m", "zf", "total", "total_score"))
    return {
        "zf6m": 750.0,
        "zf4m": 600.0,
        "zf3m": 450.0,
    }.get(str(total_col or "").lower())


def _cohort_filter(ov_cols: list[str], cohort: str) -> str:
    if not cohort:
        return ""
    cohort_col = _first_col(ov_cols, ("jc", "cohort", "cohort_id", "届次"))
    if not cohort_col:
        return ""
    return f"{_ident(cohort_col)} = '{_sql_quote(cohort)}'"


def _where_sql(parts: list[str]) -> str:
    parts = [part for part in parts if part]
    return (" WHERE " + " AND ".join(parts)) if parts else ""


def _scoped_exams_sql(ov_cols: list[str], *, cohort: str, class_name: str) -> str | None:
    exam_col = _first_col(ov_cols, ("exam_name", "exam", "ksmc"))
    total_col = _first_col(ov_cols, ("zf6m", "zf4m", "zf3m", "zf", "total", "total_score"))
    if not exam_col or not total_col:
        return None
    class_col = _first_col(ov_cols, ("bj", "class", "class_name"))
    track_col = _first_col(ov_cols, ("xkkm", "xkqk", "track", "xkfx"))
    exam = _ident(exam_col)
    score_columns = [total_col, *_subject_columns(ov_cols)]
    valid_score = " OR ".join(f"{_ident(column)} IS NOT NULL" for column in score_columns)
    where = _where_sql([
        f"{exam} <> ''",
        f"({valid_score})",
        _cohort_filter(ov_cols, cohort),
        f"{_ident(class_col)} = '{_sql_quote(class_name)}'" if class_name and class_col else "",
    ])
    # 与成绩明细走相同的执行器，学校、届别、班级/个人授权会下推到此查询。
    return f"SELECT DISTINCT {exam} AS exam_name, {_track_case_sql(track_col)} AS track FROM tb_score_overview{where}"


def _exam_batches(
    rows: list[dict[str, Any]], scoped_cohort: str,
) -> dict[str, tuple[datetime, str, str]]:
    dates: dict[str, set[tuple[datetime, str, str]]] = {}
    for row in rows:
        name = str(row.get("batch_name") or "").strip()
        when = _parse_exam_time(row.get("exam_time"))
        if name and when:
            cohort = str(row.get("jc") or scoped_cohort).strip().removesuffix("届")
            dates.setdefault(name, set()).add((when, cohort, str(row.get("exam_type") or "")))
    return {name: next(iter(values)) for name, values in dates.items() if len(values) == 1}


def _class_agg_sql(
    ov_cols: list[str],
    bars: list[dict[str, Any]],
    *,
    exam_name: str,
    track: str,
    cohort: str = "",
) -> str | None:
    """学校/年级：按班级聚合达线人数/率。"""
    if not ov_cols:
        return None
    total_col = _first_col(ov_cols, ("zf6m", "zf4m", "zf3m", "zf", "total", "total_score"))
    class_col = _first_col(ov_cols, ("bj", "class", "class_name"))
    if not total_col or not class_col:
        return None
    district_col = _first_col(ov_cols, ("dq", "district", "qx"))
    exam_col = _first_col(ov_cols, ("exam_name", "exam", "ksmc"))
    track_col = _first_col(ov_cols, ("xkkm", "xkqk", "track", "xkfx"))
    d_expr = (
        f"COALESCE(CAST({_ident(district_col)} AS TEXT), '未知区县')"
        if district_col
        else "'未知区县'"
    )
    c_expr = f"COALESCE(CAST({_ident(class_col)} AS TEXT), '未知班级')"
    t_ident = _ident(total_col)
    reached = [
        f"SUM(CASE WHEN {t_ident} >= {float(bar['threshold'])} THEN 1 ELSE 0 END) AS r{i}"
        for i, bar in enumerate(bars)
    ]
    where = _where_sql(
        [
            f"{_ident(exam_col)} = '{_sql_quote(exam_name)}'" if exam_name and exam_col else "",
            _track_sql_predicate(track_col, track) if track else "",
            _cohort_filter(ov_cols, cohort),
        ]
    )
    return (
        f"SELECT {d_expr} AS district, {c_expr} AS school_name, "
        f"{_track_case_sql(track_col)} AS track, "
        f"{', '.join(['COUNT(*) AS candidates', *reached])} "
        f"FROM tb_score_overview{where} "
        f"GROUP BY 1, 2, 3 ORDER BY 1, 2, 3 LIMIT 5000"
    )


def _student_rows_sql(
    ov_cols: list[str],
    *,
    exam_name: str,
    track: str,
    cohort: str = "",
    class_name: str = "",
    limit: int = 1000,
) -> str | None:
    """班级：只取匿名学号、班级、总分、选科和科目分。"""
    if not ov_cols:
        return None
    total_col = _first_col(ov_cols, ("zf6m", "zf4m", "zf3m", "zf", "total", "total_score"))
    student_col = _first_col(ov_cols, ("anon_stu_id", "student_id", "sid"))
    class_col = _first_col(ov_cols, ("bj", "class", "class_name"))
    exam_col = _first_col(ov_cols, ("exam_name", "exam", "ksmc"))
    track_col = _first_col(ov_cols, ("xkkm", "xkqk", "track", "xkfx"))
    if not total_col or not student_col:
        return None
    select = [
        f"CAST({_ident(student_col)} AS TEXT) AS student_id",
        f"{_ident(total_col)} AS total",
        f"{_track_case_sql(track_col)} AS track",
    ]
    if class_col:
        select.append(f"CAST({_ident(class_col)} AS TEXT) AS class_name")
    for column in _subject_columns(ov_cols):
        select.append(f"{_ident(column)} AS {column}")
    where = _where_sql(
        [
            f"{_ident(exam_col)} = '{_sql_quote(exam_name)}'" if exam_name and exam_col else "",
            _track_sql_predicate(track_col, track) if track else "",
            _cohort_filter(ov_cols, cohort),
            f"{_ident(class_col)} = '{_sql_quote(class_name)}'"
            if class_name and class_col
            else "",
        ]
    )
    return (
        f"SELECT {', '.join(select)} FROM tb_score_overview{where} "
        f"ORDER BY {_ident(total_col)} DESC LIMIT {int(limit)}"
    )


def _grade_avg_sql(
    ov_cols: list[str],
    *,
    exam_name: str,
    track: str,
    cohort: str = "",
) -> str | None:
    """班级级年级对照：只返回均值、有效人数、参评班数。"""
    if not ov_cols:
        return None
    total_col = _first_col(ov_cols, ("zf6m", "zf4m", "zf3m", "zf", "total", "total_score"))
    class_col = _first_col(ov_cols, ("bj", "class", "class_name"))
    exam_col = _first_col(ov_cols, ("exam_name", "exam", "ksmc"))
    track_col = _first_col(ov_cols, ("xkkm", "xkqk", "track", "xkfx"))
    if not total_col or not class_col:
        return None
    where = _where_sql(
        [
            f"{_ident(exam_col)} = '{_sql_quote(exam_name)}'" if exam_name and exam_col else "",
            _track_sql_predicate(track_col, track) if track else "",
            _cohort_filter(ov_cols, cohort),
        ]
    )
    return (
        f"SELECT ROUND(AVG({_ident(total_col)}), 2) AS avg, "
        f"COUNT({_ident(total_col)}) AS n, "
        f"COUNT(DISTINCT {_ident(class_col)}) AS units "
        f"FROM tb_score_overview{where}"
    )


def _student_exam_sql(
    ov_cols: list[str],
    *,
    exam_name: str = "",
) -> str | None:
    """学生个人：按考试返回总分和科目分。"""
    if not ov_cols:
        return None
    total_col = _first_col(ov_cols, ("zf6m", "zf4m", "zf3m", "zf", "total", "total_score"))
    student_col = _first_col(ov_cols, ("anon_stu_id", "student_id", "sid"))
    exam_col = _first_col(ov_cols, ("exam_name", "exam", "ksmc"))
    if not total_col or not student_col or not exam_col:
        return None
    select = [f"{_ident(exam_col)} AS exam_name", f"{_ident(total_col)} AS total"]
    for column in _subject_columns(ov_cols):
        select.append(f"{_ident(column)} AS {column}")
    where = _where_sql(
        [f"{_ident(exam_col)} = '{_sql_quote(exam_name)}'" if exam_name else ""]
    )
    return (
        f"SELECT {', '.join(select)} FROM tb_score_overview{where} "
        f"ORDER BY {_ident(exam_col)} LIMIT 100"
    )


def _line_meta(stats: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for item in stats or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("line_key") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "line_key": key,
                "line_name": item.get("line_name") or "",
                "label": item.get("label") or item.get("line_name") or "",
                "threshold": item.get("threshold"),
                "track": item.get("track") or "",
            }
        )
    return out


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 2) if values else None


def _totals(students: list[dict[str, Any]]) -> list[float]:
    values: list[float] = []
    for row in students:
        try:
            value = float(row.get("total"))
        except (TypeError, ValueError):
            continue
        if value > 0:
            values.append(value)
    return values


def _class_trend_point(
    exam: str, date: str, students: list[dict[str, Any]], bars: list[dict[str, Any]]
) -> dict[str, Any]:
    totals = _totals(students)
    # Use only the thresholds explicitly configured for this exam.
    special = [
        bar for bar in bars
        if bar.get("exam_name") == exam and line_code_of(bar) == "tz"
    ]
    tracks = {bar.get("track") or "" for bar in special}
    candidates, stats = line_stats_for_totals(special, students)
    complete = bool(totals and stats) and all(
        "" in tracks or (student.get("track") or "") in tracks for student in students
    )
    return {
        "exam": exam,
        "date": date,
        "avg": _mean(totals),
        "valid_count": len(totals),
        "special": {
            "rate": stats[0]["rate"],
            "reached": stats[0]["reached"],
            "candidates": candidates,
            "thresholds": [
                {"track": bar.get("track") or "", "score": bar["threshold"]}
                for bar in special if not bar.get("track") or any(
                    student.get("track") == bar["track"] for student in students
                )
            ],
        } if complete else None,
    }


async def _class_exam_trends(
    execute, ov_cols: list[str], *, exam: str, track: str, cohort: str,
    class_name: str, students: list[dict[str, Any]], bars: list[dict[str, Any]],
    exams: list[str], batches: dict[str, tuple[datetime, str, str]],
) -> list[dict[str, Any]]:
    if not students or len(students) >= 1000:
        return []
    anchor = batches.get(exam)
    if not anchor or not anchor[1]:
        return []
    anchor_time, anchor_cohort, anchor_type = anchor
    history = sorted({
        (batch[0], name)
        for name in exams
        if name != exam
        and (batch := batches.get(name))
        and batch[0] < anchor_time
        and batch[1:] == (anchor_cohort, anchor_type)
    })
    points = []
    for when, name in history[-4:]:
        rows = await _fetch_dicts(execute, _student_rows_sql(
            ov_cols, exam_name=name, track=track, cohort=cohort, class_name=class_name,
        ))
        points.append(_class_trend_point(name, when.date().isoformat(), rows if len(rows) < 1000 else [], bars))
    points.append(_class_trend_point(exam, anchor_time.date().isoformat(), students, bars))
    return points


def _filters(
    *,
    scope: dict[str, Any],
    exams: list[str],
    tracks: list[str],
    lines: list[dict[str, Any]],
    ov_cols: list[str],
) -> dict[str, Any]:
    return {
        "exams": exams,
        "tracks": tracks,
        "lines": lines,
        "cohorts": list(scope.get("cohorts") or []),
        "classes": list(scope.get("classes") or []),
        "subjects": _subject_options(_subject_columns(ov_cols)),
    }


def _actions(level: str) -> list[dict[str, str]]:
    common = [
        {"label": "学情分析", "href": "/construct/analysis"},
        {"label": "达线看板", "href": "/construct/education/line-reach"},
    ]
    if level in {"school", "grade", "class"}:
        common[0] = {"label": "本范围报告", "href": "/construct/analysis"}
    return common


def _legacy_payload(level: str, scope: Any) -> dict[str, Any]:
    label = str(getattr(scope, "school_name", "") or getattr(scope, "school_id", "") or "")
    return {
        "level": level,
        "fallback": "legacy_line_reach",
        "scope": {"label": label, "levels": [level] if level else [], "capabilities": []},
        "filters": {},
        "kpis": {"candidates": 0, "by_line": []},
        "chart": {"dimension": "", "metric": "rate", "rows": []},
        "table": {"dimension": "", "columns": [], "rows": []},
        "secondary": {},
        "notices": [notice("legacy_scope", "旧版权限账号请使用达线看板")],
        "actions": _actions(level),
    }


@router.get("/overview")
async def education_overview(
    exam_name: str | None = Query(None, max_length=200),
    track: str = Query("", max_length=40),
    line_key: str = Query("", max_length=200),
    cohort: str = Query("", max_length=100),
    class_name: str = Query("", max_length=100),
    current_user: UserResponse = Depends(get_current_user),
    workspace_oid: int = Depends(get_workspace_oid),
) -> dict:
    """按当前 v2 授权范围返回一份可直接渲染的总览 payload。"""
    _ = line_key  # 分数线由前端从 filters.lines 选择；后端返回全部 by_line。
    ds_id, ds_oid, legacy_scope = _exam_line_tool_context(current_user)
    report_scope = _load_report_scope(current_user, ds_id, int(workspace_oid))
    level = derive_level(report_scope, legacy_scope)
    if not level:
        raise ForbiddenException("当前账号没有可用的教育数据范围")
    if report_scope is None:
        return success_response(_legacy_payload(level, legacy_scope))

    orch = _build_orchestrator(
        ds_id,
        ds_oid,
        user_id=int(current_user.id),
        report_scope=report_scope,
    )
    execute = orch._execute_sql
    ov_cols = await _probe_columns(execute, "tb_score_overview")
    exam_sql = _scoped_exams_sql(ov_cols, cohort=cohort, class_name=class_name)
    exam_rows = await _fetch_dicts(execute, exam_sql) if exam_sql else []
    tracks = sorted({row["track"] for row in exam_rows if row.get("track")})
    scored_exams = {
        str(row["exam_name"]) for row in exam_rows
        if row.get("exam_name") and (track not in {"物理类", "历史类"} or row.get("track") == track)
    }
    scope = scope_payload(report_scope, level)
    dimension = dimension_for_level(level)
    notices: list[dict[str, str]] = []
    secondary: dict[str, Any] = {}

    if not scored_exams:
        return success_response(
            {
                "level": level,
                "scope": scope,
                "exam_name": None,
                "filters": _filters(
                    scope=scope, exams=[], tracks=tracks, lines=[], ov_cols=ov_cols
                ),
                "kpis": {"candidates": 0, "by_line": []},
                "chart": {"dimension": dimension, "metric": "rate", "rows": []},
                "table": {"dimension": dimension, "columns": [], "rows": []},
                "secondary": {},
                "notices": [notice("no_data", "当前授权范围及筛选下暂无考试成绩")],
                "actions": _actions(level),
            }
        )

    load_batches = _fetch_dicts(execute, "SELECT batch_name, exam_time, jc, exam_type FROM tb_exam_batch")
    bars_all: list[dict[str, Any]] = []
    if level == "student":
        batch_rows = await load_batches
    else:
        batch_rows, bar_rows = await asyncio.gather(load_batches, _load_line_reach_bar_rows(execute))
        bars_all = normalize_fraction_bars(bar_rows)
    batches = _exam_batches(
        batch_rows, cohort or (report_scope.cohorts[0] if len(report_scope.cohorts) == 1 else "")
    )
    exams = sorted(scored_exams, key=lambda name: (batches.get(name, (datetime.min, "", ""))[0], name), reverse=True)
    requested = (exam_name or "").strip()
    # 学生可主动切换为历次成绩；首次进入与失效选项均回退到最近的授权考试。
    exam = requested if requested in scored_exams else exams[0]
    if level == "student" and exam_name == "":
        exam = ""
    bars = filter_fraction_bars(bars_all, exam_name=exam, track="")

    # 学校级跨多校时先看学校对比；单校时看本校班级。
    if level == "school" and len(report_scope.schools) > 1:
        dimension = "school"
        rows = await _fetch_dicts(
            execute,
            _overview_agg_sql(ov_cols, bars, exam_name=exam, track=track, allow_without_bars=True),
        )
        payload = payload_from_school_agg(rows, bars, exam_name=exam, track=track)
        chart_rows = school_chart_rows(payload)
        lines = payload.get("lines") or []
        kpis = payload.get("kpis") or {"candidates": 0, "by_line": []}
    elif level in {"city", "district"}:
        dimension = "district" if level == "city" else "school"
        rows = await _fetch_dicts(
            execute,
            _overview_agg_sql(ov_cols, bars, exam_name=exam, track=track, allow_without_bars=True),
        )
        payload = payload_from_school_agg(rows, bars, exam_name=exam, track=track)
        chart_rows = (
            district_chart_rows(payload) if dimension == "district" else school_chart_rows(payload)
        )
        lines = payload.get("lines") or []
        kpis = payload.get("kpis") or {"candidates": 0, "by_line": []}
    elif level in {"school", "grade"}:
        dimension = "class"
        rows = await _fetch_dicts(
            execute,
            _class_agg_sql(
                ov_cols, bars, exam_name=exam, track=track, cohort=cohort
            ),
        )
        payload = payload_from_school_agg(rows, bars, exam_name=exam, track=track)
        chart_rows = class_chart_rows(payload)
        lines = payload.get("lines") or []
        kpis = payload.get("kpis") or {"candidates": 0, "by_line": []}
    elif level == "class":
        dimension = "student"
        students = await _fetch_dicts(
            execute,
            _student_rows_sql(
                ov_cols,
                exam_name=exam,
                track=track,
                cohort=cohort,
                class_name=class_name,
            ),
        )
        candidates, by_line = line_stats_for_totals(bars, students)
        chart_rows = student_chart_rows(students)
        lines = _line_meta(by_line)
        kpis = {"candidates": candidates, "by_line": by_line}
        totals = _totals(students)
        secondary["score_bands"] = score_bands(
            students, config_store.get_config(), full_score=_total_full_score(ov_cols)
        )
        secondary["subject_avg"] = subject_averages(students, _subject_columns(ov_cols))
        secondary["class_trends"] = await _class_exam_trends(
            execute, ov_cols, exam=exam, track=track, cohort=cohort,
            class_name=class_name, students=students, bars=bars_all,
            exams=exams, batches=batches,
        )
        if report_scope.has("grade_baseline"):
            grade_sql = _grade_avg_sql(
                ov_cols, exam_name=exam, track=track, cohort=cohort
            )
            grade_rows: list[dict[str, Any]] = []
            if grade_sql:
                async def _reference(sql: str) -> dict[str, Any]:
                    return await orch._execute_reference(sql, "grade")

                grade_rows = await _fetch_dicts(_reference, grade_sql)
            if grade_rows:
                row = grade_rows[0]
                n = int(row.get("n") or 0)
                units = int(row.get("units") or 0)
                minimum_sample = int(report_scope.manifest.minimum_sample_size)
                minimum_units = int(report_scope.manifest.minimum_reference_units)
                if n >= minimum_sample and units >= minimum_units:
                    own_avg = _mean(totals)
                    grade_avg = float(row.get("avg")) if row.get("avg") is not None else None
                    secondary["grade_baseline"] = {
                        "avg": grade_avg,
                        "n": n,
                        "units": units,
                        "own_avg": own_avg,
                        "gap": (
                            round(own_avg - grade_avg, 2)
                            if own_avg is not None and grade_avg is not None
                            else None
                        ),
                    }
                else:
                    notices.append(
                        notice(
                            "insufficient_sample",
                            f"年级对照有效人数 {n}、参评班数 {units}，未达到门槛",
                        )
                    )
            else:
                notices.append(notice("grade_baseline_unavailable", "年级对照暂不可用"))
        else:
            notices.append(
                notice("capability_missing", "未开通年级均分基准，已隐藏年级对照")
            )
        if len(students) >= 1000:
            notices.append(notice("truncated", "学生明细已截断"))
        if not students:
            notices.append(notice("no_data", "该考试暂无本班成绩数据"))
    else:  # student
        dimension = "exam"
        rows = await _fetch_dicts(
            execute, _student_exam_sql(ov_cols, exam_name=exam)
        )
        # 历次成绩时间正序，最后一项的科目分对应最新一场。
        exam_order = {name: index for index, name in enumerate(exams)}
        rows = sorted(
            (row for row in rows if row.get("exam_name") in exam_order),
            key=lambda row: exam_order[row["exam_name"]], reverse=True,
        )
        chart_rows = [
            {
                "id": str(row.get("exam_name") or ""),
                "label": str(row.get("exam_name") or ""),
                "total": row.get("total"),
            }
            for row in rows
        ]
        latest = rows[-1] if rows else {}
        latest_subjects = subject_averages([latest], _subject_columns(ov_cols)) if latest else []
        secondary["subject_avg"] = latest_subjects
        candidates = 1 if rows else 0
        by_line: list[dict[str, Any]] = []
        lines = []
        kpis = {"candidates": candidates, "by_line": by_line}
        if not rows:
            notices.append(notice("no_data", "暂无个人成绩数据"))

    if not chart_rows:
        notices.append(notice("no_data", "当前筛选下暂无成绩数据"))
    elif dimension in {"district", "school", "class"} and len(chart_rows) == 1:
        notices.append(
            notice("single_group", "当前范围只有 1 个单位，排名无意义，已展示该单位明细")
        )
    if level == "class":
        notices.append(notice("class_scope", "班级级不展示班级间排名"))
    elif level != "student" and chart_rows and not lines:
        notices.append(notice("no_lines", "该考试尚未配置分数线"))

    metric = "total" if dimension in {"student", "exam"} else "rate"
    return success_response(
        {
            "level": level,
            "scope": scope,
            "exam_name": exam,
            "filters": _filters(
                scope=scope,
                exams=exams,
                tracks=tracks,
                lines=lines,
                ov_cols=ov_cols,
            ),
            "kpis": kpis,
            "chart": {"dimension": dimension, "metric": metric, "rows": chart_rows},
            "table": {"dimension": dimension, "columns": [], "rows": chart_rows},
            "secondary": secondary,
            "notices": notices,
            "actions": _actions(level),
        }
    )


__all__ = ["router"]
