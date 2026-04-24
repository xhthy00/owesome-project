"""DataAnalystAgent：用 ReAct + 工具链完成"自然语言 → SQL → 结果"的数据分析师。

工作流（ReAct 多轮示例）：

    round 1: list_tables           -> 看到候选表
    round 2: describe_table users  -> 看到列信息
    round 3: sample_rows users     -> 看到真实数据
    round 4: execute_sql SELECT …  -> 拿到结果
    round 5: terminate {...}       -> 给出最终答案（含 SQL + 结论）

关键设计：
- Prompt 强制 JSON 输出，与 ToolAction 解析器完美对齐；
- 明确告诉模型"工具失败也是有效 observation，不要重复同一个错误"；
- terminate 的 final_answer 约定包含"结论 + SQL + 关键数字"，便于前端直接展示。
"""

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

DATA_ANALYST_DESC = """[可用工具]
{{tools_prompt}}

[输出协议 - 严格]
每一轮回复必须是单个 JSON 对象（可包在 ```json ... ``` 代码块里），形如：
{
  "thoughts": "<一句话说明本轮要做什么、为什么>",
  "tool": "<工具名，严格匹配上面列表>",
  "args": { "<arg_name>": <value>, ... }
}
不要输出 JSON 以外的额外解释。

[工作原则]
1. 未见过真实 Schema/样例前，不要凭记忆写 SQL。优先顺序：
   - **表多或问题含明确业务关键词** → `find_related_tables`（按关键词召回 top-K，省 token）；
   - 否则 → `list_tables` 看全量；
   - → `describe_table` 看字段 → `sample_rows` 看真实值 → `execute_sql` 查询。
2. 所有 SQL 必须是 SELECT（只读）。execute_sql 若失败，observation 里会带
   error 文本——**仔细阅读 error，改写 SQL 后再试**，不要重复同一个错误。
3. 表名、列名严格采自 Schema，不臆造；采样若为空，先扩大筛选而非立刻放弃。
4. 涉及 **百分比 / 同比环比 / 均值 / 加权** 等后处理算术，**优先用 `calculate`
   工具**求值——LLM 心算易错，沙盒求值器结论可验证。例：
   拿到 `去年 1000 / 今年 1234` 后不要直接写"增长 23.4%"，先调
   `calculate("(1234-1000)/1000*100")` 再引用结果。
5. 当已能回答用户问题，调用 `terminate`，``final_answer`` 里包含：
   - 一段中文结论（先结论后依据）；
   - 最终使用的 SQL（用 ```sql 代码块 包裹）；
   - 若适用，关键数值摘要（如 "共 37 条，Top1 为 …"）。
6. 当用户要求“可视化报告/分析报告/图表页面/HTML 报告”时，优先调用
   `render_html_report` 产出 HTML，再 `terminate` 简短说明已生成报告。
   报告类任务在调用 `render_html_report` 前不要直接 terminate。
7. 轮数有上限——尽量每一步都向结论推进，避免重复探查同一张表。"""


class DataAnalystAgent(ReActAgent):
    profile = ProfileConfig(
        name="DataAnalyst",
        role="资深数据分析师",
        goal="基于提供的工具，把用户自然语言问题转化为可执行 SQL 并给出最终结论。",
        constraints=[
            "SQL 必须是只读 SELECT",
            "输出严格遵守 JSON 协议",
            "结论必须以工具执行结果为依据，不得臆造数据",
        ],
        desc=DATA_ANALYST_DESC,
    )


def build_data_analyst(
    *,
    llm_client: Any,
    datasource_id: int | None = None,
    user_id: int | None = None,
    tool_pack: ToolPack | None = None,
    pack_name: str = DEFAULT_PACK_NAME,
    max_react_rounds: int | None = None,
    **kwargs: Any,
) -> DataAnalystAgent:
    """快捷工厂：组装 DataAnalyst + 默认 ToolPack + 运行时 bindings。

    工具来源的优先级（从高到低）：

    1. 显式传入的 ``tool_pack``——调用方完全控制；
    2. ``ResourceManager`` 里按 ``pack_name`` 注册的**模板 pack** +
       ``.bind(datasource_id, user_id)``——这是标准路径，模板 pack 在
       ``install_default_resources()``（lifespan 启动阶段）注册；
    3. 兜底：直接用 ``build_default_toolpack(...)`` 即时构造——仅当
       ``ResourceManager`` 里没有 ``pack_name``（比如单测未跑 lifespan）时使用。

    第 2、3 路径行为等价，但第 2 路径省掉每次请求重新构造 8 个 FunctionTool
    对象的开销；第 3 路径保证"就算忘了跑 lifespan，代码也不炸"。
    """
    if tool_pack is None:
        mgr = get_resource_manager()
        # 兜底：未安装默认资源时静默 install 一次（幂等），避免调用方漏掉 lifespan
        # 就整个 Agent 路径瘫痪——防御式编程。
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
            # 非默认 pack 名且未注册——走即时构造兜底（现阶段也只有 default 一个实现）
            tool_pack = build_default_toolpack(
                datasource_id=datasource_id,
                user_id=user_id,
            )
    return DataAnalystAgent(
        llm_client=llm_client,
        tool_pack=tool_pack,
        max_react_rounds=max_react_rounds,
        **kwargs,
    )
