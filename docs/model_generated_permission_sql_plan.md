# 模型生成带权限语义 SQL：详细落地方案

日期：2026-11-10。核心实现已落地，实际支持边界及启用步骤见[实现与启用](education_permission_v2.md)；生产发布映射与真实库验收待完成。本文为查询执行的详细设计。

## 1. 确定的架构分工

**由模型生成业务计算和权限条件所在的完整语义 SQL；宿主验证，DataSemantic 编译，宿主执行。**

模型必须写出实际生效的城市、区县、学校、届次、班级、个人及其他强制条件；这些条件不能只出现在解释、JSON 声明或 SQL 注释中。漏写或错写时，宿主拒绝查询并返回可修正错误，模型重新生成。

| 组件 | 职责 | 边界 |
| --- | --- | --- |
| 用户与权限管理 | 维护账号、范围、能力、有效期、可委派范围 | 用户问题与模型输出不能修改授权事实 |
| 宿主授权服务 | 从可信存储构造结构化权限上下文及验证义务 | 提供字段、操作符、授权值和能力约束，不为业务查询自动补权限 SQL |
| 模型 | 按问句和权限上下文生成带权限的语义 SQL，包括汇总、排名、最终输出过滤 | 生成结果始终视为待验证输入 |
| 宿主语义验证器 | 验证每个语义来源的范围、列、聚合、窗口和数据输出路径 | 不依赖另一个模型批准，不使用字符串包含检查代替作用域分析 |
| DataSemantic | 将已验证语义 SQL 编译为物理 SQL，保留输入查询语义 | 不读取用户授权、不识别教育角色、不补充或删除账号权限条件 |
| 宿主物理计划验证器 | 核对发布物绑定、物理来源、过滤位置及输出约束 | 验证失败不执行，也不自动修复权限 SQL |
| 宿主执行与展示 | 只读执行、超时/行数限制、脱敏、结果校验、报告和审计 | 未经验证的物理 SQL、中间池或结果不能作为业务数据返回 |

DataSemantic 编译器为展开语义模型、计算字段、业务视图而生成物理表达式属于正常编译。用户权限谓词来自模型提交的语义 SQL；本期不要求 DataSemantic 增加 `user_id / role / allowed_schools` 等权限 API，也不依赖其提供新的权限证明或 lineage 接口。

宿主允许进行不会扩大读取范围的操作：SQL 标准化、经核验的 schema 限定、只读事务、超时和最终结果行数限制。任何 SQL 层修改后都必须重新验证最终计划。脱敏优先使用已发布匿名字段和结果处理；现有 SQL 隐私改写如需保留，也须进入最终验证，不成为未经检查的后门。

## 2. 当前实现与改造位置

当前 `SemanticQuery.run` 已接收模型 SQL，`_plan` 向 DataSemantic 发送发布绑定和 `semantic_sql`。主要变化在宿主链路：

| 当前行为 | 目标行为 |
| --- | --- |
| Runtime 只提供简化 `edu_scope` 作为提示 | 提供版本化、字段级的权限上下文，绑定当前发布物 |
| 模型可以不写范围，物理 SQL 执行前再调用 `apply_permissions_for_execute` | 模型必须写条件，语义验证通过后才能 `/plan`；新模式不调用行权限补写函数 |
| `_verify_row_filters` 在物理 SQL 上重新构造旧谓词做语法片段对比 | 使用语义验证义务及静态发布映射验证编译结果，支持有限而明确的 SQL 结构 |
| 聚合/排名查询可能删除范围条件 | 模型显式写参照池范围与最终结果范围，验证器检查两者，不能因出现聚合自动放宽 |
| SQL、物理表名随 SSE、结果、历史记录传播 | 普通查询按权限返回必要信息；涉及参照池的物理 SQL 不送入模型或普通用户可访问的事件/历史接口 |
| 独立工具或报告通过旧 SQL helper 查数 | 同一新执行服务验证模型 SQL；未接入的业务读取入口在新模式数据源上拒绝执行 |

现有 `_plan` 协议可以先保持 `{bundle_id, manifest_hash, semantic_sql}`。当前没有确认参数绑定、可信物理 lineage 等外部能力，方案不把这些假定为已存在。

## 3. 权限事实源、映射及上线前提

复用 `sys_user.system_variables.edu_auth_v2`，不新增授权表。使用版本化文档和 grants 数组，按工作空间、数据源存储完整范围记录，包含稳定组织标识、届次/班级标识、能力、有效期和历史区间。角色范围覆盖市、区县、学校、年级、班级，并兼容学生。

