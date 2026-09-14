# `src/agent/education/` 模块地图（分支 `edu`）

> 实测规模：**64 个 `.py` 文件 / 46 659 行**（另有 `src/agent/resource/templates/education/` 下 **17 个 HTML 模板**，合计 81 个文件——任务描述的「78 个」应是 py+模板的粗算）。所有行号为 read 工具实测。

---

## 1. 整体流水线：从自然语言到报表 HTML

存在**两条并行链路**，长期各自演化（这是本模块最大的结构性风险）。

### A. 聊天主链路（生产路径，工具驱动，LLM 在环）

```
POST /api/v1/chat/chat-stream                    src/chat/api/chat.py
 └─ agent_runner（真正的编排中心）                src/chat/service/agent_runner.py
     ├─ clarification_gate  抽槽/追问            src/chat/service/clarification_gate.py
     ├─ _run_planner_phase                          agent_runner.py:1647   ← 入口函数
     │   ├─ classify_report_intent (LLM+规则)       intent_router.py:1137
     │   │    └─ fallback_classify_report_intent    intent_router.py:598
     │   │    └─ _hard_route / _pick_report_type   intent_router.py:969 / :508
     │   ├─ → ReportRoute                           intent_router.py:309
     │   ├─ should_use_deterministic_report_plan    intent_router.py:805
     │   └─ plan_items_for_route → plan_items_for_report_type
     │                                              intent_router.py:1285 / :1308
     │        （EXPECTED_PLAN_TOOLS 把 ReportType 映射到具体 build_*_report_data_tool
     │          并生成中文 sub_task 文案）           intent_router.py:1250
     ├─ DataAnalyst / ReAct 执行子任务              src/agent/expand/data_analyst.py
     │   └─ 工具来自 EDUCATION_TOOLS（36 个）        tools.py:7780
     │        └─ build_*_report_data_tool 内部组 data dict
     │             └─ render_html_report             business.py:504
     │                  └─ _render_template_html     business.py:313
     │                       └─ templates/education/*.html  {{KEY}} 占位替换
     └─ Summarizer 对账（KPI 校验/去 SQL 细节）      summary_context.py:977 / :1202
```

### B. API 确定性链路（Orchestrator，无 LLM）

```
POST /api/v1/education/{generate-report|batch-report|diagnostic-report}
        api.py:364 / :197 / :294
 └─ _build_orchestrator                            api.py:115
     ├─ execute_sql  回调 → execute_sql_with_permission_by_user_id   api.py:129
     └─ resolve_schema 回调 → education_schema.json → 启发式推断      api.py:151
 └─ ReportOrchestrator.run_spec                     orchestrator.py:306  ← 入口函数
     ├─ ReportIntentResolver.resolve                orchestrator.py:112
     ├─ select_report_template                      templates.py:236
     └─ _gather_data                                orchestrator.py:346
          ├─ LINE_REACH / DIFFICULTY_CURVE / SUBJECT_RESEARCH / _BUREAU_TYPES 分派 (:359-366)
          ├─ KPI 库内无 LIMIT 聚合 (kpi_sql) 或行级 prepare_score_rows_for_kpi
          ├─ _fill_*（:489-561）按 ReportType 逐个补字段
          └─ _render_template_html                  business.py:313
```

**结论**：真正的入口是 `_run_planner_phase`（chat）与 `ReportOrchestrator.run_spec`（API/批量）；真正的编排中心是 **`intent_router`（选型）+ `tools.py` 的 36 个 `build_*_tool`（组数）**。`ReportOrchestrator` 只是 API/批量旁路，与 `tools.py` 存在同类型报告的双实现。

---

## 2. 文件分组（64 文件）

