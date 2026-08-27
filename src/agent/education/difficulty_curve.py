"""难度曲线：按该科卷面总分十分段，算全卷/小题得分率。

缺考（科目分 <= 0）不入段。分段不用六门 zf6m。
数字一律纯函数；小题明细禁止拉全量到 Python，由调用方 SQL 聚合后再 shape。
"""

from __future__ import annotations

import html
import re
from collections import defaultdict
from typing import Any

from src.agent.education.score_band import SUBJECTS, band_lo

__all__ = [
    "BAND_WIDTH",
    "SUBJECT_FULL_SCORE",
    "assemble_curve_template_data",
    "build_curve_report_data",
    "build_item_charts_html",
    "build_paper_curve",
    "curve_axis_label",
    "curve_insight",
    "extract_question_target",
    "filter_curve_students",
    "is_difficulty_curve_query",
    "item_band_sql",
    "item_catalog_sql",
    "item_not_found_message",
    "match_question_nos",
    "paper_anomaly_items",
    "render_curve_tables",
    "resolve_item_question_nos",
    "shape_item_curves",
    "subject_column",
    "subject_full_score",
]

BAND_WIDTH = 10
SUBJECT_FULL_SCORE: dict[str, float] = {
    "语文": 150.0,
    "数学": 150.0,
    "英语": 150.0,
    "物理": 100.0,
    "化学": 100.0,
    "生物": 100.0,
    "历史": 100.0,
    "政治": 100.0,
    "地理": 100.0,
}

_SUBJECT_COL = {name: key for key, name in SUBJECTS}
_SUBJECT_COL["化学"] = "hxzh"
_SUBJECT_COL["生物"] = "swzh"
_SUBJECT_COL["政治"] = "zzzh"
_SUBJECT_COL["地理"] = "dlzh"

_SUB_Q_RE = re.compile(r"(?:第)?(\d+)\s*题(?:第)?(\d+)\s*(?:问|小题)")
_TYPED_RE = re.compile(r"(单选|多选|填空|解答)(?:题)?(?:第)?(\d+)")
_UNDER_RE = re.compile(r"(?<![\d])(\d+_\d+)\b")
_DI_TI_RE = re.compile(r"第\s*(\d+)\s*题")
_TIMU_RE = re.compile(r"题目\s*(\d+)")
_TI_RE = re.compile(r"(?<![_\d第])(\d+)\s*题")
_XIAOTI_RE = re.compile(r"小题\s*(\d+)")
_TYPED_PREFIXES = ("单选", "多选", "填空", "解答")

_CURVE_HINTS = ("难度曲线", "难度分析", "得分率曲线")
_QUALITY_HINTS = ("试题质量",)
_PAPER_RATE_HINTS = ("试卷得分率", "试题得分率")


def is_difficulty_curve_query(question: str) -> bool:
    q = (question or "").strip()
    if not q:
        return False
    if "知识点" in q:
        return False
    if any(h in q for h in _CURVE_HINTS):
        return True
    if any(h in q for h in _PAPER_RATE_HINTS):
        return True
    if any(h in q for h in _QUALITY_HINTS) and ("难度" in q or "曲线" in q):
        return True
    return False


def extract_question_target(question: str) -> str | None:
    """口语题号 → overview/detail 的 question_no 线索。"""
    q = (question or "").strip()
    if not q:
        return None
    m = _SUB_Q_RE.search(q)
    if m:
        return f"{m.group(1)}_{m.group(2)}"
    m = _TYPED_RE.search(q)
    if m:
        return f"{m.group(1)}{m.group(2)}"
    m = _UNDER_RE.search(q)
    if m:
        return m.group(1)
    m = _DI_TI_RE.search(q)
    if m:
        return m.group(1)
    m = _TIMU_RE.search(q)
    if m:
        return m.group(1)
    m = _XIAOTI_RE.search(q)
    if m:
        return m.group(1)
    m = _TI_RE.search(q)
    if m:
        return m.group(1)
    return None


