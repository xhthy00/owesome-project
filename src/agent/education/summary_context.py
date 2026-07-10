"""为 Summarizer 补充教育学情产出上下文，避免与 HTML 报告矛盾。"""

from __future__ import annotations

from typing import Any

from src.agent.education.query_parse import find_upstream_fetch_data

_DIAGNOSIS_REPORT_TOOLS = frozenset({
    "build_subject_diagnosis_sections_tool",
    "build_subject_diagnosis_report_tool",
    "build_student_subject_diagnosis_tool",
    "build_diagnostic_report_data_tool",
    "build_citywide_exam_analysis_report_tool",
})


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


def format_tool_expert_sub_task_block(
    *,
    tool_calls: list[dict[str, Any]] | None = None,
    reports: list[dict[str, Any]] | None = None,
    final_answer: str = "",
) -> str:
    """ToolExpert 子任务的可读摘要（供 Summarizer 使用）。"""
    agg = collect_education_artifacts(tool_calls=tool_calls, reports=reports)
    lines = ["执行者：ToolExpert（教育报告工具链）"]
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
        lines.append(f"工具结论：{final_answer.strip()[:400]}")
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
    return "\n".join(parts)


__all__ = [
    "collect_education_artifacts",
    "format_education_pipeline_footer",
    "format_tool_expert_sub_task_block",
]