| 组 | 文件（行数）概览 |
|---|---|
| **报告类型实现**（18） | `subject_diagnosis`1526 · `student_exam`1453 · `demo_report`1432 · `comprehensive`1127 · `bureau_analysis`1025 · `line_reach`803 · `line_reach_report`772 · `subject_research`1866 · `difficulty_curve`722 · `knowledge_cohort`558 · `class_weak_subject`544 · `school_intervention`493 · `score_band`406 · `knowledge_tier`396 · `group_feature`335 · `trend_tracking`302 · `subject_strength`241 · `diagnostic_report`241 —— 每文件负责一种报告的「查数+算指标+拼 HTML 片段」 |
| **意图/路由/抽槽**（6） | `orchestrator`2181 · `intent_router`1522 · `clarification`990 · `intent_detect`459 · `slot_merge`338 · `entity_resolve`319 |
| **问句解析/规则**（5） | `query_parse`3040（108 个 `is_*` 正则谓词）· `query_frame`426 · `scope_guard`312 · `question_parser`99 · `dimension_parse`60 · `line_offset`42 |
| **统计计算（纯函数）**（3） | `stats`853（`compute_score_stats`:54 核心 KPI 引擎，另有 ranking/at-risk/correlation/imbalance/趋势）· `aggregation`235 · `cross_analysis`86 |
| **SQL / 数据访问**（6） | `sql_lint`1269 · `score_indicator`606 · `schema_mapping`331 · `filter_peek`231 · `kpi_sql`209 · `data_adapter`129 |
| **图表 / 模板**（2） | `charts`628（15 个 `_register_builder` 注册的 ECharts builder）· `templates`284 |
| **工具层 / API**（2） | `tools`7858 · `api`1942 |
| **配置持久化**（4） | `config`314 · `anomaly_persistence`165 · `config_store`112 · `models_anomaly`42 |
| **权限 / 隐私**（4） | `privacy_mode`217 · `school_cipher`189 · `student_privacy`147 · (+`scope_guard`312 归上) |
| **数据导入**（3） | `raw_import`1819 · `score_import`1106 · `paper_parser`452 |
| **告警**（2） | `alert_service`901 · `models_alert`75 |
| **上下文 / Prompt**（2） | `summary_context`1439 · `prompt_context`199 |
| **其他**（6） | `report_edit`164（建议区回写）· `report_quality`82（空壳检测）· `class_track`77 · `capability`57 · `report_types`163 · `__init__`30 |

**评估**在模块外：`src/agent/edu_e2e_eval.py`、`tests/agent/test_edu_e2e_eval.py`、`test_edu_nl2sql_accuracy.py`，配合 `report_quality.py` 做质量断言。

---

## 3. 关键数据结构

| 结构 | 位置 | 说明 |
|---|---|---|
| `ReportType`(str,Enum) | `report_types.py:17` | **19 个取值**：`class_overview` `grade_comparison` `subject_diagnosis` `student_profile` `trend_tracking` `tier_alert` `group_feature` `comprehensive` `diagnostic_report` `line_reach` `subject_avg` `assign_grade` `rank_bucket` `contribution` `combo_reach` `elite_roster` `score_band` `subject_research` `difficulty_curve`（值即模板文件名） |
| `Audience` | `report_types.py:44` | 6 值：`principal` `grade_head` `head_teacher` `subject_teacher` `parent` `default` |
| `REPORT_TYPE_LABELS` | `report_types.py:56` | 类型→中文角标；`format_report_display_title`:113 / `strip_report_type_markers`:96 是标题关键函数 |
| `ReportSpec` | `report_types.py:140` | `report_type` + `audience` + `filters: dict[str,str]`（`exam_id/class_name/grade/subject/student_name/scope/district/question`）+ `include_charts` |
| `ReportRoute`(frozen) | `intent_router.py:309` | `needs_report` / `report_type` / `confidence` / `reason` / `source(llm\|fallback\|hard)` |
| `QueryFrame` | `query_frame.py:210` | `mentions: list[EntityMention]` + `scope` + `answer_dimensions` + `exam_window: ExamWindow` + `diagnostics`；配套 `EntityMention`:120、`EntityRole/EntitySource/EntityStatus`:15/20/27；`to_filled()` 兼容旧 slot 字典；`push_constraints/reset_constraints`(:358/:363) 用 `ContextVar` 跨层传约束 |
| `ReportResult` | `orchestrator.py:245` | `html/spec/template_name/data_keys/stats/charts/error` |
| `ScoreSchemaMapping` | `schema_mapping.py:76` | 见 §4；`EducationSchemaMeta`:98、`EducationSchemaBundle`:109 |
| `NormalizedScoreRow` | `data_adapter.py:16` | 宽表/分表归一化后的长表行（student/exam/subject/score） |
| `EducationConfig` / `AnomalyRule` | `config.py:115` / `:44` | 阈值 + 五类异常规则参数 |
| `MetricSpec` | `metric_catalog.py:33` | 认证指标 `score_kpi`/`line_reach_count`/`subject_strength`/`overview_total` |
| `EduAnomalyAlert` / `EduAnomalyConfig` | `models_alert.py:13` / `models_anomaly.py:13` | 告警待办表 / 配置表（JSONB `rules_json`） |
| `EduIntent` / `SlotMergeResult` / `RequiredSlotResolver` | `intent_detect.py:156` / `slot_merge.py:30` / `clarification.py:705` | LLM 抽槽结果 → 合并 → 必填矩阵 |

