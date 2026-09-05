"""成绩 KPI 库内聚合 SQL（无 LIMIT），避免行级截断导致均分/及格率偏差。

聚合字段与 ``stats.compute_score_stats`` 对齐；满分取 ``MAX(exam_score)``，
缺省时回退 ``config.default_full_score``，阈值按 ratio 计算（与有满分时的 Python 路径一致）。
"""

from __future__ import annotations

from typing import Any

from src.agent.education.config import EducationConfig
from src.agent.education.schema_mapping import EXAM_JOIN
from src.agent.education.stats import _seg_label, _seg_pairs

SCORE_JOIN_FROM = (
    "FROM tb_score sc\n"
    "JOIN tb_school sch ON sc.school_id = sch.id\n"
    f"{EXAM_JOIN}"
)


def _normalize_where(where_sql: str) -> str:
    text = (where_sql or "").strip()
    if not text:
        return ""
    if text.upper().startswith("WHERE"):
        return "\n" + text
    return "\nWHERE " + text


def build_primary_exam_id_sql(where_sql: str = "") -> str:
    """参与人数最多的 exam_id（与 prepare_score_rows_for_kpi 收敛策略一致）。"""
    where = _normalize_where(where_sql)
    return (
        "SELECT sc.exam_id AS exam_id\n"
        f"{SCORE_JOIN_FROM}"
        f"{where}\n"
        "GROUP BY sc.exam_id\n"
        "ORDER BY COUNT(*) DESC\n"
        "LIMIT 1"
    )


def build_score_count_sql(where_sql: str = "") -> str:
    where = _normalize_where(where_sql)
    if where:
        return (
            "SELECT COUNT(*) AS cnt\n"
            f"{SCORE_JOIN_FROM}"
            f"{where}\n"
            "AND sc.score > 0"
        )
    return (
        "SELECT COUNT(*) AS cnt\n"
        f"{SCORE_JOIN_FROM}\n"
        "WHERE sc.score > 0"
    )


def build_kpi_aggregate_sql(where_sql: str, config: EducationConfig) -> str:
    """单行 KPI 聚合；无 LIMIT。"""
    where = _normalize_where(where_sql)
    default_fs = float(config.default_full_score)
    pass_r = float(config.pass_ratio)
    exc_r = float(config.excellent_ratio)
    good_r = float(config.good_ratio)
    low_r = float(config.low_score_ratio)
    ratios = [float(r) for r in (config.score_segment_ratios or [0.6, 0.7, 0.8, 0.9])]
    # 边界比例：0, r0, r1, ..., 1.0
    bound_ratios = [0.0] + ratios + [1.0]

    seg_selects: list[str] = []
    for i in range(len(bound_ratios) - 1):
        lo_r, hi_r = bound_ratios[i], bound_ratios[i + 1]
        if i == len(bound_ratios) - 2:
            cond = f"b.score >= meta.fs * {lo_r} AND b.score <= meta.fs * {hi_r}"
        else:
            cond = f"b.score >= meta.fs * {lo_r} AND b.score < meta.fs * {hi_r}"
        seg_selects.append(f"COUNT(*) FILTER (WHERE {cond}) AS seg_{i}_count")

    return (
        "WITH base AS (\n"
        "  SELECT sc.score AS score, sc.exam_score AS exam_score\n"
        f"  {SCORE_JOIN_FROM}"
        f"{where}\n"
        "),\n"
        "meta AS (\n"
        f"  SELECT COALESCE(MAX(exam_score), {default_fs}) AS fs,\n"
        "         COUNT(*) FILTER (WHERE score > 0) AS cnt\n"
        "  FROM base\n"
        ")\n"
        "SELECT\n"
        "  meta.cnt AS count,\n"
        "  meta.fs AS full_score,\n"
        "  ROUND(AVG(b.score)::numeric, 2) AS avg,\n"
        "  ROUND((PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY b.score))::numeric, 2) AS median,\n"
        "  ROUND(STDDEV_SAMP(b.score)::numeric, 2) AS stdev,\n"
        "  MIN(b.score) AS min,\n"
        "  MAX(b.score) AS max,\n"
        f"  ROUND(COUNT(*) FILTER (WHERE b.score >= meta.fs * {pass_r})::numeric "
        "/ NULLIF(meta.cnt, 0) * 100, 2) AS pass_rate,\n"
        f"  ROUND(COUNT(*) FILTER (WHERE b.score >= meta.fs * {exc_r})::numeric "
        "/ NULLIF(meta.cnt, 0) * 100, 2) AS excellent_rate,\n"
        f"  ROUND(COUNT(*) FILTER (WHERE b.score >= meta.fs * {good_r})::numeric "
        "/ NULLIF(meta.cnt, 0) * 100, 2) AS good_rate,\n"
        f"  ROUND(COUNT(*) FILTER (WHERE b.score < meta.fs * {low_r})::numeric "
        "/ NULLIF(meta.cnt, 0) * 100, 2) AS low_score_rate,\n"
        f"  ROUND(COUNT(*) FILTER (WHERE b.score < meta.fs * {pass_r})::numeric "
        "/ NULLIF(meta.cnt, 0) * 100, 2) AS fail_rate,\n"
        + ",\n".join(seg_selects)
        + "\nFROM base b\nCROSS JOIN meta\n"
        "WHERE b.score > 0\n"
        "GROUP BY meta.cnt, meta.fs"
    )


