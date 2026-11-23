# 教育权限 V1 → V2 整体迁移与 V1 停用方案

状态：方案（已按 2026-11-23 决策定稿：D1–D5、D7，未执行）  
适用分支：`edu-rev`  
关联文档：[V1 现状](education_permission.md)、[V2 实现与启用](education_permission_v2.md)、[V2 总设计](education_permission_v2_plan.md)

## 决策记录（2026-11-23）

| 编号 | 决策 | 结论 |
| --- | --- | --- |
| D1 | 教师随迁移失去“全市合计” | 接受收窄，教师授权不挂 `city_baseline` |
| D2 | 同名班跨届次 | 不展开，进待确认清单 |
| D3 | 学生学号 → 匿名 ID | 由迁库脚本按 `anon_stu_id / sfzh / ksh` 反查（§3.2.1）；查不到或多条进待确认 |
| D4 | 成绩导入 / 试卷库写 / 预测分数线 / 知识点维护 | 改为平台管理员入口，不再由教育角色授权 |
| D5 | 授权工作空间 | 只绑定学情库所属工作空间 |
| D7 | 成绩库读页面（`/education/score-library`） | 适配 V2：非管理员按 grants 下推范围；平台管理员直通全量（§6.1） |

## 0. 结论先行

V2 已经具备授权模型、查询执行链、管理页和报表/工具适配，但目前是**按账号双轨**运行的：`sys_user.system_variables` 里有 `edu_auth_v2` 文档的账号走 V2，其余账号继续走 V1（四级角色 + SQL 谓词注入）。

“整体迁移、停用 V1”必须同时完成三件事，缺一不可：

1. **数据**：把剩余账号的 V1 范围迁成 V2 grants；
2. **门禁**：让 V1 从授权决策链上彻底摘掉，无授权即拒绝（不回退）；
3. **清理**：删除 V1 的接口、配置、页面、测试与文档。

只做第 1 项、只删接口都不安全：V1 目前在 6 类运行时位置生效；而且 `system/api/auth_deps.py` 的门禁是**反向**的（有 V2 文档的账号会被禁止访问未适配的旧接口），全员迁移后会把成绩导入、学校维护等入口对所有人关死。所以“门禁重构”必须与迁移同版本发布。

## 1. 现状（代码级）

### 1.1 V2 已具备的能力

| 层 | 落点 | 状态 |
| --- | --- | --- |
| 事实源 | `sys_user.system_variables.edu_auth_v2`，模型 `EducationAuthorization`（`src/datasource/service/education_authorization.py`） | 就绪，不新增表、不需要 Alembic |
| 授权编辑 | `/api/v1/permission/edu/v2/*`（`src/system/api/edu_permission_v2.py`）：users/source/bindings/catalog/grants/effective/batch-bind | 就绪 |
| 聊天查询 | `SemanticQuery`：`context_for()` 加载授权 → 模型写完整权限 SQL → 语义证明 → DataSemantic 编译 → 物理证明 → 只读执行 → 前后两次指纹复核（`semantic_query.py::_execute_explicit`） | 就绪 |
| 分析工具 | `ReportScope`（`education_report_scope.py`）把 grants 下推到宿主模板 SQL（`execute_report_sql`） | 报告/历史/元数据/分数线/预警/总览/试卷库已接入；成绩库读页面 P4 接入（§6.1） |
| 测试 | `tests/chat/test_explicit_permissions.py`、`test_explicit_permission_api.py`、`test_permission_contract.py`、`tests/datasource/test_education_report_scope.py`、`tests/agent/test_analysis_v2_scope.py` | 有覆盖 |

### 1.2 V1 仍在生效的位置

| # | 位置 | 作用 | 迁移后处理 |
| --- | --- | --- | --- |
| 1 | `system_variables.edu_role / school_id / class_names / student_id` | 授权事实源 | 迁成 grants 后删除键 |
| 2 | `POST /permission/edu/batch-bind`、`PUT/DELETE /user/{id}/edu-scope`、`GET /permission/edu/{roles,schools,template}`、`POST /permission/edu/effective` | V1 写入口 | 停写 → 删除（或 410） |
| 3 | `query_permission.py::apply_permissions_for_execute` → `build_edu_row_predicates` + `qualify_edu_row_predicates` | SQL 谓词注入 | 删除 |
| 4 | `query_permission.py::strip_city_scope_xx_filters`、`expand_overview_xx_school_code_literals`、`expand_indicator_school_code_literals` | V1 的“聚合放宽/校名展开”改写，让全市合计绕过本校谓词 | 删除（与 V2“宿主不补写不改写”的契约冲突） |
| 5 | `semantic_query.py::_verify_row_filters`、`_prepare_and_execute` | 把 V1 谓词当作必须出现在计划里的义务；无 V2 文档时的旧执行链 | 删除 |
| 6 | `chat/api/chat.py::_edu_out_of_scope_message`、`scope_guard.out_of_scope_question`、`edu_scope_dict_for_user_id` | 中文校名/班名正则预拒绝 + `{"edu_role": "v2"}` 特判 | 删除 |
| 7 | `EduScope.edu_role` 被 7 个模块当授权用：`score_library.py`、`score_import.py`、`raw_import.py`、`alert_service.py`、`line_reach.py`、`exam_library.py`、`api.py` | 角色化读写判断 | 改 `ReportScope.levels/capabilities`；`api.py` 的 `_report_legacy_scope` 是过渡 shim；`score_library.py` 见 §6.1 |
| 8 | `system/api/auth_deps.py::_ADAPTED_EDUCATION_PREFIXES` | 反向门禁 | 重建为“读类/维护类/未分类”三分类 |
| 9 | `system/api/system.py::get_me` 的 `edu_scope`、前端 20+ 处 `edu_role` 判断 | 展示 | 改 `levels/citywide` |

