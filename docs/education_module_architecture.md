# 教育模块架构文档（Education Module Architecture）

> 面向主力开发的深度参考文档。覆盖 `src/agent/education/`（40 个 `.py`，约 27k 行）+ `src/agent/edu_e2e_eval.py`。
> 所有行号以当前 `edu` 分支工作区为准（`git log` 最近提交：`754ca22 fix:team/agent模式、回答优化`）。
> 阅读建议：先看 §1 全景图建立地图，再看 §2 数据流理解两条主线，§3 报告类型表是日常开发高频查询点。

---

## 0. 模块一句话定位

教育模块是**确定性报告管线（Phase 3+）与 ReAct Agent 工具链（Phase 2）双模式并存**的学情分析域：

- **设计哲学**（`__init__.py` L15）：数值由工具算（stats 纯函数）、文字由 LLM 写（Summarizer）、模板管排版（Jinja2）。
- **双入口**：① `education_router`（api.py，HTTP 人用入口）→ `ReportOrchestrator` 确定性流水线；② `EDUCATION_TOOLS`（tools.py，22 个 `@tool`）→ LLM ReAct 循环。
- **数据底座**：教育业务数据源（外部 PG，非平台库）表 `tb_score`/`tb_score_detail`/`tb_exam`/`tb_school`/`tb_exam_question`/`tb_exam_question_knowledge`/`tb_knowledge`/`tb_student`/`tb_fraction_bar`/`tb_score_overview`/`tb_score_indicator`；平台库表 `edu_anomaly_config`（配置单例）、`edu_anomaly_alert`（预警事件）。
- **字段映射**：`config/education_schema.json`（`mode=normalized, source=config_edu`）为固定映射；`ScoreSchemaMapping` 另有 `wide` 宽表模式（`infer_wide_mapping` 启发式）。

---

## 1. 模块全景图（40 文件 + eval）

> I/O 分类标注：**纯**=纯函数无 I/O；**SQL**=生成/执行 SQL；**DB**=直接读写数据库；**LLM**=调用 LLM；**工具**=被 tools.py `@tool` 包装/被 Agent 工具链调用；**API**=仅被 `education_router` HTTP 端点消费。

### 1.1 编排层（Orchestration）

| 文件 | 行数 | 核心职责 | 关键公开符号（行号） | 依赖/被依赖 | I/O |
|---|---|---|---|---|---|
| `orchestrator.py` | 1826 | 确定性报告流水线：意图→查数→统计→图表→渲染 | `ReportOrchestrator`(L218)、`ReportIntentResolver`(L88)、`ReportResult`(L206)、`ExecuteSqlFn`/`ResolveSchemaFn`(L62/64)、`_SINGLE_EXAM_KPI_TYPES`(L67)、`_fill_*` 9 个方法 | import：charts/config/diagnostic_report/knowledge_tier/kpi_sql/query_parse/report_types/schema_mapping/stats/subject_diagnosis/templates；被 api.py、`__init__.py` 使用 | 编排层，SQL 经注入回调执行 |
| `intent_router.py` | 899 | 报告意图路由：`needs_report` + `ReportType`；LLM 分类失败规则兜底 | `ReportRoute`(L241)、`classify_report_intent`(L644,async LLM)、`classify_report_intent_sync`(L701,纯规则)、`fallback_classify_report_intent`(L421)、`should_use_deterministic_report_plan`(L512)、`plan_items_for_route`(L732)、`plan_items_for_report_type`(L747)、`plan_matches_report_type`(L807)、`plan_is_fact_query`(L832)、`coerce_plan_to_route`(L855)、`EXPECTED_PLAN_TOOLS`(L706) | 依赖 report_types/util.json_parser；被 agent_runner(L1053)、planner(L839)、orchestrator(L102)、tools(L3364)、edu_e2e_eval(L50) 使用 | 纯规则 + LLM（async 入口） |
| `report_types.py` | 143 | 报告类型/受众枚举 + ReportSpec | `ReportType`(L17,9 值)、`Audience`(L34,6 值)、`REPORT_TYPE_LABELS`(L46)、`ReportSpec`(L121)、`report_type_label`(L63)、`strip_report_type_markers`(L76)、`format_report_display_title`(L93) | 被全包引用（依赖图根节点） | 纯 |
| `templates.py` | 241 | 报告类型→模板名 + 模板所需 data keys 声明 | `_TEMPLATE_PATH`(L20)、`_ALIAS_TEMPLATE_STEMS`(L33)、`_AUDIENCE_SUFFIX`(L42)、`_REQUIRED_KEYS`(L48,9 类模板字段清单)、`select_report_template`(L193)、`resolve_report_type_from_template`(L155)、`ensure_report_type_in_data`(L176) | 被 orchestrator/tools/business 使用 | 纯（`_template_exists` 做文件探测） |

### 1.2 统计 / 图表 / 聚合（纯计算基座）

| 文件 | 行数 | 核心职责 | 关键公开符号（行号） | 依赖 | I/O |
|---|---|---|---|---|---|
| `stats.py` | 843 | KPI 纯函数引擎（依赖图叶节点） | `compute_score_stats`(L54,核心)、`describe_score_dispersion`(L140)、`compute_rankings`(L298)、`identify_at_risk_students`(L339,三态预警)、`pearson_r`(L38)、`compute_correlations`(L500)、`compute_level_distribution`(L549)、`compute_trend_distribution`(L576)、`compute_top_progress_regress`(L611)、`compute_imbalance_degree`(L652)、`compute_subject_extremes`(L714)、`compute_item_metrics`(L755)、`compute_knowledge_mastery`(L796)、`normalize_segments`(L247)、`score_segment_label`(L228,⚠️不在 `__all__`) | 仅 config；被 14+ 文件引用 | 纯（被 `compute_score_stats_tool` L555 / `compute_rankings_tool` L2786 / `identify_at_risk_students_tool` L2815 包装） |
| `charts.py` | 566 | ECharts option JSON 生成（零依赖叶节点） | `build_chart_option`(L111)、`resolve_chart_type`(L57)、`SUPPORTED_CHART_TYPES`(L33,16 种)、builder 注册表 `_CHART_BUILDERS`(L21) | 零教育模块依赖 | 纯（被 `build_chart_option_tool` L2701 包装） |
| `aggregation.py` | 235 | 多维分组聚合 + 单场 KPI 口径收敛 | `aggregate_by`(L64)、`aggregate_hierarchy`(L106)、`pick_primary_exam_id`(L152)、`narrow_score_rows_to_primary_exam`(L181)、`dedupe_score_rows_by_student`(L195)、`prepare_score_rows_for_kpi`(L219,核心口径)、`DIMENSIONS`(L12,10 维) | config/dimension_parse/stats | 纯（被 `aggregate_dimension_tool` L4548 包装） |
| `kpi_sql.py` | 208 | 无 LIMIT 库内 KPI 聚合 SQL 生成（不执行） | `build_kpi_aggregate_sql`(L59,核心,PG CTE)、`build_primary_exam_id_sql`(L30)、`build_score_count_sql`(L43)、`kpi_row_to_stats`(L116)、`append_exam_id_predicate`(L188)、`SCORE_JOIN_FROM`(L14) | config + stats 私有 `_seg_label/_seg_pairs`(L12) | SQL 生成（执行方=orchestrator 回调/tools `_run_edu_sql`） |
| `score_indicator.py` | 464 | 预测线达线指标落库重算（唯一写库模块之一） | `bars_to_wide_row`(L191)、`agg_rows_to_indicator_rows`(L211)、`ensure_table`(L267,仅 pg)、`list_fraction_bars`(L370)、`upsert_fraction_bar_and_recompute`(L403)、`recompute_exams`(L427)、`recompute_if_bars_exist`(L456) | line_reach 私有(L9-11) + api 私有 `_overview_agg_sql`(L324) | DB 写（tb_fraction_bar/tb_score_indicator）；**API** 仅 |
| `line_reach.py` | 784 | 预测线达线纯函数分析（区县/学校/选科聚合） | `build_line_reach_payload`(L754,总入口)、`aggregate_district_line_reach`(L537)、`normalize_fraction_bars`(L310)、`normalize_overview_students`(L388)、`filter_fraction_bars`(L435)、`remap_agg_rows`(L636)、`payload_from_school_agg`(L662)、`student_total`(L182)、`reached_lines`(L197)、`can_access_line_reach`(L153)、`filter_students_by_scope`(L159) | 仅 EduScope(L13) | 纯；**API** 仅 |

