# 教育模块原始成绩导入实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增教科院原始成绩导入：固定写入 edu 业务库（不选手动数据源），宽表落入 `tb_score_overview`/`tb_student`/`tb_score` 并扫异常，再逐科小题分落入 `tb_score_detail`。旧脱敏导入页与接口原样保留。

**Architecture:** 新建 `raw_import.py` 做解析/校验/写入编排，复用 `score_import.py` 的批量 UPSERT；新接口组 `/raw-score-import/*` 不接收 `datasource_id`，用 `Settings.edu_datasource_id` 解析已登记数据源；前端新建 `raw-score-import.tsx` + 侧栏菜单，不改 `score-import.tsx`。

**Tech Stack:** Python 3.11/FastAPI/uv, pandas/openpyxl/xlrd, PostgreSQL `ON CONFLICT`, Next.js 14/Ant Design 5/TypeScript/Tailwind.

**Spec:** `docs/superpowers/specs/2026-08-31-raw-score-import-design.md`（2026-08-31 评审修订版）。

---

## File Structure

| File | Responsibility |
|------|----------------|
| `src/common/core/config.py` | 新增 `edu_datasource_id: int` |
| `.env.example` | 增加 `EDU_DATASOURCE_ID=` |
| `src/agent/education/raw_import.py` | 解析、校验、写入编排（新建） |
| `src/agent/education/alert_service.py` | `scan_alerts_after_import` 支持按批次全量扫描 |
| `src/agent/education/api.py` | 新增 `/raw-score-import/*` 7 个端点；**不改**旧 `/score-import/*` |
| `deploy/nginx.conf` | `client_max_body_size 64m` |
| `frontend-react/src/api/education.ts` | 新增 raw import API 方法（无 datasource_id） |
| `frontend-react/pages/construct/education/raw-score-import.tsx` | **新建**三步向导页 |
| `frontend-react/src/components/layout/side-bar.tsx` | 菜单项「原始成绩导入」 |
| `frontend-react/pages/construct/permission/menu.tsx` | 菜单权限列表补 key |
| `tests/agent/test_raw_import.py` | 单元/回归测试 |
| `docs/raw_score_import.md` | 产品约定文档 |
| `docs/education_module_architecture.md` | 模块表补一行 |

**禁止修改：** `frontend-react/pages/construct/education/score-import.tsx`、旧 `/score-import/preview|execute|templates` 行为。

---

## Task 1: Config + helpers

**Files:**
- Modify: `src/common/core/config.py`
- Modify: `.env.example`
- Create: `src/agent/education/raw_import.py`
- Test: `tests/agent/test_raw_import.py`

- [ ] **Step 1: Add setting**

In `Settings`:

```python
# 教育业务库对应的已登记数据源 ID（原始成绩导入固定使用，不由前端选择）
edu_datasource_id: int = 0
```

`.env.example` 增加一行 `EDU_DATASOURCE_ID=`（不要填密码或完整连接串）。

- [ ] **Step 2: Write constants, dataclasses, helpers in `raw_import.py`**

与离线端 / `scripts/import_score_overview.py` 一致：

```python
_SCHOOL_CIPHER_SECRET = b"yz_edu_k1"
_SCHOOL_HEX_START = 12
_SCHOOL_HEX_LEN = 8
_REQUIRED_SUBJECTS = ("语文", "数学", "英语", "物理", "化学", "生物", "历史", "政治", "地理")
_SUBJECT_CODE_TO_NAME = {
    "YW": "语文", "SX": "数学", "YY": "英语",
    "WL": "物理", "HX": "化学", "SW": "生物",
    "LS": "历史", "ZZ": "政治", "DL": "地理",
}
_OVERVIEW_REQUIRED_COLS = {"KSH", "SFZH", "XM", "XX", "ZF6M"}
```

`_encode_school_token` / `_generate_anon_stu_id` / `_ksh_to_class_name` 算法与现脚本一致。  
`_normalize_id_str`：把 `261081010844.0`、空格去掉，返回纯字符串。  
`assert_raw_import_role_allowed(scope)`：仅 `bureau_admin`/`school_admin`，teacher 拒绝。

`RawOverviewRow` 含 `row_num, ksh, sfzh, xm, xx, scores, totals, others`。  
`RawDetailRow` 含 `row_num, sfzh, ksh, school_name, scores`（`school_name` 仅用于校管理员整文件判断的展示，不用于算匿名码）。

- [ ] **Step 3: Tests**

