# 教育模块试卷题目重做设计

日期：2026-08-19  
主题：将 `2026届高三1月期末` 9 科 mock 题目替换为真实试卷题目，并按小题分粒度拆分记录

---

## 1. 背景与目标

### 1.1 背景

数据库 `edu`（PostgreSQL）中，考试批次 `2026届高三1月期末` 的 9 门科目（语数英物化政史地生）题目数据当前为 mock 生成：

- `tb_exam_question.content` 全部填 `'暂无'`
- 大题 `15` 下的子题 `15_1`、`15_2` 被聚合为单行 `question_no=15`
- `tb_score_detail` 按大题记录，不拆子题

现在已有真实试卷 PDF 和对应小题分文件，需要重做题目数据。

### 1.2 目标

1. 按小题分文件表头粒度拆分题目：大题保留、子题独立成行
2. 从真实试卷 PDF 提取题干写入 `tb_exam_question.content`
3. 同步更新 `tb_score_detail` 为子题粒度
4. 将解析/拆分逻辑固化到代码，使后续 11月/3月/5月 等批次可复用
5. 不新增独立脚本，改造现有 `scripts/import_exam_scores.py`

---

## 2. 数据位置

```
temp/教科院/考试/2026届-高三/2026届高三1月期末/
├── 成绩宽表.xlsx          # 学生总分宽表
├── 小题分/                # 9 科 .xls 小题分
│   └── 小题分(科目).xls
└── 试卷/                  # 9 科真实试卷 PDF
    └── 科目.pdf
```

---

## 3. 数据拆分规则

### 3.1 `tb_exam_question` 记录规则

| xls 表头示例 | `question_no` | `question_score` | `content` |
|---|---|---|---|
| `单选1（5.0分）` | `单选1` | 5.0 | PDF 第 1 题完整题干 |
| `多选9（6.0分）` | `多选9` | 6.0 | PDF 第 9 题完整题干 |
| `12（5.0分）` | `12` | 5.0 | PDF 第 12 题完整题干 |
| `15（13.0分）` | `15` | 13.0 | PDF 第 15 题完整题干 |
| `15_1（6.0分）` | `15_1` | 6.0 | PDF 第 15 题完整题干 + `(1)` 小问内容 |
| `15_2（7.0分）` | `15_2` | 7.0 | PDF 第 15 题完整题干 + `(2)` 小问内容 |

### 3.2 关键约定

- `question_no` 为字符串，直接沿用 xls 表头列名，**不转换下划线**
- 大题保留，子题保留
- 子题 content = 完整题干 + 对应小问文本，保留题号前缀（如 `15.`）
- 选择题/填空题无子题，content 为完整题干

### 3.3 旧数据处理

对于 `2026届高三1月期末` 这 9 科：

1. 通过 `tb_exam_batch` → `tb_exam.exam_batch_id` 定位 9 科 `exam_id`
2. `DELETE FROM tb_exam_question WHERE exam_id IN (...)`
3. `DELETE FROM tb_score_detail WHERE exam_id IN (...)`
4. 重新写入新题目 + 新小题分明细

`tb_score` 学生科目总分行走 `ON CONFLICT ... DO UPDATE` 覆盖，不删除。

---

## 4. 架构与数据流

### 4.1 新增模块

| 模块 | 路径 | 职责 |
|---|---|---|
| 题目解析器 | `src/agent/education/question_parser.py` | xls 表头 → 题目列表（question_no、score、col_idx、父/子关系） |
| 试卷解析器 | `src/agent/education/paper_parser.py` | PDF 文本提取 → 按题号索引题干/小问 |

### 4.2 改造入口

- `scripts/import_exam_scores.py`
  - 从 `question_parser.py` / `paper_parser.py` import
  - 新增 `--paper-dir` 参数：传入则解析 PDF 填 content，不传则 content 填 `'暂无'`（向后兼容）
  - `build_questions()` 改为调用 `parse_questions_from_headers()`
  - 删除旧版聚合逻辑

### 4.3 数据流

```
小题分 xls ──┐
             ├──► question_parser ──► 题目定义列表
PDF 目录  ───┘                            │
                                          ▼
                         tb_exam_question (DELETE + INSERT)
                                          │
成绩宽表 xls ─────────────────────────────┤
                                          ▼
                         tb_score_detail (DELETE + INSERT, 子题粒度)
```

---

## 5. PDF 解析策略

### 5.1 工具

- 使用 `pymupdf`（`fitz`）读取 PDF 文本层
- 真实试卷 PDF 已有文本层，无需 OCR

### 5.2 题号匹配

PDF 中题号格式：

```
1．设集合 ...
2．已知复数 ...
...
15．（13分）已知函数 ...
（1）求 ...
（2）若 ...
（3）证明 ...
```

解析步骤：

1. 提取全部文本，按页拼接
2. 正则匹配题号边界：`r"(?m)^\s*(\d+)\.\s*”`
3. 每道题保存：`{题号: {full: 完整题干, sub_questions: {子号: 子问文本}}}`
4. 选择题/多选题按 xls 中的 `单选N` / `多选N` 做偏移映射
   - `单选N` → PDF 题号 `N`
   - `多选N` → PDF 题号 `N`（因为 PDF 中多选题本身就是连续题号 9/10/11）
5. 子题识别：在大题文本块内，按 `（1）/（2）/（3）` 或 `(1)/(2)/(3)` 拆分

### 5.3 content 拼接规则