### 1.3 双轨判定点（4 处，必须同时切）

| 判定点 | 现在的行为 |
| --- | --- |
| `SemanticQuery._authorization` / `_fresh_authorization` | 无 V2 文档 → 返回 `None` → 走 `_prepare_and_execute` 旧链 |
| `education_report_scope.load_report_scope` | 无 `edu_auth_v2` 键 → 返回 `None`，调用方回退旧路径 |
| `agent/education/api.py::_load_report_scope` | 同上 |
| `auth_deps.get_current_user` | 有 V2 文档 → 禁止访问未适配的 `/education/*` |

## 2. 停用 V1 的 8 个阻碍

| 编号 | 阻碍 | 证据 / 影响 |
| --- | --- | --- |
| G1 | 仍有账号没有 V2 文档 | 一旦 strict 直接断服 |
| G2 | 门禁反向 | 迁移后 `score-import`、`raw-score-import`、`fraction-bar`、`score-indicator`、`knowledge`、`schools` 对所有带文档账号 403；这些接口内部还依赖 `parse_edu_scope` |
| G3 | 未适配入口 | `/education/score-library` 仍用 `EduScope + is_platform_admin`（P4 按 §6.1 适配）；写入类入口改平台管理员 |
| G4 | V1 SQL 改写 | 删注入/放宽必须等 V1 账号清零，否则直查与报告结果变化 |
| G5 | 数据不可机械映射 | 届次、匿名学生 ID、`city_id`、`school_id` 取值域、多工作空间（见 §3） |
| G6 | 行为收窄 | V1 校长看“全市合计”不注入谓词；V2 中 `CAPABILITIES["city_baseline"].grant_levels == ("district","school")`，教师迁到 `class` 后失去全市对照（D1 已接受） |
| G7 | 前端角色字符串 | V2 账号 `edu_role == "v2"`，侧栏/分析页/成绩库/建议问句里的 `school_admin/teacher` 判断全落空 |
| G8 | 残留 | `config/education_permission.json`、`EDU_PERMISSION_CONFIG_PATH`、`EduScopePayload/EduBatchBindRow/EduEffectiveRequest`、约 56 个 V1 用例、`docs/education_permission.md`、`CLAUDE.md` |

## 3. 迁移规则（V1 → V2）

### 3.1 前置条件（缺一条不进入 apply）

1. `DATABASE_EDU_URL` 可达，`tb_score_overview` 含 `xx/dq/jc/bj/exam_batch_id`；
2. `DATASEMANTIC_BINDINGS` 只有唯一绑定（`source_settings()` 能通过）；
3. `/permission/edu/v2/catalog` 能拉到目录（`validate_grant_catalog` 依赖它）；
4. 已核实 V1 `school_id` 与 `tb_score_overview.xx` 的对应关系（用 `_scope_school_ids` 同样的 id/name/s_name 映射核对）；
5. 迁移窗口内冻结 V1 写入。

### 3.2 映射表

| V1 | V2 | 说明 |
| --- | --- | --- |
| `bureau_admin` | `level=city, city_id=education` | 等价“全量”；city 级不挂对照能力 |
| `school_admin` | `level=school, city_id, school_id` + `capabilities=["city_baseline"]` | 保留“本校明细 + 全市整体基准”；不给 `district_self_rank` |
| `teacher` | 每个班一条 `level=class, city_id, school_id, cohort_id, class_id`，`capabilities=[]` | 多班 = 多条；禁止拼成“学校 IN + 班级 IN”；按 D1 不挂 `city_baseline` |
| `student` | `level=student, city_id, school_id, cohort_id, student_id=匿名ID` | 匿名 ID 由 §3.2.1 反查；`class_id` 可选 |
| 无 | `district` / `grade` | V1 没有，不生成 |

补充规则：

- `city_id` 固定为 `EDUCATION_RESOURCE_ID = "education"`；`normalize_education_grant` 兼容旧的 `education-<16位摘要>` 标记。
- `workspace_oid`：**只取固定学情库所属工作空间**（`sys_datasource.oid`；无该行时用 `--workspace-oid` 显式指定一次并写入报告）。按 D5 不按用户成员关系扩到其他空间；不是该空间成员 → 待确认。
- `datasource_id`：`source_settings()` 返回的唯一绑定 id。
- `revision`：无文档 → 1；已有文档 → `revision + 1`，只替换 `{oid}:{ds_id}` 这一组，保留其他资源。
- `managed_resources`：写入 `{oid}:{ds_id}`，作为“已迁移/撤权”标记，防止回退 V1。
- 生成后逐条 `EducationGrant.model_validate` + `validate_grant_catalog`；任一失败，该账号整体进“待确认”，不写半套。

