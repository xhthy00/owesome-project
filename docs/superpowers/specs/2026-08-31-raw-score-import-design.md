# 教育模块原始成绩导入设计（成绩宽表 + 各科小题分）

日期：2026-08-31  
修订：2026-08-31（需求评审后）  
主题：Web 端**新增**教科院原始成绩材料导入——成绩宽表 + 各科小题分两类 Excel。离线端脱敏导入整页整接口原样保留，本次只加新页面和新接口。

---

## 1. 背景与目标

### 1.1 背景

教科院提供的原始成绩材料分两类：

1. **成绩宽表**（`成绩宽表.xlsx`，38 列）：一行一名学生，含 KSH（考号）/XX（学校码）/XM/SFZH（身份证号）、9 科单科分、3 个总分（ZF3M/ZF4M/ZF6M）、转换分等级等。样例：`temp/教科院/考试/2026届-高三/2026届高三1月期末/成绩宽表.xlsx`（17,700 行）。
2. **各科小题分**（`小题分/小题分(科目).xls`，9 科）：一行一名学生 × 一列一道题，3 行复合表头（标题行/信息行/得分标签行）。样例：同目录 `小题分/*.xls`（最大英语 34MB / 121 列）。

现有 Web 端「成绩导入」（`score-import.tsx` + `score_import.py`）面向的是**离线端导出的脱敏模板**，必须保留。教科院原始材料目前只能靠手工脚本入库。

学生考试成绩固定落在教育业务库 `edu`（现网 `36.213.182.180:5435/edu`），不会写入其它数据源。新导入页**不选手动数据源**。

### 1.2 目标

一次考试（一个批次）导入流程：

```
选/建批次
  → 预检：该批次必须已有 9 科试卷（语数英物化生史政地），不齐不能上传宽表
  → 导入成绩宽表（有错误行则整文件不写）
  → 写 tb_score_overview / tb_student / tb_score
  → 扫异常（失败不阻断；不重算达线）
  → 导入各科小题分（须宽表已在库中或本次刚导入成功；有错误行则该科整文件不写）
  → 写 tb_score_detail
```

- 宽表落 `tb_score_overview` + 同步 `tb_student` + 写 `tb_score`，导完即触发异常提醒扫描（**不**重算达线指标）
- 小题分落 `tb_score_detail`
- 旧脱敏模板页与接口一律不动

### 1.3 已确认口径

| # | 决策点 | 口径 |
|---|--------|------|
| 1 | 落地范围 | 全套：宽表 → `tb_score_overview`；单科分 → `tb_score`；小题分 → `tb_score_detail` |
| 2 | 维度数据 | 批次/学校/试卷/题目**必须已存在，导入只匹配不创建**；匹配不到进错误清单 |
| 3 | 批次 | 预建，导入页下拉选择；页内可新建（重名→自动选中已有批次）。空批次会被 9 科预检拦住 |
| 4 | 班级 | 取 KSH 第 4-5 位、去前导 0（`501101360479` → `10`），**不要求必须为数字**；年级前缀取自批次名 → `高三(10)班` |
| 5 | 匿名码 | 后端自动算：`{schoolToken}_{SHA256(token:sfzh)[:8].upper()}`，`schoolToken` 形如 `GZ_F57E7326` |
| 6 | 角色 | 仅 `bureau_admin` + `school_admin`；`teacher`/`student` 拒绝（新入口单独校验，不复用会放行教师的旧函数） |
| 7 | 市报生 | 学校归属按宽表 XX 列（市报虚拟校） |
| 8 | 校名匹配 | XX → `tb_school.s_name` **精确匹配** |
| 9 | 试卷匹配 | 小题分**逐文件手选试卷**；后端校验批次一致 |
| 10 | 题目对齐 | 列标签去分值括号 = `tb_exam_question.question_no` **精确匹配** |
| 11 | 总分口径 | `tb_score.score` 以**宽表科目分**为准；不核对小题分全卷列 |
| 12 | 跨表一致 | 小题分学生**只**按 SFZH 查已导入宽表；查不到 → 错误行。**取消学校列回退** |
| 13 | tb_student | 宽表导入时同步写 |
| 14 | 异常扫描 | **宽表导完即扫**（按本文件涉及学校 × 该批次 9 科）；小题分导入后不扫；**不重算达线** |
| 15 | 重复导入 | 全程 UPSERT 覆盖；失败后整文件重导即可 |
| 16 | 旧入口 | **原样保留**：旧页、旧接口、旧菜单「成绩导入」均不改 |
| 17 | 新入口 | 全新页面 + 全新接口组（见 §4、§5） |
| 18 | 数据源 | 新页**不选手动数据源**；服务端固定写入 `database=edu` 的已登记数据源，**无环境配置** |
| 19 | 校管理员 | 文件中出现任意一行非本校 → **整文件拒绝**，须先筛成本校 Excel |
| 20 | 试卷预检 | 强制 9 科试卷齐全，缺一科整文件拒绝；同科目多卷拒绝 |

