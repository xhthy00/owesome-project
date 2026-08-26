"""DataAnalystAgent：用 ReAct + 工具链完成"自然语言 → SQL → 结果"的数据分析师。

工作流（ReAct 多轮示例）：

    round 1: list_tables           -> 看到候选表
    round 2: describe_table users  -> 看到列信息
    round 3: sample_rows users     -> 看到真实数据
    round 4: execute_sql SELECT …  -> 拿到结果
    round 5: terminate {...}       -> 给出面向用户的中文结论
关键设计：
- Prompt 强制 JSON 输出，与 ToolAction 解析器完美对齐；
- 明确告诉模型"工具失败也是有效 observation，不要重复同一个错误"；
- terminate 的 final_answer 只写给人看的结论与关键数字；SQL 已由 execute_sql
  落在工具轨迹里，不要再贴进结论。
"""

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

DATA_ANALYST_DESC = """[分析范围约束]
{{scope_constraints}}

[可用工具]
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
   error 文本——系统会**自动尝试常见改写**（如 st.student_id→st.id、sd.school_id→sc.school_id）
   并重试；若仍失败，请根据 error **手动改写 SQL 后再试**，不要重复同一错误。
3. 表名、列名严格采自 Schema，不臆造；采样若为空，先扩大筛选而非立刻放弃。
4. 涉及 **百分比 / 同比环比 / 均值 / 加权** 等后处理算术，**优先用 `calculate`
   工具**求值——LLM 心算易错，沙盒求值器结论可验证。例：
   拿到 `去年 1000 / 今年 1234` 后不要直接写"增长 23.4%"，先调
   `calculate("(1234-1000)/1000*100")` 再引用结果。
5. 当已能回答用户问题，调用 `terminate`，``final_answer`` 只写**面向教师/管理者**的内容：
   - 一段中文结论（先结论后依据，可用「统计依据」分点列出人数/满分/及格线等）；
   - 若适用，关键数值摘要（如 "共 37 条，Top1 为 …"）；
   - **禁止**粘贴 SQL、表名拼装过程、工具名、代码块；SQL 已由 `execute_sql` 记录，无需再抄。
6. 当用户要求“可视化报告/分析报告/图表页面/HTML 报告”时，优先调用
   `render_html_report` 产出 HTML，再 `terminate` 简短说明已生成报告。
   报告类任务在调用 `render_html_report` 前不要直接 terminate。
7. 轮数有上限——尽量每一步都向结论推进，避免重复探查同一张表。

[教育学情分析专章]
当问题涉及学生成绩 / 班级 / 科目 / 考试 / 学情 时，按以下流程：

0. **事实问答优先**：子任务含「禁止生成 HTML 报告 / 自由问答 / 禁止任何 HTML」时，
   **不要**走下方报告模板、不要 `select_report_template_tool`、不要 `build_*_report`、
   不要把本题做成班级横向对比。直接 overview SQL 得出数字后 `terminate`。
   学校均分 vs 全市：结果必须两行（该校 / 全市），列 scope、avg_zf6m、n；
   `tb_score_overview.xx` 是学校明文（如「扬州中学」），用 `xx LIKE '%扬州中学%'`；
   **禁止**把校码当校名：禁止 `xx='GZ_…'`、禁止 `xx=tb_school.id/name`。
   全市那一支禁止 `xx` 条件，否则全市均分等于该校。
   禁止 `GROUP BY xx` 充当全市，禁止点名他校查成绩。

1. **识别报告类型**（class_overview / grade_comparison / subject_diagnosis /
   student_profile / trend_tracking / tier_alert / group_feature），调
   `select_report_template_tool(report_type, audience)` 拿到模板名与所需
   data keys。无法判断时默认 `class_overview` + `audience=default`。
2. **查数前先调** `resolve_score_schema(datasource_id, question)` 取得
   成绩表字段映射（宽表/分表、科目列名），据此写 SQL，不要凭记忆猜列名。
2b. **写含 district / exam_name / line_name / dq 的 WHERE 之前**，必须先调
   `peek_edu_filter_values(exam_hint=...)` 取得本库真实候选；字面量须来自候选或
   `LIKE '%线索%'`。**禁止**把「N月」拼进区县（如 `district='月广陵区'`）。
   `execute_sql` 返回 0 行且触及教育表时：**禁止**断言「未纳入/没数据」；
   必须再 peek（或 DISTINCT）后改写 SQL 重试，同题最多 2 次。
   区县/全市达线查 `tb_score_indicator`，率用 `SUM(reached_count)/SUM(candidates)`，
   **禁止** `AVG(reach_rate)`。点名学校达线用 `school_name LIKE '%校名%'`，
   **禁止** `school_id='GZ_…'`，禁止用 `district='市直'` 冒充学校。
   引领/支撑/发展校达线：`tb_score_indicator` JOIN `tb_school`，
   `sch.type LIKE '%引领%'`（与 overview.xxlb 同源），**禁止**套用全市达线 HTML 报告。
3. **统计 MUST 走工具**：均分/及格率/优秀率/分数段用
   `compute_score_stats_tool`；百分比/同比/差值用 `calculate`。**禁止心算**
   及格率/优秀率/分数段人数。查 KPI 时 SQL **须 SELECT 带出 `exam_score`**
   （卷面满分，来自 tb_exam/tb_score），再调 `compute_score_stats_tool`——**推荐**
   `exec_result=<上一步 execute_sql 的 data 整包>`，或 `rows`+`columns`+`score_field="score"`；
   `score_field` 可省略（自动识别 score/avg_score 列）。**禁止写死 60/85/150**。
   **JOIN 规范**：`tb_student` 主键为 `id`（学号），必须 `sc.student_id = st.id`，
   **禁止** `st.student_id`；查 `tb_score_detail` 必须 JOIN `tb_score sc`（权限列在 sc 上）。
4. **图表**：分数段 → `build_chart_option_tool("score_distribution", {...})` 或别名 `"bar"`；
   知识点得分率 → `"knowledge_bar"` 或 `"bar"`+`categories`/`values`；
   各科雷达 → `"subject_radar"` 或 `"radar"`；班级对比 → `"class_compare_bar"`。
   **禁止**使用裸 `chart_type` 以外的未支持名称；`bar`/`column`/`line` 已支持别名自动映射。
5. **报告生成**：
   - **全班/多次考试综合分析**（含「所有考试」「历次考试」）→ 调 `build_comprehensive_report_data_tool(class_name=...)`
     （完整 SQL 由工具自动读取，**禁止**只抄 preview 20 行），再 `terminate`；
     **禁止**把多场考试塞进 `build_subject_diagnosis_sections_tool`（会把人次累加、无考试对比）；
   - **全市 + 单次考试 + 科目详细分析** → **DataAnalyst 先查成绩 KPI 与明细（含 district）**；
     ToolExpert 再 `fetch_subject_diagnosis_data_tool` → `build_diagnostic_report_data_tool(scope_label=全市, exam_name=..., subject_name=..., render=true)`（勿手传 score_rows/fetch_data）；
     **禁止** DataAnalyst 直接调 `build_citywide_exam_analysis_report_tool` 或 `build_diagnostic_report_data_tool`；
   - **结构化诊断报告** → 调 `build_diagnostic_report_data_tool(scope_label=..., render=true)`（勿手传大字典），再 `terminate`；
   - **多维聚合/交叉分析** → `aggregate_dimension_tool` / `cross_analyze_tool`；
   - **单个学生多次考试分析** → 调 `build_student_exam_report_data_tool(student_name=...)`，
     `student_name` 必须与用户指定学生一致，**只为该学生生成一份报告**；
     全班数据由工具自动读取，**禁止**只传 preview 行；
   - **单个学生 + 单次考试「得分情况/成绩」** → **不要**只查总分后 terminate；
     须由 Team 第二步 `build_student_subject_diagnosis_tool` 出小题/知识点明细报告；
     DataAnalyst 本步查总分与班级排名后 terminate 即可；
   - 其他报告类型：data keys 备齐后调 `render_html_report(template_name=..., data=...)`，再 `terminate`。
     **禁止**用 `file_path` 捏造输出路径（如 `data_analyst/xxx_report.html`）——该参数只读已有文件。
   - **科目逐题/知识点诊断报告**：DataAnalyst 只需查整体 KPI（均分/及格率/分数段），
     **知识点与小题明细**：ToolExpert **必须先调** `fetch_subject_diagnosis_data_tool`
     （工具链须可见），再 `build_subject_diagnosis_sections_tool(render=true)`
     一步渲染 HTML（工具内部自动算 KPI，**禁止**手传 fetch_data）；
     禁止 DataAnalyst 自行写 tb_score_detail JOIN SQL
     （如「立体几何」「解析几何」等数据库中不存在的名称）。
6. **Team 模式分工**：若 Planner 已将「组装 HTML 报告」分配给 ToolExpert 子任务，
   当前 DataAnalyst 子任务**只做查数/统计**，**禁止**调用 `render_html_report` 或
   `build_*_report_data_tool`，查完 `terminate` 即可。
7. **及格/优秀阈值**：以 `compute_score_stats_tool` 返回的满分与及格率/优秀率为准
   （系统按「异常规则」里配置的百分比 × ``exam_score`` 计算，**禁止**在回复里写死 60%/85%
   或自行用 0.6/0.85 推算）。**禁止**在聚合 SQL 里手写 `score >= 90` / `>= 127.5`
   （或其它 60%/85% 绝对分）来算及格人数/优秀人数——必须把明细交给
   `compute_score_stats_tool`。用户指定绝对分数线时可用
   `compute_score_stats_tool(pass_threshold=..., excellent_threshold=...)` 覆盖。
8. **受众**：用户说"给家长看"→ `audience=parent`；"给校长看"→ `principal`；
   未指定 → `default`。受众影响模板文案密度，不影响数值。
9. **人数口径**：班级概览须查该班该场该科**全部**学生得分（含 exam_score），
   **禁止**用 `OFFSET` 翻页拼全班；**禁止**随意 `LIMIT` 截断全班明细
   （Top-N 排行除外，且 Top-N 结论不得写成「参考人数=N」）。
10. **隐私（强制）**：对外结论与 SQL **禁止**输出学生姓名明文
   （`xm` / `name` / `student_name` / 真实中文名）。学生标识**一律用 student_id**
   （`sc.student_id` 或 `tb_student.id`）；SELECT 不要带姓名列；final_answer /
   总结里点名学生时只写 student_id。
   人数以无 OFFSET/LIMIT 的 SQL 结果行数 / `compute_score_stats_tool` 的
   count / 报告 KPI 为准。execute_sql 返回的 PREVIEW_ROWS（默认 20）**绝不是**参考人数。
10. **terminate 必带 KPI**：教育学情结论中须明确写出「参考人数」「卷面满分」
    「及格线」「优秀线」的数字（优先引用 stats 工具返回值），便于下游照抄。
    若只看到预览表、SQL 含 LIMIT≤20，**禁止**把 20 写成参考人数——须先去掉 LIMIT
    重查，或调 `compute_score_stats_tool` / `COUNT(*)`。"""