### 3.2.1 学生匿名 ID 解析（D3）

`anon_stu_id` 由 `{学校码 GZ_xxx}_{SHA256("GZ_xxx:身份证号")[:8]}` 生成（`raw_import._generate_anon_stu_id`），学校码又依赖学校展示名与密钥，**不在迁移脚本里重算**，改为只读反查：

1. 取 V1 的 `student_id`，在学情库按顺序匹配 `tb_score_overview.anon_stu_id` → `sfzh` → `ksh` → `tb_student.id`；
2. 限定在 V1 `school_id` 对应的 `xx` 内，取 `DISTINCT (anon_stu_id, xx, jc)`；
3. 唯一命中 → 生成 `level=student` 授权（`school_id=xx, cohort_id=jc, student_id=anon_stu_id`）；
4. 0 条或多条（跨届次/跨校）→ 待确认，不猜；
5. 隐私：查询只取 `anon_stu_id/xx/jc`，**不读、不记** `xm/sfzh/ksh`；报告只记匿名 ID 与计数。

### 3.3 待确认清单（默认不自动扩权）

1. `school_id` 在 catalog 找不到，或映射到多所学校；
2. 班级名在同一学校命中多个届次（同名班跨届）——按 D2 不展开，列出候选供人工配置；
3. 学生按 §3.2.1 反查不到，或命中多个 `(anon_stu_id, xx, jc)` 元组；
4. `bureau_admin` 但不是学情库空间的成员，无法绑定 `workspace_oid`（D5）；
5. V1 字段互相矛盾（有班级无学校、有角色但范围全空）；
6. 账号已有 V2 文档且与 V1 范围不一致（不覆盖，只报告差异）。

### 3.4 迁移脚本

- 形态：`src/system/cli/migrate_edu_scope_v2.py`，`uv run python -m src.system.cli.migrate_edu_scope_v2`。不用 Alembic data migration——映射需要业务确认、需要 dry-run 报告、需要可重跑。
- 参数：`--dry-run`（默认，产出 `artifacts/edu_v1_to_v2_<ts>.jsonl` 明细 + `.csv` 汇总）、`--apply`、`--user-id`、`--limit`、`--workspace-oid`、`--report-path`、`--force-existing`。
- 事务：单账号一个事务（`with_for_update` 锁 `sys_user` 行）；写 `AuditOperationLog(operation_type="migrate", resource_type="edu_auth_v2_migration")`，只记前后指纹，与授权同事务提交。
- 幂等：已有 `managed_resources` 的账号默认跳过，`--force-existing` 才重算。
- 安全：只写 `edu_auth_v2`，**暂不删 V1 键**（保留回滚能力，删除放 P5）。
- 报告：总数、跳过、成功、待确认（按原因分组）、每账号 V1 范围 vs 新 grants 的可读对比。

### 3.5 脚本级验收

- 逐账号 `load_report_scope()` 返回非 `None`（失败关闭语义正确）；
- `context_for()` 通过（catalog 校验）；
- 抽样：school 集合与 V1 完全一致；class 集合一致或是 V1 子集并已列入待确认；
- 不出现任何账号被扩到 `city` 或跨校。

## 4. 分阶段实施

| 阶段 | 内容 | 完成条件 | 粗估 |
| --- | --- | --- | --- |
| P0 | 盘点：V1 账号/角色分布、字段完整度、D1–D5 落地后的标识映射覆盖率 | 迁移清单可审阅 | 0.5–1 人日 |
| P1 | 迁移 CLI（含 §3.2.1 学生反查）+ dry-run 报告 + 审计；V1 写接口停写（410 或开关） | 报告覆盖 100% 账号，无写入 | 2.5–4 人日 |
| P2 | 门禁重构（读类/维护类/未分类）+ `EDU_PERMISSION_MODE`（dual/strict）贯穿 4 个判定点 | 路由分类测试通过；dual 下行为不变 | 3–4 人日 |
| P3 | 分批 apply（bureau → school → teacher → student）+ 每批冒烟 | 100% 迁移，待确认清单清零或明确接受 | 1–2 人日 + 业务时间 |
| P4 | 未适配入口收口（§6）：维护类改平台管理员；成绩库按 §6.1 适配 V2 | 每个 `/education/*` 有明确归属，成绩库各层级范围正确 | 3–4 人日 |
| P5 | 删除 V1（§5）+ 清 V1 键 | V1 代码/配置/页面/测试归零 | 2–3 人日 |
| P6 | 回归与验收（§8） | A01–A23 抽样 + 金标账号通过 | 2–3 人日 |

合计约 14–21 人日，不含业务确认与真实库金标等待。

## 5. 停用清单

### 5.1 后端 API

- 删除 `src/system/api/edu_permission.py`，并在 `common/router.py` 去掉导入与注册。
- 删除 `system/api/user.py` 的 `GET/PUT/DELETE /{user_id}/edu-scope`。
- `pager_users` 去掉 `edu_role/school_id/school_name/class_names/student_id/edu_remark` 字段（或改 V2 摘要），前端用户表同步删列。
- `system/api/system.py::get_me` 去掉 V1 分支与 `lookup_school_display_name` 调用，统一走 `ReportScope.summary()`。
- 过渡期可保留路由 1 个版本，返回 `410 Gone`（`detail="教育权限已统一至 V2，请使用 /permission/edu/v2"`），让旧前端/脚本失败可见，而不是 404 静默。

