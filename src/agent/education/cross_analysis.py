"""交叉分析——二维 pivot 与组间对比。"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from src.agent.education.aggregation import _float, _row_key


def cross_analyze(
    dim_a: str,
    dim_b: str,
    rows: list[dict[str, Any]],
    *,
    metric: str = "avg",
    score_key: str = "score",
) -> dict[str, Any]:
    """返回 pivot：rows/cols/matrix。"""
    buckets: dict[tuple[str, str], list[float]] = defaultdict(list)
    row_set: set[str] = set()
    col_set: set[str] = set()
    for row in rows:
        a = _row_key(row, dim_a)
        b = _row_key(row, dim_b)
        val = _float(row.get(score_key))
        if val is None:
            continue
        buckets[(a, b)].append(val)
        row_set.add(a)
        col_set.add(b)
    row_labels = sorted(row_set)
    col_labels = sorted(col_set)
    matrix: list[list[float | None]] = []
    for a in row_labels:
        row_vals: list[float | None] = []
        for b in col_labels:
            vals = buckets.get((a, b), [])
            if not vals:
                row_vals.append(None)
            elif metric == "count":
                row_vals.append(float(len(vals)))
            else:
                row_vals.append(round(sum(vals) / len(vals), 2))
        matrix.append(row_vals)
    return {
        "dim_a": dim_a,
        "dim_b": dim_b,
        "rows": row_labels,
        "cols": col_labels,
        "matrix": matrix,
        "metric": metric,
    }


def compare_groups(
    base_group: dict[str, Any],
    target_groups: list[dict[str, Any]],
    metrics: list[str] | None = None,
    *,
    stdev_key: str = "stdev",
) -> list[dict[str, Any]]:
    """与基准组对比差值。"""
    metrics = metrics or ["avg", "pass_rate", "excellent_rate"]
    base_stdev = _float(base_group.get(stdev_key)) or 0.0
    out: list[dict[str, Any]] = []
    for tg in target_groups:
        item = {
            "name": tg.get("dimension_value") or tg.get("name") or "",
            "deltas": {},
            "notable": False,
        }
        for m in metrics:
            bv = _float(base_group.get(m))
            tv = _float(tg.get(m))
            if bv is None or tv is None:
                continue
            delta = round(tv - bv, 2)
            item["deltas"][m] = delta
            if base_stdev and abs(delta) > base_stdev:
                item["notable"] = True
        out.append(item)
    return out


__all__ = ["compare_groups", "cross_analyze"]