### 1.3 报告构建层（纯函数，输出模板 data dict / HTML 片段）

| 文件 | 行数 | 核心职责 | 关键公开符号（行号） | 被谁依赖 | I/O |
|---|---|---|---|---|---|
| `subject_diagnosis.py` | 1488 | **公共工具箱**：小题/知识点聚合、薄弱识别、HTML 表格/芯片/文案、SQL JOIN 片段 | `build_diagnosis_summary`(L681)、`build_diagnosis_recommendations`(L1173)、`build_item_table_html`(L267)、`build_knowledge_table_html`(L359)、`build_segment_table_html`(L1387)、`build_class_overview_summary`(L1026)、`build_class_overview_recommendations`(L1141)、`identify_weak_knowledge`(L489)、`pick_weak_knowledge_topn`(L507)、`identify_weak_items`(L529)、`enrich_knowledge_rows`(L1450)、`coerce_report_table_fields`(L1416)、`knowledge_names_subquery_join`(L135)、`knowledge_weighted_join`(L155)、`collect_class_names`(L88) | 被 7+ 位置依赖（student_exam/group_feature/knowledge_tier/school_intervention/orchestrator/tools/business.py） | 纯（SQL 片段仅字符串） |
| `comprehensive.py` | 1110 | 综合分析 9 维度一次性组装 | `build_comprehensive_data`(L610)、`build_student_archive_from_score_rows`(L476)、`aggregate_student_item_insights`(L1008) | student_exam/trend_tracking import 其**私有函数**；`__init__.py` L18 包级导出 | 纯 |
| `student_exam.py` | 1453 | 单学生多场深度分析（STUDENT_PROFILE） | `build_student_exam_data`(L536,唯一公开) | tools/orchestrator | 纯 |
| `diagnostic_report.py` | 396 | 结构化诊断（一般 S1/特殊 S2/动态 S3） | `build_diagnostic_data`(L151)、`_exam_avg_trend_from_rows`(L30)、`_student_progress_from_rows`(L56) | orchestrator(L28/910)/tools(L37) | 纯 |
| `trend_tracking.py` | 302 | 成绩趋势报告 | `build_trend_tracking_data`(L112,唯一公开) | tools/orchestrator | 纯 |
| `group_feature.py` | 335 | 群体特征画像 | `build_group_feature_data`(L124)、`classify_group_feature`(L67)、`enrich_group_features`(L100)；⚠️**无 `__all__`** | tools/orchestrator | 纯 |
| `school_intervention.py` | 430 | 学校级重点干预识别 + HTML | `identify_weak_classes`(L32)、`identify_concern_segments`(L103)、`identify_weak_question_types`(L156)、`build_school_intervention_insights`(L183)、`build_class_compare_table_html`(L249)、`build_intervention_section_html`(L292) | tools(L55-59)/orchestrator(L771) | 纯 |
| `knowledge_tier.py` | 396 | 能力层级（ability_level：basic/applied/advanced）**纵向分级**画像 | `build_ability_tier_summary`(L29)、`build_ability_tier_matrix`(L54)、`build_ability_tier_table_html`(L73)、`build_question_type_table_html`(L91)、`build_question_type_compare_chart_payload`(L175)、`build_ability_tier_insight`(L257)、`ABILITY_LABELS`(L9) | school_intervention/orchestrator/subject_diagnosis/student_exam/group_feature/tools/business | 纯 |
| `knowledge_cohort.py` | 558 | 后十名 vs 中位组（±2）知识点差距——按名次切学生群体**横向对比**（区别于 tier 的纵向分级） | `split_score_cohorts`(L50)、`compare_knowledge_by_cohort`(L110)、`build_knowledge_cohort_report_data`(L272)、`render_knowledge_cohort_html`(L461) | 仅 tools `compare_knowledge_cohort_tool`(L5598)；由 `is_knowledge_cohort_gap_query` 强制路由，**不走 orchestrator** | 纯 |
| `cross_analysis.py` | 86 | 二维 pivot + 组间对比 | `cross_analyze`(L11)、`compare_groups`(L56) | school_intervention/diagnostic_report/tools(`cross_analyze_tool` L4589) | 纯（import aggregation **私有** `_float/_row_key` L8） |

### 1.4 解析 / 上下文层

| 文件 | 行数 | 核心职责 | 关键公开符号（行号） | 依赖 | I/O |
|---|---|---|---|---|---|
| `query_parse.py` | 1776 | **解析中枢**：实体抽取 + 意图判定器 + 上游数据回收（反幻觉）；⚠️**不存在 `QuerySpec`/`FilterCondition` dataclass**——解析产物是普通 dict（`build_edu_aware_constraints` L799），规格=report_types.`ReportSpec`，路由=intent_router.`ReportRoute` | `extract_student_target`(L151)、`extract_student_id_target`(L134)、`extract_school_target`(L769)、`extract_exam_name_hint`(L197)、`extract_district_target`(L351)、13 个 `is_*_query` 判定器(L310-737)、`build_edu_aware_constraints`(L799)、`format_scope_constraints`(L859)、`student_matches`(L841)、`extract_score_rows_from_report_data`(L1154)、`resolve_comprehensive_table_input`(L1436)、`resolve_stats_input`(L1502)、`resolve_diagnostic_score_rows`(L1547)、`report_participant_count_conflicts`(L1684)、`sub_task_called_tools`(L1605) | 与 summary_context **循环依赖**（函数内延迟 import）；被 21+ 文件引用（全项目复用最高） | 纯 |
| `schema_mapping.py` | 281 | 宽表/分表双模式映射 | `ScoreSchemaMapping`(L34)、`EducationSchemaMeta`(L56)、`EducationSchemaBundle`(L67)、`load_schema_from_config`(L84)、`infer_wide_mapping`(L196)、`infer_normalized_mapping`(L223)、`validate_mapping_against_schema`(L130) | 被 tools/orchestrator/api/score_import/data_adapter 使用 | 纯 + 文件读 JSON（无缓存，L93） |
| `dimension_parse.py` | 60 | 班级名→年级、学校行→区县 | `parse_grade_from_class`(L15)、`parse_class_only`(L27)、`parse_district`(L32)、`class_matches_grade`(L43) | aggregation/orchestrator | 纯 |
| `data_adapter.py` | 124 | 行归一化为 `NormalizedScoreRow` | `NormalizedScoreRow`(L16)、`normalize_rows`(L48)、`group_by_subject`(L98)、`group_by_class_subject`(L108) | ⚠️**生产代码零引用（仅测试）**，文档与现实脱节 | 纯 |
| `capability.py` | 57 | 可选维度字段可用性检测 | `detect_available_dimensions`(L26)、`filter_supported_dimensions`(L44) | tools(L35) | 纯 |
| `prompt_context.py` | 64 | 教育场景识别 + legacy SQL prompt 注入 | `is_education_question`(L48)、`build_education_prompt_extras`(L56) | chat/api/chat.py(L776) | 纯 |
| `summary_context.py` | 949 | Summarizer 反幻觉：权威 KPI 抽取/对账/改写 | `reconcile_answer_with_artifacts`(L762)、`reconcile_answer_with_artifacts_detailed`(L728)、`extract_stats_authority_data`(L484)、`extract_stats_authority_block`(L781)、`audit_summary_kpi_claims`(L279)、`format_sql_result_authority_notes`(L822)、`format_tool_expert_sub_task_block`(L854)、`format_education_pipeline_footer`(L884)、`sql_looks_row_capped`(L124) | query_parse(循环)/config_store；被 agent_runner/business/query_parse 使用 | 纯（读 config_store 进程缓存） |

### 1.5 隐私 / 持久化 / 预警

