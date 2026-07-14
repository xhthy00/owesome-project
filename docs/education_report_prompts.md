# 九大类学情报告 · Skill 级提示词规格

> 本文档按 **Cursor Agent Skill** 粒度编写，后续可直接拆成：
>
> ```
> .cursor/skills/edu-report-{type}/SKILL.md
> ```
>
> 每个报告类型一节 = 一份独立 Skill 的正文草稿；文首通用章 = 共享路由 Skill。

---

## 0. 拆 Skill 约定

### 建议目录

| Skill 目录名 | 对应 `ReportType` |
|--------------|-------------------|
| `edu-report-router` | （路由与槽位抽取，无独立渲染） |
| `edu-report-class-overview` | `class_overview` |
| `edu-report-grade-comparison` | `grade_comparison` |
| `edu-report-subject-diagnosis` | `subject_diagnosis` |
| `edu-report-student-profile` | `student_profile` |
| `edu-report-trend-tracking` | `trend_tracking` |
| `edu-report-tier-alert` | `tier_alert` |
| `edu-report-group-feature` | `group_feature` |
| `edu-report-comprehensive` | `comprehensive` |
| `edu-report-diagnostic` | `diagnostic_report` |

### 每个 Skill 的 frontmatter 模板

```yaml
---
name: edu-report-class-overview
description: >
  生成教育学情「班级总览报告」(class_overview)。在用户提到班级总览、成绩总览、
  班级成绩概览，或要求单班单场整体 KPI/分数段/能力画像时使用。不要用于各班横向对比、
  科目小题诊断、临界生预警或个人学情。
disable-model-invocation: true
---
```

`description` 必须同时写清 **WHAT（做什么）** 与 **WHEN（何时触发 / 何时不要触发）**，第三人称。

### 每节统一结构（便于复制进 SKILL.md）

1. Skill 元数据（name / description）
2. 何时使用 / 何时不要用
3. 必填槽位与抽取规则
4. Agent 执行步骤（含工具参数）
5. 标准多步计划原文（可喂给 Planner）
6. 输出契约与验收标准
7. 用户提示词（完整可复制）与反例
8. 已知坑点

---

## 通用路由 Skill 草稿：`edu-report-router`

### description（建议）

> Routes education analytics report requests to one of nine ReportType values. Use when the user asks for 学情报告、诊断报告、总览、横向对比、分层预警、趋势、综合分析 or similar. Extracts school/class/exam/subject/student_id slots and picks exactly one report type by priority.

### 意图优先级（高 → 低，命中即停）

1. 全市 / 大市 / 联考结构化诊断 → `diagnostic_report`
2. 学号 / 某个学生 / 学生档案 → `student_profile`
3. 多场考试综合 / 全程复盘 → `comprehensive`
4. 临界生 / 大幅退步 / 偏科预警 → `tier_alert`
5. 群体特征 / 按班级或区县画像 → `group_feature`
6. **班级总览 / 成绩总览 / 概览** → `class_overview`
7. 各班横向 / 全校对比 / 年级对比（无「总览」）→ `grade_comparison`
8. 学校/班级科目诊断（小题+知识点）→ `subject_diagnosis`
9. 历次趋势 / 进退步折线 → `trend_tracking`
10. 关键词模糊回落 → 再问槽位，不要猜渲染模板

### 槽位 Schema

| 槽位 | 字段名 | 规则 | 失败时 |
|------|--------|------|--------|
| 学校 | `school_name` | 全称，如「扬州中学」 | 可从数据源默认学校补 |
| 班级 | `class_name` | 统一成 `高三(10)班` | 总览/个人/预警类缺则追问 |
| 考试 | `exam_name` | **业务原文**：「连淮扬镇」「宁镇扬联考」 | **禁止**填「本次考试」「问题中的考试」；抽不到则先列考试清单 |
| 科目 | `subject_name` | 「数学」「语文」等全称 | 总览可「全科」；诊断类缺则追问 |
| 学号 | `student_id` | 数字/字符串原样 | 仅个人报告必需 |

### 考试名抽取铁律

- ✅ 用户句子里的专名、简称原样保留（去掉「的考试」「联考」后缀时要谨慎，优先保留用户原文中的核心专名）。
- ❌ 绝对不要输出：`本次考试`、`本场考试`、`问题中的考试`、`最近一次考试`（除非系统后续有明确 resolve，且不得写进 SQL WHERE）。
- Planner `_plan_label(exam)`：考试名为空时，计划文案写「考试名待确认」，**不要**写「问题中的考试」。