### 5.2 服务层

- 删除 `datasource/service/edu_permission.py` 的 V1 能力：
  - `EduScope` 若在 P4 前不能完成入口改造，临时降级为“由 grants 派生、只读、不参与授权判断”的 DTO（放 `education_compat.py`），P5 后删除（D6）；
  - `lookup_school_display_name` / `list_edu_school_catalog` / `match_edu_school` 随 V1 页面删除；仍需展示校名时改用 manifest catalog。
- `query_permission.py`：删 edu 注入、`qualify_edu_row_predicates`、`strip_city_scope_xx_filters`、`expand_overview_xx_school_code_literals`、`expand_indicator_school_code_literals`；同步清理 `execute_with_permission.py` 的调用。
- `semantic_query.py`：删 `_verify_row_filters` 的 edu 部分（保留 `ds_rules` 义务）、删 `_prepare_and_execute` 旧链；`_authorization` 无文档即 `AUTH_SCOPE_OUTSIDE_GRANT`。
- `chat/api/chat.py`、`deepagents_runtime.py`：删 `_edu_out_of_scope_message` / `out_of_scope_question` 调用；`scope_guard.py` 若只服务 V1 则整体删除。
- `deepagents_runtime.py`、`business.py`、`orchestrator.py` 的 `edu_scope_dict_for_user_id` 兜底删除（V2 下 `report_scope` 恒存在）。
- `system/schemas.py`：删 `EduScopePayload`、`EduBatchBindRow`、`EduEffectiveRequest`。

### 5.3 配置 / 环境

- 删除 `config/education_permission.json` 与 `EDU_PERMISSION_CONFIG_PATH`。
- 新增 `EDU_PERMISSION_MODE`（`dual` / `strict`）；`strict` 下 4 个判定点全部失败关闭。

### 5.4 前端

- `pages/construct/permission/edu-permission.tsx`：删 `LegacyEduPermissions` 与 `<details>`（约 600 行），只留 `<EduPermissionV2 />`。
- `src/api/permission.ts`：删 7 个 V1 方法与 `EduScope/EduRoleItem/EduSchoolItem` 类型。
- 角色字符串改造：`side-bar.tsx`、`_app.tsx`、`analysis/index.tsx`、`score-library/index.tsx`、`ChatContentContainer.tsx`、`CoworkChat.tsx`、`new-components/chat/assistant.ts` 改用 `edu_scope.levels / citywide / capabilities`（`/me` 已提供）。
- 成绩库页面：按 §6.1 适配后，侧栏可见性改为“有任一教育授权”。

### 5.5 测试

- 删除/重写 V1 用例：`tests/datasource/test_edu_permission.py`（34）、`tests/system/test_edu_permission_api.py`（4）、`tests/agent/test_edu_school_catalog.py`（8）、`tests/agent/test_edu_scope_context.py`（10，随 `scope_guard` 一起删）。
- 改写：`tests/agent/test_education_api.py`、`test_education_regression.py`、`test_edu_nl2sql_accuracy.py` 中依赖 V1 账号/谓词的用例（改成构造 V2 grants 或平台管理员）。
- 新增：
  1. 迁移映射矩阵（4 角色 × 正常/歧义/缺字段）、幂等、审计、待确认不写入；
  2. strict 模式：无文档拒绝、有文档无 grants 拒绝、越界拒绝；
  3. 路由分类覆盖：遍历 `app.routes`，所有 `/api/v1/education/*` 必须落在显式分类里，否则测试失败；
  4. V1 残留断言：除迁移脚本外，代码不得再读写 `edu_role/class_names` 等键；
  5. 成绩库范围用例（§6.1）。

### 5.6 文档

- `docs/education_permission.md` 删除或改为一页“已停用，见 V2”。
- `docs/education_permission_v2.md` 的“旧入口及回滚”改写为“V1 已移除 + 当前回滚边界”。
- 更新 `CLAUDE.md` 权限段落、`docs/frontend-react-architecture.md` 的教育权限页说明。

## 6. 门禁重构（P2 核心）

用“读类 → V2 范围 / 维护类 → 维护权限 / 未分类 → 拒绝”替代“有 V2 文档就禁旧接口”：

| 类别 | 路由（示例） | 判定 |
| --- | --- | --- |
| 读类（已适配 V2） | `generate-report`、`batch-report`、`diagnostic-report`、`report-history`、`meta/options`、`dimensions`、`dashboards/line-reach`、`overview`、`anomaly-alerts`、`exam-library`、`paper-import`、`score-library`（§6.1） | 需要有效 V2 grants（`load_report_scope` 失败关闭）；平台管理员同样需要业务授权，仅成绩库例外（§6.1） |
| 维护类 | `report-config`、`score-import`、`raw-score-import`、`fraction-bar`、`score-indicator`、`knowledge`、`schools` | 按 D4 只对平台管理员开放；保留功能权限扩展位；与教育 grants 解耦 |
| 未分类 | 其他 `/education/*` | 403 + 告警日志，由测试强制登记 |

