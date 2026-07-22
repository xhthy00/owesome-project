# 异常规则配置说明（验收 + 落库版）

> 配置存于**系统库**表 ``edu_anomaly_config``（``DATABASE_URL`` / 库名一般为 `awesome`），**不是**教育业务数据源。  
> 默认三条规则与历史硬编码一致；前端「异常规则」菜单可改，重启不丢失。

---

## 1. 改动影响

| 模块 | 说明 |
|------|------|
| 表 `edu_anomaly_config` | 新建；Alembic `20260721_01` |
| 判定 `identify_at_risk_students` | 读 `get_config()`（优先 DB） |
| API `/education/report-config` | 读写落库 |
| 前端 `/construct/education/anomaly-rules` | 侧栏「异常规则」 |
| 报告模板 / 聊天 | **未改** |

不配或恢复默认时：临界 ±5、退步 −10、偏科分差 ≥20，与改造前一致。

**异常提醒（校内待办）**：检出后的列表 / 确认流程见 [`docs/anomaly_alerts.md`](./anomaly_alerts.md)；本页只描述阈值配置。

**对话仍写 90/127.5？** 多半是 SQL 示例/术语曾写死 `0.6/0.85`，模型照抄。已改为注入当前库表比例，并要求用 `compute_score_stats_tool`。请**新开对话**再测；按你现在的 30%/50%，150 分卷应为及格 **45**、优秀 **75**。

---

## 2. 表结构（系统库）

```text
edu_anomaly_config
  id                      主键
  pass_ratio              及格比例（默认 0.6 = 60%）
  excellent_ratio         优秀比例（默认 0.85 = 85%）
  pass_threshold          绝对分兜底（= 比例 × 满分兜底）
  excellent_threshold     绝对分兜底
  default_full_score      满分兜底（默认 100，仅无卷面满分时用）
  critical_margin         临界半径（默认 5 分）
  regression_threshold    退步阈值（默认 -10）
  imbalance_score_gap     偏科分差（默认 20）
  rules_json              JSONB：三条异常规则（五类参数）
  update_time
```

迁移：`20260721_01` 建表；`20260721_02` 增加比例字段。

**有卷面满分时**：及格线 = 满分 × `pass_ratio`（150×60%=90）。  
**无满分列时**：用 `pass_threshold`（由比例×满分兜底同步）。

```bash
uv run alembic upgrade head
```

---

## 3. 五类参数（rules_json）

| 客户参数 | 字段 |
|----------|------|
| 阈值 | `threshold` |
| 对比对象 | `compare_target`（`pass_line` / `prev_exam` / `self_subjects`） |
| 连续次数 | `consecutive_n`（一期仅 1 生效） |
| 波动幅度 | `fluctuation_mode` + `fluctuation_value`（一期仅 `abs`） |
| 范围 | `range_lo_offset` / `range_hi_offset` 或绝对 `range_lo` / `range_hi` |

---

## 4. 怎么配置

### 前端（推荐）

侧栏 → **异常规则** → 改经典阈值 → 保存。下方表格只读预览生效规则。

### API

```http
GET  /api/v1/education/report-config
PUT  /api/v1/education/report-config
Content-Type: application/json

{ "critical_margin": 8, "regression_threshold": -15, "imbalance_score_gap": 25 }

POST /api/v1/education/report-config/reset
```

### 环境变量

仅在**首次种子 / DB 不可用回落**时参与默认值；正式以表数据为准。

---

## 5. 相关代码

- Model: `src/agent/education/models_anomaly.py`
- 持久化: `src/agent/education/anomaly_persistence.py`
- 配置入口: `src/agent/education/config_store.py`
- 前端: `pages/construct/education/anomaly-rules.tsx`
