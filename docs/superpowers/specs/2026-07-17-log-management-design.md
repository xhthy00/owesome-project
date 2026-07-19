# 日志管理系统设计

- **日期**: 2026-07-17
- **分支**: edu
- **范围**: 为学情分析平台新增"系统管理 - 日志管理"功能，含访问日志、操作日志、登录日志三类，超管可见全部、普通用户仅见自己的日志。

## 1. 背景与目标

当前平台无任何 HTTP 访问/操作/登录日志的采集与查询能力（`src/agent/audit/` 仅采集 Agent 工具调用，与平台访问无关）。需要新增日志管理以满足安全审计与运营追溯需求。

**目标**
- 新增"系统管理 - 日志管理"一级菜单，下挂三个二级子菜单：访问日志、操作日志、登录日志。
- 三类日志写入 PostgreSQL，提供分页查询与筛选。
- 权限隔离：超级管理员可见全部日志，其他登录用户仅可见自己的日志（后端强制隔离）；前端"系统管理 - 日志管理"菜单对所有登录用户可见，进入后按上述权限展示数据。

**非目标（YAGNI）**
- 不做日志自动清理 / 定时归档（由运维或外部脚本处理）。
- 不做通用菜单权限框架（仅对"系统管理"这一组做超管过滤）。
- 操作日志不记录变更前后值对比，仅记动作 + 入参摘要。
- 不做日志导出、告警、图表统计。

## 2. 现状关键事实（设计依据）

- **超级管理员判定**: `user.id == 1 and user.account == "admin"`，定义于 `src/system/authz.py:11-15`、`src/system/api/permission.py:23-28`、`src/system/workspace_scope.py:37`。无 `is_superuser` / 独立 `role` 字段。
- **当前用户获取**: `Depends(get_current_user)`（`src/system/api/system.py:24`），返回 `UserResponse`（`src/system/schemas.py:25`），字段含 `id/account/name/email/oid/status/language/origin/create_time`，**不含 role / is_platform_admin**。
- **路由注册**: 所有 router 经 `src/common/router.py:14-32` 的 `register_routers()` 挂到 `/api/v1`。新增 router 在此注册，feature 模块内不直接 `app.include_router`。
- **统一响应信封**: `common/schemas/response.py:47` 的 `success_response(data=..., message=...)`，`{code, message, data}`，HTTP 200 + 业务 code。
- **分页范式**: `/pager/{page_num}/{page_size}`，`offset((page_num-1)*page_size).limit(page_size)`，返回 `{total, items}`（见 `src/system/api/user.py:28`、`workspace.py:128`）。
- **现有审计基建**: `src/agent/audit/tool_call_log.py` 用 fire-and-forget（`asyncio.create_task` + `asyncio.to_thread`）+ raw SQL 后台写库，写失败只 `logger.warning`。表 DDL 在 `common/core/database.py:98` 启动时 `CREATE TABLE IF NOT EXISTS`。本设计沿用 fire-and-forget 模式，但升级为 SQLModel ORM（便于按字段分页筛选）。
- **trace_id**: `src/common/core/trace.py` 提供 per-request trace_id（`new_trace_id/set_trace_id/get_trace_id`）。当前仅 chat 流式端点设置（`chat/api/chat.py:545`），其他端点默认 `"-"`。
- **前端菜单**: 硬编码于 `frontend-react/src/components/layout/side-bar.tsx:22-28`，字段 `{ key; path; label; icon }`，无权限标识、无 children 嵌套。二级菜单（如 permission 子项）通过单独数组 + 本地展开 state + 硬编码特判实现（`side-bar.tsx:30-36, 202-247`）。**前端无菜单权限过滤**。
- **前端当前用户**: `frontend-react/src/api/auth.ts:13-23` 的 `CurrentUser` 不含 role 字段；`_app.tsx` 调 `getCurrentUser()` 仅做 token 校验。
- **前端路由**: Next.js Pages Router，页面文件即路由。
- **前端列表页范式**: antd 5 `Table`，容器 `rounded-2xl border ... bg-white shadow-sm`；现有列表页几乎都 `pagination={false}` 一次性拉取（如 `members.tsx:68`）。服务端分页 API 已存在（`system.ts:38-39`）但前端未启用翻页 UI。
- **前端 API 封装**: `frontend-react/src/api/client.ts` 的 `apiRequest<T>`（原生 fetch，自动注入 Bearer token + `X-Workspace-Oid`，解析统一信封），baseURL `NEXT_PUBLIC_API_BASE_URL ?? "/api/v1"`。

## 3. 需求确认（澄清结论）

