# 教育问数 SQL 校验失败：系统性修复方案

> 版本：v1（2026-11-11）
> 关联文档：`docs/education_permission_v2.md`、`docs/model_generated_permission_sql_plan.md`
> 适用范围：`DATABASE_EDU_URL` + DataSemantic 的显式权限查询（`model_explicit_v2`）

## 0. 摘要

这次失败不是账号权限问题，而是三个系统缺口叠加：

1. **规范不可执行**：释放形态规则只存在于校验器代码里，模型拿到的是一段散文和一个描述性字段，没有正例；
2. **拒绝不可定位**：34 个 `AUTH_RELEASE_SHAPE_INVALID` 检查点大多只返回一句中文，不告诉模型哪一列、期望什么 AST；
3. **预算连坐**：整轮共用一个修复计数器，`lookup_question_context` 的失败也扣 SQL 修复次数，导致没机会改对。

修复分两阶段：

| 阶段 | 内容 | 工期 | 目标 |
|---|---|---|---|
| **Phase A（止血）** | W2 结构化诊断 + W3 预算分账 + W1 最小正例 | 约 6.5 人日（最小止血 3 天） | 同样的问题第二次能改对，不再整轮 `stop` |
| **Phase B（治本）** | W4 能力目录/预检 + W5 错误分层/UI + W6 可观测/回归 | 约 6.5 人日 | 同类问题事前拦截、事后可量化、可防回归 |

**安全边界不变**：不放宽证明器、不扩大授权、不让宿主生成或改写 SQL。所有修复只改善“规范表达、诊断、预算、呈现”，不改变“哪些 SQL 被接受”。

### 0.1 实施状态（2026-11-11）

| 工作流 | 状态 | 代码落点 |
|---|---|---|
| W1 正例 | 已实现 | `semantic_permission_examples.py`、`model_context()`、运行提示 |
| W2 结构化诊断 | 已实现 | `fail_shape()`、错误域/用户文案、`repair_contract` |
| W3 预算分账 | 已实现 | `PermissionRecovery`、`query_semantic`/`lookup`、阶段阻塞 |
| W4 能力目录 | 已实现 | `CAPABILITY_CATALOG`、提示、`question_assessment` |
| W5 错误分层/UI | 已实现 | SSE `error_detail`、`chatAdapter.ts`、`useChat.ts` |
| W6 可观测 | 已实现 | `tool_call_log` 扩展、lookup 日志、trace、报告脚本 |
| 回归 | 已补 | `tests/chat/test_permission_diagnostics.py` 等 |

---

## 1. 目标与非目标

### 1.1 目标

- **可修复**：可修复的形态错误必须带 `reason`、`location`、`rejected_expression`、`expected_expression`，模型不需要猜。
- **可归因**：用户与模型都能区分 `authorization` / `capability` / `sql_shape` / `execution`，不再把“写法不对”读成“没权限”。
- **预算按查询负责**：候选探查失败不消耗 SQL 修复次数；同一子任务最多 2 次修复；终止性错误仍然全局停止。
- **能力可发现**：问题超出当前授权能力时，在生成 SQL 之前说明并给出替代口径，不用查询试探。
- **可量化**：首轮通过率、修复成功率、失败分类、Top 规则可统计。
- **可防回归**：每条新增形态规则必须同时有结构化诊断和近失测试。

### 1.2 非目标

- 不新增可接受的 SQL 形态（本期不放宽证明器）。
- 不让宿主补 WHERE、改写 SQL 或生成基准/排名 SQL。
- 不扩大任何用户授权，不新增业务能力。
- 不重写 DataSemantic 规划器或物理再证明链路。
- 不改动报告、导出、图表等下游功能。

---

## 2. 根因与工作流映射

| 根因 | 工作流 | 优先级 | 验收信号 |
|---|---|---|---|
| R1 契约不是单一事实源 | W1 契约生成正例 | P0 | 每条能力一条可证明的正例进入提示 |
| R2 形态代理语义、诊断粗 | W2 结构化诊断 | P0 | 近失 SQL 返回 `reason/location/expected` |
| R3 自由生成无约束 | W1 + W4 | P0/P1 | 首轮通过率提升；能力缺口前置说明 |
| R4 预算连坐、终止无出路 | W3 预算分账 | P0 | lookup 失败不扣 SQL 预算；阶段内 2 次 |
| R5 能力层缺失 | W4 能力目录/预检 | P1 | “全校个人名次”在 SQL 前被说明 |
| R6 错误语义/可观测/测试/文档漂移 | W5 + W6 | P1/P2 | 错误分层；指标可查；近失语料入 CI |

