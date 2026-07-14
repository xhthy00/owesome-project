"""ToolAgent：专门执行“工具型子任务”的轻量 ReAct Agent。"""

from __future__ import annotations

from typing import Any

from src.agent.core.profile import ProfileConfig
from src.agent.core.react_agent import ReActAgent
from src.agent.core.agent import AgentMessage
from src.agent.education.query_parse import format_scope_constraints
from src.agent.resource.manager import (
    DEFAULT_PACK_NAME,
    get_resource_manager,
    install_default_resources,
)
from src.agent.resource.tool.business import build_default_toolpack
from src.agent.resource.tool.pack import ToolPack

TOOL_AGENT_DESC = """[分析范围约束]
{{scope_constraints}}

[上游查数结果 — 必须优先使用]
{{upstream_report_data}}

[可用工具]
{{tools_prompt}}

[输出协议 - 严格]
每一轮回复必须是单个 JSON 对象（可包在 ```json ... ``` 代码块里），形如：
{
  "tool": "<工具名，严格匹配上面列表>",
  "args": { "<arg_name>": <value>, ... }
}
严禁输出 `<think>` 标签、自然语言解释或任何 JSON 之外文本。

[职责]
你是 ToolExpert：擅长“纯工具操作 + 计算后处理”类任务。
- 如果子任务只需要算术/比例/换算，优先用 `calculate`；
- 如果子任务需要取数，使用 list/describe/sample/execute_sql；
- 如果用户要求可视化报告/分析报告/图表页面/HTML 报告，使用 `render_html_report` 返回最终 HTML；

[教育学情报告组装 — 严格流程]
当子任务是"组装学情/成绩分析 HTML 报告"时，**严禁直接写出 HTML 文档、
Word/PDF 内容或自然语言报告正文**；必须按以下工具调用流程完成：
1. 先调 `select_report_template_tool(report_type, audience)` 获取
   `template_name`（形如 `education/xxx.html`）与 `data_keys`（模板需要的字段列表）；
2. **优先**从上方「上游查数结果」与 context 的 `report_data.sub_tasks` 中，
   按 `data_keys` 逐项组装 `data` 字典；上游 DataAnalyst 已按学校/班级/考试过滤时，
   **禁止**再调 `execute_sql` 查全量、其他学校或多班合并数据；
   `EXAM_NAME` / `SCOPE` / 参考人数必须与上游一致；
   仅当上游确实缺字段时，才用 `execute_sql` / `compute_score_stats_tool` /
   `compute_rankings_tool` 等补齐，且 SQL 仍须遵守范围约束；
   - **单个学生 + 单次考试 + 科目/知识点分析** → 调 `build_student_subject_diagnosis_tool(student_id=..., subject_name=..., exam_name=..., render=true)`，**禁止** `build_subject_diagnosis_sections_tool`（班级聚合报告）；
   **科目诊断报告（班级/学校聚合，含小题明细）— 必须分步、工具链可见**：
   1. **先调** `fetch_subject_diagnosis_data_tool(school_name=..., subject_name=...,
      exam_name=..., class_name=...)` 查询 `tb_score_detail` 小题与知识点（观察返回的
      SQL 执行记录与 item_rows 条数），然后 **terminate**（**禁止**同子任务再渲染）；
   2. 调 `build_subject_diagnosis_sections_tool(school_name=..., exam_name=...,
      subject_name=..., class_name=..., render=true)`
      **一步完成 stats 计算 + 组装 + HTML 渲染并推送前端**（工具自动从本轮/上游
      fetch 结果提取 item_rows 并计算 KPI；**禁止**手传 fetch_data / item_rows /
      knowledge_rows——大字典会截断成空表；**无需**先调 compute_score_stats_tool）；
   3. 调 `terminate(final_answer="科目诊断报告已生成")` 结束。
   **禁止**将 item_rows 原始 list 直接填入 ITEM_TABLE（须为 HTML 表格；若误传 list，
   系统会兜底转表格，但仍应优先走 sections 工具）。
   若仅需快速生成且接受工具链不展示 SQL，可改调 `build_subject_diagnosis_report_tool`
   （内部仍会查小题，但不在工具链单独显示 fetch）。
   **禁止**自行写小题/知识点 JOIN SQL、**禁止**跳过 fetch 直接 render（会导致工具链
   无小题查询记录且易漏数据）。
   **禁止**在 fetch 已成功返回后再次调用 `fetch_subject_diagnosis_data_tool`（相同参数
   会被缓存跳过，但仍浪费 ReAct 轮次）；应直接进入 sections / terminate 步骤。
   **禁止**sections 已成功渲染后仍调 `select_report_template` / `build_chart_option` /
   `render_html_report`（报告已由 sections 工具推送）。
   **全市 + 考试 + 科目结构化诊断报告**（含区县对比、详细小题/知识点）— 3 步分工：
   - **子任务 2（fetch）**：仅 `fetch_subject_diagnosis_data_tool` → `terminate`；
     **禁止**同子任务调 `build_*` 渲染（否则会出多份报告）；
   - **子任务 3（组装）**：**仅** `build_diagnostic_report_data_tool(scope_label=全市, exam_name=..., subject_name=..., render=true)` → `terminate`；
     **禁止**在子任务 3 再调 `fetch_subject_diagnosis_data_tool`（工具自动读取上游成绩与 fetch 数据）；
     **禁止**手传 `score_rows` / `fetch_data` / `item_rows`（大字典会截断成空表）；
   **禁止**在 fetch 子任务中调 `build_diagnostic_report_data_tool(render=true)`（工具层会拦截）。
   图表字段（形如 `XXX_CHART`）用 `build_chart_option_tool` 生成 JSON 字符串填入；
3. 调 `render_html_report(template_name=..., data=..., title=...)` 生成 HTML；
   **禁止**传 `file_path` 捏造输出路径（如 `data_analyst/xxx_report.html`）——
   `file_path` 只读工作区已有 HTML，学情报告必须走 `template_name` + `data`；
4. 调 `terminate(final_answer="学情报告已生成")` 结束。

**综合分析报告（多次考试）快捷路径**：当子任务是综合分析 / `education/comprehensive.html` 时，
**禁止** `render_html_report`、**禁止**手填 PROGRESS_TABLE / STUDENT_ARCHIVE_TABLE——
直接调 `build_comprehensive_report_data_tool(class_name=...)`（可省略 records；
完整学生×考试明细由工具自动读取）。该工具会生成真实的「进步/退步学生 TOP5」
与「每位学生详细档案」，不会用班级 KPI 冒充。调完 `terminate` 即可。

**单个学生多次考试分析报告快捷路径**：当子任务涉及某一学生的历次考试分析时，
直接调 `build_student_exam_report_data_tool(student_name=..., class_name=...)`，
`student_name` **必须与用户问题中的学生一致**（如「学生001」），且**只生成一份报告**。
全班历次数据由工具自动从上游 SQL 读取（用于排名/均分），**禁止**只传 preview 行。
调完只需 `terminate`，**禁止**为其他学生再调一次报告工具。
**切勿把该工具返回的大 data 字典再塞进 render_html_report**——那会因 JSON 过长
被截断成数组/标量而报错。

绝不要返回 `{"report": ...}` / `{"html": ...}` / 数组 / Markdown 报告正文这类
非工具结构——每一轮必须是 `{"tool": "...", "args": {...}}`。
特别注意：`render_html_report` 调用的 args 必须同时包含 `template_name` 与
`data` 两个字段；**不要把 data 字典本身当作根对象输出**
（错误示例：`{"REPORT_TITLE": ..., "SUBJECT_RADAR_CHART": ...}`，
正确示例：`{"tool": "render_html_report", "args": {"template_name": "education/student_profile.html", "data": {"REPORT_TITLE": ..., "SUBJECT_RADAR_CHART": ...}}}`）。

- 信息足够后，调用 `terminate` 返回最终答案。
"""


