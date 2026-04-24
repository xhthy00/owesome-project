"""PlannerAgent：把用户问题拆成若干子任务，交给下游 DataAnalyst 逐个执行。

设计原则（极简版）：

- Planner 的输出只有一个 ``plans: string[]``——不区分 title/content，不带 agent
  字段。我们只有一个 DataAnalyst，不需要多字段装腔；
- Planner 不调工具、不看 schema。它只做**语义分解**——真正的表结构探查由
  DataAnalyst 的 ReAct 循环去做（Planner 看 schema 会让 prompt 爆炸 + 引入多轮
  LLM 交互，性价比极低）；
- 对简单问题，Planner 允许且**鼓励**返回 1 个 sub_task（即 ``plans == [question]``），
  runner 会识别这种情况、前端可以选择不渲染任务列表。

输出契约（严格 JSON，可包在 ```json 代码块里）::

    {
      "thoughts": "<一句话说明分解思路>",
      "plans": ["<sub task 1>", "<sub task 2>", ...]
    }

失败回落：JSON 解析失败、plans 非数组、plans 为空 → 返回
``[question_itself]``（不中断 team 流水线）。
"""

from __future__ import annotations

import logging
from typing import Any

from src.agent.core.action.base import Action, ActionOutput
from src.agent.core.base_agent import ConversableAgent
from src.agent.core.profile import ProfileConfig
from src.agent.util.json_parser import parse_json_tolerant

logger = logging.getLogger(__name__)

# 再多的子任务大概率是 Planner 失控了——DataAnalyst 自己的 ReAct 本来就能处理
# 多 SQL，我们没必要让 Planner 生成十几个独立子任务。超出则截断 + 打日志。
_MAX_PLANS = 6
_DEFAULT_SUB_TASK_AGENT = "DataAnalyst"
_TOOL_EXPERT_AGENT = "ToolExpert"
_TOOL_EXPERT_HINTS = (
    "html", "report", "dashboard", "template", "web page", "webpage",
    "网页", "页面", "报告", "可视化报告", "图文报告",
)

PLANNER_DESC = """[你的职责]
把用户问题拆成可独立执行的子任务，每个子任务都应该可以交给一个**只能写单条 SQL**
的数据分析师独立完成。拆解的粒度宁粗勿细——多数简单问题其实只对应 1 个子任务。

[用户问题]
{{question}}

[输出 - 严格 JSON]
只输出一个 JSON 对象（可用 ```json 代码块包裹），字段：
{
  "thoughts": "<一句话说明你的分解思路>",
  "plans": [
    "<子任务 1 描述>",
    {"task": "<子任务 2 描述>", "sub_task_agent": "ToolExpert"},
    ...
  ]
}

[分解原则]
1. 若问题是单一查询（如"用户有多少"、"本月销量 TOP 5"），返回 plans=[原问题] 即可；
2. 若问题需要对比/趋势/因果分析（如"Q2/Q3 销售差异及原因"），拆成 2~4 个子任务；
3. 子任务之间应尽量独立——不要让后一个依赖前一个的具体数值；
4. 绝对不要超过 6 个子任务；拆不动就合并；
5. 不要在子任务里写 SQL 或表名——那是 DataAnalyst 的工作，你只写"查什么"；
6. 对“可视化报告/分析报告/图表页面/HTML 报告”类子任务，优先标 `sub_task_agent`
   为 ToolExpert；其余场景默认 DataAnalyst，纯计算/工具操作也可标 ToolExpert。"""