一条范围记录是完整元组。多班/兼任授权为多个元组取并集，再与强制规则取交集。统计能力与产生它的授权元组绑定，不能组合成其他学校的额外能力。

业务默认边界仍按总体方案：区县只返回自身全市名次；班级可看本校同届次年级整体基准；年级默认不看其他届次；学校跨校名次默认关闭。未明确的业务能力配置为关闭，新增前更新模板和验收用例。

必须先完成以下发布映射：

| 映射 | 必须核实的内容 |
| --- | --- |
| 学校 | `tb_school.id`、学校码、`overview.xx` 和指标表学校标识的真实关系；名称只作展示 |
| 区县/城市 | 稳定 ID、业务数据源取值、所属城市及考试历史归属 |
| 届次/年级 | `jc` 等现有字段能否稳定表达届次；与学年及考试时年级的关系 |
| 班级 | 学校、届次、稳定班级 ID 及考试时的班级快照；不能仅依赖“高三(1)班” |
| 数据源连接 | `datasource_id + workspace_oid + 发布绑定 + 实际连接` 一一核验；当前单个 `DATABASE_EDU_URL` 不能被误当成任意数据源都可访问 |
| 事实来源 | 粒度、权限键、关联关系、可访问字段、已含的业务过滤、是否含聚合或窗口 |

缺少权限键的模型不能默认作为全量入口。可以发布不含动态用户权限的业务视图来暴露稳定维度和审核口径，但模型仍须在外层或正确的内部层级显式写账号范围。全局聚合后丢失学校/届次键的视图不能承载班级普通查询。

## 4. 两份结构化契约

### 4.1 发布物权限映射：`SemanticPermissionManifest`

由宿主维护，绑定 `datasource_id / bundle_id / manifest_hash / dialect / mapping_version`。与语义模型版本一起评审，不能取信模型提交的映射。

每个语义来源登记：

- `logical_relation`、粒度、逻辑字段和类型、每个范围层级需要的权限键。
- 字段到物理表达式/关联路径的审核映射；聚合、窗口及物化结果的静态边界。
- 来源分类：范围事实、受限组织目录、公共参考数据、已审核业务汇总。未知来源拒绝。
- 可用于普通输出、仅可用于权限过滤、禁止模型访问的字段；字段权限覆盖 SELECT、WHERE、JOIN、GROUP BY、HAVING、ORDER BY、窗口和派生表达式。
- 基准/排名允许的业务指标 ID、公式、固定人群条件、可分组维度、精度和样本门槛。
- 支持的逻辑 SQL 和编译后 SQL 结构版本，已验证的编译器版本/指纹。

这个文件表达如何验证权限，不存账号列表，也不包含自动注入用户条件的视图定义。初期使用仓库配置，例如 `config/semantic_permission_manifest.json`；后续可迁移为应用库中的版本化配置。

### 4.2 请求级权限上下文：`SemanticAuthorizationContext`

宿主在本轮工具调用前从真实账号和授权存储加载，注入模型系统上下文；每次执行时重新读取并比较有效版本。示例只展示一名教师的授权：

```json
{
  "schema_version": 1,
  "policy_version": "edu-policy-v2",
  "authorization_fingerprint": "server-generated-fingerprint",
  "binding": {
    "workspace_oid": 1,
    "datasource_id": 4,
    "bundle_id": "education-reviewed",
    "manifest_hash": "sha256-of-reviewed-publication",
    "mapping_version": 3
  },
  "scope_grants": [
    {
      "grant_ref": "grant-101",
      "city_id": "CITY001",
      "school_id": "SCH001",
      "cohort_id": "COHORT2027",
      "class_id": "CLASS01"
    }
  ],
  "relation_requirements": {
    "edu_scores": {
      "ordinary_required_keys": ["city_id", "school_id", "cohort_id", "class_id"],
      "allowed_metrics": ["subject_mean"],
      "ordinary_grouping": ["exam_batch_id", "subject"]
    }
  },
  "capabilities": [
    {
      "code": "grade_baseline",
      "grant_ref": "grant-101",
      "reference_keys": {
        "city_id": "CITY001",
        "school_id": "SCH001",
        "cohort_id": "COHORT2027"
      },
      "allowed_grouping": ["exam_batch_id", "subject"],
      "allowed_metrics": ["subject_mean"],
      "minimum_sample_size": 10
    }
  ]
}
```

