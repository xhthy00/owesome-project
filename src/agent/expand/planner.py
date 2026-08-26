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
import re
from typing import Any

from src.agent.core.action.base import Action, ActionOutput
from src.agent.core.agent import AgentMessage
from src.agent.core.base_agent import ConversableAgent
from src.agent.core.profile import ProfileConfig
from src.agent.education.query_parse import format_scope_constraints
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

[用户数据权限 / 分析范围（必须遵守；子任务描述须用绑定学校名，勿用问题中的省市区统考冠名当学校）]
{{scope_constraints}}

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
   **但以下报告类问题禁止返回 plans=[原问题]，必须拆成 2~4 个子任务**：
   - 含「学校/班级 + 考试 + 分析报告/多维分析/学情报告」；
   - 含「生成 HTML 报告 / 可视化报告」；
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
  ["查询该班每位学生各科分数（SQL 须含 student_id、subject、score、exam_score；禁止只查班级 KPI 聚合）", "查询该班在年级中的排名位置",
   {"task": "用 education/class_overview.html 模板组装 HTML 报告（数据取上游子任务；STUDENT_ARCHIVE_TABLE 由工具自动从上游成绩明细生成，勿手填空表）", "sub_task_agent": "ToolExpert"}]
- **学校/班级 + 成绩总览/班级总览**（如「扬州中学高三(10)班连淮扬镇数学成绩总览」）——
  **只拆 2 个子任务**，走班级总览，**禁止**走科目诊断 fetch+sections：
  ["查询该班【考试】【科目】每位学生得分（SQL 须含 student_id、score、exam_score；禁止只查 KPI）",
   {"task": "调 build_class_overview_report_data_tool(class_name=【班级】, subject_name=【科目】, render=true) 生成班级总览 HTML；**禁止** build_subject_diagnosis_sections_tool；完成后 terminate", "sub_task_agent": "ToolExpert"}]
- 年级对比报告（grade_comparison）/ **学校 + 各班横向多维对比**
  （如「扬州中学在连淮扬镇数学考试中各个班级的横向多维对比分析」）——
  **必须拆 3 个子任务**，范围是**全校各班**，**严禁**填写 class_name（不得缩成某一个班）：
  ["查询【XX学校】在【XX考试】【XX科目】整体成绩 KPI：均分、及格率、优秀率、分数段、各班对比（SQL 须 JOIN tb_school/tb_exam 且 SELECT exam_score；**禁止**按单班过滤）",
   {"task": "调 fetch_subject_diagnosis_data_tool(school_name=【XX学校】, subject_name=【XX科目】, exam_name=【XX考试】) 查询小题明细与知识点——本步仅 fetch，禁止 render，**禁止传 class_name**；完成后 terminate", "sub_task_agent": "ToolExpert"},
   {"task": "调 build_subject_diagnosis_sections_tool(school_name=【XX学校】, exam_name=【XX考试】, subject_name=【XX科目】, render=true) 一步完成全校 stats+各班对比 HTML；**禁止传 class_name**；完成后 terminate", "sub_task_agent": "ToolExpert"}]
- 科目诊断报告（subject_diagnosis）：
  ["查询该科目分数段分布与及格率/优秀率", {"task": "用 education/subject_diagnosis.html 模板组装 HTML 报告（数据取上游子任务）", "sub_task_agent": "ToolExpert"}]
- **学校 + 科目 + 考试 + 多维分析/分析报告**（如「分析【XX学校】在【XX考试】的数学成绩，多维分析形成报告」）——
  **必须拆 3 个子任务**（与班级诊断同构，**省略 class_name 表示全校该科**）：
  ["查询【XX学校】在【XX考试】【XX科目】整体成绩 KPI：均分、及格率、优秀率、分数段、各班对比（SQL 须 JOIN tb_school/tb_exam 且 SELECT exam_score；**禁止**按单班过滤）",
   {"task": "调 fetch_subject_diagnosis_data_tool(school_name=【XX学校】, subject_name=【XX科目】, exam_name=【XX考试】) 查询小题明细与知识点——本步仅 fetch，完成后 terminate，**禁止传 class_name**", "sub_task_agent": "ToolExpert"},
   {"task": "调 build_subject_diagnosis_sections_tool(school_name=【XX学校】, exam_name=【XX考试】, subject_name=【XX科目】, render=true) 一步完成 stats+HTML；**禁止传 class_name**；完成后 terminate", "sub_task_agent": "ToolExpert"}]
  **禁止** plans=[原问题]；**禁止** DataAnalyst 在子任务 2/3 组装报告。
- **学校/班级 + 科目 + 小题（逐题）诊断**（如「分析【XX学校】在【XX考试】的数学成绩，
  细化到每一小题，形成详细分析报告」）——**拆 3 个子任务**，小题查询须在工具链可见：
  ["查询【XX学校】【XX班级】在【XX考试】【XX科目】整体成绩 KPI：均分、及格率、优秀率、分数段（SQL 须 JOIN tb_school/tb_exam 且 SELECT exam_score）",
   {"task": "调 fetch_subject_diagnosis_data_tool(school_name=【XX学校】, subject_name=【XX科目】, exam_name=【XX考试】, class_name=【XX班级】) 查询 tb_score_detail 小题明细与知识点——**本步必须在工具链出现，禁止跳过**", "sub_task_agent": "ToolExpert"},
   {"task": "调 build_subject_diagnosis_sections_tool(school_name=【XX学校】, exam_name=【XX考试】, subject_name=【XX科目】, class_name=【XX班级】, render=true) **一步完成 stats 计算 + HTML 渲染并推送**（勿手传 fetch_data，工具自动读取上游 fetch）；完成后 terminate。**禁止**再调 compute_score_stats / select_report_template / build_chart_option / render_html_report。若 fetch 返回 0 题，terminate 说明 SQL 日志与原因", "sub_task_agent": "ToolExpert"}]
  **严禁** DataAnalyst 自行写 tb_score_detail JOIN SQL；**严禁**跳过 fetch 直接 render。
- **单个学生 + 单次考试 + 科目/知识点分析**（问题含学号/STU/学生编号/「学生001」）——
  **只拆 2 个子任务**，**禁止**走下方「学校/班级科目诊断」的 fetch+sections 三步：
  ["查询该学生【学号】在【考试】【科目】的整体成绩（分数、班级/年级排名、与班级/年级均分对照）",
   {"task": "调 build_student_subject_diagnosis_tool(student_id=【学号】, subject_name=【科目】, exam_name=【考试】, render=true) 组装该学生个人知识点分析报告；完成后 terminate。**禁止** build_subject_diagnosis_sections_tool", "sub_task_agent": "ToolExpert"}]
  **严禁**为班级/全校生成 subject_diagnosis 聚合报告。
- 个体画像/趋势/预警/群体对比同理，分别用 education/student_exam_analysis.html、
  education/trend_tracking.html、education/tier_alert.html、education/group_feature.html。