### 一次只出一类报告

若用户一句话同时含「总览 + 横向对比 + 预警」，按优先级只选一种，并在回复中说明其余可另问。

### SQL / 查数通用验收

学生级明细必须含：`student_id`、`score`、`exam_score`（满分）。  
禁止：仅返回班级聚合 KPI 就进入渲染。  
人数异常（如应为 50+ 却只有十几行）：优先排查考试名错误 / 班级过滤过严 / 连表丢行。

### 角标标题规则

渲染成功后展示名：

```text
{报告业务标题}【{报告类型中文名}】
```

示例：`扬州中学高三(10)班连淮扬镇数学考试班级总览【班级总览报告】`  
禁止：`【class_overview】业务标题`。

### 用户侧「万能槽位句」模板

```text
学校：{学校}
班级：{班级}          # 全校横向可不填班级
考试：{考试专名}      # 必须专名
科目：{科目}
学号：{学号}          # 仅个人报告
请生成【{报告类型中文名}】，不要生成其他类型报告。
```

---

## 1. Skill：`edu-report-class-overview`（班级总览报告）

### description

> Generates class overview report (class_overview): single-class KPI, score segments, ability portrait, grade rank, summary and recommendations. Use when user says 班级总览、成绩总览、班级成绩概览 or wants one class one exam overview. Do not use for 各班横向对比、科目小题诊断、临界生预警, or per-student archives.

### 何时使用

- 用户要「这一班这一场」的整体画像
- 关键词：`班级总览`、`成绩总览`、`班级成绩总览`、`总览报告`、`成绩概览`、`班级成绩概览`

### 何时不要用

| 用户说法 | 应路由到 |
|----------|----------|
| 各班横向 / 全校对比 | `grade_comparison` |
| 小题得分率 / 知识点薄弱 | `subject_diagnosis` |
| 临界生 / 退步预警 | `tier_alert` |
| 学号 / 某个学生 | `student_profile` |
| 历次考试趋势 | `trend_tracking` |

### 必填槽位

| 槽位 | 必填 | 说明 |
|------|------|------|
| school_name | 建议 | |
| class_name | **是** | |
| exam_name | **是（专名）** | |
| subject_name | 建议 | 可默认该班主科或问句中的科 |

### Agent 执行步骤

1. **确认槽位**：四元组齐全；考试名非空且非模糊词。
2. **DataAnalyst 查学生明细**（含 `student_id, score, exam_score, class_name, school_name`），可按 school+class+exam+subject 过滤。
3. **（可选）查年级对照**：同年级同场同科均分/排名所需聚合，供 RANK_INFO。
4. **组装渲染（二选一）**：
   - **推荐**：`build_class_overview_report_data_tool(school_name, class_name, subject_name, exam_name, render=true)`，依赖上游 `report_data` 自动 enrich。
   - 或：`render_html_report(template_name="education/class_overview.html", ...)`，但必须保证进入 enrich 的 data 有成绩行。
5. **禁止**：
   - 再调 `build_subject_diagnosis_sections_tool`
   - 输出「每位学生详细档案与个性化建议」大表
   - `exam_name="本次考试"` / `"问题中的考试"`
6. `terminate`，角标用「班级总览报告」。

### 标准多步计划原文（Planner 可用）

```text
步骤1：查询「{学校}」「{班级}」在「{考试专名}」「{科目}」下每位学生得分明细（student_id, score, exam_score），不得使用「问题中的考试」字样。
步骤2：必要时补充同年级同场同科对照统计，用于年级排名。
步骤3：调用 build_class_overview_report_data_tool 或 render education/class_overview.html，生成班级总览报告（KPI、分数段、学科能力画像、排名、建议），然后 terminate。
```

### 输出契约（模板关键块）

| 占位 / 区块 | 要求 |
|-------------|------|
| 核心 KPI | 人数、均分、最高/最低、及格率、优秀率、良好率、低分率、标准差说明 |
| 分数段分布 | 图+表；按 `exam_score` 分段，禁止满分误按 100 导致全 0 |
| 学科能力画像 | 雷达：知识点 → 多科均分 → 单科 KPI 五维回退 |
| 年级排名 | HTML 表，禁裸 JSON |
| 学科/维度拆解 | HTML，禁裸 JSON |
| SUMMARY / RECOMMENDATIONS | 面向班级的文字，非逐生建议 |
| **不要** | STUDENT_ARCHIVE 逐生建议区 |

