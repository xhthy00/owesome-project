"""班内后十名 vs 中位组：知识点掌握差距（纯函数，无 I/O）。

「中位数」口径：按总分降序排名后，取中位名次 ± median_band 的学生带
（至少 1 人），与最后 bottom_n 名对比各知识点加权得分率。
gap = median_rate − bottom_rate（百分点）。
"""

from __future__ import annotations

import html
from typing import Any

from src.agent.education.charts import build_chart_option

__all__ = [
    "build_knowledge_cohort_report_data",
    "compare_knowledge_by_cohort",
    "render_knowledge_cohort_html",
    "split_score_cohorts",
]

_UNLINKED = "未关联知识点"


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _student_id(row: dict[str, Any]) -> str:
    for key in ("student_id", "student", "sid", "学号"):
        v = row.get(key)
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""


def _score_value(row: dict[str, Any]) -> float | None:
    for key in ("score", "total", "总分"):
        n = _num(row.get(key))
        if n is not None:
            return n
    return None


def split_score_cohorts(
    score_rows: list[dict[str, Any]],
    *,
    bottom_n: int = 10,
    median_band: int = 2,
) -> dict[str, Any]:
    """按总分降序切分后十名与中位带。

    Returns:
        bottom_ids / median_ids（有序 list[str]）、ranked 全员、人数与中位名次说明。
    """
    n_bottom = max(1, int(bottom_n or 10))
    band = max(0, int(median_band if median_band is not None else 2))

    ranked: list[dict[str, Any]] = []
    for r in score_rows or []:
        if not isinstance(r, dict):
            continue
        sid = _student_id(r)
        sc = _score_value(r)
        if not sid or sc is None:
            continue
        ranked.append({**r, "student_id": sid, "score": sc})

    ranked.sort(key=lambda x: (-float(x["score"]), str(x["student_id"])))
    n = len(ranked)
    if n == 0:
        return {
            "bottom_ids": [],
            "median_ids": [],
            "ranked": [],
            "n": 0,
            "median_rank": 0,
            "bottom_n": n_bottom,
            "median_band": band,
        }

    bottom_rows = ranked[-min(n_bottom, n) :]
    # 1-based 中位名次；偶数为偏上的中位（与常见「中位学生」演示口径一致）
    median_rank = (n + 1) // 2
    median_idx = median_rank - 1  # 0-based
    lo = max(0, median_idx - band)
    hi = min(n - 1, median_idx + band)
    median_rows = ranked[lo : hi + 1]

    return {
        "bottom_ids": [str(r["student_id"]) for r in bottom_rows],
        "median_ids": [str(r["student_id"]) for r in median_rows],
        "bottom_rows": bottom_rows,
        "median_rows": median_rows,
        "ranked": ranked,
        "n": n,
        "median_rank": median_rank,
        "bottom_n": n_bottom,
        "median_band": band,
        "median_rank_lo": lo + 1,
        "median_rank_hi": hi + 1,
    }