> ⚠️ 模板 `data` 字典**没有 TypedDict**，全程 `dict[str, Any]`；键名靠 `templates._REQUIRED_KEYS`(:58) 与模板占位符约定，是该模块最大的隐式契约。

---

## 4. 数据访问层

**schema_mapping 两种模式**（`schema_mapping.py:76`）：
- `mode="wide"` → `table` + `subject_columns{科目:列名}`（一行多科，如 `student_score`）
- `mode="normalized"` → `tables{}` + `fields{}` + `joins[]`（`tb_score`/`tb_student`/`tb_exam`/`tb_class` JOIN）

`source` ∈ `{config_edu, inferred_wide, inferred_normalized, default}`，`resolve` 优先级：`config/education_schema.json`（或 `EDU_SCHEMA_CONFIG_PATH`，`load_schema_from_config`:126）> 启发式（`infer_normalized_mapping`:269 / `infer_wide_mapping`:242）> 内置宽表默认。生产实际配置为 `config_edu` + `normalized`（见 `config/education_schema.json`，含 14 张表与 23 条 JOIN；`student_name` 直接映射为 `sc.student_id`——**脱敏即设计**）。`validate_mapping_against_schema`:176 只告警不阻断。

**SQL 生成**：
- 权威 KPI：`kpi_sql.py` — `build_kpi_aggregate_sql`:60（`WITH base/meta` + `FILTER` 分段，**无 LIMIT**，Python 复算口径对齐 `stats.compute_score_stats`）、`build_primary_exam_id_sql`:31、`build_score_count_sql`:44、`kpi_row_to_stats`:117、`append_exam_id_predicate`:189。
- 行级 SQL：`orchestrator._build_sql`:1791 / `_build_sql_config_edu`:1813 / `_build_item_diagnosis_sql`:1861 / `_build_knowledge_diagnosis_sql`:1893。
- **散落的手拼 f-string SQL**：`tools.py` 31 处、`raw_import` 22、`score_import` 19、`student_exam` 14、`orchestrator` 14、`subject_research` 13、`bureau_analysis` 12、`api` 11。
- 参数化/转义：全靠 `\' → \'\'`（`kpi_sql.py:191`、`api._sql_quote`:677、`metric_catalog._sql_quote`:87、`filter_peek._sql_quote`:30），无统一层。
- **`kpi_sql.py` 的边界**：只覆盖 `tb_score`/`tb_school`/`tb_exam` 的**学生级单行 KPI 聚合**；其余报告专用 SQL 全部散落在报告实现文件内（`bureau_analysis.py:220-446`、`subject_research.py:434-698`、`difficulty_curve.py:561-633`、`line_reach_report.py:102/109`、`score_indicator.py:75-114` 的 DDL、`filter_peek.py:133-189` 的 DISTINCT 探查），要收敛 SQL 至少还需迁移 7 个文件。

**配置持久化层级**（高→低）：`config_store.get_config():64` → `_load_db_config():40` → `anomaly_persistence.load_config_from_db:67` → `_ensure_row:43`（无行则用 `config.load_config():213` 播种）→ 失败回落 `config.load_config()`（默认值 ← `config/education.json:31` ← 环境变量 `EDU_*:231-253`）→ 最后叠加进程内 `_override:69`。写：`config_store.update_config:79` → `apply_partial_to_config:111`（比例优先，`_sync_abs_from_ratios:19`）→ `save_config_to_db:81`。表：`edu_anomaly_config`（`models_anomaly.py:19`，JSONB `rules_json:35`），启动播种在 `common/core/database.py:48-56`；API 入口 `api.py:75/:81/:87`。

