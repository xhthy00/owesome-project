# 班级薄弱学科诊断（同校同科班际对比）

日期：2026-09-03  
状态：待实现  
读者：实现 agent / review agent

---

## 1. 背景与目标

### 1.1 问题

用户口语问句（验收用例，**不得改成冗长提示词**）：

> 2026届高三1月期末考试，请分析一下学校B11仙城中学高三(1)班的薄弱学科，然后看薄弱学科的具体题目，再给出建议

当前回答容易把「薄弱学科」理解成**同一班级不同学科互比**（语文均分 vs 数学均分）。各科试卷难度与满分不同，该比较在考情分析中不可用。

正确口径：**同校、同场、同年级、同一学科，该班 vs 其他班级**。选科与下钻由系统完成，用户不必写出方法。

### 1.2 目标

1. 命中「指定班级 + 薄弱学科」时，走专用确定性链路，不被班级总览 / 多科雷达抢走。
2. 用 A 规则选出薄弱学科；无命中则如实结论，不下钻题目。
3. 有命中则对所有薄弱科下钻小题（优先本班相对本校其他班失分的题），再给建议。
4. **不新增** `ReportType`，不改班级总览在未命中本问法时的行为。

### 1.3 已确认口径

| # | 决策 | 口径 |
|---|------|------|
| 1 | 比较对象 | 同校、同场、同年级、同一学科、不同班级 |
| 2 | 选考科目 | 只与本场**同样考了该科**的班级比 |
| 3 | 薄弱规则 A | 名次在后 30%，**或**均分低于对照班均分 ≥ 5 分（满足一条即可） |
| 4 | 「后 3 名」 | 仅当对照班数 **≥ 10** 时启用，与后 30% 取并集 |
| 5 | 无命中 | 如实「各科相对本校均无明显薄弱」+ 位置表；**不下钻题目** |
| 6 | 下钻数量 | 所有命中的薄弱科都下钻小题 |
| 7 | 问法 | 保持原句；规则内化到系统 |
| 8 | 实现形态 | 方案 1：探测器 + 硬路由 + **一个**确定性工具（仿知识点分层对比） |

### 1.4 非目标

- 新报告类型、新 Jinja 模板文件
- 改班级总览雷达 / `SUBJECT_BREAKDOWN` 的各科互比（未命中本问法时保持原样）
- 历次考试趋势、全市校际、学生个人偏科
- 把阈值做成 `EducationConfig` 配置项
- 要求用户把口径写进问句

---

## 2. 意图命中

### 2.1 `is_class_weak_subject_query(question) -> bool`

正词（命中任一）且能抽出班级名：

- `薄弱学科`
- `薄弱科目`
- `学科薄弱`
- `科目薄弱`

排除（任一为假）：

- 全市 / 结构化诊断 / 个人学号画像
- `is_school_class_comparison_query`（各班横向，未指定「某班的薄弱学科」）
- `is_subject_research_report_query`（教研）
- 仅有「薄弱知识点 / 薄弱小题」而**没有**正词（仍走科目诊断）
- 仅「班级总览 / 成绩总览」（无正词）

学校名、考试名不是探测器必要条件（抽不到时由工具报缺槽位）。验收原句必须为 True。

### 2.2 路由（硬约束，仿 `is_knowledge_cohort_gap_query`）

探测器为真时：

- `classify_report_intent_sync`：`needs_report=true`，`report_type=SUBJECT_DIAGNOSIS`，`source=hard`（只借枚举桶，不走普通科目诊断三步）
- `should_use_deterministic_report_plan`：True
- `plan_items_for_route` / `plan_items_for_report_type`：**先于** `build_school_subject_report_plan_items` 拦截
- `coerce_plan_to_route`：计划不含本工具则替换
- `plan_matches_report_type`：blob 含本工具名视为匹配

排在班级总览、`is_school_exam_report_query` 打分之前，避免被总览或多科雷达抢走。

---

## 3. 计划与工具

### 3.1 确定性计划（1 步）

`build_class_weak_subject_plan_items(question)` → 单个 ToolExpert 子任务：

```
调 build_class_weak_subject_report_data_tool(school_name=…, class_name=…, exam_name=…, render=true)
分析该班薄弱学科（同校同年级同科班际对比）并按需下钻小题；完成后 terminate。
禁止 build_class_overview_report_data_tool / build_subject_diagnosis_sections_tool / execute_sql 自行比各科均分。
```

科目参数**不传**（由工具按成绩扫描全部实考科目）。

### 3.2 工具

`build_class_weak_subject_report_data_tool`（注册进 `EDUCATION_TOOLS`）：

1. 校验 `class_name`、`exam_name`；缺则返回可读错误，不渲染。
2. 拉该校该场**全班全科**成绩（不按班过滤）。实现上复用现有 education 取数（与班级总览 `RANK_INFO` 的 peer 拉取同类，禁止 DataAnalyst 手写 SQL）。
3. 纯函数算出位置表 + 薄弱列表。
4. 若薄弱非空：对每一门薄弱科调用已有 `_fetch_subject_diagnosis_rows`（**不传 class_name**），再算班际小题差。
5. `render=true` 时组装 HTML 并走现有 HTML 报告推送（与 `compare_knowledge_cohort_tool` 同模式）。