def compare_knowledge_by_cohort(
    detail_rows: list[dict[str, Any]],
    bottom_ids: list[str] | set[str],
    median_ids: list[str] | set[str],
) -> list[dict[str, Any]]:
    """按知识点聚合两组加权得分率，按 |gap| 降序。

    detail_rows 需含 student_id、knowledge_name，以及 (score, full_score) 或 score_rate。
    """
    bottom_set = {str(x).strip() for x in (bottom_ids or []) if str(x).strip()}
    median_set = {str(x).strip() for x in (median_ids or []) if str(x).strip()}

    def _accum(ids: set[str]) -> dict[str, list[float]]:
        # kn -> [sum_score, sum_full]
        buckets: dict[str, list[float]] = {}
        for r in detail_rows or []:
            if not isinstance(r, dict):
                continue
            sid = _student_id(r)
            if sid not in ids:
                continue
            kn = str(r.get("knowledge_name") or "").strip() or _UNLINKED
            score = _num(r.get("score"))
            full = _num(r.get("full_score") or r.get("question_score"))
            if score is not None and full is not None and full > 0:
                b = buckets.setdefault(kn, [0.0, 0.0])
                b[0] += score
                b[1] += full
                continue
            rate = _num(r.get("score_rate"))
            if rate is not None:
                # 无权重明细时用得分率均值近似：累加 rate 与计数
                b = buckets.setdefault(kn, [0.0, 0.0])
                b[0] += rate
                b[1] += 100.0
        return buckets

    bottom_b = _accum(bottom_set)
    median_b = _accum(median_set)
    names = sorted(set(bottom_b) | set(median_b), key=lambda k: (k == _UNLINKED, k))

    out: list[dict[str, Any]] = []
    for kn in names:
        bs, bf = bottom_b.get(kn, [0.0, 0.0])
        ms, mf = median_b.get(kn, [0.0, 0.0])
        bottom_rate = round(bs / bf * 100, 2) if bf > 0 else None
        median_rate = round(ms / mf * 100, 2) if mf > 0 else None
        gap: float | None = None
        if bottom_rate is not None and median_rate is not None:
            gap = round(median_rate - bottom_rate, 2)
        out.append(
            {
                "knowledge_name": kn,
                "bottom_rate": bottom_rate,
                "median_rate": median_rate,
                "gap": gap,
            }
        )

    out.sort(
        key=lambda r: (
            r.get("gap") is None,
            -abs(float(r["gap"])) if r.get("gap") is not None else 0.0,
            str(r.get("knowledge_name") or ""),
        )
    )
    return out


def _fmt_rate(v: float | None) -> str:
    if v is None:
        return "-"
    return f"{v:.2f}%"


def _fmt_gap(v: float | None) -> str:
    if v is None:
        return "-"
    sign = "+" if v > 0 else ""
    return f"{sign}{v:.2f}pp"


def _rate_bar_html(rate: float | None, *, tone: str) -> str:
    """得分率 + 迷你进度条。"""
    if rate is None:
        return "<span class='kc-muted'>-</span>"
    width = max(0.0, min(100.0, float(rate)))
    return (
        f"<div class='kc-rate kc-rate-{html.escape(tone)}'>"
        f"<span class='kc-rate-num'>{width:.2f}%</span>"
        f"<span class='kc-rate-track'><span class='kc-rate-fill' style='width:{width:.1f}%'></span></span>"
        f"</div>"
    )


def _gap_badge_html(gap: float | None) -> str:
    if gap is None:
        return "<span class='kc-muted'>-</span>"
    cls = "kc-gap-pos" if gap > 0 else ("kc-gap-neg" if gap < 0 else "kc-gap-zero")
    return f"<span class='kc-gap {cls}'>{html.escape(_fmt_gap(gap))}</span>"


