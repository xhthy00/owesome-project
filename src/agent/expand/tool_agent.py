"""ToolAgent：专门执行“工具型子任务”的轻量 ReAct Agent。"""

from __future__ import annotations

from typing import Any

from src.agent.core.profile import ProfileConfig
from src.agent.core.react_agent import ReActAgent
from src.agent.resource.manager import (
    DEFAULT_PACK_NAME,
    get_resource_manager,
    install_default_resources,
)
from src.agent.resource.tool.business import build_default_toolpack
from src.agent.resource.tool.pack import ToolPack

TOOL_AGENT_DESC = """[可用工具]
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
2. 从 context 的 `report_data.sub_tasks`（上游 DataAnalyst 产出的 exec_result /
   reports / 统计数据）中，按 `data_keys` 逐项组装 `data` 字典；缺字段时再用
   `execute_sql` / `compute_score_stats_tool` / `compute_rankings_tool` 等补齐；
   图表字段（形如 `XXX_CHART`）用 `build_chart_option_tool` 生成 JSON 字符串填入；
3. 调 `render_html_report(template_name=..., data=..., title=...)` 生成 HTML；
4. 调 `terminate(final_answer="学情报告已生成")` 结束。

**综合分析报告（多次考试）快捷路径**：当 `template_name` 为
`education/comprehensive.html` 时，**不要自己调 render_html_report、也不要回填
data 字典**——直接调
`build_comprehensive_report_data_tool(records=..., exam_order=..., class_name=...)`，
该工具内部已完成「数据组装 + 模板渲染 + HTML 上报」，报告会自动推送到前端。
调完只需 `terminate(final_answer="综合分析报告已生成")` 结束。

**单个学生多次考试分析报告快捷路径**：当子任务涉及某一学生的历次考试分析时，
直接调 `build_student_exam_report_data_tool(student_name=..., records=..., exam_order=...)`，
`student_name` **必须与用户问题中的学生一致**（如「学生001」），且**只生成一份报告**。
`records` 须含全班数据（用于排名/均分），工具内部会过滤目标学生。
调完只需 `terminate`，**禁止**为其他学生再调一次报告工具。
`records` 每条形如 `{exam, student, subjects:{科目:分数}, total}`，可由上游
execute_sql 结果直接构造或用本工具的 rows+columns 长表入参自动聚合。
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
            "完成后必须 terminate",
        ],
        desc=TOOL_AGENT_DESC,
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
            tool_pack = template.bind(**bindings) if bindings else template
        else:
            tool_pack = build_default_toolpack(
                datasource_id=datasource_id,
                user_id=user_id,
                workspace_oid=workspace_oid,
            )

    return ToolAgent(
        llm_client=llm_client,
        tool_pack=tool_pack,
        max_react_rounds=max_react_rounds,
        **kwargs,
    )