```python
def test_school_token_matches_golden_pair():
    assert _encode_school_token("A05仪征中学") == "GZ_F57E7326"

def test_anon_stu_id_matches_golden_pair():
    assert _generate_anon_stu_id("GZ_F57E7326", "261081010844") == "GZ_F57E7326_54558B0F"

def test_ksh_to_class_name():
    assert _ksh_to_class_name("501101360479", "2026届高三1月期末") == "高三(10)班"
    assert _ksh_to_class_name("501101360408", "2026届高三1月期末") == "高三(8)班"
    assert _ksh_to_class_name("1234", "2026届高三1月期末") == ""

def test_normalize_id_str_strips_excel_float():
    assert _normalize_id_str("261081010844.0") == "261081010844"

def test_raw_import_role_rejects_teacher():
    from datasource.service.edu_permission import EduScope
    assert assert_raw_import_role_allowed(EduScope(edu_role="teacher"))
    assert assert_raw_import_role_allowed(EduScope(edu_role="bureau_admin")) is None
```

Run: `uv run pytest tests/agent/test_raw_import.py -v`  
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add src/common/core/config.py .env.example src/agent/education/raw_import.py tests/agent/test_raw_import.py
git commit -m "feat(raw-import): add edu datasource setting and import helpers"
```

---

## Task 2: Overview parsing and validation

**Files:**
- Modify: `src/agent/education/raw_import.py`
- Test: `tests/agent/test_raw_import.py`

- [ ] **Step 1: `_parse_overview_excel`**

`pd.read_excel(..., dtype=str)`，列名 `strip().upper()`。缺必需列返回 row=0 错误。  
逐行：KSH/SFZH 走 `_normalize_id_str`；空 KSH/SFZH/XX 记错误行并 continue；**SFZH 与 KSH 各自文件内唯一**（保留首条，重复报错）。空科目分 → `None`（未选考）。

- [ ] **Step 2: `_load_overview_dimensions`**

加载批次、`s_name IN xx_values` 的学校、该批次全部 `tb_exam`。  
`exams_by_subject`：同一科目出现多于一行则记入 `duplicate_subjects`。

- [ ] **Step 3: `_validate_overview_rows`**

顺序：

1. 9 科 `_REQUIRED_SUBJECTS` 任一不在 `exams_by_subject`，或 `duplicate_subjects` 非空 → 返回 `[], [row=0 错误]`。
2. 逐行学校匹配、班级解析、科目分范围（`None` 跳过；负分/超满分错误行）。
3. **校管理员整文件规则**（在调用方、校验之后）：若 `scope.edu_role == "school_admin"` 且任一 valid 行的 school token ≠ `scope.school_id`，丢弃全部 valid，追加 row=0 错误，列出他校样例与数量。局端不筛。

有任意 `error_rows` 时，execute 不得写库（预览仍返回 valid_rows 计数供 KPI，但 execute 入口见 Task 3：`if preview.error_rows: return`）。

- [ ] **Step 4: Tests**

覆盖：缺列、SFZH 重复、KSH 重复、学校不存在、超满分、缺 9 科试卷、同科多卷、school_admin 含他校 → 无 valid 且 row=0 错误。

Run: `uv run pytest tests/agent/test_raw_import.py -v`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agent/education/raw_import.py tests/agent/test_raw_import.py
git commit -m "feat(raw-import): overview parsing and validation"
```

---

## Task 3: Overview writing and preview/execute

**Files:**
- Modify: `src/agent/education/raw_import.py`
- Test: `tests/agent/test_raw_import.py`

- [ ] **Step 1: `_upsert_dict_rows`**

复用 `WriteDbSession.execute_upsert_batch`；≥800 行 4 线程、500 行/块。与现 `score_import` 相同策略。

- [ ] **Step 2: Build write rows**

overview 列必须包含 `ksh, exam_name, exam_batch_id, sfzh, xm, xx, bj, anon_stu_id` 以及科目/总分等已有列（`_pick_existing_cols`）。  
student：`id, school_id, class`，若有 `jc` 则从批次名解析四位届次（如 `2026届` → `2026`）。  
score：科目分 `None` 跳过；`exam_id+student_id` 冲突覆盖。

Resolved 行在内存中附带 `school_token`（给告警扫描用），**不得**进入 API dict。

- [ ] **Step 3: `preview_raw_overview_import` / `execute_raw_overview_import`**

签名无 `datasource_id`。`preview_sample` 只含 `anon_stu_id, xx, 班级, zf6m`。  
execute：`if preview.error_rows: return preview`（整文件不写）。写完返回 summary，**此处不扫异常**（由 API 层调 `scan_alerts_after_import`）。