---

## 3. W1：规则单一事实源与规范正例

### 3.1 设计

1. 新增 `src/datasource/service/semantic_permission_examples.py`，从 `PermissionManifest` + 当前 grant 生成**每条能力一条**的 canonical SQL 示例。
2. 示例只使用当前用户已授权的范围值（学校/届次/班级），考试 ID 用 `<exam_batch_id>` 占位；示例标题明确“这是形态示例，不是可直接执行的模板”。
3. 在 `AuthorizationContext.model_context()` 增加 `reference_examples` 字段，随 `scope` 进入系统提示。
4. CI 用测试夹具填充占位符并调用 `PermissionValidator.prove()`，保证示例永远可证明；示例不可证明则构建失败。
5. 示例不进入 `PermissionQueryContract` 的指纹；但会改变 `model_context`，从而改变 `context_key`，旧 checkpoint 会按设计失效（部署说明中注明）。

### 3.2 代码落点

```python
# src/datasource/service/semantic_permission_examples.py
from sqlglot import exp, parse_one

def canonical_examples(manifest, grants) -> dict[str, str]:
    """每个已授权 capability 返回一条 canonical SQL；未授权的能力不生成。"""
    examples = {}
    for capability in sorted({c for g in grants for c in g.capabilities}):
        examples[capability] = _render_baseline(manifest, capability) \
            if capability.endswith("_baseline") else _render_self_rank(manifest, capability)
    return examples

def _render_baseline(manifest, capability):
    policy = next(p for p in manifest.relations.values() if not p.public)
    rules = build_query_contract(manifest).relations[policy.physical_table]
    metric_id, formula = next(iter(rules.aggregate_expressions.items()))
    column = next(parse_one(formula).find_all(exp.Column)).name
    precision = manifest.precision
    sample = rules.sample_columns[metric_id]
    # 按 policy.scope_columns / exam_column / precision / 门槛渲染
    return f"""WITH own_scope AS (
  SELECT AVG({column}) AS {metric_id}
  FROM {policy.physical_table}
  WHERE ...  -- 当前 grant 的完整元组 + exam_batch_id = <exam_batch_id>
  HAVING COUNT({sample}) >= {manifest.minimum_sample_size}
), grade_baseline AS (
  SELECT ROUND(AVG({column}), {precision}) AS {metric_id}
  FROM {policy.physical_table}
  WHERE ...  -- 学校 + 届次 + exam_batch_id = <exam_batch_id>
  HAVING COUNT({sample}) >= {manifest.minimum_sample_size}
     AND COUNT(DISTINCT {rules.scope_columns['class_id']}) >= {manifest.minimum_reference_units}
)
SELECT ROUND(o.{metric_id}, {precision}) AS class_mean,
       g.{metric_id} AS grade_mean,
       ROUND(o.{metric_id} - g.{metric_id}, {precision}) AS diff
FROM own_scope o JOIN grade_baseline g ON g.{metric_id} IS NOT NULL"""
```

```python
# src/datasource/service/education_authorization.py::model_context
"reference_examples": canonical_examples(self.manifest, self.grants),
```

```python
# src/chat/service/deepagents_runtime.py::_run_turn（提示拼装处，约 1140 行）
if explicit_authorization is not None and examples:
    prompt += (
        "\n规范形态示例（占位符必须替换为已确认值；这是形态示例，不是可执行模板）：\n"
        + "\n\n".join(f"[{capability}]\n{sql}" for capability, sql in examples.items())
    )
```

### 3.3 测试

```python
# tests/chat/test_permission_examples.py
def test_each_capability_example_is_provable():
    for capability in ("grade_baseline", "city_baseline", "district_self_rank"):
        ctx = context(capabilities=(capability,))
        sql = fill_placeholders(canonical_examples(ctx.manifest, ctx.grants)[capability])
        assert PermissionValidator(ctx).prove(parse_one(sql, read="postgres")).releases
```

### 3.4 验收

- 提示中能看到 `grade_baseline` 的完整正例，且 `ROUND` 位于 `grade_baseline` CTE 内。
- CI 中每条示例可证明；新增能力没有示例则测试失败。
- 用假模型回放“外层 ROUND”近失 SQL，修复 1 次即可成功。

---

## 4. W2：结构化诊断

### 4.1 错误详情 schema（`error_schema_version = 2`）