- **班级 + 成绩走势/进退步（趋势跟踪，非综合复盘）**
  （如「扬州中学高三(10)班数学成绩走势与进退步分析」）——
  **只拆 2 个子任务**，走 **趋势报告**（`trend_tracking`）：
  ["查询该班历次【科目】各场考试均分与个人进退（SQL 须含 exam_name、student_id、score、exam_score；按考试时间排序；禁止只查一场）",
   {"task": "调 build_trend_tracking_report_data_tool(class_name=【班级】, subject_name=【科目】, render=true) 生成【成绩趋势报告】（均分折线+明细表+进退步解读）；**禁止** render_html_report 手填 / build_comprehensive_report_data_tool；完成后 terminate", "sub_task_agent": "ToolExpert"}]
  **区分**：问的是「走势 / 进退步 / 趋势 / 折线」且**未**说「综合分析 / 综合报告 / 所有考试 / 多次考试综合」→ 用本条；不要默认改成综合分析。
- **班级/学校 + 临界生/分层预警报告**（如「扬州中学高三(10)班数学临界生预警报告」）——
  **只拆 2 个子任务**，走分层预警，**禁止**走科目诊断 fetch+sections：
  ["查询该班【科目】每位学生得分（SQL 须含 student_id、score、exam_score；有上次成绩则带 prev_score）",
   {"task": "调 build_tier_alert_report_data_tool(class_name=【班级】, subject_name=【科目】, render=true) 生成分层预警 HTML（临界生/退步/偏科）；**禁止** build_subject_diagnosis_sections_tool；完成后 terminate", "sub_task_agent": "ToolExpert"}]
- **学校 + 按班级/区县等群体对比特征**（如「扬州中学连淮扬镇数学考试按班级群体对比特征」；
  问句须含「群体特征/按班级群体」等**明确**口径。
  **「班级横向对比 / 各班横向 / 横向对比学情」不是群体特征，必须走上方 grade_comparison 三步**）——
  **只拆 2 个子任务**，走群体特征报告，**禁止**走班级横向对比科目诊断三步：
  ["查询【XX学校】在【XX考试】【XX科目】每位学生得分（SQL 须含 student_id、class、score、exam_score；**禁止**按单班过滤）",
   {"task": "调 build_group_feature_report_data_tool(school_name=【XX学校】, subject_name=【XX科目】, exam_name=【XX考试】, dimension=class, render=true) 生成群体特征 HTML；**禁止** build_subject_diagnosis_sections_tool；完成后 terminate", "sub_task_agent": "ToolExpert"}]
- **单个学生多次考试分析**（如「分析学生001这几次考试的成绩」）：
  只拆 **2 个子任务**，且**只为问题中指定的那一个学生**生成报告：
  ["查询该学生及全班历次考试各科分数与排名（SQL 须含全班数据以便算排名，但不得为其他学生另做报告）",
   {"task": "用 build_student_exam_report_data_tool 组装该学生考试分析 HTML 报告（student_name 必须与问题一致，仅一份报告）", "sub_task_agent": "ToolExpert"}]
  **严禁**为其他学生（如学生009）额外增加子任务或报告。
- **班级 + 所有/历次/多次考试「综合分析」**（如「扬州中学高三(11)班所有数学考试综合分析」）——
  **只拆 2 个子任务**，走综合报告（9 维复盘），**禁止**走科目诊断 fetch+sections：
  ["查询该班历次考试每位学生【科目】分数（SQL 须含 exam_name、student_id、score、exam_score；禁止只查 KPI 聚合、禁止只查一场）",
   {"task": "调 build_comprehensive_report_data_tool(class_name=【班级】) 生成多次考试综合分析 HTML；**禁止** build_subject_diagnosis_sections_tool；完成后 terminate", "sub_task_agent": "ToolExpert"}]
  **勿把**仅含「走势 / 进退步」的问法扩成综合分析；用户要综合复盘时应含「综合分析 / 综合报告」或明确「所有/历次/多次考试」范围。
- 多次考试综合分析报告（comprehensive，含 9 个维度：整体概览/各科趋势/相关性/
  分布/进退步 TOP/偏科/单科之最/总分轨迹/学生档案）——仅当问题点名综合分析时使用：
  ["查询该班历次考试每位学生分数（SQL 须含 exam_name、student_id、score；禁止只查班级 KPI 聚合）",
   {"task": "调 build_comprehensive_report_data_tool(class_name=【班级】) 一步生成综合分析 HTML（进步/退步 TOP5 与每位学生档案由工具自动计算）；**禁止** render_html_report / 手填模板；完成后 terminate", "sub_task_agent": "ToolExpert"}]
- **全市达线情况分析**（如「全市2026届高三1月期末达线情况」；问句含引领校/支撑校/发展校则不是本报告，按校类事实查询）——
  **只 1 个子任务**，走 `build_line_reach_report_data_tool`，查 tb_score_indicator，含较上场人数/率环比：
  [{"task": "调 build_line_reach_report_data_tool(render=true) 生成全市达线分析 HTML；**禁止** describe_table / execute_sql / build_diagnostic_report_data_tool / 扫 tb_score_overview；完成后 terminate", "sub_task_agent": "ToolExpert"}]
- 结构化诊断报告（diagnostic_report，一般性/特殊性/动态性三节）：
  ["查询【范围】成绩明细（含 class/district/subject）用于聚合",
   {"task": "调 build_diagnostic_report_data_tool(scope_label=【范围】, exam_name=【考试】, subject_name=【科目】, render=true) 生成结构化诊断 HTML（勿手传 score_rows/fetch_data）", "sub_task_agent": "ToolExpert"}]
- **全市 + 考试 + 科目成绩分析**（如「帮我分析全市的江苏省高一上学期数学期末质量检测成绩，形成详细报告」）——
  **拆 3 个子任务**，与学校科目诊断同构（先查数、再 fetch、再组装），**禁止**一步调用 `build_citywide_exam_analysis_report_tool`：
  ["查询全市【XX考试】【XX科目】学生成绩 KPI 与明细（SQL 须 JOIN tb_school sch ON sc.school_id=sch.id JOIN tb_exam e ON sc.exam_id=e.id LEFT JOIN tb_exam_batch eb ON e.exam_batch_id=eb.id，SELECT sc.score, sc.exam_score, sc.class, sch.district, sch.name AS school_name, sc.student_id, sc.subject_name；按 subject_name 与 COALESCE(eb.batch_name, e.exam_name) 过滤；全市范围**不传** school_name/class_name；**禁止** sch.s_name）",
   {"task": "调 fetch_subject_diagnosis_data_tool(subject_name=【科目】, exam_name=【考试】) 查询全市小题明细与知识点——**本步仅 fetch，禁止 render**；完成后 terminate（**禁止**调 build_diagnostic_report_data_tool）", "sub_task_agent": "ToolExpert"},
   {"task": "调 build_diagnostic_report_data_tool(scope_label=全市, exam_name=【考试】, subject_name=【科目】, render=true) **一步完成区县对比+分数段+小题/知识点+HTML**；**禁止**再调 fetch_subject_diagnosis_data_tool（工具自动读取上游成绩与 fetch 数据）；完成后 terminate", "sub_task_agent": "ToolExpert"}]

