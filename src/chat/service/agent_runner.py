"""Agent / Team 模式的 chat-stream 执行器。

两个公开入口：

- :func:`run_agent_stream` —— 单 Agent（DataAnalyst ReAct）模式。
- :func:`run_team_stream`  —— 四节点线性 team：
    DataAnalyst → Charter → Summarizer（Planner 留给 Phase C-2）。

两个入口共享前半段"跑 DataAnalyst + 累计 state"的逻辑（见
:func:`_run_data_analyst_phase`），差别仅在后处理和持久化字段。

设计约束：
- 全程 async，不开线程池；底层 DB 访问由 ``FunctionTool`` 自动
  ``asyncio.to_thread``，持久化环节显式放到线程池（SQLAlchemy session
  不跨线程）；
- 不抛异常到调用方：所有异常转成 ``error`` SSE 事件，record_id 返回 0；
- 不负责关闭 SSE 流（``done`` / SENTINEL 由调用方发）。
"""

from __future__ import annotations

import asyncio
import html as html_lib
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from src.agent.adapter.llm_adapter import LangChainLlmClient
from src.agent.core.agent import AgentMessage
from src.agent.education.query_parse import (
    build_edu_aware_constraints,
    extract_upstream_participant_count,
    report_matches_school,
    report_matches_student,
    report_participant_count_conflicts,
)
from src.agent.expand.charter import CharterAgent
from src.agent.expand.chat_awel_team import build_chat_team
from src.agent.expand.data_analyst import build_data_analyst
from src.agent.expand.planner import PlannerAgent
from src.agent.expand.summarizer import SummarizerAgent
from src.agent.expand.tool_agent import build_tool_agent
from src.agent.expand.user_proxy import UserProxyAgent
from src.agent.util.json_parser import parse_json_tolerant
from src.chat.schemas import ChatRequest
from src.common.core.config import get_settings

logger = logging.getLogger(__name__)

EmitCallback = Callable[[str, dict[str, Any]], Awaitable[None]]

# Summarizer / Charter 上下文里塞给 LLM 的样例行数上限，避免 prompt 爆炸
_SAMPLE_ROWS_LIMIT = 20
_SQL_TABLE_RE = re.compile(r"""(?i)\b(?:from|join)\s+([`"]?[A-Za-z0-9_.]+[`"]?)""")
_ADHOC_REPORT_MAX_HTML = 120_000
_ADHOC_REPORT_TYPE_LABEL = "自主分析报告"
_ADHOC_ECHARTS_CDN = "https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"
_ADHOC_ECHARTS_BOOTSTRAP = """
<script>
(function () {
  if (typeof echarts === 'undefined') return;
  document.querySelectorAll('script[type="application/json"][data-echart-for]').forEach(function (node) {
    var id = node.getAttribute('data-echart-for');
    var el = id ? document.getElementById(id) : null;
    var raw = (node.textContent || '').trim();
    if (!el || !raw) return;
    try { echarts.init(el).setOption(JSON.parse(raw)); } catch (e) { el.style.display = 'none'; }
  });
})();
</script>
"""
# 与教育报告视觉对齐的轻量样式：片段壳与完整 HTML 均可注入
_ADHOC_REPORT_CSS = """
<style id="adhoc-report-polish">
:root {
  --adhoc-primary: #1677ff;
  --adhoc-primary-soft: #e6f4ff;
  --adhoc-accent: #0ea5e9;
  --adhoc-text: rgba(0,0,0,0.88);
  --adhoc-muted: rgba(0,0,0,0.45);
  --adhoc-border: #eef0f3;
  --adhoc-bg: #f0f2f5;
  --adhoc-card: #ffffff;
}
* { box-sizing: border-box; }
body.adhoc-body, .adhoc-page {
  margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
  background: var(--adhoc-bg);
  color: var(--adhoc-text);
  line-height: 1.65;
}
.adhoc-page { max-width: 980px; margin: 0 auto; padding: 20px 16px 36px; }
.adhoc-hero {
  background: linear-gradient(135deg, #1677ff 0%, #0ea5e9 100%);
  color: #fff;
  border-radius: 12px;
  padding: 22px 26px;
  margin-bottom: 16px;
  box-shadow: 0 8px 24px rgba(22, 119, 255, 0.18);
}
.adhoc-hero h1 { margin: 8px 0 0; font-size: 22px; font-weight: 650; line-height: 1.35; color: #fff; }
.adhoc-badge {
  display: inline-block;
  padding: 3px 10px;
  border-radius: 999px;
  font-size: 12px;
  background: rgba(255,255,255,0.2);
  border: 1px solid rgba(255,255,255,0.35);
}
.adhoc-card {
  background: var(--adhoc-card);
  border: 1px solid var(--adhoc-border);
  border-radius: 12px;
  padding: 20px 22px;
  margin-bottom: 14px;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
}
.adhoc-card h2, .adhoc-body-card h2, .adhoc-charts > h2 {
  margin: 0 0 12px;
  font-size: 16px;
  font-weight: 650;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--adhoc-border);
  color: var(--adhoc-text);
}
.adhoc-card h3, .adhoc-chart-card h3 {
  margin: 0 0 8px;
  font-size: 14px;
  font-weight: 600;
  color: rgba(0,0,0,0.65);
}
.adhoc-body-card p { margin: 8px 0; }
.adhoc-body-card ul, .adhoc-body-card ol { margin: 8px 0; padding-left: 22px; }
.adhoc-body-card li { margin: 6px 0; }
.adhoc-body-card table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
  margin: 12px 0;
  overflow: hidden;
  border-radius: 8px;
}
.adhoc-body-card th, .adhoc-body-card td {
  border: 1px solid var(--adhoc-border);
  padding: 10px 12px;
  text-align: left;
}
.adhoc-body-card thead th {
  background: var(--adhoc-primary-soft);
  color: rgba(0,0,0,0.65);
  font-weight: 600;
}
.adhoc-body-card tbody tr:nth-child(even) td { background: #fafafa; }
.adhoc-charts { margin-top: 4px; }
.adhoc-chart-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}
.adhoc-chart-card { margin-bottom: 0; }
.adhoc-chart {
  width: 100%;
  height: 320px;
  margin: 4px 0 0;
  border-radius: 8px;
  background: linear-gradient(180deg, #fafcff 0%, #fff 100%);
}
.adhoc-footer {
  margin-top: 8px;
  text-align: right;
  font-size: 12px;
  color: var(--adhoc-muted);
}
@media (max-width: 760px) {
  .adhoc-page { padding: 12px 10px 24px; }
  .adhoc-hero { padding: 16px 18px; }
  .adhoc-hero h1 { font-size: 18px; }
  .adhoc-chart-grid { grid-template-columns: 1fr; }
  .adhoc-chart { height: 280px; }
}
@media print {
  body.adhoc-body, .adhoc-page { background: #fff; }
  .adhoc-hero { box-shadow: none; }
  .adhoc-card { box-shadow: none; break-inside: avoid; }
}
</style>
"""
_ADHOC_REPORT_SYSTEM = """你是聊天 Team 的自主报告决策与撰写助手。
在子任务已完成、且尚未产出预设 HTML 报告时，根据用户问题与分析结论，决定是否需要一份独立 HTML 报告。

[何时 generate=true]
- 多维对比、分层/趋势/诊断等需要结构化「一页纸」呈现
- 结论含多指标表格，领导/教师更适合读报告而非长文聊天
- 有实质数据可支撑章节（非空结果）

[何时 generate=false]
- 单点事实问答（人数、均分、是谁、有没有等一句可答完）
- 数据为空、查询失败、或结论已足够无需另开报告

[输出]
只输出一个 JSON 对象（可包在 ```json 中），字段：
- generate: bool
- reason: 短说明
- title: 报告标题（仅 generate=true）
- html: HTML **正文片段**（仅 generate=true；不要写完整 <html>/<head>/<body>，样式由系统壳提供）
  中文；围绕结论；数字必须来自输入；用语义结构：
  <section><h2>核心结论</h2><p>...</p></section>
  <section><h2>关键指标</h2><table>...</table></section>
  <section><h2>教学建议</h2><ul>...</ul></section>
  **禁止**在 html 内写 echarts CDN、script、data-echart-for、内联大段 CSS 或图表 option。
- charts: 数组（generate=true 且有可对比数据时**必填，至少 2 项**）
  每项：{"id":"chartRadar","title":"图标题","option":{...完整 ECharts option...}}
  优先 radar（areaStyle.opacity≈0.25）与 scatter（symbolSize≈12），辅以 bar/line/pie。
  option.series 必填；数字必须来自输入。散点 data 为 [[x,y],...]。

不要输出 JSON 以外的解释。"""


@dataclass
class _RunConstraints:
    """team 会话级约束（第 1 步：只做状态与传递，不做拦截）。"""

    locked_tables: list[str]
    required_keywords: list[str]
    source_sub_task_index: int | None = None
    #: 上游 DataAnalyst 子任务产出的结构化数据（exec_result / reports / stats），
    #: 供下游 ToolExpert 组装报告时复用，避免重复查数。Phase 2 引入。
    report_data: dict[str, Any] | None = None
    #: 报告受众（principal / head_teacher / parent ...），由前端或问题推断注入。
    report_audience: str | None = None
    #: 用户问题中指定的目标学生（如「学生001」），用于过滤偏离报告。
    target_student: str | None = None
    #: 用户问题中指定的目标学校（如「南京市第一中学」），用于 SQL 范围约束。
    target_school: str | None = None
    #: 教育权限绑定的班级列表（teacher 角色）。
    target_classes: list[str] | None = None
    #: 用户 education 权限摘要（edu_role / school / class_names 等）。
    edu_scope: dict[str, Any] | None = None
    #: 意图路由结果（report_type / confidence / source），供工具守卫读取。
    report_route: dict[str, Any] | None = None
    #: 教育问数短提示（按意图裁剪的 SQL 规则摘要）。
    edu_sql_hint: str | None = None

    def to_context(self) -> dict[str, Any]:
        ctx: dict[str, Any] = {
            "locked_tables": list(self.locked_tables),
            "required_keywords": list(self.required_keywords),
            "source_sub_task_index": self.source_sub_task_index,
        }
        if self.report_data is not None:
            ctx["report_data"] = self.report_data
        if self.report_audience is not None:
            ctx["report_audience"] = self.report_audience
        if self.target_student is not None:
            ctx["target_student"] = self.target_student
        if self.target_school is not None:
            ctx["target_school"] = self.target_school
        if self.target_classes:
            ctx["target_classes"] = list(self.target_classes)
        if self.edu_scope:
            ctx["edu_scope"] = dict(self.edu_scope)
        if self.report_route:
            ctx["report_route"] = dict(self.report_route)
        if self.edu_sql_hint:
            ctx["edu_sql_hint"] = self.edu_sql_hint
        return ctx

    @classmethod
    def from_context(cls, raw: dict[str, Any] | None) -> "_RunConstraints":
        data = raw if isinstance(raw, dict) else {}
        tc = data.get("target_classes")
        rr = data.get("report_route")
        hint = data.get("edu_sql_hint")
        return cls(
            locked_tables=list(data.get("locked_tables") or []),
            required_keywords=list(data.get("required_keywords") or []),
            source_sub_task_index=data.get("source_sub_task_index"),
            report_data=data.get("report_data"),
            report_audience=data.get("report_audience"),
            target_student=data.get("target_student"),
            target_school=data.get("target_school"),
            target_classes=list(tc) if isinstance(tc, list) else None,
            edu_scope=dict(data["edu_scope"]) if isinstance(data.get("edu_scope"), dict) else None,
            report_route=dict(rr) if isinstance(rr, dict) else None,
            edu_sql_hint=str(hint).strip() if hint else None,
        )