示例中的逻辑表、字段及样本门槛是目标契约示意，尚不是当前发布物或已确认的业务值。正式上下文还需包含历史访问区间、适用强制规则、字段策略和所有必需阈值；阈值未配置完成的能力不得启用。

普通查询可以在已授权元组中选择更窄范围；不能扩大为授权列表以外的 ID。多条授权不得拆成各维度独立的 IN 后形成笛卡尔范围。

首期模型把可信上下文中的标识值写成规范字面量，宿主解析 AST 后核对值，不拼接用户问题到 SQL。若后续验证 DataSemantic 和驱动支持绑定参数，可由模型写参数位、宿主绑定可信值；绑定值不等于补写权限谓词。

## 5. Agent 工具及生成闭环

继续使用一个业务查询工具 `query_semantic`，不增加后端固定生成 SQL 的基准/排名工具。工具签名可兼容现有 `sql: str`，再增加可选的 `authorization_fingerprint` 用于快速识别过期上下文；权限判定仍以服务端身份为准。模型传入能力名、grant_ref 或 query_mode 都只能作为查询意图提示。

`get_analysis_context` 和 `describe_semantic_model` 返回已授权发布目录、字段策略和对应权限义务；需要更新权限时重新读取上下文。`lookup_question_context`、考试/班级候选、自动实体解析执行的模型 SQL 同样经过验证，不因“只查 DISTINCT 名称”跳过权限。

生成要求：

1. 读取已发布来源、字段和当前授权元组，先确认业务目标与考试，不从旧摘要推断权限。
2. 在 SQL 的实际读取层写入相应范围和强制条件。
3. 普通查询聚合前限制本范围；基准查询明确写参照池；自身排名明确写排名后的输出过滤。
4. SQL 提交一次，经语义验证、编译、物理验证后才可执行。
5. 可修正错误返回模型重写，默认同一子任务最多两次权限修正；每次同时消耗已有总查询/模型预算，预算不足时提前停止。成功后停止重试。
6. 真正越权、授权失效、未启用能力、发布映射不可信立即终止或刷新上下文，不鼓励模型寻找绕过写法。

结构化错误示例：

```json
{
  "code": "AUTH_SCOPE_MISSING",
  "phase": "semantic",
  "retryable": true,
  "location": {"scope": "own_scores", "source_alias": "s"},
  "violations": [
    {"key": "cohort_id", "reason": "未在读取聚合前限定授权届次"}
  ],
  "context_fingerprint": "server-generated-fingerprint"
}
```

建议错误码：`AUTH_CONTEXT_STALE`、`AUTH_SCOPE_MISSING`、`AUTH_SCOPE_OUTSIDE_GRANT`、`AUTH_COLUMN_FORBIDDEN`、`AUTH_REFERENCE_NOT_ALLOWED`、`AUTH_RELEASE_SHAPE_INVALID`、`AUTH_QUERY_SHAPE_UNSUPPORTED`、`AUTH_MAPPING_MISSING`、`AUTH_PHYSICAL_PROOF_FAILED`、`AUTH_REVOKED`。

错误只给出该账号有权获知的字段和规则，不返回其他单位名单或底层数据库凭据。业务考试筛选错误与权限错误分别返回。

## 6. 语义 SQL 验证器

### 6.1 不实现任意 SQL 的通用安全证明

采用有边界的 SQL 子集和保守验证器：先解析 AST、绑定列和作用域，再证明授权条件及输出路径。允许语法等价但无法识别的写法返回“查询结构暂不支持”，模型改写。不能因“模型说安全”或“暂时没返回数据”就执行。

首期支持单条 SELECT、显式字段、非递归 CTE、INNER JOIN、有限 GROUP BY、审核后的聚合与窗口、受控 UNION ALL。递归 CTE、动态 SQL、未审核函数、用户函数、LATERAL、复杂相关子查询和无法绑定的列拒绝；外连接、其他集合操作逐种验证后启用，不一次开放整个 SQL 方言。

### 6.2 普通范围证明

对每个事实/受限维度来源，验证其读取到输出的每条有效路径均被授权约束覆盖，并且约束在聚合/窗口之前生效。首期优先要求模型写独立的、带完整过滤的来源 CTE，后续再支持更多可证明的谓词下推形态。

