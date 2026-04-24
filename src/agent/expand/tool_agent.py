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
            tool_pack = template.bind(**bindings) if bindings else template
        else:
            tool_pack = build_default_toolpack(
                datasource_id=datasource_id,
                user_id=user_id,
            )

    return ToolAgent(
        llm_client=llm_client,
        tool_pack=tool_pack,
        max_react_rounds=max_react_rounds,
        **kwargs,
    )