| 文件 | 行数 | 核心职责 | 关键公开符号（行号） | 依赖 | I/O |
|---|---|---|---|---|---|
| `school_cipher.py` | 189 | 校名不可逆混淆 + SQL 改写/结果剥离 | `encode_school_name`(L53,HMAC-SHA256)、`rewrite_sql_school_s_name`(L115)、`strip_s_name_from_query_result`(L145) | 被 business.py(L1900/1945/1971) 使用 | 纯 |
| `student_privacy.py` | 147 | 学生姓名列过滤/改写/剥离 | `is_forbidden_student_name_col`(L33)、`filter_schema_fields`(L37)、`rewrite_sql_student_name_cols`(L73)、`strip_student_names_from_query_result`(L100) | 被 business.py(L1836/1946/1975) 使用 | 纯 |
| `config.py` | 314 | EducationConfig + 异常规则推导 | `EducationConfig`(L115)、`AnomalyRule`(L43)、`load_config`(L213,env>JSON>默认)、`build_default_anomaly_rules`(L156)、`resolve_anomaly_rules`(L195)、`config_to_public_dict`(L282)、常量 `ANOMALY_*`/`COMPARE_*`(L34-40) | 全包引用 | 纯 + env/文件读 |
| `config_store.py` | 112 | 配置分层加载 + 进程覆盖缓存 | `get_config`(L64)、`update_config`(L79)、`reset_config`(L96)；`_load_db_config`(L40)/`_save_db_config`(L52) | anomaly_persistence/database | DB + 进程缓存（线程锁 L22） |
| `anomaly_persistence.py` | 165 | 异常规则配置 DB 读写 | `load_config_from_db`(L67)、`save_config_to_db`(L81)、`reset_config_in_db`(L104)、`apply_partial_to_config`(L111,比例↔绝对互推) | config/models_anomaly | DB（edu_anomaly_config） |
| `models_anomaly.py` | 42 | `EduAnomalyConfig` 表模型（单例一行） | 类 L13-42；列：pass_threshold/excellent_threshold/pass_ratio(0.6)/excellent_ratio(0.85)/default_full_score/critical_margin/regression_threshold/imbalance_score_gap/rules_json(JSONB) | database.py L31 注册建表 | DB |
| `models_alert.py` | 75 | `EduAnomalyAlert` 表模型 | 类 L13-75；`dedupe_key` 唯一约束(L21-23)；列含 workspace_oid/datasource_id/school_id/class_name/student_id/exam_id/anomaly_type/title/reason/payload_json/source/status/confirmed_* | database.py L32 注册建表 | DB |
| `alert_service.py` | 852 | 预警检测→报告级事件落库→权限查询/确认 | `detect_and_upsert_for_exam`(L612)、`scan_alerts_after_import`(L710)、`upsert_from_at_risk_payload`(L762)、`list_alerts`(L205)、`get_alert_for_scope`(L249)、`confirm_alert`(L265)、`can_access_anomaly_alerts`(L58)、`build_dedupe_key`(L66)、`upsert_alert_events`(L290) | stats/models_alert/config_store/EduScope；被 api/orchestrator/tools 使用 | DB + 业务库直连 SQL |
| `report_edit.py` | 164 | 报告建议区提取/回写（审核流） | `extract_recommendations_text`(L26)、`replace_recommendations_html`(L37)、`has_recommendations_section`(L48) | 被 chat/api/chat.py(L252) 使用 | 纯 |
| `report_quality.py` | 82 | 报告空壳质量检测 | `report_html_is_sparse`(L32)、`report_html_empty_exam_signals`(L52)、`report_html_has_all_dash_score_table`(L57)、`report_html_quality_issues`(L70) | 被 agent_runner(L2191)/edu_e2e_eval(L116) 使用 | 纯 |
| `score_import.py` | 1104 | Excel 成绩导入（唯一写外部数据源模块）；两种模板 total→tb_score / detail→tb_score_detail（openpyxl，L19-26）；7 步校验链（`validate_and_resolve` L537）；UPSERT：PG `ON CONFLICT DO UPDATE` / MySQL `ON DUPLICATE KEY UPDATE`（L755）、500 行/块、≥800 行 4 线程并行写（L957）、缺学号自动补 `tb_student`（L883） | `parse_excel`(L147)、`validate_and_resolve`(L537)、`preview_import`(L819)、`import_scores`(L993)、`import_result_to_dict`(L1094)、`template_path`(L109)、数据类 ParsedRow/ResolvedRow/ImportErrorRow/ImportResult(L60-99)；⚠️**无 `__all__`** | datasource.db.db（非 SQLAlchemy 模型） | DB 写（tb_score/tb_score_detail/tb_student） |
| `raw_import.py` | 1112 | 教科院原始成绩导入（新入口）：宽表→`tb_score_overview`/`tb_student`/`tb_score`，小题分→`tb_score_detail`；固定写入 `database=edu` 的已登记数据源，无环境配置、不选手动数据源；9 科试卷预检；校管理员整文件拒绝；小题分无学校列回退；成功只扫异常不重算达线 | `preview_raw_overview_import` / `execute_raw_overview_import` / `preview_raw_detail_import` / `execute_raw_detail_import` / `assert_raw_import_role_allowed` / `resolve_edu_datasource_id` | 复用 score_import UPSERT；被 api.py `/raw-score-import/*` 调用 | DB 写（overview/student/score/detail） |

### 1.6 工具 / API / 门面

| 文件 | 行数 | 核心职责 | 关键公开符号（行号） | I/O |
|---|---|---|---|---|
| `tools.py` | 5967 | **22 个 `@tool()` 工具**：ReAct 循环的教育域接口 | `EDUCATION_TOOLS`(L5917,list[FunctionTool])、`__all__`(L5943)；工具清单见 §2.3；核心私有：`_run_edu_sql`(L105,唯一查数漏斗)、`_guard_report_when_fact_query`(L86)、`_fetch_subject_diagnosis_rows`(L1850)、`_html_report_tool_result`(L2411) | SQL（经权限执行器）+ 纯函数包装 + 渲染 |
| `api.py` | 1410 | `education_router`（prefix `/education`，L38，挂 `/api/v1`） | `_build_orchestrator`(L111)、23 个端点（见 §2.4）、`_GENERATE_REPORT_ALLOWED_TYPES`(L291) | DB + orchestrator + 各模块 |
| `__init__.py` | 30 | 包门面 | 导出 7 符号：ReportIntentResolver/ReportOrchestrator/ReportResult/ReportType/ReportSpec/Audience/build_comprehensive_data(L22-30) | —（⚠️src 内无人使用这些导出，潜在死导出） |
| `src/agent/edu_e2e_eval.py` | 506 | team 模式端到端评测 CLI | `score_case`(L107,判分器)、`summarize_results`(L246)、`main`(L501)、`_amain`(L396,CLI) | 读题库 JSON + 真实 LLM/SQL + 写结果 JSON |

### 1.7 依赖拓扑速览

```
report_types / config（根）
   ├─► stats ──► aggregation ──► dimension_parse
   │        └──► kpi_sql（import stats 私有 _seg_*）
   ├─► charts（零依赖）
   ├─► schema_mapping ──► data_adapter（死代码）
   ├─► query_parse ◄──► summary_context（循环，函数内延迟 import）
   ├─► intent_router ──► planner（expand 包）
   ├─► subject_diagnosis ◄── knowledge_tier（循环，延迟 import）
   ├─► comprehensive ◄── student_exam / trend_tracking（import 其私有函数）
   ├─► tools.py（22 @tool）──► business.py（EDUCATION_TOOLS 注册 L2149）
   ├─► orchestrator.py ──► templates / diagnostic_report / knowledge_tier / ...
   ├─► line_reach ──► score_indicator ──(函数级)──► api._overview_agg_sql（循环规避）
   └─► api.py（汇聚 orchestrator + line_reach + score_indicator + score_import + alert_service）
```

**I/O 分类结论**：纯函数无 I/O 18 个文件；SQL 生成 1 个（kpi_sql）；DB 写 3 个（score_import/score_indicator/alert_service+persistence）；LLM 调用 1 个入口（intent_router async）；被 `@tool` 包装 11 个（stats/charts/aggregation/cross_analysis/schema_mapping/templates/query_parse/capability/intent_router + 各报告构建器经全流程工具）。

---

## 2. 核心数据流

### 2.1 确定性管线（orchestrator 路径）

