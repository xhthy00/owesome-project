"""CharterAgent：为查询结果选图表类型并给字段映射。

输入（runner 把这些塞到 received_message.context，Profile 模板里自动替换）：
- ``question``       原始用户问题
- ``sql``            DataAnalyst 最终执行的 SELECT
- ``columns``        结果列清单
- ``row_count``      结果行数
- ``sample_rows``    采样前几行（markdown 格式）

输出（action_report.extra）::

    {"chart_type": "line|bar|column|pie|table",
     "chart_config": {"x": "...", "y": [...], "title": "..."}}

失败（JSON 解析失败、类型未知）一律回落 ``chart_type="table"`` + 空 config——
图表推荐只是"锦上添花"，绝不能拖垮整条 team 流水线。
"""

from __future__ import annotations

import logging
from typing import Any

from src.agent.core.action.base import Action, ActionOutput
from src.agent.core.base_agent import ConversableAgent
from src.agent.core.profile import ProfileConfig
from src.agent.util.json_parser import parse_json_tolerant

logger = logging.getLogger(__name__)

_VALID_TYPES = {"line", "bar", "column", "pie", "table"}

CHARTER_DESC = """[输入]
用户问题：{{question}}
执行的 SQL：
```sql
{{sql}}
```
结果共 {{row_count}} 行，列：{{columns}}
样例数据：
{{sample_rows}}

[输出 - 严格 JSON]
只输出一个 JSON 对象（可用 ```json 代码块 包裹），字段：
{
  "thoughts": "<一句话说明为什么选这个图>",
  "chart_type": "line | bar | column | pie | table",
  "chart_config": {
    "x": "<X 轴列名，table 可为空串>",
    "y": ["<Y 轴列名>"],
    "title": "<图表标题，可空串>"
  }
}

[选图原则]
1. 单列或行数 > 500 → table
2. X 列是日期/时间 → line
3. X 列是类别（< 30 个） + 单个数值 Y → bar
4. 多个数值 Y 同一 X 且量纲相同 → column（分组柱状）
5. Y 是占比/百分比 且 类别 ≤ 8 → pie
6. 拿不准 → table（不要硬选）

[Y 轴必须贴问题]
- Y 轴必须是用户问题关心的指标，不要用 SQL 里碰巧排在前面的数值列
- 「参考人数」/ candidates 是分母，默认不当 Y
- 问题含「率 / 占比 / 达线率」时，Y 只选对应比率列，不要和人数列共用一轴
- 多个数值列量纲不同（人数 vs 0–100%）时，只保留主指标，不要分组柱
- 学校 vs 全市均分：两行 scope/该校/全市 + avg_zf6m → bar，x=scope，y=avg_zf6m；
  禁止用 exam_type / city 这种对不上问题的列"""


class ChartAction(Action):
    name = "chart_decide"

    async def run(self, ai_message: str, **kwargs: Any) -> ActionOutput:
        try:
            parsed = parse_json_tolerant(ai_message)
        except ValueError as e:
            logger.info("ChartAction JSON parse failed: %s", e)
            return _fallback_table("JSON parse failed, fallback to table")

        if not isinstance(parsed, dict):
            return _fallback_table("LLM output not a JSON object, fallback to table")

        chart_type = str(parsed.get("chart_type") or "").lower().strip()
        if chart_type not in _VALID_TYPES:
            return _fallback_table(
                f"unknown chart_type {chart_type!r}, fallback to table",
                thoughts=parsed.get("thoughts"),
            )

        raw_config = parsed.get("chart_config") or {}
        if not isinstance(raw_config, dict):
            raw_config = {}
        chart_config = {
            "x": str(raw_config.get("x") or ""),
            "y": list(raw_config.get("y") or []),
            "title": str(raw_config.get("title") or ""),
        }

        return ActionOutput(
            is_exe_success=True,
            content=f"图表推荐：{chart_type}",
            action=self.name,
            thoughts=parsed.get("thoughts"),
            extra={"chart_type": chart_type, "chart_config": chart_config},
            terminate=True,  # Charter 不是 ReAct，单轮即终
        )


def _fallback_table(reason: str, thoughts: Any = None) -> ActionOutput:
    return ActionOutput(
        is_exe_success=True,
        content=f"图表推荐：table（{reason}）",
        action="chart_decide",
        thoughts=thoughts,
        extra={"chart_type": "table", "chart_config": {"x": "", "y": [], "title": ""}},
        terminate=True,
    )


class CharterAgent(ConversableAgent):
    profile = ProfileConfig(
        name="Charter",
        role="数据可视化专家",
        goal="根据 SQL 查询结果，为前端渲染选择合适的图表类型和字段映射。",
        constraints=[
            "只输出单个 JSON 对象，不要额外解释",
            "chart_type 只能从 line/bar/column/pie/table 中选一个",
            "拿不准时宁可返回 table 也不要硬选",
        ],
        desc=CHARTER_DESC,
    )
    actions: list[Action] = [ChartAction()]
    max_retry_count: int = 1  # Charter 的 Action 自带 fallback，不需要框架重试