简单问题（如"三班数学平均分"）不生成报告，返回 plans=[原问题] 即可。
**例外**：问题含学号/「学生xxx」且询问「得分情况/成绩/知识点」——**不算简单问题**，
须走上方「单个学生 + 单次考试」2 步计划（含 build_student_subject_diagnosis_tool），
禁止只查总分后 terminate。"""


def _plan_exam_name(question: str) -> str:
    """从问句抽取考试名；抽不到返回空串——禁止回落「本次考试」占位。"""
    from src.agent.education.orchestrator import _extract_exam

    return (_extract_exam(question) or "").strip()


def _plan_subject_name(question: str) -> str:
    """从问句抽取科目；抽不到返回空串——禁止回落「该科目」占位。"""
    from src.agent.education.orchestrator import _extract_subject

    return (_extract_subject(question) or "").strip()


def _plan_label(value: str, *, missing: str) -> str:
    """计划文案展示用：有真实抽取结果用原值，否则用中性说明（非伪造考试/科目名）。"""
    text = (value or "").strip()
    return text if text else missing


def build_citywide_team_plan_items(question: str) -> list[dict[str, str]]:
    """全市考试成绩分析的标准 3 步 Team 计划（不依赖 Planner LLM）。"""
    exam = _plan_exam_name(question)
    subject = _plan_subject_name(question)
    exam_l = _plan_label(exam, missing="问题中的考试")
    subject_l = _plan_label(subject, missing="问题中的科目")
    return [
        {
            "sub_task": (
                f"查询全市【{exam_l}】【{subject_l}】学生成绩 KPI 与明细"
                "（SQL 须 JOIN tb_school sch ON sc.school_id=sch.id JOIN tb_exam e ON sc.exam_id=e.id LEFT JOIN tb_exam_batch eb ON e.exam_batch_id=eb.id，"
                "SELECT sc.score, sc.exam_score, sc.class, sch.district, sch.name AS school_name, "
                "sc.student_id, sc.subject_name；按 subject_name 与 COALESCE(eb.batch_name, e.exam_name) 过滤；"
                "全市范围**不传** school_name/class_name；**禁止** sch.s_name；"
                "**exam_name / subject_name 必须取自问题原文，禁止填「本次考试」「该科目」**）"
            ),
            "sub_task_agent": _DEFAULT_SUB_TASK_AGENT,
        },
        {
            "sub_task": (
                f"调 fetch_subject_diagnosis_data_tool(subject_name={subject}, exam_name={exam}) "
                "查询全市小题明细与知识点——**本步仅 fetch，禁止 render**；"
                "完成后 terminate（**禁止**调 build_diagnostic_report_data_tool）"
            ),
            "sub_task_agent": _TOOL_EXPERT_AGENT,
        },
        {
            "sub_task": (
                f"调 build_diagnostic_report_data_tool(scope_label=全市, exam_name={exam}, "
                f"subject_name={subject}, render=true) **一步完成区县对比+分数段+小题/知识点+HTML**；"
                "**禁止**再调 fetch_subject_diagnosis_data_tool（工具自动读取上游成绩与 fetch 数据）；"
                "完成后 terminate"
            ),
            "sub_task_agent": _TOOL_EXPERT_AGENT,
        },
    ]


def build_school_subject_report_plan_items(question: str) -> list[dict[str, str]]:
    """学校 + 考试科目分析报告的标准 3 步计划（Planner 未拆解时的修正回落）。

    - 各班横向 / 未点名班级：全校取数，**禁止** class_name
    - 已点名班级：带上 class_name 做单班科目诊断
    """
    from src.agent.education.orchestrator import _extract_class_name
    from src.agent.education.query_parse import (
        extract_school_target,
        is_school_class_comparison_query,
    )

    school = extract_school_target(question) or "该校"
    exam = _plan_exam_name(question)
    subject = _plan_subject_name(question)
    exam_l = _plan_label(exam, missing="问题中的考试")
    subject_l = _plan_label(subject, missing="问题中的科目")
    class_name = _extract_class_name(question) or ""
    school_wide = is_school_class_comparison_query(question) or not class_name
    class_arg = "" if school_wide else f", class_name={class_name}"
    class_forbid = (
        "，禁止传 class_name**；" if school_wide else "**；"
    )
    kpi_scope = (
        "各班对比（SQL 须 JOIN tb_school/tb_exam 且 SELECT exam_score；**禁止**按单班过滤）"
        if school_wide
        else f"班级【{class_name}】KPI（SQL 须 JOIN tb_school/tb_exam 且 SELECT exam_score）"
    )
    build_note = (
        "**一步完成全校 stats + 各班对比 HTML**；**禁止传 class_name**（不得缩成单班）；"
        if school_wide
        else f"**一步完成【{class_name}】stats + HTML**；"
    )
    return [
        {
            "sub_task": (
                f"查询【{school}】在【{exam_l}】【{subject_l}】整体成绩 KPI："
                f"均分、及格率、优秀率、分数段分布、{kpi_scope}；"
                "**exam_name / subject_name 必须取自问题原文，禁止填「本次考试」「该科目」**"
            ),
            "sub_task_agent": _DEFAULT_SUB_TASK_AGENT,
        },
        {
            "sub_task": (
                f"调 fetch_subject_diagnosis_data_tool(school_name={school}, "
                f"subject_name={subject}, exam_name={exam}{class_arg}) "
                f"查询小题明细与知识点——**本步仅 fetch，禁止 render{class_forbid}"
                "完成后 terminate"
            ),
            "sub_task_agent": _TOOL_EXPERT_AGENT,
        },
        {
            "sub_task": (
                f"调 build_subject_diagnosis_sections_tool(school_name={school}, "
                f"exam_name={exam}, subject_name={subject}{class_arg}, render=true) "
                f"{build_note}完成后 terminate"
            ),
            "sub_task_agent": _TOOL_EXPERT_AGENT,
        },
    ]


def build_school_class_comparison_plan_items(question: str) -> list[dict[str, str]]:
    """学校各班横向对比：强制全校取数，严禁 class_name。"""
    from src.agent.education.query_parse import extract_school_target

    school = extract_school_target(question) or "该校"
    exam = _plan_exam_name(question)
    subject = _plan_subject_name(question)
    exam_l = _plan_label(exam, missing="问题中的考试")
    subject_l = _plan_label(subject, missing="问题中的科目")
    return [
        {
            "sub_task": (
                f"查询【{school}】在【{exam_l}】【{subject_l}】整体成绩 KPI："
                "均分、及格率、优秀率、分数段分布、各班对比"
                "（SQL 须 JOIN tb_school/tb_exam 且 SELECT exam_score；**禁止**按单班过滤）；"
                "**exam_name / subject_name 必须取自问题原文，禁止填「本次考试」「该科目」**"
            ),
            "sub_task_agent": _DEFAULT_SUB_TASK_AGENT,
        },
        {
            "sub_task": (
                f"调 fetch_subject_diagnosis_data_tool(school_name={school}, "
                f"subject_name={subject}, exam_name={exam}) "
                "查询小题明细与知识点——**本步仅 fetch，禁止 render，禁止传 class_name**；"
                "完成后 terminate"
            ),
            "sub_task_agent": _TOOL_EXPERT_AGENT,
        },
        {
            "sub_task": (
                f"调 build_subject_diagnosis_sections_tool(school_name={school}, "
                f"exam_name={exam}, subject_name={subject}, render=true) "
                "**一步完成全校 stats + 各班对比 HTML**；**禁止传 class_name**（不得缩成单班）；"
                "完成后 terminate"
            ),
            "sub_task_agent": _TOOL_EXPERT_AGENT,
        },
    ]


_GENERIC_DISTRICT_LABELS = frozenset({"各区", "各县", "各区县", "全区县", "各地区"})


def _line_reach_fact_hint(
    question: str,
    *,
    school: str,
    class_name: str,
    district: str,
    citywide: bool,
) -> str:
    """按班/校/区/全市粒度给出达线事实查询的选表提示。"""
    spec_district = district if district and district not in _GENERIC_DISTRICT_LABELS else ""
    school_type = ""
    from src.agent.education.query_parse import extract_school_type_target

    school_type = extract_school_type_target(question) or ""
    if class_name:
        return (
            "本题是班级达线/预测线查询（特招线=特控线）："
            "必须用 tb_score_overview.**zf6m**（六门/全科总分）对 tb_fraction_bar 阈值现算达线；"
            "**禁止**用 zf4m/zf3m 对照分数线（四门/三门分远低于六门线，会导致全员不达线）；"
            "物理类对照 wl_score_*，历史类对照 ls_score_*，按学生 xkkm 分轨："
            "xkkm LIKE '物%'→物理类，LIKE '史%' OR LIKE '历%'→历史类；"
            "班内只有一个选科方向时**只报该方向**，禁止文理混报或套用另一方向分数线；"
            "WHERE 必须按问题中的学校(xx)、班级(bj)过滤，人数/率只统计该班；"
            "未点名考试则对照 tb_exam_batch 取最近一场；"
            "禁止扫 tb_score_indicator（无班级粒度）、禁止套用全市达线 HTML 报告。"
        )
    if school_type:
        label = f"{school_type}校"
        return (
            f"本题是全市{label}达线查询（特招线=特控线），范围是该校类不是全体学校："
            "优先查 tb_score_indicator；"
            "JOIN tb_school sch ON ind.school_name = COALESCE(sch.s_name, sch.name)；"
            f"校类用 sch.\"type\" LIKE '%{school_type}%'（overview.xxlb 同源 tb_school.type，"
            "indicator 无 xxlb，必须关联学校表）；"
            "达线率 SUM(reached_count)/SUM(candidates)，禁止 AVG(reach_rate)；"
            "写 SQL 前先 peek_edu_filter_values；"
            "禁止套用全市达线 HTML 报告。"
        )
    if school or (spec_district and not citywide):
        dist_hint = (
            f"区县过滤用 district LIKE '%{spec_district.rstrip('区县')}%' "
            f"（库内常是「{spec_district}」）；"
            "禁止把「N月」里的「月」拼进区县名；"
            if spec_district
            else "按问句中的区县/学校过滤；"
        )
        return (
            "本题是学校/区县达线查询（特招线=特控线）："
            "优先查 tb_score_indicator；点名学校用 school_name LIKE '%校名%'，区县用 district；"
            "写最终 SQL 前须先 peek_edu_filter_values（或 DISTINCT 考试/区县）；"
            "禁止 school_id='GZ_…'（校码不是校名）；禁止用 district='市直' 冒充学校；"
            f"{dist_hint}"
            "考试名对照 tb_exam_batch.batch_name，用 exam_name LIKE '%批次%'，"
            "禁止臆造未出现在批次表中的精确全称；未点名考试则取最近一场；"
            "空结果时先 peek/DISTINCT 对照，禁止直接断言该区无数据；"
            "区县达线率须 SUM(reached_count)/SUM(candidates)，禁止 AVG(reach_rate)；"
            "禁止套用全市达线 HTML 报告。"
        )
    return (
        "本题是达线/预测线查询：必须查 tb_score_indicator（exam_name=批次名）；"
        "写 SQL 前先 peek_edu_filter_values；"
        "区县或全市须 SUM(reached_count)/SUM(candidates) 重算率，禁止 AVG(reach_rate)；"
        "禁止扫 tb_score_overview 学生明细。"
    )


def _score_band_fact_hint() -> str:
    """绝对分数阈值/分段人数：钉死 overview 与分箱列。"""
    return (
        "本题是绝对分数阈值/分段人数（不是达线、不是位次桶）："
        "必须查 tb_score_overview；禁止 tb_score、禁止 tb_score_indicator。"
        "问句「总分」未写语数英/三门时必须用 zf6m 六门全科，禁止 zf3m、禁止英语 yy；"
        "物理类 xkkm LIKE '物%'；历史类 LIKE '史%' OR LIKE '历%'；"
        "区县 dq LIKE '%邗江%'；"
        "引领校 xxlb LIKE '%引领%' AND xsxz='在籍生'（xxlb 同源 tb_school.type）；市报生 xsxz LIKE '%市报%'。"
        "N分以上：COUNT(*) FILTER (WHERE zf6m >= N) 与同分母 COUNT(*)。"
        "十分段 GROUP BY ((CAST(zf6m AS int)-1)/10)*10+1。"
        "点名学校时必须 xx LIKE '%校名%'（xx 是学校明文），禁止 xx='GZ_…' 校码；"
        "禁止出全市/各区县 HTML 报告；"
        "点名班级时 WHERE 必含 bj。"
        "化学用 hxzh 禁止 hx；生物 swzh、政治 zzzh、地理 dlzh；语数英物史用 yw/sx/yy/wl/ls。"
        "比例分母=该切片有效人数，禁止对区县比例再平均。"
    )


def _school_vs_city_avg_hint() -> str:
    """点名学校均分 vs 全市：自由 SQL；xx 是校名明文，禁止拿校码去查。"""
    return (
        "本题是点名学校均分与全市比较（自由 SQL）："
        "必须查 tb_score_overview；禁止 tb_score / tb_exam / exam_type；禁止任何 HTML 报告。"
        "未点名语数英/单科时均分=AVG(zf6m) 六门全科，禁止用物理单科 wl。"
        "物理类/历史类是选科方向不是学科：物理类 xkkm LIKE '物%'，历史类 LIKE '史%' OR LIKE '历%'。"
        "tb_score_overview.xx 是学校明文（如「扬州中学」），不是校码。"
        "禁止 xx='GZ_…'、禁止 xx=tb_school.id、禁止 xx=tb_school.name（那些是脱敏校码）。"
        "点名学校用 xx LIKE '%扬州中学%'。"
        "「本校/我校」=权限绑定学校，xx LIKE '%绑定校名%'。"
        "全市那一支禁止任何 xx 条件，禁止 GROUP BY xx 当全市，禁止点名他校。"
        "最终结果必须恰好两行：scope（该校/全市）、avg_zf6m、n；用 UNION ALL。"
        "terminate 必须写出该校均分、全市均分、人数、分差。"
        "禁止套用班级横向对比 / 科目诊断 / 各区县均分 HTML 报告。"
    )


def build_fact_query_plan_items(question: str) -> list[dict[str, str]]:
    """事实查询：仅 DataAnalyst，禁止生成任何 HTML 报告。"""
    from src.agent.education.orchestrator import _extract_class_name, _extract_subject
    from src.agent.education.query_parse import (
        extract_district_target,
        extract_school_target,
        extract_school_type_target,
        is_line_reach_citywide_scope,
        is_line_reach_query,
        is_overview_total_query,
        is_school_vs_city_avg_query,
        is_score_threshold_fact_query,
    )

    q = (question or "").strip()
    school = extract_school_target(q) or ""
    school_type = extract_school_type_target(q) or ""
    class_name = _extract_class_name(q) or ""
    subject = _plan_subject_name(q) or (_extract_subject(q) or "")
    exam = _plan_exam_name(q)
    district = extract_district_target(q) or ""
    spec_district = district if district and district not in _GENERIC_DISTRICT_LABELS else ""
    scope_bits = []
    if school:
        scope_bits.append(f"学校【{school}】")
    if school_type:
        scope_bits.append(f"校类【{school_type}校】")
    if class_name:
        scope_bits.append(f"班级【{class_name}】")
    if spec_district:
        scope_bits.append(f"区县【{spec_district}】")
    if subject:
        scope_bits.append(f"科目【{subject}】")
    if any(h in q for h in ("物理类", "物理方向", "理科")):
        scope_bits.append("选科【物理类】")
    elif any(h in q for h in ("历史类", "历史方向", "文科")):
        scope_bits.append("选科【历史类】")
    if exam:
        scope_bits.append(f"考试【{exam}】")
    scope = "、".join(scope_bits) if scope_bits else "问题中的范围"
    line_reach_hint = ""
    if is_line_reach_query(q):
        line_reach_hint = _line_reach_fact_hint(
            q,
            school=school,
            class_name=class_name,
            district=district,
            citywide=is_line_reach_citywide_scope(q),
        )
    score_band_hint = ""
    if not line_reach_hint and is_score_threshold_fact_query(q):
        score_band_hint = _score_band_fact_hint()
    school_city_hint = ""
    if not line_reach_hint and not score_band_hint and is_school_vs_city_avg_query(q):
        school_city_hint = _school_vs_city_avg_hint()
    overview_hint = ""
    if (
        not line_reach_hint
        and not score_band_hint
        and not school_city_hint
        and is_overview_total_query(q)
    ):
        overview_hint = (
            "本题是语数外三门/四门/六门总分均分："
            "必须查 tb_score_overview；"
            "语数外三门均分=AVG(zf3m)（三科总分的校均，约 300+，禁止除以 3，"
            "禁止对 tb_score 语文/数学/英语 AVG(score) 当三门均分，禁止写满分150）；"
            "四门=zf4m，六门/全科=zf6m；"
            "理科=物理类（xkkm LIKE '物%'），文科=历史类（xkkm LIKE '史%' OR xkkm LIKE '历%'）；"
            "学校用 xx 分组（xx 是学校明文，禁止拿 GZ_ 校码当 xx）；"
            "参考人数=COUNT(*)。"
        )
    from src.agent.education.privacy_mode import is_anonymize_display_enabled

    if is_anonymize_display_enabled():
        student_hint = (
            "涉及学生时 SELECT **仅用 student_id**（或 sc.student_id / st.id 学号），"
            "以及 score、exam_score、class 等；"
            "**禁止** SELECT/展示姓名明文（xm、name、student_name、真实姓名）；"
            "结论里点名学生时**一律写 student_id**，禁止写中文姓名。"
        )
    else:
        student_hint = (
            "当前已关闭匿名脱敏：涉及学生时可 SELECT xm（姓名）、xh/student_id（学号）、"
            "sch.s_name（学校全称）；结论里可用姓名/学号/校名。"
            "仍禁止 SELECT sfzh/ksh。"
        )
    return [
        {
            "sub_task": (
                f"用 SQL 直接回答用户问题（范围：{scope}）。"
                f"{line_reach_hint}"
                f"{score_band_hint}"
                f"{school_city_hint}"
                f"{overview_hint}"
                "WHERE 必须按问题中的班级/学校/科目/考试过滤（有则过滤）；"
                f"{student_hint}"
                "只回答用户所问（如最高分是谁/多少分）；"
                + (
                    ""
                    if overview_hint or school_city_hint
                    else (
                        "**禁止**写「参考人数/共N人参考/班级人数」——"
                        "Top-N、LIMIT、返回行数都不是全班人数；"
                    )
                )
                + "**禁止**套用学情总判/关键指标/教学建议长文。"
                "用一两段自然语言给出结论即可。"
                "**禁止**调任何 build_*_report / render_html / 学情报告工具；"
                "**禁止**生成 HTML 报告；答完即 terminate。"
                f"原问：{q}"
            ),
            "sub_task_agent": _DEFAULT_SUB_TASK_AGENT,
        },
    ]


def build_line_reach_plan_items(question: str) -> list[dict[str, str]]:
    """全市达线分析：直接渲染指标表报告，禁止扫学生明细。"""
    return [
        {
            "sub_task": (
                "调 build_line_reach_report_data_tool(render=true) "
                "生成全市达线情况分析（含较上次考试人数/率环比）；"
                "完成后 terminate。"
                "**禁止** describe_table / execute_sql / "
                "build_diagnostic_report_data_tool / 扫 tb_score_overview 学生明细"
            ),
            "sub_task_agent": _TOOL_EXPERT_AGENT,
        }
    ]


def build_bureau_plan_items(tool_name: str, question: str) -> list[dict[str, str]]:
    """局端基础分析：从 tb_score_overview 重算，禁止套结构化诊断。"""
    return [
        {
            "sub_task": (
                f"调 {tool_name}(render=true) 生成局端基础分析报告；"
                "完成后 terminate。"
                "**禁止** build_diagnostic_report_data_tool / "
                "build_class_overview_report_data_tool。"
                f"原问：{question or ''}"
            ),
            "sub_task_agent": _TOOL_EXPERT_AGENT,
        }
    ]


def build_top_student_lookup_plan_items(question: str) -> list[dict[str, str]]:
    """兼容旧名：已并入 build_fact_query_plan_items。"""
    return build_fact_query_plan_items(question)


def build_individual_student_exam_plan_items(question: str) -> list[dict[str, str]]:
    """单个学生分析：单场 → 知识点诊断；多次/这几次/个人画像 → 趋势对比报告。"""
    from src.agent.education.query_parse import (
        extract_student_target,
        is_multi_exam_student_analysis_query,
        is_vague_exam_name,
    )

    sid = extract_student_target(question) or "该学生"
    exam = _plan_exam_name(question)
    subject = _plan_subject_name(question)
    if is_vague_exam_name(exam):
        exam = ""
    subject_arg = f", subject_name={subject}" if subject else ""
    subject_disp = f"【{subject}】" if subject else ""
    q = question or ""
    wants_portrait = any(h in q for h in ("个人画像", "学生画像", "个体画像"))

    if is_multi_exam_student_analysis_query(question) or wants_portrait:
        subject_label = subject_disp or "各科"
        return [
            {
                "sub_task": (
                    f"查询学生【{sid}】及全班历次{subject_label}考试分数与排名"
                    "（SQL 须含 exam_name、student_id、score、exam_score、class；"
                    "须覆盖该生全部相关考试，禁止只查单场；"
                    "须含全班同学分以便算班级均分与第1名；"
                    "**禁止**把 exam_name 写成「这几次/本次考试」）"
                ),
                "sub_task_agent": _DEFAULT_SUB_TASK_AGENT,
            },
            {
                "sub_task": (
                    f"调 build_student_exam_report_data_tool(student_id={sid}"
                    f"{subject_arg}, render=true) "
                    "组装该生**多次考试**学情 HTML（须体现：具体考试次数、历次得分明细、"
                    "多次均分、与班级第1名差距、成绩趋势）；完成后 terminate。"
                    "**禁止** build_student_subject_diagnosis_tool；"
                    "**禁止** build_subject_diagnosis_sections_tool；"
                    "**禁止** exam_name 填「这几次」"
                ),
                "sub_task_agent": _TOOL_EXPERT_AGENT,
            },
        ]

    exam_l = _plan_label(exam, missing="问题中的考试")
    return [
        {
            "sub_task": (
                f"查询学生【{sid}】在【{exam_l}】"
                f"{subject_disp}的总分、卷面满分、班级排名、"
                "班级均分对照（SQL 须含全班同学分以便算排名；可 JOIN tb_exam/tb_school）；"
                "**exam_name 必须取自问题原文，禁止填「本次考试/这几次」**"
            ),
            "sub_task_agent": _DEFAULT_SUB_TASK_AGENT,
        },
        {
            "sub_task": (
                f"调 build_student_subject_diagnosis_tool(student_id={sid}, "
                f"exam_name={exam}{subject_arg}, render=true) "
                "组装该生个人小题/知识点得分明细与提升建议 HTML 报告；完成后 terminate。"
                "**禁止** build_subject_diagnosis_sections_tool；**禁止**只总结总分"
            ),
            "sub_task_agent": _TOOL_EXPERT_AGENT,
        },
    ]


def build_comprehensive_class_plan_items(question: str) -> list[dict[str, str]]:
    """班级多场考试 → 查数 + comprehensive 综合报告（含考试对比）。"""
    from src.agent.education.orchestrator import _extract_class_name, _extract_subject

    class_name = _extract_class_name(question) or "该班"
    subject = _extract_subject(question) or ""
    subject_label = f"【{subject}】" if subject else "各科"
    return [
        {
            "sub_task": (
                f"查询【{class_name}】历次{subject_label}考试每位学生分数"
                "（SQL 须含 exam_name、student_id、score、exam_score；"
                "须带出该班全部相关考试，禁止只查班级 KPI 聚合、禁止只查单场）"
            ),
            "sub_task_agent": _DEFAULT_SUB_TASK_AGENT,
        },
        {
            "sub_task": (
                f"调 build_comprehensive_report_data_tool(class_name={class_name}) "
                "一步生成多次考试综合分析 HTML；"
                "**禁止** build_subject_diagnosis_sections_tool / fetch 渲染；完成后 terminate"
            ),
            "sub_task_agent": _TOOL_EXPERT_AGENT,
        },
    ]


def build_tier_alert_plan_items(question: str) -> list[dict[str, str]]:
    """临界生/分层预警：查学生分数 + 一键渲染 tier_alert 报告。"""
    from src.agent.education.orchestrator import _extract_class_name, _extract_subject
    from src.agent.education.query_parse import extract_school_target

    school = extract_school_target(question) or ""
    class_name = _extract_class_name(question) or ""
    subject = _plan_subject_name(question) or (_extract_subject(question) or "")
    exam = _plan_exam_name(question)
    school_l = school or "该校"
    class_l = class_name or "该班"
    subject_l = f"【{subject}】" if subject else ""
    exam_l = _plan_label(exam, missing="本次相关考试")
    scope_args = []
    if school:
        scope_args.append(f"school_name={school}")
    if class_name:
        scope_args.append(f"class_name={class_name}")
    if subject:
        scope_args.append(f"subject_name={subject}")
    if exam:
        scope_args.append(f"exam_name={exam}")
    tool_args = ", ".join(scope_args)
    if tool_args:
        tool_args = tool_args + ", "
    return [
        {
            "sub_task": (
                f"查询【{school_l}】【{class_l}】在【{exam_l}】{subject_l}每位学生得分"
                "（SQL 须含 student_id、score、exam_score；"
                "有上次同科成绩则带 prev_score；禁止只查 KPI 聚合）"
            ),
            "sub_task_agent": _DEFAULT_SUB_TASK_AGENT,
        },
        {
            "sub_task": (
                f"调 build_tier_alert_report_data_tool({tool_args}render=true) "
                "生成分层预警 HTML（临界生/大幅退步/偏科名单）；完成后 terminate。"
                "**禁止** build_subject_diagnosis_sections_tool / fetch 渲染"
            ),
            "sub_task_agent": _TOOL_EXPERT_AGENT,
        },
    ]


def build_knowledge_cohort_plan_items(question: str) -> list[dict[str, str]]:
    """班内后十 vs 中位组：知识点掌握差距（专用工具一键查数+渲染）。"""
    from src.agent.education.orchestrator import _extract_class_name, _extract_subject
    from src.agent.education.query_parse import extract_school_target

    school = extract_school_target(question) or ""
    class_name = _extract_class_name(question) or ""
    subject = _plan_subject_name(question) or (_extract_subject(question) or "")
    exam = _plan_exam_name(question)
    scope_args: list[str] = []
    if school:
        scope_args.append(f"school_name={school}")
    if class_name:
        scope_args.append(f"class_name={class_name}")
    if subject:
        scope_args.append(f"subject_name={subject}")
    if exam:
        scope_args.append(f"exam_name={exam}")
    tool_args = ", ".join(scope_args)
    if tool_args:
        tool_args = tool_args + ", "
    class_l = class_name or "该班"
    subject_l = subject or "问题中的科目"
    exam_l = _plan_label(exam, missing="问题中的考试")
    return [
        {
            "sub_task": (
                f"调 compare_knowledge_cohort_tool({tool_args}"
                "bottom_n=10, median_band=2, render=true) "
                f"对比【{class_l}】【{exam_l}】【{subject_l}】最后十名与中位组的知识点得分率差距，"
                "生成对比表+分组柱图 HTML；完成后 terminate。"
                "**禁止** build_subject_diagnosis_sections_tool / build_subject_diagnosis_report_tool / "
                "execute_sql 自行多步 JOIN 知识点"
            ),
            "sub_task_agent": _TOOL_EXPERT_AGENT,
        },
    ]


def build_group_feature_plan_items(question: str) -> list[dict[str, str]]:
    """群体特征：按维度聚合全校/范围成绩并渲染 group_feature 报告。"""
    from src.agent.education.orchestrator import _extract_subject
    from src.agent.education.query_parse import (
        extract_school_target,
        infer_group_feature_dimension,
    )

    school = extract_school_target(question) or ""
    subject = _plan_subject_name(question) or (_extract_subject(question) or "")
    exam = _plan_exam_name(question)
    dimension = infer_group_feature_dimension(question)
    dim_label = {
        "class": "班级",
        "district": "区县",
        "grade": "年级",
        "subject": "科目",
        "school": "学校",
    }.get(dimension, dimension)
    school_l = school or "该校"
    subject_l = f"【{subject}】" if subject else ""
    exam_l = _plan_label(exam, missing="问题中的考试")
    scope_args = [f"dimension={dimension}"]
    if school:
        scope_args.append(f"school_name={school}")
    if subject:
        scope_args.append(f"subject_name={subject}")
    if exam:
        scope_args.append(f"exam_name={exam}")
    tool_args = ", ".join(scope_args) + ", "
    return [
        {
            "sub_task": (
                f"查询【{school_l}】在【{exam_l}】{subject_l}每位学生得分"
                f"（最终 SQL 必须是学生明细：含 student_id、class、score、exam_score；"
                f"按{dim_label}可分组；**禁止**最终只输出校级/班级 KPI 聚合行、"
                "**禁止**按单班过滤）"
            ),
            "sub_task_agent": _DEFAULT_SUB_TASK_AGENT,
        },
        {
            "sub_task": (
                f"调 build_group_feature_report_data_tool({tool_args}render=true) "
                f"生成按{dim_label}的群体特征 HTML（含均分/及格率对比、特征画像、干预建议）；"
                "完成后 terminate。"
                "**禁止** build_subject_diagnosis_sections_tool / build_tier_alert_report_data_tool"
            ),
            "sub_task_agent": _TOOL_EXPERT_AGENT,
        },
    ]


def build_trend_tracking_plan_items(question: str) -> list[dict[str, str]]:
    """班级成绩趋势：查历次明细 + build_trend_tracking_report_data_tool。"""
    from src.agent.education.orchestrator import _extract_class_name, _extract_subject
    from src.agent.education.query_parse import extract_school_target

    school = extract_school_target(question) or ""
    class_name = _extract_class_name(question) or ""
    subject = _plan_subject_name(question) or (_extract_subject(question) or "")
    school_l = school or "该校"
    class_l = class_name or "该班"
    subject_l = f"【{subject}】" if subject else ""
    scope_args = []
    if school:
        scope_args.append(f"school_name={school}")
    if class_name:
        scope_args.append(f"class_name={class_name}")
    if subject:
        scope_args.append(f"subject_name={subject}")
    tool_args = ", ".join(scope_args)
    if tool_args:
        tool_args = tool_args + ", "
    return [
        {
            "sub_task": (
                f"查询【{school_l}】【{class_l}】历次{subject_l}考试每位学生分数"
                "（SQL 须含 exam_name、student_id、subject_name、score、exam_score；"
                "按考试时间排序；禁止只查一场、禁止只查 KPI 聚合）"
            ),
            "sub_task_agent": _DEFAULT_SUB_TASK_AGENT,
        },
        {
            "sub_task": (
                f"调 build_trend_tracking_report_data_tool({tool_args}render=true) "
                "生成【成绩趋势报告】（均分折线+明细表+进退步解读）；完成后 terminate。"
                "**禁止** render_html_report 手填 / build_comprehensive_report_data_tool"
            ),
            "sub_task_agent": _TOOL_EXPERT_AGENT,
        },
    ]


def should_replace_with_trend_tracking_plan(
    question: str,
    plan_items: list[dict[str, str]],
) -> bool:
    """走势/趋势：强制改走 trend_tracking 2 步计划。"""
    from src.agent.education.query_parse import is_trend_tracking_query

    if not is_trend_tracking_query(question):
        return False
    blob = " ".join(str(it.get("sub_task") or "") for it in plan_items)
    if "build_trend_tracking_report_data_tool" in blob:
        return False
    return True


def build_class_overview_plan_items(question: str) -> list[dict[str, str]]:
    """班级成绩总览：查该班学生分数 + 渲染 class_overview。"""
    from src.agent.education.orchestrator import _extract_class_name, _extract_subject
    from src.agent.education.query_parse import extract_school_target

    school = extract_school_target(question) or ""
    class_name = _extract_class_name(question) or ""
    subject = _plan_subject_name(question) or (_extract_subject(question) or "")
    exam = _plan_exam_name(question)
    school_l = school or "该校"
    class_l = class_name or "该班"
    subject_l = f"【{subject}】" if subject else ""
    exam_l = _plan_label(exam, missing="问题中的考试")
    scope_args = []
    if school:
        scope_args.append(f"school_name={school}")
    if class_name:
        scope_args.append(f"class_name={class_name}")
    if subject:
        scope_args.append(f"subject_name={subject}")
    if exam:
        scope_args.append(f"exam_name={exam}")
    tool_args = ", ".join(scope_args)
    if tool_args:
        tool_args = tool_args + ", "
    return [
        {
            "sub_task": (
                f"查询【{school_l}】【{class_l}】在【{exam_l}】{subject_l}每位学生得分"
                "（SQL 须含 student_id、score、exam_score；禁止只查班级 KPI 聚合）"
            ),
            "sub_task_agent": _DEFAULT_SUB_TASK_AGENT,
        },
        {
            "sub_task": (
                f"调 build_class_overview_report_data_tool({tool_args}render=true) "
                "生成班级总览 HTML；完成后 terminate。"
                "**禁止** build_subject_diagnosis_sections_tool / fetch 渲染"
            ),
            "sub_task_agent": _TOOL_EXPERT_AGENT,
        },
    ]


def should_replace_with_comprehensive_plan(
    question: str,
    plan_items: list[dict[str, str]],
) -> bool:
    """多场考试班级分析：未点名 comprehensive 工具时强制改走综合报告计划。"""
    from src.agent.education.query_parse import is_multi_exam_class_analysis_query

    if not is_multi_exam_class_analysis_query(question):
        return False
    blob = " ".join(str(it.get("sub_task") or "") for it in plan_items)
    if "build_comprehensive_report_data_tool" in blob:
        return False
    return True


def should_replace_with_individual_student_plan(
    question: str,
    plan_items: list[dict[str, str]],
) -> bool:
    """Planner 回落为单任务/未点名诊断工具时，改用个人知识点 2 步计划。"""
    from src.agent.education.query_parse import is_individual_student_analysis_query

    if not is_individual_student_analysis_query(question):
        return False
    q = (question or "").strip()
    if len(plan_items) <= 1:
        return True
    if len(plan_items) == 1 and (plan_items[0].get("sub_task") or "").strip() == q:
        return True
    # 已规划但漏掉个人诊断工具
    blob = " ".join(str(it.get("sub_task") or "") for it in plan_items)
    if "build_student_subject_diagnosis_tool" in blob:
        return False
    if "build_student_exam_report_data_tool" in blob:
        return False
    return True


def should_replace_with_school_class_comparison_plan(
    question: str,
    plan_items: list[dict[str, str]],
) -> bool:
    """各班横向对比：改用确定性全校 3 步（禁止 class_name）。"""
    from src.agent.education.query_parse import is_school_class_comparison_query

    if not is_school_class_comparison_query(question):
        return False
    blob = " ".join(str(it.get("sub_task") or "") for it in plan_items)
    # 已是禁止 class_name 的标准全校 3 步则保留
    if (
        len(plan_items) >= 3
        and "禁止传 class_name" in blob
        and "build_subject_diagnosis_sections_tool" in blob
        and not re.search(r"class_name\s*=\s*(?:高|初)[^\s,，)）]*", blob)
    ):
        return False
    return True


def should_replace_with_tier_alert_plan(
    question: str,
    plan_items: list[dict[str, str]],
) -> bool:
    """临界生/分层预警：强制改走 tier_alert 2 步计划。"""
    from src.agent.education.query_parse import is_tier_alert_query

    if not is_tier_alert_query(question):
        return False
    blob = " ".join(str(it.get("sub_task") or "") for it in plan_items)
    if "build_tier_alert_report_data_tool" in blob:
        return False
    return True


def should_replace_with_group_feature_plan(
    question: str,
    plan_items: list[dict[str, str]],
) -> bool:
    """群体特征：强制改走 group_feature 2 步计划。"""
    from src.agent.education.query_parse import is_group_feature_query

    if not is_group_feature_query(question):
        return False
    blob = " ".join(str(it.get("sub_task") or "") for it in plan_items)
    if "build_group_feature_report_data_tool" in blob:
        return False
    return True


def should_replace_with_class_overview_plan(
    question: str,
    plan_items: list[dict[str, str]],
) -> bool:
    """成绩总览/班级总览：强制改走 class_overview 2 步计划。"""
    from src.agent.education.query_parse import is_class_overview_query

    if not is_class_overview_query(question):
        return False
    blob = " ".join(str(it.get("sub_task") or "") for it in plan_items)
    if "build_class_overview_report_data_tool" in blob:
        return False
    if "education/class_overview.html" in blob:
        return False
    return True


def coerce_plan_items_if_needed(
    question: str,
    plan_items: list[dict[str, str]],
    *,
    route: Any | None = None,
) -> list[dict[str, str]]:
    """按意图路由纠正 LLM 计划；无 route 时同步分类后再对齐。"""
    from src.agent.education.intent_router import (
        ReportRoute,
        classify_report_intent_sync,
        coerce_plan_to_route,
        should_use_deterministic_report_plan,
    )

    q = (question or "").strip()
    if isinstance(route, ReportRoute):
        resolved = route
    else:
        resolved = classify_report_intent_sync(q)

    if not resolved.needs_report:
        return coerce_plan_to_route(q, plan_items, resolved)

    if should_use_deterministic_report_plan(q, resolved):
        return coerce_plan_to_route(q, plan_items, resolved)
    return plan_items


def should_replace_with_school_exam_plan(
    question: str,
    plan_items: list[dict[str, str]],
) -> bool:
    """学校科目报告（非各班横向专用）：改用确定性 3 步。"""
    from src.agent.education.query_parse import (
        is_school_class_comparison_query,
        is_school_exam_report_query,
    )

    if is_school_class_comparison_query(question):
        return False
    if not is_school_exam_report_query(question):
        return False
    if len(plan_items) <= 1:
        return True
    blob = " ".join(str(it.get("sub_task") or "") for it in plan_items)
    # 计划过碎或混入多种出报告工具 → 强制收成标准 3 步，避免刷多份空/半空报告
    report_builders = (
        "build_subject_diagnosis_sections_tool",
        "build_diagnostic_report_data_tool",
        "build_comprehensive_report_data_tool",
        "build_student_exam_report_data_tool",
        "build_student_subject_diagnosis_tool",
        "build_subject_diagnosis_report_tool",
        "render_html_report",
    )
    builder_hits = sum(1 for name in report_builders if name in blob)
    if len(plan_items) > 3 or builder_hits > 1:
        return True
    if "build_subject_diagnosis_sections_tool" in blob and "fetch_subject_diagnosis_data_tool" in blob:
        return False
    return True


def should_replace_with_citywide_plan(
    question: str,
    plan_items: list[dict[str, str]],
) -> bool:
    """Planner 回落为单任务时，全市分析类问题改用确定性 3 步计划。"""
    from src.agent.education.query_parse import is_citywide_analysis_query

    if not is_citywide_analysis_query(question):
        return False
    q = (question or "").strip()
    if len(plan_items) <= 1:
        return True
    if len(plan_items) == 1 and (plan_items[0].get("sub_task") or "").strip() == q:
        return True
    return False


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
    """拆解失败 → 原问题作为唯一子任务；报告类改用标准多步回落。"""
    from src.agent.education.query_parse import (
        is_individual_student_analysis_query,
        is_school_class_comparison_query,
        is_school_exam_report_query,
    )

    q = (question or "").strip() or "（原始问题）"
    if is_individual_student_analysis_query(q):
        items = build_individual_student_exam_plan_items(q)
        plans = [it["sub_task"] for it in items]
        plan_agents = [it["sub_task_agent"] for it in items]
        return ActionOutput(
            is_exe_success=True,
            content=f"计划回落为个人知识点诊断 2 步子任务（{reason}）",
            action="plan",
            extra={"plans": plans, "plan_agents": plan_agents},
            terminate=True,
        )
    if is_school_class_comparison_query(q) or is_school_exam_report_query(q):
        items = build_school_class_comparison_plan_items(q) if is_school_class_comparison_query(q) else build_school_subject_report_plan_items(q)
        plans = [it["sub_task"] for it in items]
        plan_agents = [it["sub_task_agent"] for it in items]
        return ActionOutput(
            is_exe_success=True,
            content=f"计划回落为学校报告 3 步子任务（{reason}）",
            action="plan",
            extra={"plans": plans, "plan_agents": plan_agents},
            terminate=True,
        )
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

    def _build_prompt_variables(self, reply: AgentMessage) -> dict[str, Any]:
        base = super()._build_prompt_variables(reply)
        raw = dict(reply.context or {}).get("constraints")
        constraints = raw if isinstance(raw, dict) else {}
        base["scope_constraints"] = format_scope_constraints(constraints)
        return base

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