```
用户问题
  │
  ▼
ReportIntentResolver.resolve(question, audience_hint)          [orchestrator L95]
  ├─ classify_report_intent_sync(q) → ReportRoute              [intent_router L701,纯规则]
  │     （needs_report=false 时仅当 type 占位 class_overview）
  ├─ _resolve_audience(q, hint)                                [L143,关键词：家长/校长/班主任/任课]
  ├─ 正则抽 filters：_extract_class_name(L169) / _extract_subject(L177) / _extract_exam(L182)
  │     + query_parse.extract_school_target / extract_district_target / is_citywide_analysis_query
  └─► ReportSpec{report_type, audience, filters, include_charts} [report_types L121]
  │
  ▼
ReportOrchestrator.run(question)  [L253] / run_spec(spec)  [L266]
  ├─ select_report_template(type, audience) → {template_name, data_keys}  [templates L193]
  │     （无模板 → ReportResult.error="模板尚未实现"，不抛异常）
  ├─ await resolve_schema() → ScoreSchemaMapping  [注入回调]
  ├─ _gather_data(spec, mapping)  [L306]
  │    ├─ cfg = replace(self._config)；schema 的 score_segment_ratios 覆盖 cfg  [L323-328]
  │    ├─ config_edu + normalized 时：
  │    │    ├─ where = _config_edu_where_sql(spec, mapping)  [L1660，含 LIKE 模糊]
  │    │    ├─ 单场 KPI 报告(_SINGLE_EXAM_KPI_TYPES L67)且未指定考试时：
  │    │    │    _fetch_primary_exam_id → build_primary_exam_id_sql（人数最多场）[L1413]
  │    │    └─ 指定/锁定考试时 KPI 走 _fetch_kpi_stats [L1434]
  │    │         → build_kpi_aggregate_sql（无 LIMIT 权威聚合）→ kpi_row_to_stats  [kpi_sql L59/L116]
  │    ├─ rows = await _fetch_score_rows(work_spec, mapping)  [L1400]
  │    │    └─ _build_sql(L1452)：config_edu→JOIN tb_score/tb_school/tb_exam LIMIT 50000(L1511)
  │    │                        wide→SELECT 科目列 FROM 表 LIMIT 1000(L1464)
  │    │                        其他→SELECT score FROM score LIMIT 1000(L1472)
  │    ├─ 截断检测：_fetch_score_count vs len(rows) → data_incomplete  [L358-362]
  │    ├─ stats 兜底（无 SQL 聚合时）：prepare_score_rows_for_kpi(L365) → compute_score_stats(L372)
  │    │    （data_incomplete=True 时 kpi_authoritative=False，截断行不覆盖 KPI  [L374-377]）
  │    ├─ charts["SCORE_DIST_CHART"] = build_chart_option("score_distribution", ...)  [L381]
  │    └─ data dict 组装 [L396-438]：REPORT_TITLE/REPORT_TYPE/…/TOTAL_COUNT/AVG_SCORE/…/
  │         SEGMENT_TABLE/SUMMARY/RECOMMENDATIONS + 私有 _stats/_charts/_kpi_authoritative
  ├─ 按 report_type 分派 _fill_*（见 §3 映射表）
  └─ render：business._render_template_html(template_name, data)  [business L312]
       ├─ coerce_report_table_fields 兜底（LLM 填 list 时 ast.literal_eval 转 HTML 表）
       ├─ Jinja2 Environment(StrictUndefined, autoescape=False) 渲染；语法错误回退正则替换
       └─ 上层 _sanitize_report_html 去内联事件/javascript:（XSS 最小清洗）[business L210]
  │
  ▼
ReportResult{html, spec, template_name, data_keys, stats, charts, error}  [L206]
```

**输入/输出类型**：
- 输入：`str` 问题 + 可选 `audience_hint` / `locked_class`（权限锁定班级，L262）
- 中间：`ReportRoute`(frozen dataclass) → `ReportSpec`(dataclass) → `ScoreSchemaMapping`(dataclass) → `execute_sql` 回调返回 `{columns, rows, row_count}` → `list[dict[str, Any]]` 行 → `dict` stats（与 `compute_score_stats` 同构）→ `dict[str, str]` charts（ECharts JSON）→ `dict` 模板上下文（大写下划线 key）→ `ReportResult`
- 失败语义：`execute_sql` 异常被 run_spec 兜住返回 `error` 字段，不 raise（L296-304）

### 2.2 ReAct Agent 链路（tools.py 路径）

```
用户问题 → chat/service/agent_runner.py（agent_mode=team/agent）
  │
  ▼
_run_planner_phase [agent_runner L1034]
  ├─ classify_report_intent(question, llm_client)  [intent_router L644：LLM 分类→失败规则兜底]
  ├─ should_use_deterministic_report_plan → plan_items_for_route（确定性计划，跳过 Planner LLM）
  ├─ 否则 PlannerAgent 生成计划 → coerce_plan_items_if_needed → coerce_plan_to_route（纠偏）
  │
  ▼
DataAnalyst / ToolAgent 执行子任务 → 调 tools.py 的 @tool
  │
  ├─ EDUCATION_TOOLS（22 个）经 business.default_business_tools(L2149) → build_default_toolpack
  │    → ResourceManager.install_default_resources()（lifespan 注册）
  ├─ 查数漏斗 _run_edu_sql(L105) → execute_sql_with_permission_by_user_id
  │    → 行列权限(execute_with_permission) → run_sql_with_auto_fix(sql_auto_fix)
  │    → db.execute_sql 内 check_sql_read（sqlglot AST 拒绝非 SELECT）[db.py L290-302]
  ├─ 运行时上下文注入 tool_runtime_ctx（last_exec_result / report_route 等）
  │    → 工具内 query_parse.resolve_* 从 report_data 还原全量数据（防 LLM 手抄 20 行预览）
  │    → needs_report=false 时 _guard_report_when_fact_query(L86) 拦截报告工具
  │
  ▼
Charter → Summarizer
  ├─ summary_context 组装权威 KPI 块（extract_stats_authority_block / format_tool_expert_sub_task_block / format_education_pipeline_footer）
  └─ 后处理 reconcile_answer_with_artifacts_detailed(L728)：把结论里撒谎的人数/及格线/参考人数改写为权威值
```

**双链路关系**：同一套 `stats/charts/aggregation` 纯函数，一边被 `orchestrator._fill_*` 确定性调用，一边被 tools.py `@tool` 包装供 LLM 调用；`_guard_report_when_fact_query` 保证"事实问不走报告工具"（需 `tool_runtime_ctx.report_route.needs_report=false`）。

### 2.3 tools.py 22 个工具清单

| # | 工具（行号） | 类别 | 输入→输出 |
|---|---|---|---|
| 1 | `resolve_score_schema`(196) | 数据获取 | datasource_id, question → ScoreSchemaMapping dict |
| 2 | `compute_score_stats_tool`(555) | 计算 | scores/exec_result/rows+columns → KPI dict |
| 3 | `fetch_subject_diagnosis_data_tool`(695) | 数据获取(SQL) | school/subject/exam/class → {item_rows, knowledge_rows, score_rows} |
| 4 | `build_subject_diagnosis_sections_tool`(834) | 组装/渲染 | fetch 结果 → ITEM/KNOWLEDGE/SUMMARY/RECOMMENDATIONS 区块 |
| 5 | `build_subject_diagnosis_report_tool`(1200) | 全流程 | 一键科目诊断，is_final=True |
| 6 | `build_chart_option_tool`(2701) | 渲染 | chart_type, data → ECharts JSON |
| 7 | `select_report_template_tool`(2746) | 渲染 | report_type, audience → {template_name, data_keys} |
| 8 | `compute_rankings_tool`(2786) | 计算 | items → ranking+percentile |
| 9 | `identify_at_risk_students_tool`(2815) | 计算 | students → {critical, regression, imbalanced} |
| 10 | `build_tier_alert_report_data_tool`(3077) | 全流程 | 分层预警报告渲染 |
| 11 | `build_group_feature_report_data_tool`(3321) | 全流程 | 群体特征报告渲染 |
| 12 | `build_trend_tracking_report_data_tool`(3499) | 全流程 | 趋势报告渲染 |
| 13 | `build_class_overview_report_data_tool`(3646) | 全流程 | 班级总览渲染 |
| 14 | `build_comprehensive_report_data_tool`(3826) | 全流程 | 综合报告渲染 |
| 15 | `build_student_exam_report_data_tool`(4287) | 全流程 | 学生画像渲染 |
| 16 | `aggregate_dimension_tool`(4548) | 计算 | dimension, rows → {dimension, groups} |
| 17 | `cross_analyze_tool`(4589) | 计算 | dim_a, dim_b → pivot + heatmap |
| 18 | `build_citywide_exam_analysis_report_tool`(4624) | 全流程 | 全市诊断报告 |
| 19 | `build_diagnostic_report_data_tool`(4741) | 组装/渲染 | 结构化诊断报告 |
| 20 | `build_student_subject_diagnosis_tool`(5237) | 全流程 | 单学生科目分析 ⚠️**见 §6.1 严重 bug** |
| 21 | `build_knowledge_tier_sections_tool`(5548) | 组装/渲染 | 能力层级+题型区块 |
| 22 | `compare_knowledge_cohort_tool`(5598) | 全流程 | 后十/中位组知识点对比 |

