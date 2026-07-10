"""SummarizerAgent：把 SQL + 查询结果 + 原始问题 凝练成面向用户的中文结论。

为什么要有 Summarizer（即便 DataAnalyst 的 terminate 已经写了 final_answer）？
- DataAnalyst 的 final_answer 经常带工具调用痕迹（代码块、`execute_sql: ...`），
  不适合直接展示；
- team 模式下 DataAnalyst 的 terminate 只是"技术上成功"的信号，用户看的
  "最终结论"由 Summarizer 统一生成，风格可控、便于 A/B 调 prompt。

本 Agent 无 Action—— thinking 的原文即结论。避免多一层"LLM 输出 → 解析 →
格式化"的脆弱中间环节。
"""

from __future__ import annotations

from src.agent.core.action.base import Action
from src.agent.core.base_agent import ConversableAgent
from src.agent.core.profile import ProfileConfig

SUMMARIZER_DESC = """[输入]
用户问题：{{question}}

子任务执行详情（按顺序）：
{{sub_tasks_block}}

[输出要求]
- 直接输出面向用户的中文回答，不要 JSON、不要代码块包裹。
- 先给结论（1~2 句），再简述依据（≤ 3 句）。必要时附上最关键的数字。
- 若问题被拆成多个子任务，结论应是各子任务结果的综合判断，而不是逐条复述。
- 如果所有子任务都返回 0 行，明确说明"未查到符合条件的数据"并简述可能原因。
- 不要编造数字；所有断言必须能在子任务的样例数据里找到依据。
- 不要提及"SQL"、"工具"、"Agent"、"子任务"等实现细节 —— 用户只关心业务结论。
- **教育学情报告**：若上下文出现「教育学情产出摘要」且标明小题/知识点数据**已获取**，
  或已生成 HTML 诊断报告，结论须引导用户查看报告中的逐题分析、知识点掌握与干预建议，
  **禁止**写「本次无法获取小题级、知识点级诊断数据」等与报告内容矛盾的表述。"""


class SummarizerAgent(ConversableAgent):
    profile = ProfileConfig(
        name="Summarizer",
        role="数据分析结论撰写者",
        goal="把 SQL 执行结果凝练成面向用户的中文业务结论。",
        constraints=[
            "只输出中文自然语言，不要 JSON / 代码块",
            "不得虚构结果中不存在的数字或类别",
            "不得暴露 SQL 或 Agent 实现细节",
        ],
        desc=SUMMARIZER_DESC,
    )
    actions: list[Action] = []  # 无 Action：基类 act() 会直接把 thinking 文本回填为 ActionOutput
    max_retry_count: int = 1  # 结论生成失败也不重试，调用方自行回落 DataAnalyst 原文