---

## 2. 入口隔离

| | 旧（保留，本次不改） | 新（本次只碰这些） |
|--|--|--|
| 页面 | `/construct/education/score-import` | `/construct/education/raw-score-import` |
| 菜单 | 「成绩导入」`score-import` | 「原始成绩导入」`raw-score-import` |
| 接口 | `/api/v1/education/score-import/*` | `/api/v1/education/raw-score-import/*` |
| 材料 | 离线端脱敏模板 | 成绩宽表.xlsx + 小题分(科目).xls |
| 数据源 | 用户选择 | 服务端固定 edu 库 |

后端内部可复用 `score_import.py` 的批量 UPSERT、学校匹配工具函数，**不得改变旧接口行为、不得改旧页面**。

---

## 3. 整体架构与数据流

**方案：新解析层 + 复用写入基础设施。** 新建 `src/agent/education/raw_import.py`（解析 + 校验 + 写入编排），新端点组挂在 `education_router` 下 `/raw-score-import/*`；复用 `score_import.py` 已有的批量 UPSERT（500 行/块、≥800 行 4 线程并行、PG `ON CONFLICT DO UPDATE`）。前端**新建** `raw-score-import.tsx`，不改 `score-import.tsx`。

教育业务库写死为已登记数据源中 `database=edu` 的那一条（当前工作空间内匹配）。新接口不接收 `datasource_id`，也不读环境变量。解析时 `assert_datasource_accessible`；用户无该数据源权限则拒绝导入。连接串与密码只存在数据源配置中，不进前端、不进本设计文档。

### 3.1 宽表导入（一次调用完成）

```
1. 解析 38 列 → 取成绩相关列（KSH/XX/SFZH/XM/各科分/ZKCJ/ZF3M/ZF4M/ZF6M/各科转换分等级/XKKM/XSXZ/XXLB/DQ）
   （跳过 XH/QH/RY/RYKG/RYZW 等非成绩列不作为必填；能映射到 overview 已有列的仍可写入）
2. XX 按 tb_school.s_name 精确匹配 → 学校 token
3. 班级 = KSH 第 4-5 位去前导 0 → '高三(N)班'（年级前缀取自批次名）
4. 匿名码 = schoolToken + '_' + SHA256(token:sfzh)[:8].upper()
5. UPSERT tb_score_overview（唯一键 ksh+exam_name；exam_name=所选批次名；同时写 exam_batch_id）
6. UPSERT tb_student（id=匿名码, school_id, 届次, class）
7. 按批次+科目匹配 tb_exam → UPSERT tb_score（每学生×参考科目一行，唯一键 exam_id+student_id，score=宽表科目分）
8. 触发异常提醒扫描 scan_alerts_after_import（按本文件涉及学校 × 该批次全部试卷；不重算达线）
```

**必须写入 overview 的敏感列：** `ksh` / `sfzh` / `xm` / `xx`。唯一键和小题分按 `sfzh` 反查都依赖它们。表注释「禁止使用 xm/sfzh/ksh」指问数查询，不是禁止落库。

### 3.2 小题分导入（每文件一次调用）

```
1. 解析 3 行复合表头 → 学号列 + 得分列
   （跳过：列名含「答案」——学生作答选项；列名含「全卷」「1卷」「2卷」等合计列）
2. 学生身份只按 SFZH 从同批次 tb_score_overview 反查（anon_stu_id/班级/学校 token）
   —— 查不到 → 错误行。无学校列回退
3. 列标签去分值括号 → 精确匹配 tb_exam_question.question_no（试卷=用户手选）
4. UPSERT tb_score_detail（唯一键 exam_id+student_id+question_no）
```

### 3.3 关键约束

- 所有维度（批次/学校/试卷/题目）只匹配不创建；匹配不到 → 错误清单
- 重复导入全程 UPSERT 覆盖（重导即修复）
- **`school_cipher` 豁免**：本模块必须读 `tb_school.s_name` 做匹配，但明文不落日志
- 敏感列 XM/SFZH/KSH **写入 overview**，但**不出现在响应、日志、preview_sample**；预览只给匿名码、班级、学校名（`xx`）、总分等
- 三表非跨表事务：任一块失败已写块保留；产品口径为「失败后整文件重导（UPSERT）」