### 2.4 api.py 23 个端点

| # | 方法+路径 | 函数（行号） | 审计 | 依赖模块 |
|---|---|---|---|---|
| 1 | GET `/report-config` | `get_report_config`(70) | ❌无 auth/audit | config_store/config |
| 2 | PUT `/report-config` | `update_report_config`(76) | ❌无 auth/audit | config_store |
| 3 | POST `/report-config/reset` | `reset_report_config`(82) | ❌无 auth/audit | config_store |
| 4 | POST `/batch-report` | `batch_report`(175) | ✅audit L174 | orchestrator.run_spec/run |
| 5 | POST `/diagnostic-report` | `diagnostic_report`(264) | ✅audit L263 | orchestrator.run |
| 6 | POST `/generate-report` | `generate_report`(319) | ✅audit L318 | orchestrator.run_spec |
| 7 | POST `/save-report-history` | `save_report_history`(399) | 有 auth | chat CRUD（agent_mode="analysis_tool"） |
| 8 | GET `/report-history` | `list_report_history`(519) | 有 auth | chat CRUD |
| 9 | GET `/report-history/{id}` | `get_report_history_detail`(541) | 有 auth | chat CRUD |
| 10 | DELETE `/report-history/{cid}` | `delete_report_history`(567) | 有 auth | chat CRUD |
| 11 | GET `/meta/options` | `list_meta_options`(590) | 有 auth+scope | orchestrator._execute_sql + DISTINCT SQL |
| 12 | GET `/dimensions` | `list_dimensions`(750) | ❌**无任何 auth** | aggregation.DIMENSIONS |
| 13 | GET `/dashboards/line-reach/meta` | `line_reach_meta`(899) | auth+can_access_line_reach | line_reach |
| 14 | GET `/dashboards/line-reach` | `line_reach_dashboard`(938) | 同上 | line_reach + `_overview_agg_sql`(L839) |
| 15 | GET `/fraction-bar` | `list_fraction_bar`(1041) | auth+_deny_student | score_indicator |
| 16 | PUT `/fraction-bar` | `upsert_fraction_bar`(1066) | 同上 | score_indicator |
| 17 | POST `/score-indicator/recompute` | `recompute_score_indicator`(1109) | 同上 | score_indicator |
| 18 | GET `/score-import/templates/{t}` | `download_score_import_template`(1158) | 仅认证 | score_import |
| 19 | POST `/score-import/preview` | `preview_score_import`(1177) | auth+scope | score_import |
| 20 | POST `/score-import/execute` | `execute_score_import`(1212) | auth+scope | score_import + alert_service(L1254) + score_indicator(L1282) |
| 21 | GET `/anomaly-alerts` | `list_anomaly_alerts`(1304) | auth+can_access_anomaly_alerts | alert_service |
| 22 | GET `/anomaly-alerts/{id}` | `get_anomaly_alert`(1351) | 同上 | alert_service |
| 23 | POST `/anomaly-alerts/{id}/confirm` | `confirm_anomaly_alert`(1379) | 同上 | alert_service |

---

## 3. 报告类型体系（ReportType → _fill_* → 意图关键词 → 模板）

### 3.1 完整映射表

| ReportType | 枚举值 | 中文标签 | `_fill_*` 方法 | 模板文件 | 意图关键词（intent_router） | 对应 planner 计划 |
|---|---|---|---|---|---|---|
| `CLASS_OVERVIEW` | `class_overview` | 班级总览报告 | 内联(L459-475) + `_fill_class_overview_rank`(L1254) + `_fill_class_overview_weak_knowledge`(L1375) | `education/class_overview.html` | 班级总览/成绩总览/总览报告/班级成绩/期中分析/期末分析（L117-129,133-140） | `build_class_overview_plan_items` |
| `GRADE_COMPARISON` | `grade_comparison` | 班级横向对比报告 | `_fill_grade_comparison`(L760) | `education/grade_comparison.html` | 横向对比/各班对比/年级对比/班级排名/多维对比（L89-103,141-154） | `build_school_class_comparison_plan_items` |
| `SUBJECT_DIAGNOSIS` | `subject_diagnosis` | 科目诊断报告 | `_fill_subject_diagnosis`(L514) | `education/subject_diagnosis.html` | 科目诊断/学科诊断/小题/逐题/知识点/详细分析（L105-115,155-162） | `build_school_subject_report_plan_items` |
| `STUDENT_PROFILE` | `student_profile` | 学生学情报告 | `_fill_student_profile`(L948) | `education/student_exam_analysis.html`（别名：student_profile.html / student_profile_parent.html / student_subject_diagnosis.html，templates L33-39） | 该生/个人报告/个人画像/学生画像/学号/学生个体（L81-83,163-172） | `build_individual_student_exam_plan_items` |
| `TREND_TRACKING` | `trend_tracking` | 成绩趋势报告 | `_fill_trend_tracking`(L1056) | `education/trend_tracking.html` | 趋势/变化/历次成绩/走势/进退步/折线（L79,173-180） | `build_trend_tracking_plan_items` |
| `TIER_ALERT` | `tier_alert` | 分层预警报告 | `_fill_tier_alert`(L1084) | `education/tier_alert.html` | 预警/临界生/退步生/偏科/分层（L78,181） | `build_tier_alert_plan_items` |
| `GROUP_FEATURE` | `group_feature` | 群体特征报告 | `_fill_group_feature`(L1202) | `education/group_feature.html` | 群体特征/按班级群体/群体对比（L85-87,182-189） | `build_group_feature_plan_items` |
| `COMPREHENSIVE` | `comprehensive` | 综合分析报告 | `_fill_comprehensive`(L1233) | `education/comprehensive.html` | 综合分析/综合报告/多次考试/历次考试/所有考试（L62-77,190） | `build_comprehensive_class_plan_items` |
| `DIAGNOSTIC_REPORT` | `diagnostic_report` | 结构化诊断报告 | `_fill_diagnostic`(L897) | `education/diagnostic_report.html` | 全市/结构化诊断/区县诊断/质量检测（L191）；硬约束 `is_citywide_analysis_query`/`is_structured_diagnostic_query`（L284-285,448-455） | `build_citywide_team_plan_items`（全市）或 `build_school_subject_report_plan_items` |

**规则机制要点**：
- 硬约束单候选：全市/结构化→DIAGNOSTIC_REPORT（conf 0.95, source="hard"）；具名学生学情→STUDENT_PROFILE；知识点分层对比→SUBJECT_DIAGNOSIS（`_candidate_pool` L269、fallback L421）。
- 打分：`_POSITIVE_HINTS`(+2/+1) `_NEGATIVE_HINTS`(-2.5/-1.5) + 探测器加分(+5/+2) + `_TIE_BREAK`(L209) 决胜；无信号回落 CLASS_OVERVIEW（conf 0.45）。
- 拿不准立场：`needs_report=false`（事实问走 SQL，L491-498）。
- 受众 Audience：PRINCIPAL/GRADE_HEAD/HEAD_TEACHER/SUBJECT_TEACHER/PARENT/DEFAULT（report_types L34-42）；家长版模板后缀 `_parent`（templates L42-44）。

### 3.2 模板 data keys（templates.py `_REQUIRED_KEYS` L48）

每类模板声明期望字段清单（供 Agent 校验），公共要求 `REPORT_TYPE`（标准中文名，`ensure_report_type_in_data` 自动纠偏 L176）。规模示例：
- CLASS_OVERVIEW：31 键（TOTAL_COUNT/AVG_SCORE/PASS_RATE/…/SEGMENT_TABLE/SUBJECT_RADAR_CHART/…/WEAK_KNOWLEDGE_LIST/DISPERSION_TIP）
- COMPREHENSIVE：35+ 键（9 个 section：OVERVIEW_KPI_GRID→STUDENT_ARCHIVE_TABLE）
- STUDENT_PROFILE：30+ 键（含兼容旧字段 TOTAL_SCORE/CLASS_RANK/GRADE_RANK）
- 模板目录：`src/agent/resource/templates/education/`（13 个 .html，含 `_base.html`）

---