class PlanAction(Action):
    name = "plan"

    async def run(self, ai_message: str, question: str = "", **kwargs: Any) -> ActionOutput:
        try:
            parsed = parse_json_tolerant(ai_message)
        except ValueError as e:
            logger.info("PlanAction JSON parse failed: %s", e)
            return _fallback_single_plan(question, f"JSON parse failed: {e}")

        if not isinstance(parsed, dict):
            return _fallback_single_plan(question, "LLM output not a JSON object")

        raw_plans = parsed.get("plans")
        if not isinstance(raw_plans, list) or not raw_plans:
            return _fallback_single_plan(question, "plans field is not a non-empty list")

        items = [_normalize_plan_item(item) for item in raw_plans]
        items = [it for it in items if it is not None]
        if not items:
            return _fallback_single_plan(question, "all plans entries are empty")

        if len(items) > _MAX_PLANS:
            logger.info("PlanAction trimmed plans from %d to %d", len(items), _MAX_PLANS)
            items = items[:_MAX_PLANS]

        plans = [it["task"] for it in items]
        plan_agents = [it["sub_task_agent"] for it in items]

        return ActionOutput(
            is_exe_success=True,
            content=f"计划共 {len(plans)} 个子任务",
            action=self.name,
            thoughts=parsed.get("thoughts"),
            extra={"plans": plans, "plan_agents": plan_agents},
            terminate=True,  # Planner 不走 ReAct，单轮即终
        )


def _normalize_plan_item(item: Any) -> dict[str, str] | None:
    if item is None:
        return None
    if isinstance(item, str):
        text = item.strip()
        if not text:
            return None
        return {"task": text, "sub_task_agent": _infer_sub_task_agent(text)}
    if isinstance(item, dict):
        text = str(item.get("task") or item.get("plan") or "").strip()
        if not text:
            return None
        raw_agent = str(item.get("sub_task_agent") or "").strip()
        if raw_agent == _TOOL_EXPERT_AGENT:
            sub_task_agent = _TOOL_EXPERT_AGENT
        elif raw_agent == _DEFAULT_SUB_TASK_AGENT:
            sub_task_agent = _DEFAULT_SUB_TASK_AGENT
        else:
            sub_task_agent = _infer_sub_task_agent(text)
        return {"task": text, "sub_task_agent": sub_task_agent}
    text = str(item).strip()
    if not text:
        return None
    return {"task": text, "sub_task_agent": _infer_sub_task_agent(text)}


def _infer_sub_task_agent(task: str) -> str:
    lowered = (task or "").lower()
    if any(h in lowered for h in _TOOL_EXPERT_HINTS):
        return _TOOL_EXPERT_AGENT
    return _DEFAULT_SUB_TASK_AGENT


def _fallback_single_plan(question: str, reason: str) -> ActionOutput:
    """拆解失败 → 原问题作为唯一子任务。保证 team 流水线不断。"""
    q = (question or "").strip() or "（原始问题）"
    return ActionOutput(
        is_exe_success=True,
        content=f"计划回落为 1 个子任务（{reason}）",
        action="plan",
        extra={"plans": [q], "plan_agents": [_DEFAULT_SUB_TASK_AGENT]},
        terminate=True,
    )


class PlannerAgent(ConversableAgent):
    profile = ProfileConfig(
        name="Planner",
        role="任务规划师",
        goal="把用户问题拆成可独立执行的数据查询子任务。",
        constraints=[
            "只输出单个 JSON 对象，不要额外解释",
            "子任务不超过 6 个；拿不准就合并",
            "子任务描述里不要写 SQL、表名、字段名",
        ],
        desc=PLANNER_DESC,
    )
    actions: list[Action] = [PlanAction()]
    max_retry_count: int = 1  # Action 自带 fallback，不需要框架重试

    async def act(
        self,
        message: Any,
        sender: Any,
        reviewer: Any | None = None,
        **kwargs: Any,
    ) -> ActionOutput:
        """把 question 透传给 PlanAction，让 fallback 能拿到原问题。"""
        if not self.actions:
            return ActionOutput(content=message.content or "", is_exe_success=True)
        action = self.actions[0]
        return await action.run(
            ai_message=message.content or "",
            sender=sender,
            reviewer=reviewer,
            memory=self.memory,
            question=(message.current_goal or message.content or ""),
            **kwargs,
        )