---

## 4. 校验与错误处理

预览时全量执行校验链；执行阶段**重新跑一遍完整校验**（防预览后文件内容不一致）。**统一规则：只要有错误行（含整文件级 row=0），该次执行一条不写。** 预览不写库。

### 4.1 校验分层

**整文件拒绝（row=0，无有效数据行）：**

- 缺必需列、不是 Excel、批次不存在
- 该批次 9 科试卷不齐（语文、数学、英语、物理、化学、生物、历史、政治、地理；日语不在清单里）
- 同批次同一科目有多张卷
- 小题分题号列与试卷 `question_no` 对不齐
- 所选试卷不属于该批次
- 校管理员：文件中出现任意一行非本校（列出他校数量与校名样例，例如「文件包含非本校数据（A01扬州中学 等 37 所），请先筛选为本校后再导入」）

**错误行 → 同样导致整文件不写：**

- 学校名匹配失败（局端）
- SFZH 或 KSH 为空，或**文件内 SFZH 重复、文件内 KSH 重复**
- 考号过短无法解析班级
- 科目分 / 小题得分：非数值、为负、超满分
- 小题分学生不在本批次宽表

**不算错误：**

- 宽表某科空单元格 = 未选考，不写该科 `tb_score`
- 小题分科目名与所选卷不一致 → **警告可继续**
- 不校验身份证 18 位（教科院样例已是短号）
- Excel 把 KSH/SFZH 读成 `xxxx.0` 时规范化后再比

**超满分是错误，不是警告。** warning 只留给：科目名与卷名不一致、扫描失败等不阻断写入的情况。

### 4.2 宽表导入校验链

| # | 校验项 | 失败处理 |
|---|--------|---------|
| 1 | 文件结构：sheet 名任意；表头必须含必需列（KSH/XX/XM/SFZH + 至少 ZF6M），列名大小写不敏感 | 整文件拒绝 |
| 2 | 学校匹配：XX → `tb_school.s_name` 精确匹配 | 错误行「学校『X』不存在」 |
| 3 | 学号/考号唯一性：同文件内 SFZH 重复或 KSH 重复 | 错误行（保留首条，重复报错） |
| 4 | 匿名码生成：SFZH/KSH 非空 | 错误行（不做 18 位格式校验） |
| 5 | 班级解析：KSH 第 4-5 位直接取、去前导 0，**不校验必须为数字** | KSH 过短取不足 → 错误行 |
| 6 | 数值列：科目分——空单元格视为**未选考，跳过该科**；非数值 / 为负 / 超该科满分（对照 `tb_exam.exam_score`）→ 阻断 | 错误行 |
| 7 | 科目→试卷：9 科中**任一科**在所选批次下无卷，或同科多卷 → **阻断整个导入** | 提示「批次『X』缺少『物理』试卷，请先补齐试卷维度」 |
| 8 | 权限：`bureau_admin` 全量放行；`school_admin` 出现任意非本校园 → **整文件拒绝** | 见 §4.1 |

### 4.3 小题分导入校验链

| # | 校验项 | 失败处理 |
|---|--------|---------|
| 1 | 文件结构：前 3 行复合表头（标题行「小题分(科目)」→ 自动识别科目并预填试卷下拉）；第 2 行含「学号/考号/姓名/学校」；第 3 行含得分列标签 | 整文件拒绝 |
| 2 | 试卷匹配：用户手选试卷；后端校验该卷 `exam_batch_id` 与所选批次一致（不一致**拒绝**）；校验科目与文件标题科目一致（不一致**警告**可继续） | 拒绝/警告 |
| 3 | 题目对齐：每个得分列标签去分值括号后必须命中 `tb_exam_question.question_no`；未命中的列**整列报错**（列出列名清单）。解析时显式丢弃答案列与全卷/1卷/2卷合计列 | 整文件拒绝 |
| 4 | 学生反查：「学号」列（即 SFZH）按 `overview.sfzh` 反查同批次记录 → 取 `anon_stu_id`/班级/学校 token；**查不到即错误行，无回退** | 错误行 → 整文件不写 |
| 5 | 得分列：空单元格 → 按 0 分；**非数值 / 超该题满分 / 为负 → 阻断** | 错误行 |
| 6 | 权限：按反查到的 overview 学校 token 校验；校管理员出现他校 → 整文件拒绝 | 整文件拒绝 |

