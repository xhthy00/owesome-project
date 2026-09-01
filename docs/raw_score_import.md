# 教科院原始成绩导入

Web 端新增入口，用于导入教科院原始材料（成绩宽表 + 各科小题分）。旧「脱敏模板」成绩导入页与 `/score-import/*` 接口保持不变。

## 入口隔离

| | 旧脱敏导入 | 新原始导入 |
|---|---|---|
| 页面 | `/construct/education/score-import` | `/construct/education/raw-score-import` |
| 菜单 | 成绩导入 | 原始成绩导入 |
| 接口 | `/api/v1/education/score-import/*` | `/api/v1/education/raw-score-import/*` |
| 数据源 | 前端选择 | 固定写入 edu 业务库（按已登记数据源的 database=edu 匹配，无环境配置） |

新接口一律不接收 `datasource_id`。不要把 edu 库密码或完整连接串写进前端或本文档。

## 流程

1. 选择或新建考试批次。该批次必须已有 9 科试卷（语文、数学、英语、物理、化学、生物、历史、政治、地理）；缺科或同科多卷时不能上传宽表。批次/学校/试卷/题目只匹配不创建。
2. 导入成绩宽表（`.xlsx`）。写入 `tb_score_overview` / `tb_student` / `tb_score`。成功后扫描异常提醒，**不重算达线**。重进页面时若该批次宽表已在库中，可跳过此步直接去导入小题分；重新上传仍会 UPSERT 覆盖。
3. 逐科导入小题分（`.xls` / `.xlsx`）。写入 `tb_score_detail`。学生必须已出现在本批次宽表中（按身份证号匹配），没有学校列兜底。小题分导入后不扫异常。预览返回全部校验通过行（前端分页），列含 Excel 身份字段（学号/考号/姓名/学校）、`tb_score_detail` 的试卷/匿名学号/班级，以及各题得分。若该科小题分已在库中，页面会提示并需确认后才覆盖。校验通过与导入成功用不同状态区分。

重复导入全程 UPSERT 覆盖；失败后整文件重导即可。

## 权限与整文件拒绝

- 仅局端管理员（`bureau_admin`）和校管理员（`school_admin`）可导入；教师与学生拒绝（新入口单独校验，不复用会放行教师的旧函数）。
- 校管理员文件中一旦出现外校数据，整文件拒绝，不筛本校导入。
- 校验出现错误行则整文件不写。
- 小题分里有学生不在本批次宽表，或题号不在试卷题目中，整文件拒绝。

## 接口（9 个，均无 `datasource_id`）

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/api/v1/education/raw-score-import/batches` | 列批次 |
| POST | `/api/v1/education/raw-score-import/batches` | 新建批次（重名 400，返回已有 id） |
| GET | `/api/v1/education/raw-score-import/papers` | 批次下试卷 + 9 科缺科/同科多卷 + 宽表/小题分是否已导入 |
| GET | `/api/v1/education/raw-score-import/templates/overview` | 下载成绩宽表 Excel 模板（38 列，列序与教科院成绩宽表一致） |
| GET | `/api/v1/education/raw-score-import/templates/detail` | 下载小题分 Excel 模板（须 `exam_id`；表头合并对齐教科院小题分） |
| POST | `/api/v1/education/raw-score-import/overview-preview` | 宽表预览（不写库） |
| POST | `/api/v1/education/raw-score-import/overview-execute` | 宽表执行；成功后扫异常 |
| POST | `/api/v1/education/raw-score-import/detail-preview` | 小题分预览（不写库） |
| POST | `/api/v1/education/raw-score-import/detail-execute` | 小题分执行 |

所有端点在当前工作空间中匹配 `database=edu` 的已登记数据源，再校验数据源权限与角色。不接收、也不读取 `datasource_id` / `EDU_DATASOURCE_ID`。

## 错误策略

- 预览不写库；执行阶段会重新跑完整校验。
- 只要有错误行（含整文件级 `row=0`），该次执行一条不写，返回 `400` 及错误行清单。
- 当前工作空间未登记 edu 业务库、无数据源权限、连接失败 → `400` / `403` / `500`。
- 异常扫描失败不阻断导入，写入 warnings。

## 隐私

`XM` / `SFZH` / `KSH` 会写入业务库，但预览样本、API 响应和日志不得回显这些字段。预览以 `anon_stu_id` 为主键。

## 部署

- nginx：`client_max_body_size 64m`（`deploy/nginx.conf`）。`proxy_read_timeout` 保持 300s。
- 前端 fetch 超时与 300s 对齐。