- 允许 `=`、受限 `IN`，以及按完整 grant 元组组合的 AND/OR；额外用户条件与范围条件取 AND。
- 检查 OR 的每个分支都属于授权范围，不能用 `OR 1=1`、NULL 分支或另一个未授权分支放宽。
- 首期不通过 `LIKE`、字符串前缀、任意 CAST 或用户函数证明学校/年级/班级归属。
- JOIN 不能只检查一侧。两次引用同一事实表分别证明；不能从一侧有授权条件推断另一侧也受限。
- 对非递归 CTE 建立依赖图；同一 CTE 被多处消费时，每个消费路径都必须符合其数据级别。
- 不把 `HAVING` 中出现学校名称、最终 `LIMIT 1`、全量排名后取第一行当作范围过滤。
- 权限保护涉及的历史日期、成员有效期和强制规则也进入义务；“高三”文字不能代替届次键。

### 6.3 对照与排名的数据流验证

宿主根据真实上下文和 SQL 结构识别能力，不相信 CTE 名叫 `safe_pool` 就已安全。为 SQL 作用域记录数据级别：

- `OWN_SCOPE`：在普通授权范围内的数据。
- `REFERENCE_RAW`：经明确能力允许读取的更大参照池，只能留在数据库内部。
- `REFERENCE_GROUPS`：已按批准单位分组的数据，仍不能向该账号释放其他单位指标。
- `RELEASED_BASELINE`：按固定完整人群形成、通过阈值和精度要求的整体指标。
- `RELEASED_SELF_RANK`：在批准参照池计算后，仅保留目标授权单位的排名结果。

只允许 `OWN_SCOPE` 和符合输出契约的 RELEASED 数据进入最终业务结果。REFERENCE 级别不能进入最终 SELECT、标量子查询、字符串拼接、存在性判断、排序探测、其他 CTE 的未批准聚合或错误信息输出。

基准验证：参照池城市/学校/届次、考试和固定人群条件完整；不得按其他班/校分组或按未授权单位过滤；只有批准的指标表达式，不允许 `MAX(姓名)`、带任意人群 CASE 的计数或变形明细聚合。样本阈值、输出精度必须在任何外部消费之前生效。

排名验证：分组单位为该能力批准的区县；先得到完整合规参照池，再计算批准的排名窗口，最后过滤目标区县。提前过滤本区再算排名、改变 PARTITION BY、排名池掺入其他城市、通过 UNION 追加外区结果均拒绝。最终只保留批准的本区指标、名次和参评数。

防差分：下级基准能力只允许固定的完整人群和审核维度，不接受排除某班/某校、按学生属性拆组、任意滑动人群等查询。允许跨考试比较时固定每场口径；低样本及单位数门槛须有对应数据样本验收。该策略控制本系统直接开放的组合查询，不能宣称消除用户通过其他渠道已有信息所作的所有统计推断。

### 6.4 验证结果

生成只存在宿主内部的 `VerifiedSemanticQuery`：规范化 SQL 哈希、上下文指纹、发布/映射版本、每个来源的范围义务、能力及输出路径、允许投影和粒度。模型不能提交或伪造这个对象。

这个对象用于核对编译结果，不是 SQL 模板，也不承担补写 WHERE。第一期无需把它传给 DataSemantic。

## 7. SQL 示例：由模型完整生成

以下使用拟定逻辑模型 `edu_scores`，不是当前已发布表名。示例考试已确认且处于获准历史区间；分数字段已按审核口径归一化，NULL 表示不参与均分。示例样本门槛是演示值，正式值来自策略。

### 7.1 班级对比本校年级整体均分

```sql
WITH own_scores AS (
  SELECT s.subject, AVG(s.score_value) AS own_mean
  FROM edu_scores s
  WHERE s.city_id = 'CITY001'
    AND s.school_id = 'SCH001'
    AND s.cohort_id = 'COHORT2027'
    AND s.class_id = 'CLASS01'
    AND s.exam_batch_id = 901
  GROUP BY s.subject
), grade_baseline AS (
  SELECT s.subject, ROUND(AVG(s.score_value), 2) AS grade_mean
  FROM edu_scores s
  WHERE s.city_id = 'CITY001'
    AND s.school_id = 'SCH001'
    AND s.cohort_id = 'COHORT2027'
    AND s.exam_batch_id = 901
  GROUP BY s.subject
  HAVING COUNT(s.score_value) >= 10
    AND COUNT(DISTINCT s.class_id) >= 3
)
SELECT o.subject, o.own_mean, g.grade_mean,
       o.own_mean - g.grade_mean AS mean_gap
FROM own_scores o
INNER JOIN grade_baseline g ON g.subject = o.subject
ORDER BY o.subject
```