## 4. 配置与持久化

### 4.1 EducationConfig 字段（config.py L115-153）

| 字段 | 默认 | 含义 | 覆盖方式 |
|---|---|---|---|
| `pass_threshold` / `excellent_threshold` | 60 / 85 | 及格/优秀绝对分兜底 | env `EDU_PASS_THRESHOLD`/`EDU_EXCELLENT_THRESHOLD` |
| `pass_ratio` / `excellent_ratio` | 0.6 / 0.85 | 占卷面满分比例（有 exam_score 时优先） | env（无直接变量，走 API/DB） |
| `score_segment_ratios` | [0.6,0.7,0.8,0.9] | 分数段边界比例 | env `EDU_SCORE_SEGMENTS`(绝对)/schema 元数据 |
| `critical_margin` | 5.0 | 临界生判定半径 | env `EDU_CRITICAL_MARGIN` |
| `regression_threshold` | -10.0 | 大幅退步阈值（负） | env `EDU_REGRESSION_THRESHOLD` |
| `imbalance_score_gap` | 20.0 | 偏科科间分差下限 | env `EDU_IMBALANCE_SCORE_GAP` |
| `score_segments` | [60,70,80,90] | 绝对分数段上界 | env `EDU_SCORE_SEGMENTS` |
| `default_full_score` | 100.0 | 满分兜底 | env `EDU_DEFAULT_FULL_SCORE` |
| `good_ratio` / `low_score_ratio` | 0.70 / 0.40 | 良好/低分率（占满分比例） | — |
| `weak_knowledge_threshold` | 60.0 | 知识点薄弱得分率阈值 | — |
| `anomaly_rules` | None | 显式异常规则列表（None→经典字段推导） | API/DB |

### 4.2 配置分层加载顺序（config_store.py）

```
config_store.get_config() [L64]
  ├─ ① DB：anomaly_persistence.load_config_from_db(edu_anomaly_config 单例行)  [L40-49]
  │     └─ 无行 → _ensure_row 用 load_config() 种子化（无并发锁，L43-64）
  ├─ ② 回落：config.load_config()  [config L213]
  │     env 变量 > config/education.json > 内置默认
  └─ ③ 进程覆盖：_override dict（线程锁 _lock）合并 EducationConfig  [L66-76]
update_config(partial) [L79]：apply_partial_to_config（比例↔绝对互推，anomaly_persistence L111）
  → save_config_to_db → 刷新 _override（float 化 + anomaly_rules 深拷贝）
reset_config() [L96]：恢复代码默认并写回 DB
```

**建表/种子链（dev 路径）**：`database.init_db()` → L31-32 import 注册 `EduAnomalyConfig`/`EduAnomalyAlert` 进 `SQLModel.metadata`（`create_all` 才建表）→ `_ensure_anomaly_config_seed()`（database.py L37/L40-48，表空时调 `load_config_from_db` 写默认规则）→ `_ensure_columns()`（L83-86 为旧表补 `pass_ratio`/`excellent_ratio` 列）。Alembic 迁移对应 `20260721_01/02/03`。

**关键口径**：`apply_partial_to_config`（anomaly_persistence L111-157）——传 `pass_ratio` 以比例为准并同步绝对分；只传绝对阈值反推比例；改 `default_full_score` 用比例重算绝对分；动经典字段重建默认规则。

### 4.3 异常规则

- 默认三条（`build_default_anomaly_rules` L156-192）：critical（及格线±margin）、regression（prev_exam 差 < threshold）、imbalanced（同生科间差 ≥ gap）。
- 显式 `anomaly_rules` 优先（`resolve_anomaly_rules` L195-206），`AnomalyRule` 五类参数（L43-67）：threshold/compare_target/consecutive_n/fluctuation_mode/range_lo(offset)/range_hi(offset)；一期仅 `consecutive_n=1`、`fluctuation=abs` 生效。
- 落库：`edu_anomaly_config`（JSONB rules_json），迁移 `20260721_01_edu_anomaly_config.py` + `20260721_02_edu_anomaly_ratios.py`。

### 4.4 预警事件链路（edu_anomaly_alert）

```
三个触发入口（均 try/except 吞异常，失败不阻断主流程）：
  ① 小题导入成功 → api.py L1250-1268 → alert_service.scan_alerts_after_import(L710)
  ② tier_alert 报告编排 → orchestrator.py L1163-1199 → upsert_from_at_risk_payload(L762)
  ③ ReAct 工具路径 → tools.py L3213-3254 → upsert_from_at_risk_payload(L762)
        ↓
_fetch_score_rows(tb_score) + _fetch_prev_scores(tb_exam 上一场同科)   [alert_service L353/L383，业务库直连 SQL]
        ↓
identify_at_risk_students(stats.py L339，纯函数，三态 critical/regression/imbalanced)
        ↓
_split_at_risk_by_class(L507) → _build_report_event(L530) 聚合为报告级事件
  （anomaly_type="tier_alert"，payload={counts, critical[], regression[], imbalanced[]}）
        ↓
_purge_legacy_student_alerts(L587) → upsert_alert_events(L290)
  （dedupe_key = ws|ds|school|exam|class|subject|source|tier_alert，唯一约束 uq_edu_anomaly_alert_dedupe）
  （已确认保持 confirmed 只刷快照，pending 可刷新 L332-341）
        ↓
查询/确认：list_alerts(L205，先 _consolidate_legacy_to_reports 合并旧数据再过滤)
  → get_alert_for_scope(L249) → confirm_alert(L265)
  权限：can_access_anomaly_alerts(L58) 仅 school_admin/teacher；教育局/学生 _BLOCKED_ROLES(L55)
```

表结构：`edu_anomaly_alert`（models_alert L13-75，迁移 `20260721_03_edu_anomaly_alert.py`）；`source ∈ {score_import, tier_alert_report, manual_scan}`（L49-53）。

---

## 5. 隐私与脱敏

### 5.1 校名混淆（school_cipher.py）

- **入库混淆**：`encode_school_name(name, stage, secret)`（L53-73）= 学段前缀（小学`xx_`/初中`cz_`/高中`gz_`，L37-41）+ HMAC-SHA256(secret, 校名) hex[12:20]（8 hex=32bit，L33-34）。token 同时作为 `tb_school.id` 与 `tb_school.name`，外键 `tb_score.school_id` 存同一 token。secret 默认 `yz_edu_k1`（L32，硬编码）或 env `SCHOOL_CIPHER_KEY`。
- **SQL 改写**：`rewrite_sql_school_s_name`（L115-142）把标识符 `s_name` 改为 `name`（保留表别名），经 `_map_sql_outside_string_literals`（L82-112）跳过字符串字面量。
- **结果剥离**：`strip_s_name_from_query_result`（L145-181）从 `{columns, rows}` 剔除 `s_name` 列。

### 5.2 学生姓名脱敏（student_privacy.py）

- 黑名单列（L14-25）：`xm/姓名/真实姓名/学生姓名/stu_name/stuname/real_name/realname`。
- 三机制：`filter_schema_fields`（L37，schema 隐藏）、`rewrite_sql_student_name_cols`（L73，`xm`→`student_id`）、`strip_student_names_from_query_result`（L100，结果剔除）。
- 说明：SQL 改写**仅处理 `xm`**；`stuname/realname` 只被 schema 过滤与结果剥离覆盖。

### 5.3 生效路径（business.py 的 ReAct 工具）

| 工具 | 改写/剥离点 | 行号 |
|---|---|---|
| `execute_sql` | 执行前 `s_name→name` + `xm→student_id`；执行后剥离 s_name + 姓名列 | L1984-1989 / L2017-2018 |
| `sample_rows` | WHERE 子句改 `s_name`；结果剥离 | L1900-1904 / L1948-1949 |
| `describe_table` | tb_school 删 `s_name` 列 + `filter_schema_fields`；给 `name` 列加"脱敏码，禁止改用 s_name"注释 | L1834-1839 / L1847-1852 |

**注意**：这些改写只作用于 business.py 的 `execute_sql`/`sample_rows`/`describe_table` 工具（LLM 即席查询路径）；tools.py 自己的 `_run_edu_sql`（L105）走 `execute_sql_with_permission_by_user_id` 不经过上述改写——但 tools.py 手写 SQL 只引用硬编码的安全列名（`sch.name`/`sc.student_id`），无 s_name/xm，故无泄露面。