配套说明：

- 维护类与业务授权解耦后，平台管理员即使有 V2 文档也能维护——这正是现在被反向门禁挡住的部分。
- 维护类内部仍调用 `parse_edu_scope` 的地方，改为平台管理员直接放行（`is_platform_admin`），随 P5 一起删除 `EduScope` 依赖。

### 6.1 成绩库读页面适配 V2（D7）

现状：`score_library_api._db()` 用 `resolve_edu_datasource_id`（找库名 `edu` 的数据源）+ `parse_edu_scope` + `is_platform_admin`；`score_library.py` 以 `access=all|school` + 校名拼 `score_list_filters`，读 `tb_score_overview`，详情读 `tb_exam`/`tb_score_detail`。

目标：非管理员的范围全部来自 `ReportScope`；平台管理员保留直通全量（业务确认：平台管理需要访问成绩库）；不再读 `edu_role`。

1. **取数源对齐与管理分支**：`_db()` 改用 `source_settings()` / `edu_db_connection()` 的固定学情库；`is_platform_admin` 为真时 `scope=None`（全量），否则 `load_report_scope(session, user, workspace_oid, fixed_ds_id)` 失败即 403。弃用 `resolve_edu_datasource_id`，避免“权限映射在 A 库、页面读 B 库”。管理员直通仅限成绩库读接口，不扩散到聊天与报告。
2. **范围谓词**：新增 `_scope_where(scope, dialect)`，直接复用 `ReportScope.overview_predicate("", dialect)`（不做 sqlglot 改写，避免与 `%s` 占位符冲突），以 `AND` 并入 `list_scores`、`list_filter_options`、`list_score_detail` 的 overview 查询；`scope is None`（平台管理员）或全市授权时不加条件。详情查询本就先查“按范围过滤的 overview 行”拿 `anon_stu_id`，天然受同一范围约束。
3. **参数替换**：`list_scores` / `list_filter_options` / `list_score_detail` 的 `access`、`school_id` 改为 `scope`；`resolve_score_access` 与 `_school_name` 随 V1 删除（校名只用于展示，不再作为权限输入）。
4. **有效期**：查询前与返回前各调一次 `scope.assert_active()`，撤权/到期立即放弃结果。
5. **前端**：`pages/construct/education/score-library/index.tsx` 的 `edu_role` 判断改 `edu_scope.levels / citywide`；侧栏 `canSeeScoreLibrary` 改为“平台管理员或有 city/district/school/grade/class/student 任一授权”。
6. **测试**：
   - 新增 `tests/agent/test_education_score_library_scope.py`：city 全量、district 只本区、school 只本校、class 只本班、student 只本人、过期拒绝、非管理员无 grants 拒绝、平台管理员无 grants 读全量、筛选下拉只含范围内学校；
   - 改写 `tests/chat/test_explicit_permission_api.py` 中“`/education/score-library` 被禁止”的断言：改为验证“无 grants 403、有 grants 可按范围读取”。
7. **兼容**：列可见性与脱敏维持现状（`xm/sfzh/ksh` 的可见性沿用现有列权限/脱敏策略），本项只改“能看哪些行”，不改“能看哪些列”。
8. **审计**：平台管理员的直通读取记一条 `AuditOperationLog`（`operation_type=read`、`resource_type=edu_score_library_admin_bypass`），便于事后核对直通访问。

## 7. 严格模式与回滚

- `EDU_PERMISSION_MODE`：`dual`（默认，V1 仍可读）→ `strict`（无 V2 文档即拒绝）。切 strict 前要求 100% 迁移 + 待确认清单清零或明确接受。
- 回滚：
  1. apply 只增 `edu_auth_v2`，V1 键保留到 P5；回滚 = 切回 `dual`，必要时按快照恢复 `system_variables`；
  2. P5 前导出全量 `system_variables` 快照（`artifacts/sys_user_system_variables_<ts>.jsonl`），脚本提供 `--restore`；
  3. 代码回滚不得恢复 V1 谓词注入或默认放行；V2 故障时宁可停教育查询（fail closed），不回到 V1。
- 审计：迁移、保存、撤销都写 `AuditOperationLog`（`resource_type=edu_auth_v2`，迁移用 `edu_auth_v2_migration`），记录前后指纹，不记录具体范围值。

## 8. 验收

1. **迁移等价性（影子）**：对每个迁移账号，用 V1 `build_edu_row_predicates` 与 V2 `ReportScope.overview_predicate` 在 catalog 上求允许集合，断言 school/class 集合相等；student 允许为子集并单独列出。
2. **功能**：V2 账号跑本班明细、本校汇总、按能力的全市基准、`generate-report`、`report-history`、`anomaly-alerts`、`exam-library`、`score-library`；平台管理员跑 `score-import` 等维护入口。
3. **拒绝**：无授权、越权点名、授权过期、跨空间、仅剩 V1 键的账号。
4. **回归**：`uv run pytest`、`uv run ruff check .`；前端 `npm run typecheck`、`npm run lint`、Vitest。
5. **业务金标**：10 个典型账号（市/区/校/年级/班/生/兼任多班/撤销/到期）在真实库跑一轮，按 `education_permission_v2_plan.md` A01–A23 抽验。