验证关键：`own_scores` 符合完整班级元组；`grade_baseline` 必须得到对应 grant 的基准能力，并且不按班分组；其他年级、其他学校、未批准细分维度不可混入。分组内阈值已应用，整个年级最少参评班数如有要求也必须在 SQL 中表达并通过验证。

### 7.2 区县用户查看本区全市名次

```sql
WITH district_metrics AS (
  SELECT s.district_id, AVG(s.score_value) AS mean_score
  FROM edu_scores s
  WHERE s.city_id = 'CITY001'
    AND s.cohort_id = 'COHORT2027'
    AND s.exam_batch_id = 901
    AND s.subject = 'math'
  GROUP BY s.district_id
  HAVING COUNT(s.score_value) >= 10
), district_ranks AS (
  SELECT d.district_id, d.mean_score,
         RANK() OVER (ORDER BY d.mean_score DESC) AS city_rank,
         COUNT(*) OVER () AS district_count
  FROM district_metrics d
)
SELECT r.district_id, ROUND(r.mean_score, 2) AS own_mean,
       r.city_rank, r.district_count
FROM district_ranks r
WHERE r.district_id = 'DISTRICT01'
  AND r.district_count >= 3
```

验证关键：该账号具备此授权城市的 `district_self_rank`；`DISTRICT01` 为其目标授权；参照池指标、考试、届次、科目、样本门槛符合策略；最终过滤在窗口之后且不释放其他区县行。不能把此示例直接当作“达线率排名”，达线率需使用审核后的分子/分母公式。

CTE 不单独执行。任意一次模型工具调用都必须作为完整查询通过验证，不能先查出 `district_metrics` 给模型再二次处理。

## 8. 编译后物理验证及执行协议

### 8.1 DataSemantic 保持无用户权限状态

沿用现有 `/plan` 请求，提交模型生成且已验证的完整语义 SQL。服务只做已发布模型的展开、字段和关系编译；即使展开后的谓词被下推或别名变化，也必须保留原有逻辑。

客户端不发送“teacher 请帮我过滤”的权限指令，也不传一个缺权限的 SQL 让 DataSemantic 补条件。需要补充字段或发布业务口径视图时，按普通模型发布流程处理，而不是在编译器中新增账号权限功能。

### 8.2 宿主物理验证

只读解析 `/plan` 返回值，按绑定版本的静态映射验证 `VerifiedSemanticQuery` 的义务是否落实到每个实际来源和每条输出路径：

1. 校验返回的发布版本、方言、实际数据库/schema 和物理对象；当前只限制 schema 不够，还需物理来源白名单。
2. 建立物理作用域和字段来源，识别权限条件的精确常量、过滤支配关系、聚合/窗口顺序、最终输出约束。
3. 正确处理编译器生成的 CTE、子查询、别名和投影；权限键变成审核过的物理表达式时通过映射核验。
4. 检查不存在额外未授权扫描、原权限被 OR 放宽、聚合早于必需过滤、参照池被额外消费或最终自身过滤丢失。
5. 对固定业务视图中的公共维表等来源按 manifest 分类证明；未知的编译展开或无法确认的函数拒绝，不因来源属于 public schema 就默认安全。
6. 校验列权限和匿名字段。需要改变 schema、投影、限制或脱敏 SQL 时，再验证实际将执行的最终 AST。

不要求 DataSemantic 返回本项目尚未确认的 lineage；第一期通过审核静态映射和当前编译器可识别结构实现保守验证。遇到语义等价但无法证明的优化形式暂不开放。P0 原型必须证明本校过滤、年级基准和排名三类实际计划可验证；若失败，先限制发布物/SQL 子集或补宿主验证能力，不关闭物理验证上线。

### 8.3 执行服务契约

建议入口：`execute_verified_semantic_sql(actor, datasource_binding, semantic_sql, execute=True)`。

```text
load_current_authorization()
check_workspace_datasource_binding()
logical_ast = parse_and_bind(semantic_sql)
verified = verify_semantic_permissions(logical_ast, authorization, publication)
plan = datasemantic.plan(verified.normalized_sql, publication.binding)
physical = verify_compiled_plan(plan, verified, publication.permission_manifest)
recheck_authorization_fingerprint_and_expiry()
result = execute_read_only(physical, automatic_sql_fixes=0)
recheck_authorization_before_publish()
return validate_release_and_apply_result_privacy(result, verified)
```

伪代码中的验证函数不生成权限 SQL。任何 SQL 修正回到模型生成和完整校验；运行时禁止“报错后删 WHERE 重试”。授权在长查询期间改变时，执行结果丢弃，不先流式发送查询行再校验。

