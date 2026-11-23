# 让 Agent 自主编排数据洞察图：方案设计

> 状态：方案稿 · 2026-11-22
> 范围：`src/chat` 问答链路 + `frontend-react` 答案内「数据洞察」卡片
> 前置：P0 已落地，见 [qa-visualization-p0.md](qa-visualization-p0.md)
> 说明：本文把「图标」按「图表（chart）」理解；若确实指 icon，协议部分同样适用，只需换渲染器。

---

## 0. 一页结论

现在不是「没有图」，而是**图是查询结果的固定副产品**：

- 一条链路只保留**一张**图（`chartByRunId` 单值、`chart_config` 单值）；
- 选图只认**最后一次**查询结果（`last_result`）；
- 构图固定为 **KPI 条 + 主图 + 数值分布辅图 + 明细表**；
- 选型靠**问题关键词 + 列形态**的规则表，规则外的情况一律退化；
- 图没有「为什么画它」的解释，也没有和结论句对齐的洞察文案。

本方案把「画图」从**渲染后处理**提升为**回答编排的一步**：

```
本轮全部查询结果
      │
      ▼
① Evidence Catalog（证据目录：每个结果集的列画像、样本、行数、子任务）
      │
      ▼
② Visual Candidates（规则枚举「这组数据能画什么」，附适配分与数据事实）
      │
      ▼
③ Agent Planner（模型在候选里选「该画什么、画几张、先画哪张、标题与洞察句」）
      │
      ▼
④ Validator / Repair（确定性校验：列存在、类型匹配、反模式、数量上限、数字可溯源）
      │
      ▼
⑤ Insight Deck（有序的可视化块列表，前端逐块渲染，支持逐块切换）
```

三个关键决策：

| 决策 | 建议 | 理由 |
|------|------|------|
| 谁决定画什么 | **规则枚举候选 + Agent 选择编排 + 宿主校验兜底** | 纯规则不懂分析结论；纯模型会幻觉列名；分层后两者互补 |
| 什么时候决定 | **回答定稿后、`done` 之前，一次结构化调用**；简单单值轮次直接走规则 | 此时才看得到「结论 + 全部证据」；不阻塞正文流式输出 |
| 输出形态 | **Insight Deck（0~4 个块）**，不是单张图 | 多结果、多视角、可解释、可回放；旧单图协议作为兼容层保留 |

**不改什么（硬约束）：**

- 不新增取数、不改 SQL 语义、不改 KPI 口径；所有图只消费本轮 `query_semantic` 已返回的行。
- 不跨结果集拼接/关联（那属于查询，不属于绘图）；一个块只能绑定一个 `result_key`。
- 权限、行级过滤、脱敏、水印沿用现有链路。
- 空数据、单值、权限错误不强行出图。
- 报告模板（HTML/iframe）本期不动，只在「本轮已生成报告」时收缩 Deck，避免重复。

---

## 1. 现状盘点

### 1.1 链路事实

```
query_semantic 成功
  ├─ SSE result  → useChat.queryResults（每组结果都进前端）
  └─ 后端 evidence[] 追加；last_result_index 只记最后一次
        │
        ├─ 模型调用 chart_semantic_result（可选，只能画 last_result）
        │     └─ emit chart（chart_type + chart_spec + option）
        │
        └─ 回合结束仍无 chart
              └─ recommend_chart_spec(question, last_result) → emit chart（兜底）
                    │
                    ▼
useChat.chartByRunId[runId] = 单个 { chartSpec, chartType }
                    │
                    ▼
CoworkChat → AnswerVisual（固定构图）
  ├─ KpiStrip（前端自动派生）
  ├─ 主图 ChartView
  ├─ buildSecondarySpec（固定「第一个数值列的直方图」）
  └─ SmartTable
```

### 1.2 代码证据

| # | 事实 | 位置 |
|---|------|------|
| 1 | 一轮只保存一张图，后一次覆盖前一次 | `src/chat/service/deepagents_runtime.py:536`；`frontend-react/src/hooks/useChat.ts:82` |
| 2 | 绘图工具只接受「最近一次查询」 | `deepagents_runtime.py:769-806`（无 `result_index` 参数） |
| 3 | 服务端兜底也只画 `last_result` | `deepagents_runtime.py:1598-1636` |
| 4 | 前端只渲染最后一组结果 | `frontend-react/src/components/chat/CoworkChat.tsx:277-313` |
| 5 | 答案卡构图写死：KPI + 主图 + 分布辅图 + 表 | `frontend-react/src/components/chat/AnswerVisual.tsx:52-86` |
| 6 | 辅图规则写死为「第一个数值列的分箱直方图」 | `frontend-react/src/components/charts/chartRecommend.ts:234-287` |
| 7 | 前端拿到 spec 后仍用最后一组 rows 渲染，忽略 `spec.source.resultIndex` | `AnswerVisual.tsx:14-17`、`:303-310` |
| 8 | 0 行结果会造成「后端 evidence 下标」与「前端 queryResults 下标」错位 | `deepagents_runtime.py:726` 无条件追加；`useChat.ts:877-892` 仅非空才追加 |
| 9 | 选型只看关键词与列形态，相关性直接退化为表格 | `src/chat/service/chart_spec.py:189-210`、`:551-553` |
| 10 | 仅 10 种图，无散点/热力/雷达/组合图 | `chart_spec.py:31-42`；`frontend-react/src/components/charts/echartsRuntime.ts:2` 只注册 Bar/Line/Pie |
| 11 | 图没有洞察句、参考线、重点标注 | `chart_spec.py:248-266` 标题由列名拼装 |
| 12 | 历史只持久化最后一组结果 | `deepagents_runtime.py:1664-1676`；`turn_records.py:41` |

### 1.3 根因

