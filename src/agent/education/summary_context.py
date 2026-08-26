"""为 Summarizer 补充教育学情产出上下文，避免与 HTML 报告矛盾。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from src.agent.education.query_parse import find_upstream_fetch_data

#: 率/分数类 KPI 对账容差（与格式化两位小数对齐）
_KPI_FLOAT_TOLERANCE = 0.05

_DIAGNOSIS_REPORT_TOOLS = frozenset({
    "build_subject_diagnosis_sections_tool",
    "build_subject_diagnosis_report_tool",
    "build_student_subject_diagnosis_tool",
    "build_diagnostic_report_data_tool",
    "build_citywide_exam_analysis_report_tool",
    "build_line_reach_report_data_tool",
    "build_subject_research_report_data_tool",
})

_SQL_OFFSET_RE = re.compile(r"\bOFFSET\b", re.IGNORECASE)
_SQL_LIMIT_RE = re.compile(r"\bLIMIT\s+\d+", re.IGNORECASE)
_KPI_LINE_RE = re.compile(
    r"(参考人数|班级人数|应考|实考|全班|共\s*\d+\s*人|卷面满分|及格线|优秀线|"
    r"及格率|优秀率|均分|满分|count\s*=)",
    re.IGNORECASE,
)


def _safe_len(value: Any) -> int:
    if isinstance(value, list):
        return len(value)
    return 0


def _scan_tool_calls(tool_calls: list[dict[str, Any]] | None) -> dict[str, Any]:
    item_count = 0
    knowledge_count = 0
    score_count = 0
    report_titles: list[str] = []
    fetch_ok = False
    report_ok = False

    for tc in tool_calls or []:
        if not tc.get("success"):
            continue
        tool = str(tc.get("tool") or "")
        data = tc.get("data")
        if not isinstance(data, dict):
            continue
        if tool == "fetch_subject_diagnosis_data_tool" and not data.get("error"):
            fetch_ok = True
            item_count = max(item_count, _safe_len(data.get("item_rows")))
            knowledge_count = max(knowledge_count, _safe_len(data.get("knowledge_rows")))
            score_count = max(score_count, _safe_len(data.get("score_rows")))
        if tool in _DIAGNOSIS_REPORT_TOOLS and data.get("output_type") == "html":
            report_ok = True
            title = str(data.get("title") or "").strip()
            if title and title not in report_titles:
                report_titles.append(title)

    return {
        "item_count": item_count,
        "knowledge_count": knowledge_count,
        "score_count": score_count,
        "report_titles": report_titles,
        "fetch_ok": fetch_ok,
        "report_ok": report_ok,
    }


def collect_education_artifacts(
    *,
    tool_calls: list[dict[str, Any]] | None = None,
    reports: list[dict[str, Any]] | None = None,
    report_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """汇总工具链中的小题/知识点/报告产出。"""
    agg = _scan_tool_calls(tool_calls)
    for rp in reports or []:
        title = str(rp.get("title") or "").strip()
        if title and title not in agg["report_titles"]:
            agg["report_titles"].append(title)
            agg["report_ok"] = True

    fetch_data = find_upstream_fetch_data(report_data)
    if isinstance(fetch_data, dict) and not fetch_data.get("error"):
        agg["fetch_ok"] = True
        agg["item_count"] = max(agg["item_count"], _safe_len(fetch_data.get("item_rows")))
        agg["knowledge_count"] = max(
            agg["knowledge_count"], _safe_len(fetch_data.get("knowledge_rows"))
        )
        agg["score_count"] = max(agg["score_count"], _safe_len(fetch_data.get("score_rows")))

    if report_data:
        for st in report_data.get("sub_tasks") or []:
            sub = _scan_tool_calls(st.get("tool_calls"))
            for rp in st.get("reports") or []:
                title = str(rp.get("title") or "").strip()
                if title and title not in sub["report_titles"]:
                    sub["report_titles"].append(title)
                    sub["report_ok"] = True
            agg["item_count"] = max(agg["item_count"], sub["item_count"])
            agg["knowledge_count"] = max(agg["knowledge_count"], sub["knowledge_count"])
            agg["score_count"] = max(agg["score_count"], sub["score_count"])
            agg["fetch_ok"] = agg["fetch_ok"] or sub["fetch_ok"]
            agg["report_ok"] = agg["report_ok"] or sub["report_ok"]
            for t in sub["report_titles"]:
                if t not in agg["report_titles"]:
                    agg["report_titles"].append(t)

    agg["has_item_data"] = agg["item_count"] > 0
    agg["has_knowledge_data"] = agg["knowledge_count"] > 0
    agg["has_diagnosis_report"] = agg["report_ok"] or bool(agg["report_titles"])
    return agg


def sql_looks_paginated(sql: str | None) -> bool:
    """SQL 是否含 OFFSET（分页片段，返回行数≠班级总人数）。"""
    return bool(_SQL_OFFSET_RE.search(sql or ""))


def sql_looks_row_capped(sql: str | None) -> bool:
    """SQL 是否含 LIMIT/OFFSET，使返回行数不能当作全体参考人数。

    常见误判：``LIMIT 20`` 无 OFFSET 时，下游仍把「共 20 行」写成全班 20 人。
    """
    s = sql or ""
    return bool(_SQL_OFFSET_RE.search(s) or _SQL_LIMIT_RE.search(s))


def truncate_keeping_kpi_lines(text: str, *, limit: int = 1200) -> str:
    """截断结论时优先保留含人数/分数线的行，避免丢掉 52 人 / 45 / 75。"""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    lines = text.splitlines()
    kpi_lines = [ln for ln in lines if _KPI_LINE_RE.search(ln)]
    head = text[: max(400, limit - 400)].rstrip()
    if not kpi_lines:
        return head + "\n…（已截断）"
    kpi_block = "\n".join(kpi_lines[:40])
    combined = f"{head}\n\n【保留的人数/分数线要点】\n{kpi_block}"
    if len(combined) <= limit + 200:
        return combined
    return combined[: limit + 200] + "\n…（已截断）"


_PASS_LINE_IN_TEXT_RE = re.compile(r"(及格线\s*)(\d+(?:\.\d+)?)")
_EXCELLENT_LINE_IN_TEXT_RE = re.compile(r"(优秀线\s*)(\d+(?:\.\d+)?)")
_REF_HEADCOUNT_IN_TEXT_RE = re.compile(
    r"((?:年级|班级|全校|全年级)?"
    r"(?:参考人数|班级人数|应考人数|实考人数|全班(?:人数)?)\s*(?:为|共)?\s*)(\d+)(\s*人)"
)
_AVG_IN_TEXT_RE = re.compile(
    r"((?:年级|班级|全校|全年级)?(?:均分|平均分)\s*(?:为|是)?\s*)(\d+(?:\.\d+)?)"
)
_STDEV_IN_TEXT_RE = re.compile(r"(标准差\s*(?:为|是)?\s*)(\d+(?:\.\d+)?)")
_PASS_RATE_IN_TEXT_RE = re.compile(r"(及格率\s*(?:为|是)?\s*)(\d+(?:\.\d+)?)\s*%")
_EXCELLENT_RATE_IN_TEXT_RE = re.compile(r"(优秀率\s*(?:为|是)?\s*)(\d+(?:\.\d+)?)\s*%")
_FULL_SCORE_IN_TEXT_RE = re.compile(
    r"((?:卷面)?满分\s*(?:为|是)?\s*)(\d+(?:\.\d+)?)(\s*分)?"
)

# HTML KPI 卡片：<div class="label">参考人数</div><div class="value">829</div>
_HTML_KPI_RE = re.compile(
    r'class="label"[^>]*>\s*(参考人数|平均分|均分|及格率|优秀率|标准差|满分|卷面满分)'
    r"\s*</div>\s*<div[^>]*class=\"value\"[^>]*>\s*([^<]+?)\s*</div>",
    re.IGNORECASE,
)
_HTML_REF_COUNT_RE = re.compile(r"参考人数\s*(\d+)\s*人")
_PREVIEW_LIKE_COUNTS = frozenset({3, 5, 10, 20})  # 常见预览/LIMIT 上限，优先不信
_SCORE_COL_HINTS = (
    "student",
    "学生",
    "姓名",
    "name",
    "学号",
    "score",
    "分数",
    "成绩",
    "得分",
)
# 「共 20 人 / 20 名学生」等无标签人数——仅当数值像预览规模或明显小于权威 count 时改写
_LOOSE_TOTAL_HEADCOUNT_RE = re.compile(r"(共\s*)(\d+)(\s*人)")
_LOOSE_STUDENT_HEADCOUNT_RE = re.compile(r"(\d+)(\s*名(?:学生|同学))")
_VIEWED_HEADCOUNT_RE = re.compile(r"(所查看的\s*)(\d+)(\s*名?)")
_MD_TABLE_HEADCOUNT_RE = re.compile(
    r"(\|\s*(?:参考人数|班级人数|应考人数|实考人数)\s*\|\s*)(\d+)(\s*人?\s*\|)"
)
_MD_TABLE_AVG_RE = re.compile(
    r"(\|\s*(?:均分|平均分)\s*\|\s*)(\d+(?:\.\d+)?)(\s*\|)"
)
_MD_TABLE_PASS_RATE_RE = re.compile(
    r"(\|\s*及格率\s*\|\s*)(\d+(?:\.\d+)?)(?:\s*%)?(\s*\|)"
)
_MD_TABLE_EXCELLENT_RATE_RE = re.compile(
    r"(\|\s*优秀率\s*\|\s*)(\d+(?:\.\d+)?)(?:\s*%)?(\s*\|)"
)
_MD_TABLE_FULL_SCORE_RE = re.compile(
    r"(\|\s*(?:卷面)?满分\s*\|\s*)(\d+(?:\.\d+)?)(\s*(?:分)?\s*\|)"
)
_MD_TABLE_PASS_LINE_RE = re.compile(
    r"(\|\s*及格线\s*\|\s*)(\d+(?:\.\d+)?)(\s*(?:分)?\s*\|)"
)
_MD_TABLE_EXCELLENT_LINE_RE = re.compile(
    r"(\|\s*优秀线\s*\|\s*)(\d+(?:\.\d+)?)(\s*(?:分)?\s*\|)"
)
_MD_TABLE_STDEV_RE = re.compile(
    r"(\|\s*标准差\s*\|\s*)(\d+(?:\.\d+)?)(\s*\|)"
)


@dataclass(frozen=True)
class KpiClaimConflict:
    """结论中某 KPI 声明与权威统计不一致。"""

    field: str
    claimed: float | int
    authority: float | int
    span: str = ""


def _values_conflict(field: str, claimed: float, authority: float) -> bool:
    if field == "count":
        return int(round(claimed)) != int(round(authority))
    return abs(float(claimed) - float(authority)) > _KPI_FLOAT_TOLERANCE


def _iter_labeled_kpi_claims(text: str) -> list[tuple[str, float, str]]:
    """从正文抽取带标签或 Markdown 表行的 KPI 声明 ``(field, value, span)``。"""
    claims: list[tuple[str, float, str]] = []

    def add(field: str, raw: str, span: str) -> None:
        if field == "count":
            v = _safe_int(raw)
        else:
            v = _safe_float(raw)
        if v is None:
            return
        claims.append((field, float(v) if field != "count" else float(int(v)), span))

    for m in _REF_HEADCOUNT_IN_TEXT_RE.finditer(text):
        add("count", m.group(2), m.group(0))
    for m in _MD_TABLE_HEADCOUNT_RE.finditer(text):
        add("count", m.group(2), m.group(0))
    for m in _AVG_IN_TEXT_RE.finditer(text):
        add("avg", m.group(2), m.group(0))
    for m in _MD_TABLE_AVG_RE.finditer(text):
        add("avg", m.group(2), m.group(0))
    for m in _PASS_RATE_IN_TEXT_RE.finditer(text):
        add("pass_rate", m.group(2), m.group(0))
    for m in _MD_TABLE_PASS_RATE_RE.finditer(text):
        add("pass_rate", m.group(2), m.group(0))
    for m in _EXCELLENT_RATE_IN_TEXT_RE.finditer(text):
        add("excellent_rate", m.group(2), m.group(0))
    for m in _MD_TABLE_EXCELLENT_RATE_RE.finditer(text):
        add("excellent_rate", m.group(2), m.group(0))
    for m in _STDEV_IN_TEXT_RE.finditer(text):
        add("stdev", m.group(2), m.group(0))
    for m in _MD_TABLE_STDEV_RE.finditer(text):
        add("stdev", m.group(2), m.group(0))
    for m in _PASS_LINE_IN_TEXT_RE.finditer(text):
        add("pass_line", m.group(2), m.group(0))
    for m in _MD_TABLE_PASS_LINE_RE.finditer(text):
        add("pass_line", m.group(2), m.group(0))
    for m in _EXCELLENT_LINE_IN_TEXT_RE.finditer(text):
        add("excellent_line", m.group(2), m.group(0))
    for m in _MD_TABLE_EXCELLENT_LINE_RE.finditer(text):
        add("excellent_line", m.group(2), m.group(0))
    for m in _FULL_SCORE_IN_TEXT_RE.finditer(text):
        add("full_score", m.group(2), m.group(0))
    for m in _MD_TABLE_FULL_SCORE_RE.finditer(text):
        add("full_score", m.group(2), m.group(0))
    return claims


def audit_summary_kpi_claims(
    text: str,
    stats: dict[str, Any] | None,
) -> list[KpiClaimConflict]:
    """比对结论中的 KPI 声明与权威统计，返回冲突列表。"""
    if not text or not stats:
        return []
    out: list[KpiClaimConflict] = []
    for field, claimed, span in _iter_labeled_kpi_claims(text):
        auth_raw = stats.get(field)
        if auth_raw is None or auth_raw == "" or auth_raw == "-":
            continue
        if field == "count":
            authority = _safe_int(auth_raw)
        else:
            authority = _safe_float(auth_raw)
        if authority is None:
            continue
        if not _values_conflict(field, claimed, float(authority)):
            continue
        claimed_out: float | int = int(claimed) if field == "count" else claimed
        auth_out: float | int = int(authority) if field == "count" else authority
        out.append(
            KpiClaimConflict(
                field=field,
                claimed=claimed_out,
                authority=auth_out,
                span=span[:80],
            )
        )
    return out


def _fmt_authority_score(value: Any) -> str:
    """分数线展示：整数写 ``70.0``（与工具原文常见写法一致），非整保留有效小数。"""
    f = float(value)
    if abs(f - round(f)) < 1e-9:
        return f"{int(round(f))}.0"
    text = f"{round(f, 4):.4f}".rstrip("0").rstrip(".")
    return text


def _fmt_authority_rate(value: Any) -> str:
    """百分率展示：保留最多两位小数，去掉尾随 0。"""
    f = float(value)
    text = f"{round(f, 2):.2f}".rstrip("0").rstrip(".")
    return text if text else "0"


def _fmt_authority_metric(value: Any) -> str:
    """均分/标准差等：最多两位小数。"""
    f = float(value)
    if abs(f - round(f)) < 1e-9:
        return str(int(round(f)))
    return f"{round(f, 2):.2f}".rstrip("0").rstrip(".")


def _safe_int(value: Any) -> int | None:
    try:
        if value is None or value == "" or value == "-":
            return None
        return int(float(str(value).strip().replace(",", "").replace("人", "")))
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or value == "" or value == "-":
            return None
        text = str(value).strip().replace(",", "").replace("%", "").replace("分", "")
        return float(text)
    except (TypeError, ValueError):
        return None


def _stats_rank_key(stats: dict[str, Any]) -> tuple[int, int, int, int]:
    """权威候选排序：人数优先，其次完整度；预览规模人数降权。"""
    count = _safe_int(stats.get("count")) or 0
    preview_penalty = 1 if count in _PREVIEW_LIKE_COUNTS else 0
    has_rate = 1 if stats.get("pass_rate") is not None else 0
    has_avg = 1 if stats.get("avg") is not None else 0
    return (count, -preview_penalty, has_rate, has_avg)


def _merge_stats_fields(*parts: dict[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for part in parts:
        if not isinstance(part, dict):
            continue
        for k, v in part.items():
            if v is None or v == "" or v == "-":
                continue
            if k not in out or out[k] in (None, "", "-"):
                out[k] = v
    return out


def _stats_from_mapping(data: dict[str, Any]) -> dict[str, Any] | None:
    """从工具 data / 模板字段抽出 KPI（支持 ``_stats`` 与 TOTAL_COUNT 等）。"""
    if not isinstance(data, dict) or data.get("error"):
        return None
    cached = data.get("_stats")
    base: dict[str, Any] = {}
    if isinstance(cached, dict) and (
        cached.get("count") is not None or cached.get("avg") is not None
    ):
        base = dict(cached)

    mapped = {
        "count": _safe_int(data.get("TOTAL_COUNT") if data.get("TOTAL_COUNT") is not None else data.get("count")),
        "avg": _safe_float(data.get("AVG_SCORE") if data.get("AVG_SCORE") is not None else data.get("avg")),
        "pass_rate": _safe_float(
            data.get("PASS_RATE") if data.get("PASS_RATE") is not None else data.get("pass_rate")
        ),
        "excellent_rate": _safe_float(
            data.get("EXCELLENT_RATE")
            if data.get("EXCELLENT_RATE") is not None
            else data.get("excellent_rate")
        ),
        "stdev": _safe_float(data.get("STDEV") if data.get("STDEV") is not None else data.get("stdev")),
        "full_score": _safe_float(
            data.get("FULL_SCORE")
            if data.get("FULL_SCORE") is not None
            else data.get("_FULL_SCORE")
            if data.get("_FULL_SCORE") is not None
            else data.get("full_score")
        ),
        "max": _safe_float(data.get("MAX_SCORE") if data.get("MAX_SCORE") is not None else data.get("max")),
        "min": _safe_float(data.get("MIN_SCORE") if data.get("MIN_SCORE") is not None else data.get("min")),
        "pass_line": _safe_float(data.get("pass_line")),
        "excellent_line": _safe_float(data.get("excellent_line")),
    }
    merged = _merge_stats_fields(base, {k: v for k, v in mapped.items() if v is not None})
    if merged.get("count") is None and merged.get("avg") is None and merged.get("pass_rate") is None:
        return None
    return merged


def extract_stats_from_report_html(html: str | None) -> dict[str, Any] | None:
    """从已渲染报告 HTML 抽取 KPI（报告侧全量统计，优先于样例预览）。"""
    if not html:
        return None
    blob = html[:20000]
    out: dict[str, Any] = {}
    for m in _HTML_KPI_RE.finditer(blob):
        label = m.group(1).strip()
        raw = m.group(2).strip()
        if label == "参考人数":
            n = _safe_int(raw)
            if n is not None:
                out["count"] = n
        elif label in ("平均分", "均分"):
            v = _safe_float(raw)
            if v is not None:
                out["avg"] = v
        elif label == "及格率":
            v = _safe_float(raw)
            if v is not None:
                out["pass_rate"] = v
        elif label == "优秀率":
            v = _safe_float(raw)
            if v is not None:
                out["excellent_rate"] = v
        elif label == "标准差":
            v = _safe_float(raw)
            if v is not None:
                out["stdev"] = v
        elif label in ("满分", "卷面满分"):
            v = _safe_float(raw)
            if v is not None:
                out["full_score"] = v
    if out.get("count") is None:
        m = _HTML_REF_COUNT_RE.search(blob)
        if m:
            out["count"] = int(m.group(1))
    if not out:
        return None
    return out


def _columns_look_like_roster(columns: list[Any] | None) -> bool:
    blob = "".join(str(c).lower() for c in (columns or []))
    return any(h in blob for h in _SCORE_COL_HINTS)


def extract_exec_authority_data(
    *,
    sql: str | None = None,
    row_count: int | None = None,
    columns: list[Any] | None = None,
    sql_row_capped: bool | None = None,
) -> dict[str, Any] | None:
    """无 LIMIT/OFFSET 的明细查询行数可作为参考人数权威来源。"""
    rc = _safe_int(row_count)
    if rc is None or rc <= 0:
        return None
    capped = bool(sql_row_capped) if sql_row_capped is not None else sql_looks_row_capped(sql)
    if capped:
        return None
    if columns is not None and not _columns_look_like_roster(columns):
        return None
    return {"count": rc}


def extract_stats_authority_data(
    tool_calls: list[dict[str, Any]] | None = None,
    *,
    reports: list[dict[str, Any]] | None = None,
    exec_results: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """抽取权威 KPI（全模式通用）。

    候选来源（取人数最大、预览规模降权）：
    1. 报告 HTML / ``_stats`` / TOTAL_COUNT
    2. ``compute_score_stats_tool``
    3. 其它 build 工具 data
    4. 无 LIMIT 的 ``execute_sql`` / exec_result 行数（纯 SQL 提问也能对齐人数）
    """
    candidates: list[dict[str, Any]] = []

    for rp in reports or []:
        if not isinstance(rp, dict):
            continue
        from_html = extract_stats_from_report_html(str(rp.get("html") or ""))
        from_meta = _stats_from_mapping(rp)
        merged = _merge_stats_fields(from_html, from_meta)
        if merged:
            candidates.append(merged)

    for tc in tool_calls or []:
        if not tc.get("success"):
            continue
        tool = str(tc.get("tool") or "")
        data = tc.get("data")
        if not isinstance(data, dict) or data.get("error"):
            continue
        if tool == "compute_score_stats_tool" and data.get("count") is not None:
            candidates.append(dict(data))
            continue
        if tool in ("execute_sql", "execute_sql_tool") or (
            "row_count" in data and ("columns" in data or "rows" in data)
        ):
            exec_auth = extract_exec_authority_data(
                sql=str(data.get("sql") or ""),
                row_count=data.get("row_count"),
                columns=list(data.get("columns") or []),
                sql_row_capped=data.get("sql_row_capped"),
            )
            if exec_auth:
                candidates.append(exec_auth)
        mapped = _stats_from_mapping(data)
        if mapped:
            candidates.append(mapped)
        if data.get("output_type") == "html" or data.get("html"):
            from_html = extract_stats_from_report_html(str(data.get("html") or ""))
            if from_html:
                candidates.append(from_html)

    for er in exec_results or []:
        if not isinstance(er, dict):
            continue
        exec_auth = extract_exec_authority_data(
            sql=str(er.get("sql") or ""),
            row_count=er.get("row_count"),
            columns=list(er.get("columns") or []),
            sql_row_capped=er.get("sql_row_capped"),
        )
        if exec_auth:
            candidates.append(exec_auth)

    if not candidates:
        return None
    return max(candidates, key=_stats_rank_key)


def reconcile_summary_kpis(
    text: str,
    stats: dict[str, Any] | None,
) -> str:
    """用权威统计改写结论中的人数/分数线/均分/率，避免与报告 KPI 矛盾。

    无权威统计或空文本时原样返回。覆盖带标签人数、Markdown 指标表，以及
    「共 N 人 / N 名学生」等预览规模误写（全模式通用后处理）。
    """
    if not text or not stats:
        return text or ""

    out = text
    if stats.get("pass_line") is not None:
        pl = _fmt_authority_score(stats["pass_line"])
        out = _PASS_LINE_IN_TEXT_RE.sub(rf"\g<1>{pl}", out)
        out = _MD_TABLE_PASS_LINE_RE.sub(rf"\g<1>{pl}\g<3>", out)
    if stats.get("excellent_line") is not None:
        el = _fmt_authority_score(stats["excellent_line"])
        out = _EXCELLENT_LINE_IN_TEXT_RE.sub(rf"\g<1>{el}", out)
        out = _MD_TABLE_EXCELLENT_LINE_RE.sub(rf"\g<1>{el}\g<3>", out)

    count = _safe_int(stats.get("count"))
    if count is not None:
        out = _REF_HEADCOUNT_IN_TEXT_RE.sub(rf"\g<1>{count}\g<3>", out)
        out = _MD_TABLE_HEADCOUNT_RE.sub(rf"\g<1>{count}\g<3>", out)

        def _rewrite_loose(match: re.Match[str], *, num_g: int, keep_prefix: bool) -> str:
            try:
                n = int(match.group(num_g))
            except (TypeError, ValueError):
                return match.group(0)
            if n == count:
                return match.group(0)
            # 预览规模，或明显小于权威人数（常见 LIMIT/样例误写）
            if n in _PREVIEW_LIKE_COUNTS or n < count:
                if keep_prefix:
                    return f"{match.group(1)}{count}{match.group(3)}"
                return f"{count}{match.group(2)}"
            return match.group(0)

        out = _LOOSE_TOTAL_HEADCOUNT_RE.sub(
            lambda m: _rewrite_loose(m, num_g=2, keep_prefix=True), out
        )
        out = _LOOSE_STUDENT_HEADCOUNT_RE.sub(
            lambda m: _rewrite_loose(m, num_g=1, keep_prefix=False), out
        )
        out = _VIEWED_HEADCOUNT_RE.sub(
            lambda m: _rewrite_loose(m, num_g=2, keep_prefix=True), out
        )

    if stats.get("avg") is not None:
        avg = _fmt_authority_metric(stats["avg"])
        out = _AVG_IN_TEXT_RE.sub(rf"\g<1>{avg}", out)
        out = _MD_TABLE_AVG_RE.sub(rf"\g<1>{avg}\g<3>", out)
    if stats.get("stdev") is not None:
        sd = _fmt_authority_metric(stats["stdev"])
        out = _STDEV_IN_TEXT_RE.sub(rf"\g<1>{sd}", out)
        out = _MD_TABLE_STDEV_RE.sub(rf"\g<1>{sd}\g<3>", out)
    if stats.get("pass_rate") is not None:
        pr = _fmt_authority_rate(stats["pass_rate"])
        out = _PASS_RATE_IN_TEXT_RE.sub(rf"\g<1>{pr}%", out)
        out = _MD_TABLE_PASS_RATE_RE.sub(rf"\g<1>{pr}%\g<3>", out)
    if stats.get("excellent_rate") is not None:
        er = _fmt_authority_rate(stats["excellent_rate"])
        out = _EXCELLENT_RATE_IN_TEXT_RE.sub(rf"\g<1>{er}%", out)
        out = _MD_TABLE_EXCELLENT_RATE_RE.sub(rf"\g<1>{er}%\g<3>", out)
    if stats.get("full_score") is not None:
        fs = _fmt_authority_metric(stats["full_score"])
        out = _FULL_SCORE_IN_TEXT_RE.sub(rf"\g<1>{fs}\g<3>", out)
        out = _MD_TABLE_FULL_SCORE_RE.sub(rf"\g<1>{fs}\g<3>", out)
    return out


def _claimed_literal_pattern(claimed: float | int, *, field: str) -> re.Pattern[str]:
    """生成与冲突 claimed 字面量精确匹配、带数字边界的正则。"""
    if field == "count" or (
        isinstance(claimed, float) and abs(claimed - round(claimed)) < 1e-9
    ):
        lit = str(int(round(float(claimed))))
    else:
        # 保留常见小数写法：91.07 / 91.070
        lit = f"{float(claimed):.4f}".rstrip("0").rstrip(".")
        if "." not in lit:
            lit = str(int(round(float(claimed))))
    escaped = re.escape(lit)
    # 避免匹配更长数字的子串（如 91 不匹配 910）
    return re.compile(rf"(?<![\d.]){escaped}(?![\d.])")


def scrub_residual_conflicting_values(
    text: str,
    conflicts: list[KpiClaimConflict],
    stats: dict[str, Any] | None,
) -> str:
    """清扫对账发现的错误 claimed 残留字面量，替换为权威格式化值。"""
    if not text or not conflicts or not stats:
        return text or ""

    out = text
    # 同一字段可能多处冲突，按 claimed 去重后替换
    seen: set[tuple[str, str]] = set()
    for c in conflicts:
        # 人数字面量过短（如 20）全文替换易误伤班级号/分数，标签路径已覆盖
        if c.field == "count":
            continue
        auth_raw = stats.get(c.field)
        if auth_raw is None:
            continue
        key = (c.field, str(c.claimed))
        if key in seen:
            continue
        seen.add(key)
        if c.field == "count":
            replacement = str(int(round(float(c.authority))))
        elif c.field in ("pass_rate", "excellent_rate"):
            replacement = _fmt_authority_rate(auth_raw)
        elif c.field in ("pass_line", "excellent_line"):
            replacement = _fmt_authority_score(auth_raw)
        else:
            replacement = _fmt_authority_metric(auth_raw)
        pat = _claimed_literal_pattern(c.claimed, field=c.field)
        out = pat.sub(replacement, out)
    return out


def scrub_preview_headcount_claims(text: str) -> str:
    """无权威 count 时，去掉明显把预览规模写成全体人数的表述。

    避免任意提问在缺 stats 时仍输出「参考人数 20 人」。
    """
    if not text:
        return text or ""
    out = text
    for n in sorted(_PREVIEW_LIKE_COUNTS, reverse=True):
        out = re.sub(
            rf"((?:年级|班级|全校|全年级)?"
            rf"(?:参考人数|班级人数|应考人数|实考人数|全班(?:人数)?)\s*(?:为|共)?\s*){n}(\s*人)",
            r"\1（以全量统计/报告为准，预览行数不可用）",
            out,
        )
        out = re.sub(
            rf"所查看的\s*{n}\s*名?",
            "（预览样本，非全体）",
            out,
        )
    return out


_FACT_REF_COUNT_CLAUSE_RE = re.compile(
    r"[，,]?\s*"
    r"(?:本次)?(?:共\s*)?\d+\s*人(?:参考|应考|实考)"
    r"(?:\s*[（(][^）)]*[）)])?"
    r"[，,]?"
)
_FACT_LABELED_REF_COUNT_RE = re.compile(
    r"[|*]?\s*(?:年级|班级|全校|全年级)?"
    r"(?:参考人数|班级人数|应考人数|实考人数)\s*[|：:为是]?\s*\d+\s*人?\s*[|*]?"
)


def scrub_fact_answer_headcount_noise(text: str) -> str:
    """事实问答后处理：去掉未询问的「参考人数 / 共N人参考」套话。"""
    if not text:
        return text or ""
    out = _FACT_REF_COUNT_CLAUSE_RE.sub("", text)
    out = _FACT_LABELED_REF_COUNT_RE.sub("", out)
    out = re.sub(r"[（(]\s*权威统计口径\s*[）)]", "", out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def reconcile_answer_with_artifacts_detailed(
    text: str,
    *,
    tool_calls: list[dict[str, Any]] | None = None,
    reports: list[dict[str, Any]] | None = None,
    exec_results: list[dict[str, Any]] | None = None,
    fact_answer: bool = False,
) -> tuple[str, list[KpiClaimConflict]]:
    """全模式统一后处理，并返回改写前发现的 KPI 冲突（供日志/测试）。"""
    if fact_answer:
        # 事实问答：不用 execute_sql 行数当「班级参考人数」权威源（常为 Top-N）
        stats = extract_stats_authority_data(
            tool_calls, reports=reports, exec_results=None
        )
        if not stats:
            return scrub_fact_answer_headcount_noise(
                scrub_preview_headcount_claims(text)
            ), []
        conflicts = audit_summary_kpi_claims(text, stats)
        out = reconcile_summary_kpis(text, stats)
        out = scrub_residual_conflicting_values(out, conflicts, stats)
        return scrub_fact_answer_headcount_noise(out), conflicts

    stats = extract_stats_authority_data(
        tool_calls, reports=reports, exec_results=exec_results
    )
    if not stats:
        return scrub_preview_headcount_claims(text), []
    conflicts = audit_summary_kpi_claims(text, stats)
    out = reconcile_summary_kpis(text, stats)
    out = scrub_residual_conflicting_values(out, conflicts, stats)
    return out, conflicts


def reconcile_answer_with_artifacts(
    text: str,
    *,
    tool_calls: list[dict[str, Any]] | None = None,
    reports: list[dict[str, Any]] | None = None,
    exec_results: list[dict[str, Any]] | None = None,
    fact_answer: bool = False,
) -> str:
    """全模式统一后处理：有权威 KPI 则对账改写，否则清洗预览规模人数幻觉。"""
    out, _conflicts = reconcile_answer_with_artifacts_detailed(
        text,
        tool_calls=tool_calls,
        reports=reports,
        exec_results=exec_results,
        fact_answer=fact_answer,
    )
    return out


def extract_stats_authority_block(
    tool_calls: list[dict[str, Any]] | None = None,
    *,
    reports: list[dict[str, Any]] | None = None,
) -> str:
    """抽取权威 KPI 块，供 Summarizer 照抄（报告 HTML / _stats / compute_score_stats）。"""
    best = extract_stats_authority_data(tool_calls, reports=reports)
    best_content = ""
    best_count = _safe_int(best.get("count")) if best else None
    for tc in tool_calls or []:
        if not tc.get("success"):
            continue
        if str(tc.get("tool") or "") != "compute_score_stats_tool":
            continue
        content = str(tc.get("content") or "")
        data = tc.get("data") if isinstance(tc.get("data"), dict) else {}
        tc_count = _safe_int(data.get("count"))
        if best_count is not None and tc_count == best_count:
            best_content = content
            break
        if not best_content and "成绩统计完成" in content:
            best_content = content
    if best is None and not best_content:
        return ""
    lines = ["【报告权威 KPI（须原样照抄；禁止用样例/LIMIT 行数改写）】"]
    if best is not None:
        lines.append(
            f"- 参考人数 count={best.get('count')}；均分={best.get('avg')}；"
            f"满分={best.get('full_score')}；标准差={best.get('stdev')}；"
            f"及格线={best.get('pass_line')}；优秀线={best.get('excellent_line')}；"
            f"及格率={best.get('pass_rate')}%；优秀率={best.get('excellent_rate')}%"
        )
    if best_content:
        lines.append(f"- 统计工具原文：{best_content.strip()[:500]}")
    lines.append(
        "- **禁止**把下方「样例」行数或带 LIMIT/OFFSET 的「共 N 行」当成参考人数；"
        "有本块时人数、均分、及格率、优秀率一律以本块为准。"
    )
    return "\n".join(lines)


def format_sql_result_authority_notes(
    *,
    sql: str | None,
    row_count: int,
    sample_shown: int,
) -> str:
    """针对 execute_sql 结果的人数口径说明（防 LIMIT/OFFSET→20 人幻觉）。"""
    lines: list[str] = []
    if sql_looks_row_capped(sql):
        kind = "OFFSET 分页" if sql_looks_paginated(sql) else "LIMIT 截断"
        lines.append(
            f"⚠️ 本 SQL 含 {kind}：下列「共 {row_count} 行」仅为本次返回行数，"
            f"**禁止**当作班级/全体参考人数；总人数须用 compute_score_stats 的 count、"
            f"无 LIMIT/OFFSET 的 COUNT(*)/明细全量查询，或权威统计块中的 count。"
        )
    else:
        lines.append(
            f"权威行数：共 {row_count} 行（无 LIMIT/OFFSET 时可作为明细人数；"
            f"若另有权威统计块，以统计块的 count 为准）。"
        )
    if sample_shown > 0 and sample_shown < row_count:
        lines.append(
            f"样例仅预览前 {sample_shown} 行（非全量），**禁止**按样例表「数人头」写班级人数，"
            f"**禁止**写「所查看的 {sample_shown} 名」充当整体结论。"
        )
    elif sample_shown > 0:
        lines.append(
            f"样例展示 {sample_shown} 行（含系统预览上限），**禁止**把样例行数写成全班参考人数。"
        )
    return "\n".join(lines)


def format_tool_expert_sub_task_block(
    *,
    tool_calls: list[dict[str, Any]] | None = None,
    reports: list[dict[str, Any]] | None = None,
    final_answer: str = "",
) -> str:
    """ToolExpert 子任务的可读摘要（供 Summarizer 使用）。"""
    agg = collect_education_artifacts(tool_calls=tool_calls, reports=reports)
    lines = ["执行者：ToolExpert（教育报告工具链）"]
    stats_block = extract_stats_authority_block(tool_calls, reports=reports)
    if stats_block:
        lines.append(stats_block)
    if agg["has_item_data"] or agg["has_knowledge_data"]:
        lines.append(
            f"小题明细：{agg['item_count']} 题；知识点：{agg['knowledge_count']} 个；"
            f"成绩：{agg['score_count']} 条"
        )
    if agg["report_titles"]:
        lines.append("已生成 HTML 报告：" + "、".join(agg["report_titles"]))
    elif agg["has_diagnosis_report"]:
        lines.append("已生成 HTML 诊断报告")
    if final_answer.strip():
        lines.append(
            "工具结论：\n" + truncate_keeping_kpi_lines(final_answer.strip(), limit=1200)
        )
    if len(lines) == 1:
        lines.append("（工具已执行，未见结构化小题/知识点摘要）")
    return "\n".join(lines)


def format_education_pipeline_footer(report_data: dict[str, Any] | None) -> str:
    """全局教育学情产出脚注，防止 Summarizer 误判数据缺失。"""
    agg = collect_education_artifacts(report_data=report_data)
    if not (
        agg["has_item_data"]
        or agg["has_knowledge_data"]
        or agg["has_diagnosis_report"]
    ):
        return ""

    parts = ["## 教育学情产出摘要（全局）"]
    if agg["has_item_data"]:
        parts.append(f"- 小题级诊断数据：**已获取**（{agg['item_count']} 题）")
    if agg["has_knowledge_data"]:
        parts.append(f"- 知识点级诊断数据：**已获取**（{agg['knowledge_count']} 个）")
    if agg["report_titles"]:
        parts.append(f"- 已生成结构化 HTML 报告：{'、'.join(agg['report_titles'])}")
    elif agg["has_diagnosis_report"]:
        parts.append("- 已生成结构化 HTML 诊断报告")
    parts.append(
        "- **重要**：以上数据已在报告中完整呈现（小题明细、知识点掌握、重点干预等）。"
        "撰写结论时须引导用户查看报告详情，**禁止**声称「无法获取小题级/知识点级诊断数据」。"
    )
    parts.append(
        "- **人数口径**：以「报告权威 KPI」/「权威统计 count」为准；"
        "**禁止**把样例行数、LIMIT/OFFSET 本页「共 N 行」写成参考人数；"
        "均分/及格率/优秀率亦须与报告权威 KPI 一致。"
    )
    try:
        from src.agent.education.config_store import get_config

        cfg = get_config()
        pr, er = float(cfg.pass_ratio), float(cfg.excellent_ratio)
        parts.append(
            f"- **及格/优秀阈值（异常规则）**：及格 {round(pr * 100, 2)}%、优秀 {round(er * 100, 2)}%；"
            f"有卷面满分时及格线=满分×{pr}、优秀线=满分×{er}。"
            "**禁止**在结论中改用惯例 60%/85%（如 150 分卷写 90/127.5）。"
            "若统计结果已给出及格线/优秀线，必须原样采用；"
            "**禁止**把 SQL 里手写的 `>=90`/`127.5` 当成系统配置线。"
        )
    except Exception:
        pass
    return "\n".join(parts)


__all__ = [
    "KpiClaimConflict",
    "audit_summary_kpi_claims",
    "collect_education_artifacts",
    "extract_exec_authority_data",
    "extract_stats_authority_block",
    "extract_stats_authority_data",
    "extract_stats_from_report_html",
    "format_education_pipeline_footer",
    "format_sql_result_authority_notes",
    "format_tool_expert_sub_task_block",
    "reconcile_answer_with_artifacts",
    "reconcile_answer_with_artifacts_detailed",
    "reconcile_summary_kpis",
    "scrub_fact_answer_headcount_noise",
    "scrub_preview_headcount_claims",
    "scrub_residual_conflicting_values",
    "sql_looks_paginated",
    "sql_looks_row_capped",
    "truncate_keeping_kpi_lines",
]
