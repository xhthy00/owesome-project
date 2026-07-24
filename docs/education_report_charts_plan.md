# 教育报告图表增强方案

> 状态：方案对照仍见下文；**2026-07-23～24 本轮实际落地**见 §7（以代码为准，勿以旧「已全部完成」表述为准）。  
> 对照客户要求：趋势 / 分布 / 对比 / 雷达 / 柱状 / 折线 / 热力 / 知识点薄弱清单。  
> 权限策略：继续 **只靠 SQL 行级数据权限**，不按 `edu_role` 开关图表类型。

相关代码：`src/agent/education/charts.py`、`templates.py`、`orchestrator.py`、`trend_tracking.py`、`subject_diagnosis.py`、`tools.py`、`src/agent/resource/tool/business.py`、`src/agent/resource/templates/education/*.html`

---

## 1. 目标

在现有 9 类报告上补齐客户要求的展示方式，优先填「完全无图」与「对比/热力缺失」缺口；复用已有 ECharts builder，少扩新类型。

**本轮约束（必须遵守）：**

- 只加展示槽位；无数据空串，前端隐藏。  
- **不改**意图分流 / planner / KPI 口径 / `score_rows` 回写。  
- 禁止为出图拉全校 peer 并写回本班成绩（曾导致班级总览人数扩成 208）。

---

## 2. 九类报告：更改前后图表对比（方案目标，非全部已落地）

计数口径：**模板中可渲染的 ECharts 图表槽位**（同一占位符只计 1；有数据才显示，无数据前端隐藏）。不含纯 HTML 表。薄弱知识点「芯片清单」单独注明，不计入图表数。

### 2.1 数量总览（方案目标）

| # | 报告 | 更改前（张） | 方案目标（张） | 净增 | 主要新增（方案） |
|---|------|-------------|---------------|------|------------------|
| 1 | 班级总览 | 2 | 3 | +1 | 薄弱知识点柱（对照柱已放弃，见 §7） |
| 2 | 班级横向对比 | 1 | 4 | +3 | 及格率柱；优秀率柱；班×分数段热力 |
| 3 | 科目诊断 | 4 | 4～8 | — | 本轮决定**不加**（已有分布/知识点/能力/题型，再加意义有限） |
| 4 | 学生学情 | 8 | 9 | +1 | 我 vs 班均对比柱 |
| 5 | 成绩趋势 | 1 | 1 | 0 | 本轮曾试加比率/进退步图后**整段回滚** |
| 6 | 分层预警 | 0 | 2 | +2 | 三类人数饼；临界生分数分布（班间对比未做） |
| 7 | 群体特征 | 2 | 4 | +2 | 分组×分数段热力；Top 组多维 KPI 柱 |
| 8 | 综合分析 | 9 | 10 | +1 | 跨场薄弱知识点柱 |
| 9 | 结构化诊断 | 5 | 6 | +1 | 薄弱知识点柱 |

### 2.2 本轮已落地（以仓库为准）

| # | 报告 | 模板 | 本轮新增 / 保留 |
|---|------|------|-----------------|
| 1 | 班级总览 | `class_overview.html` | **薄弱知识点 TopN 芯片 + 横向柱**（`WEAK_KNOWLEDGE_LIST` / `WEAK_KNOWLEDGE_CHART`） |
| 6 | 分层预警 | `tier_alert.html` | **预警类型饼图** + **临界生分数分布柱**（`TIER_TYPE_CHART` / `CRITICAL_DIST_CHART`） |

原有未删：班级总览分数段柱、科目雷达；分层预警 KPI 卡片与三类名单表。

### 2.3 本轮明确不做 / 已回滚

| 报告 | 项 | 原因 |
|------|----|------|
| 班级总览 | `COHORT_COMPARE_CHART`（本班 vs 年级对照） | 拉 peer / 误写 `score_rows` 易把人数扩成多人次（如 208）；已还原 |
| 科目诊断 | 再叠班对比柱 / 薄弱柱 | 已有四图，加了信息重复；本轮不加 |
| 成绩趋势 | `TREND_RATE_CHART` / `TREND_DIST_CHART` | 已实现后整段回滚，模板仍只有均分折线 |

