# `run_team_stream` 分支与 SSE 事件清单（阶段 0）

对照实现：[agent_runner.py](../src/chat/service/agent_runner.py) 中 `_run_team_stream_legacy` / `run_team_stream`。

## SSE 事件（按出现顺序的典型流水线）

| 事件名 | 触发时机 | 备注 |
|--------|----------|------|
| `agent_speak` | Planner 开始 / 结束 / 错误 | `agent`=`Planner` |
| `plan` | Planner 完成后 | `plans`, `sub_task_agents` |
| `plan_update` | 每个 sub_task `running` | `index`, `sub_task`, `sub_task_agent` |
| `tool_call` / `tool_result` / `agent_thought` / `final_answer` / `report` | DataAnalyst / ToolExpert 内 | team 模式下可带 `sub_task_index` |
| `sql` / `result` | execute_sql 成功后（legacy 转发） | |
| `plan_update` | 每个 sub_task 结束 | `ok` 或 `error`；fatal 时额外一次 error 后 **break** |
| `error` | 全 sub_task 失败 / 报告拦截等 | |
| `agent_speak` | Charter / Summarizer | |
| `chart` | Charter 后 | `chart_type`, `chart_config` |
| `summary` | Summarizer 后 | `content` |

前端适配入口：[frontend-react/src/api/adapter/chatAdapter.ts](../frontend-react/src/api/adapter/chatAdapter.ts)（`plan`、`plan_update` 等）。

## 控制流分支

1. **Planner 异常**：`_run_planner_phase` 内部回落单条 `sub_task`（原问题），不中断流水线。
2. **子任务 `fatal_error`**：`plan_update` error，`break`，后续 sub_task 不执行；若此前已有成功子任务，`last_good_phase` 仍可能非空 → 继续 Charter/Summarizer。
3. **子任务非 fatal 失败**：`plan_update` error，继续下一 sub_task。
4. **`last_good_phase is None`**：不发 Charter/Summarizer/chart/summary；`emit error`；失败持久化分支。
5. **Charter 异常**：回落 `table` + `{}`，流程继续。
6. **Summarizer 异常**：回落 `fallback`（最后成功 DA 原文）。

## 修订

| 日期 | 说明 |
|------|------|
| 2026-05-07 | 初稿，与 LangGraph 迁移阶段 0 对齐 |