| 决策点 | 选择 |
|---|---|
| 登录日志范围 | 登录成功 + 失败（含密码错误、账号不存在、账号禁用） |
| 操作日志采集 | 装饰器 `@audit_operation` 手动贴到写接口（增删改） |
| 访问日志范围 | 仅 NL2SQL（generate-sql / execute-sql / chat-stream）与 education 学情查询接口 |
| 存储 | PostgreSQL，新建 3 张表 |
| 保留策略 | 不自动清理（运维/外部脚本处理） |
| 操作日志详细度 | 仅记动作 + 入参摘要，不记变更前后值对比 |
| 模块归属 | 独立 `src/audit/` feature 包 |
| 前端菜单过滤 | `side-bar.tsx` 内特判不再对"系统管理"做超管过滤，所有登录用户可见；页面内部按 `is_platform_admin` 区分展示与筛选能力 |
| 日志详情查看 | 表格行展开看完整内容 |

## 4. 架构

### 4.1 模块布局

新建独立 feature 包 `src/audit/`，遵循项目分层约定：

```
src/audit/
├── __init__.py
├── models/
│   ├── __init__.py
│   └── audit.py          # 3 个 SQLModel 表
├── crud/
│   ├── __init__.py
│   └── audit.py          # 分页查询 + 权限过滤
├── service/
│   ├── __init__.py
│   ├── writer.py         # fire-and-forget 异步写入器
│   └── decorators.py     # @audit_access / @audit_operation 装饰器
└── api/
    ├── __init__.py
    └── audit.py          # router = APIRouter(prefix="/audit", tags=["audit"])
```

在 `src/common/router.py` 顶部 import `audit_router` 并加入 `get_all_routers()` 返回列表，自动挂到 `/api/v1/audit`。

### 4.2 采集层（三类日志不同触发点）

| 日志类型 | 采集方式 | 触发点 | 业务语义字段 |
|---|---|---|---|
| 登录日志 | 登录端点内显式记录 | `src/system/api/system.py` 的 `/system/login` 成功/失败分支 | account、fail_reason |
| 操作日志 | `@audit_operation(operation_type, resource_type)` 装饰器 | 各写接口（POST/PUT/DELETE/PATCH） | operation_type、resource_type、resource_id、detail（入参摘要） |
| 访问日志 | `@audit_access` 装饰器 | NL2SQL 与学情查询接口 | request_method、request_path、datasource_id、query_text |

**统一写入模式**: 所有采集走 fire-and-forget（`asyncio.create_task` + `asyncio.to_thread` 后台写库），写库失败只 `logger.warning`，绝不阻塞或影响主业务请求。**日志写入使用独立 `SessionLocal()`**，不复用业务请求 session，避免日志写入失败污染业务事务；后台线程内用完即关。

**请求上下文捕获**: 装饰器通过 `request: Request` 依赖注入拿 IP/UA/path/method；`trace_id` 用 `common.core.trace.get_trace_id()`，装饰器执行时若无 trace_id 则 `new_trace_id() + set_trace_id()` 兜底。

**字段截断**: `query_text` 截断 500 字符、`detail` 截断 2000 字符、`error_msg` 截断 500 字符、`user_agent` 截断 500 字符，写库前截断避免超长。

**IP 解析**: `X-Forwarded-For` 多级代理取第一个有效 IP，无则回落 `request.client.host`，再无则空字符串。

### 4.3 权限隔离（后端强制 + 前端菜单）

**后端 API 强制隔离（硬底线）**

每个日志查询接口接受 `current_user = Depends(get_current_user)`，在 crud 层根据用户身份应用不同过滤：

```python
if is_platform_admin(current_user):   # user.id == 1 and account == "admin"
    # 查全部日志，支持按 user_id / account / workspace_oid 等筛选
else:
    # 强制 WHERE user_id = current_user.id
    # 登录日志特殊：WHERE account = current_user.account（含失败记录）
```

- 复用 `src/system/authz.py::is_platform_admin(user)`（接收 SysUser ORM 对象）。
- 普通用户过滤在 crud 层强制注入，前端无法绕过（即使前端不传 user_id 也只返回自己的）。
- 登录日志普通用户按 `account` 匹配，使其能看到自己登录失败的记录（失败时 user_id 为空）。

**前端菜单与页面**

1. **扩展 `/system/me`**: `src/system/api/system.py:86` 的 me 端点用 `authz.is_platform_admin` 算好返回 `is_platform_admin: bool`。
2. **扩展前端 `CurrentUser`**: `frontend-react/src/api/auth.ts:13-23` 增加 `is_platform_admin: boolean` 字段。
3. **`side-bar.tsx`**: 新增"系统管理"一级菜单 + 三个二级子菜单；**不对"系统管理"组做超管过滤**，所有登录用户均可见。进入日志页面后：
   - 超管可查看全部日志，并显示用户账号列、用户级筛选条件（user_id / account 等）。
   - 普通用户查看自己的日志，不显示用户账号列、不提供按他人账号/用户 ID 筛选。