`execute=False` 也完整执行语义与物理验证，只跳过业务数据库执行。生成 SQL 入口不能成为获取未授权计划或绕过检查的通道。

## 9. 现有行列规则、隐私与数据输出

### 9.1 规则迁移

- 将有效旧行规则映射为逻辑字段上的结构化义务，交给模型写入 SQL，宿主验证。
- 原有重复学校/班级限制，逐条迁移到范围授权；不得在新模式下继续双重注入导致基准被缩为本班。
- 仍适用的硬限制保持有效；无法映射到逻辑字段的规则阻止该模型启用，不静默忽略。
- 参照池若被硬限制收窄，禁止标注为全市。只有经过业务确认的策略变更才能改变这类限制。
- 宿主可在内部将旧规则解析成 AST 用于验证，这不等于改写模型提交的 SQL。

### 9.2 字段与隐私

区分正常可见字段、仅用于政策过滤的字段和完全禁止字段。例如某字段可以用于匹配账号授权，但不允许被 SELECT、分组、排序或转成字符串输出。不能只在查询结果回来后删除列，因为禁止字段还可能通过其他表达式泄漏。

既有脱敏继续生效；模型使用已发布匿名身份字段。结果裁剪属于额外兜底，不能作为语义验证失败后的替代。

### 9.3 SQL、事件、日志和文件

当前 runtime 会将 `result['sql']`、物理表名及查询事件传给 UI，并存入结果/历史。接入本模式时同步调整：

- 普通用户仅接收获准的查询结果、范围摘要、语义来源、业务口径及 query_id。
- 参照池查询不向模型或普通用户暴露编译物理 SQL、底层表名、内部 SQL 错误和中间 CTE 结果。
- 模型自己生成的语义 SQL 包含的 ID 仅来自本账号上下文或批准的固定参照范围，不额外附上其他单位目录。
- 物理 SQL 若确需保留用于诊断，进入访问受控的内部存储；普通会话详情、导出和工具日志接口不能透出。
- 图表、报告只消费已 RELEASED 的结果；不得重新查询更大范围补图。

## 10. 接入清单与工程拆分

### 10.1 建议新增模块

| 模块 | 内容 |
| --- | --- |
| 现有 `sys_user.system_variables.edu_auth_v2` | 范围授权、文档revision、有效期和资源标记；不新增表 |
| `src/datasource/service/education_authorization.py` | 加载真实授权、强制规则和可用能力，组合完整 grant 元组 |
| `src/chat/service/semantic_permission_context.py` | 结构化模型上下文和错误 schema，发布绑定 |
| `src/chat/service/semantic_permission_manifest.py` | 审核来源、字段/物理映射、业务指标和编译形态 |
| `src/chat/service/semantic_permission_validator.py` | 逻辑作用域、谓词义务、参照池数据流及输出证明 |
| `src/chat/service/physical_permission_validator.py` | 编译结果与最终执行 AST 的证明 |
| `src/chat/service/permission_validation_types.py` | 可信上下文、内部验证结果、错误码、数据级别 |
| `config/education_permission_v2.json` | 业务范围和统计能力模板 |
| `config/semantic_permission_manifest.json` | 绑定发布物的权限键、字段、粒度及物理映射 |

名称为建议，避免一个大文件承担上下文、谓词证明、数据流及物理兼容全部职责。

### 10.2 修改现有模块

| 模块 | 必须修改的行为 |
| --- | --- |
| `src/chat/service/semantic_query.py` | 先语义验证再 `/plan`；移除新模式的 `apply_permissions_for_execute` 调用；替换 `_verify_row_filters`；返回受限结果契约 |
| `src/chat/service/deepagents_runtime.py` | 模型权限上下文、query_semantic 约束、结构化修正、候选查询鉴权、SSE/工具结果权限隔离 |
| `src/chat/service/conversation_state.py` | 指纹增加 grant、策略/映射版本和到期边界；权限变化后不复用旧模型上下文与结果 |
| `src/datasource/service/query_permission.py` | 列检查及旧模式工具可暂存；按数据源切流后不能给新模式自动补行条件或放宽聚合 |
| `src/datasource/service/execute_with_permission.py` | 显式区分旧路径与新路径；新模式只接受已验证查询引用或调用统一服务；缺身份拒绝 |
| `src/system/workspace_scope.py`、`authz.py` | 空间成员、资源访问、委派管理边界和业务管理员身份校验 |
| `src/system/api/edu_permission.py`、`user.py`、schemas | 正式范围模型、批量预览、生效预览及上下文管理 |
| `src/chat/api/chat.py` 及实际 SQL 接口 | 同一服务覆盖生成/执行；问句拦截保留为体验层 |
| 实体解析、教育 API、报告、预警、后台任务 | 盘点所有真实入口；候选数据走同一验证，固定业务服务读取单独证明或暂时关闭；无用户不能默认全量 |
| React 权限页和 API | 稳定范围选择、能力模板、有效期、模型 SQL 的权限验证解释 |
| `src/audit`、会话详情和下载接口 | 审计变更和拒绝码，隐藏参照池中间信息；报告读取重新鉴权 |