1. **选择发生在「单个结果」上，而不是「整轮证据」上。** 分析常常查 2~5 次（趋势、排名、分布、明细），最有信息量的往往不是最后一次，但系统只认最后一次。
2. **决定权在规则，模型只有「可选 hint」。** 规则能判断列形态，判断不了「这张图是否支撑结论」「两张图是否在讲同一件事」。
3. **输出协议是单图。** 即便模型连续调用两次绘图工具，前端与落库也只能保留一张。
4. **前端有第二套规则。** 前后端各自 `classifyIntent` / `recommendChartSpec`，行为容易漂移；`resultIndex` 断链后前端的 rows 与 spec 可能不是同一组数据。
5. **没有「数据事实」层。** 标题里没有极值、差值、占比、基准，图只是形状，不是洞察。
6. **最后一条空结果会「锁死」绘图。** 绘图工具要求 `last_result["rows"]` 非空；若模型最后补查了一条 0 行结果，即使前面有数据也画不出来。

---

## 2. 目标与非目标

### 2.1 目标

1. **Agent 自主**：由模型基于「问题 + 结论 + 全部查询结果」决定出几张图、每张图看哪组数据、用什么图型、标题与洞察句。
2. **多结果覆盖**：多个查询结果都能被选中；同一轮可以呈现「趋势 + 排名」「对比 + 分布」等组合。
3. **可解释**：每个块带一句洞察句（数字必须来自结果），并保留选择理由（`why`）供调试与评测。
4. **可回放**：历史会话不重新查数也能还原 Deck。
5. **可兜底**：模型超时/输出非法时，规则能产出可用的 Deck，最多退化为单图，不白屏。
6. **可控成本**：新增至多一次结构化模型调用；简单单值轮次不走模型。

量化目标（建议纳入验收）：

- 含多行结果的问答中，**≥85%** 至少出现 1 个有效可视化块；
- 图表类型与人工期望一致率 **≥80%**（40 条评测集）；
- 洞察句数字可溯源率 **≥98%**；
- 非法块导致白屏 **0** 起；
- Planner 额外延迟 P95 **≤3s**，额外 token **≤2k/轮**。

### 2.2 非目标

- 不做「自然语言改图」（「把图换成折线」）——列入 P3。
- 不做跨结果集的合并/关联图——需要新查询，不属于绘图。
- 不重写教育报告模板与 `agent/education/charts.py`。
- 不引入 Vega-Lite / 图表 DSL 全家桶。
- 不做图表协作、批注、收藏。

---

## 3. 方案总览

### 3.1 分层职责

| 层 | 负责 | 不负责 |
|----|------|--------|
| Evidence Catalog（宿主） | 整理本轮结果、列画像、样本、行数、子任务、SQL 摘要 | 不做选择 |
| Candidate Generator（规则，纯函数） | 枚举「能画什么」、算适配分、算数据事实、生成备选 | 不决定最终组合 |
| Agent Planner（模型） | 选块、排序、选图型/编码、写标题与洞察句、决定不出图 | 不写 ECharts option、不编造列 |
| Validator / Repair（宿主，纯函数） | 校验、去重、限流、反模式修正、规则补齐 | 不做语义创新 |
| Deck Renderer（前端） | 块布局、图渲染、逐块切换、空/错状态 | 不再重新选型 |

一句话：**规则负责「能画什么」，模型负责「该画什么、怎么讲」，宿主负责「不许画错」。**

### 3.2 为什么不是别的方案

| 备选 | 问题 |
|------|------|
| 让模型直接输出 ECharts option | 不可校验、可注入任意结构、体积大、主题/导出/测试都失控 |
| 纯规则继续加关键词 | 规则无法理解「结论在讲什么」，也无法在多结果间取舍；每加一个场景就多一堆 if |
| 全前端决定 | 前端没有 SQL、子任务、权限上下文；历史回放不可复现；前后端规则继续漂移 |
| 让主 Agent 在推理中顺手调绘图工具 | 可以保留为「主动强调」通道，但不能作为唯一通道：模型经常忘记、且只能看到最近结果；回答定稿后仍需一次编排 |
| 用 Vega-Lite | 表达力强但依赖重、校验难、主题/交互/导出要重做，收益不匹配 |

---

## 4. 核心设计

### 4.1 Evidence Catalog（证据目录）

后端在回合内维护 `evidence: list[EvidenceItem]`（现有 `evidence` 升级），每项：

```python
@dataclass
class EvidenceItem:
    result_key: str          # 稳定键，如 "r0"、"r1"；不要用易漂移的下标
    index: int               # 兼容旧协议的下标
    sql: str
    columns: list[str]
    rows: list[list[Any]]    # 原始行（服务端持有）
    row_count: int
    truncated: bool
    subtask: str | None      # 对应 write_todos 的阶段
    bundle_id: str | None
    profile: list[ColumnProfile]   # 复用 chart_spec.profile_result
    sample_rows: list[list[Any]]   # 前 8~10 行，给 Planner 看
    stats: dict[str, Any]          # 见 4.3 数据事实
```

要点：

- **`result_key` 优先于 `index`**。SSE `result` 事件带上 `result_key`，前端 `QueryResult.key` 直接用它；解决「0 行结果导致下标错位」的既有隐患。
- 目录构建时按 `(sql 归一化, columns, row_count)` 指纹去重，保留最新一份；**事件层不合并**，每次执行仍按顺序发 `result_key`，避免前端已收到的结果被「抽走」。
- 只收 `query_semantic` 成功且 `execute=True` 的结果；`lookup_question_context` 不进目录（那是候选探查，不是业务证据）。
- 目录同时写入落库（见 4.7），保证历史回放。

### 4.2 Visual Candidates（规则枚举「能画什么」）