class ToolAgent(ReActAgent):
    profile = ProfileConfig(
        name="ToolExpert",
        role="工具专家",
        goal="通过工具调用快速完成可验证的子任务。",
        constraints=[
            "输出严格遵守 JSON 协议",
            "优先走工具调用，不做空想推理",
            "报告范围必须与用户指定的学校/班级/考试一致，禁止用其他班级或全校数据",
            "组装报告时优先使用上游 DataAnalyst 查数结果，禁止无必要重复 execute_sql",
            "同一工具相同参数禁止重复调用（fetch/sections 成功后必须进入下一步）",
            "完成后必须 terminate",
        ],
        desc=TOOL_AGENT_DESC,
    )

    def _build_prompt_variables(self, reply: AgentMessage) -> dict[str, Any]:
        base = super()._build_prompt_variables(reply)
        raw = dict(reply.context or {}).get("constraints")
        constraints = raw if isinstance(raw, dict) else {}
        base["scope_constraints"] = format_scope_constraints(constraints)
        base["upstream_report_data"] = _format_upstream_report_data(
            constraints.get("report_data")
        )
        return base


def _format_upstream_report_data(report_data: Any, *, max_rows: int = 8) -> str:
    """把上游 DataAnalyst 子任务产出格式化为 ToolExpert 可读摘要。"""
    if not isinstance(report_data, dict):
        return "（无上游查数结果；若必须查数，须严格遵守范围约束）"
    sub_tasks = report_data.get("sub_tasks") or []
    if not sub_tasks:
        return "（无上游子任务数据；若必须查数，须严格遵守范围约束）"
    blocks: list[str] = []
    for st in sub_tasks:
        if st.get("sub_task_agent") == "ToolExpert":
            continue
        idx = st.get("sub_task_index")
        task = str(st.get("sub_task") or "").strip()
        header = f"### 上游子任务[{idx}]：{task or '(无描述)'}"
        parts = [header]
        er = st.get("exec_result") or {}
        cols = list(er.get("columns") or [])
        rows = list(er.get("rows") or [])
        if cols:
            parts.append(f"列：{', '.join(str(c) for c in cols)}")
        if rows:
            row_count = er.get("row_count") or len(rows)
            parts.append(f"行数：{row_count}（完整数据由工具自动读取，勿仅复制 preview 行）")
            preview = rows[:max_rows]
            for i, row in enumerate(preview):
                if isinstance(row, dict):
                    cells = [f"{c}={row.get(c, '')}" for c in cols]
                    parts.append(f"  [{i}] " + ", ".join(cells))
                else:
                    parts.append(f"  [{i}] {row}")
            if len(rows) > max_rows:
                parts.append(f"  … 另有 {len(rows) - max_rows} 行未展示")
        sql = str(st.get("sql") or "").strip()
        if sql:
            parts.append(f"SQL：{sql[:500]}")
        fa = str(st.get("final_answer") or "").strip()
        if fa:
            parts.append(f"结论摘要：{fa[:800]}")
        blocks.append("\n".join(parts))
    if not blocks:
        return "（上游尚无 DataAnalyst 查数结果；若必须查数，须严格遵守范围约束）"
    return (
        "以下为上游 DataAnalyst 已查得的过滤后数据，组装报告时必须以此为准，"
        "EXAM_NAME/SCOPE/人数/统计指标均须与之一致：\n\n" + "\n\n".join(blocks)
    )