```json
{
  "code": "AUTH_RELEASE_SHAPE_INVALID",
  "failure_domain": "sql_shape",
  "is_permission_issue": false,
  "reason": "baseline_round_required",
  "location": { "cte": "grade_baseline", "projection": "mean_sx" },
  "rejected_expression": "AVG(sx)",
  "expected_expression": "ROUND(AVG(sx), 2)",
  "precision": 2,
  "repair_hint": "取整必须发生在参照池聚合 CTE 内；外层只引用已取整的值。",
  "remaining_sql_repairs": 1
}
```

### 4.2 首批覆盖的 10 条规则

| `reason` | 当前触发点 | `location` | `expected_expression` 示例 |
|---|---|---|---|
| `baseline_round_required` | 聚合 CTE 投影不是 `ROUND(..., precision)` | `{cte, projection}` | `ROUND(AVG(sx), 2)` |
| `baseline_having_required` | 缺少样本/单位数门槛 | `{cte}` | `HAVING COUNT(sx) >= 10 AND COUNT(DISTINCT bj) >= 3` |
| `baseline_group_not_allowed` | 分组超出授权粒度 | `{cte, group}` | 不分组，或仅 `[exam_batch_id]` |
| `baseline_count_output_not_allowed` | SELECT 输出未批准 COUNT | `{cte, projection}` | 仅输出审核指标/允许维度 |
| `baseline_projection_not_allowed` | 指标表达式不在审核定义内 | `{cte, projection}` | 展开后的审核表达式 |
| `reference_filter_not_allowed` | 参照池额外人群筛选 | `{cte, expression}` | 移除额外谓词（AVG 自动忽略 NULL） |
| `pool_order_limit_distinct_not_allowed` | 参照池排序/截断/去重 | `{cte}` | 移除 ORDER BY/LIMIT/OFFSET/DISTINCT |
| `pool_join_before_release` | 参照池释放前参与关联 | `{cte}` | 先聚合释放，再在最终 SELECT 关联 |
| `window_requires_separate_cte` | 聚合与窗口同层 | `{cte}` | 先聚合，再单独 CTE 开窗口 |
| `scope_tuple_incomplete` | 过滤未构成完整授权元组 | `{source_alias, missing_keys}` | 补齐 `school/cohort/class/exam` |

### 4.3 实现

```python
# src/chat/service/semantic_permission_validator.py
def fail_shape(message, *, reason, location=None, rejected=None, expected=None,
               hint=None, example=None, **details):
    fail(
        "AUTH_RELEASE_SHAPE_INVALID",
        message,
        reason=reason,
        location=location or {},
        rejected_expression=rejected,
        expected_expression=expected,
        repair_hint=hint,
        example=example,
        **details,
    )
```

```python
# aggregate() 的 ROUND 检查
if not is_rank:
    if not isinstance(expression, exp.Round) or literal(
        expression.args.get("decimals")
    ) != str(self.contract.reference_policy.precision):
        fail_shape(
            "整体基准必须按策略精度ROUND后释放",
            reason="baseline_round_required",
            location={
                "cte": getattr(select.parent, "alias_or_name", "") or source.relation,
                "projection": e.alias_or_name,
            },
            rejected=expression.sql(dialect=self.manifest.dialect),
            expected=f"ROUND({expression.sql(dialect=self.manifest.dialect)}, "
                     f"{self.contract.reference_policy.precision})",
            hint="取整必须发生在参照池聚合 CTE 内；外层只引用已取整的值，"
                 "不要在外层再对未取整的 AVG 结果做 ROUND。",
            precision=self.contract.reference_policy.precision,
            relation=source.relation,
        )
```

错误包装：

```python
# src/datasource/service/semantic_permission_contract.py
_DOMAIN = {
    "AUTH_COLUMN_FORBIDDEN": "authorization",
    "AUTH_SCOPE_OUTSIDE_GRANT": "authorization",
    "AUTH_REVOKED": "authorization",
    "AUTH_RELEASE_SHAPE_INVALID": "sql_shape",
    "AUTH_SCOPE_MISSING": "sql_shape",
    "AUTH_QUERY_SHAPE_UNSUPPORTED": "sql_shape",
    "FIELD_NOT_PUBLISHED": "sql_shape",
    "FIELD_WRONG_SOURCE": "sql_shape",
    "AUTH_PHYSICAL_PROOF_FAILED": "compiled_plan",
    "QUERY_PLAN_INVALID": "compiled_plan",
    "QUERY_PLAN_UNSUPPORTED": "compiled_plan",
    "QUERY_EXECUTION_FAILED": "execution",
    "QUERY_SERVICE_UNAVAILABLE": "service",
    "AUTH_MAPPING_MISSING": "configuration",
    "AUTH_CONTEXT_INVALID": "configuration",
    "AUTH_CONTEXT_STALE": "context",
    "AUTH_CONTRACT_MISMATCH": "context",
    "QUERY_PUBLICATION_STALE": "context",
}
```

