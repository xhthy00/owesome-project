# LangGraph 替换现有 DAG / Team 编排 — 详细开发方案

| 项 | 内容 |
|----|------|
| 状态 | 已落地（Team 编排；默认 `langgraph`） |
| 目录 | `docs/langgraph_migration_plan.md` |
| 依赖版本 | `langgraph>=0.3,<0.4`（已在 `pyproject.toml` 声明） |

### 落地摘要（2026-05-07）

- **代码**：[`src/chat/service/team_graph/`](src/chat/service/team_graph/) — `build_team_graph()`、`run_team_stream_graph()`；节点内复用 `_run_planner_phase`、`_run_data_analyst_phase` / `_run_tool_expert_phase`、`_run_charter`、`_run_summarizer_multi`、`_persist_sync`。
- **入口**：[`run_team_stream`](src/chat/service/agent_runner.py) 根据 [`Settings.team_orchestrator`](src/common/core/config.py)（环境变量 **`TEAM_ORCHESTRATOR`**：`legacy` \| `langgraph`）分发；**默认 `langgraph`**；回滚手写协程时设 `TEAM_ORCHESTRATOR=legacy`。
- **测试**：[`tests/agent/test_team_graph.py`](tests/agent/test_team_graph.py)（图结构 + LangGraph 路径冒烟）；[`tests/agent/test_team_runner.py`](tests/agent/test_team_runner.py) 覆盖手写路径。
- **SSE 清单**：[`docs/team_run_team_stream_emit_inventory.md`](docs/team_run_team_stream_emit_inventory.md)。
- **`LinearDAG`**：未用于线上 Team；保留冒烟与 [`tests/agent/test_dag.py`](tests/agent/test_dag.py)，不再扩展。

---

## 1. 背景与目标

### 1.1 为什么要迁移

- **控制流显式化**：当前 Team 模式的核心逻辑集中在 `run_team_stream` 的手写协程中（Planner → 多 sub_task → Charter → Summarizer），分支与失败策略靠 `if/for` 表达，长期演进（并行 sub_task、条件跳过节点、人机协作）成本高。
- **与生态对齐**：LangGraph 提供 `StateGraph`、条件边、checkpoint、流式事件等能力，与现有 `langchain` 栈一致，减少自研编排负担。
- **保留业务内核**：`ConversableAgent`、ReAct 内循环、工具层**不要求**因迁移而重写；迁移重点是 **阶段级编排**，而非替换 Agent 实现。

### 1.2 成功标准（项目级）

1. **行为等价**：在相同输入与 mock 下，SSE 事件序列、持久化字段、前端契约与现有 `run_team_stream` 一致（允许文档化的极少数内部差异）。
2. **可测试**：新增/迁移的图路径具备单元测试与集成测试，关键失败分支有覆盖。
3. **可灰度**：生产可通过配置或环境变量在「旧路径 / LangGraph 路径」间切换，默认可先旧路径，验证后切新路径。
4. **可清理**：迁移完成后，避免长期并行维护两套编排；自定义 `LinearDAG` 要么删除，要么变为极薄适配层。

### 1.3 非目标（首轮不承诺）

- 全面重写 `DataAnalyst` / `Planner` / `Summarizer` 为 LangChain native Agent。
- 立即引入复杂并行 sub_task（可作为后续迭代）。
- 替代 `run_agent_stream`（单 Agent 模式）——可选第二阶段再做。

---

## 2. 现状盘点

### 2.1 两套「DAG」概念（避免混淆）

| 组件 | 路径 | 作用 | 生产是否使用 |
|------|------|------|----------------|
| **LinearDAG** | `src/agent/awel/dag.py` | 串行执行 `MapOperator` 链 | **否**（仅 `tests/agent/test_dag.py` 等） |
| **Team 流水线** | `src/chat/service/agent_runner.py` → `run_team_stream` | Planner → N×子任务 → Charter → Summarizer + `emit` + 持久化 | **是** |

**结论**：迁移的「主战场」是 **`run_team_stream` 及其调用的阶段函数**；`LinearDAG` 为前期 AWEL 雏形，迁移后可废弃或薄封装，不应再扩张。

### 2.2 `run_team_stream` 语义摘要（与实现对齐）

摘自模块文档字符串，用于验收对照：

1. Planner 拆成 N 个 `sub_task`（失败时 N=1，原问题）。
2. **串行**跑 N 次子任务（`DataAnalyst` 或 `ToolExpert`，由 `build_chat_team().resolve_sub_task_agent` 决定）。
3. Charter 基于**最后一个成功**的 sub_task 的 state。
4. Summarizer 综合**所有** sub_task 的 SQL/结果。
5. 持久化字段语义：`sql` / `exec_result` / `chart_type` 来自最后成功 sub_task；`reasoning` 为 Summarizer 输出；`steps` 累积并带 `sub_task_index`。