- [ ] **Step 4: Tests**

mock `_upsert_dict_rows` 三次（overview/student/score）；有 error_rows 时 upsert 调用次数为 0；preview_sample 不含 `xm`/`sfzh`/`ksh`。

Run: `uv run pytest tests/agent/test_raw_import.py -v`

- [ ] **Step 5: Commit**

```bash
git add src/agent/education/raw_import.py tests/agent/test_raw_import.py
git commit -m "feat(raw-import): overview write and preview/execute"
```

---

## Task 4: Detail parsing

**Files:**
- Modify: `src/agent/education/raw_import.py`
- Test: `tests/agent/test_raw_import.py`

- [ ] **Step 1: `_parse_detail_excel`**

`header=None` 读入。标题行 `小题分(科目)` 抽科目。第 3 行（iloc[2]）作列标签，调用 `parse_questions_from_headers`。  
**额外过滤：** `question_no` 或原表头含 `答案` / `全卷` / `1卷` / `2卷` / `合计` 的列丢弃。  
学号列：在第 3 行找「学号」，找不到再在第 2 行找。KSH/学校列同理。学号空 → 错误行。空得分 → `0.0`。

- [ ] **Step 2: Tests**

基本三列得分；答案列与「全卷（150分）」不进入 `scores`；科目识别。

Run: `uv run pytest tests/agent/test_raw_import.py::test_parse_detail_basic -v`

- [ ] **Step 3: Commit**

```bash
git add src/agent/education/raw_import.py tests/agent/test_raw_import.py
git commit -m "feat(raw-import): detail excel parser"
```

---

## Task 5: Detail validation, writing, preview/execute

**Files:**
- Modify: `src/agent/education/raw_import.py`
- Test: `tests/agent/test_raw_import.py`

- [ ] **Step 1: `_load_detail_dimensions`**

批次、试卷（批次不一致 raise/转 row=0 错误）、overview `WHERE exam_name=? AND sfzh = ANY(?)`、题目 `question_no`。  
**不加载学校 fallback 映射。**

- [ ] **Step 2: `_validate_detail_rows`**

题号集合必须 ⊆ DB；缺一列 → row=0 整文件拒绝。  
学生：`overview_by_sfzh.get(sfzh)` 没有 → 错误行「学号不在已导入的宽表中」。**禁止按学校列算匿名码。**  
得分范围校验。  
校管理员：任一命中行的 overview 学校 token ≠ 本校 → 清空 valid，row=0 整文件拒绝。

- [ ] **Step 3: preview/execute**

execute 遇 `error_rows` 不写。summary：`detail_upserted, students_matched`（无 `students_by_school_fallback`）。

- [ ] **Step 4: Tests**

命中 overview 写入；不在宽表 → 有错误且 execute 不 upsert；校管理员他校整文件拒绝。

Run: `uv run pytest tests/agent/test_raw_import.py -v`

- [ ] **Step 5: Commit**

```bash
git add src/agent/education/raw_import.py tests/agent/test_raw_import.py
git commit -m "feat(raw-import): detail validation and write without school fallback"
```

---

## Task 6: Batch-wide alert scan

**Files:**
- Modify: `src/agent/education/alert_service.py`
- Test: `tests/agent/test_raw_import.py`

- [ ] **Step 1: Extend `scan_alerts_after_import`**

增加可选 `exam_batch_id: int | None = None`。原 `resolved_rows` 路径保持不变（旧脱敏导入仍按班级过滤）。

当 `exam_batch_id is not None`：从 resolved 收集 `school_id`/`school_token`；查出该批次全部 exam；对每个 `(school_id, exam_id)` 调用 `detect_and_upsert_for_exam`，`class_names=[]`（不按班过滤）。学校集合为空则返回全 0。

- [ ] **Step 2: Test**

mock `execute_sql` 返回 2 科、`resolved_rows=[{"school_id": "GZ_19D9D68D"}]`、`exam_batch_id=1` → `detect_and_upsert_for_exam` 调用 2 次。

Run: `uv run pytest tests/agent/test_raw_import.py::test_scan_alerts_batch_wide_uses_all_exams -v`

- [ ] **Step 3: Commit**

```bash
git add src/agent/education/alert_service.py tests/agent/test_raw_import.py
git commit -m "feat(raw-import): batch-wide alert scan after overview import"
```

---

## Task 7: New API endpoints

**Files:**
- Modify: `src/agent/education/api.py`（只追加，不改旧 score-import 三个函数体）
- Test: `tests/agent/test_raw_import.py`