宽表尚未成功导入时，前端第 2 步预览/导入按钮禁用。

### 4.4 错误结构与写入原子性

- 错误行复用 `ImportErrorRow {row, field, message}` + `ImportResult`；预览返回 `total_rows / valid_rows / error_rows / warnings / summary / preview_sample`（前 10 行，**匿名码为主键，禁止 xm/sfzh/ksh**）
- `import_result_to_dict` 不得把 `resolved_rows` 放进响应
- 写入沿用分块并行（500 行/块、≥800 行 4 线程）；块级事务，任一块失败整体返回失败信息、已写块保留（UPSERT 幂等，重导修复）。UI 提示「部分可能已写入，请整文件重导」
- 规模预估：宽表 17,700 行 → overview 17,700 + student 17,700 + score ~90,000 行；小题分英语最大 17,366 行 × 58 题 ≈ 100 万 detail。执行 1-3 分钟，前端 loading 态即可（**不做异步任务队列**）

---

## 5. API 设计（9 个端点，全部 `@audit_access`）

在 `education_router`（`src/agent/education/api.py`）下**新增**（不挂在旧 `/score-import` 下）：

```
GET  /api/v1/education/raw-score-import/batches              列批次（下拉数据源）
POST /api/v1/education/raw-score-import/batches              新建批次
GET  /api/v1/education/raw-score-import/papers               批次下试卷列表（含 9 科齐备预检信息）
GET  /api/v1/education/raw-score-import/templates/overview   下载成绩宽表 Excel 模板
GET  /api/v1/education/raw-score-import/templates/detail     下载小题分模板（可选 exam_id 生成该卷题号）
POST /api/v1/education/raw-score-import/overview-preview     宽表预览
POST /api/v1/education/raw-score-import/overview-execute     宽表执行
POST /api/v1/education/raw-score-import/detail-preview       小题分预览
POST /api/v1/education/raw-score-import/detail-execute       小题分执行
```

所有端点**不接收** `datasource_id`。开头：在当前工作空间匹配 `database=edu` 的已登记数据源 → `assert_datasource_accessible` → 新函数 `assert_raw_import_role_allowed`（仅局/校管理员）。

**GET /raw-score-import/batches** — `{batches: [{id, batch_name, exam_time}]}`；复用 `score_indicator._load_exam_batches` 查询模式（`ORDER BY exam_time DESC LIMIT 500`）。

**POST /raw-score-import/batches** — body `{batch_name, exam_time}`；批次名非空、不与现有重名（重名 → 400 带已有批次 id，前端自动选中）；插入 `tb_exam_batch` 返回新行。新建后 9 科预检仍会拦住上传，须先去试卷管理补卷。

**GET /raw-score-import/papers?exam_batch_id=X** — 该批次下试卷列表 `[{exam_id, subject, exam_score}]`，并附 `missing_subjects`、`duplicate_subjects`，以及 `overview: {imported, row_count, school_count, last_write_time}`（校管理员只统计本校）。前端用 `overview.imported` 决定能否跳过宽表、直接去小题分。同科多卷时该科目列入冲突说明。

**GET /raw-score-import/templates/{overview|detail}** — 下载可被解析器直接读取的 Excel 模板。数据必须在 sheet 0；「填写说明」放第二张。宽表模板 38 列及列序与教科院 `成绩宽表.xlsx` 一致。小题分**必须带 `exam_id`**，按该卷题目生成；表头合并对齐原表（标题整行、身份列竖向、科目盖住合计列、1卷/2卷盖住对应题列）。不提供无科目的通用模板。

**POST /raw-score-import/overview-preview / overview-execute** — `multipart/form-data`：

| 字段 | 类型 | 说明 |
|------|------|------|
| `exam_batch_id` | int Form | 必填，所选批次 |
| `file` | UploadFile | 必填，.xlsx |

无 `override_school_id`（校管理员规则改为整文件拒绝，不再逐行覆盖学校）。

响应（预览/执行同构）：

```json
{
  "total_rows": 17700, "valid_rows": 17695, "error_rows": [...],
  "warnings": [{"row": 0, "message": "异常扫描失败，导入已成功，请稍后在异常提醒中重试"}],
  "summary": {
    "overview_upserted": 17695, "students_created": 120, "students_updated": 17575,
    "score_upserted": 88432, "skipped_subjects": [],
    "alert_scan": {"detected": 3, "inserted": 3, "updated": 0}
  },
  "preview_sample": [{"anon_stu_id": "GZ_19D9D68D_...", "xx": "A01扬州中学", "班级": "高三(2)班", "zf6m": 663}]
}
```