| 题目类型 | content 内容 |
|---|---|
| 单选/多选/填空 | PDF 中对应题号完整文本 |
| 大题 `15` | PDF 中题号 `15` 完整文本 |
| 子题 `15_1` | PDF 中题号 `15` 完整文本 + `（1）...` 小问文本 |
| 子题 `15_2` | PDF 中题号 `15` 完整文本 + `（2）...` 小问文本 |

---

## 6. 异常与边界处理

| 场景 | 行为 |
|---|---|
| 某科 PDF 不存在 | `[警告] 未找到 科目.pdf，content 填 '暂无'`，继续 |
| PDF 中找不到对应题号 | `[警告] 科目 题号 N 未在 PDF 中命中，content 填 '暂无'`，继续 |
| 子题号在 PDF 中找不到小问 | `[警告] 科目 15_1 未命中小问，content 仅放完整题干`，继续 |
| xls 表头解析不出题目列 | `[警告] 科目 无有效题目列，跳过`，继续 |
| 题目分值总和 ≠ 满分 | `[警告] 科目 总分 N ≠ 满分 M，仍按文件写入`，继续 |
| 大题列不存在、只有子题列（如 15_1/15_2 无 15） | 子题仍写入，content 仅放小问文本（无完整题干可拼） |
| 子题列不存在、只有大题列（如只有 15） | 大题写入，无子题 |
| `--paper-dir` 未传 | 不解析 PDF，所有 content 填 `'暂无'`（与旧行为一致） |
| 数据库连接失败 | 报错退出，事务回滚 |
| `--dry-run` | 打印预览统计，不执行 DELETE/INSERT |

---

## 7. 测试

### 7.1 `tests/agent/education/test_question_parser.py`

覆盖 xls 表头解析：

- `test_header_with_choice`：单选/多选正常识别
- `test_header_with_main_and_sub`：大题 `15` + 子题 `15_1/15_2` 都保留
- `test_header_no_sub`：只有大题 `15` 也能生成
- `test_answer_column_skipped`：`单选1_答案` 列跳过
- `test_score_sum`：大题 13 = 6 + 7

### 7.2 `tests/agent/education/test_paper_parser.py`

覆盖 PDF 解析：

- `test_parse_single_question`：单题文本提取
- `test_parse_question_with_sub_questions`：大题含 (1)(2)(3) 拆分
- `test_build_content_for_main`：大题 content = 完整题干
- `test_build_content_for_sub`：子题 content = 完整题干 + 小问
- `test_missing_question_fallback`：题号未命中时 fallback 到 `'暂无'`

使用构造的文本 fixture，不依赖真实 PDF。

---

## 8. 命令行示例

### 8.1 本次执行：2026届高三1月期末

```bash
uv run python scripts/import_exam_scores.py \
  --score-file "temp/教科院/考试/2026届-高三/2026届高三1月期末/成绩宽表.xlsx" \
  --detail-dir "temp/教科院/考试/2026届-高三/2026届高三1月期末/小题分" \
  --paper-dir "temp/教科院/考试/2026届-高三/2026届高三1月期末/试卷" \
  --exam-name "2026届高三1月期末" \
  --exam-time 2026-01-23 \
  --database-url "postgresql://root:123456@36.213.182.180:5435/edu"
```

### 8.2 预览模式

```bash
uv run python scripts/import_exam_scores.py \
  --score-file "..." --detail-dir "..." --paper-dir "..." \
  --exam-name "2026届高三1月期末" --exam-time 2026-01-23 \
  --database-url "..." \
  --dry-run
```

### 8.3 后续批次：不传 PDF（保持旧行为）

```bash
uv run python scripts/import_exam_scores.py \
  --score-file "..." --detail-dir "..." \
  --exam-name "2026届高三3月模拟" --exam-time 2026-03-15 \
  --database-url "..."
```

---

## 9. 已完成的 DDL

用户已手动执行，并已验证生效：

```sql
ALTER TABLE tb_exam_question
    ALTER COLUMN question_no TYPE VARCHAR(16) USING question_no::VARCHAR(16);

COMMENT ON COLUMN tb_exam_question.question_no
    IS '题号字符串，如 24-1/单选1/语法填空';
```

验证结果：

- `question_no` 当前类型为 `character varying(16)`
- 现有 `'1'`, `'2'`, `'3'` ... 等字符串值正常
- 新COMMENT已写入

---

## 10. 风险与注意事项

1. **PDF 版式差异**：9 科 PDF 版式可能略有不同，解析正则需要在数学卷上验证后再跑 9 科。
2. **题号偏移**：不同科目选择题数量不同，但 PDF 中题号是连续的，xls 中 `单选N`/`多选N` 也是按顺序对应的，映射关系应天然成立。
3. **tb_score_detail 删除重建**：删除后重新插入，数据行数会变多（每个子题一行），但学生总分 `tb_score` 不受影响。
4. **content 长度**：完整题干 + 小问可能较长，`content` 列类型为 `text` 无长度限制，安全。
5. **可重入性**：可重复执行，每次都会 DELETE + INSERT 同一批 exam_id 的题目和明细。

---

## 11. 实现范围

- [x] 明确拆分规则（大题保留、子题独立、content 拼接）
- [x] 明确数据位置与 CLI 参数
- [x] 明确 PDF 解析策略
- [x] 明确测试范围
- [x] DDL 已手动执行并验证
- [ ] 实现 `src/agent/education/question_parser.py`
- [ ] 实现 `src/agent/education/paper_parser.py`
- [ ] 改造 `scripts/import_exam_scores.py`
- [ ] 编写测试
- [ ] 跑 `--dry-run` 预览
- [ ] 真实执行并校验 9 科题目