def kpi_row_to_stats(
    row: dict[str, Any] | None,
    config: EducationConfig,
) -> dict[str, Any]:
    """把聚合行还原为 ``compute_score_stats`` 同结构 dict。"""
    if not row:
        return compute_empty_stats(config, full_score=None)

    def _f(key: str) -> float | None:
        v = row.get(key)
        if v is None or v == "":
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    count = int(_f("count") or 0)
    full_score = _f("full_score")
    upper = full_score if full_score is not None else config.default_full_score
    if count <= 0:
        return compute_empty_stats(config, full_score=upper)

    pass_thr = float(upper) * float(config.pass_ratio)
    exc_thr = float(upper) * float(config.excellent_ratio)
    seg_bounds = config.resolved_segments(upper)
    seg_pairs = list(_seg_pairs(seg_bounds))
    segments: list[dict[str, Any]] = []
    for i, (lo, hi) in enumerate(seg_pairs):
        cnt = int(_f(f"seg_{i}_count") or 0)
        segments.append(
            {
                "label": _seg_label(lo, hi),
                "count": cnt,
                "ratio": round(cnt / count * 100, 2) if count else 0.0,
            }
        )

    stdev = _f("stdev")
    variance = None if stdev is None else round(stdev ** 2, 2)
    return {
        "count": count,
        "avg": _f("avg"),
        "median": _f("median"),
        "stdev": stdev,
        "variance": variance,
        "min": _f("min"),
        "max": _f("max"),
        "pass_rate": _f("pass_rate"),
        "excellent_rate": _f("excellent_rate"),
        "good_rate": _f("good_rate"),
        "low_score_rate": _f("low_score_rate"),
        "fail_rate": _f("fail_rate"),
        "full_score": upper,
        "pass_line": round(pass_thr, 4),
        "excellent_line": round(exc_thr, 4),
        "pass_ratio": float(config.pass_ratio),
        "excellent_ratio": float(config.excellent_ratio),
        "segments": segments,
    }


def compute_empty_stats(
    config: EducationConfig,
    *,
    full_score: float | None,
) -> dict[str, Any]:
    from src.agent.education.stats import compute_score_stats

    return compute_score_stats([], config, full_score)


def append_exam_id_predicate(where_sql: str, exam_id: str) -> str:
    """在已有 WHERE 上追加 exam_id 精确条件。"""
    eid = str(exam_id).replace("'", "''")
    pred = f"sc.exam_id = '{eid}'"
    text = (where_sql or "").strip()
    if not text:
        return f"WHERE {pred}"
    if text.upper().startswith("WHERE"):
        return f"{text} AND {pred}"
    return f"WHERE {text} AND {pred}"


__all__ = [
    "SCORE_JOIN_FROM",
    "append_exam_id_predicate",
    "build_kpi_aggregate_sql",
    "build_primary_exam_id_sql",
    "build_score_count_sql",
    "compute_empty_stats",
    "kpi_row_to_stats",
]