### 验收标准

- [ ] 报告类型角标为【班级总览报告】
- [ ] 标题含学校、班、考试专名、科目（若有）
- [ ] 参考人数与班级实考人数接近（不应无故只有十来人）
- [ ] 分数段非全 0（卷面满分正确）
- [ ] 无 JSON 原文晾晒；无逐生档案建议区
- [ ] 标准差有自然语言说明（集中 / 适中 / 偏大 / 分化明显）

### 用户提示词（完整）

**A. 生产标准句（推荐）**

```text
请生成【班级总览报告】（class_overview）。

槽位：
- 学校：扬州中学
- 班级：高三(10)班
- 考试：连淮扬镇
- 科目：数学

硬性要求：
1. 考试名必须写「连淮扬镇」，禁止写成「本次考试」或「问题中的考试」。
2. 先查明细学生得分（含 exam_score），再渲染 education/class_overview.html 或调用 build_class_overview_report_data_tool。
3. 必须包含：核心 KPI、分数段分布、学科能力画像、年级对照排名、总体分析与改进建议。
4. 不要输出每位学生的详细档案与个性化建议。
5. 不要生成科目诊断或横向对比报告。
6. 成功后用 terminate 结束。
```

**B. 口语版**

```text
扬州中学高三(10)班「连淮扬镇」数学这场，给我一份班级总览报告：人数均分及格优秀、分数段、能力画像和年级位置就行，别做各班对比，也别出临界生名单。
```

**C. 追问补槽（考试名缺失时对用户说）**

```text
要出班级总览需要确认考试名称。请直接回复考试专名（例如「连淮扬镇」），不要回复「本次考试」。
```

### 反例

```text
❌ 分析一下高三(10)班这次考试情况
   → 考试名模糊，易写成「问题中的考试」，SQL 失败或人数错。

❌ 扬州中学连淮扬镇数学各班情况和总览一起出
   → 两类冲突；应拆成两次提问。

❌ 班级总览并附每个学生建议
   → 总览 Skill 明确不做逐生档案。
```

### 已知坑点

1. Planner 占位「问题中的考试」→ 查询失败或人很少；必须先 resolve exam。
2. 满分按 100 → 150 分卷分数段全 0。
3. `SUBJECT_BREAKDOWN` / `RANK_INFO` 若直接塞 dict，页面会显示 JSON。
4. 被 `is_school_exam_report_query` 误收成科目诊断 3 步 → 用户句中需保留「班级总览」且带班级。

---

## 2. Skill：`edu-report-grade-comparison`（班级横向对比报告）

### description

> Generates grade-wide class comparison report (grade_comparison): multi-class subject diagnosis tables without class_name filter. Use when user asks 各班横向对比、年级对比、全校各班对比. Do not use for single-class 班级总览 or 分层预警.

### 何时使用

- 同校同场同科，**多个班级**并排对比
- 关键词：`横向对比`、`各班对比`、`年级对比`、`全校各班`、`班级对比报告`

### 何时不要用

- 只问一个班总览 → `class_overview`
- 只要某一个班的小题/知识点深挖且带班名 → `subject_diagnosis`
- 「群体特征」「区县维度」→ `group_feature`

### 必填槽位

| 槽位 | 必填 | 说明 |
|------|------|------|
| school_name | 是 | |
| exam_name | 是（专名） | |
| subject_name | 是 | |
| class_name | **禁止传入** | fetch/sections 不得带班，否则退化为单班诊断 |

### Agent 执行步骤

1. 确认：学校 + 考试专名 + 科目；**确认用户要「各班」**。
2. `fetch_subject_diagnosis_data_tool(school_name, exam_name, subject_name, class_name="")`  
   - **class_name 必须空字符串或不传**。
3. `build_subject_diagnosis_sections_tool(..., class_name="", render=true)`  
   - 不要手抄 `item_rows`/`fetch_data`（防截断）。
4. 模板按路由应为横向对比展示；`terminate`。
5. **禁止**接着再渲 `class_overview`。

### 标准多步计划原文

```text
步骤1：fetch_subject_diagnosis_data_tool —— 学校={学校}，考试={考试专名}，科目={科目}，class_name 留空，拉取全校各班小题与知识点数据。
步骤2：build_subject_diagnosis_sections_tool —— 同样不传 class_name，render=true，生成【班级横向对比报告】。
步骤3：terminate。禁止再调用班级总览或预警工具。
```