**脱敏链路**：开关读 DB `sys_edu_privacy`（`privacy_mode.py:55-64`，5 s 进程缓存 `:21`，读失败 **fail-closed** 返回 True）；`apply_sql_privacy:114` = `school_cipher.rewrite_sql_school_s_name:115`（`s_name`→`name`）+ `student_privacy.rewrite_sql_student_name_cols:73`（`xm`→`student_id`）；`apply_result_privacy:131` 再做列级剔除（`sfzh/ksh` 恒删）。

**执行**：`tools._run_edu_sql`:137 / `api._build_orchestrator.execute_sql`:129 都走 `execute_sql_with_permission_by_user_id`（行列级权限）。写路径：`score_import._build_upsert_sql`:756（PG `ON CONFLICT DO UPDATE` / MySQL `ON DUPLICATE KEY`）、`raw_import` 走 `WriteDbSession.execute_upsert_batch`:753；写入表白名单 `score_import._WRITE_TABLE_ROLES:297`。

**注入点（callback）**：
- `ExecuteSqlFn = Callable[[str], Awaitable[dict]]`、`ResolveSchemaFn = Callable[[], Awaitable[ScoreSchemaMapping]]`（`orchestrator.py:66-69`），真实实现在 `api.py:129/:151`，测试用 mock —— 这是**唯一的正式扩展点**。
- 隐私改写钩子：`privacy_mode.apply_sql_privacy`:114 / `apply_result_privacy`:131，挂在 `business.py:1921/:1966/:1991` 与 `schema_mapping.py:169-172`（`overlay_schema_fields`/`overlay_table_comments`）。
- 图表注册表：`charts._register_builder`:54 + `_CHART_BUILDERS`:21。
- 工具注册：`tools.EDUCATION_TOOLS`:7780 → `business.build_default_toolpack`:2359（`business.py:2343/2355`）。

---

## 5. 扩展点清单

**新增一种报表类型**（8 处，缺一即静默回退）：
1. `report_types.py:17` 加枚举 + `:56` 加中文标签
2. `templates.py:20` `_TEMPLATE_PATH` + `:58` `_REQUIRED_KEYS`（复用 `bureau_analysis.html` 需同步 `:187-195` 别名块）
3. 新建 `templates/education/<x>.html`
4. `tools.py` 加 `@tool() build_<x>_report_data_tool`，注册进 `EDUCATION_TOOLS:7780` 与 `__all__:7820`
5. `intent_router.py`：`_REPORT_TYPE_DEFS:50`、`_FALLBACK_KEYWORDS:73`、`_POSITIVE_HINTS:162`、`_NEGATIVE_HINTS:242`、`_TIE_BREAK:267`、`EXPECTED_PLAN_TOOLS:1250`、`plan_items_for_report_type:1308`
6. `query_parse.py` 加 `is_<x>_report_query()`
7. `src/agent/expand/planner.py` 加 `build_<x>_plan_items()` 并回填步骤 5 的 import
8. （仅 API 路径）`orchestrator.py` `_BUREAU_TYPES:82`/`_SINGLE_EXAM_KPI_TYPES:72` + `_gather_data:346` 分支 + `_fill_<x>`

**新增一个指标**：`config.py` `EducationConfig` 阈值字段 + `resolved_segments` → `stats.py` 纯函数 → `kpi_sql.py` 聚合列 + `kpi_row_to_stats` → `templates._REQUIRED_KEYS` → 若为「事实问」还需 `metric_catalog.py` `MetricSpec`:40 + `compile_metric_sql`:132 + `tools.query_certified_metric_tool`:7679。

**新增一个 tool**：`tools.py` 加 `@tool()` 函数（遵循 business.py「业务失败不抛」：返回 `ToolResult(content=错误, data={"error":...})`）→ 加入 `EDUCATION_TOOLS` → 自动进 `build_default_toolpack`。若需被 Planner **确定性调度**（不走 LLM 自由选工具），还须挂到 `EXPECTED_PLAN_TOOLS` 并在 `planner.py` 生成对应 sub_task 文案。

---

## 6. 代码坏味道与风险

