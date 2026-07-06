"""科目诊断报告——小题与知识点聚合、薄弱项识别（纯函数，不依赖 LLM）。"""

from __future__ import annotations

import ast
import json
from typing import Any


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt(v: Any) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.2f}"
    return str(v)


def _is_html_fragment(value: str) -> bool:
    s = value.strip().lower()
    return s.startswith("<table") or s.startswith("<p ") or s.startswith("<div")


def _coerce_row_list(value: Any) -> list[dict[str, Any]] | None:
    """将 list / JSON 字符串 / Python repr 转为 dict 列表。"""
    if isinstance(value, list):
        if value and all(isinstance(x, dict) for x in value):
            return [dict(x) for x in value]
        return None
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not s.startswith("["):
        return None
    for loader in (json.loads, ast.literal_eval):
        try:
            parsed = loader(s)
        except Exception:
            continue
        if isinstance(parsed, list) and parsed and all(isinstance(x, dict) for x in parsed):
            return [dict(x) for x in parsed]
    return None


def normalize_item_row(row: dict[str, Any]) -> dict[str, Any]:
    """统一班级聚合行与学生逐题行的字段名。"""
    r = dict(row)
    if r.get("full_score") is None and r.get("question_score") is not None:
        r["full_score"] = r["question_score"]
    if r.get("avg_score") is None and r.get("score") is not None:
        r["avg_score"] = r["score"]
    if r.get("score_rate") is None:
        fs = _num(r.get("full_score"))
        av = _num(r.get("avg_score"))
        if fs and av is not None:
            r["score_rate"] = round(av / fs * 100, 2)
    return r


def normalize_knowledge_row(row: dict[str, Any]) -> dict[str, Any]:
    r = dict(row)
    if r.get("question_count") is None and r.get("question_nos") is not None:
        r["question_count"] = r["question_nos"]
    return r


