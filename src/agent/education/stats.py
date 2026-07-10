"""成绩统计——纯函数，确定性，不依赖 LLM 心算。

``compute_score_stats`` 是核心：给定一组分数 + 阈值配置，一次性算出报告里
所有 KPI（均分 / 中位数 / 标准差 / 及格率 / 优秀率 / 分数段分布 / 最高最低）。
LLM 只负责调用本函数并把结果填进模板，绝不自行心算。
"""

from __future__ import annotations

import math
import statistics
from typing import Any

from src.agent.education.config import EducationConfig


def _safe_mean(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


def _safe_median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def _safe_stdev(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    return statistics.stdev(values)


def _rate(values: list[float], predicate) -> float | None:
    if not values:
        return None
    hit = sum(1 for v in values if predicate(v))
    return round(hit / len(values) * 100, 2)


def pearson_r(x: list[float], y: list[float]) -> float | None:
    """皮尔逊相关系数；样本数 < 2 或方差为 0 返回 None。"""
    n = min(len(x), len(y))
    if n < 2:
        return None
    xs, ys = x[:n], y[:n]
    mx = statistics.fmean(xs)
    my = statistics.fmean(ys)
    num = sum((xi - mx) * (yi - my) for xi, yi in zip(xs, ys))
    dx = math.sqrt(sum((xi - mx) ** 2 for xi in xs))
    dy = math.sqrt(sum((yi - my) ** 2 for yi in ys))
    if dx == 0 or dy == 0:
        return None
    return round(num / (dx * dy), 3)


def compute_score_stats(
    scores: list[float],
    config: EducationConfig,
    full_score: float | None = None,
) -> dict[str, Any]:
    """对一组分数计算报告级 KPI。

    Args:
        scores: 数值列表；None / 非数已在 ``data_adapter`` 层过滤。
        config: 阈值配置。
        full_score: 满分；为 None 时用 ``config.default_full_score``。

    Returns:
        dict，所有 rate 字段为百分数（0–100，保留两位小数）。
        空列表返回各字段为 ``None`` 的占位结构，便于模板统一渲染。
    """
    valid = [float(s) for s in scores if s is not None]
    upper = full_score if full_score is not None else config.default_full_score
    seg_bounds = config.resolved_segments(upper if full_score is not None else None)

    if not valid:
        return {
            "count": 0,
            "avg": None,
            "median": None,
            "stdev": None,
            "min": None,
            "max": None,
            "pass_rate": None,
            "excellent_rate": None,
            "good_rate": None,
            "low_score_rate": None,
            "fail_rate": None,
            "full_score": upper,
            "segments": [{"label": _seg_label(lo, hi), "count": 0, "ratio": 0.0}
                         for lo, hi in _seg_pairs(seg_bounds)],
        }

    if full_score is not None:
        pass_thr = float(full_score) * config.pass_ratio
        exc_thr = float(full_score) * config.excellent_ratio
        good_thr = float(full_score) * config.good_ratio
        low_thr = float(full_score) * config.low_score_ratio
    else:
        pass_thr = config.pass_threshold
        exc_thr = config.excellent_threshold
        good_thr = config.excellent_threshold * (config.good_ratio / config.excellent_ratio)
        low_thr = config.pass_threshold * (config.low_score_ratio / config.pass_ratio)

    seg_pairs = list(_seg_pairs(seg_bounds))
    segments = []
    for lo, hi in seg_pairs:
        # 左闭右开，最高段闭区间
        if hi == seg_bounds[-1]:
            cnt = sum(1 for v in valid if lo <= v <= hi)
        else:
            cnt = sum(1 for v in valid if lo <= v < hi)
        segments.append({
            "label": _seg_label(lo, hi),
            "count": cnt,
            "ratio": round(cnt / len(valid) * 100, 2) if valid else 0.0,
        })

    return {
        "count": len(valid),
        "avg": round(_safe_mean(valid) or 0, 2),
        "median": round(_safe_median(valid) or 0, 2),
        "stdev": None if _safe_stdev(valid) is None else round(_safe_stdev(valid) or 0, 2),
        "min": min(valid),
        "max": max(valid),
        "pass_rate": _rate(valid, lambda v: v >= pass_thr),
        "excellent_rate": _rate(valid, lambda v: v >= exc_thr),
        "good_rate": _rate(valid, lambda v: v >= good_thr),
        "low_score_rate": _rate(valid, lambda v: v < low_thr),
        "fail_rate": _rate(valid, lambda v: v < pass_thr),
        "full_score": upper,
        "segments": segments,
    }


def _seg_pairs(bounds: list[float]) -> list[tuple[float, float]]:
    return [(bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1)]


def _seg_label(lo: float, hi: float) -> str:
    lo_i = int(lo) if lo == int(lo) else lo
    hi_i = int(hi) if hi == int(hi) else hi
    return f"{lo_i}-{hi_i}"


def score_segment_label(
    score: float,
    *,
    full_score: float | None = None,
    config: EducationConfig | None = None,
) -> str:
    """将单科分数映射到分数段标签（供交叉分析/热力图使用）。"""
    if config is None:
        config = EducationConfig()
    bounds = config.resolved_segments(full_score)
    pairs = _seg_pairs(bounds)
    for lo, hi in pairs:
        if lo <= score <= hi:
            return _seg_label(lo, hi)
    if pairs:
        return _seg_label(pairs[-1][0], pairs[-1][1])
    return "其他"


def normalize_segments(
    segments: list[dict[str, Any]],
    *,
    config: EducationConfig | None = None,
    full_score: float | None = None,
) -> list[dict[str, Any]]:
    """补齐分数段 ``label`` / ``ratio``，避免 LLM 仅传 count 导致表格空白。

    当 ``label`` 缺失时，按 ``config`` 分数段边界推断；``ratio`` 缺失时按
    各段 ``count`` 占合计人数重算百分比。
    """
    if not segments:
        return []
    if config is None:
        config = EducationConfig()
    upper = full_score if full_score is not None else config.default_full_score
    bounds = config.resolved_segments(upper if full_score is not None else None)
    pairs = list(_seg_pairs(bounds))
    default_labels = [_seg_label(lo, hi) for lo, hi in pairs]

    total = sum(int(s.get("count") or 0) for s in segments)
    out: list[dict[str, Any]] = []
    for idx, seg in enumerate(segments):
        row = dict(seg)
        label = str(row.get("label") or "").strip()
        if not label:
            if idx < len(default_labels):
                label = default_labels[idx]
            elif len(segments) == len(default_labels):
                label = default_labels[idx]
            else:
                label = f"段{idx + 1}"
            row["label"] = label
        count = int(row.get("count") or 0)
        ratio = row.get("ratio")
        if ratio is None and total > 0:
            row["ratio"] = round(count / total * 100, 2)
        elif ratio is not None:
            try:
                row["ratio"] = round(float(ratio), 2)
            except (TypeError, ValueError):
                row["ratio"] = round(count / total * 100, 2) if total > 0 else 0.0
        else:
            row["ratio"] = 0.0
        row["count"] = count
        out.append(row)
    return out


# ---- 排名 ----------------------------------------------------------------

def compute_rankings(
    items: list[dict[str, Any]],
    value_key: str = "value",
    name_key: str = "name",
) -> list[dict[str, Any]]:
    """对聚合结果计算排名与百分位。

    Args:
        items: ``[{name: "初三1班", value: 78.5}, ...]``；
        value_key / name_key: 值与名称字段名。

    Returns:
        按值降序排列，每项追加 ``rank``（1 起，同值并列）与 ``percentile``
        （0–100，越靠前越大）。空列表返回 ``[]``。
    """
    if not items:
        return []
    enriched: list[dict[str, Any]] = []
    for it in items:
        enriched.append(dict(it))
    enriched.sort(key=lambda x: float(x.get(value_key) or 0), reverse=True)

    n = len(enriched)
    prev_value: float | None = None
    prev_rank = 0
    for idx, it in enumerate(enriched):
        val = float(it.get(value_key) or 0)
        if prev_value is None or val != prev_value:
            prev_rank = idx + 1
            prev_value = val
        it["rank"] = prev_rank
        # percentile：排名越靠前分数越高；用 100 * (n - rank) / (n - 1) 处理 n=1
        if n <= 1:
            it["percentile"] = 100.0
        else:
            it["percentile"] = round(100.0 * (n - prev_rank) / (n - 1), 2)
    return enriched


# ---- 分层预警 ------------------------------------------------------------

def identify_at_risk_students(
    students: list[dict[str, Any]],
    config: EducationConfig,
    score_key: str = "score",
    name_key: str = "name",
    subject_key: str = "subject",
    prev_score_key: str = "prev_score",
) -> dict[str, list[dict[str, Any]]]:
    """识别需要关注的学生：临界生 / 大幅退步 / 偏科生。

    Args:
        students: 每条形如 ``{name, subject, score, prev_score?}``；可含多科目
            多行（同一学生多科）。``prev_score`` 为上一次同科目成绩，缺省视为
            无退步数据。
        config: 阈值配置。

    Returns:
        ``{"critical": [...], "regression": [...], "imbalanced": [...]}``。
        每条含原始字段 + ``reason`` 说明。
    """
    critical: list[dict[str, Any]] = []
    regression: list[dict[str, Any]] = []
    imbalanced: list[dict[str, Any]] = []

    pass_thr = config.pass_threshold
    margin = config.critical_margin
    reg_thr = config.regression_threshold

    # 按学生分组科目，用于偏科判定
    by_student: dict[str, list[dict[str, Any]]] = {}
    for s in students:
        name = str(s.get(name_key) or "")
        score = s.get(score_key)
        if score is None:
            continue
        try:
            score_val = float(score)
        except (TypeError, ValueError):
            continue
        by_student.setdefault(name, []).append(s)

        # 临界生：分数位于 [pass - margin, pass + margin)
        if pass_thr - margin <= score_val < pass_thr + margin:
            critical.append({**s, "reason": f"临界生：{score_val} 分处于及格线 ±{margin} 区间"})

        # 大幅退步：与 prev_score 相比降幅超过 |regression_threshold|
        prev = s.get(prev_score_key)
        if prev is not None:
            try:
                prev_val = float(prev)
                delta = score_val - prev_val
                if delta <= reg_thr:
                    regression.append({**s, "reason": f"大幅退步：{prev_val} → {score_val}（降 {abs(delta)} 分）"})
            except (TypeError, ValueError):
                pass

    # 偏科生：同一学生各科分差最大值 ≥ 20 且最低科 < 及格线
    for name, rows in by_student.items():
        if len(rows) < 2:
            continue
        scores = []
        for r in rows:
            try:
                scores.append((str(r.get(subject_key) or ""), float(r.get(score_key))))
            except (TypeError, ValueError):
                continue
        if len(scores) < 2:
            continue
        scores.sort(key=lambda x: x[1])
        low_sub, low_val = scores[0]
        high_sub, high_val = scores[-1]
        if high_val - low_val >= 20 and low_val < pass_thr:
            imbalanced.append({
                "name": name,
                "low_subject": low_sub, "low_score": low_val,
                "high_subject": high_sub, "high_score": high_val,
                "reason": f"偏科：{high_sub} {high_val} vs {low_sub} {low_val}",
            })

    return {"critical": critical, "regression": regression, "imbalanced": imbalanced}


# ---- 综合分析（多次考试） --------------------------------------------------


def compute_correlations(
    records: list[dict[str, Any]],
    exam_key: str = "exam",
    student_key: str = "student",
    subjects_key: str = "subjects",
    total_key: str = "total",
) -> dict[str, Any]:
    """各科与总分的相关性（Pearson r）。

    Args:
        records: 每条 ``{exam, student, subjects: {科目: 分数}, total}``。

    Returns:
        ``{"exams": [...], "subjects": [...], "series": [{"name": exam, "values": [r, ...]}]}``，
        可直接喂给 ``build_chart_option("correlation_bar", ...)``。
    """
    if not records:
        return {"exams": [], "subjects": [], "series": []}

    # 收集所有考试与科目
    exams: list[str] = []
    seen_exam: set[str] = set()
    for r in records:
        e = str(r.get(exam_key) or "")
        if e and e not in seen_exam:
            seen_exam.add(e)
            exams.append(e)
    subjects: list[str] = []
    seen_sub: set[str] = set()
    for r in records:
        for sub in (r.get(subjects_key) or {}).keys():
            if sub not in seen_sub:
                seen_sub.add(sub)
                subjects.append(sub)

    series: list[dict[str, Any]] = []
    for exam in exams:
        rows = [r for r in records if str(r.get(exam_key) or "") == exam]
        values: list[float | None] = []
        for sub in subjects:
            xs = [float((r.get(subjects_key) or {}).get(sub)) for r in rows
                  if (r.get(subjects_key) or {}).get(sub) is not None]
            ys = [float(r.get(total_key)) for r in rows
                  if (r.get(subjects_key) or {}).get(sub) is not None and r.get(total_key) is not None]
            values.append(pearson_r(xs, ys))
        series.append({"name": exam, "values": values})
    return {"exams": exams, "subjects": subjects, "series": series}


def compute_level_distribution(
    totals: list[float],
    config: EducationConfig,
    full_score: float | None = None,
) -> list[dict[str, Any]]:
    """按总分把学生分到 A/B/C/D 水平段。

    阈值（占满分百分比，可经 config 覆盖）：A≥85%，B≥70%，C≥60%，其余 D。
    返回 ``[{"name": "A (优秀)", "value": n, "color": ...}, ...]``，可直接喂给
    ``build_chart_option("pie", ...)``。
    """
    upper = full_score if full_score is not None else config.default_full_score
    a_thr = upper * 0.85
    b_thr = upper * 0.70
    c_thr = upper * 0.60
    a = sum(1 for t in totals if t >= a_thr)
    b = sum(1 for t in totals if b_thr <= t < a_thr)
    c = sum(1 for t in totals if c_thr <= t < b_thr)
    d = sum(1 for t in totals if t < c_thr)
    return [
        {"name": "A (优秀)", "value": a, "color": "#2ecc71"},
        {"name": "B (良好)", "value": b, "color": "#3498db"},
        {"name": "C (中等)", "value": c, "color": "#f39c12"},
        {"name": "D (待提升)", "value": d, "color": "#e74c3c"},
    ]


def compute_trend_distribution(
    deltas: list[dict[str, Any]],
    stable_margin: float = 5.0,
    value_key: str = "delta",
    name_key: str = "name",
) -> dict[str, Any]:
    """按总分变化把学生分为进步/退步/稳定。

    Args:
        deltas: ``[{name, delta: 末次-首次}, ...]``。
        stable_margin: |delta| <= margin 视为稳定。

    Returns:
        ``{"items": [pie items], "progress": [...], "regress": [...], "stable": [...]}``。
    """
    progress: list[dict[str, Any]] = []
    regress: list[dict[str, Any]] = []
    stable: list[dict[str, Any]] = []
    for d in deltas:
        delta = float(d.get(value_key) or 0)
        bucketed = dict(d)
        if delta > stable_margin:
            progress.append(bucketed)
        elif delta < -stable_margin:
            regress.append(bucketed)
        else:
            stable.append(bucketed)
    items = [
        {"name": "进步 ↑", "value": len(progress), "color": "#2ecc71"},
        {"name": "退步 ↓", "value": len(regress), "color": "#e74c3c"},
        {"name": "稳定 →", "value": len(stable), "color": "#3498db"},
    ]
    return {"items": items, "progress": progress, "regress": regress, "stable": stable}


def compute_top_progress_regress(
    deltas: list[dict[str, Any]],
    top_n: int = 5,
    value_key: str = "delta",
    name_key: str = "name",
) -> dict[str, Any]:
    """进步/退步最快 TOP N。

    Returns:
        ``{"progress": [...], "regress": [...], "chart_items": [{"name","value","color"}]}``。
        chart_items 已按 delta 降序，进步绿、退步红，可直接喂给
        ``build_chart_option("progress_regress_bar", ...)``。
    """
    sorted_desc = sorted(deltas, key=lambda x: float(x.get(value_key) or 0), reverse=True)
    progress = sorted_desc[:top_n]
    regress = list(reversed(sorted_desc[-top_n:]))  # 退步最严重在前
    chart_items = [
        {"name": str(d.get(name_key) or ""), "value": float(d.get(value_key) or 0),
         "color": "#2ecc71" if float(d.get(value_key) or 0) >= 0 else "#e74c3c"}
        for d in sorted_desc[: top_n * 2]
    ]
    return {"progress": progress, "regress": regress, "chart_items": chart_items}


def compute_imbalance_degree(
    student_subject_avgs: list[dict[str, Any]],
    student_key: str = "name",
    subjects_key: str = "subjects",
    top_n: int = 10,
    min_degree: float = 0.0,
) -> list[dict[str, Any]]:
    """偏科度 = 各科班级排名的标准差；值越大科目间差距越大。

    Args:
        student_subject_avgs: 每条 ``{name, subjects: {科目: 均分}}``。

    Returns:
        按 imbalance_degree 降序前 top_n，每条含
        ``{name, degree, subjects, strong_subject, weak_subject, ...}``。
    """
    if not student_subject_avgs:
        return []
    subjects: list[str] = []
    seen: set[str] = set()
    for s in student_subject_avgs:
        for sub in (s.get(subjects_key) or {}).keys():
            if sub not in seen:
                seen.add(sub)
                subjects.append(sub)

    # 每科按分数排名（分数越高排名越靠前=数值越小）
    rank_map: dict[str, dict[str, int]] = {sub: {} for sub in subjects}
    for sub in subjects:
        ranked = sorted(
            student_subject_avgs,
            key=lambda s: float((s.get(subjects_key) or {}).get(sub, float("-inf"))),
            reverse=True,
        )
        for idx, s in enumerate(ranked, start=1):
            rank_map[sub][str(s.get(student_key) or "")] = idx

    result: list[dict[str, Any]] = []
    for s in student_subject_avgs:
        name = str(s.get(student_key) or "")
        subs = s.get(subjects_key) or {}
        ranks = [rank_map[sub][name] for sub in subjects if name in rank_map.get(sub, {})]
        if len(ranks) < 2:
            continue
        degree = round(statistics.pstdev(ranks), 2)
        if degree < min_degree:
            continue
        # 强弱科：按均分
        ordered = sorted(subs.items(), key=lambda kv: float(kv[1]), reverse=True)
        result.append({
            "name": name,
            "degree": degree,
            "subjects": subs,
            "strong_subject": ordered[0][0] if ordered else "",
            "strong_score": ordered[0][1] if ordered else None,
            "weak_subject": ordered[-1][0] if ordered else "",
            "weak_score": ordered[-1][1] if ordered else None,
        })
    result.sort(key=lambda x: float(x["degree"]), reverse=True)
    return result[:top_n]


def compute_subject_extremes(
    subject_deltas: list[dict[str, Any]],
    top_n: int = 5,
    value_key: str = "delta",
    student_key: str = "name",
    subject_key: str = "subject",
) -> dict[str, Any]:
    """单科进步/退步之最。

    Args:
        subject_deltas: ``[{name, subject, delta}, ...]``。

    Returns:
        ``{"progress": [...top_n], "regress": [...top_n], "chart_items": [...]}``。
    """
    sorted_desc = sorted(subject_deltas, key=lambda x: float(x.get(value_key) or 0), reverse=True)
    progress = sorted_desc[:top_n]
    regress = list(reversed(sorted_desc[-top_n:]))
    chart_items = [
        {
            "name": f"{d.get(student_key)} {d.get(subject_key)}",
            "value": float(d.get(value_key) or 0),
            "color": "#2ecc71" if float(d.get(value_key) or 0) >= 0 else "#e74c3c",
        }
        for d in sorted_desc[: top_n * 2]
    ]
    return {"progress": progress, "regress": regress, "chart_items": chart_items}


# ---- 题目指标 / 知识点掌握度 --------------------------------------------


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def compute_item_metrics(
    item_rows: list[dict[str, Any]],
    *,
    student_item_scores: list[dict[str, Any]] | None = None,
    question_key: str = "question_no",
    score_rate_key: str = "score_rate",
) -> list[dict[str, Any]]:
    """为每题计算得分率、难度、区分度（有学生逐题数据时）。"""
    out: list[dict[str, Any]] = []
    for row in item_rows:
        r = dict(row)
        rate = _num(r.get(score_rate_key))
        if rate is not None:
            r["difficulty"] = round(1 - rate / 100, 3)
        discrimination = None
        if student_item_scores and r.get(question_key) is not None:
            qno = r[question_key]
            pairs = [
                (_num(s.get("total_score")), _num(s.get("item_score")))
                for s in student_item_scores
                if s.get(question_key) == qno
                and _num(s.get("total_score")) is not None
                and _num(s.get("item_score")) is not None
            ]
            if len(pairs) >= 4:
                pairs.sort(key=lambda x: x[0], reverse=True)
                n = len(pairs)
                hi_n = max(1, int(n * 0.27))
                lo_n = max(1, int(n * 0.27))
                hi_rates = [b / a for a, b in pairs[:hi_n] if a]
                lo_rates = [b / a for a, b in pairs[-lo_n:] if a]
                if hi_rates and lo_rates:
                    discrimination = round(
                        (sum(hi_rates) / len(hi_rates) - sum(lo_rates) / len(lo_rates)) * 100,
                        2,
                    )
        r["discrimination"] = discrimination
        out.append(r)
    return out


def compute_knowledge_mastery(
    knowledge_rows: list[dict[str, Any]],
    *,
    ability_level_key: str = "ability_level",
    score_rate_key: str = "score_rate",
    weak_threshold: float = 60.0,
) -> dict[str, Any]:
    """知识点掌握度汇总 + 按能力层级聚合。"""
    by_level: dict[str, list[float]] = {}
    items: list[dict[str, Any]] = []
    for row in knowledge_rows:
        rate = _num(row.get(score_rate_key))
        level = str(row.get(ability_level_key) or "unknown")
        if rate is not None:
            by_level.setdefault(level, []).append(rate)
            items.append({
                **dict(row),
                "mastery_rate": rate,
                "weak": rate < weak_threshold,
            })
    level_summary = []
    for level, rates in sorted(by_level.items()):
        avg = round(sum(rates) / len(rates), 2) if rates else None
        level_summary.append({
            "ability_level": level,
            "count": len(rates),
            "avg_score_rate": avg,
            "weak": avg is not None and avg < weak_threshold,
        })
    return {"items": items, "by_ability_level": level_summary}


__all__ = [
    "compute_correlations",
    "compute_imbalance_degree",
    "compute_item_metrics",
    "compute_knowledge_mastery",
    "compute_level_distribution",
    "compute_rankings",
    "compute_score_stats",
    "compute_subject_extremes",
    "compute_top_progress_regress",
    "compute_trend_distribution",
    "identify_at_risk_students",
    "normalize_segments",
    "pearson_r",
]