- `permission_error_detail` 增加 `failure_domain`、`is_permission_issue`、`error_schema_version=2`。
- `prove()` 中：如果 `detail` 已有 `repair_hint`，只附加 `repair_contract`（失败 relation 的 `reference_query_contract` + `precision` + `example`），不再附带完整 `reference_query_contracts`；没有结构化提示时保留旧行为，避免一次性改动过大。
- 用户文案按 domain 分开：
  - `sql_shape`：`这条查询的写法未通过规则校验（你的账号权限正常）：{message}`
  - `authorization`：`当前授权不允许这项操作：{message}`
  - `capability`：`当前授权未开通这个口径：{message}`
  - `execution`：保持现有文案。
- 文案变更会影响现有断言“查询 SQL”的测试（如 `test_parallel_bad_queries_cannot_exceed_host_repair_budget`），需同步更新；`access_denied` / `query_unsupported` / `compiled_plan` 的文案保持不变。

### 4.4 测试

```python
# tests/chat/test_permission_diagnostics.py
@pytest.mark.parametrize("mutate,reason", [
    (lambda s: s.replace("ROUND(AVG(s.score_value),2) AS grade_mean",
                         "AVG(s.score_value) AS grade_mean"), "baseline_round_required"),
    (lambda s: s.replace("HAVING COUNT(s.score_value)>=10", ""), "baseline_having_required"),
    (lambda s: s.replace("HAVING COUNT(DISTINCT s.class_id)>=3", ""), "baseline_having_required"),
])
def test_near_miss_returns_actionable_diagnostic(mutate, reason):
    with pytest.raises(AuthorizationError) as caught:
        prove(mutate(BASELINE), context(capabilities=("grade_baseline",)))
    detail = caught.value.detail
    assert detail["reason"] == reason
    assert detail["failure_domain"] == "sql_shape"
    assert detail["location"]
    assert detail["expected_expression"]
    assert detail["repair_hint"]
```

**防回归的元测试**：

```python
# tests/chat/test_permission_diagnostics.py
def test_every_release_shape_failure_has_reason():
    tree = ast.parse(Path("src/chat/service/semantic_permission_validator.py").read_text())
    missing = []
    for node in ast.walk(tree):
        if _is_fail_call(node, "AUTH_RELEASE_SHAPE_INVALID"):
            if not any(kw.arg == "reason" for kw in node.keywords):
                missing.append(node.lineno)
    assert not missing, f"未结构化的释放形态检查点: {missing}"
```

首期允许用白名单基线（记录剩余数量），随后逐步清零；不允许新增无 `reason` 的检查点。

---

## 5. W3：修复预算分账

### 5.1 目标语义

| 失败类型 | 计数 | 是否阻塞 |
|---|---|---|
| `query_semantic` 可修复错误 | 记入当前子任务 | 同一子任务超过 2 次 → 该阶段停止查询 |
| `lookup_question_context` 失败 | 不记入 SQL 修复 | 只影响本轮探查上限，不阻塞查询 |
| `access_denied` / `compiled_plan_unverifiable` / `configuration` | 不计数 | 立即全局停止 |
| 新子任务 | 独立计数 | 之前阶段失败不影响新阶段 |

### 5.2 `PermissionRecovery` 重构

```python
# src/datasource/service/semantic_permission_contract.py
@dataclass
class PermissionRecovery:
    max_repairs: int = RECOVERY_CONTRACT.max_sql_repairs
    stage_repairs: dict[object, int] = field(default_factory=dict)
    blocked: bool = False                 # 仅终止性错误
    blocked_stages: set[object] = field(default_factory=set)
    last_error: dict | None = None
    _terminal_error: dict | None = field(default=None, repr=False)

    def reject(self, detail, *, stage=None, tool="query_semantic"):
        if self.blocked:
            return self.last_error
        error = permission_error_detail(...)
        self.last_error = error
        if tool != "query_semantic":
            error["remaining_sql_repairs"] = self.remaining(stage)
            return error
        if error["recovery_action"] != "repair_sql":
            self.blocked = True
            self._terminal_error = error
            return error
        used = self.stage_repairs.get(stage, 0) + 1
        self.stage_repairs[stage] = used
        error["remaining_sql_repairs"] = max(0, self.max_repairs + 1 - used)
        if used > self.max_repairs:
            self.blocked_stages.add(stage)
            error.update(recovery_action="stop", retryable=False,
                         sql_repair_required=False, repair_budget_exhausted=True)
            if self._terminal_error is None:
                self._terminal_error = error
        return error

    def stage_blocked(self, stage):
        return stage in self.blocked_stages
```