v2 数据源不能让用户通过旧“执行 SQL”接口或未迁移报表 helper 绕过验证。后台固定统计可以保留独立服务身份，但不得被 Agent 当作无权限原始查询接口调用。用户发起的自由分析 SQL 均由模型生成。

### 10.3 上下文与撤权

缓存键包含用户、空间、数据源、发布绑定、策略/映射版本、有效 grant 指纹及时间有效性。权限错误缓存也不能只按 SQL 文本跨用户复用。

执行前、执行后发布结果前检查权限。会话恢复、卸载工具结果、报告历史、导出下载都重新鉴权；第一期权限指纹变化后旧报告拒绝下载并提示重新生成。报告文件不能留在公共静态路径。

授权写入和变更记录事务性提交；访问审计可沿用异步基础。不能把模型已观察到的越权结果通过后续撤权“收回”，因此内部池必须在第一次返回前完成输出验证。

## 11. 验收与测试策略

### 11.1 三层测试各自证明什么

| 层次 | 核验内容 | 不能替代的检查 |
| --- | --- | --- |
| AST/规则测试 | 固定 SQL 的作用域、值域、数据流和拒绝码；对正确 SQL 做越权变异 | 不证明真实编译器或模型表现 |
| 编译/执行集成测试 | 对实际绑定发布物调用 `/plan`，用混有授权/越权样本的数据核对物理计划和结果 | 不以 mock 掉权限验证的通过作为验收 |
| 模型端到端评测 | 按真实上下文生成 SQL、接收结构化错误、在预算内修正并作答 | 模型成功率不能代替确定性拦截 |

权限语法变异包括删除一个权限条件、改值、加 OR、复制未受限扫描、移动过滤、增加 UNION、换 JOIN 一侧、从内部池另开输出路径、改窗口分区、删除最终自身过滤、移除样本门槛和反复差分。

### 11.2 必须通过的场景

沿用总体方案 A01–A23，并增加以下本模式验收：

| 编号 | 场景 | 期望 |
| --- | --- | --- |
| M01 | 模型漏写 school/class/cohort 等必需条件 | `/plan` 和业务数据库执行前拒绝；返回定位错误供模型修正 |
| M02 | 模型写全正确条件 | DataSemantic 编译前后均通过；宿主没有新增权限 WHERE |
| M03 | 规范化、别名和编译器 CTE 展开 | 同一安全约束可验证；无法识别时拒绝而非放行 |
| M04 | OR、NULL、表达式和未授权 IN 值放宽范围 | 拒绝；不能靠 SQL 注释或 JSON 声明通过 |
| M05 | 多学校多班授权交叉组合 | 只允许完整 grant 元组的并集 |
| M06 | 班级均分与年级整体基准 | 数值正确，无其他班逐班值；基准未授权或未满足门槛时拒绝/抑制 |
| M07 | 本区全市名次 | 排名池完整、口径正确，最后只返回本区；提前过滤造成第一名亦判定不合格 |
| M08 | 从排名/基准 CTE 另开 SELECT、EXISTS、排序/聚合泄漏 | 所有未批准消费路径拒绝 |
| M09 | 隐藏字段用于 WHERE/CASE/ORDER BY 或聚合输出 | 按字段策略拒绝，不仅裁剪最终列 |
| M10 | 物理计划遗漏过滤、新增来源、换库或不同发布版本 | 不执行；验证器不会代为补条件 |
| M11 | 强制旧行规则不可映射或条件被模型遗漏 | 阻止启用/拒绝查询，不静默跳过 |
| M12 | 正常 SQL 失败，模型试图改物理 SQL 或去掉权限再试 | 仅接受新语义 SQL 并重走全链；自动修正次数为零 |
| M13 | generate-sql / execute=False | 仍完成所有权限验证，不执行数据查询，不提供未经授权计划 |
| M14 | 候选查询、实体解析、直接 SQL、报表旁路 | 同一范围，不存在旧接口回退 |
| M15 | 权限在查询中到期/撤销，或上下文被伪造 | 不发布结果；服务端身份及版本优先 |
| M16 | SSE、工具日志、历史、文件下载含物理 SQL/内部池 | 普通用户不可获取，只有获准业务结果可见 |
| M17 | 同题同义改写、续问换考试及重复运行 | 权限范围不变，指标与同一数据基准一致；缺条件时澄清 |

