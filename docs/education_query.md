# 教育场景问数配置说明

## Schema 配置

固定教育域表结构映射见 [`config/education_schema.json`](../config/education_schema.json)。

- **不绑定数据源连接**：用户通过控制台选择任意 PG 数据源，只要物理表结构与配置一致即可。
- 环境变量 `EDU_SCHEMA_CONFIG_PATH` 可覆盖配置文件路径。
- `resolve_score_schema` 工具优先读取该配置，再回退启发式推断。

### 主要字段

| 配置块 | 说明 |
|--------|------|
| `tables` | 逻辑角色 → 物理表名（如 `score` → `tb_score`） |
| `joins` | 标准 JOIN 片段，供 Agent / Orchestrator 参考 |
| `fields` | 逻辑字段 → SQL 表达式（含表别名） |
| `fields.full_score` | 卷面满分来源列（`sc.exam_score`），**非固定数值** |
| `defaults.pass_ratio` / `excellent_ratio` | 及格/优秀比例（0.6 / 0.85） |

## 动态满分机制

每套卷子的满分由数据库 **`tb_exam.exam_score`**（`tb_score.exam_score` 冗余同值）记录。

- 查 KPI 时 SQL 须 `SELECT ... exam_score ...`
- 统计工具 `compute_score_stats_tool` 从结果列读取满分，计算：
  - 及格线 = `exam_score × 0.6`
  - 优秀线 = `exam_score × 0.85`
- 无 `exam_score` 列时回退 `default_full_score=100` 并 warning

## Legacy / Agent 模式增强

教育类问题（含学情、成绩、班级、科目等关键词）在 legacy 问数路径自动注入：

- SQL few-shot（`tb_*` 表）
- 术语块（学校/班级/学号/小题/满分比例）

## 科目诊断报告（subject_diagnosis）

含 **小题明细**（带知识点列）与 **知识点掌握诊断**：

- `KNOWLEDGE_TABLE`：各知识点汇总得分率
- `WEAK_KNOWLEDGE_LIST`：得分率 &lt; 60% 的薄弱知识点
- `SUMMARY` / `RECOMMENDATIONS`：自动生成需加强的知识点与教学建议

Team 模式 Planner 会拆 3 步查数 + 1 步 ToolExpert 组装；ToolExpert 应调用
`build_subject_diagnosis_sections_tool` 生成上述字段。

## 结构化诊断报告（diagnostic_report）

三节结构：一般性（区域/年级趋势）→ 特殊性（班级/分数段差异）→ 动态性（进退步）。

- 触发关键词：「结构化诊断」「区域诊断报告」
- 工具：`build_diagnostic_report_data_tool`
- API：`POST /api/v1/education/diagnostic-report`

## 多维分析

- 维度：`citywide` / `district` / `school` / `grade`（从 `class` 解析）/ `class` / `subject` 等
- 工具：`aggregate_dimension_tool`、`cross_analyze_tool`
- 维度列表：`GET /api/v1/education/dimensions`

## 外部库 DDL

见 [`docs/education_schema_ddl.sql`](education_schema_ddl.sql)：`tb_exam_batch`、`tb_exam.exam_batch_id`、`tb_fraction_bar`、`tb_score_indicator`、`tb_score_overview`、`tb_school.district`、`tb_exam_question.question_type`、`tb_knowledge.ability_level`、`tb_exam_question_knowledge`。

### 考试批次与试卷

- **批次** `tb_exam_batch`：用户口中的「考试」（如 `2026届高三5月模拟`）对应 `batch_name`；`exam_time` 为考试时间，达线报告上场/环比按本列取上一场。
- **试卷** `tb_exam`：一批次下多科试卷；`exam_batch_id` 关联批次，`exam_score` 为该科卷面满分。
- 问数过滤「XX考试」须 `JOIN tb_exam e ... LEFT JOIN tb_exam_batch eb ON e.exam_batch_id = eb.id`，按 `COALESCE(eb.batch_name, e.exam_name)` 过滤；禁止把单科试卷当成考试批次。

### 预测线 / 达线 / 总览

| 表 | 粒度 | 用途 |
|----|------|------|
| `tb_fraction_bar` | 一场批次一行 | 预测分数线阈值（`wl_score_*` 物理类、`ls_score_*` 历史类；物理美术列为 `wl_socre_ms`） |
| `tb_score_indicator` | 批次×选科×学校×线种 | 达线人数 `reached_count`、参考人数 `candidates`、学校达线率 `reach_rate`。区县/全市须 `SUM` 后重算率，禁止 `AVG(reach_rate)` |
| `tb_score_overview` | 一学生一场批次 | 全科总分 `zf6m`、选科 `xkkm`、学校 `xx`、区县 `dq`、班级 `bj`；科目/转换/等级 `yw`…`dldj`；应届 `xsxz`、校层 `xxlb`。学生标识只用 `anon_stu_id`，禁止 `xm`/`sfzh`/`ksh` |