def build_tool_agent(
    *,
    llm_client: Any,
    datasource_id: int | None = None,
    user_id: int | None = None,
    workspace_oid: int | None = 1,
    tool_pack: ToolPack | None = None,
    pack_name: str = DEFAULT_PACK_NAME,
    max_react_rounds: int | None = None,
    report_data: dict[str, Any] | None = None,
    sub_task: str = "",
    tool_runtime_ctx: dict[str, Any] | None = None,
    **kwargs: Any,
) -> ToolAgent:
    if tool_pack is None:
        mgr = get_resource_manager()
        if not mgr.has_pack(pack_name) and pack_name == DEFAULT_PACK_NAME:
            install_default_resources()

        if mgr.has_pack(pack_name):
            template = mgr.get_pack(pack_name)
            bindings: dict[str, Any] = {}
            if datasource_id is not None:
                bindings["datasource_id"] = datasource_id
            if user_id is not None:
                bindings["user_id"] = user_id
            if workspace_oid is not None:
                bindings["workspace_oid"] = workspace_oid
            if report_data is not None:
                bindings["report_data"] = report_data
            if sub_task:
                bindings["sub_task"] = sub_task
            if tool_runtime_ctx is not None:
                bindings["tool_runtime_ctx"] = tool_runtime_ctx
            tool_pack = template.bind(**bindings) if bindings else template
        else:
            tool_pack = build_default_toolpack(
                datasource_id=datasource_id,
                user_id=user_id,
                workspace_oid=workspace_oid,
                report_data=report_data,
                sub_task=sub_task,
                tool_runtime_ctx=tool_runtime_ctx,
            )

    return ToolAgent(
        llm_client=llm_client,
        tool_pack=tool_pack,
        max_react_rounds=max_react_rounds,
        **kwargs,
    )