**菜单与路由结构**
- 一级菜单: `{ key: "system", path: "/system", label: "系统管理", icon: <SettingOutlined /> }`
- 二级子菜单（仿现有 `permissionSubRoutes` 模式，新增 `systemSubRoutes` + 本地展开 state）:
  - 访问日志 `/system/log/access`
  - 操作日志 `/system/log/operation`
  - 登录日志 `/system/log/login`
- 选中态逻辑 `pathname === item.path || pathname.startsWith(item.path + "/")` 天然支持新 path。
- 折叠态侧栏（`isMenuExpand=false` 分支）当前不支持二级菜单，"系统管理"在折叠态仅显示 icon 跳转到第一个子页面 `/system/log/access`（与现有 permission 折叠态行为一致）。
- 新建页面**不要**加入 `_app.tsx:31-35` 的 `isBypassLayout`，否则会失去侧边栏。

## 5. 数据模型

3 张表，公共字段 + 各自业务字段。统一 SQLModel，走 Alembic 迁移（`alembic/versions/` 现有 2 个迁移，`alembic/env.py:11-15` 已 import 各 model 注册 metadata，新增 model 需在此 import）。

**公共字段（三表都有）**
- `id`: BigInteger PK autoincrement
- `trace_id`: str（关联现有 trace_id 机制，便于和 tool_call_log 串联排查）
- `user_id`: Optional[int]（登录失败/未登录访问时为空）
- `user_account`: Optional[str]（冗余存账号名，避免 join 用户表；登录失败时 user_id 为空但 account 有值）
- `workspace_oid`: Optional[int]
- `ip`: Optional[str]
- `user_agent`: Optional[str]
- `success`: bool
- `error_msg`: Optional[str]
- `elapsed_ms`: Optional[int]（访问日志记录耗时）
- `created_at`: DateTime（建索引）

**`audit_access_log`（访问日志）** — 公共字段 +
- `request_method`: str
- `request_path`: str
- `datasource_id`: Optional[int]
- `query_text`: Optional[str]（截断 500）

**`audit_operation_log`（操作日志）** — 公共字段 +
- `operation_type`: str（create/update/delete/patch）
- `resource_type`: str（user/workspace/datasource/permission 等）
- `resource_id`: Optional[str]
- `detail`: Optional[str]（入参摘要 JSON，截断 2000）

**`audit_login_log`（登录日志）** — 公共字段 +
- `account`: str（尝试登录的账号，即使失败也记）
- `fail_reason`: Optional[str]（密码错误/账号不存在/账号禁用，成功时为空）

**索引**
- 三表: `idx_<table>_created_at` (created_at)
- 三表: `idx_<table>_user_id` (user_id)
- 登录日志: `idx_audit_login_log_account` (account)

## 6. 查询接口

`src/audit/api/audit.py`，router 前缀 `/audit`，每类一个分页端点，遵循现有 `pager` 范式：

| 端点 | 超管筛选参数 | 普通用户 |
|---|---|---|
| `GET /audit/access/pager/{page}/{size}` | user_id, datasource_id, success, start_time, end_time | 强制 user_id=自己 |
| `GET /audit/operation/pager/{page}/{size}` | user_id, resource_type, operation_type, start_time, end_time | 强制 user_id=自己 |
| `GET /audit/login/pager/{page}/{size}` | account, success, start_time, end_time | 强制 account=自己 |

- 返回 `{ total, items }`，与 `systemApi.pagerUsers` 一致。
- 时间筛选用 `created_at` 范围（`start_time` / `end_time` 为毫秒时间戳，与项目 `create_time` 毫秒时间戳惯例一致）。
- 排序: `created_at DESC`。
- 统一走 `success_response` 信封。

## 7. 前端

**API 层**: 新建 `frontend-react/src/api/audit.ts`，导出 `auditApi = { pagerAccessLog, pagerOperationLog, pagerLoginLog }`，照搬 `system.ts` 的 `apiRequest` 范式。

**列表页**: 3 个页面，仿 `pages/construct/permission/members.tsx`：
- 顶部: `Typography.Title level={4}` + 副标题 + 右侧刷新/搜索区。
- 顶部筛选条: `DatePicker.RangePicker`（时间范围）+ 各日志特有筛选项（操作日志有 resource_type/operation_type 下拉，登录日志有 success 下拉，访问日志有 datasource 选择）+ 查询按钮。
- 表格: antd `Table`，`rowKey="id"`，**服务端分页**（`pagination` + `onChange` 回调拉新页）。这是与现有列表页 `pagination={false}` 的关键差异，因日志表会持续增长。
- 列定义: 时间、用户（account）、IP、操作/查询内容（截断）、结果（success Tag）、耗时。
- **行展开详情**: 表格行可展开，显示完整 `query_text`/`detail`/`error_msg`/`user_agent`/`trace_id`。
- 容器样式沿用 `rounded-2xl border border-[#e5e7eb] bg-white shadow-sm`。