对每个 `EvidenceItem`，用纯函数生成 1~5 个候选。候选不是最终图，而是**带解释的备选**，供模型挑选。

```python
@dataclass
class VisualCandidate:
    id: str                  # "c3"
    result_key: str
    type: str                # column / bar / line / donut / groupedColumn / scatter / heatmap / radar / combo / table / kpi
    encoding: dict           # {x, y, series, size, color}
    score: float             # 0~1
    reasons: list[str]       # ["时间列 + 单指标 → 趋势", "类别 7 个，适合纵向对比"]
    facts: list[dict]        # [{"kind":"delta","text":"5月较3月 -2.5 分","values":[...]}]
    spec: dict               # 物化后的 ChartSpec v2（含 title/subtitle/source）
    alternatives: list[str]  # 同数据可切换的类型
```

候选生成矩阵（规则，不调用模型）：

| 数据形态 | 首选 | 备选 | 否决 / 降级 |
|----------|------|------|-------------|
| 1 行、1~4 个数值列 | `kpi` | `table` | 不画柱状图 |
| 时间列 + 1~6 数值列 | `line` | `area`、`column` | 点数 <3 → 表格 / 坡度图（P2） |
| 类别 3~6 + 1 指标 + 非负 + 合计有意义 | `donut` | `bar`、`column` | 含负值、切片 >6 → 条形 |
| 类别 3~12 + 1~2 指标 | `column` / `groupedColumn` | `bar`、`combo`（量纲不同） | 类别 >12 → 条形 + TOP N |
| 类别 >12 或长标签 | `bar`（横条，降序） | `column` + dataZoom | 名次列不当长度 |
| 2 个数值列、样本 ≥8 | `scatter` | `heatmap`（离散化）、`table` | 样本 <8 → 表格 |
| 2 个维度 + 1 指标 | `heatmap` | `groupedColumn`（维度基数小） | 缺失维度 → 表格 |
| 3~6 个同量纲指标、同一实体 | `radar` | `groupedColumn` | 量纲混杂 → 条形 |
| 有序阶段且逐级递减 | `funnel`（P2） | `bar` | 非单调 → 条形 |
| 目标值/基准列 | `bullet`（P2） | `combo` + 参考线 | 无目标 → 普通柱 |
| 纯文本/多列明细 | `table` | — | 不强行聚合 |

**适配分**（建议初始权重，后续用评测集调）：

```
+5  意图匹配（trend / compare / composition / distribution / correlation / ranking / detail / target）
+4  时间列 × 趋势意图 → line
+3  类别 3~12 × 对比意图 → column
+3  类别 >12 或长标签 × 排名意图 → bar
+4  非负 且 合计≈100% × 构成意图 → donut
+4  2 数值列 × 相关意图 且 n≥8 → scatter
+3  2 维度 + 1 指标 → heatmap
+3  3~6 同量纲指标 → radar
+3  计数 + 比率同屏（量纲不同）→ combo
+2  有目标列 → bullet / gauge

-20 Y 轴是名次列
-20 饼图含负值
-10 饼图切片 >6
-8  折线 X 不是时间/有序
-8  系列 >8（改 TOP 8 + 其他）
-6  原始行 >500 未聚合
-5  单行结果画柱状
```

规则输出按 `score` 排序，`score≥0.55` 的进候选池；每结果最多 5 个，整轮最多 12 个，避免 Prompt 膨胀。

### 4.3 数据事实（Facts）：让图和结论说同一件事

候选同时携带可复用的「事实」，Planner 写洞察句时只能引用这些事实（或结果里的原始数值）：

| fact | 计算 | 用途 |
|------|------|------|
| `range` | 最大值/最小值及对应维度 | 标题「广陵最高、宝应最低」 |
| `delta` | 同一实体两期差值（按 x/series 对齐） | 「5 月较 3 月下降 2.5 分」 |
| `growth` | 首尾变化率 | 趋势句 |
| `top_share` | 第一名占总和比例 | 构成句 |
| `concentration` | 前 3 名占比 / HHI | 「集中度高」 |
| `outlier` | 1.5×IQR 之外的点 | 「XX 为离群值」 |
| `target_gap` | 与目标/阈值列的差 | 「距 70% 还差 4.2pp」 |
| `correlation` | Pearson r（n≥8） | 散点标题「r≈0.62」 |
| `distribution` | 均值/中位数/标准差 | 分布图副标题 |
| `rank_shift` | 两期名次变化（P2） | 坡度图 |

事实生成必须是纯函数、可单测；数值格式与前端 `formatNumericDisplay` 保持一致，避免「图上 78.57、句子里 78.6」的错位。

### 4.4 Agent Planner

#### 两条通道：过程中「钉住」+ 定稿后「编排」

Agent 的自主性可以发生在两个时刻，二者互补：

| 通道 | 触发 | 作用 | 优先级 |
|------|------|------|--------|
| **过程中 pin** | 主 Agent 在推理中调用升级后的 `chart_semantic_result(result_key, ...)` | 模型认为某个中间结果就是答案的关键证据，主动固定一张图 | 高（Planner 必须保留或给出替换理由） |
| **定稿后编排** | 回答定稿后，宿主用完整证据目录调用 Planner | 补全、排序、写洞察句、决定不出图 | 默认 |

实现上：

- `chart_semantic_result` 增加 `result_key`（或兼容 `result_index`），把块追加到 `pinned_blocks`，**不再覆盖**；
- 同时新增只读工具 `list_query_results()`，返回本轮各结果的 `result_key + columns + row_count + 子任务`，让主 Agent 在推理中知道「还有哪些数据可画」，不必靠记忆；
- Planner 的输入带上 `pinned_blocks`；Prompt 要求「优先保留 pinned 块，只有明确不合适时才替换，并在 `reason` 说明」；
- 若 `pinned_blocks` 已覆盖全部结果且校验通过，可跳过 Planner，省一次调用。