### 5.3 Runtime 改动

```python
# query_semantic
stage = active_stage_index()
if permission_recovery.blocked or permission_recovery.stage_blocked(stage):
    return json.dumps({"error": permission_recovery.public_error() or "本阶段查询已停止。",
                       "error_detail": permission_recovery.last_error}, ensure_ascii=False)
...
permission_recovery.reject({"message": str(exc), **exc.detail},
                           stage=stage, tool="query_semantic")
```

```python
# lookup_question_context
permission_recovery.reject({"message": str(exc), **exc.detail},
                           stage=active_stage_index(), tool="lookup")
```

```python
# restrict_model_tools（约 943 行）
if permission_recovery.blocked:
    ...  # 现有全局摘除逻辑不变
elif permission_recovery.stage_blocked(active_stage_index()):
    model_tools = [t for t in model_tools if t.name != "query_semantic"]
    model_request = model_request.override(
        system_prompt=f"{model_request.system_prompt}\n当前子任务查询已停止；"
                      "可继续其他阶段，或直接说明本阶段缺口。"
    )
```

### 5.4 测试

```python
def test_lookup_failure_does_not_consume_sql_repair_budget():
    recovery = PermissionRecovery()
    recovery.reject(AuthorizationError("AUTH_SCOPE_MISSING", "lookup").detail,
                    stage=0, tool="lookup")
    recovery.reject(AuthorizationError("AUTH_RELEASE_SHAPE_INVALID", "q1").detail,
                    stage=0, tool="query_semantic")
    recovery.reject(AuthorizationError("AUTH_RELEASE_SHAPE_INVALID", "q2").detail,
                    stage=0, tool="query_semantic")
    assert not recovery.blocked
    assert not recovery.stage_blocked(0)   # 还剩 1 次
    recovery.reject(AuthorizationError("AUTH_RELEASE_SHAPE_INVALID", "q3").detail,
                    stage=0, tool="query_semantic")
    assert recovery.stage_blocked(0)
```

保留现有并行查询测试：同一阶段 5 条坏 SQL 仍然最多执行 3 条（2 次修复）。

**阶段阻塞 ≠ 整轮失败**：`blocked` 只用于终止性错误；阶段阻塞后若后续阶段成功，最终答复按 `evidence` 走 partial/full，不能把单阶段失败说成整轮失败。

### 5.5 风险

- 按阶段计数会让一轮总尝试次数上升；增加**整轮硬上限**（如 `max(2 * 阶段数 + 1, 6)`）控制成本。
- 终止性错误（越权、物理证明失败）仍然全局停止，不能重开。
- `human_retry_blocked` 语义保持不变（技术失败后不允许用 `ask_human` 让用户确认技术重试）。

---

## 6. W4：能力目录与预检

### 6.1 能力目录

先区分两类口径，否则模型会把“只开通了年级基准”误读成“不能查本班学生明细”：

- `scope_answers`：授权范围内可直接查询的普通口径（本班学生成绩明细、班级内排名/分布/均分/及格率），不需要 capability；
- `capability_catalog`：只约束参照池/跨单位对照（`grade_baseline` / `city_baseline` / `district_self_rank`）。

```python
# src/datasource/service/education_authorization.py::model_context
"scope_answers": [
    "授权范围内的学生成绩明细（匿名学号、各科分数、总分）",
    "授权范围内的排名、分数分布、均分/及格率等聚合",
    "授权范围与参照基准的对照（需对应 capability）",
],
"scope_not_answers": [
    "授权范围外的其他班级/学校明细",
    "未开通的学生个人全校名次、跨校排名",
],

# src/datasource/service/semantic_permission_contract.py
CAPABILITY_CATALOG = {
    "grade_baseline": {
        "label": "本校同届次年级整体基准",
        "answers": ["本班与本校同届次、同考试的年级均值/分差"],
        "not_answers": ["其他班级/其他学校的原始明细", "学生个人全校名次", "跨校排名"],
    },
    "city_baseline": {
        "label": "全市整体基准",
        "answers": ["本校/本区与全市同届次、同考试的均值对照"],
        "not_answers": ["其他学校明细", "全市学生个人排名"],
    },
    "district_self_rank": {
        "label": "本区县全市名次（仅自身）",
        "answers": ["本区县自身的全市名次和参评区县数"],
        "not_answers": ["其他区县名次", "全市学校/学生排名"],
    },
}
```