三表的 `exam_name` 均与 `tb_exam_batch.batch_name` 对齐；`tb_fraction_bar` / `tb_score_indicator` / `tb_score_overview` 的 `exam_batch_id` 关联批次。

### 局端基础分析（扬州模考口径）

离线「基础分析表」由 `tb_score_overview` 学生行重算，不导入汇总 Excel。

| 口径 | 规则 |
|------|------|
| 特招线 | 等同特控线 |
| 应届 | `xsxz = 在籍生`（排除市报生） |
| 三/四/六门均分 | `AVG(zf3m/zf4m/zf6m)`，禁止再除以 3；报告默认分全员/理科（物理类）/文科（历史类） |
| ABCDE | 聚合 `hxdj/swdj/zzdj/dldj` |
| 位次前 N | 含并列（`zf6m ≥` 第 N 名分数） |
| 贡献分 | 达线且总分等于切线分的学生各科均值 |
| 尖子班 | 只认 `tb_elite_class`，不猜班级号 |
| 高分名单 | 只出 `anon_stu_id`，禁止 xm/ksh/sfzh |

报告类型：`subject_avg` / `assign_grade` / `rank_bucket` / `contribution` / `combo_reach` / `elite_roster`；达线报告 `line_reach` 含学校明细表。

### 题目 ↔ 知识点多对多

- 关联表：`tb_exam_question_knowledge(question_id, knowledge_id, weight)`。
- 诊断 SQL **只读**关联表（不再经 `tb_exam_question.knowledge_id`）；`knowledge_id` 列保留兼容、不删除。
- **权重拆分**：题内 `w_norm = weight / SUM(weight)`；知识点得分贡献 = 题得分 × `w_norm`，满分同理。
- **发版前须回填**：将旧 `eq.knowledge_id` 写入关联表（`weight=1`）；未回填时诊断会大量显示「未关联知识点」。
- 小题展示：一题一行，知识点名为排序后的聚合串（如 `函数、导数`）；掌握度按知识点展开计分。

## 成绩导入

支持 Excel 模板批量写入外部数据源成绩表：

| 类型 | 文件 | 目标表 | Sheet | 必填列 |
|------|------|--------|-------|--------|
| 仅总分 | `脱敏成绩_仅总分.xlsx` | `tb_score` | 成绩录入 | 学校编号、试卷编号、试卷名称、学号、班级、总分 |
| 小题分明细 | `脱敏成绩_小题分明细.xlsx` | `tb_score_detail` | 小题分明细 | 学校编号、试卷编号、试卷名称、学号、题目编号、题号、题目满分、得分、班级 |

旧版四列/五列模板（仅试卷名称、学号、班级、总分/题号）仍兼容。

### API

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/v1/education/score-import/templates/{total\|detail}` | 下载官方模板 |
| `POST` | `/api/v1/education/score-import/preview` | 上传校验（multipart：`file`, `datasource_id`, `import_type`, 可选 `school_id`） |
| `POST` | `/api/v1/education/score-import/execute` | 校验通过后 UPSERT 写入 |

### 权限与规则

- 须登录且对数据源有访问权限；`student` 角色禁止导入。
- `teacher` 仅可导入授权班级；`school_admin` 限本校 `school_id`。
- 学号须存在于 `tb_student`；学校编号须存在于 `tb_school.id`；试卷编号须存在于 `tb_exam.id`，且与试卷名称一致。
- 小题模板中题目编号须存在于 `tb_exam_question.id`，并与题号、试卷编号一致；得分不得超过题目满分（Excel 或库中值）。
- 同一「试卷 + 学号」（总分）或「试卷 + 学号 + 题号」（小题）重复导入时 **覆盖更新**。
- 学号不存在时，执行导入会自动在 `tb_student` 新增（写入 `id`，若有列则附带 `class` / `school_id`），再写入成绩表。
- 仅总分与小题分明细分开导入：总分走 `脱敏成绩_仅总分.xlsx` → **仅写** `tb_score`，小题走 `脱敏成绩_小题分明细.xlsx` → **仅写** `tb_score_detail`。
- 导入过程中对 `tb_exam` / `tb_school` / `tb_exam_question` **只读校验**；`tb_student` 仅在学号缺失时自动新增。
- 目标表须具备对应唯一约束（`tb_score`: `exam_id, student_id`；`tb_score_detail`: `exam_id, student_id, question_no`）。

### 前端入口

**构造台 → 成绩导入**（`/construct/education/score-import`）

## 示例问法

1. 南京市第一中学高一(1)班数学平均分
2. 对比三所学校数学均分排名
3. 南京市各区县数学均分对比
4. 分析南京市第一中学数学，细化到每一小题
5. STU20240001 数学考了多少分
6. 生成南京市第一中学高一(1)班数学学情报告
7. 南京市第一中学数学结构化诊断报告