这样既保留「边分析边出图」的即时性，又保证「回答定稿后」有一次全局视角的编排。

#### 触发条件（Gate）

```python
def should_plan(evidence, reports, question, settings) -> bool:
    if settings.insight_mode in {"off", "rules"}:     return False   # 关闭 / 强制规则
    if not any(item.rows for item in evidence):        return False   # 无数据
    if reports:                                        return False   # 报告已覆盖，改用紧凑 Deck
    if len(evidence) == 1 and len(evidence[0].rows) <= 1:
        return False                                                   # 单值 → 规则 KPI
    return True

# 调用侧
if settings.insight_mode == "off":
    deck = None
elif settings.insight_mode == "rules" or not should_plan(...):
    deck = compact_deck(...) if reports else rules_deck(catalog, candidates)
else:
    deck = planner_or_fallback(...)
```

推荐默认 `hybrid`：多行/多结果走 Planner；单值、纯明细、报告轮次走规则。

#### 输入

一次结构化调用，输入 JSON 控制在 ~2k tokens：

```json
{
  "question": "对比各区县 3 月与 5 月数学均分，并指出降幅最大的区县",
  "answer": "……（最终结论正文，截断 1200 字）",
  "results": [
    {
      "result_key": "r0",
      "subtask": "查询各区县两期均分",
      "row_count": 7,
      "columns": [
        {"name": "区县", "role": "dimension", "cardinality": 7},
        {"name": "3月均分", "role": "measure", "unit": "分"},
        {"name": "5月均分", "role": "measure", "unit": "分"}
      ],
      "sample_rows": [["市直", 88.4, 86.1], ["广陵", 82.1, 79.6]],
      "candidates": [
        {"id": "r0-c1", "type": "groupedColumn", "x": "区县", "y": ["3月均分", "5月均分"], "score": 0.92,
         "reasons": ["同一维度两期可比指标"], "facts": ["广陵降幅最大 -2.5 分", "市直最高 88.4"]},
        {"id": "r0-c2", "type": "bar", "x": "区县", "y": ["5月均分"], "score": 0.71}
      ]
    }
  ],
  "constraints": {"max_blocks": 3, "one_primary": true, "no_cross_result": true}
}
```

#### 输出（强制工具调用）

```python
class InsightBlockDraft(BaseModel):
    candidate_id: str | None          # 优先用候选
    result_key: str
    kind: Literal["kpi", "chart", "table", "callout"]
    chart_type: str | None = None     # 允许脱离候选显式指定
    x: str | None = None
    y: list[str] = []
    series: str | None = None
    sort: dict | None = None          # {"by": "...", "order": "desc"}
    top_n: int | None = None
    title: str = ""
    insight: str = ""                 # 一句洞察，数字必须来自 facts/结果
    show_table: bool = False
    annotations: dict | None = None   # 参考线 / 目标线 / 高亮点

class InsightPlanDraft(BaseModel):
    blocks: list[InsightBlockDraft] = Field(default_factory=list, max_length=4)
    narrative: str = ""               # 可选：整卡导语
    reason: str = ""                  # 编排理由，便于调试
```

Planner 的 Prompt 约束（要点）：

1. 只允许使用给定 `result_key` 与列名；不得新增查询、不得跨结果合并。
2. 每个块必须回答原问题的一部分；不要为同一组数据画两张表达同一件事的图。
3. 最多 1 个 KPI 块 + 1 个表格块；总块数 ≤3（复杂分析 ≤4）。
4. 标题写「比较/结论」，不要写「柱状图」；洞察句只引用 facts 中的数值。
5. 明细/名单类问题优先 `table`；不要为了出图而聚合。
6. 数据不足以支撑时返回空 `blocks`，并在 `reason` 说明。
7. 若回答本身已逐行列出明细，`show_table=false`。

调用方式复用现有 `question_assessment` 模式：`model.bind_tools([submit_insight_plan], tool_choice=...)`，失败重试一次，再失败走规则。

#### 成本与延迟

- 只跑一轮；输入 ~1.5~3k tokens，输出 ~400~800 tokens。
- 放在**最终答案定稿之后、`done` 之前**：先 `emit("summary")` 让正文立即出现，再算 Deck，最后 `emit("insight")` 与 `done`。前端在 `done` 前保持 `loading`，卡片显示骨架屏。
- 可配置 `INSIGHT_PLANNER_MODEL` 使用更小模型；`INSIGHT_PLANNER_TIMEOUT_MS` 超时直接规则兜底。
- 同一轮重试复用缓存（按 `question + evidence fingerprint`）。

### 4.5 Validator / Repair / Fallback

校验分四层，全部纯函数、可单测：

| 层 | 检查 | 处理 |
|----|------|------|
| Schema | 版本、枚举、字段类型、块数 ≤4 | 非法块丢弃 |
| Data | `result_key` 存在；`x/y/series` 在列中；y 为数值；donut 非负 | 尝试用同结果规则候选替换；仍失败则丢弃 |
| Semantics | 反模式（名次当长度、饼图 >6、折线 X 非时间、系列 >8、散点 <8 点） | 降级到备选类型；不通过则换规则候选 |
| Narrative | 洞察句数字是否能在 facts/结果中找到（容差 0.5% 或 0.05） | 去掉不可溯源句子，保留标题 |
| Composition | 去重（同 result + 同 y 集合）、块数上限、最多 1 KPI/1 表、主图唯一 | 按优先级裁剪：KPI → 主图 → 辅图 → 表 |

**兜底阶梯：**