**重复实现**
- `_fmt` 在 **10 个文件**各写一遍：`bureau_analysis:717`、`comprehensive:128`、`diagnostic_report:25`、`group_feature:23`、`knowledge_tier:21`、`orchestrator:1961`、`school_intervention:31`、`student_exam:21`、`subject_diagnosis:22`、`subject_research:126`；`_num` 12 处、`_now_str` 3 处、`_sql_quote` 3 处、`_cell` 5 处、`_esc` 3 处。
- SQL 字面量/转义工具 `_str/_esc/_sql_lit/sql_lit` 在 **7 个文件**各一份：`bureau_analysis:112`、`subject_research:98/104`、`demo_report:421/868`、`difficulty_curve:557`、`line_reach:149`、`line_reach_report:56`、`score_indicator:139`。
- 内联 HTML 卡片/表格拼装：`_kpi_card` 四处重写（`diagnostic_report:33`、`group_feature:50`、`line_reach_report:408`、`bureau_analysis:743`），`_table` 在 `bureau_analysis:725`、`diagnostic_report`、`subject_research:1471`、`line_reach_report:421`、`group_feature:37` 各一套。
- **同一段骨架逐字复制**：`comprehensive.py:652-658` 的 exam_order 去重循环被 `student_exam.py:553-559` 与 `trend_tracking.py:134-156` 各抄一遍；`student_exam.py:8` 甚至直接 import `comprehensive` 的私有函数（`_normalize_records`/`_resolve_exams`/`_exam_has_valid_data`）。
- `demo_report.school_city_level_*_sql:447/467/500/516` 与 `bureau_analysis.school_city_avg_sql:303` 同口径重写。
- `_map_sql_outside_string_literals` 在 `student_privacy.py:42` 与 `school_cipher.py:82` **逐字复制**（两份同构的 SQL 字面量感知扫描）。
- `_looks_like_school_id` 三份：`entity_resolve.py:39`、`scope_guard.py:167`、`tools.py:1575`。
- **同一报告类型双实现**：`orchestrator._fill_*`（API 路径）vs `tools.build_*_report_data_tool`（chat 路径），如 `_fill_group_feature:1541` ↔ `build_group_feature_report_data_tool:3668`，二者字段集与算法已漂移。

**死代码 / 临时补丁**
- `demo_report.py`（**1432 行**）是硬编码演示快照：`_yangzhou_hardcoded_snapshot:213`、`build_yangzhou_jan_level_html:1382`、`yangzhou_jan_demo_tool_payload:1410`、`is_yangzhou_jan_level_demo_query:113`、`DEMO_YANGZHOU_JAN_QUESTION:20` 均**无生产调用者**；`agent_runner.py:2743` 只用真正查库的 `is_school_city_level_query` / `load_school_city_level_snapshot` / `school_city_level_tool_payload` 三个入口。
- `capability.detect_available_dimensions` 在 `tools.py:38` 只 import 未调用；`filter_supported_dimensions:44`、`student_privacy.filter_schema_fields:37`、`aggregation.aggregate_hierarchy:106` 生产零调用（仅测试）。
- `sql_lint.lint_edu_sql:810` 只是 `lint_edu_sql_blocks` 的别名；`subject_research.py:567 detail_select_sql` 是兼容旧名的转发桩；`difficulty_curve.item_not_found_message:333`/`sub_split_note:345` 仅内部调用；`question_parser.py`/`paper_parser.py` 仅被 `scripts/` 引用。
- 地域硬编码：`query_parse.py` 17 处、`demo_report` 12、`bureau_analysis` 8、`intent_detect` 5 处固定城市/学校名。
- 密钥硬编码且两套算法分叉：`school_cipher.py:32` 与 `raw_import.py:34` 都是 `b"yz_edu_k1"`；`encode_school_name:53`（小写 `gz_`）与 `raw_import._encode_school_token:235`（大写 `GZ_`）同算法不同前缀，注释自认易混（`raw_import.py:236-240`）。