上线门槛：所有确定性越权变异必须拦截，所有必须允许的图片场景有真实编译/执行证据。模型首轮 SQL 通过率、修正后完成率、错误拒绝率、权限修正次数、编译/验证耗时和查询耗时分别统计；P0 建立基线后制定性能阈值。不得因模型通过率低而关闭验证。

当前已有部分相关本地测试不在 Git 白名单中。新增测试目录和文件必须同步更新 `.gitignore`；正式回归前记录/处理已有会话摘要断言失败，不能将其混同为新权限回归。

## 12. 分阶段交付、迁移和回滚

建议每个阶段形成可独立审阅的提交，生产数据迁移和切流另行执行。以下为单人粗估；假设数据字典、业务口径和编译环境可用，不包含外部依赖等待及大量历史数据清洗。

| 阶段 | 交付内容 | 退出条件 | 粗估 |
| --- | --- | --- | --- |
| P0：实际计划原型 | 对本校过滤、年级基准、本区排名三类模型 SQL 调用真实 `/plan`，完成字段/标识映射和攻击样本 | 不改 DataSemantic 权限逻辑，宿主能证明三类计划；明确支持子集 | 2–3 人日 |
| P1：授权与上下文 | grant 模型、目录/历史归属、管理边界、manifest 和模型上下文、旧规则试迁移 | 范围/能力只来自可信授权，未确认旧账号不自动扩权 | 3–4 人日 |
| P2：普通语义验证 | 作用域、完整元组条件、字段、CTE/JOIN/UNION、强制规则与结构化错误 | 普通查询越权变异稳定拒绝，模型漏写可在预算内改正 | 4–6 人日 |
| P3：物理计划验证 | 静态映射、过滤/聚合顺序、物理对象、最终 SQL 检查，接通统一执行 | 新路径没有行条件补写，验证失败数据库零执行 | 4–6 人日 |
| P4：基准及排名 | 参照池数据流、审核指标/维度、输出范围、精度/样本限制 | 基准/排名全部由模型 SQL 完成且只输出获准结果 | 4–6 人日 |
| P5：产品及旁路接入 | 管理页、批量预览、候选/实体/报告/导出、SSE/日志、历史失效 | 所有活跃读取入口统一执行，授权变化不复用旧结果 | 3–5 人日 |
| P6：集成与灰度 | A01–A23、M01–M17、多用户真实库金标、模型重复评测、迁移/回滚演练 | 无未确认范围扩大，口径/隔离/稳定性达到验收阈值 | 3–5 人日 |

合计约 **23–35 人日，单人约 5–7 个工作周**。相较初版额外投入主要在语义数据流验证、编译后证明和模型修正闭环。P0 若发现编译形态不可验证，先调整发布模型或缩小支持范围，后续工作量重估；不能将尚未证明的路径计为已支持。

迁移策略：按工作空间和数据源启用 `model_explicit_permission_v2`；影子阶段仅验证 SQL/计划与合成数据，不将扩围数据送回模型。新模式一旦启用，该数据源所有用户读取入口统一走新门禁，缺失权限不能回退旧注入链路。

保留授权和映射快照、发布版本及上一应用版本。回滚必须维持当前范围和默认拒绝；旧版本不能表达区县/届次或受控排名时暂停对应查询，不能恢复聚合自动放宽。灰度过程记录拒绝码、模型修正和策略版本，明确支持范围。

## 13. 完成定义

1. 模型生成的语义 SQL 本身包含正确权限条件及基准/排名的输出约束。
2. DataSemantic 无需获知账号权限，也不增加用户权限谓词生成逻辑。
3. 宿主只做策略解析、确定性验证和执行，缺条件时拒绝并让模型重写，不自动补写或删改权限条件。
4. 普通查询、参照池、窗口排名、历史趋势、候选目录和报告各入口经过同一可信授权边界。
5. 正确请求可以完成，错误或无法证明的计划不执行，越权数据从未进入无权用户的模型上下文、事件、历史或文件。