---

## 3. 实施优先级（历史方案条目，保留备查）

### P0 — 分层预警

| 占位符 | 图表 | 本轮 |
|--------|------|------|
| `TIER_TYPE_CHART` | 三类人数饼 | **已做** |
| `CRITICAL_DIST_CHART` | 临界分数分布 | **已做** |
| `CLASS_ALERT_CHART` | 多班预警人次 | **未做** |

### P1 — 年级对比 / 班级总览

| 占位符 | 说明 | 本轮 |
|--------|------|------|
| `PASS_COMPARE_CHART` 等 | 年级对比报告 | 非本轮重点 |
| `COHORT_COMPARE_CHART` | 班级总览对照柱 | **做过又回滚** |
| `WEAK_KNOWLEDGE_*` | 班级总览薄弱 | **已做**（见 §7.1） |

### P2 — 成绩趋势 / 科目诊断

| 项 | 本轮 |
|----|------|
| 趋势比率折线 / 进退步分布 / 年级折线 | 比率+进退步试过后**回滚**；年级折线未做 |
| 科目诊断班对比柱 / 薄弱强化 | **不加** |

---

## 4. 技术约定

- 后端只产出 ECharts `option` JSON 字符串；浏览器 `echarts.init().setOption`。  
- 优先复用：`pie`、`score_distribution`、`knowledge_bar`、`trend_line` 等。  
- 无数据：option 空串，前端隐藏 chart 容器 / section。  
- **不**按角色隐藏图表类型。  
- **不**为出图回写 / 污染本班 `score_rows`（避免 KPI 人数异常）。  
- 均分与比率若同页展示，须拆图分轴（量纲不同）；本轮趋势相关已回滚，约定仍保留。

---

## 5. 验收（本轮）

1. **班级总览**：有知识点行时可见薄弱芯片 + 柱；整班得分率都 ≥60% 时仍应出相对最低 TopN（见 `pick_weak_knowledge_topn`）。人数仍为本班单次口径（如约 52），不应因出图变成 208。  
2. **分层预警**：有预警对象时可见三类饼；有临界生分数时可见临界分分布；无人预警则两段隐藏。名单表与识别逻辑不变。  
3. **成绩趋势**：仍为原均分折线 + 明细表（无新增图）。

---

## 6. 变更记录

| 日期 | 说明 |
|------|------|
| 2026-07-23 | 初稿；启动分层预警 / 班级总览等方案条目 |
| 2026-07-23～24 | **本轮落地**：班级总览薄弱芯片+柱；分层预警两类图（详见 §7） |
| 2026-07-23～24 | 班级总览「本班 vs 年级对照」试过后回滚（人数污染） |
| 2026-07-24 | 成绩趋势「及格/优秀率折线 + 进退步分布」试过后整段回滚 |
| 2026-07-24 | 本文档改为以本轮实际代码为准，纠正「九类已全部补齐」的过时表述 |

---

## 7. 本轮修改说明（怎么改的）

### 7.1 班级总览 — 薄弱知识点 TopN 芯片 + 横向柱

**保留结果**

| 占位符 | UI |
|--------|-----|
| `WEAK_KNOWLEDGE_LIST` | 芯片清单 HTML（`edu-diag-chips`） |
| `WEAK_KNOWLEDGE_CHART` | 横向柱 ECharts JSON（`knowledge_bar`） |

**改了哪些文件**

