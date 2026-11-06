# WrenAI 接入本项目实施指南

日期：2026-11-02。状态：接入设计与源码核对，尚未安装 Wren 或修改业务运行链路。

当前选型采用开源自托管 WrenAI Core。此前集团模块方案继续作为模块边界依据；新链路使用本项目的 LLM 接入与会话能力，不新增 DB-GPT 运行时。

## 1. 推荐链路

```text
React 聊天 / 指标 API / 固定报告
    → 本项目认证、工作空间、数据源授权
    → 选择已发布语义模型与指标版本
    → 绑定实体、确认口径
    → 认证指标编译器，或 LLM 根据逻辑模型生成语义 SQL
    → WrenEngine.dry_plan：展开为物理 SQL
    → 本项目权限约束、最终 SQL 校验
    → 数据库只读执行
    → 指标结果、图表、报告、会话记录
```

Wren Core 的 dry_plan 接收 SQL，不直接接收自然语言问题。自然语言理解由 src/llm 与新的语义查询服务负责。认证指标可由确定性编译器直接形成语义 SQL；探索查询使用模型生成，并标记探索状态。[WrenEngine 官方源码](https://github.com/Canner/WrenAI/blob/main/core/wren/src/wren/engine.py)

## 2. 部署为独立语义服务

新增 services/wren-semantic/，建立独立 Python 3.11+ 环境、依赖锁文件和镜像。安装 wrenai 与自建 HTTP 包装需要的框架，锁定经过原型验证的版本。规划服务只加载模型与执行 dry_plan，不配置业务数据库凭据。

现有项目要求 cryptography >=44.0.3,<45.0.0，而当前 Wren main 的包声明要求 >=48.0.1，直接加入根 pyproject 会产生版本冲突。独立环境可保留双方依赖边界。main 包版本不能当作已经验证的正式发布包，需核对实际安装版本与接口。[Wren 依赖声明](https://github.com/Canner/WrenAI/blob/main/core/wren/pyproject.toml)

以下是拟新增的本项目配置，当前 Settings 尚未实现：

```env
QUERY_BACKEND=existing
WREN_BASE_URL=http://wren-semantic:8090
WREN_PLAN_TIMEOUT_SECONDS=10
```

业务主链路先保持 existing；按授权数据源/工作空间的服务器端绑定灰度切换到 wren。WREN_BASE_URL 指向自建包装服务，不是 Wren 官方固定 HTTP 地址。

建议包装内部接口：

- GET /health：运行状态与实际引擎版本。
- POST /internal/v1/semantic/context：读取当前调用者允许使用的逻辑模型说明。
- POST /internal/v1/semantic/plan：在固定发布物下规划语义 SQL。

context 接口通过本项目服务身份和模型权限筛选。plan 接收 bundle_id、manifest_hash、dialect、semantic_sql 和关联请求标识；具体模型路径由服务器解析，不由模型或前端指定。响应包含 physical_sql、发布物标识和规划信息；物理对象与字段由网关重新解析验证。

内部接口执行身份校验，拒绝未知发布物、哈希不匹配和未授权绑定。长耗时规划使用受限 worker，HTTP 超时后不能让失控规划任务无限积累。

## 3. Wren SDK 最小规划示例

示例依据当前 main 源码编写，尚未在本地 Wren 环境执行。manifest 是已审核并构建的 MDL dict；它不是原始数据库 schema JSON。

```python
import base64
import json

from wren import DataSource, WrenEngine
from wren.config import WrenConfig


def plan_sql(manifest: dict, semantic_sql: str) -> str:
    encoded = base64.b64encode(
        json.dumps(manifest, ensure_ascii=False).encode("utf-8")
    ).decode("ascii")
    with WrenEngine(
        manifest_str=encoded,
        data_source=DataSource.postgres,
        connection_info={},
        fallback=False,
        config=WrenConfig(strict_mode=True),
    ) as engine:
        return engine.dry_plan(semantic_sql)
```

输入 SQL 使用目标方言并引用 MDL 逻辑模型。输出是展开后的 SQL；模型范围约束与最终只读检查还要与应用层授权配合。fallback=False、所用 SQL 形态及引擎版本须在原型中验证。Wren 配置类型见[官方配置源码](https://github.com/Canner/WrenAI/blob/main/core/wren/src/wren/config.py)。

## 4. 本项目具体改造位置

| 位置 | 建议变更 |
|---|---|
| src/common/core/config.py | 新增后端选择、Wren 地址、超时等 Settings |
| 新增 src/semantic/registry.py | 按工作空间、数据源、领域解析固定发布物与可用模型 |
| 新增 src/semantic/client.py | 调用自建语义服务，处理身份、超时、版本、错误码 |
| 新增 src/chat/service/semantic_query.py | 统一需求、上下文、指标编译/生成、规划、授权与结果流程 |
| src/chat/api/chat.py | generate-sql、execute-sql、chat-stream 共用新服务；接口处理鉴权与响应 |
| src/chat/service/sql_generator.py | 逐步将原有物理表 SQL 生成功能隔离为 existing 实现；Wren 路径使用逻辑模型提示与结构化输出 |
| src/datasource/service/execute_with_permission.py | 新增受控规划 SQL 执行能力；适配展开 SQL；最终检查后执行 |
| src/datasource/service/query_permission.py | 验证 CTE、子查询、别名、排名比较池与字段血缘的权限语义 |
| src/agent/education/ 与新领域包 | 将指标、数据模型、模板迁为明确能力；新增经营财务模型时复用公共链路 |
| frontend-react/src/api/adapter/chatAdapter.ts | 兼容现有 SSE 字段，按需增加口径、来源和版本显示 |
| deploy/ | 新增语义服务镜像、网络、健康检查与只读发布物挂载 |

query_backend 是服务器端执行后端选择，agent_mode 是现有 Agent 展示/执行模式，二者分开处理。首期不增加第四种公开 agent_mode；Wren 灰度请求在旧 agent/team 分发前进入统一语义服务。现有 Agent 工具后续也调用这一公共服务，避免继续维护不同口径的入口。

## 5. 数据源绑定与模型发布

新增绑定记录：workspace_oid、datasource_id、domain、bundle_id、manifest_hash、dialect、status。当前 pg 类型映射到 Wren 的 postgres；其他数据库逐一验证，不能直接沿用字符串。

绑定必须经过工作空间与数据源访问校验。同一数据源可有多个领域发布物；请求使用已授权的数据集或领域绑定，存在歧义时明确选择。访问权限与认证数据源不能由模型决定。

语义项目建议分领域：

```text
semantic/
  education/   # 考试、学校、班级、成绩、认证统计视图
  finance/     # 法人、期间、币种、收入、回款、应收
  operations/  # 客户、订单、经营目标
```

每个项目维护逻辑模型、业务键、粒度、关联基数、指标说明、审核示例与标准结果；由发布过程构建不可变 MDL。现有 config/education_schema.json 可作为映射输入，不能原样作为 MDL 使用。

先用一个现有 PostgreSQL 数据源和小规模认证模型验证。经营财务源的实际表、身份源和口径尚未提供，作为下一领域接入时确认，不编造源模型。

模型说明中注册口径并不足以保证数字正确。高频均分、达线率、收入、回款等指标仍由注册表和认证编译/计算提供者定义；Wren 展开其模型，报告引用同一份结果。

## 6. 权限执行接入是首要集成测试

当前 execute_sql_with_permission 会执行权限改写与错误自动修复。Wren 输出通常包含 CTE 和新的别名；不能假定旧改写器在这些形态下仍保持权限与排名语义。

新路径建议：校验服务端身份与绑定 → 解析规划 SQL → 应用授权约束 → 校验最终对象和字段 → 只读执行 → 结果字段授权 → 记录实际执行 SQL。对用户不存在、策略异常、无法解析的 SQL 返回失败。

为 Wren 路径提供 allow_auto_fix=False 或单独执行入口。若需要修复，修改候选语义 SQL 后重新规划，再走全部权限检查。禁止根据物理库报错临时增加未经授权的 JOIN 或替换查询范围。

本班全市排名需要同时满足正确比较池和数据访问边界；使用有授权依据的汇总视图或受控排名能力，返回允许查看的结果。不能简单在最外层追加 WHERE 并认为排名与权限都已正确。

tables_hint 等元信息依据最终物理对象重新计算，不信任模型提供的表名列表。权限改写后再次校验只读、对象和字段；必要时采用数据库 RLS/授权视图作为实际约束，能力在集成测试中确认。

## 7. API、SSE 与错误兼容

generate-sql 调用统一 prepare 能力，返回计划而不查数据。execute-sql 与 chat-stream 共用 prepare + execute；如需先预览再执行，保存 plan_id 及其用户、绑定、权限版本、发布物哈希，并在执行时重新授权。

对外保持 sql 为最终可执行 SQL 的既有语义。新增 semantic_sql、bundle_id、plan_id、metric_version 等信息时使用明确字段，敏感信息按权限展示。会话持久化记录实际执行 SQL 与发布物，便于回放。

前端已识别 step、sql、result、summary、report、error、done 等事件。新 runner 输出既有 result 结构 columns、rows、row_count，并兼容对应字段；复杂数值、日期和单位使用统一序列化规则。SSE 的关闭、取消、失败、等待用户状态与现有任务模型对齐。

错误至少区分：未发布模型、需要追问、维度不支持、规划失败、权限拒绝、执行失败、无数据、结果不完整。Wren 超时或失败返回明确错误；灰度回滚通过配置进行，不能对单次失败静默退回原始物理表查询。

## 8. 分阶段落地与验收

| 阶段 | 交付 | 通过条件 |
|---|---|---|
| A：无模型最小链路 | 语义服务、已构建 MDL、固定语义 SQL → dry_plan → 权限执行 | 规划 SQL 可执行，标准结果正确，权限测试通过 |
| B：统一问数 | LLM 逻辑模型上下文、结构化需求、三个 API 共享服务、SSE | 条件保留，多轮修改与前端结果兼容 |
| C：认证指标与报告 | 指标注册、复杂提供者、结果契约、教育报告适配 | 正文图表一致、全量指标不受预览截断影响 |
| D：集团复用 | 财务/经营领域包、绑定与发布管理、固定指标 API | 不依赖教育包可查询新领域；数据隔离与业务对账通过 |

固定测试集覆盖均分、人数、比率、排名、JOIN 重复、真实零分/缺考、同名对象、空结果、模型不存在、展开 CTE、字段权限、多轮换条件与超时。记录语义 SQL、物理 SQL、实际执行 SQL、模型与数据版本及期望结果。

先验证 A 再引入 LLM，可独立判断问题来自 MDL、规划、权限还是需求生成。当前文档已核对代码接口与改造位置；没有执行安装、数据库连接、Wren 规划或端到端正确率测试。