def _load_edu_scope_summary(user_id: int) -> dict[str, Any]:
    from datasource.service.edu_permission import edu_scope_dict_for_user_id

    return edu_scope_dict_for_user_id(user_id)


def _build_shared_constraints(question: str, user_id: int) -> _RunConstraints:
    edu = _load_edu_scope_summary(user_id)
    merged = build_edu_aware_constraints(
        question,
        edu,
        required_keywords=_extract_required_keywords(question),
    )
    return _RunConstraints(
        locked_tables=[],
        required_keywords=list(merged.get("required_keywords") or []),
        source_sub_task_index=None,
        target_school=merged.get("target_school"),
        target_student=merged.get("target_student"),
        target_classes=merged.get("target_classes"),
        edu_scope=merged.get("edu_scope"),
        edu_sql_hint=merged.get("edu_sql_hint"),
    )


def _extract_required_keywords(question: str) -> list[str]:
    q = (question or "").strip()
    if not q:
        return []
    # 轻量关键词：中文连续段 + 英文 token，长度 >= 2。仅用于上下文提示，不作强校验。
    tokens = re.findall(r"[A-Za-z0-9_]{2,}|[\u4e00-\u9fff]{2,}", q)
    seen: set[str] = set()
    out: list[str] = []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out[:12]


def _normalize_ident(v: str) -> str:
    return str(v or "").strip().strip("`\"").lower()


# 考试批次/学校等维表只用于对照名称，describe 后不能把后续 SQL/报告锁死在这张表上。
_NON_LOCKABLE_TABLES = frozenset(
    {
        "tb_exam_batch",
        "tb_exam",
        "tb_school",
        "tb_student",
        "tb_knowledge",
        "tb_exam_question",
        "tb_exam_question_knowledge",
        "tb_fraction_bar",
    }
)


def _is_lockable_table(table_name: str) -> bool:
    name = _normalize_ident(table_name)
    return bool(name) and name not in _NON_LOCKABLE_TABLES


def _extract_sql_tables(sql: str) -> list[str]:
    out: list[str] = []
    for m in _SQL_TABLE_RE.finditer(sql or ""):
        t = _normalize_ident(m.group(1))
        if t and t not in out:
            out.append(t)
    return out


def _sql_hits_locked_tables(sql: str, locked_tables: list[str]) -> bool:
    if not sql or not locked_tables:
        return True
    hit = _extract_sql_tables(sql)
    allowed = {_normalize_ident(x) for x in locked_tables if str(x or "").strip()}
    if not allowed:
        return True
    return any(t in allowed for t in hit)


# --------------------------------------------------------------------------- #
# 公开入口
# --------------------------------------------------------------------------- #


async def run_agent_stream(
    *,
    request: ChatRequest,
    current_user_id: int,
    emit: EmitCallback,
    llm_client: Any | None = None,
    persist: bool = True,
    enable_tool_agent: bool = True,
    workspace_oid: int = 1,
) -> int:
    """单 Agent 模式：跑 DataAnalyst ReAct 循环，全程 emit SSE。

    Returns:
        新建的 record_id；若无 conversation_id 或持久化失败则返回 0。
    """
    from src.agent.adapter.usage_sink import usage_tracking

    async with usage_tracking(emit):
        phase = await _run_data_analyst_phase(
            request=request,
            current_user_id=current_user_id,
            emit=emit,
            llm_client=llm_client,
            constraints=_build_shared_constraints(request.question, current_user_id),
            workspace_oid=workspace_oid,
        )
        if phase.fatal_error:
            return 0

        if not phase.terminated:
            await emit(
                "error", {"error": phase.fail_reason or "agent did not reach a final answer"}
            )

        raw_answer = (phase.reply.content if phase.reply else "") or ""
        # 与 team Summarizer 共用权威对齐：任意提问都纠正预览/LIMIT 人数幻觉
        reconciled = _reconcile_phase_answer(raw_answer, phase) if raw_answer else ""
        if reconciled and reconciled != raw_answer:
            await emit("summary", {"content": reconciled})
        elif reconciled and phase.is_success:
            # 即使未改写，也发 summary，保证前端气泡与落库口径一致
            await emit("summary", {"content": reconciled})

        if not persist:
            return 0

        return await _persist_async(
            request=request,
            current_user_id=current_user_id,
            question=request.question,
            sql=phase.state.last_sql,
            sql_error=None if phase.is_success else (phase.fail_reason or ""),
            exec_result=phase.state.last_exec_result,
            is_success=phase.is_success,
            reasoning=reconciled or raw_answer,
            steps=list(phase.state.steps),
            chart_type="table",
            chart_config=None,
            agent_mode="agent",
            tool_calls=list(phase.state.tool_calls),
            reports=list(phase.state.reports),
            summary=reconciled or None,
            workspace_oid=workspace_oid,
        )


async def run_team_stream(
    *,
    request: ChatRequest,
    current_user_id: int,
    emit: EmitCallback,
    llm_client: Any | None = None,
    persist: bool = True,
    enable_tool_agent: bool = True,
    workspace_oid: int = 1,
) -> int:
    """Team 模式：Planner → N × DataAnalyst → Charter → Summarizer。

    流水线语义：

    1. Planner 把用户问题拆成 N 个 sub_task（失败时 N=1，即原问题）；
    2. 串行跑 N 次 DataAnalyst，每次独立 ReAct 上下文；
    3. Chart 基于**最后一个成功**的 sub_task 推荐图表；
    4. Summarizer 综合**所有** sub_task 的 SQL+结果给出中文结论；
    5. 若子任务未产出预设 HTML 报告，AdHoc 阶段由 LLM 决定是否围绕结论
       自主撰写报告（``report`` SSE，``report_type_label=自主分析报告``）；
    6. 持久化：``sql`` / ``exec_result`` / ``chart_type`` 来自最后一个成功
       sub_task；``reasoning`` 来自 Summarizer 最终结论；``steps`` 里按
       sub_task 分组累计所有 DataAnalyst 回合。

    失败分治：
    - Planner 失败 → 回落为 1 个 sub_task（原问题），继续往下走；
    - 某个 DataAnalyst 失败 → emit plan_update(state=error)，继续下个 sub_task；
    - 全部 DataAnalyst 都失败 → 跳过 Chart/Summarizer，emit error；
    - Chart 失败 → chart_type=table；Summarizer 失败 → 回落 DataAnalyst 原文。

    编排实现由配置 ``team_orchestrator``（环境变量 ``TEAM_ORCHESTRATOR``）选择：
    ``langgraph``（默认）或 ``legacy``。
    """
    from src.agent.adapter.usage_sink import usage_tracking

    async with usage_tracking(emit):
        if get_settings().team_orchestrator == "langgraph":
            from src.chat.service.team_graph import run_team_stream_graph

            return await run_team_stream_graph(
                request=request,
                current_user_id=current_user_id,
                emit=emit,
                llm_client=llm_client,
                persist=persist,
                enable_tool_agent=enable_tool_agent,
                workspace_oid=workspace_oid,
            )
        return await _run_team_stream_legacy(
            request=request,
            current_user_id=current_user_id,
            emit=emit,
            llm_client=llm_client,
            persist=persist,
            enable_tool_agent=enable_tool_agent,
            workspace_oid=workspace_oid,
        )


