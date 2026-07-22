# 异常提醒（校内待办）

> 与「异常规则」配置分离：规则管阈值，提醒管 **检出后的待办列表**。  
> 存平台库表 ``edu_anomaly_alert``（``DATABASE_URL``），不进教育业务数据源。

相关：[`anomaly_rules.md`](./anomaly_rules.md) · 测试包说明 [`../testdata/README_anomaly_import.md`](../testdata/README_anomaly_import.md)

---

## 1. 产品范围（已确认）

| 项 | 约定 |
|----|------|
| 可见角色 | `school_admin`（校长）看本校；`teacher` 看本校 + 绑定班级 |
| 不可见 | `bureau_admin`（教育局）**完全无菜单**；`student` 不可见 |
| 状态 | `pending`（未处理）/ `confirmed`（已处理）；一键确认整份报告，备注可选 |
| 重导入 / 重跑报告 | 已确认记录 **保持已处理**，仅刷新原因/明细快照；未处理可更新 |
| 列表粒度 | **一份报告一条**（同校 + 同场 + 同班 + 同源）；点开看学生明细 |
| 一期类型 | 临界生 / 大幅退步 / 偏科（复用 `identify_at_risk_students`，嵌在 payload） |
| 二期 | 薄弱班级等 **暂不做** |

---

## 2. 表结构

Alembic：`20260721_03_edu_anomaly_alert.py`（revises `20260721_02`）

```text
edu_anomaly_alert
  id, workspace_oid, datasource_id
  school_id, class_name, student_id   # 报告级 student_id 为空
  exam_id, exam_name, subject_name
  anomaly_type          tier_alert（报告）；旧版 critical/regression/imbalanced 会自动合并
  title, reason, payload_json         # payload: counts + critical/regression/imbalanced 列表
  source                score_import | tier_alert_report | …
  status                pending | confirmed
  dedupe_key            唯一：workspace|ds|school|exam|class|subject|source|tier_alert
  confirmed_by, confirmed_at, confirm_note
  create_time, update_time            # 库内多为 UTC naive；API 输出转东八区
```

打开列表时，若仍存在旧版「每人一条」，会按同校同场同班同源 **合并为报告** 后删除旧行。

---

## 3. 触发

1. **成绩导入成功** `POST /education/score-import/execute`  
   → **仅 `import_type=detail`（小题分）成功后**调用 `scan_alerts_after_import`。  
   → 总分导入只写 `tb_score`，响应带 `anomaly_alerts_pending: true`，提示继续导小题分。  
   → 扫描按 `(school_id, exam_id)` 拉 **总分**，且 **仅本次导入涉及的班级**；再按班各写一条报告。  
   → 判定仍读 `tb_score` 总分（小题分是流程门槛，不参与临界/退步/偏科计算）。  
   → 响应可带 `anomaly_alerts: { inserted, updated, detected, exams }`（失败不阻断导入成功）。

2. **分层预警报告** `build_tier_alert_report_data_tool`  
   → `upsert_from_at_risk_payload`：整份报告一条（指定 `class_name` 时不拆班）。  
   → 须注入 `datasource_id` + 能解析出 `school_id`；缺任一会 warning 并跳过，**报告仍成功**。

阈值来源：`edu_anomaly_config` / `get_config()`（与异常规则页一致）。有卷面满分时及格线 = 满分 × `pass_ratio`。

---

## 4. API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/education/anomaly-alerts` | 列表（仅 `anomaly_type=tier_alert`）；`status` / `limit` / `offset` |
| GET | `/education/anomaly-alerts/{id}` | 详情（含 payload 学生列表） |
| POST | `/education/anomaly-alerts/{id}/confirm` | body `{ note? }` 确认整份报告 |

---

## 5. 前端

- 页面：`/construct/education/anomaly-alerts`
- 侧栏：「异常提醒」（教育局 / 学生角色隐藏）
- 列表：报告标题 + 临界/退步/偏科人数；抽屉内三张明细表 + 确认
- 成绩导入：总分成功提示继续导小题分；小题分成功提示报告条数并链到异常提醒
- 导入页：根容器 `h-full min-h-0 overflow-y-auto`，内容多时可滚动

---

## 6. 关键代码

| 文件 | 职责 |
|------|------|
| `models_alert.py` | ORM |
| `alert_service.py` | 报告级 upsert、旧数据合并、列表、确认、权限、东八区时间格式化 |
| `api.py` | HTTP + 导入钩子（仅 detail 扫描） |
| `tools.py` / `orchestrator.py` | 分层预警补写 |
| `pages/.../anomaly-alerts.tsx` | 列表/详情 UI |
| `pages/.../score-import.tsx` | 导入成功提示 |
| `docs/anomaly_alerts.md` | 本文 |

