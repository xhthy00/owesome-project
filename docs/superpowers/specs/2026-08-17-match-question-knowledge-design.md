# 题目与知识点匹配设计文档

## 背景与目标

教育模块已存在：

- `tb_exam_question`：考试题目表，包含题干、科目、题型等。
- `tb_knowledge`：知识点表，包含学段、年级、科目、模块、章节、知识点名称等。
- `tb_exam_question_knowledge`：题目与知识点关联表，字段为 `(question_id, knowledge_id, weight)`，主键 `(question_id, knowledge_id)`。

当前大量题目未与知识点关联。本设计的目标是根据**科目 + 题干内容**做语义判断，为每道题匹配最相关的知识点，并写入 `tb_exam_question_knowledge`。暂时一个问题只关联一个知识点；若实在无法匹配，则该题目暂不关联。

## 方案选择

| 方案 | 说明 | 优点 | 缺点 |
|------|------|------|------|
| A：复用离线端 LLM 匹配 | 在 `edu-offline-app` 的 `matchService.ts` 上扩展批量任务 | 复用已有提示词与校验逻辑 | 需改动 Electron 离线端，跨项目 |
| B：Web 后端一次性脚本（推荐） | 在 `awesome-data/scripts/` 下新增 `match_question_knowledge.py` | 直接在本仓库完成，风格与现有 import 脚本一致 | 需新建提示词，大量题目调用 LLM 需限速 |
| C：Web 后端 API + 异步任务 | 新增 `POST /api/v1/education/match-question-knowledge` | 可复用、可触发、可查看进度 | 当前更像一次性数据初始化，实现较重 |

采用**方案 B**。

## 总体流程

```text
读取 .env 配置连接数据库
    │
    ▼
查询未关联知识点的题目（可按 subject / exam_id 过滤）
    │
    ▼
按 subject 分组，加载 tb_knowledge 中对应科目的知识点列表
    │
    ▼
对每道题构造 prompt：题干 + 题型 + 候选知识点列表
    │
    ▼
调用 LLM，要求其返回最匹配的知识点名称，或声明无法匹配
    │
    ▼
校验返回的知识点名称是否确实在候选列表中
    │
    ▼
插入 tb_exam_question_knowledge(question_id, knowledge_id, 1)
    │
    ▼
输出运行报告：已匹配数、未匹配数、失败数
```

## 数据查询

### 待匹配题目

```sql
SELECT q.id, q.exam_id, q.question_no, q.subject, q.question_type, q.content
FROM tb_exam_question q
LEFT JOIN tb_exam_question_knowledge qk ON q.id = qk.question_id
WHERE qk.question_id IS NULL
  AND q.content IS NOT NULL AND TRIM(q.content) <> ''
  -- 可选过滤
  AND (:subject IS NULL OR q.subject = :subject)
  AND (:exam_id IS NULL OR q.exam_id = :exam_id);
```

### 候选知识点

```sql
SELECT id, stage, grade, subject, module, chapter, name, content
FROM tb_knowledge
WHERE subject = :subject;
```

## LLM Prompt 设计

### System Prompt

```text
你是一位资深学科教研员。请根据以下信息，判断该题目最对应的知识点：

- 科目：{subject}
- 题型：{question_type}
- 题干：{content}

候选知识点列表（每行一个）：
{knowledge_list}

要求：
1. 只从候选列表中选择一个最相关的知识点。
2. 若题干过于模糊、信息不足或与任何候选知识点均不相关，请输出 unmatched。
3. 输出必须是 JSON 格式：{"knowledge_name": "知识点名称"} 或 {"unmatched": true}
```

### 输出示例

```json
{"knowledge_name": "一元二次方程根的判别式"}
```

或

```json
{"unmatched": true}
```

## 结果校验

1. 若 LLM 输出 JSON 解析失败，记录为 `failed`。
2. 若 `knowledge_name` 不在候选列表中，记录为 `failed`。
3. 若返回 `unmatched: true`，记录为 `unmatched`。
4. 插入时若主键冲突，记录为 `failed` 并继续。

## 写入规则

- `weight` 固定为 `1`。
- 一个题目只关联一个知识点。
- 幂等性：重复运行只处理尚未关联的题目，不会重复插入。

## 并发与限速

- 默认单线程逐题调用，避免触发 LLM 限流。
- 提供 `--sleep-ms` 参数控制每题调用间隔（默认 200 ms）。
- 提供 `--limit` 参数限制本次处理的最大题目数，便于小批量验证。

## 命令行接口

```bash
uv run python scripts/match_question_knowledge.py
uv run python scripts/match_question_knowledge.py --subject 数学
uv run python scripts/match_question_knowledge.py --exam-id 42 --limit 10
uv run python scripts/match_question_knowledge.py --sleep-ms 500
```

## 输出示例

```text
2026-08-17 10:00:00 INFO: 待匹配题目: 153
2026-08-17 10:00:05 INFO: 已匹配: 128
2026-08-17 10:00:05 INFO: 无法匹配: 18
2026-08-17 10:00:05 INFO: 失败: 7
```

## 失败处理与日志

- 所有无法匹配和失败的题目记录到 `match_question_knowledge_YYYYMMDD_HHMMSS.log`。
- 日志包含 `question_id`、`exam_id`、`question_no`、`subject`、`content`、`reason`。
- 失败题目不会阻塞后续题目处理。

## 依赖

- 复用项目现有的：
  - `common.core.config.get_settings()` 读取配置。
  - `common.core.database.get_db_session()` 获取数据库会话。
  - `src.llm.service` 调用 LLM。
- 脚本自身处理命令行参数和日志。

## 测试与验证

1. 小批量验证：先用 `--limit 10` 跑一个科目的少量题目，人工抽查匹配结果。
2. 日志检查：确认失败/未匹配的题目分类合理。
3. 数据库校验：
   ```sql
   SELECT subject,
          COUNT(*) AS total_questions,
          COUNT(qk.question_id) AS matched,
          COUNT(*) - COUNT(qk.question_id) AS unmatched
   FROM tb_exam_question q
   LEFT JOIN tb_exam_question_knowledge qk ON q.id = qk.question_id
   GROUP BY subject;
   ```

## 后续可扩展

- 包装为 FastAPI 后台任务端点，支持按考试触发和进度查询。
- 引入 embedding 预筛选候选知识点，减少 LLM prompt 长度和调用成本。
- 支持一个题目关联多个知识点（表结构已支持，只需改脚本逻辑）。