def build_item_table_html(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "<p class='edu-sub'>暂无小题数据</p>"
    norm = [normalize_item_row(r) for r in rows]
    score_label = "得分" if any("score" in r for r in rows) else "均分"
    body = "".join(
        f"<tr><td>{r.get('question_no', '')}</td>"
        f"<td>{r.get('knowledge_name') or '-'}</td>"
        f"<td>{_fmt(r.get('full_score'))}</td>"
        f"<td>{_fmt(r.get('avg_score'))}</td>"
        f"<td>{_fmt(r.get('score_rate'))}%</td></tr>"
        for r in norm
    )
    return (
        "<table class='edu-table'><thead><tr>"
        f"<th>题号</th><th>知识点</th><th>满分</th><th>{score_label}</th><th>得分率</th></tr></thead>"
        f"<tbody>{body}</tbody></table>"
    )


def build_knowledge_table_html(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "<p class='edu-sub'>暂无知识点数据</p>"
    norm = [normalize_knowledge_row(r) for r in rows]
    body = "".join(
        f"<tr><td>{r.get('knowledge_name', '')}</td>"
        f"<td>{r.get('question_count', r.get('question_nos', '-'))}</td>"
        f"<td>{_fmt(r.get('score_rate'))}%</td>"
        f"<td>{r.get('level') or _knowledge_level(_num(r.get('score_rate')))}</td></tr>"
        for r in norm
    )
    return (
        "<table class='edu-table'><thead><tr>"
        "<th>知识点</th><th>涉及题数</th><th>得分率</th><th>掌握水平</th></tr></thead>"
        f"<tbody>{body}</tbody></table>"
    )


def _knowledge_level(rate: float | None) -> str:
    if rate is None:
        return "未知"
    if rate >= 85:
        return "优秀"
    if rate >= 70:
        return "良好"
    if rate >= 60:
        return "及格"
    return "需加强"


def identify_weak_knowledge(
    rows: list[dict[str, Any]],
    *,
    weak_threshold: float = 60.0,
    max_items: int = 8,
) -> list[dict[str, Any]]:
    """按得分率升序返回薄弱知识点（得分率 < weak_threshold）。"""
    scored: list[tuple[float, dict[str, Any]]] = []
    for r in rows:
        rate = _num(r.get("score_rate"))
        if rate is None:
            continue
        scored.append((rate, r))
    scored.sort(key=lambda x: x[0])
    weak = [r for rate, r in scored if rate < weak_threshold]
    return weak[:max_items]


def identify_weak_items(
    rows: list[dict[str, Any]],
    *,
    weak_threshold: float = 60.0,
    max_items: int = 5,
) -> list[dict[str, Any]]:
    scored: list[tuple[float, dict[str, Any]]] = []
    for r in rows:
        rate = _num(r.get("score_rate"))
        if rate is None:
            continue
        scored.append((rate, r))
    scored.sort(key=lambda x: x[0])
    return [r for rate, r in scored if rate < weak_threshold][:max_items]


def build_diagnosis_summary(
    *,
    school_name: str = "",
    exam_name: str = "",
    subject_name: str = "",
    stats: dict[str, Any] | None = None,
    item_rows: list[dict[str, Any]] | None = None,
    knowledge_rows: list[dict[str, Any]] | None = None,
    weak_threshold: float = 60.0,
) -> str:
    stats = stats or {}
    item_rows = item_rows or []
    knowledge_rows = knowledge_rows or []
    weak_k = identify_weak_knowledge(knowledge_rows, weak_threshold=weak_threshold)
    weak_i = identify_weak_items(item_rows, weak_threshold=weak_threshold)

    scope_parts = [p for p in (school_name, exam_name, subject_name) if p]
    scope = " · ".join(scope_parts) if scope_parts else "本次考试"

    lines = [f"<p><strong>{scope}</strong> 科目诊断如下：</p>"]
    if stats.get("count"):
        lines.append(
            f"<p>共 {stats.get('count')} 人参考，均分 {_fmt(stats.get('avg'))}，"
            f"及格率 {_fmt(stats.get('pass_rate'))}%，优秀率 {_fmt(stats.get('excellent_rate'))}%。</p>"
        )
    if knowledge_rows:
        lines.append(
            f"<p>共涉及 <strong>{len(knowledge_rows)}</strong> 个知识点；"
            f"其中 <strong>{len(weak_k)}</strong> 个知识点得分率低于 {weak_threshold:g}% ，需重点加强。</p>"
        )
        if weak_k:
            names = "、".join(str(r.get("knowledge_name") or "") for r in weak_k[:5])
            lines.append(f"<p><strong>薄弱知识点：</strong>{names}。</p>")
    if weak_i:
        qnos = "、".join(f"第{r.get('question_no')}题" for r in weak_i[:5])
        lines.append(f"<p><strong>薄弱小题：</strong>{qnos}（得分率偏低）。</p>")
    if not weak_k and not weak_i and knowledge_rows:
        lines.append("<p>各知识点与小题得分率整体处于可控区间，建议保持现有复习节奏并关注临界题。</p>")
    return "\n".join(lines)


def build_diagnosis_recommendations(
    knowledge_rows: list[dict[str, Any]] | None = None,
    item_rows: list[dict[str, Any]] | None = None,
    *,
    weak_threshold: float = 60.0,
) -> str:
    knowledge_rows = knowledge_rows or []
    item_rows = item_rows or []
    weak_k = identify_weak_knowledge(knowledge_rows, weak_threshold=weak_threshold)
    weak_i = identify_weak_items(item_rows, weak_threshold=weak_threshold)

    items: list[str] = []
    for r in weak_k:
        name = str(r.get("knowledge_name") or "未知知识点")
        rate = _fmt(r.get("score_rate"))
        items.append(f"针对「{name}」（得分率 {rate}%）安排专项练习与错题回顾")
    for r in weak_i:
        kn = r.get("knowledge_name")
        qno = r.get("question_no")
        suffix = f"（关联知识点：{kn}）" if kn else ""
        items.append(f"精讲并变式训练第 {qno} 题{suffix}")
    if not items:
        return "<ul><li>整体掌握较好，建议以综合卷维持题感，并关注个别临界生的巩固。</li></ul>"
    lis = "".join(f"<li>{x}</li>" for x in items[:10])
    return f"<ul>{lis}</ul>"


def build_segment_table_html(segments: list[dict[str, Any]]) -> str:
    if not segments:
        return "<p class='edu-sub'>暂无分数段数据</p>"
    rows = "".join(
        f"<tr><td>{s.get('label', '')}</td><td>{s.get('count', 0)}</td>"
        f"<td>{_fmt(s.get('ratio'))}%</td></tr>"
        for s in segments
    )
    return (
        "<table class='edu-table'><thead><tr><th>分数段</th><th>人数</th><th>占比</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
    )


def _coerce_table_field(value: Any, builder: Any) -> Any:
    if isinstance(value, str) and _is_html_fragment(value):
        return value
    rows = _coerce_row_list(value)
    if rows is not None:
        return builder(rows)
    return value


def coerce_report_table_fields(data: dict[str, Any]) -> dict[str, Any]:
    """渲染前兜底：LLM 误将 list/JSON 填入 *_TABLE 占位符时自动转 HTML 表格。"""
    if not data:
        return {}
    out = dict(data)
    if "ITEM_TABLE" in out:
        out["ITEM_TABLE"] = _coerce_table_field(out["ITEM_TABLE"], build_item_table_html)
    if "KNOWLEDGE_TABLE" in out:
        val = out["KNOWLEDGE_TABLE"]
        if not (isinstance(val, str) and _is_html_fragment(val)):
            kn = _coerce_row_list(val)
            if kn is not None:
                out["KNOWLEDGE_TABLE"] = build_knowledge_table_html(enrich_knowledge_rows(kn))
    if "SEGMENT_TABLE" in out:
        out["SEGMENT_TABLE"] = _coerce_table_field(out["SEGMENT_TABLE"], build_segment_table_html)
    weak = out.get("WEAK_KNOWLEDGE_LIST")
    if isinstance(weak, list):
        out["WEAK_KNOWLEDGE_LIST"] = "、".join(str(x.get("knowledge_name") if isinstance(x, dict) else x) for x in weak)[:500]
    return out


def enrich_knowledge_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """为知识点行补充掌握水平标签。"""
    out: list[dict[str, Any]] = []
    for r in rows:
        row = dict(r)
        rate = _num(row.get("score_rate"))
        row["level"] = _knowledge_level(rate)
        out.append(row)
    return out


__all__ = [
    "build_diagnosis_recommendations",
    "build_diagnosis_summary",
    "build_item_table_html",
    "build_knowledge_table_html",
    "build_segment_table_html",
    "coerce_report_table_fields",
    "enrich_knowledge_rows",
    "identify_weak_items",
    "identify_weak_knowledge",
    "normalize_item_row",
]
