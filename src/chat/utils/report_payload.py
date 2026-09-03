"""从 tool_calls 抽出 HTML 报告，供落库与审核在 reports 列为空时回填。"""

from __future__ import annotations

from typing import Any


def extract_reports_from_tool_calls(tool_calls: Any) -> list[dict[str, Any]]:
    """与前端 ``extractReportFromToolData`` 对齐：``output_type=html`` 或 build_* 工具。"""
    if not isinstance(tool_calls, list):
        return []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for tc in tool_calls:
        if not isinstance(tc, dict) or tc.get("success") is False:
            continue
        data = tc.get("data")
        if not isinstance(data, dict) or data.get("error"):
            continue
        html = str(data.get("html") or "").strip()
        if not html:
            continue
        tool = str(tc.get("tool") or "")
        if data.get("output_type") != "html" and tool != "render_html_report" and not tool.startswith(
            "build_"
        ):
            continue
        if html in seen:
            continue
        seen.add(html)
        item: dict[str, Any] = {
            "title": str(data.get("title") or "Report"),
            "html": html,
            "review_status": "pending",
        }
        if data.get("mode"):
            item["mode"] = data.get("mode")
        if tc.get("sub_task_index") is not None:
            item["sub_task_index"] = tc.get("sub_task_index")
        if data.get("report_type"):
            item["report_type"] = data.get("report_type")
        if data.get("report_type_label"):
            item["report_type_label"] = data.get("report_type_label")
        out.append(item)
    return out


def coalesce_record_reports(reports: Any, tool_calls: Any) -> list[dict[str, Any]]:
    """优先用已落库的 reports；为空则从 tool_calls 回填。"""
    if isinstance(reports, list) and reports:
        cleaned: list[dict[str, Any]] = []
        for item in reports:
            if not isinstance(item, dict):
                continue
            row = dict(item)
            if "review_status" not in row:
                row["review_status"] = "pending"
            cleaned.append(row)
        if cleaned:
            return cleaned
    return extract_reports_from_tool_calls(tool_calls)


__all__ = ["coalesce_record_reports", "extract_reports_from_tool_calls"]