**其它脱敏点**：alert_service `_rows_to_students` 的 `name` 一律填 `student_id`（L475-476）；orchestrator `_build_sql_config_edu` 注释"tb_student 无姓名列（id 即学号），学生标识统一用 student_id"（L1482-1489）；`query_parse.format_scope_constraints`（L859）提示词禁止 `SELECT tb_school.s_name`。

---

## 6. 关键技术细节与潜在坑点

### 6.1 🔴 严重 bug（必然触发）

1. **`tools.py` L5264-5266 NameError**：`build_student_subject_diagnosis_tool`（L5237）签名**没有** `tool_runtime_ctx` 参数（对比其它 11 个工具都在签名里声明，如 L566/848/3092/3329/3511/3653/3784/3840/4303/4754/5609），却调用 `_guard_report_when_fact_query(tool_runtime_ctx, ...)` → 只要 LLM 合法调用该工具即抛 `NameError: name 'tool_runtime_ctx' is not defined`。工具已注册进 `EDUCATION_TOOLS`(L5930) 但**当前不可用**。修复：传 `None` 或补参数。
2. **`api.py` L772-787 脱敏回退漏洞**：`_PRIVACY_COLS = {"xm","s_name","sfzh","ksh"}` 全部被剔除时 `_select_list` 返回 `"*"`（L786），可能重新暴露隐私列。

### 6.2 技术债务清单（按影响排序）

| 类别 | 位置 | 说明 |
|---|---|---|
| **关键词表 3+ 份重复** | query_parse hint 元组 20+ / intent_router `_EXPLICIT_REPORT_HINTS`(L18) `_FALLBACK_KEYWORDS`(L61) `_POSITIVE_HINTS`(L132) `_NEGATIVE_HINTS`(L194) `_CLASS_COMPARE_HINTS`(L221) / prompt_context `_EDUCATION_KEYWORDS`(L11) | 改词必失同步 → 意图路由漂移，最大维护风险 |
| **双 KPI 实现口径分叉** | stats.py（Python，无满分时走绝对阈值 L99-102）vs kpi_sql.py（SQL，恒为满分比例制 L87-108） | 同一配置下 Python/SQL 路径阈值可能不同；orchestrator 的 KPI 权威切换依赖此 |
| **跨模块 import 私有符号 5 处** | cross_analysis→aggregation.`_float/_row_key`(L8)；student_exam→comprehensive.`_*`(L8-16)；trend_tracking→comprehensive.`_*`(L11-17)；score_indicator→line_reach.`_LINE_SUFFIX_LABEL/_WIDE_LINE_RE`(L9-11) + api.`_overview_agg_sql`(L324)；kpi_sql→stats.`_seg_*`(L12) | 上游改名即碎 |
| **循环依赖靠函数内延迟 import** | query_parse↔summary_context、knowledge_tier↔subject_diagnosis、tools↔business | 可读性差，重构易破 |
| **硬编码阈值泛滥** | charts L152-153 markLine 写死"60-70"、L252 pass_line=60；stats L561-563 水平段 85/70/60（docstring 称可配置但未接 config）；line_reach L238-281 线种目录（含"南大线"单校定制）+ 显示优先级；subject_diagnosis 60.0 阈值 7+ 处；comprehensive/student_exam ±3/±5/±8、偏科度 7.0、极差 1.15×/30；school_intervention -5 分/-8pp/12% | 均未收敛进 EducationConfig |
| **脆弱正则/字符串匹配** | orchestrator `_CLASS_RE`(L158) 班级名、`_EXAM_RE`(L163)；line_reach L237 `^(wl\|ls)_(?:score\|socre)_(.+)$`（贪婪吞后缀，"socre"拼写容错）；subject_diagnosis 低分段靠 `"低" in label`(L996)；summary_context 20+ 中文格式正则(L150-212)；report_edit `_HEADING_RE`(L17) 标题白名单；report_quality 中文信号串(L14-21) | 文案/格式一改即静默失效 |
| **SQL 注入面** | kpi_sql `build_kpi_aggregate_sql` 原样拼接 where_sql(L59-80)；`append_exam_id_predicate` 仅转义引号(L188-197)；orchestrator `_filters_to_where` LIKE + `_sql_escape`(L1697)；tools `_esc` 单引号转义(L1517)；alert_service 手工 f-string SQL(L364-367/397-415/446)；score_import 表名/列名插值(L408-499) | 依赖调用方自行消毒；sqlglot 只读校验只挡非 SELECT |
| **死代码/悬挂符号** | data_adapter.py 生产零引用（仅测试）；comprehensive L24 `pearson_r` 死 import；school_intervention `build_intervention_recommendations`(L369)/`append_intervention_to_summary`(L398) 无人调用；report_edit `ReviewStatus`(L12) 仅自用；report_quality L60-62 空 pass 死分支；`__init__.py` 导出无人用 | 文档与现实脱节 |
| **审计/权限盲区** | `@audit_access` 仅 3/23 端点（L174/263/318）；`GET /dimensions`(L749) 无认证；配置 3 端点无 auth | 合规风险 |
| **弃用 API** | `datetime.utcnow()`：models_anomaly L41、models_alert L65/72、anomaly_persistence L59/75/97、alert_service L281/283/298 | Python 3.12 弃用 |
| **并发/原子性** | anomaly_persistence `_ensure_row` 无锁（L43-64，并发首次访问可能多行）；alert_service upsert 查→改非原子(L304-343)、dedupe_key `\|` 拼接碰撞(L77-88)、`list_alerts` 读路径写放大（每次先合并旧数据 L219-224）；score_import 学生补齐与成绩写入分两事务（L1060-1074），并行写部分失败即部分导入 | |
| **杂项** | `__import__("datetime")`：comprehensive L969 / student_exam L1386；`sql_preview` 600 字符硬截断（tools L1924 等 6 处）；`_guard_report_when_fact_query` 8+ 处内联重复；group_feature 无 `__all__` 且泄漏 `_groups/_school_stats` 进模板上下文；query_parse `__all__` 重复导出 `extract_student_target` 两次(L1742/1747)；占位科目名集合重复（comprehensive `_PLACEHOLDER_SUBJECTS` L172 vs trend_tracking `_PLACEHOLDER_SUBJECT_KEYS` L29，键集合不一致）；kpi_sql 分数段比例默认值与 config 双处维护；score_indicator `ensure_table` 仅 pg(L268) 但 `_quote` 处理 mysql 反引号（半吊子跨库）；stats L70 `float(s)` 无防御（非数抛 ValueError，契约依赖调用方过滤——而 data_adapter 已死代码） | |

### 6.3 文档与代码不一致

| 不一致点 | 文档说法 | 代码现实 |
|---|---|---|
| **CLAUDE.md 文件清单** | 列出 28 个 education 文件 | 实际 40 个；漏列 12 个：`alert_service.py`、`models_alert.py`、`intent_router.py`、`knowledge_cohort.py`、`kpi_sql.py`、`line_reach.py`、`report_edit.py`、`report_quality.py`、`school_cipher.py`、`score_indicator.py`、`student_privacy.py`、`summary_context.py` |
| **CLAUDE.md 测试约定** | "Education tests use `MockOrchestrator`" | `tests/agent/` 中**不存在** `MockOrchestrator` 类；实际是每个用例内联 `async def fake_execute/fake_schema` 闭包注入 `ReportOrchestrator` 构造器（如 test_education_orchestrator.py L96-104） |
| **CLAUDE.md 模式表述** | "`schema_mapping.py` handles `config_edu` and `wide` modes" | `ScoreSchemaMapping.mode` 只有 `wide`/`normalized` 两个值；`config_edu` 只是 `education_schema.json` 的 `source` 标注（L109），非模式 |
| **`data_adapter.py` 定位** | schema_mapping/`__init__` docstring 声称它是行归一化出口 | 生产代码零引用，仅测试引用；实际由 query_parse 的 `extract_score_rows_from_report_data`/`_parse_score_rows_from_exec` 直接从 exec_result 回收 |
| **config.py docstring** | "JSON 文件读取留到 Phase 3 再加" | `load_config` 已实现 `config/education.json` 读取（L221-229） |
| **charts.py docstring** | 只列 5 种图 | `SUPPORTED_CHART_TYPES` 实际 16 种（L33） |
| **stats `compute_level_distribution`** | docstring 称阈值可经 config 覆盖 | 代码硬编码 A≥85/B≥70/C≥60（L561-563），未接 config |
| **api.py docstring** | 只描述 3 个配置端点 | 实际 23 个端点 |
| **templates 主模板** | 文档/CLAUDE.md 常提 `student_profile.html` | STUDENT_PROFILE 主模板为 `student_exam_analysis.html`；`student_profile*.html` 为别名（templates L24/L33-39） |