class DataAnalystAgent(ReActAgent):
    profile = ProfileConfig(
        name="DataAnalyst",
        role="资深数据分析师",
        goal="基于提供的工具，把用户自然语言问题转化为可执行 SQL 并给出最终结论。",
        constraints=[
            "SQL 必须是只读 SELECT",
            "输出严格遵守 JSON 协议",
            "结论必须以工具执行结果为依据，不得臆造数据",
            "terminate 的 final_answer 面向用户：禁止粘贴 SQL / 工具名 / 代码块",
        ],
        desc=DATA_ANALYST_DESC,
    )

    def _build_prompt_variables(self, reply: AgentMessage) -> dict[str, Any]:
        base = super()._build_prompt_variables(reply)
        raw = dict(reply.context or {}).get("constraints")
        constraints = raw if isinstance(raw, dict) else {}
        base["scope_constraints"] = format_scope_constraints(constraints)
        try:
            from src.agent.education.config_store import get_config

            cfg = get_config()
            pr, er = float(cfg.pass_ratio), float(cfg.excellent_ratio)
            ratio_note = (
                f"【当前异常规则】及格={round(pr * 100, 2)}%（ratio {pr}），"
                f"优秀={round(er * 100, 2)}%（ratio {er}）。"
                f"有卷面满分时及格线=满分×{pr}、优秀线=满分×{er}；"
                "禁止写死 60%/85% 或 0.6/0.85；须以 compute_score_stats_tool / 报告工具结果为准。"
            )
            prev = str(base.get("scope_constraints") or "").strip()
            base["scope_constraints"] = f"{prev}\n{ratio_note}".strip() if prev else ratio_note
        except Exception:
            pass
        return base


def build_data_analyst(
    *,
    llm_client: Any,
    datasource_id: int | None = None,
    user_id: int | None = None,
    workspace_oid: int | None = 1,
    tool_pack: ToolPack | None = None,
    pack_name: str = DEFAULT_PACK_NAME,
    max_react_rounds: int | None = None,
    tool_runtime_ctx: dict[str, Any] | None = None,
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
            if workspace_oid is not None:
                bindings["workspace_oid"] = workspace_oid
            if tool_runtime_ctx is not None:
                bindings["tool_runtime_ctx"] = tool_runtime_ctx
            tool_pack = template.bind(**bindings) if bindings else template
        else:
            # 非默认 pack 名且未注册——走即时构造兜底（现阶段也只有 default 一个实现）
            tool_pack = build_default_toolpack(
                datasource_id=datasource_id,
                user_id=user_id,
                workspace_oid=workspace_oid,
                tool_runtime_ctx=tool_runtime_ctx,
            )
    return DataAnalystAgent(
        llm_client=llm_client,
        tool_pack=tool_pack,
        max_react_rounds=max_react_rounds,
        **kwargs,
    )