在 `model_context()` 增加：

```python
"capability_catalog": [
    {"id": name, **CAPABILITY_CATALOG[name],
     "granted": any(name in g.capabilities for g in self.grants)}
    for name in CAPABILITIES
],
```

### 6.2 提示与评估

系统提示增加：

```
只能使用 capability_catalog 中 granted=true 且 answers 覆盖问题口径的能力。
若问题要求 not_answers 中的口径，直接说明“当前授权未开通”，并给出替代口径
（例如“班级内排名 + 年级整体基准”）。
不得用 lookup_question_context 或 query_semantic 试探未开通的能力。
```

`question_assessment._INSTRUCTIONS` 增加一条：

```
若问题要求的粒度/范围不在 authorization_scope.capability_catalog 的 answers 内，
优先 clarify 说明能力缺口，不要用 lookup 试探。
```

### 6.3 探查失败的定向提示

`lookup_question_context` 收到 `AUTH_SCOPE_MISSING` 时，在 `error_detail` 增加：

```json
{
  "repair_hint": "该探查未落在完整授权元组内。若目标是其他班级明细或全校个人名次，该能力未开通；可改用 grade_baseline 做班级与年级基准对照。",
  "available_alternatives": ["grade_baseline"]
}
```

### 6.4 可选：结构化查询意图（Phase B 后）

给 `query_semantic` 增加可选参数 `capability`、`metric_ids`、`group_by`，仅作为**意图提示**，宿主仍以 SQL 为准、仍由证明器判定。好处：

- 能力缺口可以在 SQL 之前被识别；
- 错误可以定位到“意图”而不是“语法”；
- 不改变安全模型（不信任模型参数）。

---

## 7. W5：错误分层与 UI

### 7.1 后端

- 错误详情增加 `failure_domain`、`is_permission_issue`、`error_schema_version`。
- `user_message` 按 domain 分开（见 4.3）。
- SSE `error` 事件已经带 `error_detail`（`deepagents_runtime.py` 约 1413 行），保持不变；新增 `failure_domain` 字段。

### 7.2 SSE / 前端类型

```ts
// frontend-react/src/api/adapter/chatAdapter.ts
case "error":
  handlers.onError?.((data.error as string) ?? "Unknown error", {
    preserveInterrupt: data.preserve_interrupt === true,
    errorDetail: data.error_detail as ErrorDetail | undefined,
  });
```

```ts
type ErrorDetail = {
  failure_domain?:
    | "authorization"
    | "capability"
    | "sql_shape"
    | "execution"
    | "configuration"
    | "compiled_plan"
    | "context"
    | "service";
  is_permission_issue?: boolean;
  user_message?: string;
  repair_hint?: string;
};
```

### 7.3 会话卡片

`useChat.ts` 的 `onError`（约 952 行）改为：

```ts
onError: (msg, metadata) => {
  const detail = metadata?.errorDetail;
  const domain = detail?.failure_domain;
  const title = domain === "sql_shape" ? "查询写法未通过校验"
    : domain === "capability" ? "当前授权未开通该口径"
    : domain === "authorization" ? "当前授权不允许"
    : "查询未完成";
  const note = domain === "sql_shape" || domain === "capability"
    ? "\n你的账号权限正常；这是查询写法/口径问题。"
    : "";
  writeAssistant(`${title}：${msg}${note}`);
  ...
};
```

- 不在用户界面展示 `rejected_expression`、AST、表名；只展示 `user_message` 和必要的 `repair_hint`。
- “执行记录”里保留技术细节，供管理员排查。

### 7.4 最终答复

宿主生成最终失败说明时，按 domain 加前缀；禁止模型自行猜测“权限未开通”。现有 `permission_recovery.public_error()` 路径改为返回带 domain 的文案。

---

## 8. W6：可观测性与回归

### 8.1 `tool_call_log` 扩展

```sql
ALTER TABLE tool_call_log ADD COLUMN IF NOT EXISTS error_code TEXT;
ALTER TABLE tool_call_log ADD COLUMN IF NOT EXISTS error_detail TEXT;
ALTER TABLE tool_call_log ADD COLUMN IF NOT EXISTS failure_domain TEXT;
ALTER TABLE tool_call_log ADD COLUMN IF NOT EXISTS rule TEXT;
```

