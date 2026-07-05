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
    "学情", "成绩分析", "班级报告", "个体报告", "学情报告",
    "综合分析", "综合报告", "三次考试", "多次考试",
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
3. 子任务之间应尽量**执行顺序独立**——不要让后一个依赖前一个的具体数值；
   但**分析范围（学校/班级/年级/学生/考试）必须在每个 DataAnalyst 子任务描述中
   完整重复**，不得因"独立"而省略范围，更不得默认查全量学生/全校/全考试；
4. 绝对不要超过 6 个子任务；拆不动就合并；
5. 不要在子任务里写 SQL 或表名——那是 DataAnalyst 的工作，你只写"查什么"；
6. 对“可视化报告/分析报告/图表页面/HTML 报告”类子任务，优先标 `sub_task_agent`
   为 ToolExpert；其余场景默认 DataAnalyst，纯计算/工具操作也可标 ToolExpert。
7. **范围传递**：原问题若指定了学校/班级/年级/学生/某次考试，**每一个**
   DataAnalyst 子任务描述都必须显式写出该范围（用【】括起实体名），禁止写
   "该班/该校/该考试"等指代——下游子任务看不到原问题，指代会丢失过滤条件。

[教育学情报告分解模板]
当问题是“生成 XX 班 / XX 年级 / XX 科目 / XX 学生 的学情/成绩分析报告”时，按
报告类型拆成 2~4 个子任务，**最后一步固定为 ToolExpert 且必须显式指定教育模板名**：
- 子任务描述里**严禁**出现"Word/PDF 模板""热力图""图文并茂的完整报告"这类泛化
  措辞——报告组装就是一次 `render_html_report` 工具调用，不是写文档；
- 报告组装步的 task 文案统一写成：
  "用 education/<模板名>.html 模板组装 HTML 报告（数据取上游子任务）"。

- 班级总览报告（class_overview）：
  ["查询该班各科均分、及格率、优秀率、分数段分布", "查询该班在年级中的排名位置",
   {"task": "用 education/class_overview.html 模板组装 HTML 报告（数据取上游子任务）", "sub_task_agent": "ToolExpert"}]
- 年级对比报告（grade_comparison）：
  ["查询各班均分与离散度并排名", {"task": "用 education/grade_comparison.html 模板组装 HTML 报告（数据取上游子任务）", "sub_task_agent": "ToolExpert"}]
- 科目诊断报告（subject_diagnosis）：
  ["查询该科目分数段分布与及格率/优秀率", {"task": "用 education/subject_diagnosis.html 模板组装 HTML 报告（数据取上游子任务）", "sub_task_agent": "ToolExpert"}]
- **学校/班级 + 科目 + 小题（逐题）诊断**（如「分析【XX学校】在【XX考试】的数学成绩，
  细化到每一小题，形成详细分析报告」）——**只拆 2 个子任务**，组装步骤用一键工具：
  ["查询【XX学校】学生在【XX考试】【XX科目】的整体成绩：均分、及格率、优秀率、分数段分布（SQL 须含 exam_score）",
   {"task": "调 build_subject_diagnosis_report_tool(school_name=【XX学校】, subject_name=【XX科目】, exam_name=【XX考试】, class_name=【XX班级】) 一键生成科目诊断 HTML 报告——该工具内部自动查小题明细+知识点汇总（通过 tb_exam_question.knowledge_id LEFT JOIN tb_knowledge 取 knowledge_name）+成绩统计+渲染推送，调完直接 terminate，禁止再调 fetch_subject_diagnosis_data_tool / build_subject_diagnosis_sections_tool / render_html_report / 自写 JOIN SQL", "sub_task_agent": "ToolExpert"}]
  **严禁**自行写小题/知识点 JOIN SQL；**严禁**子任务不带学校名查全量。
- 个体画像/趋势/预警/群体对比同理，分别用 education/student_exam_analysis.html、
  education/trend_tracking.html、education/tier_alert.html、education/group_feature.html。
- **单个学生多次考试分析**（如「分析学生001这几次考试的成绩」）：
  只拆 **2 个子任务**，且**只为问题中指定的那一个学生**生成报告：
  ["查询该学生及全班历次考试各科分数与排名（SQL 须含全班数据以便算排名，但不得为其他学生另做报告）",
   {"task": "用 build_student_exam_report_data_tool 组装该学生考试分析 HTML 报告（student_name 必须与问题一致，仅一份报告）", "sub_task_agent": "ToolExpert"}]
  **严禁**为其他学生（如学生009）额外增加子任务或报告。
- 多次考试综合分析报告（comprehensive，含 9 个维度：整体概览/各科趋势/相关性/
  分布/进退步/偏科/单科之最/总分轨迹/学生档案）：
  ["查询该班历次考试各科均分、标准差、及格率/优秀率",
   "查询每位学生历次考试总分与各科分数，用于趋势/偏科/相关性分析",
   {"task": "用 education/comprehensive.html 模板组装综合分析 HTML 报告（数据取上游子任务，含 9 个维度）", "sub_task_agent": "ToolExpert"}]

简单问题（如"三班数学平均分"）不生成报告，返回 plans=[原问题] 即可。"""


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