async def _run_team_stream_legacy(
    *,
    request: ChatRequest,
    current_user_id: int,
    emit: EmitCallback,
    llm_client: Any | None = None,
    persist: bool = True,
    enable_tool_agent: bool = True,
    workspace_oid: int = 1,
) -> int:
    """Team 模式手写协程实现（``team_orchestrator=legacy``）。"""
    if llm_client is None:
        llm_client = LangChainLlmClient()
    team_cfg = build_chat_team(enable_tool_agent=enable_tool_agent)

    shared_constraints = _build_shared_constraints(request.question, current_user_id)
    all_steps: list[dict[str, Any]] = []
    plan_items = await _run_planner_phase(
        request=request,
        llm_client=llm_client,
        emit=emit,
        constraints=shared_constraints,
        steps=all_steps,
    )
    plans = [it["sub_task"] for it in plan_items]
    plan_agents = [team_cfg.resolve_sub_task_agent(it["sub_task_agent"]) for it in plan_items]
    await emit("plan", {"plans": plans, "sub_task_agents": plan_agents})
    upstream_report_data: dict[str, Any] = {"sub_tasks": []}

    sub_phases: list[tuple[str, _DataAnalystPhase]] = []
    last_good_phase: _DataAnalystPhase | None = None

    for idx, item in enumerate(plan_items):
        sub_task = item["sub_task"]
        sub_task_agent = team_cfg.resolve_sub_task_agent(item["sub_task_agent"])
        await emit(
            "plan_update",
            {
                "index": idx,
                "state": "running",
                "sub_task": sub_task,
                "sub_task_agent": sub_task_agent,
            },
        )
        if sub_task_agent == "ToolExpert":
            shared_constraints.report_data = upstream_report_data or None
            phase = await _run_tool_expert_phase(
                request=request,
                current_user_id=current_user_id,
                emit=emit,
                llm_client=llm_client,
                question_override=sub_task,
                sub_task_index=idx,
                constraints=shared_constraints,
                workspace_oid=workspace_oid,
            )
        else:
            phase = await _run_data_analyst_phase(
                request=request,
                current_user_id=current_user_id,
                emit=emit,
                llm_client=llm_client,
                question_override=sub_task,
                sub_task_index=idx,
                constraints=shared_constraints,
                workspace_oid=workspace_oid,
            )
        upstream_report_data["sub_tasks"].append({
            "sub_task_index": idx,
            "sub_task": sub_task,
            "sub_task_agent": sub_task_agent,
            "sql": phase.state.last_sql,
            "exec_result": phase.state.last_exec_result,
            "reports": list(phase.state.reports),
            "tool_calls": list(phase.state.tool_calls),
            "final_answer": (phase.reply.content if phase.reply else ""),
        })
        if sub_task_agent != "ToolExpert" and phase.state.last_exec_result:
            from src.agent.education.query_parse import extract_score_rows_from_report_data

            cached_rows = extract_score_rows_from_report_data(upstream_report_data)
            if cached_rows:
                upstream_report_data["sub_tasks"][-1]["score_rows"] = cached_rows
        # 把本 sub_task 的 steps 标上前缀后汇总，便于前端按子任务折叠
        for step in phase.state.steps:
            tagged = dict(step)
            tagged["sub_task_index"] = idx
            all_steps.append(tagged)
        sub_phases.append((sub_task, phase))

        if phase.fatal_error:
            await emit(
                "plan_update",
                {"index": idx, "state": "error", "error": phase.fail_reason},
            )
            # 框架级异常（如 LLM 不可达）一般是不可恢复的，直接中断后续 sub_task
            break

        if phase.is_success:
            last_good_phase = phase
            await emit(
                "plan_update",
                {
                    "index": idx,
                    "state": "ok",
                    "sub_task_agent": sub_task_agent,
                    "sql": phase.state.last_sql,
                    "row_count": (
                        (phase.state.last_exec_result or {}).get("row_count") or 0
                    ),
                },
            )
        else:
            await emit(
                "plan_update",
                {
                    "index": idx,
                    "state": "error",
                    "sub_task_agent": sub_task_agent,
                    "error": phase.fail_reason,
                },
            )

    plan_states_for_persist: list[dict[str, Any]] = []
    for idx, (sub_task, phase) in enumerate(sub_phases):
        state = "ok" if phase.is_success else "error"
        plan_states_for_persist.append(
            {
                "index": idx,
                "sub_task": sub_task,
                "sub_task_agent": plan_agents[idx] if idx < len(plan_agents) else "DataAnalyst",
                "state": state,
                "error": None if phase.is_success else (phase.fail_reason or ""),
                "sql": phase.state.last_sql if phase.is_success else None,
                "row_count": (
                    (phase.state.last_exec_result or {}).get("row_count")
                    if phase.is_success
                    else None
                ),
            }
        )

    # 所有 sub_task 都失败时，跳过 Chart/Summarizer（省 LLM）
    if last_good_phase is None:
        overall_reason = _first_non_empty(
            [p.fail_reason for _, p in sub_phases]
        ) or "all sub tasks failed"
        await emit("error", {"error": overall_reason})
        if persist:
            return await _persist_async(
                request=request,
                current_user_id=current_user_id,
                question=request.question,
                sql="",
                sql_error=overall_reason,
                exec_result=None,
                is_success=False,
                reasoning="",
                steps=all_steps,
                chart_type="table",
                chart_config=None,
                agent_mode="team",
                plans=plans,
                sub_task_agents=plan_agents,
                plan_states=plan_states_for_persist,
                tool_calls=[tc for _, p in sub_phases for tc in p.state.tool_calls],
                reports=[rp for _, p in sub_phases for rp in p.state.reports],
                workspace_oid=workspace_oid,
            )
        return 0

    chart_type, chart_config = await _run_charter(
        question=request.question,
        state=last_good_phase.state,
        llm_client=llm_client,
        emit=emit,
        steps=all_steps,
    )
    await emit("chart", {"chart_type": chart_type, "chart_config": chart_config})

    default_summary = last_good_phase.reply.content if last_good_phase.reply else ""
    rr = shared_constraints.report_route if shared_constraints else None
    needs_report = True
    if isinstance(rr, dict) and "needs_report" in rr:
        needs_report = bool(rr.get("needs_report"))
    fact_answer = not needs_report
    summary_text = await _run_summarizer_multi(
        question=request.question,
        sub_phases=sub_phases,
        llm_client=llm_client,
        emit=emit,
        fallback=default_summary,
        report_data=upstream_report_data,
        steps=all_steps,
        fact_answer=fact_answer,
    )
    await emit("summary", {"content": summary_text})

    # needs_report=false：只需自然语言结论，禁止再生成自主报告
    if needs_report:
        await _run_ad_hoc_report_phase(
            question=request.question,
            summary_text=summary_text,
            sub_phases=sub_phases,
            llm_client=llm_client,
            emit=emit,
            steps=all_steps,
            report_data=upstream_report_data,
            chart_type=chart_type,
            report_route=rr if isinstance(rr, dict) else None,
        )

    if not persist:
        return 0

    return await _persist_async(
        request=request,
        current_user_id=current_user_id,
        question=request.question,
        sql=last_good_phase.state.last_sql,
        sql_error=None,
        exec_result=last_good_phase.state.last_exec_result,
        is_success=True,
        reasoning=summary_text or "",
        steps=all_steps,
        chart_type=chart_type,
        chart_config=chart_config,
        agent_mode="team",
        plans=plans,
        sub_task_agents=plan_agents,
        plan_states=plan_states_for_persist,
        tool_calls=[tc for _, p in sub_phases for tc in p.state.tool_calls],
        summary=summary_text or None,
        reports=[rp for _, p in sub_phases for rp in p.state.reports],
        workspace_oid=workspace_oid,
    )


def _first_non_empty(items: list[str]) -> str:
    for s in items:
        if s:
            return s
    return ""


async def _emit_agent_speak(
    emit: EmitCallback,
    *,
    agent: str,
    status: str,
    steps: list[dict[str, Any]] | None = None,
    **extra: Any,
) -> None:
    """广播 agent_speak，并同步写入 steps 供落库 / 历史回放（与 SSE 一致）。"""
    payload: dict[str, Any] = {"agent": agent, "status": status, **extra}
    await emit("agent_speak", payload)
    if steps is None:
        return
    err = str(extra.get("error") or "")
    detail = err
    if not detail and status == "end":
        if extra.get("chart_type") is not None:
            detail = f"chart_type={extra.get('chart_type')}"
        elif extra.get("plan_count") is not None:
            detail = f"plan_count={extra.get('plan_count')}"
        elif extra.get("summary_preview"):
            detail = str(extra.get("summary_preview"))
    step_status = "error" if status == "error" else ("running" if status == "start" else "ok")
    step: dict[str, Any] = {
        "name": f"{agent}:{status}",
        "label": f"{agent}: {status}",
        "status": step_status,
        "detail": detail,
    }
    if extra.get("sub_task_index") is not None:
        step["sub_task_index"] = extra["sub_task_index"]
    steps.append(step)


# --------------------------------------------------------------------------- #
# DataAnalyst 阶段（两个入口共享）
# --------------------------------------------------------------------------- #


@dataclass
class _DataAnalystPhase:
    """DataAnalyst 阶段的汇总结果。"""

    reply: AgentMessage | None
    state: "_RunState"
    terminated: bool
    is_success: bool
    fail_reason: str
    fatal_error: bool  # True 表示框架级异常（已发 error 事件，调用方应提前退出）


async def _run_data_analyst_phase(
    *,
    request: ChatRequest,
    current_user_id: int,
    emit: EmitCallback,
    llm_client: Any | None,
    question_override: str | None = None,
    sub_task_index: int | None = None,
    constraints: _RunConstraints | None = None,
    workspace_oid: int = 1,
) -> _DataAnalystPhase:
    """跑一次独立的 DataAnalyst ReAct 循环。

    Args:
        question_override: 团队模式下用 Planner 拆分后的子任务替换原问题；
            为 ``None`` 则用 ``request.question``。每次调用都会 **新建** 一个
            DataAnalyst + 全新 _RunState，确保多 sub_task 之间上下文完全隔离。
        sub_task_index: 仅 team 模式下传入，forwarder 会把它注入到本 sub_task
            产生的 ``tool_call`` / ``tool_result`` / ``agent_thought`` /
            ``final_answer`` 事件 payload 里，前端据此按子任务折叠展示。
    """
    state = _RunState(sub_task_index=sub_task_index, constraints=constraints)
    state.tool_runtime_ctx["datasource_id"] = request.datasource_id
    state.tool_runtime_ctx["workspace_oid"] = workspace_oid
    state.tool_runtime_ctx["user_question"] = request.question
    state.tool_runtime_ctx["user_id"] = current_user_id
    if constraints is not None and constraints.report_route:
        state.tool_runtime_ctx["report_route"] = dict(constraints.report_route)

    if llm_client is None:
        llm_client = LangChainLlmClient()

    agent = build_data_analyst(
        llm_client=llm_client,
        datasource_id=request.datasource_id,
        user_id=current_user_id,
        workspace_oid=workspace_oid,
        tool_runtime_ctx=state.tool_runtime_ctx,
    )
    agent.stream_callback = _make_forwarder(state, emit)

    question = question_override if question_override is not None else request.question
    await _emit_agent_speak(
        emit,
        agent="DataAnalyst",
        status="start",
        steps=state.steps,
        sub_task_index=sub_task_index,
    )
    try:
        reply = await agent.generate_reply(
            received_message=AgentMessage(
                content=question,
                role="user",
                context={"constraints": constraints.to_context() if constraints else {}},
            ),
            sender=UserProxyAgent(),
            sub_task_index=sub_task_index,
            constraints=constraints.to_context() if constraints else {},
        )
    except Exception as e:  # noqa: BLE001 - 端点级兜底，禁止异常外溢
        logger.exception("agent run failed")
        await _emit_agent_speak(
            emit,
            agent="DataAnalyst",
            status="error",
            steps=state.steps,
            error=str(e),
            sub_task_index=sub_task_index,
        )
        await emit("error", {"error": f"agent run failed: {e}"})
        return _DataAnalystPhase(
            reply=None,
            state=state,
            terminated=False,
            is_success=False,
            fail_reason=str(e),
            fatal_error=True,
        )

    terminated = bool(reply.action_report and reply.action_report.terminate)
    # 成功判定：只要 Agent 主动 terminate 并给出了非空的 final_answer 即算成功。
    # 不强制要求调用 execute_sql——schema/元数据探索类问题（"有哪些表"、"XX 表字段"）
    # 通过 list_tables / describe_table / sample_rows 就能完整回答，此时
    # last_exec_result 为 None 但 reply.content 已包含可交付的结论，属于合法路径。
    has_content = bool((reply.content or "").strip())
    is_success = terminated and has_content
    fail_reason = ""
    if not is_success:
        fail_reason = (
            reply.action_report.content
            if reply.action_report and reply.action_report.content
            else "agent did not reach a final answer"
        )
        await _emit_agent_speak(
            emit,
            agent="DataAnalyst",
            status="error",
            steps=state.steps,
            error=fail_reason,
            sub_task_index=sub_task_index,
        )
    else:
        await _emit_agent_speak(
            emit,
            agent="DataAnalyst",
            status="end",
            steps=state.steps,
            sub_task_index=sub_task_index,
        )

    return _DataAnalystPhase(
        reply=reply,
        state=state,
        terminated=terminated,
        is_success=is_success,
        fail_reason=fail_reason,
        fatal_error=False,
    )