- PostgreSQL 用 `ADD COLUMN IF NOT EXISTS`；SQLite 用 `PRAGMA table_info` 判断后 `ALTER TABLE`。
- 更新 `src/common/core/database.py::_ensure_tool_call_log_table`，新增 `_ensure_tool_call_log_columns(conn, dialect)`。
- 更新 `src/agent/audit/tool_call_log.py::_INSERT_SQL` 与 `write_tool_call_log_async` 参数。

### 8.2 记录内容

- `query_semantic`：`error_code`、`error_detail`（截断 4KB）、`failure_domain`、`rule`（`reason`）。
- `lookup_question_context`：也写 `tool_call_log`（当前完全没记），包含 SQL、错误码、`repair_hint`。
- **trace_id 修复**：在 `log_tool_call_fire_and_forget` 调用时捕获 `get_trace_id()`，作为参数传入异步任务，避免后台任务拿到 `-`。

### 8.3 指标

新增 `scripts/semantic_failure_report.py`（只读 SQL）：

| 指标 | 口径 |
|---|---|
| 首轮通过率 | 每个 `query_semantic` 首次调用成功 / 总会话 |
| 修复成功率 | 失败后 2 次内成功 / 有失败的会话 |
| 失败分类分布 | `failure_domain` 计数 |
| Top 规则 | `rule` 计数 |
| 阶段预算耗尽率 | `repair_budget_exhausted` 计数 |

先做脚本，不做实时看板；等数据稳定后再考虑接入监控。

### 8.4 测试语料

新增 `tests/chat/test_permission_diagnostics.py`：

- **近失语料**（每条一个用例）：
  - 外层 ROUND（本次生产形态）
  - 完全缺 ROUND
  - 精度写成 1 / 3
  - 缺样本门槛 / 缺单位数门槛
  - 参照池额外 `sx IS NOT NULL`
  - 参照池 `ORDER BY` / `LIMIT`
  - 参照池释放前 JOIN
  - SELECT 输出 COUNT
  - 分组列超出白名单
  - 授权元组缺 `exam_batch_id`
- **属性测试**（可选）：对同一语义的多种等价写法，断言“要么接受、要么给出同一个 `reason`”，防止错误信息漂移。
- **端到端**：假模型先提交外层 ROUND，再提交修正 SQL；断言第 1 次失败带 `repair_hint`、第 2 次成功、只消耗 1 次修复。
- **元测试**：所有 `AUTH_RELEASE_SHAPE_INVALID` 调用必须有 `reason`（白名单逐步清零）。

### 8.5 文档

- 更新 `docs/education_permission_v2.md`：
  - 明确“ROUND 在参照池聚合 CTE 内”，给出正例；
  - 明确预算按子任务、lookup 不计数；
  - 明确 `failure_domain` 与用户文案。
- 新增 `docs/education_sql_failure_playbook.md`：运维/支持按 `failure_domain` 和 `rule` 排查的步骤。

---

## 9. 版本、兼容与灰度

| 项 | 处理 |
|---|---|
| `CONTRACT_VERSION` | 不变（`education-sql/1`）；本期不改接受形态 |
| `PROVER_VERSION` | 不变 |
| `error_schema_version` | 新增 `2`；前端按字段存在性兼容 |
| `model_context` 新增字段 | 会改变 `context_key`，旧 checkpoint 失效；部署说明注明“本轮会话需重新发起” |
| Feature flags | `semantic_error_schema_v2`、`semantic_stage_repair_budget`、`semantic_prompt_examples`；默认关闭，灰度后开启 |
| 回滚 | 关闭 flag 即回到旧行为；数据库新增列可保留，不影响旧代码 |

### 部署顺序

1. 后端 W2/W3（flags off）→ 部署；
2. 前端 W5（兼容旧错误格式）→ 部署；
3. 开启 `semantic_error_schema_v2` + `semantic_stage_repair_budget`，观察 1 周；
4. 开启 `semantic_prompt_examples`，对比首轮通过率；
5. 上线 W4/W6，最后移除 flag。

---

## 10. 排期