**POST /raw-score-import/detail-preview / detail-execute** — `multipart/form-data`：`exam_batch_id`、`exam_id`（手选试卷）、`file`（.xls/.xlsx）。单文件调用。响应同构，summary 为 `{detail_upserted, students_matched}`（无 fallback 计数、无 alert_scan）。

**异常映射**：解析失败/维度缺失等业务错误 → `error_response(400, message, data=错误行清单)`；数据源连接失败 / 工作空间未登记 edu 库 → 400/500。

**执行后钩子**：宽表 execute 成功 → `scan_alerts_after_import` 扫描本文件涉及学校 × 该批次全部试卷（`class_names` 空 = 不按班过滤）。扫描失败不阻断导入，写入 `warnings`。**不调用** `recompute_if_bars_exist`。小题分 execute 成功 → 不再扫描。

**部署配套**：nginx `client_max_body_size` 由 20m 提升至 **64m**（34MB + multipart）。`proxy_read_timeout` 现网已是 300s，保持。

---

## 6. 前端页面设计（新建 `/construct/education/raw-score-import`）

**设计基调**：完全复用项目既有「政务蓝」设计语言——组合 `line-reach` 的 KPI 卡片、`fraction-bar` 的分组着色面板/pill、`data-rules` 的 Steps 向导三种既有构件。颜色/圆角/阴影 token 均取自现有页面。不引入新依赖与 Motion 库。

侧栏与菜单权限各加一项：`raw-score-import` / 「原始成绩导入」，紧挨现有「成绩导入」。旧项不改。

```
┌ 页头 ────────────────────────────────────────────────────────┐
│ [⬆]  原始成绩导入                          [重新开始]          │
│      教科院材料两步导入：成绩宽表 → 各科小题分                    │
└──────────────────────────────────────────────────────────────┘
┌ PANEL_CARD ── Steps（已完成步骤可点击回跳）──────────────────────┐
│  ● 选择考试批次 ──── ● 导入成绩宽表 ──── ● 导入各科小题分         │
└──────────────────────────────────────────────────────────────┘
```

**第 0 步 · 选择考试批次**（一张 PANEL_CARD，**无数据源 Select**）：

- 考试批次 Select（`showSearch`，显示 `批次名 + 考试时间`）+「新建批次」→ Modal（批次名 + DatePicker）；重名创建返回已有 id 时自动选中并提示「已存在，已为您选中」
- 选中批次后展示 9 科齐备状态（来自 `GET .../papers` 的 `missing_subjects`）。不齐则红色 Alert 列出缺科，**「下一步」禁用**
- 若 `overview.imported`：提示已导入人数/校数，主按钮改为「去导入小题分」，「重新导入宽表」为次要入口
- 蓝色着色面板作「流程说明」：两步导什么、什么顺序、导完自动生成异常提醒、不刷新达线

**第 1 步 · 导入成绩宽表**：

- 「下载宽表模板」按教科院 `成绩宽表.xlsx` 生成 **38 列**英文表头（列序：KSH/XX/XM/ZF3M/ZF4M/ZF6M/YW/YWZW/…/RY/RYKG/RYZW），带边框
- 上传区：`Upload.Dragger`（.xlsx）
- 若库中已有宽表：顶部 info Alert 显示人数/校数，可跳过直接去小题分；重新上传仍 UPSERT 覆盖
- 预览结果区：KPI 迷你卡 4 张——总行数/校验通过/错误行/警告行；错误行表 + 警告 Collapse + 预览样本表（匿名码列为主键）
- 「确认导入」仅当预览 `error_rows.length === 0` 可点；成功 → 绿色 Alert：summary +「去异常提醒查看」链接（**不**提示达线已更新）

**第 2 步 · 导入各科小题分**（逐文件管理）：

```
┌────────────────────────────────────────────────────────────┐
│ [📐] 小题分(数学).xls   [数学·自动识别]   试卷: [批次·数学 ▾]      │
│      状态: ● 已校验通过  17,640行        [预览校验] [确认导入] [移除] │
└────────────────────────────────────────────────────────────┘
```