迁移：`uv run alembic upgrade head`  
单测：`uv run pytest tests/agent/test_anomaly_alerts.py -q`

---

## 7. 行为约定（验收必读）

### 7.1 退步按考试时间，不按导入先后

「上场」= 同校同科、`exam_time` 更早的最近一场，**不是**「先导入的那一场」。

例（库内时间轴）：

| exam_id | 名称 | exam_time |
|--------|------|-----------|
| 4 | 扬州市摸底 | 2026-03-20 |
| 2 | 扬州市调研 | 2026-04-02 |
| 3 | 连淮扬镇统考 | 2026-05-08 |
| 1 | 江苏省一模 | 2026-06-30 |

先导 exam1（后一场）、再导 exam3（前一场）时：

- 扫描 **只针对本次导入的 exam_id**；
- **不会**回头重算已存在的更晚考试报告；
- 因此后一场报告 **不会**因导入前一场而自动出现退步（无论是否已确认）；
- 要让后一场出现相对前一场的退步：前一场成绩已在库后，**再重导一次后一场**（走完小题分）。

### 7.2 已确认后再导同一场

同 `dedupe_key` upsert：刷新 title/reason/payload，**status 仍为 confirmed**。  
列表「时间」列当前展示 `create_time`（东八区），内容刷新后创建时间可能不变。

### 7.3 班内扫描范围

导入触发时按 **本次 Excel 班级** 过滤，不会扫出其它班。  
但对该班该场仍拉 **全班总分** 做三态检测：班内已有历史分时，报告人数可大于本次导入行数。空班/新班测最干净。

### 7.4 无异常报告也正常

若分数未落在临界带、无上场可对比、无多科偏科，则 `detected=0`，提示「未检出新的异常报告」——属预期，不是故障。

---

## 8. 自查结论与已知限制（2026-07-22）

主流程（导入 → 报告 → 确认 → 权限）已验收可用；`tests/agent/test_anomaly_alerts.py` 通过。

### 8.1 已验证正常

- 报告粒度：一班一场一条；详情内嵌三类明细  
- 权限：校长本校、老师本班；教育局/学生无菜单  
- 仅小题分导入后扫描；总分提示继续导小题分  
- 已确认保留；导入扫描按本次班级过滤  
- API 时间东八区展示  
- 先导后一场再导前一场，后一场不会自动补退步（与代码一致）

### 8.2 已知限制 / 易误解点

| 优先级 | 项 | 说明 |
|--------|----|------|
| 中 | 班内整班重算 | 导入子集仍扫该班该场全部总分；有历史分时人数 > Excel 行数 |
| 中 | 不回刷更晚考试 | 导前一场不更新后一场报告；需重导后一场才刷新退步 |
| 低～中 | 已确认仍可刷新内容 | 再导同一场会更新 payload，状态仍 confirmed；列表时间用 create_time |
| 低 | 列表 consolidate | 每次 list 可能合并旧版每人一条；报告级数据下基本无感 |
| 低 | 测试覆盖偏薄 | 缺 detail-only 扫描、班级过滤、不回刷后场等集成测试 |
| — | 一期能力边界 | 无班均对比；偏科需多科；小题分不参与三态判定 |

### 8.3 后续可改进（未做）

1. 列表展示 `update_time`（或同时展示创建/更新）  
2. 可选：导入前一场时，自动重算同班更晚考试的 **pending** 报告（已确认是否动需产品再定）  
3. 补导入扫描集成测试  
4. 「未检出」提示可区分：无阈值命中 vs 扫描跳过/失败  

---

## 9. 测试数据

目录：`testdata/`（说明见 `testdata/README_anomaly_import.md`）

| 场景 | 文件前缀 |
|------|----------|
| 高三(11)班验证导入顺序 | `anomaly_class11_exam1_later_*`（后一场）+ `anomaly_class11_exam3_earlier_*`（前一场） |
| 高三(9)班两场 | `anomaly_class9_exam1_*` / `anomaly_class9_exam3_*` |
| 高三(10)班（有历史分） | `anomaly_score_import_test.xlsx` / `anomaly_score_detail_import_test.xlsx` |

建议验证顺序（(11) 班）：

1. 先导后一场（exam1）→ 报告退步应为 0  
2. 可选确认  
3. 再导前一场（exam3）→ 可不产生新报告；后一场报告应保持原样  
4. 再重导后一场 → 后一场应刷新出退步（如 `C11_0004` 130→85）

---

## 10. 与异常规则文档关系

见 `docs/anomaly_rules.md`。提醒列表 **不** 替代规则配置；规则改动影响下次检出，不自动改写历史已确认记录的状态。