def match_question_nos(clue: str, catalog: list[str]) -> list[str]:
    """把口语线索对齐到卷面真实题号。

    数字线索（题目1/第1题）优先对齐「单选1」等带题型前缀的卷面号；
    与裸号「1」并存时也走选择题号，避免把合计行/错号当成第1题。
    否则精确相等，再退到同主号子题。
    """
    c = str(clue or "").strip()
    names = [str(x).strip() for x in catalog if str(x).strip()]
    if not c or not names:
        return []
    if c.isdigit():
        for prefix in _TYPED_PREFIXES:
            typed = f"{prefix}{c}"
            if typed in names:
                return [typed]
    if c in names:
        return [c]
    prefix = c + "_"
    subs = sorted((n for n in names if n.startswith(prefix)), key=_qno_sort_key)
    if subs:
        return subs
    return []


def _qno_sort_key(qno: str) -> tuple[Any, ...]:
    parts = re.split(r"(_)", qno)
    out: list[Any] = []
    for p in parts:
        if p.isdigit():
            out.append((0, int(p)))
        else:
            out.append((1, p))
    return tuple(out)


def subject_column(subject: str) -> str | None:
    name = (subject or "").strip()
    return _SUBJECT_COL.get(name)


def curve_axis_label(lo: int, width: int = BAND_WIDTH) -> str:
    """教研难度曲线横轴用段上限：51–60 → 60。"""
    return str(int(lo) + int(width) - 1)


def subject_full_score(subject: str, fallback: float | None = None) -> float:
    name = (subject or "").strip()
    if fallback is not None and fallback > 0:
        return float(fallback)
    return float(SUBJECT_FULL_SCORE.get(name) or 100.0)