### 验收标准

- [ ] 出现多个班级对比，而非单班 KPI 总览
- [ ] 请求链路中 class_name 全程为空
- [ ] 角标【班级横向对比报告】
- [ ] 考试名为专名

### 用户提示词

**A. 标准句**

```text
请生成【班级横向对比报告】（grade_comparison）。

槽位：
- 学校：扬州中学
- 考试：连淮扬镇
- 科目：数学
- 班级：不限（不要传 class_name）

硬性要求：
1. 必须用 fetch_subject_diagnosis_data_tool + build_subject_diagnosis_sections_tool，且 class_name 为空。
2. 对比各班得分率/知识点，不要只渲染单个班的班级总览。
3. 考试名用「连淮扬镇」，禁止「本次考试」「问题中的考试」。
4. 完成后 terminate。
```

**B. 口语版**

```text
扬州中学「连淮扬镇」数学，把所有班级横向对比一下，出班级横向对比报告，不要做成高三(10)班单独总览。
```

### 反例

```text
❌ 扬州中学高三(10)班连淮扬镇数学横向对比
   → 带了单班，易变成 subject_diagnosis。

❌ fetch 时 class_name='高三(10)班'
   → 全校对比被滤成单班。
```

### 已知坑点

- 「对比」+ 班级名 → 路由游离；提示词里写清「各班/全校」「禁止 class_name」。

---

## 3. Skill：`edu-report-subject-diagnosis`（科目诊断报告）

### description

> Generates subject diagnosis report (subject_diagnosis) with item-level and knowledge-point tables for a school or one class. Use when user asks 科目诊断、知识点薄弱、小题得分率. Do not use for 班级总览 KPI, 各班横向对比 (empty class_name path), or personal student reports.

### 何时使用

- 小题表 + 知识点表 + 薄弱点建议
- 关键词：`科目诊断`、`知识点诊断`、`小题分析`、`得分率`、`薄弱知识点`

### 何时不要用

- 「成绩总览/班级总览」→ `class_overview`
- 「各班横向」且不要班 → `grade_comparison`
- 学号维度 → `student_profile`

### 必填槽位

| 槽位 | 必填 |
|------|------|
| school_name | 是 |
| exam_name | 是（专名） |
| subject_name | 是 |
| class_name | 可选；有则单班诊断 |

### Agent 执行步骤

1. `fetch_subject_diagnosis_data_tool(school, exam, subject, class_name?)`
2. `build_subject_diagnosis_sections_tool(..., render=true)`（依赖运行时注入 fetch，禁止手抄大 JSON）
3. 模板：`education/subject_diagnosis.html`
4. `terminate`；禁止再 `render_html_report` / `build_chart_option_tool` 重复劳动

### 标准多步计划原文

```text
步骤1：fetch_subject_diagnosis_data_tool（学校={学校}，考试={考试专名}，科目={科目}，班级={班级或空}）。
步骤2：build_subject_diagnosis_sections_tool(render=true)，生成科目诊断 HTML（ITEM_TABLE、KNOWLEDGE_TABLE、SUMMARY、RECOMMENDATIONS）。
步骤3：terminate。
```

### 验收标准

- [ ] 有小题表与知识点表（非空，除非数据源确实无）
- [ ] 薄弱点建议与表数据一致
- [ ] 角标【科目诊断报告】
- [ ] 未误用班级总览 enrich

### 用户提示词

**A. 单班诊断**

```text
请生成【科目诊断报告】（subject_diagnosis）。

槽位：学校=扬州中学；班级=高三(10)班；考试=连淮扬镇；科目=数学。

要求：
1. fetch_subject_diagnosis_data_tool → build_subject_diagnosis_sections_tool(render=true)。
2. 必须输出小题得分率表与知识点得分率表，并给出薄弱知识点教学建议。
3. 不要渲染班级总览，不要做各班横向（除非我明确说各班）。
4. 考试名禁止「本次考试」「问题中的考试」。
```

**B. 校级（不限班）诊断**

```text
请生成【科目诊断报告】：扬州中学，「连淮扬镇」，数学；不限定班级。不要做成班级横向对比标题，按科目诊断模板输出小题与知识点分析。
```

### 反例

```text
❌ 高三(10)班连淮扬镇数学总览式诊断
   → 「总览」会抢 class_overview。
```

### 已知坑点

- LLM 手传空 `item_rows=[]` 会覆盖上游真实数据 → **禁止手传明细**。