1. Planner 成功且校验通过 → 用。
2. 部分块非法 → 保留合法块，用规则候选补齐缺失视角。
3. Planner 超时/格式失败 → 规则 Deck（主图选「最相关结果」，辅图选「不同意图的次优结果」）。
4. 全部非法 → 现有单图 `recommend_chart_spec(last_result)`（完全兼容旧行为）。
5. 无数据 → 不渲染 Deck。

规则 Deck 的「最相关结果」不再简单取最后一个：优先取「最后有数据的、且候选分最高」的结果；若前一个结果分更高且问题意图匹配，允许前移。

### 4.6 Insight Deck 协议

```json
{
  "kind": "insight",
  "version": 1,
  "source": "agent",
  "confidence": 0.86,
  "narrative": "两期对比看，整体均分回落，广陵降幅最大。",
  "blocks": [
    {
      "id": "b1",
      "kind": "kpi",
      "resultKey": "r0",
      "title": "关键指标",
      "items": [
        {"column": "5月均分", "agg": "avg", "label": "市均分", "unit": "分",
         "delta": {"value": -2.4, "unit": "分", "basis": "较3月"}}
      ]
    },
    {
      "id": "b2",
      "kind": "chart",
      "resultKey": "r0",
      "title": "各区县数学均分：3 月 vs 5 月",
      "insight": "广陵下降 2.5 分，降幅最大；市直仍以 86.1 分领先。",
      "spec": { "kind": "spec", "version": 1, "type": "groupedColumn", "...": "..." },
      "alternatives": ["groupedColumn", "bar", "line"],
      "showTable": true,
      "why": "r0-c1：同一维度两期可比指标，分组柱最易读"
    }
  ],
  "datasets": {
    "r0": {"columns": ["区县", "3月均分", "5月均分"], "rows": [["市直", 88.4, 86.1]]}
  }
}
```

块类型：

| kind | 用途 | 渲染 |
|------|------|------|
| `kpi` | 2~4 个关键指标，可带对比/涨跌 | KPI 条 |
| `chart` | 主图/辅图，绑定一个 `ChartSpec` | `ChartView` |
| `table` | 明细/名单/补充数据 | `SmartTable`（可折叠） |
| `callout` | 一句重点结论或口径说明 | 高亮行 |

KPI 块的计算规则沿用现有 `frontend-react/src/utils/chartKpi.ts`（单行取原值、两期取最新+差值、多行取均值/合计、优先比率/均分/人数），迁移为服务端纯函数，这样：① Planner 只需选列与聚合方式，不必编数字；② Deck 落库后历史回放不依赖前端重算；③ 前端 `KpiStrip` 保留为旧路径兜底。

`ChartSpec` 在 v1 基础上扩展（向后兼容）：

- `type` 增加 `scatter`、`heatmap`、`radar`、`combo`、`funnel`、`waterfall`、`bullet`、`gauge`；
- `encoding` 增加 `size`、`color`；
- `transform` 增加 `aggregate`、`normalize`、`cumulative`；
- `annotations` 增加 `target`、`highlight`；
- `source` 增加 `resultKey`（保留 `resultIndex` 兼容）；
- 新增 `narrative`（可选）。

### 4.7 前端渲染与交互

组件树（替换现有 `AnswerVisual` 内部，但保留其作为兼容壳）：

```
AnswerVisual
  ├─ 有 deck → InsightDeckView
  │     ├─ DeckNarrative（可选导语）
  │     ├─ KpiBlock
  │     ├─ ChartBlock × N
  │     │     ├─ ChartView（懒加载 ECharts）
  │     │     ├─ BlockToolbar（切换类型 / 展开 / 查看数据 / 导出）
  │     │     └─ SmartTable（可折叠）
  │     ├─ TableBlock
  │     └─ CalloutBlock
  └─ 无 deck → 旧路径（resolveChartSpec + KpiStrip + 主图 + 辅图 + 表）
```

关键行为：

1. **按 `resultKey` 取数**：`deck.datasets[key]` → `queryResults.find(r => r.key === key)` → 最后结果。找不到则跳过该块并记一条前端日志，不整卡失败。
2. **逐块切换图型**：`alternatives` 由服务端规则给出；用户切换只改本地状态（`deckOverrides[runId][blockId] = type`），不回写后端、不重新查数。
3. **密度控制**：桌面最多 1 主图 + 2 辅图；移动端默认只展开主图，其余折叠。
4. **状态**：查询中骨架屏；Planner 运行中「正在生成数据洞察…」；无 deck 时整块不渲染；单块渲染失败降级为该结果的表格。
5. **可访问**：`aria-label` = 标题 + 洞察句；每张图下方都有「查看数据表」；颜色 + 标签双编码。
6. **性能**：ECharts 仍按需加载；高级图型放独立异步 chunk；单图点数上限（折线 ≤500、散点 ≤2000、类别 ≤50），超出聚合/抽样并在副标题注明。

### 4.8 持久化与历史回放

不改表结构，继续用 `chart_config` JSON：

```json
{
  "kind": "insight",
  "version": 1,
  "source": "agent",
  "blocks": [...],
  "datasets": {"r0": {...}, "r1": {...}},
  "meta": {"planner_ms": 1840, "planner_tokens": 1620, "repairs": 1}
}
```

- `chart_type` 写主块类型（兼容旧统计与旧客户端）。
- **为什么把 `datasets` 放进 Deck**：历史只持久化最后一组结果（`exec_result = last_result`），而 Deck 可能引用更早的结果。把被引用结果的后处理数据集（≤200 行/块）内嵌，历史回放无需重查。总 JSON 上限 256KB；超限则丢弃次要块的 `datasets`，只保留主图与最后结果。
- 旧记录：`chart_config` 是单 spec 或 ECharts option → `adaptLegacyChartConfig` 转成「单块 Deck」或走旧路径。
- `exec_result` 增加 `insight_meta`（source、块类型、耗时、是否兜底、修复次数），供埋点与排障；不存大对象。