- [ ] **Step 1: Helper `resolve_edu_datasource`**

```python
def resolve_edu_datasource(session, current_user, workspace_oid) -> tuple[int, str, dict]:
    ds_id = int(get_settings().edu_datasource_id or 0)
    if ds_id <= 0:
        raise BadRequestException("未配置 EDU_DATASOURCE_ID，无法导入原始成绩")
    assert_datasource_accessible(session, current_user, ds_id, workspace_oid)
    db_type, config, _ = _load_datasource(ds_id, workspace_oid)
    return ds_id, db_type, config
```

每个新端点：`assert_raw_import_role_allowed`；`@audit_access`。Form/Query **没有** `datasource_id`。

- [ ] **Step 2: batches + papers**

`GET/POST /raw-score-import/batches`、`GET /raw-score-import/papers`。  
papers 响应：`{papers: [...], missing_subjects: [...], duplicate_subjects: [...]}`。9 科相对 `_REQUIRED_SUBJECTS` 做差。

- [ ] **Step 3: overview preview/execute**

multipart：`exam_batch_id` + `file`。execute 成功后：

```python
scan_summary = scan_alerts_after_import(
    session, db_type=..., config=..., workspace_oid=...,
    datasource_id=ds_id,
    resolved_rows=result.resolved_rows,
    exam_batch_id=exam_batch_id,
)
```

扫描异常 catch 后写入 `warnings`，不把导入改成失败。**不要**调用 `recompute_if_bars_exist`。

- [ ] **Step 4: detail preview/execute**

multipart：`exam_batch_id` + `exam_id` + `file`。成功不扫异常。

- [ ] **Step 5: Route smoke**

`GET /api/v1/education/raw-score-import/batches` 未登录 → 401/403。  
`GET /api/v1/education/score-import/templates/total` 仍存在。

Run: `uv run pytest tests/agent/test_raw_import.py -v`  
`uv run ruff check src/agent/education/api.py src/agent/education/raw_import.py`

- [ ] **Step 6: Commit**

```bash
git add src/agent/education/api.py tests/agent/test_raw_import.py
git commit -m "feat(raw-import): add raw-score-import REST endpoints"
```

---

## Task 8: nginx upload limit

**Files:**
- Modify: `deploy/nginx.conf`

- [ ] **Step 1:** `client_max_body_size 64m;`（原 20m）。不要改 `proxy_read_timeout`（已是 300s）。

- [ ] **Step 2: Commit**

```bash
git add deploy/nginx.conf
git commit -m "deploy(nginx): raise client_max_body_size to 64m for raw score files"
```

---

## Task 9: Frontend API layer

**Files:**
- Modify: `frontend-react/src/api/education.ts`

- [ ] **Step 1: Types + methods**

`ExamBatchOption`、`RawPaperOption`、`RawPapersResponse { papers, missing_subjects, duplicate_subjects }`、`RawImportResult`。

方法均**无 datasourceId 参数**：

- `listRawImportBatches()`
- `createRawImportBatch(batchName, examTime)`
- `listRawImportPapers(examBatchId)`
- `postRawOverviewImport("overview-preview"|"overview-execute", formData)`
- `postRawDetailImport("detail-preview"|"detail-execute", formData)`

Base path: `/education/raw-score-import/...`。旧 `previewScoreImport` / `executeScoreImport` 不动。

- [ ] **Step 2: Commit**

```bash
git add frontend-react/src/api/education.ts
git commit -m "feat(raw-import): add frontend API for raw-score-import"
```

---

## Task 10: New wizard page + menu

**Files:**
- Create: `frontend-react/pages/construct/education/raw-score-import.tsx`
- Modify: `frontend-react/src/components/layout/side-bar.tsx`
- Modify: `frontend-react/pages/construct/permission/menu.tsx`

- [ ] **Step 1: Menu**

在 `score-import` 条目后插入：

```ts
{ key: "raw-score-import", path: "/construct/education/raw-score-import", label: "原始成绩导入", icon: <UploadOutlined /> },
```

`menu.tsx` 的 `routes` 同样补 `{ key: "raw-score-import", label: "原始成绩导入" }`。  
**不要改**现有 `score-import` 项。

- [ ] **Step 2: Page skeleton**

三步：`0 选择考试批次` → `1 导入成绩宽表` → `2 导入各科小题分`。无数据源 Select。复用现页 `PANEL_CARD` / KPI 视觉 token（可从 `score-import.tsx` 或 `line-reach` 抄样式常量，不要 import 旧页逻辑）。