### 6.4 已知"设计性"口径（非 bug，但易踩）

- **LIMIT 截断与 KPI 权威性**：config_edu 行级拉取 LIMIT 50000（orchestrator L1511），`DATA_INCOMPLETE=true` 时 Python 行级 KPI 被标记不可信（L374-377），以无 LIMIT SQL 聚合为准；tools 注释明示"勿按 score DESC 排序，避免截断偏向高分"（tools L1744）。
- **多场收敛**：单场 KPI 报告先 `_fetch_primary_exam_id`（人数最多场）再聚合（orchestrator L341-347）；行级 `prepare_score_rows_for_kpi` = 收敛主考试 + 学生去重（aggregation L219）。
- **`_score_dicts_to_records`**（orchestrator L1755）：扁平行→`{exam, student, subjects, total}` records，是 comprehensive/student_exam/trend_tracking 的统一输入契约。
- **宽表 vs 分表 SQL**：wide 模式只取单科列 `LIMIT 1000`（L1464）；normalized 模式固定 JOIN tb_score/tb_school/tb_exam（L1483-1493），`student_name` 恒等于 `student_id`。
- **`_looks_like_school_id`**（tools L1534）`^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$`：校名 token（`gz_2d2b5c7b`）命中该正则 → 过滤自动走 `sc.school_id =` 精确匹配而非 name LIKE。

---

## 7. 测试覆盖

> 教育测试全部在 `tests/agent/`（**不存在** `tests/education/`），27 个文件、400+ 用例。`tests/` 根与 `tests/agent/` 均无 conftest.py（fixture 全部文件内自建）。

### 7.1 测试文件 ↔ 被测模块

| 测试文件（用例数） | 覆盖模块/行为 |
|---|---|
| `test_education_tools.py`（111，全仓最大） | tools.py 16 个 @tool 直接执行 + 大量内部 helper（`_stats_from_fetch_bundle`/`_subject_diagnosis_student_archive`/`_pick_score_from_score_rows`/`_align_score_rows_to_exam` 等）；文件头 `@pytest.fixture(autouse=True) _isolate_edu_config_from_db`（L47-55）monkeypatch 掉 DB 配置读取 |
| `test_education_orchestrator.py`（26） | ReportIntentResolver 意图/受众/过滤器；orchestrator 全链路（内联 fake_execute/fake_schema 闭包注入）；9 类报告全有模板且可跑通(L148)；execute_sql 异常不 raise(L175)；locked_class 覆盖(L513)；KPI 忽略截断行(L591) |
| `test_education_regression.py`（34） | 统计口径不回归（L25/36）；分数段/年级解析；**Team 子任务 fetch/build 阶段互斥**（`fetch_not_allowed_in_build_subtask` L187、`render_not_allowed_in_fetch_subtask` L212）；**上游数据权威性**（LIMIT 20 预览不得当权威 L287、KPI 用 40 行全量 L465） |
| `test_subject_diagnosis.py`（28） | 薄弱识别、诊断摘要/建议、表格 HTML、`_coerce_row_list` 的 ast.literal_eval 路径、tools 内部 helper |
| `test_student_exam_report.py`（31） | query_parse 学生/学校目标抽取（含学号放宽正则）、build_student_exam_data 各种形态（单科/多科/知识点/热力图） |
| `test_education_api.py`（24） | 配置 CRUD（含 422 校验、改及格线→pass_rate 重算）、/generate-report、/batch-report、报告历史、meta、line-reach/fraction-bar/score-indicator API、学生角色 403 |
| `test_report_intent_routing.py`（24） | intent_router 规则打分 + LLM 分类分支 + 计划对齐/纠偏（`coerce_plan_to_route`） |
| `test_score_import.py`（24） | Excel 解析（新/旧模板）、维度解析、行级权限、upsert SQL、preview/execute API、约束检查 |
| `test_summary_context.py`（20） | 权威 KPI 抽取排序、对账改写（错误及格线→权威值）、scrub 预览人数幻觉、格式块 |
| `test_line_reach.py`（16） | 列别名探测、宽表 unpivot、区县聚合、payload、学生不可见 |
| `test_class_overview_archive.py`（14） | 班级总览模板无档案节、polish 样式注入、离散度分级、rank_info、多科雷达 |
| `test_anomaly_rules.py`（7）/`test_anomaly_alerts.py`（5） | 默认规则与历史阈值一致、三态识别、upsert 去重/保留 confirmed、teacher 班级过滤、legacy 行合并（sqlite 本地建表） |
| `test_kpi_sql.py`（5） | `kpi_row_to_stats` 与 `compute_score_stats` 口径对齐、无 LIMIT、主考试/计数 SQL |
| `test_knowledge_cohort.py`（7） | 队列切分、gap 排序、报告 data/HTML、意图强制路由 |
| `test_edu_e2e_eval.py`（5） | `score_case`/`summarize_results` + report_quality 信号函数（不调 LLM） |
| `test_student_privacy.py`（6） | 姓名列过滤/改写/剥离 + 事实问计划禁明文 |
| 其它 | `test_education_charts`(4)、`test_school_intervention`(5)、`test_report_type_display`(6)、`test_report_edit`(2)、`test_diagnostic_dynamic`(3)、`test_trend_tracking_report`(7)、`test_edu_scope_context`(5)、`test_education_prompt_context`(4)、`test_education_integration`(1,唯一 ReAct 端到端)、`test_score_indicator`(2) |

**相邻层（非 `agent/education` 目录）教育覆盖**：`tests/datasource/test_edu_permission.py`（21 例）与 `tests/system/test_edu_permission_api.py`（4 例）覆盖 `datasource/service/edu_permission.py`（EduScope 解析/谓词生成/SQL 限定）；`tests/agent/test_agent_runner.py`、`tests/agent/test_planner.py` 覆盖 query_parse/planner 的教育集成点（如 `is_school_exam_report_query`、计划纠偏）。

### 7.2 覆盖缺口

1. **完全无测试模块（4 个）**：`capability.py`、`school_cipher.py`（隐私敏感却零测试，建议补 encode 确定性 + 字符串字面量不误改）、`anomaly_persistence.py` + `models_anomaly.py`（**DB 持久化层**——config_store 只测进程内缓存，`test_education_api.py` 明确 monkeypatch 绕开 DB）。
2. **6 个工具无直接执行测试**：`build_class_overview_report_data_tool`、`cross_analyze_tool`（零引用）、`build_citywide_exam_analysis_report_tool`（零引用）、`build_student_subject_diagnosis_tool`（⚠️所以 L5265 的 NameError 未被测试发现）、`build_knowledge_tier_sections_tool`、`compare_knowledge_cohort_tool`。
3. **LLM 路径弱覆盖**：intent_router LLM 分支仅用假返回值测；ReAct 端到端仅 test_education_integration 一条 class_overview 链路。

---

## 附：快速开发指引

- **加新报告类型**（CLAUDE.md 约定 + 现状核对）：① `report_types.py` ReportType 加枚举值；② `intent_router.py` 加关键词/`_TIE_BREAK`/`EXPECTED_PLAN_TOOLS`；③ `templates.py` `_TEMPLATE_PATH` + `_REQUIRED_KEYS`；④ `orchestrator.py` 加 `_fill_*`；⑤ 新建 `templates/education/xxx.html`；⑥ 如需 ReAct 可达，tools.py 加 `@tool` 并注册进 `EDUCATION_TOOLS`。
- **改阈值**：优先走 `GET/PUT /api/v1/education/report-config`（落 `edu_anomaly_config`），不要改代码默认值；注意 `pass_ratio`/`pass_threshold` 互推语义（`apply_partial_to_config`）。
- **查数安全**：新增 SQL 一律 SELECT + 走 `execute_sql_with_permission_by_user_id`；列名/表名插值需标识符白名单（参考 api.py `_ident` L760）；字面量单引号转义。
- **隐私红线**：任何新查询不得 SELECT `s_name`/`xm`/`姓名` 明文列；学生展示一律 `student_id`；新模板建议区用 `data-edu-section="recommendations"` 标记以便审核回写。