### 4.9 SSE 协议与兼容

新增 `insight` 事件，`chart` 事件保留：

```
summary  → 正文先出现
insight  → { kind, version, source, blocks, datasets, meta }
chart    → 主块的 chart_type/chart_spec（旧客户端兼容）
done
```

- 新前端：优先 `insight`；没有则回退 `chart`；再没有则旧启发式。
- 旧前端：忽略 `insight`，继续用 `chart`，行为不变。
- 事件顺序调整（summary 在 insight 前）对旧前端无影响；`loading` 仍由 `done` 结束。

---

## 5. 图表类型扩展

按「问答结果能直接画」的原则扩展，报告侧 `agent/education/charts.py` 不动。

| 类型 | 回答什么问题 | 编码 | ECharts | 阶段 |
|------|--------------|------|---------|------|
| `scatter` | 两个指标是否相关、离群点 | x/y 数值 + 可选 color/size | ScatterChart | P1 |
| `heatmap` | 二维矩阵（班×科、校×分段） | x/y 维度 + value | HeatmapChart + VisualMap | P1 |
| `radar` | 多维画像（六科/能力） | 指标列 + 实体行 | RadarChart | P1 |
| `combo` | 量 + 率同屏 | 柱 + 线，双轴 | Bar + Line | P1 |
| `funnel` | 阶段转化/达标 | 有序阶段 + 数值 | FunnelChart | P2 |
| `waterfall` | 增减因素拆解 | 初始值 + 正负变化 | 透明堆叠柱 | P2 |
| `bullet` | 目标达成/差距 | 值 + 目标 + 区间 | Bar + MarkLine | P2 |
| `gauge` | 单一达成率 | 0~100% | GaugeChart | P2 |
| `slope` | 两期名次/分数变化 | 两期 + 实体 | Line/Custom | P2 |

实现要点：

- 前端 `ChartSpecType`、后端 `SUPPORTED_CHART_TYPES`、校验白名单**三处同步**；加一个契约测试比对两端枚举快照，防漂移。
- `chartOption.ts` 每种类型一个纯函数分支，输入 `spec + rows + columns`，输出 option；单测覆盖空值、负值、单点、超量。
- `echartsRuntime.ts` 基础图（Bar/Line/Pie/Scatter）进主 chunk；Heatmap/Radar/Gauge/Funnel 进异步 chunk，按类型动态 `echarts.use`。
- 服务端 `spec_to_echarts_option` 只保留给兼容与测试；新类型不再要求服务端生成 option，避免两套渲染逻辑。

---

## 6. 关键算法（伪代码）

### 6.1 一轮的编排

```python
async def compose_insight(question, answer, evidence, reports, settings):
    catalog = build_catalog(evidence)                 # 4.1
    candidates = [c for item in catalog for c in generate_candidates(item, question)]  # 4.2
    if not catalog or not candidates:
        return None

    if not should_plan(catalog, reports, question):
        return rules_deck(catalog, candidates)        # 4.5 第 3 档

    try:
        draft = await planner(question, answer, catalog, candidates, timeout=8)
        deck = materialize(draft, catalog, candidates)  # candidate_id → spec
    except (Timeout, PlannerError):
        return rules_deck(catalog, candidates)

    deck = validate_and_repair(deck, catalog, candidates)
    if not deck.blocks:
        return rules_deck(catalog, candidates)
    return deck
```

### 6.2 规则 Deck

```python
def rules_deck(catalog, candidates):
    # 主图：最后有数据的结果中候选分最高者；若更早结果分明显更高且匹配意图，可前移
    primary = pick_primary(candidates)
    blocks = [primary]
    # KPI：单值结果或主图结果有可比指标
    if kpi := kpi_block(catalog): blocks.insert(0, kpi)
    # 辅图：不同结果、不同意图、分 ≥0.6
    if secondary := pick_secondary(candidates, exclude=primary): blocks.append(secondary)
    # 表格：明细意图或用户可能需要核对
    if table := table_block(primary): blocks.append(table)
    return Deck(source="rules", blocks=cap(blocks, 3))
```

### 6.3 校验修复

```python
def validate_and_repair(deck, catalog, candidates):
    blocks = []
    for block in deck.blocks:
        errors = validate_block(block, catalog)
        if errors:
            block = repair_with_candidates(block, catalog, candidates)
        if block and validate_block(block, catalog) == []:
            blocks.append(block)
    blocks = dedupe(blocks)                    # 同 result + 同 y 集合
    blocks = enforce_composition(blocks)       # ≤1 kpi、≤1 table、≤3 块、主图唯一
    return deck.with_blocks(blocks)
```

---

## 7. 代码改动清单

### 7.1 后端

| 文件 | 改动 |
|------|------|
| `src/chat/service/insight_plan.py`（新增） | 目录、候选、事实、Planner 调用、校验、修复、规则 Deck、KPI 计算、序列化 |
| `src/chat/service/chart_spec.py` | 扩展类型与校验；新增 `build_spec_from_intent`、`alternatives_for`；`source.resultKey` |
| `src/chat/service/deepagents_runtime.py` | 收集 `evidence` 目录；回合末调用 `compose_insight`；发 `insight` 事件；`chart_semantic_result` 增加 `result_key/result_index` 并累加 `pinned_blocks`；新增 `list_query_results` 只读工具；持久化 Deck；`summary` 前移 |
| `src/chat/service/turn_records.py` | `chart_config` 支持 Deck（无需改签名） |
| `src/chat/schemas.py`、`src/chat/api/chat.py`、`src/chat/crud/chat.py` | 读取/返回 Deck 兼容；`chart_type` 取主块 |
| `src/common/core/config.py` | `insight_mode`、`insight_max_blocks`、`insight_planner_model`、`insight_planner_timeout_ms` |