- 模板下载：须先选科目。表头合并对齐教科院小题分（标题整行、身份列竖向、科目盖住合计列、1卷/2卷盖住对应题列），题号列按该卷真实题目生成。无通用空白模板
- 科目识别以**文件内标题行**为准，文件名 `小题分(数学)` 仅作回退
- 试卷下拉：`GET .../papers` 按批次过滤，默认预填自动识别科目对应的卷
- 批量操作：「全部预览」（**串行**）+「导入全部通过项」（仅 `error_rows` 为空的文件）
- 宽表未在库中且本次也未导入成功时：文件列表顶部黄色 Alert，预览/导入按钮禁用
- 状态机：`待预览 → 校验中 → 有错误/通过 → 导入中 → 已导入/失败`

**交互细节**：

- Steps 已完成步骤可点击回跳；回跳重选批次时清空两步所有状态
- 上传/执行中全页 `Spin` + 按钮 loading；大文件不做前端分片
- 前端 fetch 超时与 nginx 300s 对齐，不要用浏览器默认超时

---

## 7. 测试与验收

### 7.1 单元测试（`tests/agent/test_raw_import.py`，Mock 回调、不连真库）

| 测试组 | 覆盖点 |
|--------|--------|
| 宽表解析 | 38 列表头识别；缺必需列整文件拒绝；SFZH 重复、KSH 重复报错；KSH 班级解析；SFZH `xxxx.0` 规范化 |
| 匿名码 | `SHA256(token:sfzh)[:8].upper()` 与库内历史数据逐字节一致（`GZ_F57E7326_54558B0F` 黄金断言） |
| 宽表校验 | s_name 匹配失败报错；负分/超满分阻断；任一科无卷阻断；同科多卷阻断；school_admin 含他校整文件拒绝 |
| 宽表写入编排 | mock execute_sql：overview 含 ksh/sfzh/xm/exam_batch_id；student/score UPSERT 唯一键正确；未选考科目跳过 tb_score |
| 小题分解析 | 3 行复合表头；得分列/答案列/全卷列区分；列标签去括号 → question_no；子题 `15_1` 与大题 `15` 并存 |
| 小题分校验 | 试卷批次不符拒绝、科目不符警告；得分非数值阻断、空=0、超满分报错；学号不在宽表 → 错误行（无回退） |
| 市报生一致性 | 宽表 XX=市报校 → 匿名码用市报校 token；小题分反查命中宽表 → 同 token |
| 批次/权限 API | 重名创建返回已有 id；teacher/student 拒绝；接口不接收 datasource_id |
| 响应隐私 | preview_sample 不含 xm/sfzh/ksh |

### 7.2 集成冒烟（手动，用 `temp/教科院/` 真实材料，对 edu 数据源执行）

1. 新菜单进入新页面；旧「成绩导入」仍可用
2. 页面无数据源选择；工作空间未登记 edu 库时接口返回明确错误
3. 新建批次「测试批次X」→ 下拉出现且自动选中；9 科不齐时不能进入宽表
4. 补齐 9 科后导入宽表 → KPI 四卡、错误行 0、summary 计数正确；达线指标**不变**
5. 重复导入同一宽表 → UPSERT 无重复行（`count(ksh, exam_name)` 不变）
6. 导入 9 科小题分 → detail 行数 = 各文件行数×题数；抽查 1 名学生 24 题得分与 Excel 一致
7. 市报生抽查：overview/tb_score/tb_score_detail 三表 token 一致
8. 异常提醒生成且指向该批次
9. 校管理员上传全市宽表 → 整文件拒绝，一条不写
10. 旧入口回归：`/score-import/templates/{total,detail}` 与旧 preview/execute 行为不变

### 7.3 验收标准

全部单测通过 + `uv run ruff check .` 干净 + `npm run typecheck` 干净 + 冒烟全过；真实材料（1.7 万行宽表 + 英语 34MB 小题分）预览建议 <60s、执行 <3min。

### 7.4 文档

- 新增 `docs/raw_score_import.md`（产品约定/校验链/API/验收必读）
- `docs/education_module_architecture.md` 模块表中补 `raw_import.py` 一行
- 无 `EDU_DATASOURCE_ID` 环境变量；edu 库按已登记数据源的 `database=edu` 写死匹配

---

## 8. 范围外（明确不做）

- 改旧脱敏页/接口/菜单；删除旧后端代码
- 达线指标自动重算（`recompute_if_bars_exist`）
- 小题分全卷列与宽表总分的核对
- 小题分学校列回退
- 异步任务队列
- 批次管理独立页面（仅导入页内下拉 + 新建）
- 日语 RY 列作为强制科目
- 把教育库连接串或密码写进前端 / 本设计文档