- [ ] **Step 3: Step 0**

加载 batches；新建批次 Modal；选中后拉 papers。`missing_subjects.length > 0` 或 `duplicate_subjects.length > 0` → Alert + 禁用下一步。

- [ ] **Step 4: Step 1**

Dragger 宽表；预览；仅 `error_rows.length === 0` 可执行。成功设 `overviewDone=true`。失败提示可重导。

- [ ] **Step 5: Step 2**

多文件列表；标题行识别科目（文件名回退）；试卷下拉。`!overviewDone` 时禁用预览/导入。全部预览串行。科目不符走 backend warnings 展示。

前端 fetch：若项目有统一 timeout，对齐 ≥ 300s。

- [ ] **Step 6: typecheck**

```bash
cd frontend-react && npm run typecheck
```

Expected: 无错误。`score-import.tsx` git diff 为空。

- [ ] **Step 7: Commit**

```bash
git add frontend-react/pages/construct/education/raw-score-import.tsx \
        frontend-react/src/components/layout/side-bar.tsx \
        frontend-react/pages/construct/permission/menu.tsx
git commit -m "feat(raw-import): add raw-score-import page and menu"
```

---

## Task 11: Tests, lint, smoke checklist

**Files:**
- Modify: `tests/agent/test_raw_import.py`

- [ ] **Step 1: Regression list（必须有）**

- 缺 ZF6M 拒绝
- SFZH/KSH 重复
- school_admin 全市文件 row=0 拒绝且 execute 不写
- 小题分不在宽表 → 不写
- preview_sample 无 xm/sfzh/ksh
- teacher 角色拒绝
- 旧 score-import 路由仍在

- [ ] **Step 2: Full matrix**

```bash
uv run pytest tests/agent/test_raw_import.py tests/agent/test_score_import.py -v
uv run ruff check .
cd frontend-react && npm run typecheck && npm run lint
```

Expected: all green. `test_score_import.py` 证明旧入口未破。

- [ ] **Step 3: Manual smoke**（见设计 §7.2，10 项）

- [ ] **Step 4: Commit**

```bash
git add tests/agent/test_raw_import.py
git commit -m "test(raw-import): regression coverage for raw import rules"
```

---

## Task 12: Documentation

**Files:**
- Create: `docs/raw_score_import.md`
- Modify: `docs/education_module_architecture.md`

- [ ] **Step 1:** 产品约定与设计 §1–§5 对齐：固定 edu 库、新旧入口、校管理员整文件拒绝、无学校列回退、9 科预检、扫异常不重算达线、`EDU_DATASOURCE_ID`、nginx 64m。

- [ ] **Step 2:** 架构模块表增加 `raw_import.py` 一行。

- [ ] **Step 3: Commit**

```bash
git add docs/raw_score_import.md docs/education_module_architecture.md
git commit -m "docs(raw-import): add operational and architecture notes"
```

---

## Self-Review Checklist

**1. Spec coverage**
- [x] 不选手动数据源 / `EDU_DATASOURCE_ID` → Task 1/7/10
- [x] 新页面新接口、旧入口不改 → Task 7/10/11
- [x] 校管理员整文件拒绝 → Task 2/5
- [x] 小题分无学校列回退 → Task 5
- [x] 9 科试卷必须齐全 + papers.missing_subjects → Task 2/7/10
- [x] 宽表写 ksh/sfzh/xm 但响应脱敏 → Task 3
- [x] 扫异常不重算达线 → Task 7
- [x] nginx 64m → Task 8
- [x] KSH 文件内唯一、ID `.0` 规范化 → Task 1/2
- [x] 显式跳过全卷列 → Task 4

**2. Placeholder scan**
- [x] 无 TBD
- [x] 新接口无 `datasource_id` 表单字段

**3. Known traps**
- 不要复用 `assert_import_role_allowed`（会放行 teacher）
- `scan_alerts_after_import` 的 resolved 必须带 `school_id`，`RawOverviewRow` 本身没有该字段，execute 前要挂上
- 不要把密码或完整 edu 连接串写入文档/前端
- `score-import.tsx` 必须保持 git 未改

---

## Execution Handoff

**Plan updated to match the revised spec.**

Two execution options:

**1. Subagent-Driven (recommended)** — 每个 Task 派一个新 subagent，Task 之间人工过一眼。REQUIRED SUB-SKILL: `superpowers:subagent-driven-development`。

**2. Inline Execution** — 本会话用 `superpowers:executing-plans` 按 Task 执行。

Which approach would you like?
