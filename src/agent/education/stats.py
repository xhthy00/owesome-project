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
            未选考/缺考的 0 分不计入任何 KPI。
        config: 阈值配置。
        full_score: 满分；为 None 时用 ``config.default_full_score``。

    Returns:
        dict，所有 rate 字段为百分数（0–100，保留两位小数）。
        空列表返回各字段为 ``None`` 的占位结构，便于模板统一渲染。
    """
    valid: list[float] = []
    for s in scores:
        if s is None:
            continue
        try:
            v = float(s)
        except (TypeError, ValueError):
            continue
        if v > 0:
            valid.append(v)
    upper = full_score if full_score is not None else config.default_full_score
    seg_bounds = config.resolved_segments(upper if full_score is not None else None)

    if not valid:
        return {
            "count": 0,
            "avg": None,
            "median": None,
            "stdev": None,
            "variance": None,
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
        "variance": None if _safe_stdev(valid) is None else round((_safe_stdev(valid) or 0) ** 2, 2),
        "min": min(valid),
        "max": max(valid),
        "pass_rate": _rate(valid, lambda v: v >= pass_thr),
        "excellent_rate": _rate(valid, lambda v: v >= exc_thr),
        "good_rate": _rate(valid, lambda v: v >= good_thr),
        "low_score_rate": _rate(valid, lambda v: v < low_thr),
        "fail_rate": _rate(valid, lambda v: v < pass_thr),
        "full_score": upper,
        "pass_line": round(pass_thr, 4),
        "excellent_line": round(exc_thr, 4),
        "pass_ratio": float(config.pass_ratio),
        "excellent_ratio": float(config.excellent_ratio),
        "segments": segments,
    }


def describe_score_dispersion(
    stdev: float | None,
    *,
    full_score: float | None = None,
    variance: float | None = None,
) -> dict[str, Any]:
    """把标准差/方差翻译成教学可读的离散程度说明。

    分级按「标准差占满分」比例（常见学情经验阈值）：

    - ``<10%``：较集中——多数学生分数靠近均分；
    - ``10%–15%``：适中——有合理分层，班内差异正常；
    - ``15%–20%``：偏大——尖子与学困差距拉大，需分层辅导；
    - ``≥20%``：分化明显——两极分化风险高，优先关注两端。

    方差 = 标准差²，单位是「分²」，数值越大同样表示更分散；解读等级与标准差一致。
    """
    empty = {
        "level": "-",
        "level_class": "",
        "stdev_hint": "样本不足，暂无法评估离散程度",
        "variance": "-",
        "variance_hint": "方差为标准差的平方；样本不足暂无法计算",
        "tip": (
            "标准差/方差反映班内成绩离散程度：数值越大，分数越分散。"
            "参照占比：&lt;10% 较集中 · 10%–15% 适中 · 15%–20% 偏大 · ≥20% 分化明显。"
        ),
    }
    try:
        stdev_f = float(stdev) if stdev is not None else None
    except (TypeError, ValueError):
        stdev_f = None
    if stdev_f is None or stdev_f < 0:
        return empty

    try:
        upper_f = float(full_score) if full_score is not None else None
    except (TypeError, ValueError):
        upper_f = None
    if upper_f is None or upper_f <= 0:
        upper_f = 100.0

    ratio = stdev_f / upper_f
    if ratio < 0.10:
        level, level_class, meaning = "较集中", "ok", "多数学生分数靠近均分，班内差异较小"
    elif ratio < 0.15:
        level, level_class, meaning = "适中", "", "存在合理分层，班内差异属正常范围"
    elif ratio < 0.20:
        level, level_class, meaning = "偏大", "warn", "尖子与学困差距拉大，建议加强分层辅导"
    else:
        level, level_class, meaning = "分化明显", "bad", "两极分化风险较高，优先关注两端学生"

    if variance is None:
        var_f: float | str = round(stdev_f * stdev_f, 2)
    else:
        try:
            var_f = round(float(variance), 2)
        except (TypeError, ValueError):
            var_f = round(stdev_f * stdev_f, 2)

    pct = round(ratio * 100, 1)
    return {
        "level": level,
        "level_class": level_class,
        "stdev_hint": f"{level}（约占满分 {pct}%）· {meaning}",
        "variance": var_f,
        "variance_hint": (
            f"{level} · 方差=标准差²={stdev_f:.2f}²，"
            "同样表示离散程度，数值越大分数越分散"
        ),
        "tip": (
            "标准差/方差反映班内成绩离散程度：数值越大，分数越分散。"
            f"当前标准差约占满分 {pct}%。"
            "参照：&lt;10% 较集中 · 10%–15% 适中 · 15%–20% 偏大 · ≥20% 分化明显。"
        ),
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
        config: 阈值配置（含可选 ``anomaly_rules``；缺省由经典字段推导，
            行为与历史硬编码一致）。

    Returns:
        ``{"critical": [...], "regression": [...], "imbalanced": [...]}``。
        每条含原始字段 + ``reason`` 说明。
    """
    from src.agent.education.config import (
        ANOMALY_CRITICAL,
        ANOMALY_IMBALANCED,
        ANOMALY_REGRESSION,
        COMPARE_PASS_LINE,
        COMPARE_PREV_EXAM,
        COMPARE_SELF_SUBJECTS,
        resolve_anomaly_rules,
    )

    critical: list[dict[str, Any]] = []
    regression: list[dict[str, Any]] = []
    imbalanced: list[dict[str, Any]] = []

    rules = {r.anomaly_type: r for r in resolve_anomaly_rules(config)}
    critical_rule = rules.get(ANOMALY_CRITICAL)
    regression_rule = rules.get(ANOMALY_REGRESSION)
    imbalance_rule = rules.get(ANOMALY_IMBALANCED)

    pass_thr = float(config.pass_threshold)
    margin = float(config.critical_margin)
    reg_thr = float(config.regression_threshold)
    gap_thr = float(getattr(config, "imbalance_score_gap", 20.0))

    critical_enabled = True
    if critical_rule is not None:
        critical_enabled = bool(critical_rule.enabled)
        if critical_rule.fluctuation_value is not None:
            margin = float(critical_rule.fluctuation_value)
        lo_off = (
            float(critical_rule.range_lo_offset)
            if critical_rule.range_lo_offset is not None
            else -margin
        )
        hi_off = (
            float(critical_rule.range_hi_offset)
            if critical_rule.range_hi_offset is not None
            else margin
        )
        target = critical_rule.compare_target or COMPARE_PASS_LINE
        if target == COMPARE_PASS_LINE:
            crit_lo = pass_thr + lo_off
            crit_hi = pass_thr + hi_off
        elif critical_rule.range_lo is not None and critical_rule.range_hi is not None:
            crit_lo = float(critical_rule.range_lo)
            crit_hi = float(critical_rule.range_hi)
        else:
            crit_lo = pass_thr - margin
            crit_hi = pass_thr + margin
    else:
        crit_lo = pass_thr - margin
        crit_hi = pass_thr + margin

    regression_enabled = True
    if regression_rule is not None:
        regression_enabled = bool(regression_rule.enabled) and (
            (regression_rule.compare_target or COMPARE_PREV_EXAM) == COMPARE_PREV_EXAM
        )
        if regression_rule.threshold is not None:
            reg_thr = float(regression_rule.threshold)

    imbalance_enabled = True
    if imbalance_rule is not None:
        imbalance_enabled = bool(imbalance_rule.enabled) and (
            (imbalance_rule.compare_target or COMPARE_SELF_SUBJECTS) == COMPARE_SELF_SUBJECTS
        )
        if imbalance_rule.threshold is not None:
            gap_thr = float(imbalance_rule.threshold)

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
        if critical_enabled and crit_lo <= score_val < crit_hi:
            critical.append(
                {**s, "reason": f"临界生：{score_val} 分处于及格线 ±{margin} 区间"}
            )

        # 大幅退步：与 prev_score 相比降幅超过 |regression_threshold|
        if regression_enabled:
            prev = s.get(prev_score_key)
            if prev is not None:
                try:
                    prev_val = float(prev)
                    delta = score_val - prev_val
                    if delta <= reg_thr:
                        regression.append(
                            {
                                **s,
                                "reason": (
                                    f"大幅退步：{prev_val} → {score_val}（降 {abs(delta)} 分）"
                                ),
                            }
                        )
                except (TypeError, ValueError):
                    pass

    # 偏科生：同一学生各科分差最大值 ≥ gap 且最低科 < 及格线
    if imbalance_enabled:
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
            if high_val - low_val >= gap_thr and low_val < pass_thr:
                imbalanced.append(
                    {
                        "name": name,
                        "low_subject": low_sub,
                        "low_score": low_val,
                        "high_subject": high_sub,
                        "high_score": high_val,
                        "reason": f"偏科：{high_sub} {high_val} vs {low_sub} {low_val}",
                    }
                )

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

    仅将 ``delta > 0`` 计入进步、``delta < 0`` 计入退步；持平不混入 TOP 表，
    避免「进步最快」表里出现班级阶段/零变化行。

    Returns:
        ``{"progress": [...], "regress": [...], "chart_items": [{"name","value","color"}]}``。
        chart_items 已按 |delta| 取两侧极值，进步绿、退步红，可直接喂给
        ``build_chart_option("progress_regress_bar", ...)``。
    """
    scored = [
        d for d in deltas
        if d.get(name_key) is not None and d.get(value_key) is not None
    ]
    progress = sorted(
        [d for d in scored if float(d.get(value_key) or 0) > 0],
        key=lambda x: float(x.get(value_key) or 0),
        reverse=True,
    )[:top_n]
    regress = sorted(
        [d for d in scored if float(d.get(value_key) or 0) < 0],
        key=lambda x: float(x.get(value_key) or 0),
    )[:top_n]
    chart_src = list(progress) + list(regress)
    chart_items = [
        {
            "name": str(d.get(name_key) or ""),
            "value": float(d.get(value_key) or 0),
            "color": "#2ecc71" if float(d.get(value_key) or 0) >= 0 else "#e74c3c",
        }
        for d in chart_src
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
    "describe_score_dispersion",
    "identify_at_risk_students",
    "normalize_segments",
    "pearson_r",
]
