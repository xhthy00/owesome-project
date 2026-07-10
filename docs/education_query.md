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

见 [`docs/education_schema_ddl.sql`](education_schema_ddl.sql)：`tb_school.district`、`tb_exam_question.question_type`、`tb_knowledge.ability_level`。

## 示例问法

1. 南京市第一中学高一(1)班数学平均分
2. 对比三所学校数学均分排名
3. 南京市各区县数学均分对比
4. 分析南京市第一中学数学，细化到每一小题
5. STU20240001 数学考了多少分
6. 生成南京市第一中学高一(1)班数学学情报告
7. 南京市第一中学数学结构化诊断报告