---

## 4. Skill：`edu-report-student-profile`（学生学情报告）

### description

> Generates individual student learning report (student_profile). Use when user provides student_id or asks 学生档案、个人学科诊断、某考号分析. Prefer build_student_subject_diagnosis_tool; do not generate class-level overview as substitute.

### 何时使用

- 明确学号 / 「某某同学」可解析为 student_id
- 关键词：`学生学情`、`学生档案`、`个人诊断`、`考号`、`学号`

### 何时不要用

- 无学号的班级报告 → 其他类型
- 「全班临界生」→ `tier_alert`

### 必填槽位

| 槽位 | 必填 |
|------|------|
| student_id | **是** |
| subject_name | 强烈建议 |
| exam_name | 建议；模糊词视为空 |
| school_name / class_name | 建议，用于过滤 |

### Agent 执行步骤

1. 解析 `student_id`；缺失则追问，**禁止**用班均代替个人。
2. 优先：`build_student_subject_diagnosis_tool(datasource_id, student_id, subject_name, exam_name, school_name, class_name, render=true)`  
   - 多场自动切多次概览；单场走小题/知识点个人诊断。
3. 备选：`build_student_exam_report_data_tool` + `education/student_exam_analysis.html`（全科/档案向）。
4. `exam_name` 若为模糊词，传空字符串，勿写入 SQL。
5. `terminate`。

### 标准多步计划原文

```text
步骤1：确认学号={student_id}；如缺学号则停止并向用户追问。
步骤2：调用 build_student_subject_diagnosis_tool（datasource 用当前会话），科目={科目}，考试={考试专名或空}，学校/班级可选。
步骤3：渲染学生个人报告后 terminate。禁止改出班级总览。
```

### 验收标准

- [ ] 报告主体是该学号学生，不是班级 KPI
- [ ] 角标【学生学情报告】
- [ ] 无「问题中的考试」进标题/SQL

### 用户提示词

**A. 标准句**

```text
请生成【学生学情报告】（student_profile）。

槽位：
- 学号：22100128
- 学校：扬州中学
- 班级：高三(10)班
- 考试：连淮扬镇
- 科目：数学

要求：使用 build_student_subject_diagnosis_tool（或学生考试分析工具）输出个人小题/知识点或趋势；禁止输出班级总览；考试名禁止「本次考试」。
```

**B. 多场个人**

```text
学号 22100128，数学，汇总他参加过的多次考试学情（次数、均分、与第1名差距、趋势），出学生学情报告。
```

### 反例

```text
❌ 高三(10)班随便找个同学看看
   → 无学号，应追问。
```

---

## 5. Skill：`edu-report-trend-tracking`（成绩趋势报告）

### description

> Generates multi-exam trend tracking report (trend_tracking) with time-ordered scores. Use when user asks 成绩趋势、历次进退步、折线对比 across exams. Do not use for single-exam 班级总览 or multi-exam 综合分析报表 (comprehensive).

### 何时使用

- 明确「历次 / 趋势 / 进退步 / 折线」
- 需要 ≥2 场考试时间序

### 何时不要用

- 单场总览 → `class_overview`
- 「综合分析/复盘」篇章结构 → `comprehensive`
- 「动态性」三性诊断 → `diagnostic_report`

### 必填槽位

| 槽位 | 必填 |
|------|------|
| school_name | 是 |
| class_name 或 student_id | 至少一类主体 |
| subject_name | 建议 |
| exam 列表 | 建议用户点名，或「近 N 场」由系统解析 |

### Agent 执行步骤

1. 解析考试列表（专名数组），排序（按考试时间或用户给定顺序）。
2. 按场次查明细或均分序列（保留 student/class 粒度一致性）。
3. `render_html_report(template_name="education/trend_tracking.html", ...)` 或项目内等价趋势组装工具。
4. 图表：类目=考试名专名；禁止「考试1/考试2」空泛命名（除非无专名且已标注）。
5. `terminate`。

### 标准多步计划原文

```text
步骤1：列出主体（班级={班} 或 学号={id}）在科目={科目} 下的考试场次及得分（考试名用专名）。
步骤2：按时间排序，组装均分/个人分趋势序列。
步骤3：渲染 education/trend_tracking.html【成绩趋势报告】，terminate。
```

### 验收标准

- [ ] ≥2 个考点
- [ ] 角标【成绩趋势报告】
- [ ] 轴标签为考试专名