| 文件 | 改动 |
|------|------|
| `src/agent/resource/templates/education/class_overview.html` | 增加「薄弱知识点」section；无 list/chart 时 JS 隐藏 |
| `src/agent/education/templates.py` | `CLASS_OVERVIEW` 必填键增加 `WEAK_KNOWLEDGE_LIST` / `WEAK_KNOWLEDGE_CHART` |
| `src/agent/resource/tool/business.py` | `_enrich_class_overview_archive` 调用 `_fill_class_overview_weak_knowledge` |
| `src/agent/education/subject_diagnosis.py` | 新增 `pick_weak_knowledge_topn`：先 `< 阈值`，否则回退得分率最低 TopN |
| `src/agent/education/tools.py` | `build_class_overview_report_data_tool`：缺 knowledge 时仅按本班自动 fetch 知识点；**不拉 peer、不改写 `score_rows`** |
| `src/agent/education/orchestrator.py` | `_fill_class_overview_weak_knowledge`：config_edu 路径补薄弱图；排名仍只填 `RANK_INFO` |

**取数与展示逻辑**

1. 聊天路径：工具有 `datasource_id` + `class_name` 且无 `knowledge_rows` → `_fetch_subject_diagnosis_rows`（本班范围）只写入 `knowledge_rows`。  
2. enrich / orchestrator：`enrich_knowledge_rows` → `pick_weak_knowledge_topn` → `build_weak_knowledge_list_html` + `build_weak_knowledge_chart`。  
3. 曾试「本班 vs 年级对照柱」+ 全校 peer autofetch：会导致 KPI 人数异常 → **已整段删除**（模板 section、`COHORT_COMPARE_CHART`、peer 注入均回滚）。

**为何曾不显示**：仅用「得分率 &lt; 60%」时，整班最低约 65% 会得到空列表，模板整段隐藏；故改为绝对薄弱优先、否则相对 TopN。

---

### 7.2 分层预警 — 三类饼 + 临界生分数分布

**保留结果**

| 占位符 | UI |
|--------|-----|
| `TIER_TYPE_CHART` | 临界生 / 大幅退步 / 偏科生人数饼图（`pie`） |
| `CRITICAL_DIST_CHART` | 临界生名单分数的分数段柱（`score_distribution`） |

**改了哪些文件**

| 文件 | 改动 |
|------|------|
| `src/agent/resource/templates/education/tier_alert.html` | 引入 echarts；预警概览下增加两段图表；空 option 隐藏 |
| `src/agent/education/templates.py` | `TIER_ALERT` 必填键增加上述两占位符 |
| `src/agent/education/tools.py` | 仅在 `_build_tier_alert_template_data` 末尾用**已识别名单**填图（聊天工具与 orchestrator 共用此函数） |
| `tests/agent/test_tier_alert_charts.py` | 覆盖有预警出图、无预警空图、HTML 含 chart 槽位 |

**怎么填（不影响识别逻辑）**

1. **不改** `identify_at_risk_students`、名单表、异常落库。  
2. 饼图：`len(critical/regression/imbalanced)` → `build_chart_option("pie", …)`；三类合计为 0 则空串。  
3. 临界分布：收集临界生 `score` → `compute_score_stats` 得 `segments` → `score_distribution`；无临界生或无数则空串。  
4. 及格线仅用于分段估计满分（`pass_line / pass_ratio`），不改变预警判定。

**未做**：`CLASS_ALERT_CHART`（班间预警对比）。

---

### 7.3 成绩趋势 — 已回滚（勿再按「已上线」验收）

曾在 `trend_tracking.py` / `trend_tracking.html` 增加：

- `TREND_RATE_CHART`：历次及格/优秀率折线  
- `TREND_DIST_CHART`：本班学生首→末场进退步饼图  

以及 `charts._trend_line` 可选 `y_name` / `y_max`。

**2026-07-24 已全部还原**：趋势报告恢复为仅 `TREND_CHART`（均分折线）+ 明细表 + 变化分析。  
说明：当时出现「考试名有、参考人数 0」与 `subject_name=各科` 取分失败有关，与上述图表试装并存；按产品决定功能不做，故整段回滚，未保留比率/进退步槽位。

---

### 7.4 其它报告（本轮结论）

| 报告 | 结论 |
|------|------|
| 科目诊断 | 不加图 |
| 学生学情 / 群体特征 / 综合 / 结构化诊断 | 本轮未改模板出图；方案表仍可作后续 backlog |