**菜单/路由文件**:
- 新建 `frontend-react/pages/system/log/{access,operation,login}.tsx`
- 修改 `frontend-react/src/components/layout/side-bar.tsx`（加一级 + 二级菜单 + 超管过滤）
- 修改 `frontend-react/src/api/auth.ts`（CurrentUser 加 `is_platform_admin`）

## 8. 错误处理

1. **写入失败绝不影响业务**: 采集装饰器/登录端点记录日志都用 fire-and-forget，写库异常只 `logger.warning(...)`，不抛出、不回滚业务事务。
2. **独立 session**: 日志写入用独立 `SessionLocal()`，不复用业务请求 session，后台线程内用完即关。
3. **请求上下文捕获**: 装饰器通过 `request: Request` 拿 IP/UA/path/method；trace_id 用 `get_trace_id()`，无则兜底生成。
4. **查询接口异常**: 走现有 `common/middlewares/exception.py` 统一捕获，转 HTTP 200 + 业务 code；权限不足抛 `ForbiddenException`（`common/exceptions/base.py`）。
5. **字段截断**: 见 4.2。
6. **IP 解析容错**: 见 4.2。

## 9. 测试

`tests/audit/`，镜像 `src/audit/`，遵循 CLAUDE.md 约定（pytest，`testpaths=["tests"]`，`pythonpath=["."]`）：

1. `test_models.py`: 三张表字段与建表 DDL。
2. `test_service_writer.py`: 异步写入器对三类的写入 + 字段截断 + fire-and-forget 不抛错（模拟写库失败时主流程不受影响）。
3. `test_api_query.py`:
   - 超管查全部、带各筛选参数。
   - **普通用户被强制限制为自己的日志**（核心安全测试：构造别人的日志，断言普通用户查不到）。
   - 登录日志普通用户按 account 匹配（含失败记录）。
   - 分页边界（page/size、超出 total）。
4. `test_decorators.py`: `@audit_operation` / `@audit_access` 在成功/失败分支都记录、且不阻塞原函数返回值/异常透传。
5. `test_login_log.py`: 登录成功/密码错误/账号不存在/禁用各记一条，字段正确。

测试用项目现有 pytest + 真实 SessionLocal（参照 `tests/agent/` 风格），不引入新测试框架。

## 10. 改动清单

**后端新建**
- `src/audit/{__init__,models/__init__,models/audit,crud/__init__,crud/audit,service/__init__,service/writer,service/decorators,api/__init__,api/audit}.py`
- Alembic 迁移（`alembic revision --autogenerate -m "add audit log tables"`）

**后端修改**
- `src/common/router.py`: import 并注册 `audit_router`
- `src/system/api/system.py`: `/system/me` 返回 `is_platform_admin`；`/system/login` 成功/失败分支记录登录日志
- 给重要查询接口贴 `@audit_access`：`chat/api/chat.py` 的 generate-sql / execute-sql / chat-stream、`agent/education/api.py` 的学情查询接口
- 给写接口贴 `@audit_operation`：`system/api/user.py`、`workspace.py`、`datasource/api/datasource.py`、`permission.py` 等的 POST/PUT/DELETE/PATCH
- `alembic/env.py`: import 新 model 注册 metadata（如 autogenerate 需要）

**前端新建**
- `frontend-react/src/api/audit.ts`
- `frontend-react/pages/system/log/access.tsx`
- `frontend-react/pages/system/log/operation.tsx`
- `frontend-react/pages/system/log/login.tsx`

**前端修改**
- `frontend-react/src/api/auth.ts`: `CurrentUser` 增加 `is_platform_admin: boolean`
- `frontend-react/src/components/layout/side-bar.tsx`: 加"系统管理"一级菜单 + 三个二级子菜单 + 超管过滤（新增 `systemSubRoutes` + 展开状态，仿 `permissionSubRoutes` / `permissionExpanded`）

**测试新建**
- `tests/audit/{test_models,test_service_writer,test_api_query,test_decorators,test_login_log}.py`

## 11. 验证标准

- `uv run ruff check .` 通过
- `uv run pytest tests/audit/` 全部通过，含普通用户权限隔离用例
- 前端 `npm run typecheck` + `npm run lint` 通过
- 手动验证: 所有登录用户均可见"系统管理"菜单；超管可查看全部日志，普通用户进入日志页面仅能看到自己的日志；直接调日志 API 也越权失败
- 登录失败场景在登录日志中可见（含 fail_reason）