### 用户提示词

```text
请生成【成绩趋势报告】（trend_tracking）。

槽位：学校=扬州中学；班级=高三(10)班；科目=数学。
考试范围：连淮扬镇、宁镇扬联考、市直期末（按时间排序）。

要求：输出历次均分趋势与进退步解读；不要只做单场班级总览；考试名全部用专名。
```

口语：

```text
高三(10)班数学，把连淮扬镇到市直期末的成绩趋势报告做一下。
```

### 反例

```text
❌ 这次考试趋势怎么样
   → 单场无法做趋势。
```

---

## 6. Skill：`edu-report-tier-alert`（分层预警报告）

### description

> Generates tier alert report (tier_alert): critical/borderline students, sharp regression, subject imbalance. Use when user says 临界生、分层预警、大幅退步、偏科预警. Call build_tier_alert_report_data_tool; do not use subject diagnosis sections or class overview.

### 何时使用

- 关键词：`分层预警`、`临界生`、`临界生预警`、`大幅退步`、`偏科`、`预警名单`

### 何时不要用

- 只要分数段分布无名单 → `class_overview`
- 小题知识点 → `subject_diagnosis`

### 必填槽位

| 槽位 | 必填 |
|------|------|
| school_name / class_name | 建议有班 |
| exam_name | 是（专名）；退步类可能需对照场 |
| subject_name | 建议 |
| pass_threshold / full_score | 可选；有 `exam_score` 时应用满分×比例，勿默认 60 当分制 150 |

### Agent 执行步骤

1. 查明细：`student_id, score, exam_score`（多场则含历史场用于退步）。
2. **唯一渲染入口**：`build_tier_alert_report_data_tool(class_name, school_name, subject_name, exam_name, render=true)`  
   - 学生名单从上游 `score_rows` / `report_data` 解析，禁止手抄超大 JSON。
3. **禁止**：`build_subject_diagnosis_sections_tool`、`render_html_report` 重复渲染。
4. `terminate`。

### 标准多步计划原文

```text
步骤1：查询「{学校}」「{班级}」「{考试专名}」「{科目}」学生得分明细（含 exam_score）；若需退步预警再查对照场。
步骤2：build_tier_alert_report_data_tool(render=true)，生成【分层预警报告】（临界生/退步/偏科名单）。
步骤3：terminate。禁止调用科目诊断 sections。
```

### 验收标准

- [ ] 预警表有人（或明确说明无人触发阈值）
- [ ] 及格线相对满分合理（150 卷 ≈ 90，而非仍用 60）
- [ ] 角标【分层预警报告】
- [ ] 未误出总览模板

### 用户提示词

```text
请生成【分层预警报告】（tier_alert）。

槽位：学校=扬州中学；班级=高三(10)班；考试=连淮扬镇；科目=数学。

要求：
1. 使用 build_tier_alert_report_data_tool，不要走科目诊断或班级总览。
2. 识别临界生、大幅退步、偏科（按系统默认阈值即可）。
3. 阈值须按卷面满分换算，禁止在 150 分制上用 60 当分制线硬套。
4. 考试名禁止「本次考试」「问题中的考试」。
```

口语：

```text
高三(10)班连淮扬镇数学的临界生和退步预警名单给我出分层预警报告。
```

### 已知坑点

- 误走诊断 3 步计划；提示词必须点名 `build_tier_alert_report_data_tool`。

---

## 7. Skill：`edu-report-group-feature`（群体特征报告）

### description

> Generates group feature report (group_feature) aggregating scores by class/district dimensions. Use when user asks 群体特征、班级群体画像、区县对比特征. Call build_group_feature_report_data_tool; dimension in {class, ...}. Not for single-class overview.

### 何时使用

- 关键词：`群体特征`、`群体画像`、`特征分析`、按班级/区县聚合对比

### 何时不要用

- 「各班横向」且要诊断小题表话术 → 可与 `grade_comparison` 区分：群体特征偏聚合画像；横向偏诊断明细
- 单班总览 → `class_overview`

### 必填槽位

| 槽位 | 必填 |
|------|------|
| school_name | 是 |
| exam_name | 是 |
| subject_name | 是 |
| dimension | 默认 `class`；可按产品支持的 DIMENSIONS |

### Agent 执行步骤

1. 尽量提供 `datasource_id`，以便工具在明细不足时自动回拉全校。
2. `build_group_feature_report_data_tool(dimension="class", school_name=..., subject_name=..., exam_name=..., render=true)`
3. 禁止再调科目诊断 sections / `render_html_report`
4. `terminate`

