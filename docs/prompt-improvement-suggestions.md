# 提示词改进建议

> 扫描范围：`src/templates/sql_gen_prompt.py`、`src/agent/core/profile.py`、`src/agent/expand/*.py`、`src/chat/service/sql_generator.py`、`src/chat/service/agent_runner.py`、`src/llm/base.py`
>
> 日期：2026-05-07

---

## 目录

- [一、SQL 生成提示词](#一sql-生成提示词)
  - [1. 规则冗余严重 —— 合并同类项](#1-规则冗余严重--合并同类项)
  - [2. Process check 可以砍掉](#2-process-check-可以砍掉)
  - [3. XML 标签开销过高](#3-xml-标签开销过高)
  - [4. build_schema_info 格式过于贫瘠](#4-build-schema-info-格式过于贫瘠)
  - [5. 缺少默认 few-shot 示例](#5-缺少默认-few-shot-示例)
  - [6. 错误重试提示词信息量不足](#6-错误重试提示词信息量不足)
- [二、Agent 系统提示词](#二agent-系统提示词)
  - [7. ProfileConfig.render_system_prompt() 可扩展](#7-profileconfigrender-system-prompt-可扩展)
  - [8. DataAnalyst 提示词中工具发现逻辑可以前置](#8-dataanalyst-提示词中工具发现逻辑可以前置)
  - [9. 严禁输出 \<think\> 标签可能适得其反](#9-严禁输出-think-标签可能适得其反)
  - [10. Planner 的 task-agent 自动路由可以更精确](#10-planner-的-task-agent-自动路由可以更精确)
  - [11. Summarizer 的子任务上下文可能过长](#11-summarizer-的子任务上下文可能过长)
- [三、跨域改进](#三跨域改进)
  - [12. 语言参数化](#12-语言参数化)
  - [13. 提示词版本管理](#13-提示词版本管理)
  - [14. Token 预算感知](#14-token-预算感知)
- [四、优先级汇总](#四优先级汇总)

---

## 一、SQL 生成提示词

**涉及文件**：`src/templates/sql_gen_prompt.py`

### 1. 规则冗余严重 —— 合并同类项

当前 system prompt 包含 20+ 条 `<rule>`，大量重复表述：

- "检查表名/字段名是否在 schema 内" 出现了 4 次（process check step 3、no-additional-info 规则、规则 2、规则 15）
- 图表类型选择规则分散在 5 条独立 rule 中（chart-type 选择、维度/指标限制、排序规则、聚合规则、时间格式规则），每条之间又有交叉引用

**建议**：将 5 条图表规则合并为 1 条紧凑的图表生成契约：

```
图表 = 类型(table|column|bar|line|pie) + 维度(1个) + 指标(1-2个) + 排序(维度优先)
```

token 用量可减少约 40%。

### 2. Process check 可以砍掉

`<SQL-Generation-Process>` 有 11 个步骤，其中 4 个是"强制检查"。对当前这代模型（DeepSeek V3/V4、GPT-4o），**链式 self-check 效率很低**——模型不会因为被要求"强制检查"而真的更准确地检查表名、SQL 语法、JSON 格式。这些验证应该在代码层做（项目已在 `parse_llm_sql_response` / `validate_sql` 中做了），不需要在 prompt 里浪费 token。

**建议**：删除整个 `<SQL-Generation-Process>` 块，用一条规则替代：

```
生成 SQL 后，在返回 JSON 前确认：表名/字段名均来自 <m-schema>，无虚构。
```

### 3. XML 标签开销过高

`<Instruction>`, `<Rule>`, `<requirement>`, `<enforcement>`, `<action>`, `<title>`, `<note>`, `<background-infos>`, `<current-time>`... 这些 XML 标签加起来约 **15-20% 的 token 开销**，但对模型理解几乎没有增量价值。模型是通过自然语言语义理解规则的，不是通过 XML 层级。

**建议**：全部改用 Markdown 层级（`##`, `###`）或纯列表，token 量可降低约 15%。

### 4. build_schema_info 格式过于贫瘠

当前 M-Schema 格式：

```
# Table: users, 用户信息
[(id: int, PK), (name: varchar, 用户名称)]
```

LLM 无法区分"可为空"、"默认值"、"外键关系"。对于需要做 JOIN 推理的场景，模型只能靠猜。

**建议**：扩展为含可空性 + 外键提示的紧凑格式：

```
# users (用户表)
- id: int, NOT NULL, PK
- name: varchar(128), NOT NULL
- dept_id: int, -> departments.id
```

对一般项目这可能多消耗 20% token，但大幅降低"字段不存在"类错误。

### 5. 缺少默认 few-shot 示例

`data_training` 参数虽然支持注入示例，但没有默认值。在 prompt 里提到的 `<sql-examples>` 块也没有默认内容。一条好的 few-shot 示例能比 10 条规则更有效。

**建议**：在 `build_sql_generation_prompt` 中内置 2 条默认示例（在用户没传入 `data_training` 时使用）：

- 一条简单查询
- 一条多表 JOIN

顺便展示期望的 JSON 输出格式。

### 6. 错误重试提示词信息量不足

```python
custom_prompt=f"请注意：你之前生成的SQL有误，错误原因：{error_msg}。请重新生成。"
```

只传错误信息，但没有传**上次生成的那个错误的 SQL**。模型不知道"哪条 SQL 错了"，只能盲猜。

**建议**：同时传入上次生成的 SQL：

```python
custom_prompt=(
    f"你上次生成的 SQL:\n```sql\n{last_sql}\n```\n"
    f"数据库返回错误: {error_msg}\n"
    f"请基于错误信息修正后重新生成。"
)
```

---

## 二、Agent 系统提示词

**涉及文件**：`src/agent/core/profile.py`、`src/agent/core/base_agent.py`、`src/agent/core/react_agent.py`、`src/agent/expand/data_analyst.py`、`src/agent/expand/tool_agent.py`、`src/agent/expand/planner.py`、`src/agent/expand/summarizer.py`

### 7. ProfileConfig.render_system_prompt() 可扩展

当前只是拼接 `name/role/goal/constraints/desc`。所有 Agent 共用同一个模板，带来了两个问题：

- **统一性**：所有 Agent 都以 `"你是 XXX（角色：YYY）"` 开头，对 ReAct agent 来说少了关键的**输出协议前置**（应该在第一句就说明"你的回复必须是 JSON"）
- **缺失字段**：没有 `output_format`、`stop_condition`、`failure_policy` 等 Agent 常用属性

**建议**：在 `ProfileConfig` 和 `render_system_prompt()` 中增加可选的 `output_rules: str` 字段，并调整渲染顺序：

1. 先讲"你是谁"
2. 再讲"你该怎么输出"
3. 然后"你能用什么工具"
4. 最后"约束"

优先级与人类阅读习惯一致。

### 8. DataAnalyst 提示词中工具发现逻辑可以前置

当前 `{{tools_prompt}}` 占位符被放在 prompt 首位，之后才是"输出协议"和"工作原则"。模型会在看到工具列表之后才被告知"你必须输出 JSON"。**这会导致部分模型在工具列表后直接开始输出自然语言**。

**建议**：调整 `DATA_ANALYST_DESC` 的顺序为：

```
1. 你是谁（角色）
2. 输出协议（JSON 格式约束）——这一条必须最先看到
3. 可用工具
4. 工作原则
```

`TOOL_AGENT_DESC` 同理。

### 9. 严禁输出 \<think\> 标签可能适得其反

ToolAgent 的 prompt 说 `严禁输出 <think> 标签、自然语言解释或任何 JSON 之外文本`。但 DeepSeek V3/V4 模型的 `<think>` 标签是在**服务端截获**的，模型本身并未被告知它要输出 JSON 以外的文本。强制禁止 `<think>` 可能导致模型混淆。

**建议**：改为告诉模型"直接输出 JSON 对象，不要包在任何标签中"，而不是点名特定标签。同时在后端用 `extract_reasoning()` 做剥离兜底（项目已实现）。

### 10. Planner 的 task-agent 自动路由可以更精确

当前 Planner 通过关键词匹配决定 sub_task 走 ToolExpert 还是 DataAnalyst：

```python
_TOOL_EXPERT_HINTS = (
    "html", "report", "dashboard", "template", "web page", "webpage",
    "网页", "页面", "报告", "可视化报告", "图文报告",
)
```

这种硬编码关键词匹配只能覆盖中英文表面词。如果用户说"生成图表展示方案"，它不会匹配到任何关键词。

**建议**：在 Planner 的 prompt 里直接教 LLM 判断，而不是后端猜。Planner 的输出已有 `sub_task_agent` 字段——让 LLM 自己填 `"ToolExpert"` 或 `"DataAnalyst"`。同时保留后端关键词匹配作为兜底。

### 11. Summarizer 的子任务上下文可能过长

`_format_sub_tasks_block()` 把每个成功子任务的 SQL + 列名 + 样例数据全拼成 Markdown。当 team 模式有 4+ 个子任务时，`{{sub_tasks_block}}` 可能超过 5K token。Summarizer 的指令却是"直接输出中文结论"——模型可能在大量数据中迷失。

**建议**：

- 每个子任务的样例数据不传完整表格，只传前 3 行
- 为 Summarizer 加上明确的输出长度约束（≤ 300 字）
- 考虑让 Summarizer 先看子任务的标题+结论，再决定是否需要展开看细节

---

## 三、跨域改进

### 12. 语言参数化

`sql_gen_prompt.py` 第 219 行硬编码 `使用语言：zh`。`generate_followup_questions()` 有 `lang` 参数但主 SQL 生成路径没有。多语言支持需要改硬编码字符串。

**建议**：将 `lang` 参数向下透传到 `build_sql_generation_prompt()`，替换硬编码的 `zh`。

### 13. 提示词版本管理

所有提示词都是 Python 代码里的 f-string，没有外置的 prompt registry。调 prompt 需要改代码 + 部署，无法做 A/B 测试。

**建议**：如果 prompt 调优频率上升，考虑把 prompt 模板移到 `.yaml` 或 `.toml` 配置文件中，按版本号加载。对 MVP 阶段可先忽略，但值得关注。

### 14. Token 预算感知

整个系统没有任何地方估算 prompt token 数量。当 `schema_info` 很长（几十张表、几百个字段），system prompt 可能轻松超过 10K token，但系统不会自动截断。

**建议**：在 `build_sql_generation_prompt` 和 `_format_sub_tasks_block` 中增加简易 token 估算（1 字符 ≈ 0.3 token for 中文），超出阈值时自动删减样例行数或去掉注释。

---

## 四、优先级汇总

| 优先级 | 编号 | 建议 | 涉及文件 | 预期效果 |
|--------|------|------|----------|----------|
| 🔴 高 | 1 | 合并图表规则 | `sql_gen_prompt.py` | 省 ~40% token，减少模型理解负担 |
| 🔴 高 | 2 | 砍掉 process check | `sql_gen_prompt.py` | 省 token，不影响准确性（有代码层验证兜底） |
| 🔴 高 | 6 | 错误重试带 SQL | `sql_gen_prompt.py` / `sql_generator.py` | 直接提升自动修复成功率 |
| 🟡 中 | 3 | 去 XML 标签 | `sql_gen_prompt.py` | 省 ~15% token |
| 🟡 中 | 4 | schema 格式增强 | `sql_gen_prompt.py` | 减少"字段不存在"类错误 |
| 🟡 中 | 5 | 默认 few-shot | `sql_gen_prompt.py` | 提升复杂 JOIN 场景的 SQL 质量 |
| 🟡 中 | 8 | 调整 prompt 顺序 | `data_analyst.py` / `tool_agent.py` | 提高 JSON 协议遵守率 |
| 🟢 低 | 7 | ProfileConfig 扩展 | `profile.py` | 提升 prompt 结构一致性 |
| 🟢 低 | 10 | Planner agent 路由 | `planner.py` | 提升 team 模式下 tool 分流准确率 |
| 🟢 低 | 11 | Summarizer 上下文裁剪 | `agent_runner.py` | 减少 token 浪费，提升结论质量 |
| 🟢 低 | 12 | 语言参数化 | `sql_gen_prompt.py` | 工程整洁度 |
| 🟢 低 | 13 | 提示词版本管理 | 全模块 | 可维护性，A/B 测试能力 |
| 🟢 低 | 14 | Token 预算感知 | `sql_gen_prompt.py` / `agent_runner.py` | 防止 prompt 超限 |