| 阶段 | 工作流 | 人日 | 依赖 |
|---|---|---|---|
| A | W2 结构化诊断（首批 10 条） | 2.5 | 无 |
| A | W3 预算分账 | 2.0 | 无 |
| A | W1 最小正例（grade_baseline） | 1.0 | W2 的 `fail_shape` 可复用 |
| A | 近失语料 + 元测试 | 1.0 | W2/W3 |
| **A 合计** | | **6.5** | |
| B | W1 其余能力正例 | 1.0 | A |
| B | W4 能力目录/预检 | 1.5 | A |
| B | W5 错误分层/UI | 1.5 | W2 |
| B | W6 日志/指标/文档 | 2.5 | W2 |
| **B 合计** | | **6.5** | |

**最小止血包（3 天）**：W2 只做 `baseline_round_required` / `baseline_having_required` / `baseline_count_output_not_allowed` / `baseline_group_not_allowed` 四条，W3 只做“lookup 不计数”，加一条回放测试。能先把“整轮 stop”变成“第二次改对”。

---

## 11. 验收标准

### 11.1 功能

- 回放生产记录 `chat_conversation_record.id=3191`：
  - 第 1 次提交返回 `reason=baseline_round_required`、`location.cte`、`expected_expression`、`repair_hint`；
  - 第 2 次提交修正 SQL 通过；
  - 整轮不触发 `stop`，最终能给出“班级 vs 年级基准”结果。
- `lookup_question_context` 失败后，仍有 2 次 SQL 修复机会。

### 11.2 安全

- 现有 `test_explicit_permissions.py` 的越权变异全部仍然拦截；
- 新增诊断/示例不扩大接受面：同一 SQL 在旧/新代码下的 accept/reject 结果一致（除错误详情外）；
- 示例 SQL 必须通过证明器，不能是“绕过规则”的模板。

### 11.3 体验

- `sql_shape` 错误的用户文案包含“权限正常”；
- 前端不再把“写法错误”显示成“权限失败”；
- 管理端能看到失败分类和 Top 规则。

### 11.4 回归

- 近失语料全部有 `reason`；
- 元测试阻止新增无 `reason` 的形态检查点；
- 并行坏查询仍然最多 3 次（同阶段）；
- 旧格式错误（无 `error_detail`）前端不崩。

---

## 12. 风险与回滚

| 风险 | 影响 | 缓解 |
|---|---|---|
| 预算按阶段计数导致总尝试变多 | 成本/延迟上升 | 整轮硬上限；终止性错误仍全局停 |
| 结构化错误泄露表名/字段 | 信息暴露 | 用户界面只显示 `user_message`；技术细节仅管理端 |
| 提示变长稀释注意力 | 首轮通过率反而下降 | 示例控制在 1 条/能力、<1KB；灰度对比 |
| `model_context` 变更导致 checkpoint 失效 | 旧会话需重开 | 发布说明；按设计接受 |
| 改造 `PermissionRecovery` 引入回归 | 越权查询被放行 | 保留现有并行/终止测试；flag 回滚 |
| 示例被模型原样执行（含占位符） | 查询失败 | 占位符显式标注；证明器拒绝非法值；提示说明“非模板” |

---

## 13. 一页结论

- **先做 W2 + W3**：让拒绝可定位、让预算不连坐，约 4–5 天就能把“整轮不可重试”变成“第二次改对”。
- **再做 W1**：给每条能力一条可证明的正例，从源头降低首轮失败率。
- **最后做 W4/W5/W6**：能力缺口前置说明、错误分层、指标与语料，防止同类问题复发。
- **安全边界不动**：证明器不变、授权不变、宿主不改写 SQL；修复的是“规范表达、诊断、预算、呈现”。

---

## 14. 后续方向（非本期）

以下改动会触及安全边界或产品原则，需单独评审，不纳入本期：

1. **语义等价接受**：证明“基准指标只用于最终投影”，从而允许外层 ROUND 等等价写法。需要扩展证明器的数据流分析能力，属于安全边界变更，必须配越权变异矩阵。
2. **受约束生成**：对高频口径（班级 vs 年级基准、自身名次）用槽位模板/DSL，模型只选范围/指标/分组，宿主渲染规范 SQL。会改变“SQL 全由模型生成”的原则，需产品与安全确认。
3. **结构化查询意图**：`query_semantic(capability, metric_ids, group_by, sql)`，宿主只校验、不生成；可把“能力缺口”从 SQL 错误中彻底分离。
4. **模型分层**：SQL 生成使用更强的模型，Flash 级模型负责规划/解释；用首轮通过率与修复成功率数据决定，而不是凭感觉换模型。
5. **编译证明监控**：统计 `AUTH_PHYSICAL_PROOF_FAILED` 与 DataSemantic 规划器版本的关系，避免“逻辑 SQL 正确但编译后被拒”的隐性失败。