### 标准多步计划原文

```text
步骤1：准备「{学校}」「{考试专名}」「{科目}」学生明细（多班）。
步骤2：build_group_feature_report_data_tool(dimension=class, render=true)，生成【群体特征报告】。
步骤3：terminate。
```

### 验收标准

- [ ] 维度下 ≥2 个群体，否则应拉数或提示数据不足
- [ ] 角标【群体特征报告】

### 用户提示词

```text
请生成【群体特征报告】（group_feature）。

槽位：学校=扬州中学；考试=连淮扬镇；科目=数学；聚合维度=class（班级）。

要求：调用 build_group_feature_report_data_tool；按班级聚合画像对比；不要单班总览；考试名用专名。
```

---

## 8. Skill：`edu-report-comprehensive`（综合分析报告）

### description

> Generates multi-exam comprehensive analysis report (comprehensive). Use when user asks 综合分析、多场复盘、阶段综合报告 across several exams. Call build_comprehensive_report_data_tool. Do not use for simple trend-only charts (trend_tracking) or citywide diagnostic_report.

### 何时使用

- 多场拼起来的综合结论、表格长板
- 关键词：`综合分析`、`综合报告`、`多场复盘`、`阶段总结`

### 何时不要用

- 只要折线趋势 → `trend_tracking`
- 全市三性结构 → `diagnostic_report`

### 必填槽位

| 槽位 | 必填 |
|------|------|
| 多场 exam | 是（列表） |
| class_name / school | 建议 |
| subject | 建议 |

### Agent 执行步骤

1. 多场明细查询 → 规范为 records / 长表。
2. `build_comprehensive_report_data_tool(records=..., exam_order=[...专名...], class_name=..., render=true)`  
   - `exam_order` 用专名列表。
3. 模板：`education/comprehensive.html`
4. `terminate`

### 标准多步计划原文

```text
步骤1：拉取考试列表={e1,e2,e3} 下主体成绩明细。
步骤2：build_comprehensive_report_data_tool(exam_order=[...], render=true)，生成【综合分析报告】。
步骤3：terminate。
```

### 验收标准

- [ ] 覆盖用户指定的多场
- [ ] 角标【综合分析报告】
- [ ] exam_order 无「本次考试」

### 用户提示词

```text
请生成【综合分析报告】（comprehensive）。

槽位：学校=扬州中学；班级=高三(10)班；科目=数学。
考试顺序：连淮扬镇 → 宁镇扬联考 → 市直期末。

要求：build_comprehensive_report_data_tool；综合多场得失与建议；不要只出趋势折线模板；考试名全部专名。
```

---

## 9. Skill：`edu-report-diagnostic`（结构化诊断报告）

### description

> Generates structured diagnostic report (diagnostic_report) with 一般性/特殊性/动态性分析, often citywide or cross-school exams. Use when user asks 结构化诊断、一般性特殊性动态性、全市诊断报告. Call build_diagnostic_report_data_tool after fetch; do not render inside the fetch subtask.

### 何时使用

- 关键词：`结构化诊断`、`诊断报告`、`一般性`、`特殊性`、`动态性`、`全市`、`大市联考诊断`

### 何时不要用

- 普通单班科目诊断 → `subject_diagnosis`
- 班级总览 → `class_overview`

### 必填槽位

| 槽位 | 必填 |
|------|------|
| exam_name | 是（专名，常为联考） |
| subject_name | 是 |
| scope_label | 建议（如「扬州市」） |
| score_rows / fetch | 由上游注入 |

### Agent 执行步骤

1. DataAnalyst / `fetch_subject_diagnosis_data_tool`（若任务写明仅 fetch：**本步 render=false / 禁止诊断渲染**）。
2. 下一步：`build_diagnostic_report_data_tool(render=true, exam_name, subject_name, scope_label=...)`  
   - **禁止**在 fetch 子任务里 `render=true`。
   - **禁止**手传巨型 `score_rows` JSON。
3. 模板：`education/diagnostic_report.html`
4. `terminate`

### 标准多步计划原文

```text
步骤1：fetch_subject_diagnosis_data_tool（考试={联考专名}，科目={科目}，范围按全市/ associa），仅取数，不渲染。
步骤2：build_diagnostic_report_data_tool(render=true)，组装一般性/特殊性/动态性【结构化诊断报告】。
步骤3：terminate。
```