def build_knowledge_cohort_table_html(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "<p class='edu-sub'>暂无知识点对比数据</p>"
    parts = [
        "<div class='edu-table-wrap'><table class='edu-table kc-table'><thead><tr>",
        "<th class='kc-col-kn'>知识点</th>",
        "<th class='kc-col-rate'>后十得分率</th>",
        "<th class='kc-col-rate'>中位组得分率</th>",
        "<th class='kc-col-gap'>差距 (中位−后十)</th>",
        "</tr></thead><tbody>",
    ]
    for i, r in enumerate(rows):
        kn = html.escape(str(r.get("knowledge_name") or ""))
        gap = _num(r.get("gap"))
        row_cls = " class='kc-row-top'" if i == 0 else ""
        mark = " <span class='kc-top-tag'>差距最大</span>" if i == 0 else ""
        parts.append(
            f"<tr{row_cls}>"
            f"<td class='kc-col-kn'>{kn}{mark}</td>"
            f"<td class='kc-col-rate'>{_rate_bar_html(_num(r.get('bottom_rate')), tone='bottom')}</td>"
            f"<td class='kc-col-rate'>{_rate_bar_html(_num(r.get('median_rate')), tone='median')}</td>"
            f"<td class='kc-col-gap'>{_gap_badge_html(gap)}</td>"
            "</tr>"
        )
    parts.append("</tbody></table></div>")
    return "".join(parts)


def build_knowledge_cohort_chart_payload(
    rows: list[dict[str, Any]],
    *,
    max_items: int = 12,
) -> dict[str, Any] | None:
    """供 group_compare_bar：各组=知识点，系列=后十/中位组。"""
    usable = [
        r
        for r in (rows or [])
        if _num(r.get("bottom_rate")) is not None or _num(r.get("median_rate")) is not None
    ]
    if not usable:
        return None
    top = usable[: max(1, int(max_items or 12))]
    groups = [str(r.get("knowledge_name") or _UNLINKED) for r in top]
    return {
        "groups": groups,
        "metrics": [
            {
                "name": "后十名",
                "values": [round(_num(r.get("bottom_rate")) or 0.0, 2) for r in top],
            },
            {
                "name": "中位组",
                "values": [round(_num(r.get("median_rate")) or 0.0, 2) for r in top],
            },
        ],
        "y_name": "得分率(%)",
        "y_max": 100,
    }


def build_knowledge_cohort_report_data(
    *,
    class_name: str = "",
    subject_name: str = "",
    exam_name: str = "",
    cohorts: dict[str, Any] | None = None,
    compare_rows: list[dict[str, Any]] | None = None,
    max_chart_items: int = 12,
) -> dict[str, Any]:
    """组装轻量对比报告 data（标题、摘要、表、柱图 JSON）。"""
    cohorts = cohorts or {}
    rows = list(compare_rows or [])
    n = int(cohorts.get("n") or 0)
    bottom_ids = list(cohorts.get("bottom_ids") or [])
    median_ids = list(cohorts.get("median_ids") or [])
    median_rank = int(cohorts.get("median_rank") or 0)
    band = int(cohorts.get("median_band") or 2)
    lo = int(cohorts.get("median_rank_lo") or median_rank)
    hi = int(cohorts.get("median_rank_hi") or median_rank)

    scope_bits = [x for x in (class_name, subject_name, exam_name) if str(x or "").strip()]
    scope = " · ".join(str(x) for x in scope_bits) if scope_bits else "本班本场"
    title = f"{scope} · 知识点分层对比"

    empty = not rows
    top = rows[0] if rows else None
    if empty:
        summary = (
            f"在「{scope}」范围内未找到可对比的知识点得分率"
            "（可能尚未关联知识点，或后十/中位组无小题明细）。"
        )
        conclusion = summary
    else:
        kn = str((top or {}).get("knowledge_name") or "")
        gap = _num((top or {}).get("gap"))
        gap_txt = _fmt_gap(gap) if gap is not None else "-"
        summary = (
            f"共 {n} 人参考；后十名 {len(bottom_ids)} 人，"
            f"中位组为名次 {lo}–{hi}（中位名次 {median_rank} ±{band}）共 {len(median_ids)} 人。"
            f"按 |中位组得分率 − 后十得分率| 排序，差距最大知识点为「{kn}」"
            f"（中位组 − 后十 = {gap_txt}）。"
        )
        conclusion = (
            f"差距最大知识点为 {kn}（中位组得分率 − 后十得分率 = {gap_txt}）。"
        )

    table_html = build_knowledge_cohort_table_html(rows)
    chart_payload = None if empty else build_knowledge_cohort_chart_payload(
        rows, max_items=max_chart_items
    )
    chart_option = ""
    if chart_payload:
        chart_option = build_chart_option(
            "group_compare_bar",
            chart_payload,
            title="知识点得分率对比（后十 vs 中位组）",
        )

    return {
        "title": title,
        "REPORT_TITLE": title,
        "report_type_label": "知识点分层对比",
        "SUMMARY": summary,
        "CONCLUSION": conclusion,
        "TABLE_HTML": table_html,
        "CHART_OPTION": chart_option,
        "chart_payload": chart_payload,
        "compare_rows": rows,
        "cohorts": {
            "n": n,
            "bottom_count": len(bottom_ids),
            "median_count": len(median_ids),
            "median_rank": median_rank,
            "median_band": band,
            "median_rank_lo": lo,
            "median_rank_hi": hi,
            "bottom_ids": bottom_ids,
            "median_ids": median_ids,
        },
        "empty": empty,
        "class_name": class_name,
        "subject_name": subject_name,
        "exam_name": exam_name,
    }


_KC_REPORT_CSS = """
:root{
  --edu-primary:#1677ff;--edu-primary-soft:#e8f3ff;--edu-primary-bg:#e6f4ff;
  --edu-text-lv1:rgba(0,0,0,.88);--edu-text-lv2:rgba(0,0,0,.65);--edu-text-lv3:rgba(0,0,0,.45);
  --edu-border:#e8edf3;--edu-surface:#f7f9fc;--edu-success:#52c41a;--edu-warning:#fa8c16;--edu-error:#ff4d4f;
  --kc-bottom:#fa8c16;--kc-median:#1677ff;
}
.kc-report{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
  color:var(--edu-text-lv1);line-height:1.65;max-width:1040px;margin:0 auto;padding:8px 4px 20px}
.kc-report *{box-sizing:border-box}
.kc-hero{background:linear-gradient(135deg,#f8fbff 0%,#fff 55%,#f7f9fc 100%);
  border:1px solid var(--edu-border);border-radius:14px;padding:18px 20px 16px;margin-bottom:14px}
.kc-hero h1{margin:0 0 8px;font-size:20px;font-weight:700;letter-spacing:.01em}
.kc-badges{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 4px}
.kc-badge{display:inline-flex;align-items:center;padding:2px 10px;border-radius:999px;font-size:12px;
  background:var(--edu-primary-bg);color:var(--edu-primary);font-weight:600}
.kc-badge.alt{background:#fff7e6;color:#d46b08}
.kc-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin:14px 0 0}
.kc-kpi{border:1px solid var(--edu-border);border-radius:12px;padding:12px 14px;background:#fff;
  box-shadow:0 1px 2px rgba(16,24,40,.03)}
.kc-kpi .label{font-size:12px;color:var(--edu-text-lv3)}
.kc-kpi .value{margin-top:4px;font-size:22px;font-weight:700;color:var(--edu-text-lv1)}
.kc-kpi.accent .value{color:var(--edu-primary)}
.kc-kpi.warn .value{color:var(--edu-warning)}
.kc-card{background:#fff;border:1px solid var(--edu-border);border-radius:14px;padding:16px 18px;margin-bottom:14px;
  box-shadow:0 1px 2px rgba(16,24,40,.03)}
.kc-card h2{margin:0 0 10px;font-size:16px;font-weight:650}
.kc-card h3{margin:0 0 8px;font-size:14px;color:var(--edu-text-lv2);font-weight:650}
.kc-conclusion{margin:0;padding:14px 16px;border-radius:12px;border:1px solid var(--edu-border);
  border-left:3px solid var(--edu-primary);background:linear-gradient(135deg,#fafcff 0%,#f7f9fc 100%);
  color:var(--edu-text-lv2);line-height:1.8}
.kc-conclusion strong{color:var(--edu-text-lv1);font-weight:700}
.kc-sub{margin:10px 0 0;font-size:13px;color:var(--edu-text-lv3);line-height:1.7}
.kc-empty{margin:0;padding:12px 14px;border-radius:10px;background:#fff2f0;border:1px solid #ffccc7;color:#cf1322;font-size:13px}
.edu-table-wrap{overflow-x:auto;margin:4px 0 0;border:1px solid var(--edu-border);border-radius:12px;background:#fff}
.kc-table{width:100%;border-collapse:collapse;font-size:13px;min-width:560px}
.kc-table th,.kc-table td{border:none;border-bottom:1px solid var(--edu-border);padding:11px 14px;
  text-align:left;vertical-align:middle;color:var(--edu-text-lv1)}
.kc-table thead th{background:linear-gradient(180deg,#f3f8ff 0%,var(--edu-primary-bg) 100%);
  color:#3b6fb8;font-weight:650;white-space:nowrap;font-size:12.5px}
.kc-table tbody tr:nth-child(even) td{background:#fafcfe}
.kc-table tbody tr:hover td{background:#f0f7ff}
.kc-table tbody tr:last-child td{border-bottom:none}
.kc-table tr.kc-row-top td{background:#fff7e6 !important}
.kc-col-rate{min-width:132px}
.kc-col-gap{min-width:120px;text-align:right !important}
.kc-col-kn{min-width:160px}
.kc-top-tag{display:inline-block;margin-left:6px;padding:1px 7px;border-radius:999px;font-size:11px;
  background:#fff1f0;color:#cf1322;font-weight:650;vertical-align:middle}
.kc-rate{display:flex;flex-direction:column;gap:4px}
.kc-rate-num{font-variant-numeric:tabular-nums;font-weight:600;font-size:13px}
.kc-rate-track{display:block;height:6px;border-radius:999px;background:#f0f0f0;overflow:hidden}
.kc-rate-fill{display:block;height:100%;border-radius:999px}
.kc-rate-bottom .kc-rate-fill{background:linear-gradient(90deg,#ffc069,#fa8c16)}
.kc-rate-median .kc-rate-fill{background:linear-gradient(90deg,#69b1ff,#1677ff)}
.kc-gap{display:inline-block;min-width:72px;padding:2px 8px;border-radius:999px;font-size:12px;
  font-weight:700;font-variant-numeric:tabular-nums;text-align:center}
.kc-gap-pos{background:#fff1f0;color:#cf1322}
.kc-gap-neg{background:#f6ffed;color:#389e0d}
.kc-gap-zero{background:#f5f5f5;color:var(--edu-text-lv3)}
.kc-muted{color:var(--edu-text-lv3)}
.edu-chart{width:100%;height:380px;margin-top:4px}
.kc-legend{display:flex;flex-wrap:wrap;gap:14px;margin:0 0 8px;font-size:12px;color:var(--edu-text-lv3)}
.kc-legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px;vertical-align:-1px}
.kc-legend .bottom i{background:var(--kc-bottom)}
.kc-legend .median i{background:var(--kc-median)}
@media print{.kc-report{max-width:none}.kc-card,.kc-hero{box-shadow:none}}
""".strip()


def _safe_json_script(raw: str) -> str:
    """避免 JSON 中的 </script> 截断 HTML。"""
    return (raw or "").replace("</", "<\\/")


_KC_ECHART_BOOT = """
(function () {
  function paint() {
    if (typeof echarts === 'undefined') return false;
    document.querySelectorAll('script[data-edu-echart]').forEach(function (el) {
      var target = el.getAttribute('data-edu-echart');
      var raw = (el.textContent || '').trim();
      if (!target || !raw) return;
      var node = document.getElementById(target);
      if (!node) return;
      try {
        var chart = echarts.init(node);
        chart.setOption(JSON.parse(raw));
        window.addEventListener('resize', function () { chart.resize(); });
      } catch (e) {}
    });
    return true;
  }
  if (paint()) return;
  var n = 0;
  var timer = setInterval(function () {
    n += 1;
    if (paint() || n > 40) clearInterval(timer);
  }, 50);
})();
""".strip()


def render_knowledge_cohort_html(data: dict[str, Any]) -> str:
    """完整 HTML 报告：自带样式 + ECharts，iframe srcDoc 可直接出图。"""
    title = html.escape(str(data.get("title") or data.get("REPORT_TITLE") or "知识点分层对比"))
    summary = html.escape(str(data.get("SUMMARY") or ""))
    conclusion = html.escape(str(data.get("CONCLUSION") or ""))
    table = str(data.get("TABLE_HTML") or "")
    chart_option = _safe_json_script(str(data.get("CHART_OPTION") or "").strip())
    empty = bool(data.get("empty"))
    cohorts = data.get("cohorts") if isinstance(data.get("cohorts"), dict) else {}
    n = int(cohorts.get("n") or 0)
    bottom_count = int(cohorts.get("bottom_count") or len(cohorts.get("bottom_ids") or []))
    median_count = int(cohorts.get("median_count") or len(cohorts.get("median_ids") or []))
    lo = cohorts.get("median_rank_lo")
    hi = cohorts.get("median_rank_hi")
    rank_label = f"{lo}–{hi}" if lo and hi else "-"

    class_name = html.escape(str(data.get("class_name") or "").strip() or "本班")
    subject_name = html.escape(str(data.get("subject_name") or "").strip() or "本科目")
    exam_name = html.escape(str(data.get("exam_name") or "").strip() or "本场考试")

    top_gap = "-"
    rows = data.get("compare_rows") if isinstance(data.get("compare_rows"), list) else []
    if rows and isinstance(rows[0], dict) and _num(rows[0].get("gap")) is not None:
        top_gap = _fmt_gap(_num(rows[0].get("gap")))

    chart_block = ""
    if chart_option and not empty:
        chart_block = (
            "<section class='kc-card' id='kcChartSection'>"
            "<h2>得分率对比图</h2>"
            "<div class='kc-legend'>"
            "<span class='bottom'><i></i>后十名</span>"
            "<span class='median'><i></i>中位组</span>"
            "</div>"
            "<p class='kc-sub' style='margin-top:0'>按 |差距| 取前若干知识点，便于一眼看清后十与中位组落差。</p>"
            '<div id="kc_gap_chart" class="edu-chart"></div>'
            f'<script type="application/json" data-edu-echart="kc_gap_chart">{chart_option}</script>'
            "</section>"
        )

    empty_note = ""
    if empty:
        empty_note = (
            "<div class='kc-empty'>"
            "无知识点关联或明细为空，无法计算后十与中位组差距。"
            "</div>"
        )

    body = (
        f"<div class='kc-report edu-report knowledge-cohort'>"
        f"<header class='kc-hero'>"
        f"<div class='kc-badges'>"
        f"<span class='kc-badge'>知识点分层对比</span>"
        f"<span class='kc-badge alt'>{class_name}</span>"
        f"<span class='kc-badge'>{subject_name}</span>"
        f"<span class='kc-badge'>{exam_name}</span>"
        f"</div>"
        f"<h1>{title}</h1>"
        f"<div class='kc-grid'>"
        f"<div class='kc-kpi'><div class='label'>参考人数</div><div class='value'>{n}</div></div>"
        f"<div class='kc-kpi warn'><div class='label'>后十名</div><div class='value'>{bottom_count}</div></div>"
        f"<div class='kc-kpi accent'><div class='label'>中位组（名次 {html.escape(str(rank_label))}）"
        f"</div><div class='value'>{median_count}</div></div>"
        f"<div class='kc-kpi'><div class='label'>最大差距</div><div class='value' "
        f"style='font-size:18px'>{html.escape(top_gap)}</div></div>"
        f"</div>"
        f"</header>"
        f"<section class='kc-card'>"
        f"<h2>结论</h2>"
        f"<p class='kc-conclusion'><strong>结论：</strong>{conclusion}</p>"
        f"<p class='kc-sub'>{summary}</p>"
        f"{empty_note}"
        f"</section>"
        # 表在前：即使 CDN 慢也能先看到明细
        f"<section class='kc-card'>"
        f"<h2>知识点得分率对比表</h2>"
        f"<p class='kc-sub' style='margin-top:0'>正差距表示中位组高于后十；负差距表示后十在该知识点上反而更高。</p>"
        f"{table}"
        f"</section>"
        f"{chart_block}"
        f"</div>"
    )

    return (
        "<!DOCTYPE html>\n"
        '<html lang="zh-CN">\n'
        "<head>\n"
        '<meta charset="UTF-8" />\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1.0" />\n'
        f"<title>{title}</title>\n"
        '<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>\n'
        f"<style>{_KC_REPORT_CSS}</style>\n"
        "</head>\n"
        f"<body>{body}\n"
        f"<script>{_KC_ECHART_BOOT}</script>\n"
        "</body>\n"
        "</html>"
    )