**Bug 风险**
- 手拼 SQL + 手工单引号转义，转义实现分散 4 处且不一致（`metric_catalog.py:148` 还用 `str.replace` 换表别名，列名含子串会误替换）；`raw_import._sql_like_prefix:582` 转义 `%_` 但 SQL 无 `ESCAPE` 子句。
- **77 处 `except Exception`**（`orchestrator` 22、`tools` 17、`sql_lint` 11）；`orchestrator.py:494/505` 直接 `pass`，`run_spec:336` 兜底后返回空 html，只能靠 `report_quality.py` 事后识别「空壳报告」。
- `sql_lint.py` 内 10 处 `except Exception`（`:213/:258/:508/:914/:951/:970/:984/:1022/:1103/:1184`）会让 AST 规则**静默失效**——护栏本身不可靠。
- `config_store._override`（`:23/:69`）永不失效，外部改 DB 后进程内旧值优先；`update_config:79` 只回填 `_FLOAT_KEYS`+rules（`:89-92`），`score_segments` 等不落 override。
- 旁路缓存不同步：`config_store.get_config():64` 返回 DB 对象，`alert_service.py:666` 浅拷贝后改 `pass_threshold`，`anomaly_rules` 列表仍与缓存共享。
- **模块级可变状态**：`score_band.py:47 _chart_ids = itertools.count(1)` 并发下图表 id 会漂移。
- **写死业务常量**：`score_band.py:33-34` 硬编码扬州区县/校类名单；`demo_report.py:490-493` 写死 600/650；`knowledge_tier.py:68`/`:32` 写死 60 分；`difficulty_curve.py:41-51` 写死各科满分；`query_parse.py` 17 处固定城市/学校名。
- `config.py:31` `Path("config/education.json")` 相对 CWD；`score_import.py:56` 用 `parents[3]`。
- `datetime.utcnow()`（naive）散落在 `alert_service.py:281/283/298`、`anomaly_persistence.py:59/75/97`、`models_alert.py:65/72/73`、`models_anomaly.py:41`，与 `alert_service.py:43` 手工 +8 小时强耦合。
- `templates._REQUIRED_KEYS` 只定义 13 个类型，6 个 bureau 类型靠 `:187-195` 复用 `SUBJECT_AVG` 的键 → 模板字段契约脆弱。
- 隐私常量散落：`privacy_mode.py:23` `sfzh/ksh` 恒删、`:24/:142` 匿名时另删 `xh/s_name`；`school_cipher` 与 `student_privacy` 各有一份同构的「跳过字符串字面量」扫描。

---

## 7. 文件行数 Top 20（共 46 659 行 / 64 文件）

| # | 文件 | 行数 |
|---|---|---|
| 1 | `tools.py` | 7858 |
| 2 | `query_parse.py` | 3040 |
| 3 | `orchestrator.py` | 2181 |
| 4 | `api.py` | 1942 |
| 5 | `subject_research.py` | 1866 |
| 6 | `raw_import.py` | 1819 |
| 7 | `subject_diagnosis.py` | 1526 |
| 8 | `intent_router.py` | 1522 |
| 9 | `student_exam.py` | 1453 |
| 10 | `summary_context.py` | 1439 |
| 11 | `demo_report.py` | 1432 |
| 12 | `sql_lint.py` | 1269 |
| 13 | `comprehensive.py` | 1127 |
| 14 | `score_import.py` | 1106 |
| 15 | `bureau_analysis.py` | 1025 |
| 16 | `clarification.py` | 990 |
| 17 | `alert_service.py` | 901 |
| 18 | `stats.py` | 853 |
| 19 | `line_reach.py` | 803 |
| 20 | `line_reach_report.py` | 772 |

Top 20 占 34 122 行（全模块 **73%**）；`tools.py` 单文件占 16.8%，内含 36 个 tool、内联 SQL 与 HTML 拼装，是最该优先拆分的文件。

---

## 8. 给后续开发的建议（按优先级）

1. **拆分 `tools.py`**：按报告类型拆成 `tools/report_*.py`，抽出公共 `_run_edu_sql`/格式化/表格 HTML 帮助函数。
2. **消除双实现**：让 `ReportOrchestrator._fill_*` 复用 `tools.build_*_report_data_tool`（或反之），否则每加一个报告类型都要改两处。
3. **抽 `sql_safety.py`**：统一 `sql_quote`、标识符白名单（已在 `score_indicator._SAFE_IDENT:33`、`api._ident:869` 有雏形）与 `sql_lint` 的静默降级。
4. **上移公共小工具**：`_fmt/_num/_now_str/_cell/_esc/_kpi_card/_table` 收敛到一个 `edu_format.py`。
5. **删除或隔离 `demo_report.py`**：把硬编码快照清出主流程。
6. **为模板 `data` 定义 TypedDict**，把 `templates._REQUIRED_KEYS` 升级为类型契约。
