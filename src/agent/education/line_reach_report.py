"""全市达线情况分析：基于 tb_score_indicator 聚合 + 较上场环比。

区县/全市达线率必须 SUM(reached_count)/SUM(candidates)，禁止 AVG(reach_rate)。
考试场次由 LLM 对照批次列表理解，不用正则从问句里抠。
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import Any, Callable

from src.agent.education.charts import build_chart_option
from src.agent.education.line_reach import canon_line_name, pick_col
from src.agent.education.report_types import ReportType, report_type_label

logger = logging.getLogger(__name__)

__all__ = [
    "LINE_ORDER",
    "TRACKS",
    "build_line_reach_report_data",
    "choose_exam_with_llm",
    "exam_batch_select_sql",
    "filter_exams_by_question",
    "indicator_select_sql",
    "ordered_exam_names",
    "parse_focus_lines",
    "parse_llm_exam_choice",
    "pick_exam_for_question",
    "pick_previous_exam",
    "resolve_current_exam",
    "sql_result_to_dicts",
    "sum_reach",
    "unique_candidates",
]

TRACKS = ("物理类", "历史类")
LINE_ORDER = ("特控线", "本科线", "体育线", "美术线", "音乐线", "211线", "985线", "清北线", "南大线")
_FOCUS_LINES = ("特控线", "本科线")
_LINE_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("清北线", ("清华北大", "清北", "清华", "北大")),
    ("985线", ("985线", "985")),
    ("211线", ("211线", "211")),
    ("南大线", ("南大线", "南大")),
    ("特控线", ("特控线", "特控", "强基", "特招线", "特招")),
    ("本科线", ("本科线", "本科")),
    ("体育线", ("体育线", "体育")),
    ("美术线", ("美术线", "美术")),
    ("音乐线", ("音乐线", "音乐")),
)
_DISTRICT_HINTS = ("各地区", "各区县", "各区", "各县")


def _str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _int(value: Any) -> int:
    if value is None or value == "":
        return 0
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _rate(reached: int, candidates: int) -> float:
    if candidates <= 0:
        return 0.0
    return round(reached * 100.0 / candidates, 2)


def unique_candidates(rows: list[dict[str, Any]]) -> int:
    """参考人数按学校×选科去重，避免多线种把 candidates 加重复。"""
    seen: dict[tuple[str, str, str], int] = {}
    for row in rows:
        key = (
            _str(row.get("school_id")),
            _str(row.get("track")),
            _str(row.get("district")),
        )
        if key not in seen:
            seen[key] = _int(row.get("candidates"))
    return sum(seen.values())


def sum_reach(rows: list[dict[str, Any]]) -> tuple[int, int, float]:
    """返回 (参考人数, 达线人数, 达线率 0-100)。

    达线人数 SUM(reached_count)；参考人数按学校×选科去重后再加。
    禁止 AVG(reach_rate)。
    """
    candidates = unique_candidates(rows)
    reached = sum(_int(r.get("reached_count")) for r in rows)
    return candidates, reached, _rate(reached, candidates)


def exam_batch_select_sql() -> str:
    return (
        "SELECT id, batch_name, exam_time FROM tb_exam_batch "
        "ORDER BY exam_time NULLS LAST, id"
    )


def indicator_select_sql(exam_name: str) -> str:
    lit = (exam_name or "").replace("'", "''")
    return (
        "SELECT exam_name, track, district, school_id, school_name, "
        "line_code, line_name, threshold, candidates, reached_count, reach_rate "
        f"FROM tb_score_indicator WHERE exam_name = '{lit}' LIMIT 8000"
    )


def sql_result_to_dicts(result: Any) -> list[dict[str, Any]]:
    from src.agent.education.line_reach import rows_as_dicts

    if isinstance(result, list):
        return [r for r in result if isinstance(r, dict)]
    if not isinstance(result, dict):
        return []
    return rows_as_dicts(result.get("columns") or [], result.get("rows") or [])


def _parse_exam_time(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    text = _str(value).replace("T", " ").replace("Z", "")
    if not text:
        return None
    if "+" in text[10:]:
        text = text.split("+", 1)[0].strip()
    for fmt, n in (
        ("%Y-%m-%d %H:%M:%S", 19),
        ("%Y-%m-%d %H:%M", 16),
        ("%Y-%m-%d", 10),
        ("%Y/%m/%d", 10),
    ):
        try:
            return datetime.strptime(text[:n], fmt)
        except ValueError:
            continue
    return None


def _batch_sort_key(row: dict[str, Any]) -> tuple:
    parsed = _parse_exam_time(pick_col(row, "exam_time"))
    bid = 0
    try:
        raw_id = pick_col(row, "id")
        if raw_id not in (None, ""):
            bid = int(float(raw_id))
    except (TypeError, ValueError):
        bid = 0
    if parsed is None:
        return (1, datetime.max, bid)
    return (0, parsed, bid)


def ordered_exam_names(batches: list[dict[str, Any]]) -> list[str]:
    """按 exam_time 从早到晚排出批次名；无时间则退回 id。"""
    rows = [r for r in (batches or []) if isinstance(r, dict)]
    rows.sort(key=_batch_sort_key)
    names: list[str] = []
    seen: set[str] = set()
    for row in rows:
        name = _str(pick_col(row, "batch_name", "exam_name", "name"))
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return names


def parse_llm_exam_choice(raw: str, ordered_exams: list[str]) -> str | None:
    """把 LLM 的选场结果对齐到批次全称；不在列表里则丢弃。"""
    from src.agent.util.json_parser import parse_json_tolerant

    names = [_str(x) for x in ordered_exams if _str(x)]
    if not names:
        return None
    text = _str(raw)
    cand = ""
    try:
        parsed = parse_json_tolerant(text)
    except ValueError:
        parsed = None
    if isinstance(parsed, dict):
        cand = _str(parsed.get("exam_name") or parsed.get("exam") or "")
    elif isinstance(parsed, str):
        cand = _str(parsed)
    if not cand:
        cand = text.strip().strip('"').strip("'")
    if cand in names:
        return cand
    if len(cand) < 6:
        return None
    hits = [n for n in names if n in cand or cand in n]
    if len(hits) == 1:
        return hits[0]
    return None


def choose_exam_with_llm(
    question: str,
    ordered_exams: list[str],
    *,
    chat_fn: Callable[[list[dict[str, str]]], str] | None = None,
) -> str | None:
    """请 LLM 根据问句从批次列表里选出当前场。"""
    names = [_str(x) for x in ordered_exams if _str(x)]
    q = _str(question)
    if not names or not q:
        return None
    listed = "\n".join(f"{i + 1}. {n}" for i, n in enumerate(names))
    messages = [
        {
            "role": "system",
            "content": (
                "你根据用户问题，从考试批次列表中选出用户要看的那一场。"
                "只输出 JSON：{\"exam_name\":\"<必须与列表某一项完全一致>\"}。"
                "要理解届别、年级、月份、期中/期末等语义；"
                "问句写了哪一届就必须选那一届，禁止改选更新的其它届；"
                "写了几月就必须选该月（1月不能选 11 月）；"
                "不要因为列表最后一项较新就改选。"
                "问题完全未指定考试时才选列表最后一项。"
            ),
        },
        {
            "role": "user",
            "content": f"用户问题：{q}\n\n考试列表（从早到晚）：\n{listed}",
        },
    ]
    try:
        if chat_fn is not None:
            raw = chat_fn(messages)
        else:
            from src.llm.service import get_default_llm

            raw = get_default_llm().chat(messages)
    except Exception:
        logger.warning("达线报告：LLM 选考试失败，回退批次列表对齐", exc_info=True)
        return None
    return parse_llm_exam_choice(str(raw or ""), names)


def resolve_current_exam(
    ordered_exams: list[str],
    hint: str = "",
    *,
    question: str = "",
    llm_pick: str = "",
) -> str:
    """对齐当前考试：优先用 LLM 选择，再用批次全称是否出现在问句里。

    不把「期末」这类短词从后往前硬配到最近一场。
    """
    names = [_str(x) for x in ordered_exams if _str(x)]
    picked = _str(llm_pick)
    if picked in names:
        return picked
    q = _str(question)
    in_q = [n for n in names if n and q and n in q]
    if in_q:
        return max(in_q, key=len)
    cur = _str(hint)
    if cur in names:
        return cur
    if not names:
        return cur
    if not q and not cur:
        return names[-1]
    return names[-1]


def filter_exams_by_question(ordered_exams: list[str], question: str) -> list[str]:
    """用问句里出现的届/年级/月对照批次全称收窄名单，不从问句里抠短词。"""
    names = [_str(x) for x in ordered_exams if _str(x)]
    q = _str(question)
    if not names or not q:
        return names
    out = names
    for cohort in re.findall(r"\d{4}届", q):
        hit = [n for n in out if cohort in n]
        if hit:
            out = hit
    for grade in ("高三", "高二", "高一", "初三", "初二", "初一"):
        if grade in q:
            hit = [n for n in out if grade in n]
            if hit:
                out = hit
            break
    for mon in re.findall(r"\d{1,2}月", q):
        pat = re.compile(rf"(?<!\d){re.escape(mon)}")
        hit = [n for n in out if pat.search(n)]
        if hit:
            out = hit
    for kind in ("期末", "期中", "月考", "摸底", "模拟"):
        if kind in q:
            hit = [n for n in out if kind in n]
            if hit:
                out = hit
            break
    return out


def pick_exam_for_question(
    ordered_exams: list[str],
    *,
    question: str = "",
    hint: str = "",
    llm_pick: str = "",
    chat_fn: Callable[[list[dict[str, str]]], str] | None = None,
) -> str:
    """对照批次名单选出当前场：届/月能唯一命中则不用模型，否则请 LLM 在收窄名单里选。"""
    names = [_str(x) for x in ordered_exams if _str(x)]
    picked = _str(llm_pick)
    q = _str(question)
    pool = filter_exams_by_question(names, q) or names
    if picked in pool:
        return picked
    in_q = [n for n in names if n and q and n in q]
    if len(in_q) == 1:
        return in_q[0]
    if len(pool) == 1:
        return pool[0]
    cur = _str(hint)
    if cur in pool:
        return cur
    if q:
        llm = choose_exam_with_llm(q, pool, chat_fn=chat_fn)
        if llm:
            return llm
    if pool:
        return pool[-1]
    return resolve_current_exam(names, hint, question=q)


def pick_previous_exam(ordered_exams: list[str], current: str) -> str | None:
    """按考试时间顺序取当前场的上一场（更早的一场）。"""
    names = [_str(x) for x in ordered_exams if _str(x)]
    if not names:
        return None
    cur = _str(current)
    idx = -1
    if cur in names:
        idx = names.index(cur)
    else:
        for i, name in enumerate(names):
            if cur and (cur in name or name in cur):
                idx = i
                break
    if idx > 0:
        return names[idx - 1]
    if idx == 0:
        return None
    if len(names) >= 2:
        return names[-2]
    return None


def _filter_rows(
    rows: list[dict[str, Any]],
    *,
    track: str = "",
    district: str = "",
    line_name: str = "",
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        if track and _str(row.get("track")) != track:
            continue
        if district and _str(row.get("district")) != district:
            continue
        if line_name and _str(row.get("line_name")) != line_name:
            continue
        out.append(row)
    return out


def _fmt_num(value: int | float | None) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.1f}"
    return str(value)


def _delta_html(value: int | float | None, *, suffix: str = "") -> str:
    if value is None:
        return "—"
    cls = "edu-up" if value > 0 else ("edu-down" if value < 0 else "")
    sign = "+" if value > 0 else ""
    text = f"{sign}{_fmt_num(value)}{suffix}"
    if not cls:
        return text
    return f'<span class="{cls}">{text}</span>'


def _kpi_card(label: str, value: str, hint: str = "") -> str:
    hint_html = (
        f'<div class="hint" style="margin-top:6px;font-size:11.5px;line-height:1.45;'
        f'color:rgba(0,0,0,0.45)">{hint}</div>'
        if hint
        else ""
    )
    return (
        f'<div class="edu-kpi"><div class="label">{label}</div>'
        f'<div class="value">{value}</div>{hint_html}</div>'
    )


def _table(headers: list[str], rows: list[list[str]], *, numeric_from: int = 1) -> str:
    head = "<tr>" + "".join(
        f"<th class='{'num' if i >= numeric_from else ''}'>{h}</th>"
        for i, h in enumerate(headers)
    ) + "</tr>"
    body = "".join(
        "<tr>"
        + "".join(
            f"<td class='{'num' if i >= numeric_from else ''}'>{c}</td>"
            for i, c in enumerate(r)
        )
        + "</tr>"
        for r in rows
    )
    inner = f'<table class="edu-table"><thead>{head}</thead><tbody>{body}</tbody></table>'
    return f'<div class="edu-table-wrap">{inner}</div>'


def parse_focus_lines(question: str) -> list[str]:
    """从问句取出点名的线种；未点名则空列表（报告再用特控/本科默认）。"""
    q = _str(question)
    if not q:
        return []
    found: set[str] = set()
    for name, aliases in _LINE_ALIASES:
        if name in q or any(a in q for a in aliases):
            found.add(name)
    return [n for n in LINE_ORDER if n in found]


def _line_short(name: str) -> str:
    n = _str(name)
    return n[:-1] if n.endswith("线") else n


def _display_scope(question: str, scope_label: str) -> str:
    q = _str(question)
    if any(h in q for h in _DISTRICT_HINTS):
        return "各地区"
    return _str(scope_label) or "全市"


def _line_names(rows: list[dict[str, Any]]) -> list[str]:
    seen = {_str(r.get("line_name")) for r in rows if _str(r.get("line_name"))}
    ordered = [n for n in LINE_ORDER if n in seen]
    extra = sorted(seen - set(ordered))
    return ordered + extra


def _tracks(rows: list[dict[str, Any]]) -> list[str]:
    seen = {_str(r.get("track")) for r in rows if _str(r.get("track"))}
    return [t for t in TRACKS if t in seen] or sorted(seen)


def _districts(rows: list[dict[str, Any]]) -> list[str]:
    return sorted({_str(r.get("district")) or "未知区县" for r in rows})


def _schools(rows: list[dict[str, Any]]) -> list[str]:
    return sorted({_str(r.get("school_id")) or "未知学校" for r in rows})


def _hint(curr_n: int, prev_n: int | None, curr_rate: float, prev_rate: float | None) -> str:
    if prev_n is None or prev_rate is None:
        return "暂无上场对比"
    return f"较上场 {_delta_html(curr_n - prev_n, suffix=' 人')} / {_delta_html(round(curr_rate - prev_rate, 2), suffix='pp')}"


def _normalize_indicator_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "exam_name": _str(pick_col(row, "exam_name", "exam", "ksmc")),
        "track": _str(pick_col(row, "track", "xkfx", "subject_type", "xkkm")),
        "district": _str(pick_col(row, "district", "dq")) or "未知区县",
        "school_id": _str(pick_col(row, "school_id", "school_name", "xx")),
        "line_name": canon_line_name(pick_col(row, "line_name")),
        "line_code": _str(pick_col(row, "line_code")),
        "candidates": _int(pick_col(row, "candidates")),
        "reached_count": _int(pick_col(row, "reached_count")),
        "reach_rate": pick_col(row, "reach_rate"),
        "threshold": pick_col(row, "threshold"),
    }


def build_line_reach_report_data(
    curr_rows: list[dict[str, Any]],
    prev_rows: list[dict[str, Any]] | None = None,
    *,
    exam_name: str = "",
    prev_exam_name: str = "",
    scope_label: str = "全市",
    question: str = "",
) -> dict[str, Any]:
    """组装全市达线分析模板 data。问句点名线种/各地区时按点名组织，不默认特控/本科。"""
    curr = [_normalize_indicator_row(r) for r in (curr_rows or []) if isinstance(r, dict)]
    prev = [_normalize_indicator_row(r) for r in (prev_rows or []) if isinstance(r, dict)]
    exam = _str(exam_name) or (curr[0]["exam_name"] if curr else "本次考试")
    prev_exam = _str(prev_exam_name)
    has_prev = bool(prev)
    q = _str(question)
    scope = _display_scope(q, scope_label)
    lines = _line_names(curr) or list(LINE_ORDER)
    tracks = _tracks(curr) or list(TRACKS)
    requested = parse_focus_lines(q)
    focus = requested or [n for n in _FOCUS_LINES if n in lines] or lines[:2]
    delta_lines = requested or lines

    def metric(rows: list[dict[str, Any]], *, track: str = "", line_name: str = "") -> tuple[int, int, float]:
        return sum_reach(_filter_rows(rows, track=track, line_name=line_name))

    kpi_html: list[str] = []
    cand_c = unique_candidates(curr)
    cand_p = unique_candidates(prev) if has_prev else None
    kpi_html.append(
        _kpi_card(
            "参考人数",
            str(cand_c),
            "较上场 " + _delta_html(None if cand_p is None else cand_c - cand_p, suffix=" 人"),
        )
    )
    for line_name in focus:
        c_n, c_hit, c_rate = metric(curr, line_name=line_name)
        p_n, p_hit, p_rate = metric(prev, line_name=line_name) if has_prev else (None, None, None)
        kpi_html.append(
            _kpi_card(
                f"{line_name}达线人数",
                str(c_hit),
                _hint(c_hit, p_hit if has_prev else None, c_rate, p_rate if has_prev else None),
            )
        )
        kpi_html.append(
            _kpi_card(
                f"{line_name}达线率",
                f"{c_rate:.1f}%",
                _hint(c_hit, p_hit if has_prev else None, c_rate, p_rate if has_prev else None),
            )
        )
    kpi_grid = f'<div class="edu-grid">{"".join(kpi_html)}</div>'

    delta_rows: list[list[str]] = []
    drops: list[tuple[int, str]] = []
    for track in tracks:
        for line_name in delta_lines:
            c_n, c_hit, c_rate = metric(curr, track=track, line_name=line_name)
            if c_n == 0 and not _filter_rows(curr, track=track, line_name=line_name):
                continue
            p_n, p_hit, p_rate = metric(prev, track=track, line_name=line_name) if has_prev else (None, None, None)
            d_hit = None if p_hit is None else c_hit - p_hit
            delta_html = _delta_html(d_hit, suffix=" 人")
            delta_rows.append(
                [
                    track,
                    line_name,
                    str(c_hit),
                    _fmt_num(p_hit) if has_prev else "—",
                    delta_html,
                    f"{c_rate:.1f}%",
                    f"{p_rate:.1f}%" if has_prev else "—",
                    _delta_html(None if p_rate is None else round(c_rate - p_rate, 2), suffix="pp"),
                ]
            )
            if d_hit is not None and d_hit < 0:
                drops.append((d_hit, f"{track}{line_name} {delta_html}"))
    drops.sort()
    delta_table = (
        _table(
            ["选科", "线种", "本次人数", "上场人数", "人数增减", "本次达线率", "上场达线率", "率增减"],
            delta_rows,
            numeric_from=2,
        )
        if delta_rows
        else "<p class='edu-sub'>暂无达线指标。</p>"
    )

    compare_chart = ""
    if focus:
        groups = list(focus)
        curr_vals = [metric(curr, line_name=n)[1] for n in groups]
        series = [{"name": exam or "本次", "values": curr_vals}]
        if has_prev:
            series.append({"name": prev_exam or "上场", "values": [metric(prev, line_name=n)[1] for n in groups]})
        compare_chart = build_chart_option(
            "group_compare_bar",
            {"groups": groups, "metrics": series, "y_name": "达线人数"},
            title="各线种达线人数对比",
        )

    dist_names = _districts(curr)
    dist_rows: list[list[str]] = []
    dist_chart = ""
    dist_headers = ["区县", "选科", "参考人数"]
    for line_name in focus:
        dist_headers += [f"{line_name}人数", f"{line_name}率"]
    if dist_names and focus:
        for district in dist_names:
            for track in tracks:
                slice_rows = _filter_rows(curr, track=track, district=district)
                if not slice_rows:
                    continue
                row = [district, track, str(unique_candidates(slice_rows))]
                for line_name in focus:
                    _, c_hit, c_rate = sum_reach(_filter_rows(slice_rows, line_name=line_name))
                    row += [str(c_hit), f"{c_rate:.1f}%"]
                dist_rows.append(row)
        chart_groups = list(dist_names)
        metrics = []
        for line_name in focus:
            metrics.append(
                {
                    "name": line_name,
                    "values": [
                        sum_reach(_filter_rows(curr, district=d, line_name=line_name))[2]
                        for d in chart_groups
                    ],
                }
            )
        if chart_groups:
            dist_chart = build_chart_option(
                "group_compare_bar",
                {"groups": chart_groups, "metrics": metrics, "y_name": "达线率", "y_max": 100},
                title="各区" + "、".join(_line_short(n) for n in focus) + "达线率",
            )
    district_table = (
        _table(dist_headers, dist_rows, numeric_from=2)
        if dist_rows
        else "<p class='edu-sub'>暂无区县达线明细。</p>"
    )

    school_names = _schools(curr)
    school_rows: list[list[str]] = []
    school_headers = ["学校", "选科", "参考人数"]
    for line_name in focus:
        school_headers += [f"{line_name}人数", f"{line_name}率"]
    if school_names and focus:
        for school in school_names:
            for track in tracks:
                slice_rows = [
                    r for r in curr
                    if _str(r.get("school_id")) == school and _str(r.get("track")) == track
                ]
                if not slice_rows:
                    continue
                row = [school, track, str(unique_candidates(slice_rows))]
                for line_name in focus:
                    _, c_hit, c_rate = sum_reach(
                        _filter_rows(slice_rows, line_name=line_name)
                    )
                    row += [str(c_hit), f"{c_rate:.1f}%"]
                school_rows.append(row)
    school_table = (
        _table(school_headers, school_rows, numeric_from=2)
        if school_rows
        else "<p class='edu-sub'>暂无学校达线明细。</p>"
    )

    line_label = "、".join(_line_short(n) for n in focus)
    if not curr:
        insight = "<p>该场暂无达线指标。请先在预测分数线页维护分数线并重算。</p>"
    elif has_prev:
        insight = (
            f"<p class='edu-insight-line'>{scope}【{exam}】{line_label}较上场【{prev_exam}】："
            f"参考人数 {cand_c}（{_delta_html(None if cand_p is None else cand_c - cand_p, suffix=' 人')}）。</p>"
        )
        if drops:
            insight += "<p>人数下降较多：" + "；".join(x[1] for x in drops[:4]) + "。</p>"
        else:
            insight += f"<p>{line_label}达线人数较上场未出现明显下滑。</p>"
    else:
        insight = (
            f"<p class='edu-insight-line'>{scope}【{exam}】{line_label}达线指标已汇总；"
            "暂无上场考试，环比从下场开始。</p>"
        )

    recs = [f"按问句关注 {line_label}，区县/全市达线率用 SUM 后重算，避免学校达线率平均。"]
    if drops:
        recs.append("对人数下滑的选科/线种，结合区县明细定位薄弱校并安排专项复习。")
    if has_prev:
        recs.append(f"环比口径为相邻两场（{prev_exam} → {exam}），人数与率均按 SUM 后重算。")
    recommendations = "<ul>" + "".join(f"<li>{x}</li>" for x in recs) + "</ul>"

    title_bits = [p for p in (scope, exam) if p]
    if requested:
        title = f"{' · '.join(title_bits)} · {line_label}达线情况分析"
        subtitle = f"{scope}{line_label}达线人数/率" + (" + 较上次考试环比" if has_prev else "")
    else:
        title = f"{' · '.join(title_bits)}达线情况分析".strip(" ·")
        subtitle = "全市达线人数/率 + 较上次考试环比"
    return {
        "REPORT_TITLE": title,
        "REPORT_TYPE": report_type_label(ReportType.LINE_REACH),
        "REPORT_SUBTITLE": subtitle,
        "REPORT_TIME": "",
        "SCOPE": scope,
        "EXAM_NAME": exam,
        "PREV_EXAM_NAME": prev_exam or "—",
        "SUBJECT_NAME": "全科",
        "KPI_GRID": kpi_grid,
        "DELTA_TABLE": delta_table,
        "COMPARE_CHART": compare_chart,
        "DISTRICT_TABLE": district_table,
        "DISTRICT_CHART": dist_chart,
        "SCHOOL_TABLE": school_table,
        "GENERAL_INSIGHT": insight,
        "SUMMARY": insight,
        "RECOMMENDATIONS": recommendations,
        "_stats": {"count": cand_c},
    }
