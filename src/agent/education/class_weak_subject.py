"""指定班级薄弱学科：同校同年级同科班际对比（纯函数，无 I/O）。"""

from __future__ import annotations

import html
import math
import re
from collections import defaultdict
from typing import Any

from src.agent.education.config import EducationConfig
from src.agent.education.dimension_parse import parse_grade_from_class
from src.agent.education.stats import compute_score_stats

WEAK_RANK_RATIO = 0.30
WEAK_AVG_GAP = 5.0
LAST_N_MIN_CLASSES = 10
LAST_N = 3
DRILL_TOP_N = 2
MIN_CLASS_N = 3
ITEM_LAG_PP = -8.0
COMMON_HARD_RATE = 60.0
MAX_LAGGING_ITEMS = 15
MAX_COMMON_ITEMS = 8

__all__ = [
    "DRILL_TOP_N",
    "build_class_weak_subject_report_data",
    "build_recommendations_html",
    "compare_class_items_vs_peers",
    "compare_class_subjects_vs_peers",
    "identify_weak_subjects",
    "missing_required_slots",
    "pick_drill_subjects",
    "pick_exam_batch_name",
    "render_class_weak_subject_html",
    "school_query_candidates",
]


def _num(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _class_of(row: dict[str, Any]) -> str:
    return str(row.get("class_name") or row.get("class") or "").strip()


def _subject_of(row: dict[str, Any]) -> str:
    return str(row.get("subject") or row.get("subject_name") or "").strip()


def _dense_rank_by_avg(class_avgs: dict[str, float]) -> dict[str, int]:
    ordered = sorted(class_avgs.items(), key=lambda kv: (-kv[1], kv[0]))
    ranks: dict[str, int] = {}
    rank = 0
    prev: float | None = None
    for name, avg in ordered:
        if prev is None or avg < prev:
            rank += 1
        ranks[name] = rank
        prev = avg
    return ranks


def compare_class_subjects_vs_peers(
    score_rows: list[dict[str, Any]],
    *,
    class_name: str,
    min_class_n: int = MIN_CLASS_N,
) -> list[dict[str, Any]]:
    target = str(class_name or "").strip()
    grade = parse_grade_from_class(target) or ""
    by: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for r in score_rows:
        cls = _class_of(r)
        sub = _subject_of(r)
        sc = _num(r.get("score"))
        if not cls or not sub or sc is None:
            continue
        if grade and parse_grade_from_class(cls) != grade:
            continue
        by[sub][cls].append(sc)

    cfg = EducationConfig()
    out: list[dict[str, Any]] = []
    for sub, classes in by.items():
        usable = {c: scores for c, scores in classes.items() if len(scores) >= min_class_n}
        if target not in usable:
            continue
        avgs = {c: sum(s) / len(s) for c, s in usable.items()}
        peer_avg = sum(avgs.values()) / len(avgs)
        ranks = _dense_rank_by_avg(avgs)
        tgt_scores = usable[target]
        tgt_stats = compute_score_stats(tgt_scores, cfg)
        peer_pass: list[float] = []
        peer_exc: list[float] = []
        for scores in usable.values():
            st = compute_score_stats(scores, cfg)
            if st.get("pass_rate") is not None:
                peer_pass.append(float(st["pass_rate"]))
            if st.get("excellent_rate") is not None:
                peer_exc.append(float(st["excellent_rate"]))
        class_avg = avgs[target]
        pass_rate = float(tgt_stats.get("pass_rate") or 0)
        exc_rate = float(tgt_stats.get("excellent_rate") or 0)
        p_pass = sum(peer_pass) / len(peer_pass) if peer_pass else None
        p_exc = sum(peer_exc) / len(peer_exc) if peer_exc else None
        out.append({
            "subject": sub,
            "class_avg": round(class_avg, 2),
            "peer_avg": round(peer_avg, 2),
            "avg_gap": round(peer_avg - class_avg, 2),
            "rank": ranks[target],
            "total_classes": len(usable),
            "pass_rate": round(pass_rate, 2),
            "peer_pass_rate": round(p_pass, 2) if p_pass is not None else None,
            "pass_gap": round((p_pass - pass_rate), 2) if p_pass is not None else None,
            "excellent_rate": round(exc_rate, 2),
            "peer_excellent_rate": round(p_exc, 2) if p_exc is not None else None,
            "n": len(tgt_scores),
        })
    out.sort(key=lambda x: str(x["subject"]))
    return out


def _is_weak(row: dict[str, Any]) -> tuple[bool, list[str]]:
    n = int(row.get("total_classes") or 0)
    rank = int(row.get("rank") or 0)
    gap = float(row.get("avg_gap") or 0)
    reasons: list[str] = []
    if n <= 0 or rank <= 0:
        return False, []
    cutoff = math.ceil(n * (1 - WEAK_RANK_RATIO))
    if rank > cutoff:
        reasons.append(f"名次第 {rank}/{n}（后 {int(WEAK_RANK_RATIO * 100)}%）")
    if gap >= WEAK_AVG_GAP:
        reasons.append(f"均分低于对照 {gap:.1f} 分")
    if n >= LAST_N_MIN_CLASSES and rank > n - LAST_N:
        tag = f"名次第 {rank}/{n}（后 {LAST_N} 名）"
        if tag not in reasons and not any("后" in r and "名" in r for r in reasons):
            reasons.append(tag)
    return bool(reasons), reasons


def identify_weak_subjects(comparisons: list[dict[str, Any]]) -> list[dict[str, Any]]:
    weak: list[dict[str, Any]] = []
    for row in comparisons:
        hit, reasons = _is_weak(row)
        item = dict(row)
        item["is_weak"] = hit
        item["reasons"] = reasons
        if hit:
            weak.append(item)
    weak.sort(key=lambda x: float(x.get("avg_gap") or 0), reverse=True)
    return weak


def pick_drill_subjects(
    weak: list[dict[str, Any]],
    top_n: int = DRILL_TOP_N,
) -> list[str]:
    return [str(w.get("subject") or "") for w in weak[:top_n] if w.get("subject")]


def pick_exam_batch_name(
    score_rows: list[dict[str, Any]],
    *,
    class_name: str,
    question: str = "",
    exam_hint: str = "",
) -> str | None:
    """多场考试时按本班是否参考 + 问句届次/年级/月份选批次名，不按人数最多。"""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in score_rows or []:
        if not isinstance(r, dict):
            continue
        name = str(r.get("exam_name") or "").strip()
        if name:
            groups[name].append(r)
    if not groups:
        return None
    if len(groups) == 1:
        return next(iter(groups))

    target = str(class_name or "").strip()
    grade = parse_grade_from_class(target) or ""
    q = f"{question or ''} {exam_hint or ''}"
    year_m = re.search(r"(\d{4}届)", q)
    year = year_m.group(1) if year_m else ""
    month_m = re.search(r"(\d{1,2}月)", q)
    month = month_m.group(1) if month_m else ""

    def _score(name: str, rows: list[dict[str, Any]]) -> int:
        s = 0
        if target and any(_class_of(r) == target for r in rows):
            s += 20
        if grade and grade in name:
            s += 10
        if year and year in name:
            s += 10
        if month and re.search(rf"(?<!\d){re.escape(month)}", name):
            s += 8
        for tok in ("期末", "期中", "月考", "模拟", "摸底"):
            if tok in q and tok in name:
                s += 3
                break
        return s

    ranked = sorted(
        groups.items(),
        key=lambda kv: (-_score(kv[0], kv[1]), -len(kv[1]), kv[0]),
    )
    return ranked[0][0]


_SCHOOL_CODE_RE = re.compile(r"^[A-Za-z]\d{2}")


_SCHOOL_NAME_TAILS = ("中学", "学校", "学院", "大学", "附中", "分校")


def school_query_candidates(school_name: str) -> list[str]:
    """查询用校名：原名优先；去掉口语「学校」前缀；带 B11 等校码时再追加去码名。"""
    raw = str(school_name or "").strip()
    if not raw:
        return []
    seeds = [raw]
    if raw.startswith("学校") and len(raw) > 2:
        rest = raw[2:].strip()
        if rest and any(rest.endswith(t) for t in _SCHOOL_NAME_TAILS):
            seeds.append(rest)
    out: list[str] = []
    for seed in seeds:
        if seed and seed not in out:
            out.append(seed)
        if _SCHOOL_CODE_RE.match(seed) and len(seed) > 3:
            stripped = seed[3:].strip()
            if stripped and stripped not in out:
                out.append(stripped)
    return out


def missing_required_slots(
    school_name: str = "",
    class_name: str = "",
    exam_name: str = "",
) -> str | None:
    """缺槽位时返回可读错误；齐全返回 None。"""
    if not str(class_name or "").strip():
        return "需要 class_name（如高三(1)班）。"
    if not str(exam_name or "").strip():
        return "需要 exam_name。"
    if not str(school_name or "").strip():
        return "需要学校，禁止改查全市。"
    return None


def _qno(row: dict[str, Any]) -> str:
    return str(row.get("question_no") or "").strip()


def compare_class_items_vs_peers(
    item_class_rows: list[dict[str, Any]],
    *,
    class_name: str,
) -> dict[str, list[dict[str, Any]]]:
    target = str(class_name or "").strip()
    grade = parse_grade_from_class(target) or ""
    by_q: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for r in item_class_rows or []:
        cls = _class_of(r)
        qno = _qno(r)
        rate = _num(r.get("score_rate"))
        if not cls or not qno or rate is None:
            continue
        if grade and parse_grade_from_class(cls) != grade:
            continue
        by_q[qno][cls] = r

    lagging: list[dict[str, Any]] = []
    common_hard: list[dict[str, Any]] = []
    for qno, class_map in by_q.items():
        if target not in class_map:
            continue
        peer_rates = [
            float(_num(row.get("score_rate")) or 0)
            for cls, row in class_map.items()
            if cls != target and _num(row.get("score_rate")) is not None
        ]
        if not peer_rates:
            continue
        class_rate = float(_num(class_map[target].get("score_rate")) or 0)
        peer_rate = sum(peer_rates) / len(peer_rates)
        delta = class_rate - peer_rate
        kn = str(class_map[target].get("knowledge_name") or "")
        item = {
            "question_no": qno,
            "knowledge_name": kn,
            "class_rate": round(class_rate, 2),
            "peer_rate": round(peer_rate, 2),
            "delta": round(delta, 2),
        }
        if delta <= ITEM_LAG_PP:
            lagging.append(item)
        elif class_rate < COMMON_HARD_RATE and peer_rate < COMMON_HARD_RATE:
            common_hard.append(item)
    lagging.sort(key=lambda x: float(x["delta"]))
    common_hard.sort(key=lambda x: float(x["class_rate"]))
    return {
        "lagging": lagging[:MAX_LAGGING_ITEMS],
        "common_hard": common_hard[:MAX_COMMON_ITEMS],
    }


def build_recommendations_html(
    weak: list[dict[str, Any]] | None,
    item_by_subject: dict[str, Any] | None,
) -> str:
    weak = weak or []
    item_by_subject = item_by_subject or {}
    if not weak:
        return (
            "<ul><li>该班各科相对本校同年级均无明显薄弱，不必单开加课时。</li></ul>"
        )
    bits: list[str] = []
    for w in weak:
        sub = str(w.get("subject") or "")
        packed = item_by_subject.get(sub) or {}
        lagging = list(packed.get("lagging") or [])
        hard = list(packed.get("common_hard") or [])
        if lagging:
            nos = "、".join(html.escape(str(x.get("question_no") or "")) for x in lagging[:8])
            bits.append(
                f"<li>{html.escape(sub)}本班特差题（{nos}）建议本班讲评并加练，"
                "对照本校其他班得分率查失分点。</li>"
            )
        elif hard:
            nos = "、".join(html.escape(str(x.get("question_no") or "")) for x in hard[:8])
            bits.append(
                f"<li>{html.escape(sub)}第{nos}题全年级都难，跟年级进度讲评即可，"
                "不必单开本班锅。</li>"
            )
        else:
            bits.append(
                f"<li>{html.escape(sub)}相对本校其他班落后，"
                "小题相对本校其他班无明显落后，先盯班际均分与名次。</li>"
            )
    return "<ul>" + "".join(bits) + "</ul>"


def build_class_weak_subject_report_data(
    *,
    school_name: str = "",
    class_name: str = "",
    exam_name: str = "",
    comparisons: list[dict[str, Any]] | None = None,
    weak_subjects: list[dict[str, Any]] | None = None,
    drill_subjects: list[str] | None = None,
    item_by_subject: dict[str, Any] | None = None,
    score_row_count: int | None = None,
) -> dict[str, Any]:
    comparisons = list(comparisons or [])
    weak_subjects = list(weak_subjects or [])
    drill_subjects = [str(s) for s in (drill_subjects or []) if s]
    item_by_subject = dict(item_by_subject or {})
    drill_set = set(drill_subjects)
    weak_names = {str(w.get("subject") or "") for w in weak_subjects}
    rows: list[dict[str, Any]] = []
    for row in comparisons:
        item = dict(row)
        sub = str(item.get("subject") or "")
        is_weak = sub in weak_names or bool(item.get("is_weak"))
        item["is_weak"] = is_weak
        item["drill"] = sub in drill_set
        if is_weak and sub not in drill_set:
            item["weak_label"] = "薄弱（本次未下钻题目）"
        elif is_weak:
            item["weak_label"] = "薄弱"
        else:
            item["weak_label"] = "—"
        rows.append(item)
    no_scores = score_row_count == 0
    no_class_subjects = (not no_scores) and not comparisons
    empty_weak = not weak_subjects
    if no_scores:
        summary = "未查到该校该场成绩，无法判断薄弱学科。"
        recs = "<ul><li>请核对学校、班级、考试名称后重试；禁止改查全市。</li></ul>"
    elif no_class_subjects:
        summary = "该班在本场没有可对比的科目（参考人数需≥3）。"
        recs = "<ul><li>请核对班级是否参考本场考试；禁止改查全市。</li></ul>"
    elif empty_weak:
        summary = "该班各科相对本校同年级均无明显薄弱。"
        recs = build_recommendations_html(weak_subjects, item_by_subject)
    else:
        names = "、".join(str(w.get("subject") or "") for w in weak_subjects)
        summary = f"相对本校同年级同科班际对比，薄弱学科为{names}。"
        recs = build_recommendations_html(weak_subjects, item_by_subject)
    title = f"{school_name} {class_name} · {exam_name} · 薄弱学科".strip()
    title = " ".join(title.split())
    return {
        "title": title,
        "school_name": school_name,
        "class_name": class_name,
        "exam_name": exam_name,
        "comparisons": rows,
        "weak_subjects": weak_subjects,
        "drill_subjects": drill_subjects,
        "item_by_subject": item_by_subject,
        "SUMMARY": summary,
        "RECOMMENDATIONS": recs,
        "empty_weak": empty_weak,
    }


def _fmt_num(v: Any) -> str:
    n = _num(v)
    if n is None:
        return "—"
    return f"{n:.1f}"


def render_class_weak_subject_html(report: dict[str, Any]) -> str:
    report = report or {}
    title = html.escape(str(report.get("title") or "薄弱学科"))
    summary = html.escape(str(report.get("SUMMARY") or ""))
    recs = str(report.get("RECOMMENDATIONS") or "")
    rows = list(report.get("comparisons") or [])
    empty_weak = bool(report.get("empty_weak"))
    body_rows = []
    for r in rows:
        label = str(r.get("weak_label") or "—")
        is_weak = bool(r.get("is_weak")) or label.startswith("薄弱")
        tr_cls = " class='is-weak'" if is_weak else ""
        label_html = (
            f"<span class='edu-badge-weak'>{html.escape(label)}</span>"
            if is_weak
            else html.escape(label)
        )
        body_rows.append(
            f"<tr{tr_cls}>"
            f"<td>{html.escape(str(r.get('subject') or ''))}</td>"
            f"<td class='num'>{_fmt_num(r.get('class_avg'))}</td>"
            f"<td class='num'>{_fmt_num(r.get('peer_avg'))}</td>"
            f"<td class='num'>{_fmt_num(r.get('avg_gap'))}</td>"
            f"<td class='num'>{html.escape(str(r.get('rank') or '—'))}/"
            f"{html.escape(str(r.get('total_classes') or '—'))}</td>"
            f"<td class='num'>{_fmt_num(r.get('pass_gap'))}</td>"
            f"<td>{label_html}</td>"
            "</tr>"
        )
    table = (
        "<table class='edu-table'><thead><tr>"
        "<th>科目</th>"
        "<th class='num'>本班均分</th>"
        "<th class='num'>对照班均</th>"
        "<th class='num'>分差</th>"
        "<th class='num'>名次</th>"
        "<th class='num'>及格率差</th>"
        "<th>判定</th>"
        "</tr></thead><tbody>"
        + "".join(body_rows)
        + "</tbody></table>"
    )
    item_html = ""
    if not empty_weak:
        for sub in report.get("drill_subjects") or []:
            packed = (report.get("item_by_subject") or {}).get(sub) or {}
            lagging = list(packed.get("lagging") or [])
            hard = list(packed.get("common_hard") or [])
            item_html += f"<h3>{html.escape(str(sub))}</h3>"
            if lagging:
                item_html += "<p>本班特差题（相对本校其他班）</p>"
                item_html += _item_table(lagging)
            if hard:
                item_html += "<p>共性难点（全年级都难，不作为薄弱判定）</p>"
                item_html += _item_table(hard)
            if not lagging and not hard:
                item_html += "<p>小题相对本校其他班无明显落后。</p>"
    css = (
        ".cws-report{font-family:sans-serif;max-width:960px;margin:0 auto;line-height:1.6;"
        "--edu-primary:#1677ff;--edu-primary-bg:#e6f4ff;--edu-border:#e8edf3;"
        "--edu-text-lv1:rgba(0,0,0,.88);--edu-text-lv2:rgba(0,0,0,.65)}"
        ".cws-card{border:1px solid var(--edu-border);border-radius:12px;padding:16px;margin:12px 0;background:#fff}"
        ".cws-card h1{margin:0 0 8px;font-size:20px;color:var(--edu-text-lv1)}"
        ".cws-card h2{margin:0 0 12px;font-size:16px;color:var(--edu-text-lv1)}"
        ".cws-card h3{margin:16px 0 8px;font-size:14px;color:var(--edu-text-lv1)}"
        ".edu-table-wrap{overflow-x:auto;margin:8px 0 12px;border:1px solid var(--edu-border);"
        "border-radius:12px;background:#fff}"
        ".edu-table{width:100%;border-collapse:collapse;font-size:13px;min-width:420px}"
        ".edu-table th,.edu-table td{border:none;border-bottom:1px solid var(--edu-border);"
        "padding:11px 14px;text-align:left;vertical-align:middle;color:var(--edu-text-lv1)}"
        ".edu-table thead th{background:linear-gradient(180deg,#f3f8ff 0%,var(--edu-primary-bg) 100%);"
        "color:#3b6fb8;font-weight:650;white-space:nowrap;font-size:12.5px;letter-spacing:.02em}"
        ".edu-table tbody tr:nth-child(even) td{background:#fafcfe}"
        ".edu-table tbody tr:hover td{background:#f0f7ff}"
        ".edu-table tbody tr.is-weak td{background:#fff7e6}"
        ".edu-table tbody tr.is-weak:hover td{background:#fff1d6}"
        ".edu-table tbody tr:last-child td{border-bottom:none}"
        ".edu-table th.num,.edu-table td.num{text-align:right;font-variant-numeric:tabular-nums;"
        "white-space:nowrap}"
        ".edu-badge-weak{display:inline-block;padding:1px 7px;border-radius:999px;font-size:11px;"
        "background:#fff1f0;color:#cf1322;font-weight:650;vertical-align:middle}"
        ".cws-note{color:var(--edu-text-lv2);font-size:13px}"
    )
    return (
        f"<div class='cws-report'><style>{css}</style>"
        f"<div class='cws-card'><h1>{title}</h1>"
        "<p class='cws-note'>口径：同校同年级同一学科、不同班级对比，不是本班各科互比。</p>"
        f"<p>{summary}</p></div>"
        f"<div class='cws-card'><h2>各科班际位置</h2>"
        f"<div class='edu-table-wrap'>{table}</div></div>"
        + (
            f"<div class='cws-card'><h2>小题下钻</h2>{item_html}</div>"
            if item_html
            else ""
        )
        + f"<div class='cws-card'><h2>建议</h2>{recs}</div></div>"
    )


def _item_table(items: list[dict[str, Any]]) -> str:
    body = []
    for it in items:
        body.append(
            "<tr>"
            f"<td>{html.escape(str(it.get('question_no') or ''))}</td>"
            f"<td>{html.escape(str(it.get('knowledge_name') or ''))}</td>"
            f"<td class='num'>{_fmt_num(it.get('class_rate'))}</td>"
            f"<td class='num'>{_fmt_num(it.get('peer_rate'))}</td>"
            f"<td class='num'>{_fmt_num(it.get('delta'))}</td>"
            "</tr>"
        )
    return (
        "<div class='edu-table-wrap'><table class='edu-table'><thead><tr>"
        "<th>题号</th><th>知识点</th>"
        "<th class='num'>本班得分率</th>"
        "<th class='num'>对照班</th>"
        "<th class='num'>差值</th>"
        "</tr></thead><tbody>"
        + "".join(body)
        + "</tbody></table></div>"
    )