## 9. 决策状态

| 编号 | 决策 | 状态 |
| --- | --- | --- |
| D1 | 教师不挂 `city_baseline`，接受失去全市合计 | 已确认（2026-11-23） |
| D2 | 同名班跨届次进待确认清单，不展开 | 已确认 |
| D3 | 学生按 §3.2.1 反查匿名 ID | 已确认 |
| D4 | 维护类入口只对平台管理员开放 | 已确认 |
| D5 | 授权只绑定学情库所属工作空间 | 已确认 |
| D6 | `EduScope` 作为“仅展示”临时 DTO 活到 P5 | 按推荐执行（不参与授权判断） |
| D7 | `score-library` 适配 V2，平台管理员直通 | 已确认（§6.1） |

## 10. 发布顺序（不可颠倒）

1. P1 迁移工具 + V1 停写；
2. P2 门禁重构 + 严格开关（默认 `dual`）；
3. P3 分批迁移；
4. P4 未适配入口收口（含 §6.1 成绩库适配）；
5. 切 `strict` 观察；
6. P5 删 V1 + 清键；
7. P6 验收。

任何一步失败：切回 `dual`，保留 V1 键，暂停教育查询，而不是放宽权限。

## 变更记录

- 2026-11-23：初稿（盘点 + 迁移规则 + 分阶段 + 停用清单 + 回滚 + 验收）。
- 2026-11-23：按 D1–D5 决策定稿；新增 §3.2.1 学生匿名 ID 反查规则；维护类入口改为平台管理员；新增 D7（成绩库读页面）。
- 2026-11-23：D7 定稿为“适配 V2”；新增 §6.1 适配方案（取数源对齐、`overview_predicate` 下推、测试与前端）；P4 调整为 3–4 人日，总工期 14–21 人日。
- 2026-11-23：D7 补充——平台管理员不要求 grants，可直接查看成绩库全量；非管理员仍按 grants 下推范围。

## 附录 A：如果直接移除 V1 的影响

“直接移除”有三种形式，后果完全不同，不能混为一谈。

| 形式 | 做法 | 后果 |
| --- | --- | --- |
| A. 只删 V1 代码 | 不迁移账号、不加严格门禁，删掉谓词注入与角色判断 | **越权放大**：空 `EduScope` 在多个入口被当作“无限制”，而不是“拒绝” |
| B. 删代码 + 严格拒绝 | 无 `edu_auth_v2` 即 403，但账号没迁移 | 安全但**大面积断服**：所有未迁移账号的教育问数、报告、成绩库、预警全部 403 |
| C. 删代码 + 全量迁移 + 严格 | 即本方案 P1–P5 的目标态 | 可上线 |

### A 的越权路径（必须避免）

1. `query_permission.apply_permissions_for_execute` 去掉 edu 注入后只剩 `ds_rules`；未迁移账号在 `_prepare_and_execute` 旧链与 `execute_sql_with_permission` 报告链上会**无范围执行**。
2. `line_reach.filter_students_by_scope` 把 `edu_role == ""` 当作返回全部学生。
3. 试卷库 `exam_library_api._db` 对空 `EduScope` 不拒绝，只是 `actor.see_all=False`，行为取决于下游过滤是否为空集合。

### B 的断服清单

| 入口 | 空 `EduScope` 后的行为 |
| --- | --- |
| 聊天 `query_semantic` | `_authorization` 返回 `None` → 若同时删了旧链，直接 `AUTH_SCOPE_OUTSIDE_GRANT` |
| 报告 / 批量报告 / 诊断报告 | `_load_report_scope` 返回 `None` → 旧链（若旧链已删则 403） |
| 成绩库 | `resolve_score_access` 抛“未配置教育角色” |
| 异常提醒 | `can_access_anomaly_alerts` 对 `""` 返回 False |
| 成绩导入 / 原始导入 | `assert_import_role_allowed` 抛“未配置教育角色” |
| 试卷库 / 预测分数线 | 角色为空导致私有/本校范围退化或拒绝 |
| 会话与历史结果 | 旧结果无 V2 指纹，按新规则不可读，需重新生成 |

### 启动与运行时错误

`datasource/service/edu_permission.py` 被多处**模块级**导入（`system/api/user.py`、`chat/service/conversation_state.py`、`chat/service/semantic_query.py`、`chat/service/deepagents_runtime.py`、`agent/education/alert_service.py`、`agent/education/line_reach.py`、`score_import.py`、`raw_import.py`、`score_library.py`）。先删模块而不清理导入，至少会让应用在启动/注册路由阶段失败，其余入口在请求时 500。

### 其它影响

- **回滚**：删代码后没有回退路径；若同时清了 `system_variables` 的 V1 键且没快照，范围只能重建。
- **审计**：迁移前的 V1 决策没有授权指纹，删除后无法与 V2 对比，“当时谁能看什么”不可追溯。
- **测试**：约 56 个 V1 用例需删除或改写；`test_explicit_permission_api.py` 里“score-library 被禁止”的断言定义的是要被拆掉的反向门禁。
- **前端**：旧面板约 600 行、7 个 API 方法、用户管理 `edu_role` 列、侧栏/分析页的角色判断都依赖 `/me` 的 `edu_scope.edu_role`；后端一旦不返回，入口会静默消失而不是报错。