### 7.2 前端

| 文件 | 改动 |
|------|------|
| `src/components/chat/AnswerVisual.tsx` | 改为「有 Deck 用 Deck，无 Deck 走旧路径」 |
| `src/components/insight/InsightDeckView.tsx`（新增） | 块渲染、密度、骨架、空状态 |
| `src/components/insight/ChartBlock.tsx`（新增） | 图 + 工具栏 + 可折叠表 |
| `src/components/insight/KpiBlock.tsx`（新增） | 服务端 KPI 渲染；无则回退 `KpiStrip` |
| `src/components/charts/insightDeck.ts`（新增） | 类型、解析、`resultKey` 取数、旧数据适配 |
| `src/components/charts/chartOption.ts` | 新图型 option 构建 |
| `src/components/charts/echartsRuntime.ts` | 按需注册新图型，拆分 chunk |
| `src/components/charts/chartSpec.ts` | `ChartSpecType` 扩展、`resultKey`、校验同步 |
| `src/hooks/useChat.ts` | `insightByRunId`；`onInsight`；历史解析 Deck；`QueryResult.key` 用服务端 `result_key` |
| `src/api/adapter/chatAdapter.ts` | `insight` 事件 |
| `src/components/chat/CoworkChat.tsx` | 传 `results`（而非 `lastResult`）与 Deck；骨架 |
| `styles/cowork-chat.css` | Deck/块/KPI/工具栏样式（含暗色、移动端） |

### 7.3 保持不变

- `agent/education/charts.py` 与报告模板；
- 权限/脱敏/水印链路；
- `ChatExecutionPanel` 的完整结果预览（P1 再统一到 `ChartView`）。

---

## 8. 分阶段实施

| 阶段 | 范围 | 交付物 | 人日 | 退出标准 |
|------|------|--------|------|----------|
| **P0 骨架** | `result_key`；证据目录；Deck 协议与前端渲染；**纯规则多结果 Deck**；feature flag `off` | 后端 `insight_plan.py`（规则部分）；前端 `InsightDeckView`；双事件兼容 | 3~4 | 多结果轮次能出现 ≥2 个块；旧会话不回归；`insight_mode=off` 时行为完全不变 |
| **P1 Agent 编排** | Planner 结构化调用、校验/修复、兜底、持久化、埋点 | Planner + `submit_insight_plan`；`insight` 事件；Deck 落库；评测脚本 | 5~7 | 40 题评测类型一致率 ≥80%；非法块 0；P95 延迟 ≤3s；规则兜底可用 |
| **P2 图型扩展** | scatter / heatmap / radar / combo + 工具栏切换 | `chartOption` 新分支；ECharts 按需 chunk；契约测试 | 4~6 | 新图型在 10 条真实问题上正确渲染；bundle 增量 ≤40KB gzip |
| **P3 体验闭环** | 逐块切换/展开/导出、callout、暗色/移动端、A/B、自然语言改图 | 工具栏、导出、埋点看板、`INSIGHT_MODE` 灰度 | 3~5 | 用户切换率、空 Deck 率、渲染错误率可观测；移动端截图验收 |

**灰度策略：**

```
off      → 完全旧行为（默认初始）
shadow   → 照常算 Deck 并落库，但前端仍渲染旧卡片；只用于比对质量
rules    → 只渲染规则 Deck，不调模型
hybrid   → Planner + 规则兜底（目标默认）
agent    → 只信 Planner，非法即空（不建议长期开启）
```

先在 `shadow` 跑一周，比较「规则 Deck vs 旧单选型」的类型分布与人工抽检，再切 `hybrid`。

---

## 9. 测试与评测

### 9.1 后端单测

`tests/chat/test_insight_plan.py`：

- 目录构建：0 行结果不进、lookup 不进、重试去重、`result_key` 稳定；
- 候选生成：单值→KPI；时间→line；3~6 类构成→donut；>12 类→bar；两数值→scatter；两维度→heatmap；明细→table；
- 事实：delta/growth/top_share/outlier/target_gap/correlation 的边界（空值、单行、全相等、负值）；
- Planner 输出：合法、缺列、错类型、饼图负值、名次当 Y、块数超限、空 blocks；
- 修复：非法块被替换、重复块被去重、成分约束生效；
- 兜底：Planner 超时/异常 → 规则 Deck；规则也失败 → 单图 spec；
- 洞察句数字溯源：可溯源保留，不可溯源剔除。

`tests/chat/test_runtime_insight.py`：

- 两条查询 → Deck 两个块，分别绑定 r0/r1；
- 0 行结果后仍有稳定 `result_key`；
- 模型未调绘图工具也能出规则 Deck；
- 模型调了绘图工具 + Planner 成功 → 以 Deck 为准，不重复发；
- 报告轮次 Deck 收缩；
- 持久化 `chart_config.kind == "insight"`，回放可还原。

### 9.2 前端测试

- `insightDeck.test.ts`：解析、`resultKey` 取数、找不到块跳过、旧 `chart_config` 适配；
- `InsightDeckView.test.tsx`：块顺序、密度、骨架、空状态、a11y；
- `chartOption.test.ts`：新图型空值/负值/单点；
- `AnswerVisual.test.tsx`：有 Deck / 无 Deck 两条路径。

### 9.3 评测集

40 条（教育 30 + 通用 10），每条标注：期望块类型、期望绑定结果、期望 x/y、是否应出图。示例：