**失败分治**：

- Planner 失败 → 回落单 sub_task，继续。
- 某 sub_task 失败 → `plan_update` error，**继续**下一个。
- **全部** sub_task 失败 → 跳过 Charter/Summarizer，`emit error`，持久化失败路径。
- `fatal_error`（如 Agent 抛异常）→ 中断后续 sub_task。
- Charter 失败 → `chart_type=table`；Summarizer 失败 → 回落 DataAnalyst 原文。

### 2.3 关键依赖函数（节点化候选）

下列函数/类型宜 **原样作为图节点内部调用**，减少重写：

| 符号 | 说明 |
|------|------|
| `_run_planner_phase` | Planner |
| `_run_data_analyst_phase` / `_run_tool_expert_phase` | 子任务执行 |
| `_run_charter` | 图表推荐 |
| `_run_summarizer_multi` | 多 sub_task 总结 |
| `_persist_sync` + `asyncio.to_thread` | 持久化（保持线程模型约束） |
| `_RunConstraints`、`_DataAnalystPhase`、`_RunState` | 状态载体（需映射进 Graph state 或可序列化摘要） |

### 2.4 SSE / `emit` 约束

- 执行器约定：**不向调用方抛未捕获异常**，错误通过 `emit("error", ...)` 上传。
- 任意迁移必须保证事件名与 payload 形状对前端兼容（与 `frontend-react` 消费侧对齐，变更需同步前端或版本协商）。

---

## 3. 目标架构

### 3.1 选型

- **主 API**：`langgraph.graph.StateGraph`（或项目后续统一到的推荐入口），Python 3.11+。
- **状态模型**：`TypedDict` + `Annotated` + reducer（如列表字段用 `operator.add`），或与 Pydantic 桥接（按团队规范二选一，需全文一致）。
- **流式**：团队场景需要细粒度 SSE 时，优先 **`graph.astream_events`**（或 LangGraph 版本对应的 stream 事件 API），在适配层映射为现有 `emit(event, payload)`。

### 3.2 逻辑结构（建议）

```
[planner]
    ↓
[sub_tasks_loop]  ← 内部可保留 for 循环，或拆成 conditional + 下一索引节点
    ↓
[branch_all_failed?] ── yes → [persist_failure] → END
    ↓ no
[charter] → [summarizer] → [persist_success] → END
```

- **首版推荐**：`sub_tasks_loop` **仍为单节点内串行 for**，与现状一致，降低图复杂度；待稳定后再拆「每 sub_task 子图」或 `Send` 并行。

### 3.3 与 `LinearDAG` 的关系

- **方案 A（推荐）**：删除或未引用则删除导出，`LinearDAG` 测试改为「LangGraph 线性链」冒烟测试。
- **方案 B**：保留 `LinearDAG` 作为别名，内部编译为只有串行边的 `StateGraph`（维护成本略高）。

---

## 4. 状态设计（TeamState）

### 4.1 必须覆盖的数据

至少应能还原当前 `run_team_stream` 末尾持久化与 `emit` 所需信息：

- 请求上下文：`ChatRequest` 引用或不可变快照字段（`question`、`datasource_id`、`conversation_id` 等）。
- Planner 输出：`plan_items`、`plans`、`plan_agents`。
- 累积：`all_steps`、`sub_phases`（或等价结构）、`last_good_phase`、`plan_states_for_persist`。
- Charter/Summarizer：`chart_type`、`chart_config`、`summary_text`。
- 运行配置：`llm_client`、`enable_tool_agent`、`workspace_oid`、`current_user_id`。
- **副作用通道**：`emit` 不可序列化，通过 `config["configurable"]` 或闭包注入节点，**不要**放进 checkpoint 字段。

### 4.2 Reducer 约定

- `steps` 类列表：追加型，使用 reducer 合并。
- 「最后成功 phase」：整字段替换，由节点显式写入。

### 4.3 序列化与 Checkpoint（可选阶段）

若启用 LangGraph checkpointer：

- 状态中禁止存放不可 pickle 的对象（如开放中的 DB session、裸回调以外的资源）。
- `emit` 仍走运行时注入，恢复会话时仅恢复业务快照，不恢复 SSE。

---

## 5. 分阶段开发计划

### 阶段 0：前置与设计冻结（约 0.5～1 天）

| 任务 | 产出 | 验收 |
|------|------|------|
| 对照 `run_team_stream` 列出所有分支与 `emit` 点 | 分支清单表（可附于本文附录） | 与代码逐行核对无遗漏 |
| 与前端确认 SSE 契约 | 契约文档或注释链接 | 无未评审字段变更 |

### 阶段 1：TeamState + 编译辅助（约 1～2 天）