async def _run_tool_expert_phase(
    *,
    request: ChatRequest,
    current_user_id: int,
    emit: EmitCallback,
    llm_client: Any | None,
    question_override: str | None = None,
    sub_task_index: int | None = None,
    constraints: _RunConstraints | None = None,
    workspace_oid: int = 1,
) -> _DataAnalystPhase:
    state = _RunState(sub_task_index=sub_task_index, constraints=constraints)
    state.tool_runtime_ctx["datasource_id"] = request.datasource_id
    state.tool_runtime_ctx["workspace_oid"] = workspace_oid
    state.tool_runtime_ctx["user_question"] = request.question
    state.tool_runtime_ctx["user_id"] = current_user_id
    if constraints is not None and constraints.report_route:
        state.tool_runtime_ctx["report_route"] = dict(constraints.report_route)
    # ToolExpert 本阶段通常不再 execute_sql；把上游 DataAnalyst 的完整明细
    # 写入 tool_runtime_ctx，供综合/学生报告工具空参读取（禁止 LLM 手抄 200+ 行）。
    if constraints and isinstance(constraints.report_data, dict):
        from src.agent.education.query_parse import (
            extract_best_exec_result_from_report_data,
            find_upstream_fetch_data,
        )

        state.tool_runtime_ctx["report_data"] = constraints.report_data
        best_er = extract_best_exec_result_from_report_data(constraints.report_data)
        if best_er:
            state.last_exec_result = best_er
            state.tool_runtime_ctx["last_exec_result"] = best_er
        upstream_fetch = find_upstream_fetch_data(constraints.report_data)
        if upstream_fetch:
            state.tool_runtime_ctx["last_fetch_data"] = upstream_fetch

    if llm_client is None:
        llm_client = LangChainLlmClient()

    question = question_override if question_override is not None else request.question
    agent = build_tool_agent(
        llm_client=llm_client,
        datasource_id=request.datasource_id,
        user_id=current_user_id,
        workspace_oid=workspace_oid,
        report_data=constraints.report_data if constraints else None,
        sub_task=question,
        tool_runtime_ctx=state.tool_runtime_ctx,
    )
    agent.stream_callback = _make_forwarder(state, emit)

    # 与 DataAnalyst 一致：独立广播 start / end / error，并写入 steps 落库
    await _emit_agent_speak(
        emit,
        agent="ToolExpert",
        status="start",
        steps=state.steps,
        sub_task_index=sub_task_index,
    )
    try:
        reply = await agent.generate_reply(
            received_message=AgentMessage(
                content=question,
                role="user",
                context={"constraints": constraints.to_context() if constraints else {}},
            ),
            sender=UserProxyAgent(),
            sub_task_index=sub_task_index,
            constraints=constraints.to_context() if constraints else {},
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("tool expert run failed")
        await _emit_agent_speak(
            emit,
            agent="ToolExpert",
            status="error",
            steps=state.steps,
            error=str(e),
            sub_task_index=sub_task_index,
        )
        await emit("error", {"error": f"tool expert run failed: {e}"})
        return _DataAnalystPhase(
            reply=None,
            state=state,
            terminated=False,
            is_success=False,
            fail_reason=str(e),
            fatal_error=True,
        )

    terminated = bool(reply.action_report and reply.action_report.terminate)
    has_content = bool((reply.content or "").strip())
    is_success = terminated and has_content
    fail_reason = ""
    if not is_success:
        fail_reason = (
            reply.action_report.content
            if reply.action_report and reply.action_report.content
            else "tool expert did not reach a final answer"
        )
        await _emit_agent_speak(
            emit,
            agent="ToolExpert",
            status="error",
            steps=state.steps,
            error=fail_reason,
            sub_task_index=sub_task_index,
        )
    else:
        await _emit_agent_speak(
            emit,
            agent="ToolExpert",
            status="end",
            steps=state.steps,
            sub_task_index=sub_task_index,
        )

    return _DataAnalystPhase(
        reply=reply,
        state=state,
        terminated=terminated,
        is_success=is_success,
        fail_reason=fail_reason,
        fatal_error=False,
    )


# --------------------------------------------------------------------------- #
# Planner 阶段
# --------------------------------------------------------------------------- #


async def _run_planner_phase(
    *,
    request: ChatRequest,
    llm_client: Any,
    emit: EmitCallback,
    constraints: _RunConstraints | None = None,
    steps: list[dict[str, Any]] | None = None,
) -> list[dict[str, str]]:
    """跑 Planner 得到 sub_task 列表。失败一律回落 [原问题]，不抛。"""
    from src.agent.education.intent_router import (
        classify_report_intent,
        coerce_plan_to_route,
        plan_items_for_route,
        should_use_deterministic_report_plan,
    )
    from src.agent.expand.planner import coerce_plan_items_if_needed

    await _emit_agent_speak(emit, agent="Planner", status="start", steps=steps)

    route = await classify_report_intent(request.question, llm_client)
    if constraints is not None:
        constraints.report_route = route.to_dict()

    if should_use_deterministic_report_plan(request.question, route):
        plan_items = plan_items_for_route(route, request.question)
        await _emit_agent_speak(
            emit,
            agent="Planner",
            status="end",
            steps=steps,
            plan_count=len(plan_items),
            deterministic=True,
            needs_report=route.needs_report,
            report_type=route.report_type.value if route.report_type else None,
            route_source=route.source,
        )
        return plan_items

    try:
        planner = PlannerAgent(llm_client=llm_client)
        reply = await planner.generate_reply(
            received_message=AgentMessage(
                content=request.question,
                role="user",
                context={
                    "question": request.question,
                    "constraints": constraints.to_context() if constraints else {},
                },
            ),
            sender=UserProxyAgent(),
        )
    except Exception as e:  # noqa: BLE001 - Planner 失败不能拖垮 team
        logger.warning("planner failed: %s", e)
        await _emit_agent_speak(emit, agent="Planner", status="error", steps=steps, error=str(e))
        plan_items = [{"sub_task": request.question, "sub_task_agent": "DataAnalyst"}]
        plan_items = coerce_plan_items_if_needed(
            request.question, plan_items, route=route
        )
        return plan_items

    ar = reply.action_report
    extra = dict(ar.extra) if ar and ar.extra else {}
    plans = extra.get("plans") or []
    plan_agents = extra.get("plan_agents") or []
    if not isinstance(plans, list) or not plans:
        plans = [request.question]
    plans = [str(p) for p in plans if p]
    if not isinstance(plan_agents, list):
        plan_agents = []
    plan_items: list[dict[str, str]] = []
    for idx, p in enumerate(plans):
        raw_agent = str(plan_agents[idx]) if idx < len(plan_agents) else "DataAnalyst"
        sub_task_agent = "ToolExpert" if raw_agent == "ToolExpert" else "DataAnalyst"
        plan_items.append({"sub_task": p, "sub_task_agent": sub_task_agent})

    plan_items = coerce_plan_items_if_needed(request.question, plan_items, route=route)
    plan_items = coerce_plan_to_route(request.question, plan_items, route)

    await _emit_agent_speak(
        emit,
        agent="Planner",
        status="end",
        steps=steps,
        plan_count=len(plan_items),
        needs_report=route.needs_report,
        report_type=route.report_type.value if route.report_type else None,
        route_source=route.source,
    )
    return plan_items


# --------------------------------------------------------------------------- #
# Charter / Summarizer 运行
# --------------------------------------------------------------------------- #


async def _run_charter(
    *,
    question: str,
    state: "_RunState",
    llm_client: Any,
    emit: EmitCallback,
    steps: list[dict[str, Any]] | None = None,
) -> tuple[str, dict[str, Any]]:
    """跑 Charter；失败一律回落 table + 空 config，不抛。"""
    context = _build_single_task_context(question, state)
    await _emit_agent_speak(emit, agent="Charter", status="start", steps=steps)
    try:
        charter = CharterAgent(llm_client=llm_client)
        reply = await charter.generate_reply(
            received_message=AgentMessage(
                content=question,
                role="user",
                context=context,
            ),
            sender=UserProxyAgent(),
        )
    except Exception as e:  # noqa: BLE001 - 后处理失败不中断主流程
        logger.warning("charter failed: %s", e)
        await _emit_agent_speak(
            emit, agent="Charter", status="error", steps=steps, error=str(e)
        )
        return "table", {}

    ar = reply.action_report
    extra = dict(ar.extra) if ar and ar.extra else {}
    chart_type = str(extra.get("chart_type") or "table")
    chart_config = extra.get("chart_config") or {}
    if not isinstance(chart_config, dict):
        chart_config = {}
    await _emit_agent_speak(
        emit,
        agent="Charter",
        status="end",
        steps=steps,
        chart_type=chart_type,
    )
    return chart_type, chart_config


async def _run_summarizer_multi(
    *,
    question: str,
    sub_phases: list[tuple[str, "_DataAnalystPhase"]],
    llm_client: Any,
    emit: EmitCallback,
    fallback: str,
    report_data: dict[str, Any] | None = None,
    steps: list[dict[str, Any]] | None = None,
    fact_answer: bool = False,
) -> str:
    """把 N 个 sub_task 的结果综合成一段中文结论。失败回落 ``fallback``。"""
    sub_tasks_block = _format_sub_tasks_block(
        sub_phases, report_data=report_data, fact_answer=fact_answer
    )
    context = {
        "question": question,
        "sub_tasks_block": sub_tasks_block,
        "answer_mode": "fact" if fact_answer else "report",
    }
    await _emit_agent_speak(emit, agent="Summarizer", status="start", steps=steps)
    try:
        summarizer = SummarizerAgent(llm_client=llm_client)
        reply = await summarizer.generate_reply(
            received_message=AgentMessage(
                content=question,
                role="user",
                context=context,
            ),
            sender=UserProxyAgent(),
        )
    except Exception as e:  # noqa: BLE001 - 后处理失败不中断主流程
        logger.warning("summarizer failed: %s", e)
        await _emit_agent_speak(
            emit, agent="Summarizer", status="error", steps=steps, error=str(e)
        )
        return _reconcile_summary_with_sub_phases(
            fallback, sub_phases, fact_answer=fact_answer
        )

    content = (reply.content or "").strip()
    reconciled = _reconcile_summary_with_sub_phases(
        content or fallback, sub_phases, fact_answer=fact_answer
    )
    preview = reconciled.replace("\n", " ").strip()[:160]
    await _emit_agent_speak(
        emit,
        agent="Summarizer",
        status="end",
        steps=steps,
        summary_preview=preview,
    )
    return reconciled


def _sub_phases_have_html_report_output(
    sub_phases: list[tuple[str, "_DataAnalystPhase"]],
) -> bool:
    """子任务是否已产出 HTML 报告（含仅出现在 tool_calls、尚未写入 state.reports）。

    前端会从 tool_result 抽出学科诊断等报告；若此处漏判，AdHoc 会再推
    「自主分析报告」，UI 默认展示最新一份，表现为诊断报告被覆盖。
    """
    for _, phase in sub_phases:
        if phase.state.reports:
            return True
        for tc in phase.state.tool_calls or []:
            if not isinstance(tc, dict):
                continue
            if tc.get("success") is False:
                continue
            data = tc.get("data")
            if not isinstance(data, dict) or data.get("error"):
                continue
            html = str(data.get("html") or "").strip()
            if not html:
                continue
            if data.get("output_type") == "html":
                return True
            tool = str(tc.get("tool") or "")
            if tool == "render_html_report" or tool.startswith("build_"):
                return True
    return False


def _should_skip_ad_hoc_report(
    sub_phases: list[tuple[str, "_DataAnalystPhase"]],
    report_route: dict[str, Any] | None = None,
) -> bool:
    """已有预设/教育 HTML，或正式学情路由已走教育工具链时，禁止再写自主分析报告。"""
    if _sub_phases_have_html_report_output(sub_phases):
        return True
    rt = ""
    if isinstance(report_route, dict):
        rt = str(report_route.get("report_type") or "").strip()
    if rt and any(_phase_has_education_tools(p) for _, p in sub_phases):
        return True
    return False


async def _run_ad_hoc_report_phase(
    *,
    question: str,
    summary_text: str,
    sub_phases: list[tuple[str, "_DataAnalystPhase"]],
    llm_client: Any,
    emit: EmitCallback,
    steps: list[dict[str, Any]] | None = None,
    report_data: dict[str, Any] | None = None,
    chart_type: str | None = None,
    report_route: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """无预设 HTML 报告时，由 LLM 决定是否围绕结论自主撰写报告。

    已有 ``phase.state.reports``、教育工具 HTML，或正式学情路由已走教育工具链时跳过。
    失败 / generate=false 均不出报告。
    agent_speak 复用 Charter（少改四卡 UI），detail 标明自主分析报告。
    """
    if _should_skip_ad_hoc_report(sub_phases, report_route=report_route):
        return []

    sub_block = _format_sub_tasks_block(sub_phases, report_data=report_data)
    if len(sub_block) > 8000:
        sub_block = sub_block[:8000] + "\n…（子任务详情已截断）"
    user_prompt = (
        f"用户问题：{question}\n\n"
        f"分析结论：\n{summary_text or '(无)'}\n\n"
        f"图表类型：{chart_type or 'table'}\n\n"
        f"子任务执行详情：\n{sub_block}"
    )

    await _emit_agent_speak(emit, agent="Charter", status="start", steps=steps)
    try:
        raw = await llm_client.chat(
            [
                {"role": "system", "content": _ADHOC_REPORT_SYSTEM},
                {"role": "user", "content": user_prompt},
            ]
        )
        parsed = parse_json_tolerant(raw)
    except Exception as e:  # noqa: BLE001 - 自主报告失败不中断主流程
        logger.warning("ad-hoc report failed: %s", e)
        await _emit_agent_speak(
            emit, agent="Charter", status="error", steps=steps, error=str(e)
        )
        return []

    if not isinstance(parsed, dict):
        await _emit_agent_speak(
            emit,
            agent="Charter",
            status="end",
            steps=steps,
            summary_preview=f"跳过{_ADHOC_REPORT_TYPE_LABEL}: 输出非 JSON 对象",
        )
        return []

    reason = str(parsed.get("reason") or "").strip()
    if not parsed.get("generate"):
        preview = f"跳过{_ADHOC_REPORT_TYPE_LABEL}"
        if reason:
            preview = f"{preview}: {reason}"
        await _emit_agent_speak(
            emit, agent="Charter", status="end", steps=steps, summary_preview=preview[:160]
        )
        return []

    title = str(parsed.get("title") or "自主分析报告").strip() or "自主分析报告"
    html_body = str(parsed.get("html") or "").strip()
    if not html_body:
        await _emit_agent_speak(
            emit,
            agent="Charter",
            status="end",
            steps=steps,
            summary_preview=f"跳过{_ADHOC_REPORT_TYPE_LABEL}: 无 HTML",
        )
        return []

    charts = parsed.get("charts") if isinstance(parsed.get("charts"), list) else []
    html_body = _inject_ad_hoc_charts(html_body, charts)
    if "data-echart-for" not in html_body:
        fallback = _build_fallback_ad_hoc_charts(summary_text, html_body)
        if fallback:
            logger.info("ad-hoc report: LLM 未给 charts，使用结论数字兜底 %d 张图", len(fallback))
            html_body = _inject_ad_hoc_charts(html_body, fallback)

    html = _wrap_ad_hoc_html(html_body, title)
    if _report_html_is_sparse(html):
        await _emit_agent_speak(
            emit,
            agent="Charter",
            status="end",
            steps=steps,
            summary_preview=f"跳过{_ADHOC_REPORT_TYPE_LABEL}: 内容过空",
        )
        return []

    report_payload: dict[str, Any] = {
        "title": title,
        "html": html,
        "mode": "inline",
        "agent": "Charter",
        "review_status": "pending",
        "report_type_label": _ADHOC_REPORT_TYPE_LABEL,
    }
    for _, phase in reversed(sub_phases):
        if phase.is_success:
            phase.state.reports.append(dict(report_payload))
            break
    await emit("report", report_payload)
    end_preview = f"{_ADHOC_REPORT_TYPE_LABEL}: {title}"
    if reason:
        end_preview = f"{end_preview}（{reason}）"
    await _emit_agent_speak(
        emit,
        agent="Charter",
        status="end",
        steps=steps,
        summary_preview=end_preview[:160],
    )
    return [report_payload]


def _build_fallback_ad_hoc_charts(summary: str, html: str) -> list[dict[str, Any]]:
    """从结论/正文中的「标签+分数」抽数字，兜底柱状 + 雷达（保证至少有图）。"""
    blob = f"{summary or ''}\n{html or ''}"
    pairs: list[tuple[str, float]] = []
    seen: set[str] = set()
    for m in re.finditer(
        r"([\u4e00-\u9fffA-Za-z0-9（）()]{2,20}?)"
        r"(?:平均分|均分|得分|分数|分差|差值)?"
        r"(?:达到|约为|为|是|：|:)?\s*"
        r"(\d+(?:\.\d+)?)\s*分",
        blob,
    ):
        label = re.sub(r"\s+", "", m.group(1))[-12:]
        try:
            val = float(m.group(2))
        except ValueError:
            continue
        if val <= 0 or val > 1000:
            continue
        key = f"{label}:{val}"
        if key in seen:
            continue
        seen.add(key)
        pairs.append((label, val))
        if len(pairs) >= 6:
            break
    if len(pairs) < 2:
        nums = []
        for m in re.finditer(r"(\d+(?:\.\d+)?)\s*分", blob):
            try:
                v = float(m.group(1))
            except ValueError:
                continue
            if 0 < v <= 1000:
                nums.append(v)
        uniq: list[float] = []
        for v in nums:
            if v not in uniq:
                uniq.append(v)
            if len(uniq) >= 4:
                break
        if len(uniq) < 2:
            return []
        pairs = [(f"指标{i + 1}", v) for i, v in enumerate(uniq)]

    names = [p[0] for p in pairs]
    values = [p[1] for p in pairs]
    vmax = max(values) * 1.15 if values else 100
    bar = {
        "id": "adhocFallbackBar",
        "title": "关键指标对比",
        "option": {
            "tooltip": {"trigger": "axis"},
            "grid": {"left": "12%", "right": "8%", "bottom": "12%", "containLabel": True},
            "xAxis": {"type": "category", "data": names},
            "yAxis": {"type": "value", "name": "分"},
            "series": [
                {
                    "type": "bar",
                    "data": values,
                    "itemStyle": {"color": "#1677ff", "borderRadius": [6, 6, 0, 0]},
                    "label": {"show": True, "position": "top"},
                }
            ],
        },
    }
    radar = {
        "id": "adhocFallbackRadar",
        "title": "指标雷达",
        "option": {
            "tooltip": {},
            "radar": {
                "indicator": [{"name": n, "max": round(vmax, 1)} for n in names],
            },
            "series": [
                {
                    "type": "radar",
                    "data": [{"value": values, "name": "数值"}],
                    "areaStyle": {"opacity": 0.25},
                }
            ],
        },
    }
    return [bar, radar]


def _inject_ad_hoc_charts(html: str, charts: list[Any]) -> str:
    """把 charts 数组渲染为卡片网格并插入 HTML。"""
    blocks: list[str] = []
    for idx, item in enumerate(charts or []):
        if not isinstance(item, dict):
            continue
        cid = re.sub(r"[^A-Za-z0-9_-]", "", str(item.get("id") or f"adhocChart{idx}")) or f"adhocChart{idx}"
        if isinstance(item.get("option"), dict):
            option = item["option"]
        elif item.get("series") is not None:
            option = {k: v for k, v in item.items() if k not in ("id", "title")}
        else:
            continue
        if not isinstance(option, dict) or not option.get("series"):
            continue
        try:
            opt_json = json.dumps(option, ensure_ascii=False)
        except (TypeError, ValueError):
            continue
        heading = str(item.get("title") or "").strip()
        h = f"<h3>{html_lib.escape(heading)}</h3>" if heading else ""
        blocks.append(
            '<div class="adhoc-card adhoc-chart-card">'
            f"{h}"
            f'<div id="{cid}" class="adhoc-chart"></div>'
            f'<script type="application/json" data-echart-for="{cid}">{opt_json}</script>'
            "</div>"
        )
    if not blocks:
        return html
    section = (
        '<section class="adhoc-charts">'
        "<h2>数据可视化</h2>"
        '<div class="adhoc-chart-grid">'
        + "".join(blocks)
        + "</div></section>"
    )
    if "data-echart-for" in html and "adhoc-charts" in html:
        return html
    if re.search(r"(?i)</body>", html):
        return re.sub(r"(?i)</body>", section + "</body>", html, count=1)
    return html + section


def _split_adhoc_body_and_charts(html: str) -> tuple[str, str]:
    """把正文与已注入的图表区分开，便于套入卡片布局。"""
    m = re.search(
        r'(?is)(<section\s+class=["\']adhoc-charts["\'][\s\S]*?</section>)',
        html or "",
    )
    if not m:
        return (html or "").strip(), ""
    charts = m.group(1)
    body = ((html or "")[: m.start()] + (html or "")[m.end() :]).strip()
    return body, charts


def _wrap_ad_hoc_html(body: str, title: str) -> str:
    """给自主报告加美化文档壳；完整 html 则补 polish CSS + ECharts 运行时。"""
    stripped = (body or "").strip()
    lower = stripped[:32].lower()
    if lower.startswith("<!doctype") or lower.startswith("<html"):
        polished = _ensure_ad_hoc_report_polish(stripped)
        return _ensure_ad_hoc_echarts_runtime(polished)[:_ADHOC_REPORT_MAX_HTML]

    prose, charts_html = _split_adhoc_body_and_charts(stripped)
    safe_title = html_lib.escape(title)
    main_inner = (
        f'<section class="adhoc-card adhoc-body-card">{prose}</section>'
        if prose
        else ""
    )
    if charts_html:
        main_inner += charts_html
    wrapped = (
        "<!DOCTYPE html><html lang=\"zh-CN\"><head>"
        "<meta charset=\"utf-8\">"
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">'
        f"<title>{safe_title}</title>"
        f'<script src="{_ADHOC_ECHARTS_CDN}"></script>'
        f"{_ADHOC_REPORT_CSS}"
        "</head>"
        '<body class="adhoc-body">'
        '<div class="adhoc-page">'
        '<header class="adhoc-hero">'
        f'<span class="adhoc-badge">{_ADHOC_REPORT_TYPE_LABEL}</span>'
        f"<h1>{safe_title}</h1>"
        "</header>"
        f"{main_inner}"
        '<p class="adhoc-footer">专家团协作生成（规划 / 分析 / 可视化 / 总结）· 待审核</p>'
        "</div>"
        f"{_ADHOC_ECHARTS_BOOTSTRAP}"
        "</body></html>"
    )
    return wrapped[:_ADHOC_REPORT_MAX_HTML]


def _ensure_ad_hoc_report_polish(html: str) -> str:
    """完整 HTML 注入统一 polish CSS，并尽量包一层 page 容器。"""
    out = html
    if 'id="adhoc-report-polish"' not in out:
        if re.search(r"(?i)</head>", out):
            out = re.sub(r"(?i)</head>", _ADHOC_REPORT_CSS + "</head>", out, count=1)
        else:
            out = _ADHOC_REPORT_CSS + out

    body_m = re.search(r"(?i)<body([^>]*)>", out)
    if body_m and "adhoc-body" not in body_m.group(0):
        attrs = body_m.group(1) or ""
        if re.search(r"(?i)\bclass\s*=", attrs):
            new_body = re.sub(
                r'(?i)\bclass\s*=\s*(["\'])',
                r'class=\1adhoc-body ',
                body_m.group(0),
                count=1,
            )
        else:
            new_body = f'<body class="adhoc-body"{attrs}>'
        out = out[: body_m.start()] + new_body + out[body_m.end() :]

    if "adhoc-page" not in out and re.search(r"(?i)<body[^>]*>", out) and re.search(
        r"(?i)</body>", out
    ):
        out = re.sub(
            r"(?i)(<body[^>]*>)",
            r'\1<div class="adhoc-page">',
            out,
            count=1,
        )
        out = re.sub(r"(?i)</body>", "</div></body>", out, count=1)
    return out


def _ensure_ad_hoc_echarts_runtime(html: str) -> str:
    """完整 HTML 若含 data-echart-for 但缺 CDN/init，则补注入。"""
    out = html
    if "data-echart-for" not in out:
        return out
    if "echarts.min.js" not in out and "echarts/dist/echarts" not in out:
        tag = f'<script src="{_ADHOC_ECHARTS_CDN}"></script>'
        if re.search(r"(?i)</head>", out):
            out = re.sub(r"(?i)</head>", tag + "</head>", out, count=1)
        else:
            out = tag + out
    if "echarts.init" not in out:
        boot = _ADHOC_ECHARTS_BOOTSTRAP.strip()
        if re.search(r"(?i)</body>", out):
            out = re.sub(r"(?i)</body>", boot + "</body>", out, count=1)
        else:
            out = out + boot
    if 'id="adhoc-report-polish"' not in out:
        out = _ensure_ad_hoc_report_polish(out)
    return out

def _reconcile_summary_with_sub_phases(
    text: str,
    sub_phases: list[tuple[str, "_DataAnalystPhase"]],
    *,
    fact_answer: bool = False,
) -> str:
    """有报告/权威 KPI 时改写结论；无权威时清洗预览规模人数幻觉。"""
    from src.agent.education.summary_context import reconcile_answer_with_artifacts_detailed

    tool_calls: list[dict[str, Any]] = []
    reports: list[dict[str, Any]] = []
    exec_results: list[dict[str, Any]] = []
    for _goal, phase in sub_phases:
        st = getattr(phase, "state", None)
        tool_calls.extend(list(getattr(st, "tool_calls", None) or []))
        reports.extend(list(getattr(st, "reports", None) or []))
        er = getattr(st, "last_exec_result", None)
        if isinstance(er, dict):
            exec_results.append(
                {
                    "sql": getattr(st, "last_sql", None) or er.get("sql"),
                    "row_count": er.get("row_count"),
                    "columns": er.get("columns"),
                    "sql_row_capped": er.get("sql_row_capped"),
                }
            )
    reconciled, conflicts = reconcile_answer_with_artifacts_detailed(
        text,
        tool_calls=tool_calls,
        reports=reports,
        exec_results=exec_results,
        fact_answer=fact_answer,
    )
    if conflicts:
        logger.info(
            "summary kpi conflicts fixed: %s",
            "; ".join(
                f"{c.field}:{c.claimed}->{c.authority}" for c in conflicts[:20]
            ),
        )
    return reconciled


def _reconcile_phase_answer(text: str, phase: "_DataAnalystPhase") -> str:
    """单 Agent / 单 phase 结论后处理（与 team Summarizer 共用同一套权威对齐）。"""
    return _reconcile_summary_with_sub_phases(text, [("", phase)])


def _build_single_task_context(question: str, state: "_RunState") -> dict[str, Any]:
    """Charter 用：DataAnalyst 单任务的 prompt 变量。"""
    exec_result = state.last_exec_result or {}
    columns = list(exec_result.get("columns") or [])
    rows = list(exec_result.get("rows") or [])
    row_count = int(exec_result.get("row_count") or len(rows))
    return {
        "question": question,
        "sql": state.last_sql or "",
        "columns": ", ".join(columns) if columns else "(无)",
        "row_count": row_count,
        "sample_rows": _format_sample_rows(columns, rows[:_SAMPLE_ROWS_LIMIT]),
    }


def _phase_has_education_tools(phase: "_DataAnalystPhase") -> bool:
    """子任务是否通过教育报告工具链产出（ToolExpert）。"""
    edu_fetch = "fetch_subject_diagnosis_data_tool"
    for tc in phase.state.tool_calls or []:
        tool = str(tc.get("tool") or "")
        if tool == edu_fetch or tool.startswith("build_"):
            return True
    return bool(phase.state.reports)


def _format_sub_tasks_block(
    sub_phases: list[tuple[str, "_DataAnalystPhase"]],
    *,
    report_data: dict[str, Any] | None = None,
    fact_answer: bool = False,
) -> str:
    """把 N 个 sub_task 执行详情拼成 Summarizer 的 {{sub_tasks_block}} 变量。

    每个 sub_task 是一小段 markdown：标题 + SQL（成功才有）+ 结果样例。失败
    的 sub_task 也列出来并附上失败原因，让 Summarizer 知道哪些维度没拿到数据。
    ToolExpert 子任务额外注入小题/知识点/报告产出摘要。
    """
    from src.agent.education.summary_context import (
        extract_stats_authority_block,
        format_education_pipeline_footer,
        format_sql_result_authority_notes,
        format_tool_expert_sub_task_block,
        truncate_keeping_kpi_lines,
    )

    if not sub_phases:
        return "（无子任务结果）"
    blocks: list[str] = []
    for idx, (sub_task, phase) in enumerate(sub_phases):
        header = f"### 子任务 {idx + 1}：{sub_task}"
        if not phase.is_success:
            blocks.append(f"{header}\n状态：失败\n原因：{phase.fail_reason or '未知'}")
            continue

        tool_block = format_tool_expert_sub_task_block(
            tool_calls=phase.state.tool_calls,
            reports=phase.state.reports,
            final_answer=(phase.reply.content if phase.reply else ""),
        )
        is_tool_expert = _phase_has_education_tools(phase)
        if is_tool_expert and not phase.state.last_sql:
            blocks.append(f"{header}\n{tool_block}")
            continue

        exec_result = phase.state.last_exec_result or {}
        columns = list(exec_result.get("columns") or [])
        rows = list(exec_result.get("rows") or [])
        row_count = int(exec_result.get("row_count") or len(rows))
        sample_shown = min(_SAMPLE_ROWS_LIMIT, row_count) if row_count else 0
        sql_text = phase.state.last_sql or ""
        stats_block = ""
        if not fact_answer:
            stats_block = extract_stats_authority_block(
                phase.state.tool_calls,
                reports=phase.state.reports,
            )
        if fact_answer:
            authority_notes = (
                f"⚠️ 事实问答：下列「共 {row_count} 行」仅为本次查询返回行数"
                "（可能含 Top-N / 并列 / LIMIT），**禁止**写成参考人数/共N人参考/班级人数；"
                "直接依据查询结论回答用户问题即可。"
            )
        else:
            authority_notes = format_sql_result_authority_notes(
                sql=sql_text,
                row_count=row_count,
                sample_shown=sample_shown,
            )
        # DataAnalyst 的 terminate 结论也注入（保留 KPI 行），避免只剩样例表
        final_ans = (phase.reply.content if phase.reply else "") or ""
        final_snip = ""
        if final_ans.strip() and not is_tool_expert:
            if fact_answer:
                final_snip = (
                    "\n查询结论（事实问答：优先照抄其中的 student_id/分数等直接答案；"
                    "忽略其中的参考人数/共N人参考套话）：\n"
                    + truncate_keeping_kpi_lines(final_ans.strip(), limit=1200)
                )
            else:
                final_snip = (
                    "\n查询结论（须优先照抄其中的人数/分数线；若与上方权威 KPI 冲突，以权威 KPI 为准）：\n"
                    + truncate_keeping_kpi_lines(final_ans.strip(), limit=1200)
                )

        block = (
            f"{header}\n"
            f"SQL:\n```sql\n{sql_text}\n```\n"
            f"{authority_notes}\n"
            f"共 {row_count} 行，列：{', '.join(columns) if columns else '(无)'}\n"
        )
        if stats_block:
            block += f"{stats_block}\n"
        # 任意提问：永不向 Summarizer 贴明细样例表，从输入侧消灭「数 20 行人头」
        if fact_answer:
            block += f"（明细已省略：共 {row_count} 行；勿把行数写成班级人数）\n"
        else:
            block += (
                f"（明细样例已省略：共 {row_count} 行；"
                "人数/率值以权威 KPI 或 AUTHORITATIVE_ROW_COUNT 为准，"
                "禁止按预览行数写参考人数）\n"
            )
        if final_snip:
            block += final_snip
        if is_tool_expert:
            block += f"\n\n{tool_block}"
        blocks.append(block)

    footer = "" if fact_answer else format_education_pipeline_footer(report_data)
    body = "\n\n".join(blocks)
    if footer:
        return f"{body}\n\n{footer}"
    return body


def _format_sample_rows(columns: list[str], rows: list[Any]) -> str:
    """把前若干行拼成 Markdown 表格，便于 LLM 稳定解析。"""
    if not rows or not columns:
        return "(无数据)"
    header = "| " + " | ".join(columns) + " |"
    sep = "| " + " | ".join(["---"] * len(columns)) + " |"
    body_lines: list[str] = []
    for row in rows:
        if isinstance(row, dict):
            cells = [str(row.get(c, "")) for c in columns]
        elif isinstance(row, (list, tuple)):
            cells = [str(v) for v in row[: len(columns)]]
            cells += [""] * (len(columns) - len(cells))
        else:
            cells = [str(row)] + [""] * (len(columns) - 1)
        body_lines.append("| " + " | ".join(cells) + " |")
    return "\n".join([header, sep, *body_lines])


# --------------------------------------------------------------------------- #
# SSE 转发 + 状态累积
# --------------------------------------------------------------------------- #


class _RunState:
    """在 emit 回调里累积的跨事件状态。"""

    def __init__(
        self,
        sub_task_index: int | None = None,
        constraints: _RunConstraints | None = None,
    ) -> None:
        self.last_sql: str = ""
        self.last_exec_result: dict[str, Any] | None = None
        self.steps: list[dict[str, Any]] = []
        self.tool_calls: list[dict[str, Any]] = []
        self._open_step_round: int | None = None
        # 仅 team 模式下非 None——让 forwarder 把 sub_task_index 也注入到
        # tool_call / tool_result / agent_thought 等 payload 里，前端才能按子任务归组。
        self.sub_task_index: int | None = sub_task_index
        self.reports: list[dict[str, Any]] = []
        self.constraints = constraints
        # 可变运行时上下文：供教育工具读取完整 last_exec_result / last_fetch_data
        #（避免 LLM 只抄 preview 行或把 fetch 大字典塞进下一轮 JSON 导致截断为空）
        self.tool_runtime_ctx: dict[str, Any] = {
            "last_exec_result": None,
            "last_fetch_data": None,
            "report_data": constraints.report_data if constraints else None,
            "datasource_id": None,
            "workspace_oid": None,
        }


#: "sub_task 内部产生"的事件——在 team 模式下给它们统一贴 sub_task_index。
#: step 已在 run_team_stream 主协程里显式 tag，此处避免重复。
_SUB_TASK_SCOPED_EVENTS: tuple[str, ...] = (
    "tool_call", "tool_result", "agent_thought", "final_answer", "report",
)

#: 会把 columns/rows 写入 last_exec_result，并向前端补发 sql/result 事件。
_TABULAR_RESULT_TOOLS = frozenset({"execute_sql", "query_school_vs_city_avg_tool"})


def _make_forwarder(state: _RunState, emit: EmitCallback) -> EmitCallback:
    """生成一个 stream_callback：先做状态累积 / 老事件回灌，再转发给 emit。"""

    async def forward(event: str, payload: dict[str, Any]) -> None:
        if event == "tool_call":
            _on_tool_call(state, payload)
        elif event == "tool_result":
            _on_tool_result(state, payload)

        if state.sub_task_index is not None and event in _SUB_TASK_SCOPED_EVENTS:
            # 不破坏上游原有字段——只加一个前端可选消费的 tag。
            payload = {**payload, "sub_task_index": state.sub_task_index}

        await emit(event, payload)

        if event == "tool_result":
            await _maybe_emit_legacy_sql_result(state, payload, emit)
            await _maybe_emit_report(payload, emit, state)

    return forward


def _on_tool_call(state: _RunState, payload: dict[str, Any]) -> None:
    round_idx = int(payload.get("round") or 0)
    tool = str(payload.get("tool") or "tool")
    thought = str(payload.get("thought") or "")
    step = {
        "name": f"agent_round_{round_idx}",
        "label": f"调用工具 {tool}",
        "status": "running",
        "elapsed_ms": 0,
        "detail": thought[:200],
    }
    state.steps.append(step)
    state._open_step_round = round_idx
    rec = next(
        (
            it
            for it in state.tool_calls
            if int(it.get("round") or 0) == round_idx
            and int(it.get("sub_task_index") or -1) == int(state.sub_task_index or -1)
        ),
        None,
    )
    if rec is None:
        rec = {
            "round": round_idx,
            "sub_task_index": state.sub_task_index,
            "tool": tool,
            "args": payload.get("args") or {},
            "thought": thought,
        }
        state.tool_calls.append(rec)
    else:
        rec["tool"] = tool
        rec["args"] = payload.get("args") or rec.get("args") or {}
        rec["thought"] = thought or rec.get("thought", "")


def _on_tool_result(state: _RunState, payload: dict[str, Any]) -> None:
    round_idx = int(payload.get("round") or 0)
    success = bool(payload.get("success"))
    content = str(payload.get("content") or "")
    elapsed = int(payload.get("elapsed_ms") or 0)

    if state.steps and state._open_step_round == round_idx:
        step = state.steps[-1]
        step["status"] = "ok" if success else "error"
        step["elapsed_ms"] = elapsed
        if content:
            step["detail"] = content[:300]
    rec = next(
        (
            it
            for it in state.tool_calls
            if int(it.get("round") or 0) == round_idx
            and int(it.get("sub_task_index") or -1) == int(state.sub_task_index or -1)
        ),
        None,
    )
    if rec is None:
        rec = {
            "round": round_idx,
            "sub_task_index": state.sub_task_index,
            "tool": str(payload.get("tool") or ""),
        }
        state.tool_calls.append(rec)
    rec["success"] = success
    rec["content"] = content
    rec["data"] = payload.get("data")
    rec["elapsed_ms"] = elapsed

    data = payload.get("data") or {}
    # 第 1 步：仅记录并传递锁表线索（describe_table 成功后锁定该表），后续步骤再做守卫拦截。
    if payload.get("tool") == "describe_table" and success and isinstance(data, dict):
        table_name = str(data.get("name") or "").strip()
        if (
            table_name
            and _is_lockable_table(table_name)
            and state.constraints is not None
            and table_name not in state.constraints.locked_tables
        ):
            state.constraints.locked_tables.append(table_name)
            if state.sub_task_index is not None:
                state.constraints.source_sub_task_index = state.sub_task_index
            logger.info(
                "constraints_locked_table_added sub_task=%s table=%s locked_tables=%s",
                state.sub_task_index,
                table_name,
                state.constraints.locked_tables,
            )

    if (
        payload.get("tool") in _TABULAR_RESULT_TOOLS
        and success
        and isinstance(data, dict)
    ):
        sql_text = str(data.get("sql") or "")
        if sql_text:
            state.last_sql = sql_text
        if "columns" in data and "rows" in data:
            rows = data.get("rows") or []
            state.last_exec_result = {
                "columns": list(data.get("columns") or []),
                "rows": list(rows),
                "row_count": int(data.get("row_count") or len(rows)),
            }
            state.tool_runtime_ctx["last_exec_result"] = state.last_exec_result

    # 同子任务内 fetch → sections：缓存完整 fetch data，避免 LLM 手抄截断成空表。
    if (
        payload.get("tool") == "fetch_subject_diagnosis_data_tool"
        and success
        and isinstance(data, dict)
        and not data.get("error")
    ):
        state.tool_runtime_ctx["last_fetch_data"] = data


async def _maybe_emit_legacy_sql_result(
    state: _RunState,
    payload: dict[str, Any],
    emit: EmitCallback,
) -> None:
    if payload.get("tool") not in _TABULAR_RESULT_TOOLS or not payload.get("success"):
        return
    if state.last_exec_result is None or not state.last_sql:
        return
    await emit(
        "sql",
        {
            "sql": state.last_sql,
            "formatted_sql": state.last_sql,
            "tables": [],
            "chart_type": "table",
        },
    )
    await emit("result", state.last_exec_result)


async def _maybe_emit_report(
    payload: dict[str, Any],
    emit: EmitCallback,
    state: "_RunState | None" = None,
) -> None:
    # 指纹去重跳过的工具结果：禁止再次推送同一份 HTML（否则下拉框成倍刷报告）
    if payload.get("deduplicated"):
        return
    data = payload.get("data")
    if not isinstance(data, dict):
        return
    if data.get("output_type") != "html":
        return
    if data.get("error"):
        return
    html = str(data.get("html") or "")
    if not html.strip():
        return
    if _report_html_is_sparse(html):
        logger.info(
            "报告已跳过：内容为空或 KPI 未填充（title=%s, html_len=%s）",
            data.get("title"),
            len(html),
        )
        return
    title = str(data.get("title") or "Report")
    type_label = str(data.get("report_type_label") or "").strip()
    report_type = str(data.get("report_type") or "").strip()
    try:
        from src.agent.education.report_types import format_report_display_title

        title = format_report_display_title(
            title,
            report_type or None,
            type_label=type_label or None,
        )
    except Exception:
        pass
    if not type_label and report_type:
        type_label = report_type
    html_fp = _report_fingerprint(html)

    if state is not None:
        for existing in state.reports:
            if _report_fingerprint(str(existing.get("html") or "")) == html_fp:
                logger.info("报告已跳过：与已推送报告 HTML 完全相同（title=%s）", title)
                return
            # 同类型：只保留更充实的一份，后续弱报告不再推送
            ex_label = str(
                existing.get("report_type_label") or existing.get("report_type") or ""
            ).strip()
            ex_title = str(existing.get("title") or "")
            same_type = bool(type_label and ex_label and type_label == ex_label)
            same_title = bool(title and ex_title and _normalize_report_title(title) == _normalize_report_title(ex_title))
            if same_type or same_title:
                ex_len = len(str(existing.get("html") or ""))
                if len(html) <= ex_len:
                    logger.info(
                        "报告已跳过：同类型/同标题已有更充实版本（title=%s, type=%s）",
                        title,
                        type_label or ex_label,
                    )
                    return

    if state is not None and state.constraints is not None:
        tool = str(payload.get("tool") or "")
        dedicated_report = tool.startswith("build_") and tool.endswith("_tool")
        locked = list(state.constraints.locked_tables or [])
        if locked and not dedicated_report and not _sql_hits_locked_tables(state.last_sql or "", locked):
            warn = (
                f"报告已拦截：当前报告来源 SQL 未命中锁定表 {locked}。"
                f" 当前 SQL={state.last_sql or '(空)'}"
            )
            logger.warning(warn)
            await emit("error", {"error": warn})
            return
        target = state.constraints.target_student
        if target and not report_matches_student(
            str(data.get("title") or ""), html, target
        ):
            logger.warning("报告已拦截：与目标学生 %s 不匹配（title=%s）", target, data.get("title"))
            return
        target_school = state.constraints.target_school
        if target_school and not report_matches_school(
            str(data.get("title") or ""), html, target_school
        ):
            logger.warning(
                "报告已拦截：与目标学校 %s 不匹配（title=%s）",
                target_school,
                data.get("title"),
            )
            return
        upstream_count = extract_upstream_participant_count(
            state.constraints.report_data
        )
        if upstream_count is not None and report_participant_count_conflicts(html, upstream_count):
            logger.warning(
                "报告已拦截：参考人数与上游不一致（expected=%s, title=%s）",
                upstream_count,
                data.get("title"),
            )
            return
    report_payload: dict[str, Any] = {
        "title": title,
        "html": html,
        "mode": str(data.get("mode") or "inline"),
        "agent": payload.get("agent"),
        "review_status": "pending",
    }
    if data.get("report_type"):
        report_payload["report_type"] = str(data.get("report_type"))
    if data.get("report_type_label"):
        report_payload["report_type_label"] = str(data.get("report_type_label"))
    if payload.get("sub_task_index") is not None:
        report_payload["sub_task_index"] = payload.get("sub_task_index")
    # 保留全量 KPI，供 Summarizer 权威块 / reconcile，避免预览 20 行污染结论
    stats_meta = data.get("_stats")
    if isinstance(stats_meta, dict) and (
        stats_meta.get("count") is not None or stats_meta.get("avg") is not None
    ):
        report_payload["_stats"] = dict(stats_meta)
    else:
        for key in (
            "TOTAL_COUNT",
            "AVG_SCORE",
            "PASS_RATE",
            "EXCELLENT_RATE",
            "STDEV",
            "FULL_SCORE",
            "MAX_SCORE",
            "MIN_SCORE",
        ):
            if data.get(key) is not None and data.get(key) != "":
                report_payload[key] = data.get(key)
    if state is not None:
        state.reports.append(dict(report_payload))
    await emit("report", report_payload)


def _normalize_report_title(title: str) -> str:
    """去掉类型角标与空白，便于同名报告去重。"""
    try:
        from src.agent.education.report_types import strip_report_type_markers

        t = strip_report_type_markers(title)
    except Exception:
        t = re.sub(r"^【[^】]+】", "", str(title or "")).strip()
    return re.sub(r"\s+", "", t)


def _report_fingerprint(html: str) -> str:
    import hashlib

    return hashlib.sha256((html or "").encode("utf-8")).hexdigest()[:24]


def _report_html_is_sparse(html: str) -> bool:
    """空壳 / KPI 未填充的报告：不推送到前端下拉框。"""
    from src.agent.education.report_quality import report_html_is_sparse

    return report_html_is_sparse(html)


# --------------------------------------------------------------------------- #
# 持久化
# --------------------------------------------------------------------------- #


def _persist_sync(
    *,
    request: ChatRequest,
    current_user_id: int,
    question: str,
    sql: str,
    sql_error: str | None,
    exec_result: dict[str, Any] | None,
    is_success: bool,
    reasoning: str,
    steps: list[dict[str, Any]],
    chart_type: str = "table",
    chart_config: dict[str, Any] | None = None,
    agent_mode: str | None = None,
    plans: list[str] | None = None,
    sub_task_agents: list[str] | None = None,
    plan_states: list[dict[str, Any]] | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
    summary: str | None = None,
    reports: list[dict[str, Any]] | None = None,
    total_tokens: int | None = None,
    elapsed_ms: int | None = None,
    workspace_oid: int = 1,
) -> int:
    """在工作线程里开短事务并写 record。失败吞掉返回 0。"""
    if not request.conversation_id:
        return 0
    try:
        from src.chat.crud import chat as chat_crud
        from src.common.core.database import get_db_session

        with get_db_session() as session:
            from src.chat.utils.report_payload import coalesce_record_reports

            persist_reports = coalesce_record_reports(reports, tool_calls)
            record = chat_crud.create_conversation_record(
                session=session,
                conversation_id=request.conversation_id,
                user_id=current_user_id,
                question=question,
                sql=sql or None,
                sql_error=sql_error,
                exec_result=exec_result,
                chart_type=chart_type,
                chart_config=chart_config,
                is_success=is_success,
                reasoning=reasoning or None,
                steps=steps or None,
                agent_mode=agent_mode,
                plans=plans,
                sub_task_agents=sub_task_agents,
                plan_states=plan_states,
                tool_calls=tool_calls,
                summary=summary,
                reports=persist_reports,
                total_tokens=total_tokens,
                elapsed_ms=elapsed_ms,
                workspace_oid=workspace_oid,
            )
            return record.id or 0
    except Exception as e:  # noqa: BLE001
        logger.warning("persist agent record failed: %s", e)
        return 0


def _usage_persist_fields() -> tuple[int | None, int | None]:
    """从当前请求 UsageSink 取 total_tokens / elapsed_ms；无 sink 则 (None, None)。"""
    try:
        from src.agent.adapter.usage_sink import get_usage_sink

        sink = get_usage_sink()
        if sink is None:
            return None, None
        snap = sink.snapshot_for_persist()
        tokens = int(snap.get("total_tokens") or 0)
        elapsed = int(snap.get("elapsed_ms") or 0)
        return (tokens if tokens > 0 else None), (elapsed if elapsed > 0 else None)
    except Exception:  # noqa: BLE001
        return None, None


async def _persist_async(**kwargs: Any) -> int:
    """在 async 协程里读取 UsageSink，再丢到线程写库（ContextVar 不进线程）。"""
    tokens, elapsed = _usage_persist_fields()
    kwargs.setdefault("total_tokens", tokens)
    kwargs.setdefault("elapsed_ms", elapsed)
    return await asyncio.to_thread(_persist_sync, **kwargs)


__all__ = ["run_agent_stream", "run_team_stream", "EmitCallback", "_persist_async"]