| # | 问题 | 期望 |
|---|------|------|
| 1 | 全市高三 3 月数学平均分 | KPI，不出柱图 |
| 2 | 对比各区县 3 月与 5 月数学均分 | groupedColumn，r0，两期 |
| 3 | 某校近两次数学均分走势 | line / 坡度，按时间 |
| 4 | 分数段分布 | column（直方），可加参考线 |
| 5 | 各校数学均分前 10 | bar，按均分降序，名次只做标签 |
| 6 | 各班 × 学科均分 | heatmap（班多）/ groupedColumn（班少） |
| 7 | 学生六科画像 vs 班均 | radar + 对比 |
| 8 | 均分和及格率有关系吗 | scatter + r，不退回表格 |
| 9 | 列出临界生名单 | table，不出图 |
| 10 | 距离 70% 及格率还差多少 | bullet / combo + 目标线 |

指标：`type_match`、`binding_match`、`coverage`、`insight_grounding`、`fallback_rate`、`p95_latency`、`tokens`。

### 9.4 回归

- 现有 `tests/chat/test_chart_spec.py`、`test_runtime_resilience.py` 全绿；
- 旧会话（单 spec / ECharts option）打开不白屏；
- `npm run typecheck`、`npm run build` 通过；
- `insight_mode=off` 时 SSE 与渲染行为与当前一致。

---

## 10. 风险与对策

| 风险 | 影响 | 对策 |
|------|------|------|
| Planner 幻觉列名/类型 | 渲染失败或误导 | 候选制 + `validate_chart_spec` + 规则替换；非法即降级 |
| 洞察句编数字 | 信任受损 | 只允许引用 facts；服务端数字溯源校验；不通过则删句 |
| 额外模型调用 | 成本/延迟 | 单值/明细/报告轮次跳过；小模型可配；超时 8s；缓存；shadow 先观测 |
| Deck 变成图表墙 | 阅读负担 | 硬上限 3~4 块；1 主图 + ≤2 辅图；移动端折叠 |
| 历史 JSON 膨胀 | DB/传输 | 数据集 ≤200 行/块，总 256KB 上限；超限丢次要数据集 |
| 前后端枚举漂移 | 校验/渲染不一致 | 契约测试比对枚举；共享 fixture |
| 多结果下标错位 | 画错数据 | 全面改用 `result_key`；`resultIndex` 仅兼容 |
| 报告与 Deck 重复 | 信息冗余 | 报告轮次 Deck 收缩为 0~1 块 + 报告入口 |
| 非确定性难排查 | 线上问题复现难 | 落库 `why`/`source`/`meta`；`shadow` 期保存 Planner 原始输出（脱敏） |
| 数据外发边界 | 隐私/合规 | Planner 只复用主模型已经看过的查询样本，不新增数据出口；可配置只传列画像与聚合统计，不传样本行 |
| 权限边界 | 数据越权 | Deck 只引用已执行结果；Planner 无 SQL/工具权限 |

---

## 11. 待确认

1. **是否接受每轮最多一次额外模型调用？** 建议接受，但只在多行/多结果时触发；也可先接小模型。
2. **高级图型优先级**：P2 先做 scatter / heatmap / radar / combo，还是先做 bullet / funnel？
3. **历史回放策略**：允许 Deck 内嵌被引用结果的数据集（≤200 行/块）吗？这是零迁移成本的关键。
4. **是否保留固定 KPI + 辅图作为兜底？** 建议保留在 `rules` 模式，`hybrid` 下由 Planner 决定。
5. **默认密度**：桌面默认「KPI + 1 主图 + 1 辅图」，还是「KPI + 主图」其余折叠？

---

## 附录 A：五类问题的目标 Deck

| 问题 | 结果集 | Deck |
|------|--------|------|
| 全市高三 3 月数学平均分 | 1 行 | KPI（均分、参考人数、及格率），无图 |
| 对比各区县 3 月 vs 5 月均分 | 7 行 × 3 列 | KPI（市均分 + 变化）+ groupedColumn（参考线：市均分）+ 表 |
| 某校近两次均分走势 | 2 行 | 坡度/哑铃（P2）或 KPI（最新 + 差值）+ 表，不用两点折线硬撑趋势 |
| 各班 × 学科均分 | 20 行 × 9 列 | heatmap（班 × 科）+ 学科均分条 + 表 |
| 列出临界生名单 | 多列文本 | 仅 table + callout（「共 N 人，其中 X 人距线 ≤5 分」） |

## 附录 B：Planner Prompt 草稿（要点）

```
你是数据回答的可视化编排器。输入包含：用户问题、已定稿的回答、以及本轮所有查询结果
（每个结果有 result_key、列画像、样本行、行数、候选图型与数据事实）。

任务：选择 0~4 个可视化块，使读者能最快验证回答中的结论。

硬约束：
1. 只能使用输入中存在的 result_key 与列名；不得假设未查询的数据。
2. 一个块只能绑定一个结果；禁止跨结果合并。
3. 最多 1 个 KPI、1 个表格；总块数 ≤3（复杂分析 ≤4）。
4. 同一组数据不要用两张图表达同一件事。
5. 明细/名单类问题用 table；不要为出图而聚合。
6. 标题写「比较/结论」，不写图型名；insight 只能引用 facts 中的数字。
7. 若数据不足以支撑任何图，返回空 blocks 并说明原因。

输出：调用 submit_insight_plan，逐块给出 candidate_id（优先）或显式 encoding，
以及 result_key、kind、title、insight、show_table。
```

## 附录 C：与现有文档的关系

- [qa-visualization-upgrade-plan.md](qa-visualization-upgrade-plan.md)：P0 的选型与协议来源；本方案在其上把「单图 + 规则」升级为「Deck + Agent 编排」。
- [qa-visualization-p0.md](qa-visualization-p0.md)：当前实现基线；本方案需改造 `AnswerVisual`、`useChat.chartByRunId`、`chart_semantic_result`。
- `docs/frontend-react-architecture.md`：组件与状态管理约定。
