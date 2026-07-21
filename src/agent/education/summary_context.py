"""为 Summarizer 补充教育学情产出上下文，避免与 HTML 报告矛盾。"""

from __future__ import annotations

import re
from typing import Any

from src.agent.education.query_parse import find_upstream_fetch_data

_DIAGNOSIS_REPORT_TOOLS = frozenset({
    "build_subject_diagnosis_sections_tool",
    "build_subject_diagnosis_report_tool",
    "build_student_subject_diagnosis_tool",
    "build_diagnostic_report_data_tool",
    "build_citywide_exam_analysis_report_tool",
})

_SQL_OFFSET_RE = re.compile(r"\bOFFSET\b", re.IGNORECASE)
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


def extract_stats_authority_block(
    tool_calls: list[dict[str, Any]] | None = None,
) -> str:
    """从 compute_score_stats_tool 抽取权威 KPI，供 Summarizer 照抄。"""
    best: dict[str, Any] | None = None
    best_content = ""
    for tc in tool_calls or []:
        if not tc.get("success"):
            continue
        if str(tc.get("tool") or "") != "compute_score_stats_tool":
            continue
        data = tc.get("data")
        if isinstance(data, dict) and not data.get("error") and data.get("count") is not None:
            best = data
            best_content = str(tc.get("content") or "")
            break
        content = str(tc.get("content") or "")
        if "成绩统计完成" in content:
            best_content = content
    if best is None and not best_content:
        return ""
    lines = ["【权威统计（compute_score_stats_tool，须照抄）】"]
    if best is not None:
        lines.append(
            f"- 人数 count={best.get('count')}；均分={best.get('avg')}；"
            f"满分={best.get('full_score')}；"
            f"及格线={best.get('pass_line')}；优秀线={best.get('excellent_line')}；"
            f"及格率={best.get('pass_rate')}%；优秀率={best.get('excellent_rate')}%"
        )
    if best_content:
        lines.append(f"- 原文：{best_content.strip()[:500]}")
    lines.append(
        "- **禁止**把下方「样例」行数或带 OFFSET 的「共 N 行」当成班级总人数；"
        "有本块时人数与分数线以本块为准。"
    )
    return "\n".join(lines)


def format_sql_result_authority_notes(
    *,
    sql: str | None,
    row_count: int,
    sample_shown: int,
) -> str:
    """针对 execute_sql 结果的人数口径说明（防 OFFSET→12 人幻觉）。"""
    lines: list[str] = []
    if sql_looks_paginated(sql):
        lines.append(
            f"⚠️ 本 SQL 含 OFFSET：下列「共 {row_count} 行」仅为本页返回行数，"
            f"**禁止**当作班级/全体总人数；总人数须用 compute_score_stats 的 count、"
            f"无 OFFSET 的 COUNT/明细查询、或查询结论中已写明的「参考人数」。"
        )
    else:
        lines.append(
            f"权威行数：共 {row_count} 行（无 OFFSET 时可作为明细人数；"
            f"若另有权威统计块，以统计块的 count 为准）。"
        )
    if sample_shown > 0 and sample_shown < row_count:
        lines.append(
            f"样例仅预览前 {sample_shown} 行（非全量），**禁止**按样例表「数人头」写班级人数，"
            f"**禁止**写「所查看的 {sample_shown} 名」充当整体结论。"
        )
    elif sample_shown > 0:
        lines.append(
            f"样例展示 {sample_shown} 行；若上文标明 OFFSET 分页，仍不得把样例/本页行数当作全班人数。"
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
    stats_block = extract_stats_authority_block(tool_calls)
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
        "- **人数口径**：班级总人数以「权威统计 count」或无 OFFSET 明细的「共 N 行」或结论中的"
        "「参考人数」为准；**禁止**把样例行数、OFFSET 本页行数写成全班人数。"
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
    "collect_education_artifacts",
    "extract_stats_authority_block",
    "format_education_pipeline_footer",
    "format_sql_result_authority_notes",
    "format_tool_expert_sub_task_block",
    "sql_looks_paginated",
    "truncate_keeping_kpi_lines",
]