---

## 4. 计算（纯函数，无 I/O）

新模块 `src/agent/education/class_weak_subject.py`，风格对齐 `knowledge_cohort.py`。

### 4.1 对照班

对每个科目 `S`：

- 年级：`parse_grade_from_class(目标班)`；只保留同年级。
- 该班在 `S` 上参考人数 ≥ 3 才参与该科。
- 对照班 = 同年级、本场 `S` 参考人数 ≥ 3 的班级（含本班）。
- 对照班均分 = 这些班的班级均分再平均（班均的均，不是学生池总均），避免大班淹没。
- 排名：按班级均分降序，1 最好。

### 4.2 A 规则

常量（模块内写死，不进配置）：

- `WEAK_RANK_RATIO = 0.30`
- `WEAK_AVG_GAP = 5.0`
- `LAST_N_MIN_CLASSES = 10`
- `LAST_N = 3`
- `MIN_CLASS_N = 3`

科目 `S` 对目标班为薄弱，当：

```
rank > ceil(n * (1 - WEAK_RANK_RATIO))     # 后 30%，n=10 → rank≥8
或 abs 分差：school_avg - class_avg ≥ 5
或 (n ≥ 10 且 rank > n - LAST_N)           # 后 3 名
```

并列均分：同均分同名次（dense rank），避免随机误伤。

无命中：`weak_subjects = []`，不下钻。

有命中：全部薄弱科都作为下钻对象。

### 4.3 小题

对下钻科：各班 × 题号得分率。对照 = 同年级考了该科的其他班（不含本班）得分率平均。

- **班级特差**：`class_rate - peer_rate ≤ -8` 个百分点（写死）。按差值升序，最多 15 题。
- **共性难点**：本班与对照均 < 60%，且不在特差列表。最多 8 题，单独一小节，**不作为薄弱学科的判定依据**。

无特差也无共性时：该科只保留班际 KPI，说明「小题相对本校其他班无明显落后」。

### 4.4 建议

规则短句，禁止空泛「加强基础」：

- 有特差题：点名题号，建议对本班加练 / 讲评。
- 仅共性难点：说明全年级都难，建议跟年级进度讲评，不单开本班锅。
- 无薄弱科：建议维持，不必针对某科加课时。

---

## 5. HTML 产出

不新建模板文件。模块内拼 HTML（同 `render_knowledge_cohort_html`），结构固定：

1. 标题：`{校} {班} · {考试} · 薄弱学科`
2. 口径一句：同校同年级同科班际对比，非本班各科互比。
3. 各科位置表（科目、本班均分、对照班均、分差、名次、及格率差、是否薄弱）。
4. 无薄弱：一段如实结论，无小题区。
5. 有薄弱：每门下钻科一节（特差题表、可选共性难点、建议）。

禁止把本班各科均分画在同一张雷达上作为薄弱证据。

---

## 6. 文件切面

| 文件 | 职责 |
|------|------|
| `src/agent/education/class_weak_subject.py` | **新建**：对比 / A 规则 / 小题差 / HTML |
| `src/agent/education/query_parse.py` | 探测器 + `__all__` |
| `src/agent/education/intent_router.py` | 硬路由、plan 拦截、coerce、`plan_is_fact_query` 排除本工具 |
| `src/agent/expand/planner.py` | `build_class_weak_subject_plan_items` |
| `src/agent/education/tools.py` | 取数包装 + 注册工具 |
| `tests/agent/test_class_weak_subject.py` | **新建**：纯函数 + 探测器 |
| `tests/agent/test_intent_router.py`（或现有路由测） | 验收句硬路由、不抢总览 |

不改：`class_overview` 填充、`stats.compute_imbalance_degree`、科目诊断默认三步（仅被本探测器截走）。

---

## 7. 测试与验收

### 7.1 纯函数

- 10 班：第 9 名 → 薄弱；第 5 名且低 6 分 → 薄弱；第 4 名且低 2 分 → 非薄弱。
- 4 个物理班：后 3 名**不**启用；仅后 30% + 5 分规则。
- 选考：未考物理的班不进分母。
- 全科优于对照且不在后 30% → `weak_subjects` 空，调用方不得去 fetch 小题。
- 3 门薄弱 → 下钻名单长度为 3。

### 7.2 探测器 / 路由

- 验收原句 → True，计划含 `build_class_weak_subject_report_data_tool`，不含班级总览 / sections。
- `…高三(1)班班级总览` → False。
- `…高三(1)班数学薄弱知识点`（无「薄弱学科」）→ False。
- `扬州中学各班数学横向对比` → False。

### 7.3 手工验收

原句走通后：结论学科来自班际名次/分差，而不是「本班数学均分低于本班语文」。无薄弱时页面无小题表。

---

## 8. 风险

- **校名抽取**：`B11仙城中学` 须能命中现有 `extract_school_target`；若抽不到，工具应用问句里的校名槽，不要静默改成全市。
- **考试名**：`2026届高三1月期末` 须走现有考试解析；对不上则工具报错，不降级成班级总览。
- **tools.py 体积**：只加一个 `@tool` 包装，计算不写进 `tools.py`。