def filter_curve_students(
    students: list[dict[str, Any]],
    *,
    subject_col: str,
    school: str = "",
    class_name: str = "",
    enrolled_only: bool = True,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in students or []:
        if enrolled_only and "市报" in str(row.get("xsxz") or ""):
            continue
        if enrolled_only and str(row.get("xsxz") or "") not in ("", "在籍生"):
            if "在籍" not in str(row.get("xsxz") or ""):
                continue
        if school and school not in str(row.get("xx") or ""):
            continue
        if class_name and class_name not in str(row.get("bj") or ""):
            continue
        score = _num(row.get(subject_col))
        if score is None or score <= 0:
            continue
        out.append(row)
    return out


def build_paper_curve(
    students: list[dict[str, Any]],
    *,
    subject_col: str,
    full_score: float,
    width: int = BAND_WIDTH,
    enrolled_only: bool = True,
) -> list[dict[str, Any]]:
    """全卷曲线：段内 AVG(科目分/卷面满分)*100。"""
    rows = filter_curve_students(
        students, subject_col=subject_col, enrolled_only=enrolled_only
    )
    full = float(full_score or 0) or 1.0
    buckets: dict[int, list[float]] = defaultdict(list)
    for row in rows:
        score = _num(row.get(subject_col))
        if score is None or score <= 0:
            continue
        lo = band_lo(score, width)
        buckets[lo].append(score / full * 100.0)
    return _bucket_curve(buckets, width)


def shape_item_curves(
    rows: list[dict[str, Any]],
    *,
    width: int = BAND_WIDTH,
) -> dict[str, list[dict[str, Any]]]:
    by_q: dict[str, dict[int, list[tuple[float, int]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for row in rows or []:
        qno = str(row.get("question_no") or "").strip()
        if not qno:
            continue
        lo = int(row.get("band_lo") or 0)
        if lo <= 0:
            continue
        rate = _num(row.get("score_rate"))
        n = int(row.get("n") or 0)
        if rate is None:
            continue
        by_q[qno][lo].append((rate, max(n, 0)))
    out: dict[str, list[dict[str, Any]]] = {}
    for qno, bands in by_q.items():
        merged: dict[int, list[float]] = {}
        ns: dict[int, int] = {}
        for lo, pairs in bands.items():
            merged[lo] = [p[0] for p in pairs]
            ns[lo] = sum(p[1] for p in pairs)
        curve = _bucket_curve(merged, width, counts=ns)
        out[qno] = curve
    return dict(sorted(out.items(), key=lambda kv: _qno_sort_key(kv[0])))


def _bucket_curve(
    buckets: dict[int, list[float]],
    width: int,
    counts: dict[int, int] | None = None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for lo in sorted(buckets):
        vals = buckets[lo]
        n = counts.get(lo) if counts else len(vals)
        if not vals or not n:
            continue
        out.append(
            {
                "band_lo": lo,
                "band_label": curve_axis_label(lo, width),
                "n": int(n),
                "score_rate": round(sum(vals) / len(vals), 2),
            }
        )
    return out


def curve_insight(curve: list[dict[str, Any]]) -> str:
    points = [p for p in (curve or []) if p.get("n")]
    if len(points) < 2:
        if len(points) == 1:
            p = points[0]
            return f"{p['band_label']} 得分率 {p['score_rate']}%，分段不足，无法判断升降。"
        return "有效分段不足，无法绘制难度曲线。"
    low, high = points[0], points[-1]
    delta = round(float(high["score_rate"]) - float(low["score_rate"]), 2)
    if delta > 2:
        trend = "整体呈上升趋势：低分段试题偏难，高分段更为容易"
    elif delta < -2:
        trend = "整体下降或倒挂：高分段得分率未高于低分段，需核查超纲或区分度"
    else:
        trend = "高低分段得分率接近，区分有限"
    return (
        f"{trend}。低段 {low['band_label']} 为 {low['score_rate']}%（{low['n']}人），"
        f"高段 {high['band_label']} 为 {high['score_rate']}%（{high['n']}人），"
        f"差 {delta} 个百分点。"
    )


def paper_item_summaries(
    item_curves: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """全部小题的高低段得分率，按题号排序。"""
    ranked: list[dict[str, Any]] = []
    for qno, curve in (item_curves or {}).items():
        pts = [p for p in curve if p.get("n")]
        if not pts:
            continue
        low, high = pts[0], pts[-1]
        delta = (
            round(float(high["score_rate"]) - float(low["score_rate"]), 2)
            if len(pts) >= 2
            else 0.0
        )
        ranked.append(
            {
                "question_no": qno,
                "low_rate": low["score_rate"],
                "high_rate": high["score_rate"],
                "delta": delta,
                "n_bands": len(pts),
            }
        )
    ranked.sort(key=lambda r: _qno_sort_key(str(r["question_no"])))
    return ranked


def paper_anomaly_items(
    item_curves: dict[str, list[dict[str, Any]]],
    *,
    top_n: int = 8,
) -> list[dict[str, Any]]:
    ranked = [r for r in paper_item_summaries(item_curves) if r["n_bands"] >= 2]
    ranked.sort(key=lambda r: (r["delta"], r["question_no"]))
    return ranked[: max(0, top_n)]


def item_not_found_message(clue: str, catalog: list[str]) -> str:
    samples = "、".join(str(x) for x in catalog[:12]) or "（本卷暂无小题号）"
    return f"本场未找到题号「{clue}」。卷面题号示例：{samples}。"


def resolve_item_question_nos(clue: str, catalog: list[str]) -> tuple[list[str], str]:
    matched = match_question_nos(clue, catalog)
    if matched:
        return matched, ""
    return [], item_not_found_message(clue, catalog)


def sub_split_note(clue: str, matched: list[str]) -> str:
    c = str(clue or "").strip()
    names = [str(x) for x in (matched or [])]
    if c and names and c not in names and all(n.startswith(c + "_") for n in names):
        return f"第{c}题拆为 {len(names)} 问（{'、'.join(names)}）。"
    return ""


def build_curve_report_data(
    *,
    exam_name: str,
    subject_name: str,
    students: list[dict[str, Any]],
    item_rows: list[dict[str, Any]],
    catalog: list[str],
    question_clue: str = "",
    school: str = "",
    class_name: str = "",
    full_score: float | None = None,
    report_time: str = "",
) -> dict[str, Any]:
    """组装难度曲线模板 data。单题找不到题号时只返回 error，不含全卷曲线。"""
    col = subject_column(subject_name)
    if not col:
        return {
            "error": "missing_subject",
            "message": "请点名科目，例如「3月数学难度曲线」。未点名科目不默认语文。",
        }
    clue = str(question_clue or "").strip()
    matched: list[str] = []
    if clue:
        matched, err = resolve_item_question_nos(clue, catalog)
        if err:
            return {"error": "item_not_found", "message": err, "catalog": list(catalog or [])}
        wanted = set(matched)
        item_rows = [
            r for r in (item_rows or []) if str(r.get("question_no") or "").strip() in wanted
        ]
    item_curves = shape_item_curves(item_rows)
    if clue and not item_curves:
        return {
            "error": "item_not_found",
            "message": item_not_found_message(clue, catalog),
            "catalog": list(catalog or []),
        }
    paper_curve: list[dict[str, Any]] = []
    if not clue:
        paper_curve = build_paper_curve(
            filter_curve_students(
                students,
                subject_col=col,
                school=school,
                class_name=class_name,
                enrolled_only=False,
            ),
            subject_col=col,
            full_score=subject_full_score(subject_name, full_score),
            enrolled_only=False,
        )
    return assemble_curve_template_data(
        exam_name=exam_name,
        subject_name=subject_name,
        paper_curve=paper_curve,
        item_curves=item_curves,
        question_clue=clue,
        matched=matched,
        school=school,
        class_name=class_name,
        report_time=report_time,
    )


def assemble_curve_template_data(
    *,
    exam_name: str,
    subject_name: str,
    paper_curve: list[dict[str, Any]],
    item_curves: dict[str, list[dict[str, Any]]],
    question_clue: str = "",
    matched: list[str] | None = None,
    school: str = "",
    class_name: str = "",
    report_time: str = "",
) -> dict[str, Any]:
    item_only = bool(question_clue)
    primary, secondary = render_curve_tables(
        paper_curve=paper_curve, item_curves=item_curves, item_only=item_only
    )
    q_title = "、".join(item_curves) if item_only else "全卷"
    title = f"{exam_name}{subject_name}难度曲线（{q_title}）".strip()
    scope_bits = [p for p in (exam_name, subject_name, school, class_name) if p]
    scope = " / ".join(scope_bits) or "全市全体"
    main_curve = next(iter(item_curves.values()), []) if item_only else paper_curve
    insight = curve_insight(main_curve)
    note = sub_split_note(question_clue, matched or list(item_curves))
    if note:
        insight = note + insight
    chart_json = build_curve_chart_json(
        title=title,
        paper_curve=paper_curve,
        item_curves=item_curves,
        item_only=item_only,
    )
    item_charts = ""
    if not item_only:
        item_charts = build_item_charts_html(
            item_curves, subject_name=subject_name
        )
    n_sum = sum(int(p.get("n") or 0) for p in (main_curve or []))
    return {
        "REPORT_TITLE": title,
        "REPORT_SUBTITLE": scope,
        "REPORT_TIME": report_time or "",
        "REPORT_TYPE": "难度曲线",
        "EXAM_NAME": exam_name,
        "SUBJECT_NAME": subject_name,
        "SCOPE": scope,
        "CURVE_CHART": chart_json or "{}",
        "PRIMARY_TABLE": primary,
        "SECONDARY_TABLE": secondary,
        "ITEM_CHARTS_HTML": item_charts,
        "GENERAL_INSIGHT": insight,
        "SUMMARY": insight,
        "RECOMMENDATIONS": "",
        "ITEM_ONLY": item_only,
        "_stats": {
            "count": n_sum,
            "subject": subject_name,
            "item_count": len(item_curves or {}),
        },
        "_charts": {"difficulty_curve": chart_json or "{}"},
    }


def curve_union_labels(
    paper_curve: list[dict[str, Any]],
    item_curves: dict[str, list[dict[str, Any]]],
) -> list[str]:
    labels: list[str] = []
    seen: set[str] = set()
    for src in (paper_curve, *(item_curves or {}).values()):
        for p in src or []:
            lab = str(p.get("band_label") or "")
            if lab and lab not in seen:
                seen.add(lab)
                labels.append(lab)
    return labels


def series_values_for_labels(
    curve: list[dict[str, Any]], labels: list[str]
) -> list[float | None]:
    by_lab = {str(p.get("band_label")): p.get("score_rate") for p in (curve or [])}
    return [by_lab.get(lab) for lab in labels]


def build_curve_chart_json(
    *,
    title: str,
    paper_curve: list[dict[str, Any]],
    item_curves: dict[str, list[dict[str, Any]]],
    item_only: bool,
) -> str:
    from src.agent.education.charts import build_chart_option

    labels = curve_union_labels([] if item_only else paper_curve, item_curves)
    series: list[dict[str, Any]] = []
    if not item_only and paper_curve:
        series.append({"name": "全卷", "values": series_values_for_labels(paper_curve, labels)})
    if item_only:
        for qno, curve in (item_curves or {}).items():
            series.append({"name": qno, "values": series_values_for_labels(curve, labels)})
    return build_chart_option(
        "difficulty_curve",
        {"x_labels": labels, "series": series},
        title,
    )


def build_item_charts_html(
    item_curves: dict[str, list[dict[str, Any]]],
    *,
    subject_name: str,
) -> str:
    """整卷报告：每小题一张独立折线（Y=得分率 0–100）。"""
    if not item_curves:
        return ""
    parts: list[str] = []
    for i, (qno, curve) in enumerate(item_curves.items()):
        title = f"{subject_name}{qno}难度曲线"
        chart = build_curve_chart_json(
            title=title,
            paper_curve=[],
            item_curves={qno: curve},
            item_only=True,
        )
        cid = f"itemCurve{i}"
        insight = html.escape(curve_insight(curve))
        q_esc = html.escape(str(qno))
        parts.append(
            "<div class='item-curve-block'>"
            f"<h3>{q_esc}</h3>"
            f"<div id='{cid}' class='edu-chart item-curve-host'></div>"
            f"<script type='application/json' class='item-curve-data' "
            f"id='{cid}Data' data-target='{cid}'>{chart}</script>"
            f"<p class='edu-sub'>{insight}</p>"
            f"{_one_item_band_table(curve)}"
            "</div>"
        )
    return "".join(parts)


def sql_lit(value: str) -> str:
    return (value or "").replace("'", "''")


def item_catalog_sql(exam_name: str, subject_name: str) -> str:
    exam = sql_lit(exam_name)
    subj = sql_lit(subject_name)
    return (
        "SELECT DISTINCT sd.question_no AS question_no "
        "FROM tb_score_detail sd "
        "JOIN tb_score sc ON sc.exam_id = sd.exam_id AND sc.student_id = sd.student_id "
        "JOIN tb_exam e ON sc.exam_id = e.id "
        "LEFT JOIN tb_exam_batch eb ON e.exam_batch_id = eb.id "
        f"WHERE sc.subject_name = '{subj}' "
        f"AND (eb.batch_name = '{exam}' OR e.exam_name LIKE '%{exam}%') "
        "AND sd.question_no IS NOT NULL AND sd.question_no <> '' "
        "ORDER BY sd.question_no LIMIT 200"
    )


def item_band_sql(
    exam_name: str,
    subject_name: str,
    subject_col: str,
    question_nos: list[str] | None = None,
    *,
    school: str = "",
    class_name: str = "",
) -> str:
    exam = sql_lit(exam_name)
    subj = sql_lit(subject_name)
    col = re.sub(r"[^a-z0-9_]", "", (subject_col or "").lower()) or "sx"
    q_filter = ""
    if question_nos:
        ins = ", ".join("'" + sql_lit(q) + "'" for q in question_nos)
        q_filter = f" AND eq.question_no IN ({ins})"
    school_f = f" AND ov.xx LIKE '%{sql_lit(school)}%'" if school else ""
    class_f = f" AND ov.bj LIKE '%{sql_lit(class_name)}%'" if class_name else ""
    # 小题分导入会跳过空单元格（未写 0 分）。INNER JOIN 明细会丢掉答错的人，
    # 选择题看起来整段 100%。以应考学生为底，缺行当 0 分。
    return (
        "SELECT ((CAST(ov." + col + " AS int) - 1) / 10) * 10 + 1 AS band_lo, "
        "eq.question_no AS question_no, "
        "ROUND(AVG(COALESCE(sd.score, 0)::numeric) * 100 "
        "/ NULLIF(MAX(eq.question_score), 0), 2) AS score_rate, "
        "COUNT(*) AS n "
        "FROM tb_score_overview ov "
        "JOIN tb_score sc ON sc.student_id = ov.anon_stu_id "
        f"AND sc.subject_name = '{subj}' "
        "JOIN tb_exam e ON sc.exam_id = e.id "
        "LEFT JOIN tb_exam_batch eb ON e.exam_batch_id = eb.id "
        "JOIN tb_exam_question eq ON eq.exam_id = sc.exam_id "
        "LEFT JOIN tb_score_detail sd ON sd.exam_id = sc.exam_id "
        "AND sd.student_id = ov.anon_stu_id "
        "AND sd.question_no = eq.question_no "
        f"WHERE ov.exam_name = '{exam}' "
        f"AND (eb.batch_name = '{exam}' "
        f"OR (eb.batch_name IS NULL AND e.exam_name LIKE '%{exam}%')) "
        f"AND ov.{col} > 0 "
        f"{school_f}{class_f}{q_filter} "
        "GROUP BY 1, eq.question_no "
        "ORDER BY 1, eq.question_no LIMIT 2000"
    )


def exam_full_score_sql(exam_name: str, subject_name: str) -> str:
    exam = sql_lit(exam_name)
    subj = sql_lit(subject_name)
    return (
        "SELECT MAX(e.exam_score) AS exam_score FROM tb_exam e "
        "LEFT JOIN tb_exam_batch eb ON e.exam_batch_id = eb.id "
        f"WHERE e.subject = '{subj}' "
        f"AND (eb.batch_name = '{exam}' OR e.exam_name LIKE '%{exam}%') "
        "LIMIT 1"
    )


def render_curve_tables(
    *,
    paper_curve: list[dict[str, Any]],
    item_curves: dict[str, list[dict[str, Any]]],
    item_only: bool,
) -> tuple[str, str]:
    """返回 (主表 HTML, 副表 HTML)。"""
    if item_only:
        primary = _item_table(item_curves)
        return primary, ""
    primary = _paper_table(paper_curve)
    summaries = paper_item_summaries(item_curves)
    if not summaries:
        secondary = "<p class='edu-sub'>未导入小题分，或小题分段不足，仅展示全卷曲线。</p>"
        return primary, secondary
    rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(r['question_no']))}</td>"
        f"<td class='num'>{r['low_rate']}</td>"
        f"<td class='num'>{r['high_rate']}</td>"
        f"<td class='num'>{r['delta']}</td>"
        "</tr>"
        for r in summaries
    )
    secondary = (
        "<table class='edu-table'><thead><tr>"
        "<th>题号</th><th class='num'>"
        "低段得分率%</th><th class='num'>高段得分率%</th>"
        "<th class='num'>升幅</th></tr></thead><tbody>"
        f"{rows}</tbody></table>"
        "<p class='edu-sub'>下图按题号给出每小题难度曲线；升幅越小（含倒挂）越值得关注。</p>"
    )
    return primary, secondary


def _paper_table(curve: list[dict[str, Any]]) -> str:
    if not curve:
        return "<p class='edu-sub'>暂无全卷分段数据。</p>"
    body = "".join(
        "<tr>"
        f"<td>{html.escape(str(p['band_label']))}</td>"
        f"<td class='num'>{p['n']}</td>"
        f"<td class='num'>{p['score_rate']}</td>"
        "</tr>"
        for p in curve
    )
    return (
        "<table class='edu-table'><thead><tr>"
        "<th>分数段</th><th class='num'>人数</th><th class='num'>全卷得分率%</th>"
        "</tr></thead><tbody>"
        f"{body}</tbody></table>"
    )


def _one_item_band_table(curve: list[dict[str, Any]]) -> str:
    if not curve:
        return "<p class='edu-sub'>暂无该题分段数据。</p>"
    body = "".join(
        "<tr>"
        f"<td>{html.escape(str(p['band_label']))}</td>"
        f"<td class='num'>{p['n']}</td>"
        f"<td class='num'>{p['score_rate']}</td>"
        "</tr>"
        for p in curve
    )
    return (
        "<table class='edu-table'><thead><tr>"
        "<th>分数段</th><th class='num'>人数</th><th class='num'>小题得分率%</th>"
        "</tr></thead><tbody>"
        f"{body}</tbody></table>"
    )


def _item_table(item_curves: dict[str, list[dict[str, Any]]]) -> str:
    if not item_curves:
        return "<p class='edu-sub'>暂无该题分段数据。</p>"
    parts: list[str] = []
    for qno, curve in item_curves.items():
        parts.append(f"<h3>{html.escape(qno)}</h3>{_one_item_band_table(curve)}")
    return "".join(parts)


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