### 验收标准

- [ ] 含一般性 / 特殊性 / 动态性结构（或模板等价块）
- [ ] fetch 步与 build 步分离
- [ ] 角标【结构化诊断报告】

### 用户提示词

```text
请生成【结构化诊断报告】（diagnostic_report）。

槽位：考试=连淮扬镇；科目=数学；范围=扬州市相关学校（scope_label=扬州市）。

要求：
1. 先 fetch 再 build_diagnostic_report_data_tool(render=true)。
2. 输出一般性、特殊性、动态性分析。
3. fetch 子任务禁止渲染；不要改成班级总览或普通科目诊断。
4. 考试名用专名。
```

### 已知坑点

- 在 fetch 子任务里调用 `build_diagnostic_report_data_tool(render=true)` 会被工具拒绝。

---

## 附录 A：工具速查（写 Skill 时用）

| 报告类型 | 首选终端工具 | 模板 |
|----------|--------------|------|
| class_overview | `build_class_overview_report_data_tool` | `education/class_overview.html` |
| grade_comparison | `fetch_*` + `build_subject_diagnosis_sections_tool`（class 空） | 横向对比路由模板 |
| subject_diagnosis | 同上（可带 class） | `education/subject_diagnosis.html` |
| student_profile | `build_student_subject_diagnosis_tool` | `education/student_subject_diagnosis.html` 等 |
| trend_tracking | 查数 + `render_html_report` | `education/trend_tracking.html` |
| tier_alert | `build_tier_alert_report_data_tool` | `education/tier_alert.html` |
| group_feature | `build_group_feature_report_data_tool` | `education/group_feature.html` |
| comprehensive | `build_comprehensive_report_data_tool` | `education/comprehensive.html` |
| diagnostic_report | `build_diagnostic_report_data_tool` | `education/diagnostic_report.html` |

### 渲染后共通禁令

1. 禁止考试名占位符进 SQL。  
2. 禁止终端工具成功后再次叠渲染。  
3. 禁止裸 JSON 作为页面正文。  
4. 禁止一类报告的成功标准用另一类模板冒充。

---

## 附录 B：自测清单（改 Skill 前跑一遍）

对每一类，用「标准句 A」跑一轮，记录：

| 检查项 | 结果 |
|--------|------|
| 路由到的 ReportType 是否正确 | |
| 计划步骤是否含错误占位考试名 | |
| 终态工具是否为附录 A 首选 | |
| 角标中文类型是否正确 | |
| 关键人数/表格是否非空 | |
| 是否误出其他类型区块 | |

---

## 附录 C：从本文生成 SKILL.md 的步骤

1. 新建 `.cursor/skills/edu-report-{type}/`。
2. 复制对应章节全部内容。
3. 顶部加 YAML `name` + `description`（用节内 description）。
4. 将「用户提示词」保留为 Examples；将「Agent 执行步骤」作为 Instructions 主体。
5. `edu-report-router` 单独拆出，供人工 `@` 或作为总入口。
6. 默认 `disable-model-invocation: true`，避免九个 Skill 互相抢触发；需要自动路由时仅放开 router。

---

## 附录 D：最小可复制「用户提问」速查

```text
1 总览：请生成【班级总览报告】学校=扬州中学；班=高三(10)班；考试=连淮扬镇；科=数学。禁「本次考试」。禁逐生建议。
2 横向：请生成【班级横向对比报告】学校=扬州中学；考试=连淮扬镇；科=数学；class_name 必须为空。
3 诊断：请生成【科目诊断报告】学校=扬州中学；班=高三(10)班；考试=连淮扬镇；科=数学。小题+知识点。
4 个人：请生成【学生学情报告】学号=22100128；考试=连淮扬镇；科=数学。
5 趋势：请生成【成绩趋势报告】班=高三(10)班；科=数学；考试=连淮扬镇,宁镇扬联考,市直期末。
6 预警：请生成【分层预警报告】班=高三(10)班；考试=连淮扬镇；科=数学。用 build_tier_alert_report_data_tool。
7 群体：请生成【群体特征报告】学校=扬州中学；考试=连淮扬镇；科=数学；dimension=class。
8 综合：请生成【综合分析报告】班=高三(10)班；科=数学；多场按时间综合。
9 结构：请生成【结构化诊断报告】考试=连淮扬镇；科=数学；scope=扬州市。先 fetch 再 build_diagnostic。
```