| 任务 | 产出 | 验收 |
|------|------|------|
| 新增 `src/chat/service/team_graph_state.py`（名称可调整）定义 `TeamState` | TypedDict / schema | mypy 或静态检查通过 |
| 新增 `build_team_graph()` 工厂，**不接**真实 LLM，节点用 `noop` 或 stub | 可 `compile()` 的图 | 单测：图结构节点数、边数符合预期 |

### 阶段 2：线性 happy path（单子任务）（约 2～3 天）

| 任务 | 产出 | 验收 |
|------|------|------|
| 实现 planner → 单次 analyst/tool → charter → summarizer 路径 | 可运行图 + 适配层 | 与现有逻辑对比：同一 fixture 下 `emit` 序列一致 |
| 从 `run_team_stream` 抽「仅执行图」的函数，例如 `_run_team_via_graph(...)` | API 草案 | `pytest` 通过 |

### 阶段 3：完整 Team 语义（约 3～5 天）

| 任务 | 产出 | 验收 |
|------|------|------|
| 接入多 sub_task 串行、fatal 中断、全失败分支 | 完整图或「外层 thin wrapper + 内层循环节点」 | `tests/agent/test_team_runner.py` 全绿 |
| 持久化路径两条（全失败 / 成功）字段与现实现一致 | 对比测试或快照测试 | DB 或 mock persist 断言一致 |

### 阶段 4：接入入口与灰度（约 1～2 天）

| 任务 | 产出 | 验收 |
|------|------|------|
| 环境变量或 settings：`TEAM_ORCHESTRATOR` | 配置项 + 文档 |  staging 切换无报错 |
| 默认 LangGraph；可选 `legacy` | — | 回滚设 `TEAM_ORCHESTRATOR=legacy` |

### 阶段 5：清理与收尾（约 1 天）

| 任务 | 产出 | 验收 |
|------|------|------|
| 删除死代码或更新 `src/agent/awel/*` 导出说明 | 精简 PR | 全仓测试 + lint |
| 更新本文档状态为「已落地」并记录实际差异 | 文档 | 评审通过 |

---

## 6. 测试策略

### 6.1 必跑套件

- `tests/agent/test_team_runner.py`：集成行为基准。
- `tests/agent/test_dag.py`：若改为 LangGraph 冒烟，更新用例名与断言。
- 新增：针对 `TeamState` reducer、条件边的单元测试。

### 6.2 对比测试建议

- 在阶段 3 引入 **双跑开关**（仅测试环境）：同输入并行调用旧 `run_team_stream` 与新图路径，对比 `emit` 收集列表（过滤时间戳等不稳定字段）。

### 6.3 性能

- 首版以正确性为主；若 `astream_events` 引入额外开销，记录基准（单次 Team 请求总耗时与事件条数）。

---

## 7. 上线与回滚

1. **配置**：默认 `langgraph`；回滚时设 `TEAM_ORCHESTRATOR=legacy`。
2. **监控**：Team 模式错误率、`error` 事件内容、持久化失败日志。
3. **回滚**：将 `TEAM_ORCHESTRATOR=legacy`（或改 Settings 默认值），无需回滚数据库（若不启用 checkpoint）。

---

## 8. 风险与缓解

| 风险 | 缓解 |
|------|------|
| SSE 顺序或字段漂移导致前端异常 | 契约评审 + 集成测试 + 双跑对比 |
| State 过大或包含不可序列化对象 | 状态评审清单；checkpoint 阶段单独审计 |
| LangGraph API 版本差异 | 锁版本区间；升级前跑全量测试 |
| 维护两套编排 | 明确废弃时间表；灰度期尽量短 |

---

## 9. 建议代码布局（可随实现微调）

```
src/chat/service/
  agent_runner.py           # 保留入口；内部根据配置 dispatch
  team_graph/
    __init__.py             # build_team_graph, run_team_graph
    state.py                  # TeamState, reducers
    nodes.py                  # planner_node, sub_tasks_node, ...
    routing.py                # 条件边函数
```

---

## 10. 附录：修订记录

| 版本 | 日期 | 说明 |
|------|------|------|
| v0.1 | 2026-05-07 | 初稿：范围、阶段、状态、测试与上线策略 |
| v0.2 | 2026-05-07 | Team LangGraph 实现、`TEAM_ORCHESTRATOR`、落地摘要 |
| v0.3 | 2026-05-07 | `team_orchestrator` 默认改为 `langgraph` |

---

## 11. 附录：参考代码锚点

便于评审时打开源码对照：

- Team 入口：`src/chat/service/agent_runner.py` → `run_team_stream`
- LangGraph Team：`src/chat/service/team_graph/` → `build_team_graph`、`run_team_stream_graph`
- 轻量 team 配置：`src/agent/expand/chat_awel_team.py` → `build_chat_team`
- 旧 DAG 原型：`src/agent/awel/dag.py` → `LinearDAG`

（完）