### 结论

唯一可接受的“直接移除”是形式 C：**同一发布内完成全量迁移 + 严格拒绝 + 删除旧执行链**。若目标是“尽快下线 V1”，最短安全路径是：冻结 V1 写 → 跑迁移（脚本 1–2 天）→ 立刻切 strict → 观察一周 → 再删代码；这比裸删只多几天，但不会把“限制”变成“没有限制”。

### 现网盘点快照（2026-11-23，只读聚合查询）

- 账号总数 **42**；有 `edu_auth_v2` 文档 **7**；无文档 **35**（34 个仍靠 V1 角色，1 个平台管理员两者皆无；直接移除会让这 34 个账号立刻失去范围约束）。
- V1 角色分布：`bureau_admin` 23、`school_admin` 13、`teacher` 1、`cohort_viewer` 1、`district_admin` 1、无角色 3。
- 其中 `cohort_viewer`、`district_admin` 不在 `_VALID_ROLES` 内，V1 谓词为空 = **当前完全不受限**（现存漏洞，迁移时需人工定级）。
- 无 `student` 账号 → D3 在本环境为空操作，代码仍需保留给其他部署。
- 绑定数据源 id=1（`exam`，oid=1）；`core_datasource` 共 2 条（oid 1/2）；`sys_user_ws` 成员：oid1 37、oid2 4、oid3 1、oid4 1。
- 影响结论：**裸删**会让 9 个 `school_admin` + 1 个 `teacher` 从“本校/本班”放大到“全市”，另 22 个 `bureau_admin` 本就全量（不变）；**严格拒绝**会让 34 个账号的教育入口全部 403。

## 执行记录（2026-11-23，直接移除 V1）

按“直接删除 V1 权限实现”执行，未先做数据迁移；无 V2 授权的账号现在一律拒绝（失败关闭），不会被放大范围。

### 已删除

- `src/system/api/edu_permission.py`（`/permission/edu/*` 五个接口）与 `common/router.py` 注册。
- `src/system/api/user.py` 的 `GET/PUT/DELETE /user/{id}/edu-scope`，用户分页不再返回 `edu_role/school_id/...`。
- `src/datasource/service/edu_permission.py`（角色配置、谓词编译、校名解析、批量绑定、`EduScope` 持久化）与 `config/education_permission.json`。
- `src/agent/education/scope_guard.py`（问句越权快拒、跨层次拒绝、排名行裁剪）。
- `V1 测试`：`tests/datasource/test_edu_permission.py`、`tests/system/test_edu_permission_api.py`、`tests/agent/test_edu_school_catalog.py`、`tests/agent/test_edu_scope_context.py`，以及各测试里的 scope_guard/旧接口断言。

### 已改写

- `query_permission.apply_permissions_for_execute` 只合并 `ds_rules`；删除谓词注入、`qualify_edu_row_predicates` 与三处“聚合放宽/校名展开”SQL 改写。
- `semantic_query`：无发布映射/授权即 `AUTH_SCOPE_OUTSIDE_GRANT`；删除 `_prepare_and_execute` 旧执行链与 `_verify_row_filters` 的 edu 义务。
- `education_source.is_fixed_education_source` + 执行入口校验：固定学情库的直连 SQL 一律失败关闭（校验只放在 `execute_sql_with_permission`，`_load_datasource` 这类配置加载不拦截，否则报告链路连连接都建不起来）。
- 教育报告/分析接口：`_load_report_scope` 无授权即 403，不再回退 V1；`_report_legacy_scope` 仅把 grants 映射为展示用视图。
- `auth_deps`：`/api/v1/education/*` 分为“V2 读类”与“维护类（仅平台管理员）”，不再依据 V1 角色放行。
- `exam_library_api`：无 `edu_auth_v2` 文档即 403，避免空范围走旧只读路径。
- 前端（`frontend-react` 与 `frontend-pro` 两套）：教育权限页只保留 V2 编辑器（各删旧面板 700+ 行及“旧版教育权限”入口）；`permission.ts` 删除 7 个 V1 方法与 4 个类型（`EduScope/EduRoleItem/EduSchoolItem/EduEffectiveResult`）；用户管理去掉“教育角色”列。
- 前端角色判断改为按 `edu_scope.levels / citywide`：`frontend-pro` 删除 `LegacyOverview` 与总览的 legacy 回退（后端 `_legacy_payload` 同步删除），导航/试卷库/成绩库/聊天建议改用层级；`frontend-react` 新增 `api/eduScope.ts`（`eduLevels/isStudentScope/isCitywideScope/legacyRoleOf`）替换侧栏、`_app`、成绩库与建议问句里的 `edu_role` 判断。

### 现状与影响

