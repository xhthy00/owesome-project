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

见 [`docs/education_schema_ddl.sql`](education_schema_ddl.sql)：`tb_school.district`、`tb_exam_question.question_type`、`tb_knowledge.ability_level`、`tb_exam_question_knowledge`。

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