- 现网 34 个仅靠 V1 角色的账号：教育问数、报告、成绩库、预警、试卷库全部 403；平台管理员仍可访问成绩库（`privileged`）与维护类入口。
- 成绩库读页面尚未做 D7 的范围下推：非管理员按“未配置教育角色”拒绝，平台管理员全量可用。
- 辅助模块（成绩导入、原始导入、试卷库、达线看板）仍保留 `EduScope` 形状的只读视图对象（`edu_scope_view.EduScope`），但生产路径不再从 V1 配置构造它，角色字段只影响展示分支。

### 验证

- `uv run python` 导入 `src.common.router` 及改动模块通过。
- V2 权限相关测试 157 项通过（`test_explicit_permission_api`、`test_explicit_permissions`、`test_permission_contract`、`test_education_report_scope`、`test_education_source`、`test_analysis_v2_scope`）。
- 全量 `pytest`：1199 passed / 117 failed / 11 collection errors；失败集中在既有坏模块（`intent_detect`、`sql_generator` 等缺失）与既有数据校验用例，非本次删除引入。
- 前端 `npm run typecheck` 通过。

## 附录 B：V1 残留审计（2026-11-23）

审计范围：`src/`、`tests/`、`frontend-react/`、`frontend-pro/`、`docs/`、`config/`、`scripts/`、`.env.example`。

### 结论

- **授权链路无 V1 残留**：代码中已搜不到 `edu_permission.py`、`parse_edu_scope`、`build_edu_row_predicates`、`scope_guard`、`out_of_scope_question`、`/permission/edu/{roles,schools,batch-bind,effective}`、`edu-scope`、`EDU_PERMISSION_CONFIG_PATH`、`education_permission.json`、`LegacyEduPermissions`、`_legacy_payload`。
- 唯一写 `system_variables` 的位置是 V2 的 `education_authorization.replace_resource_grants()`。
- 保留的 `edu_role` 均为**展示/提示词词汇**，不参与授权判断。

### 本轮审计发现并修复的真实故障

1. `alert_service`：`can_access_anomaly_alerts(空 EduScope)` 恒为 False，导致所有账号的异常提醒恒为空。已删除该门禁、`_scope_filter_clause` 及 `list_alerts/get_alert_for_scope/confirm_alert` 的 `scope` 形参，改由调用方传入已校验的 `school_ids/allowed_classes`（两者都缺失时失败关闭）。
2. `score_import`：空 scope 使 `assert_import_role_allowed` 拒绝所有人，平台管理员的成绩导入同样被拒。已删除角色校验与 `_check_row_scope`，`_resolve_school_id` 改为不做账号级拦截（入口本身只允许平台管理员）。
3. `api.py`：删除已空壳的 `_raise_if_out_of_scope`、两处 `report_scope is None` 死分支、`_alert_scope_filters`；`_anomaly_scope` 只返回 `(school_ids, allowed_classes, denied)`。
4. 测试：删除 3 个只验证 V1 预拒绝/角色校验的用例；`test_anomaly_alerts` 改用学校/班级过滤参数；`test_score_import` 的满分用例改用平台管理员语义。
5. 文档：`docs/education_permission.md` 改写为“V1 已停用”指路页；更新 `CLAUDE.md`、`frontend-react-architecture.md`、`permission_test_questions.md`、`education_module_architecture.md`。

### 保留项（均不授权）

| 位置 | 内容 | 说明 |
| --- | --- | --- |
| `edu_scope_view.EduScope`、`api.py::_report_legacy_scope` | 由 grants 派生的只读视图 | D6；达线看板/分数线/试卷库仍接收该形状参数 |
| `exam_library.ExamActor.edu_role` | 角色字段（v2 记为 `"v2"`） | 实际范围由 `see_all/school_grants/class_grants` 决定 |
| `raw_import` 的 `scope` 参数与角色分支 | 空 scope 下均不命中的死分支 | 导入入口已由平台管理员门禁 |
| `score_library.resolve_score_access` | 管理员直通、其他拒绝 | D7 适配前保持；非管理员暂时不能用成绩库 |
| `/me` 的 `edu_scope.edu_role="v2"`、`deepagents_runtime` 的 scope 标记 | 兼容标记 | 前端已改读 `levels/citywide`，旧标记保留一个周期 |
| `education_report_scope.legacy_role()` | levels → 旧角色文案 | 供展示与既有表单复用 |

### 已知词汇依赖（非授权，建议后续改写）

`query_parse`（8 处）、`direction_choice`（5）、`clarification`（3）、`subject_research`（2）、`entity_resolve`、`sql_lint`、`score_indicator`、`overview`、`line_reach`、`score_library`、`exam_library`、`raw_import` 仍读 `edu_role` 字符串。V2 账号传入 `"v2"` 时这些分支不命中，只影响提示词与文案，不影响权限；建议在 D7 之后统一改为 `levels/citywide`。

### 数据层

`sys_user.system_variables` 中 34 个账号可能仍保留 `edu_role/school_id/class_names` 旧键。代码已完全不再读取；清键、快照与回滚属于迁移 P5，未在本轮执行。

### 验证

- `import src.common.router, src.main` 通过（应用可启动）。
- 定向测试：异常提醒、成绩导入、V2 权限、范围下推、聊天路由、分析范围共 151 项通过。
- 全量 pytest 仍受分支既有坏模块（`intent_detect`、`sql_generator`、`core.action` 缺失）影响，与本次审计无关。
