# frontend-react 结构地图（分支 `edu`）

> 定位：`D:\develop\git-repo\owner\awesome-data\frontend-react`
> 技术栈：Next.js 14（**pages router**）+ React 18 + Ant Design 5 + @antv/g2 + Tailwind 3 + TS 5（strict）
> 结论先行：这是**单页聊天工作台 + 后台管理页**的混合体。真正的产品逻辑集中在
> `src/hooks/useChat.ts`（1388 行）、`src/components/chat/ChatExecutionPanel.tsx`（1278 行）、
> `src/api/adapter/chatAdapter.ts`（412 行）三者，其余多为配置页/占位页。

---

## 1. 工程与运行配置

### 1.1 依赖与脚本（`package.json`）

| 项 | 内容 | 位置 |
|---|---|---|
| scripts | `dev`（`WATCHPACK_POLLING=true next dev -p 3001`）、`devW`、`build`、`start -p 3001`、`lint`、`typecheck`、`test`（`vitest run`） | `package.json:6-12` |
| 运行时依赖 | `@ant-design/icons@6`、`@antv/g2@5`、`antd@5`、`html2canvas`、`jspdf`、`next@14`、`react-markdown`、`remark-gfm` | `package.json:14-25` |
| devDeps | `vitest@3` + `@testing-library/react` + `jsdom`、`tailwindcss`、`typescript` | `package.json:26-38` |

- 没有状态管理库（无 redux/zustand/swr/react-query）：全部靠 `useState` + Context + 手写 hook。
- 没有图表库声明之外的 UI 体系；图表类型仅 `column|bar|line|pie`（`src/components/chat/G2Chart.tsx:5`）。
- ⚠️ `classnames` 在 `src/new-components/chat/input/ChatInputPanel.tsx:3` 被 import，但 `package.json` 未声明（靠 antd 传递依赖存在，`node_modules/classnames@2.5.1` 实测存在）。

### 1.2 `next.config.js`

| 配置 | 值 | 行 |
|---|---|---|
| `compress` | `false`（**关键**：避免 gzip 缓冲破坏 SSE 小帧的即时 flush） | `next.config.js:7` |
| `reactStrictMode` | `true` | `:8` |
| `transpilePackages` | antd / rc-* 一长串（为兼容 ESM 依赖） | `:9-26` |
| `rewrites()` | **仅当 `process.env.BACKEND_URL` 存在时**，`/api/:path*` → `${BACKEND_URL}/:path*` | `:27-38` |

⚠️ **不存在 `pages/api/` 目录（已实测 `Test-Path` 为 False）**：项目里**没有任何 BFF 代理路由**。
“代理”只有 `next.config.js` 的 rewrite 一条路，且它只在 `BACKEND_URL` 有值时生效。

### 1.3 `tsconfig.json`

- `strict: true`、`noEmit`、`moduleResolution: bundler`、`jsx: preserve`、`incremental`（`:6,8,15,18,19`）
- 路径别名 `@/*` → `src/*`（`:21-25`）——`pages/` 里也用 `@/` 引 `src`
- ⚠️ 未开启 `noUnusedLocals` / `noUnusedParameters`，所以死导入不会在 `typecheck` 报错。

### 1.4 环境变量（`.env.example` / `.env.local`）

| 变量 | `.env.example:1-5` | 实际 `.env.local:1-4` | 用途 |
|---|---|---|---|
| `BACKEND_URL` | `http://127.0.0.1:5670/api` | `http://localhost:8000/api/v1` | 只在 build/start 的 rewrite 用 |
| `NEXT_PUBLIC_API_BASE_URL` | `/api`（走 rewrite） | `http://localhost:8000/api/v1`（**绝对地址，绕过 rewrite**） | 浏览器直连后端 |
| `NEXT_PUBLIC_DEFAULT_DATASOURCE_ID` | `1` | `12` | 默认数据源 |
| `NEXT_PUBLIC_AGENT_MODE` | `team` | `team` | 默认 agent 模式 |

⚠️ `frontend-react/.env.local` **已被 git 跟踪**（`git ls-files` 命中；`.gitignore:12` 只忽略 `.env`），把本机 localhost 与 datasource_id=12 提交进了仓库。

### 1.5 测试与样式

- `vitest.config.ts`：jsdom + `globals` + alias `@`→`src`，`maxWorkers: 1`（`vitest.config.ts:4-20`）
- 现有测试仅 6 个文件：`src/api/adapter/chatAdapter.test.ts`、`src/components/chat/G2Chart.test.ts`、`src/new-components/chat/content/eduMarkdownComponents.test.tsx`、`src/new-components/chat/input/QuestionDock.test.tsx`、`src/utils/columnLabels.test.ts`、`src/utils/toolLabels.test.ts`（另有 `scripts/test-assistant-markdown.ts` 手工脚本）
- 样式入口只有一条链：`pages/_app.tsx:13` → `styles/globals.css` → `@import "./theme.css"` / `"./opencode-theme.css"`（`globals.css:1-2`）。
  **`styles/gov-theme.css` 全仓 0 引用（死文件）。**
- `tailwind.config.js`：`important: true`、`darkMode: "class"`、自定义 `gradient-light/dark`（`:24-28,31,32`）。

---

## 2. 页面路由地图（`pages/`，Next pages router）

### 2.1 应用骨架

| 路由 | 文件 | 干什么 |
|---|---|---|
| 全局 `_app` | `pages/_app.tsx` | 认证守卫（`:204-228`）+ 布局装配：`SideBar`、顶部工作空间切换、用户菜单、`Watermark` 水印、`ConfigProvider` 主题（`:120-201`）。`isBypassLayout` 跳过布局：`/login`、`/mobile*`、`/share*`、`/construct/app/extra`（`:33-37`） |
| `_document` | `pages/_document.tsx` | 仅标准 `<Html>/<Main>/<NextScript>` |

### 2.2 业务路由

| 路由 | 文件 | 作用 | 主要后端调用 |
|---|---|---|---|
| `/` | `pages/index.tsx` | 探索广场：大输入框 + 4 张示例卡（硬编码教育问句，`:14-42`）+ 数据源选择；提交跳 `/chat?q=..&ds=..`（`:61-68`）。读 `sessionStorage.prefill_prompt`（`:51-57`） | `datasourceApi.list` |
| `/chat` | `pages/chat/index.tsx` | **主工作台**：45%/55% 双栏，左=对话（`ChatContentContainer`）+ 输入 + `QuestionDock`，右=`ChatExecutionPanel`（`:210-265`）。消费 `?q=`、`?conversation_id=`（`:96-146`），监听 `chat:new-task` 事件 | `sendMessageStream` |
| `/login` | `pages/login.tsx` | 登录页（用户名密码），成功后写 token 并 `router.replace(redirect)`（`:29-48`）；读取 `auth_expired_tip` 提示过期（`:20-27`） | `POST /system/login` |
| `/conversations` | `pages/conversations/index.tsx` | **纯静态桩**：写死 3 条假会话（`:4`），链接指向 `/chat?id=n`（无对应路由处理） | 无 |
| `/mobile/chat` | `pages/mobile/chat/index.tsx` | 移动端简版聊天：复用 `useChat` + **旧版** `components/chat/*`（`:2-5`） | `sendMessageStream` |
| `/share/[token]` | `pages/share/[token].tsx` | 占位页，只显示 token | 无 |
| `/construct` | `pages/construct/index.tsx` | 后台总览 Tabs（App/Datasource/Permission/Knowledge 链接） | 无 |
| `/construct/database` | `pages/construct/database/index.tsx` | 数据源 CRUD + 连接测试（`:119-146`） | `datasourceApi.*` |
| `/construct/analysis` | `pages/construct/analysis/index.tsx` | **分析工具**：技能卡列表（读静态 `public/education_skills.json`，`:242`）→ 选报告类型+受众+筛选 → 生成/批量生成报告 → iframe 预览 → HTML/Word/PDF 导出（`:380,434,470,501-580`） | `educationApi.generateReport/saveReportHistory/batchReport` |
| `/construct/analysis/history` | `pages/construct/analysis/history.tsx` | 报告历史列表/详情/删除（`:29,49,81`） | `educationApi.listReportHistory/getReportHistoryDetail/deleteReportHistory` |
| `/construct/education/raw-score-import` | `pages/construct/education/raw-score-import.tsx`（1035 行） | **当前成绩导入主入口**：考试批次、试卷/科目状态、宽表+小题分模板下载、两段式预览→执行（`:190-389`） | `/education/raw-score-import/*` |
| `/construct/education/score-import` | `pages/construct/education/score-import.tsx` | **旧版导入页**（脱敏成绩直接导入），侧栏已不链接 | `/education/score-import/*` |
| `/construct/education/line-reach` | `pages/construct/education/line-reach.tsx` | 达线看板：KPI 卡 + `G2Chart`（`:140,246-290`） | `/education/dashboards/line-reach` |
| `/construct/education/fraction-bar` | `pages/construct/education/fraction-bar.tsx` | 预测分数线录入/列表 + 重算指标（`:79,178,196`） | `/education/fraction-bar`、`/education/score-indicator/recompute` |
| `/construct/education/anomaly-alerts` | `pages/construct/education/anomaly-alerts.tsx` | 异常提醒列表/详情/确认（`:82,103,116`） | `/education/anomaly-alerts*` |
| `/construct/education/anomaly-rules` | `pages/construct/education/anomaly-rules.tsx` | 阈值/规则配置读取、更新、重置（`:47,64,85`） | `/education/report-config*` |
| `/construct/skills` | `pages/construct/skills/index.tsx` | 技能市场（`education_skills.json`），复制提示词 → 写 `prefill_prompt` → 跳首页（`:69,124`）。**侧栏未链接 = 孤儿路由** | 静态 JSON |
| `/construct/permission` | `pages/construct/permission/index.tsx` | `export { default } from "./config"` → 再转 `./data-rules`（两层别名） | — |
| `/construct/permission/config` | `pages/construct/permission/config.tsx` | 同上 → `./data-rules` | — |
| `/construct/permission/data-rules` | `pages/construct/permission/data-rules.tsx`（730 行） | 行/列级数据权限规则组 CRUD（`:131,146,157,291,387`） | `/ds_permission/*`、`/permission/*` |
| `/construct/permission/edu` → `edu-permission.tsx` | `pages/construct/permission/edu.tsx:1` + `edu-permission.tsx`（608 行） | 教育权限（角色/学校/班级/学生）单人+批量绑定、生效预览；模板下载**用裸 fetch**（`:249`） | `/permission/edu/*`、`/user/{id}/edu-scope` |
| `/construct/permission/users` | `pages/construct/permission/users.tsx` | 用户列表/新建（`:16,84`） | `/user/pager/*`、`/user` |
| `/construct/permission/workspaces` | `pages/construct/permission/workspaces.tsx` | 工作空间 + 成员管理（`:41-274`） | `/system/workspace*` |
| `/construct/permission/members` | `pages/construct/permission/members.tsx` | 按 workspace 看成员（`:17-18`） | `/system/workspace/uws/pager` |
| `/construct/permission/resources` | `pages/construct/permission/resources.tsx` | 资源授权关系（`:46`） | `/permission/grants/resource` |
| `/construct/permission/roles` | `pages/construct/permission/roles.tsx` | 角色定义 + 用户角色映射（`:34-35`） | `/permission/roles`、`/permission/grants/user-role` |
| `/construct/permission/privacy` | `pages/construct/permission/privacy.tsx` | 数据脱敏开关（`:14,30`） | `/permission/edu-privacy` |
| `/construct/permission/menu` | `pages/construct/permission/menu.tsx` | 菜单可见性开关（`:64,80`） | `/permission/menu-visibility` |
| `/system/log/access` `/operation` `/login` | `pages/system/log/*.tsx` | 三类审计日志分页查询（`:83 / :116 / :86`） | `/audit/*/pager/{page}/{size}` |

### 2.3 占位路由（`SectionPlaceholder` 一行页，非死代码但无功能）

`pages/construct/app/index.tsx`、`app/extra.tsx`、`dbgpts/index.tsx`、`flow/index.tsx`、`knowledge/index.tsx`、`models/index.tsx`、`prompt/index.tsx`、`prompt/[type]/index.tsx`
→ 全部渲染 `src/components/layout/SectionPlaceholder.tsx`（13 行）。

---

## 3. `src/` 目录清单

### 3.1 `src/api/`（API 层，仅 6 个模块 + adapter）

| 文件 | 职责 |
|---|---|
| `src/api/client.ts`（65） | 唯一通用请求封装 `apiRequest`：拼 baseURL、带 token/workspace header、401 跳登录、拆 `{code,message,data}` 信封 |
| `src/api/auth.ts`（69） | `login()`（**裸 fetch**，form-urlencoded，`:39-65`）、`getCurrentUser()`（`/system/me`）、`CurrentUser`/`edu_scope` 类型 |
| `src/api/system.ts`（98） | 用户分页/新建、工作空间 CRUD、工作空间成员（uws）增删改查 |
| `src/api/permission.ts`（135） | 角色、用户角色授权、资源授权、数据规则组（`/ds_permission/*`）、教育权限（角色/scope/批量绑定/生效预览）、菜单可见性、脱敏开关 |
| `src/api/datasource.ts`（86） | 数据源 CRUD、连接测试、表/字段列表 |
| `src/api/audit.ts`（110） | 三类审计日志分页 + 查询参数构造 |
| `src/api/education.ts`（1006） | **教育模块超级客户端**：导入（总分/小题分/宽表）、报告生成/批量/历史、异常、达线、分数线、指标重算、模板下载。⚠️ 全部用**裸 `fetch`**，不复用 `apiRequest` |

### 3.2 `src/api/adapter/`

| 文件 | 职责 |
|---|---|
| `src/api/adapter/chatAdapter.ts`（412） | 后端 ↔ 前端视图模型适配 + **SSE 事件分发中枢**（见 §4.3） |
| `src/api/adapter/chatAdapter.test.ts`（96） | 只测 `replyQuestion/rejectQuestion` 的 URL 与 body |

### 3.3 `src/app/`

| 文件 | 职责 |
|---|---|
| `src/app/chat-context.tsx`（59） | 全局 UI Context：`mode`（dark/light）、`isMenuExpand`、`isContract`、`currentDialogInfo` |

### 3.4 `src/auth/`

| 文件 | 职责 |
|---|---|
| `src/auth/session.ts`（19） | token 存 `localStorage["sqlbot_access_token"]`，`get/set/clear/isAuthed`。**没有任何路由守卫逻辑**（守卫在 `_app.tsx`） |

### 3.5 `src/components/`（旧/主用组件）

| 文件 | 行数 | 职责 |
|---|---|---|
| `layout/side-bar.tsx` | 483 | 侧栏：主菜单、权限/日志子菜单、会话历史（重命名）、新建任务、菜单可见性过滤 |
| `layout/MainShell.tsx` | 54 | **死代码**：全仓无 import，旧版 Layout 外壳 |
| `layout/SectionPlaceholder.tsx` | 13 | 占位卡片 |
| `chat/ChatExecutionPanel.tsx` | 1278 | **右栏工作台**：步骤/摘要 Tab、四角色专家条、SQL/数据/图表三视图、报告 iframe 预览、建议编辑、审核、Word/PDF/HTML 导出 |
| `chat/ChatContentContainer.tsx` | 279 | 左栏对话容器：按 run 组装轮次、欢迎页、追问 chips、滚动吸底 |
| `chat/G2Chart.tsx` | 267 | @antv/g2 封装 + 图表字段推断工具（`inferChartFields/pickYField/preferQueryTableOverChart/formatQuerySetLabel`） |
| `chat/AgentTeamStrip.tsx` | 161 | 顶部四角色流水线状态条 |
| `chat/DatasourcePicker.tsx` | 122 | 数据源下拉（Popover+搜索），首页/输入栏共用 |
| `chat/ChatHeader.tsx` | 28 | 仅移动端用的简易标题条 |
| `chat/ChatInputPanel.tsx` | 70 | **旧版输入面板**（含假的 Temperature 控件），仅移动端页引用 |
| `chat/ChatSider.tsx` | 37 | 旧版会话侧栏（仅被无人引用的 re-export 引用 → 实际死代码） |
| `chat/PromptBot.tsx` | 25 | 移动端悬浮提示词按钮 |
| `chat/G2Chart.test.ts` | 45 | 图表推断函数单测 |

### 3.6 `src/new-components/`（DB-GPT 风格目录，混合 re-export + 真实实现）

| 文件 | 行数 | 真实/壳 | 职责 |
|---|---|---|---|
| `chat/ChatContentContainer.tsx` | 1 | 壳 | `export { default } from "@/components/chat/ChatContentContainer"`（**无人引用**） |
| `chat/header/ChatHeader.tsx` | 1 | 壳 | re-export（无人引用） |
| `chat/sider/ChatSider.tsx` | 1 | 壳 | re-export（无人引用） |
| `chat/context.ts` | 50 | 真实 | `ChatContentContext`：把 `useChat` 的能力 + 参数传给输入区组件 |
| `chat/assistant.ts` | 37 | 真实 | 助手人设文案、欢迎语、`getFollowups(reportType)` 追问建议表 |
| `chat/input/ChatInputPanel.tsx` | 65 | 真实 | **/chat 在用的输入框**（中文输入法 composition 保护、Enter 发送） |
| `chat/input/ToolsBar.tsx` | 50 | 真实 | 数据源选择 + 「团队协作/单体分析」`Segmented` 切换 agentMode |
| `chat/input/QuestionDock.tsx` | 153 | 真实 | 后端 `question.asked` 澄清问答交互（单选/多选/自定义/确认/取消） |
| `chat/input/ModelSwitcher.tsx` / `Temperature.tsx` / `MaxNewTokens.tsx` / `Resource.tsx` | 26/44/51/33 | 真实但**纯装饰** | 读写 Context 里的 model/temperature/maxTokens/resource，这些值**从不发给后端** |
| `chat/content/OpenCodeSessionTurn.tsx` | 315 | 真实 | 单轮对话渲染（markdown、思考折叠、阶段时间线、指标条、复制） |
| `chat/content/eduMarkdownComponents.tsx` | 150 | 真实 | 教育报告风格 markdown 表格（数字列右对齐、薄弱行高亮、全屏 Modal） |
| `chat/content/ChatDefault.tsx` | 27 | 真实但死 | 无引用 |
| `chat/content/OpenCodeChatCompletion.tsx` | 52 | 真实但死 | 无引用（旧版消息列表渲染） |

### 3.7 `src/hooks/`

| 文件 | 行数 | 职责 |
|---|---|---|
| `src/hooks/useChat.ts` | 1388 | **前端核心状态机**：SSE 订阅、步骤/报告/查询结果/指标/澄清 状态、会话加载、中止、报告审核与建议持久化 |

### 3.8 `src/utils/`

| 文件 | 行数 | 职责 |
|---|---|---|
| `toolLabels.ts` | 1100 | 工具名/步骤名→中文文案映射（`TOOL_COPY` 大表）、四角色识别、`buildStoryPhases/summarizeRun`、markdown 归一化、`<think>` 抽取、SQL 剥离 |
| `agentTeam.ts` | 241 | 四角色流水线（`FLOW_AGENTS`/meta/头像）、`computeFlowStatus`、`groupStepsByAgent`、协作文案 |
| `userWatermark.ts` | 197 | 水印：DOM 覆盖层、iframe 绑定、HTML 注入、canvas 盖章、`captureElementWithWatermark` |
| `exportReportWord.ts` | 131 | 报告→Word(.doc)：iframe 截图 + 按 A4 切片 + msword HTML 包装下载 |
| `reportRecommendations.ts` | 114 | 报告「改进建议」区块的正则提取与回写（对齐后端 `report_edit.py`） |
| `runMetrics.ts` | 97 | Token/耗时格式化、`computeProgressPct`（team 与单 agent 两套进度模型） |
| `columnLabels.ts` | 124 | 英文/拼音列名→中文表头（含 `avg_sx`→「数学均分」类组合规则） |
| `formatNumericDisplay.ts` | 22 | 数值展示格式化（千分位/小数） |
| `conversationTitle.ts` | 14 | 会话标题截断 + `conversation:changed` 事件 |
| `uuid.ts` | 31 | `genUUID()`，兼容非安全上下文（局域网 http）无 `crypto.randomUUID` |
| 5 个 `*.test.ts(x)` | — | 单测 |

---

## 4. 核心机制精读

### 4.1 项目里没有 `pages/api/`：前端如何到达后端

```
浏览器 ──fetch(NEXT_PUBLIC_API_BASE_URL + path)──▶ 后端
                     │
                     └─ 开发库 .env.local 是 http://localhost:8000/api/v1（绝对地址，跨域直连）
                        生产若用 /api，则 next.config.js rewrites → BACKEND_URL + path
```

- baseURL 唯一来源：`src/api/client.ts:8`（`process.env.NEXT_PUBLIC_API_BASE_URL ?? "/api/v1"`），经 `getApiBaseUrl()`（`:25-27`）暴露。
- 因此**不存在服务端 BFF 路由**；`next.config.js:27-38` 的 rewrite 是唯一“代理”，且只在 `BACKEND_URL` 有值时启用。

### 4.2 聊天 SSE 客户端协议

**传输层**：`src/utils/sse.ts`（85 行）

| 环节 | 位置 | 说明 |
|---|---|---|
| 请求 | `sse.ts:41-50` | `POST` + `Accept: text/event-stream` + JSON body + `AbortSignal` |
| 非 2xx | `sse.ts:52-55` | 读 text 当错误信息抛出（后端错误信封能透出） |
| 帧切分 | `sse.ts:66-70` | 按 `\r?\n\r?\n` 切帧，**剩余部分留 buffer**（跨 chunk 安全） |
| 帧解析 | `sse.ts:27-38` | 逐行：`:` 开头忽略；`event:` 取事件名（默认 `message`）；`data:` 多行用 `\n` join；`JSON.parse` 失败则原样返回字符串（`safeParse`） |
| 收尾 | `sse.ts:72-76` | flush 残余帧 → `onDone()` |
| 中止 | `sse.ts:79-81` | `AbortError` 返回 `{aborted:true}`，不算错误 |

**事件层**：`src/api/adapter/chatAdapter.ts:274-412`（`sendMessageStream`）——共 **20 种事件**：

| 事件 | 载荷 | 前端处理（`useChat.ts`） |
|---|---|---|
| `step` | `{label,detail,status,elapsed_ms}` | 追加执行步骤 |
| `plan` | `{plans[],sub_task_agents[]}` | 生成 N 条计划步骤，初始化进度 |
| `plan_update` | `{index,state:running/ok/error,sub_task,sub_task_agent,sql,row_count,error}` | 更新/补建计划步骤 + 进度 |
| `reasoning` | `{text}` | 追加「Agent 思考」步骤（不进气泡） |
| `agent_thought` | `{text,sub_task_index}` | 同上（带子任务下标） |
| `agent_speak` | `{agent,status,error}` | 驱动四角色专家条状态（`:610-635`） |
| `sql` | `{sql,chart_type}` | 记 `latestSql` + 步骤 |
| `result` | `{columns,rows,row_count}` | 入 `queryResults` |
| `tool_call` | `{tool,args,round,sub_task_index}` | 步骤（running） |
| `tool_result` | `{tool,success,content,data,round,sub_task_index,elapsed_ms}` | 步骤收口；`execute_sql` 结果入表；`data.html` 提取报告（`:718-791`） |
| `chart` | `{chart_type,chart_config}` | 写 `chartByRunId[runId]` |
| `report` | `{title,html,mode,sub_task_index,report_type,report_type_label}` | 追加报告（标题规范化） |
| `final_answer` | `{text\|content}` | 写助手气泡 |
| `summary` | `{content}` | 写气泡 + `summaryByRunId` |
| `usage` | `{prompt_tokens,completion_tokens,total_tokens,delta_total}` | 更新 token 指标 |
| `clarify` | `{prompt,missing[],options,filled,original_question,report_type}` | 澄清 chips |
| `question.asked` / `question.rejected` | `{request_id,conv_id,questions[]}` | 弹 `QuestionDock` / 关闭 |
| `error` | `{error}` | 步骤置 error + 气泡「请求失败：…」 |
| `done` | `{record_id}` | 收口步骤、停表、进度=100、回拉 record 覆盖 token/耗时、持久化报告（`:900-1021`） |

- SSE 请求头在 `chatAdapter.ts:18-33`（`Authorization` + `X-Workspace-Oid` + `Accept`）。
- 该 switch **无 `default` 分支**（`chatAdapter.ts:286-408`）：后端新增事件前端静默丢弃，不会报错也不会提示。

### 4.3 API 客户端封装（base/request 层）

`src/api/client.ts`：

| 关注点 | 实现 | 行 |
|---|---|---|
| baseURL | `NEXT_PUBLIC_API_BASE_URL ?? "/api/v1"` | `:8` |
| 鉴权 | `Authorization: Bearer ${localStorage.getItem("sqlbot_access_token")}` | `:30,35` |
| 租户 | `X-Workspace-Oid` ← `localStorage["frontend_react_workspace_oid"]` | `:4,31-36` |
| 401 | 清 token → 写 `sessionStorage.auth_expired_tip` → 跳 `/login?redirect=当前路径` | `:11-17,44-47,55-58` |
| 信封 | 有 `code` 字段则视为信封：`code===200` 返回 `data`，否则抛 `message` | `:51-63` |
| 非信封 | 直接返回整个 JSON | `:64` |
| 其它错误 | `throw new Error("Request failed: " + status)` | `:48` |

⚠️ **两套并存的请求实现**：
- 走 `apiRequest`：auth(system/me)、system、permission、datasource、audit、chatAdapter。
- 走裸 `fetch`：`auth.ts:39`（登录）、`education.ts` **全部 26 处**、`edu-permission.tsx:249`（模板下载）。
  裸 fetch 版本没有统一的 401 跳转、没有 workspace header 复用（`education.ts:220-230` 自建 `authHeaders()`），错误语义也不一致（有的抛 `Unauthorized`、有的抛后端 message、有的返回 `{ok:false}`）。

### 4.4 适配层 `src/api/adapter/` 在做什么

`chatAdapter.ts` 承担三件事，**不是**纯 DTO 映射：

1. **类型契约层**：定义前端视图模型 `Conversation / ConversationRecord / ConversationDetail / Question / ClarifyPayload`（`:44-148`），即“后端 record 的字段形状”就是前端模型，没有做驼峰化或字段重命名。
2. **SSE → 回调编排**：把 20 种事件翻译成 `StreamHandlers` 的 20 个 `onXxx` 回调（`:150-199` 定义、`:286-408` 分发），并做**轻量归一**（缺省值填充、`typeof` 类型守卫、`NaN` 兜底）。前端页面/`useChat` 只认回调，不认原始事件名。
3. **REST 封装 + 会话上下文同步**：会话 CRUD、报告审核 PATCH/PUT、问答回复/拒绝（`:201-272`）；`syncWorkspaceOid()`（`:9-16`）在打开跨工作空间的会话时切换本地 workspace 并广播 `workspace:changed`。

真正的“展示层视图模型转换”发生在 `useChat.ts`（事件 → `Message/ExecutionStep/ReportPayload/QueryResult`，`:26-81` 定义类型，`:388-1073` 转换）和 `deriveReportsFromRecord()`（`:223-275`，历史记录回放）。

### 4.5 报告渲染与导出链路

**渲染（预览）**：报告 HTML 存后端 record，前端直接 `<iframe srcDoc={html}>`：
- 聊天右栏：`ChatExecutionPanel.tsx:1058-1066`（内嵌）、`:1247-1254`（全屏弹窗）
- 分析工具：`analysis/index.tsx:885-892`（内嵌）、`:963-969`（弹窗）
- `onLoad` 统一调 `bindIframeWatermark(iframe)`（`userWatermark.ts:120-126`）：改写表格样式（`alignEduTableNumericHeaders` 注入 `.edu-table`/`.num`/`.is-weak` 样式）+ 打水印。

**导出（4 条路径，全部客户端）**：

| 格式 | 实现 | 位置 | 机制 |
|---|---|---|---|
| HTML | Blob 下载（含水印注入） | `ChatExecutionPanel.tsx:485-493`；`analysis/index.tsx:501-515` | `stampHtmlWatermark(html)` → `<a download>` |
| **PDF** | `html2canvas` + `jspdf` | `ChatExecutionPanel.tsx:514-559`；`analysis/index.tsx:535-580` | iframe body 截图（scale 2，带水印）→ 单张长图按 A4 高度**负偏移分页** addImage → `pdf.save()` |
| **Word (.doc)** | `exportReportAsWord` | `exportReportWord.ts:94-131`，调用点 `ChatExecutionPanel.tsx:497-513`、`analysis/index.tsx:517-533` | iframe 截图 → 按 `canvas.width*1.414` 切片（`sliceCanvasToDataUrls:52-75`）→ 拼 `application/msword` HTML（内嵌 base64 PNG）→ 下载 `.doc` |
| 截图水印 | `captureElementWithWatermark` | `userWatermark.ts:182-197` | 截图前隐藏 DOM 水印层，截图后在 canvas 上重复绘制斜向文字，避免双水印 |

⚠️ **没有服务端 docx/xlsx 导出调用**（后端 `grep docx|xlsxwriter` 仅命中 `src/agent/education/api.py:1299` 的 xlsx **模板下载** mime）。CLAUDE.md 里“docx/xlsx 服务端导出”在本前端分支上不存在；前端唯一 xlsx 交互是**下载导入模板**（`education.ts:233-247,721-772`）和**上传 Excel**。

### 4.6 认证与路由守卫

| 环节 | 位置 | 行为 |
|---|---|---|
| token 存储 | `src/auth/session.ts:1,7-20` | `localStorage["sqlbot_access_token"]`，无过期时间/刷新逻辑 |
| 全局守卫 | `pages/_app.tsx:204-228` | `router.isReady` 后：无 token 且非公共页 → `/login?redirect=asPath`；有 token 且在 `/login` → `/`；有 token → 调 `/system/me` 失败则清 token 跳登录 |
| 公共页白名单 | `_app.tsx:209` | `/login`、`/share*`、`/mobile*`（`/construct/app/extra` 只在布局层白名单，`_app.tsx:37`） |
| 401 兜底 | `client.ts:11-17` | 全局拦截，写 `auth_expired_tip`，`login.tsx:20-27` 弹「登录已过期」 |
| 布局装配 | `_app.tsx:69-103` | 拉 `listWorkspaces + getCurrentUser`；决定 workspace、写 localStorage、广播 `workspace:changed`；用户菜单登出（`:46-54`） |
| 水印 | `_app.tsx:115-118,137-147`；`userWatermark.ts` | 用账号+姓名做 Ant `Watermark`，并在报告 iframe/导出中复刻 |

**没有 middleware.ts、没有基于角色的页面级守卫**——权限只影响“菜单显不显示”，直接敲 URL 仍可进入页面。

### 4.7 菜单 / 权限如何驱动 UI

`src/components/layout/side-bar.tsx`：

1. `routes`（`:32-44`）11 个主菜单项；`permissionSubRoutes`（`:46-54`）、`systemSubRoutes`（`:56-60`）子菜单。
2. 加载 `getCurrentUser()` + `permissionApi.getMenuVisibility()`（`:77-97`）。
3. `visibleRoutes`（`:207-221`）三层过滤：
   - `isPlatformAdmin` 为真时**跳过**菜单可见性过滤；`isPlatformAdmin` 是**硬编码** `currentUser.account === "admin" && currentUser.id === 1`（`:75`），忽略了后端返回的 `is_platform_admin`（`api/auth.ts:23`）。
   - `menuVisibility[key] !== false` 才显示（后端未配置的 key 默认可见）。
   - 教育角色硬编码过滤：`bureau_admin|student` 隐藏「异常提醒」；`student` 再隐藏「达线看板」「预测分数线」（`:214-219`）。
4. 菜单项点击行为：权限/日志是折叠父项，其余直接 `router.push`（`:320-344`）。
5. 会话历史：`listConversations(50)`，按今天/昨天/日期分组，支持内联重命名（`:101-193,400-494`）。

### 4.8 `new-components/chat/` 与 `components/chat/` 的关系

结论：**不是两套并行实现，而是“一层薄壳 + 一次半途迁移”**。

- 三个纯 re-export 壳（`new-components/chat/ChatContentContainer.tsx:1`、`header/ChatHeader.tsx:1`、`sider/ChatSider.tsx:1`）把旧路径转发到 `components/chat/`，且**这三个壳本身无人引用**。
- 真正被使用的分界：
  - `/chat`（主工作台）用 **new-components** 的 `input/ChatInputPanel`、`input/QuestionDock`、`context`、`assistant`、`content/OpenCodeSessionTurn`、`content/eduMarkdownComponents`，同时用 **components** 的 `ChatContentContainer`、`ChatExecutionPanel`、`G2Chart`、`DatasourcePicker`、`AgentTeamStrip`（`pages/chat/index.tsx:4-9`）。
  - `/mobile/chat` 用 **components** 的旧 `ChatHeader/ChatInputPanel/ChatContentContainer/PromptBot`（`pages/mobile/chat/index.tsx:2-5`）。
- 为什么两套：`new-components/chat/` 是照搬 DB-GPT 前端的目录结构（同名组件、`assistant`+`ChatContentContext` 模式、model/temperature/maxTokens 参数面板），迁移到本项目时只搬了输入区与消息轮次渲染；旧的 `ChatContentContainer/ChatExecutionPanel` 因为已深度定制（报告/图表/导出）而留在 `components/chat/`。留下的壳文件与 `ChatDefault/OpenCodeChatCompletion` 是迁移残留。

---

## 5. 前端调用的后端接口清单（前缀分布）

后端统一前缀 `/api/v1`（`src/common/router.py:36`）：`/system`、`/user`、`/permission`、`/permission/edu`、`/system/workspace`、`/datasource`、`/ds_permission`、`/chat`、`/chat/question`、`/education`、`/audit`。

| 前缀 | 主要接口 | 用途 | 前端位置 |
|---|---|---|---|
| `/system` | `POST /system/login`、`GET /system/me`、`GET/POST/PUT/DELETE /system/workspace`、`.../uws/pager|option` | 登录、当前用户、工作空间与成员 | `api/auth.ts:39,68`、`api/system.ts:47-96`、`_app.tsx:72` |
| `/user` | `GET /user/pager/{p}/{s}`、`POST /user`、`PATCH /user/status`、`GET/PUT/DELETE /user/{id}/edu-scope` | 用户管理、教育权限数据范围 | `api/system.ts:39-46`、`api/permission.ts:102-112` |
| `/permission` | `/permission/roles`、`/grants/user-role`、`/grants/resource`、`/data-rules`、`/edu/roles`、`/edu/batch-bind`、`/edu/effective`、`/menu-visibility`、`/edu-privacy`、`/edu/template` | 角色/授权/菜单可见性/脱敏/教育权限 | `api/permission.ts:81-135`、`edu-permission.tsx:249` |
| `/ds_permission` | `POST /ds_permission/list`、`/save`、`POST /ds_permission/delete/{id}` | 行/列级数据权限规则组 | `api/permission.ts:86-100` |
| `/datasource` | `GET/POST/PUT/DELETE /datasource`、`POST /{id}/test-connection`、`/{id}/tables`、`/table/{tid}/fields` | 数据源管理 | `api/datasource.ts:60-86` |
| `/chat` | `POST /chat/chat-stream`（SSE，**核心**）、`GET/POST/PUT /chat/conversations`、`GET /chat/conversations/{id}`、`PATCH /chat/conversations/{cid}/records/{rid}/reports/{idx}`、`PUT .../records/{rid}/reports` | 分析流、会话与报告持久化 | `api/adapter/chatAdapter.ts:201-272,280` |
| `/chat/question` | `POST /chat/question/{request_id}/reply`、`/reject` | 澄清问答回填 | `api/adapter/chatAdapter.ts:261-272` |
| `/education` | `score-import/templates/{type}`、`score-import/{preview|execute}`、`generate-report`、`batch-report`、`meta/options`、`save-report-history`、`report-history`(+`/{recordId}`/`/{conversationId}`)、`report-config`(+`/reset`)、`anomaly-alerts`(+`/{id}`/`/{id}/confirm`)、`dashboards/line-reach`(+`/meta`)、`fraction-bar`(GET/POST)、`score-indicator/recompute`、`raw-score-import/batches`(GET/POST)、`raw-score-import/papers`、`raw-score-import/templates/{kind}`、`raw-score-import/{overview|detail}-{preview|execute}` | 教育模块全部业务 | `api/education.ts:234-818` |
| `/audit` | `/audit/access/pager/{p}/{s}`、`/operation/pager/...`、`/login/pager/...` | 三类审计日志 | `api/audit.ts:95-109` |

---

## 6. 结构性风险 / 技术债（附证据）

### 6.1 重复实现

1. **PDF 导出代码几乎逐行复制两份**：`ChatExecutionPanel.tsx:514-559` 与 `analysis/index.tsx:535-580`（约 45 行，仅错误文案不同）。Word 导出已抽成 `exportReportWord.ts`，PDF 却没有。
2. **两套 HTTP 客户端**：`apiRequest`（`client.ts:29-65`）vs `education.ts` 里 26 处裸 `fetch`（`education.ts:234,253,274,305,329,353,377,396,415,430,442,459,482,498,514,535,555,572,596,622,648,671,693,730,778,801`），每处重复「加 header + 判 401 + 拆 code/message/data + 返回 `{ok,message,data}`」。后果：教育模块 401 不会触发全局跳登录（只抛 `Unauthorized`），与其它模块行为不一致。
3. **`authHeaders()` 与 `chatStreamHeaders()` 重复**：`education.ts:222-230` vs `chatAdapter.ts:18-33`，都自行读 token 与 `frontend_react_workspace_oid`。workspace key 字符串在本仓被复制 4 次：`client.ts:4`、`chatAdapter.ts:6`、`education.ts:4`、`_app.tsx:16`。
4. **角色/报告类型清单三处维护**：`useChat.ts:118-153`（`REPORT_TYPE_MARKERS`，含中文标签）、`analysis/index.tsx:52-70`（`OPEN_REPORT_TYPES`）、`analysis/index.tsx:72-79`（受众选项，同一语义另有 `_app` 侧 edu_role 硬编码）。
5. **表格样式规则重复**：`userWatermark.ts:72-118`（导出用 CSS 字符串）与 `styles/globals.css:176-313`（`.edu-md-table-*`）是同一套「圆角表头/数字右对齐/薄弱高亮」的两次实现。
6. **`datasource.ts` 的 `list` 参数拼接**：`api/datasource.ts:62` 用 `URLSearchParams(params as Record<string,string>)` 把数字强转，`workspaceOptionPager` 等则手写 query 字符串（`api/system.ts:66-96`），风格不统一。

### 6.2 死代码 / 孤儿路由

| 项 | 证据 |
|---|---|
| `src/components/layout/MainShell.tsx` | 全仓无 import（仅自身定义 `:22`），且引用未导入的 `.oc-*` 类 |
| `src/new-components/chat/ChatContentContainer.tsx:1`、`header/ChatHeader.tsx:1`、`sider/ChatSider.tsx:1` | 纯 re-export 壳，无引用方 |
| `src/components/chat/ChatSider.tsx` | 仅被上面的死壳引用 → 实际不可达 |
| `src/new-components/chat/content/ChatDefault.tsx:10`、`OpenCodeChatCompletion.tsx:16` | 无引用方 |
| `styles/gov-theme.css` | 全仓 0 引用（`globals.css:1-2` 只 import theme/opencode-theme） |
| `pages/conversations/index.tsx:4` | 写死 3 条假会话，但被 `side-bar.tsx:393` 链接 |
| `pages/share/[token].tsx`、8 个 `SectionPlaceholder` 页 | 占位 |
| `pages/construct/education/score-import.tsx` | 旧导入页，侧栏只链 `raw-score-import`（`side-bar.tsx:38`） |
| `pages/construct/skills/index.tsx` | 无任何入口链接（`grep construct/skills` 仅命中自身注释） |
| 未使用的 API 函数 | `permissionApi.listDataRules`（`permission.ts:85`）、`datasourceApi.tableList/fieldList`（`datasource.ts:82-85`）、`systemApi.updateUserStatus/addWorkspaceMember/workspaceOptionByKeyword`（`system.ts:42-46,70-74,94-97`）——全仓无调用点 |
| 装饰性假参数 | `pages/chat/index.tsx:45-48,182` 的 `model/temperature/maxNewTokens/resource` + `new-components/chat/input/{ModelSwitcher,Temperature,MaxNewTokens,Resource}.tsx`，值从不进入请求体 |

### 6.3 硬编码

1. `_app.tsx:1-2`：`isPlatformAdmin` 用 `account==="admin" && id===1`（`side-bar.tsx:75`）判定，忽略后端 `is_platform_admin`（`api/auth.ts:23`）；换管理员账号即失效。
2. `side-bar.tsx:207-221`：教育角色→菜单的规则写死在前端（`bureau_admin`/`student` 特判）。
3. `pages/index.tsx:14-42`：4 条真实学校/班级问句（扬州中学等）硬编码在页面里。
4. `analysis/index.tsx:52-70`：已开放报告类型白名单硬编码（与技术白名单重复）。
5. `.env.local:1-4` 已入库：`http://localhost:8000/api/v1`、`datasource_id=12`。
6. `chatAdapter.ts:341-342` 等：`final_answer` 兼容 `text`/`content` 两种字段名，属后端契约漂移的前端兜底。
7. `reportRecommendations.ts:5-6`：靠中文标题正则（改进建议/教学建议/…）定位区块，后端改标题即静默失效。

### 6.4 类型缺失 / 契约脆弱

1. `src/api/education.ts` 中所有 `fetch` 响应用 `as {code?,message?,data?}` 断言（如 `:261-265,285-289,311-315`），无运行时校验；`data.missing_subjects` 等用 `?? []` 兜底（`:709-717`）掩盖后端字段改名。
2. `client.ts:19-23` 的 `ApiEnvelope` + `:52-63` 的 `"code" in payload` 判别，对「后端直接返回业务对象且恰好含 code 字段」的场景会误判。
3. SSE 事件分发 `chatAdapter.ts:286-408` 无 `default` 分支，且回调参数大量 `as` 断言（`:288,304,308,311,314`…）——新增事件无编译期提示。
4. `src/hooks/useChat.ts:1388` 行单 hook 承担 8 类状态 + 4 个 ref 计数器（`:296-312`），`send` 一个 `useCallback` 跨 `:388-1073`（686 行）；`onDone` 内还有 120 行的报告合并/持久化逻辑（`:900-1021`）与 `patchReport` 的 404 降级重试（`:1323-1335`），极难单元测试（现无 useChat 测试）。
5. `src/utils/toolLabels.ts:1100` 行映射表 + `agentTeam.ts:156-205` 用**标题正则**反推 agent 状态（`/^(Planner|...)\s*:\s*(start|end)$/`），依赖后端文案字面量，后端改一个词条即导致专家条状态错乱。
6. `interface ChatStreamPayload`（`chatAdapter.ts:114-120`）未声明 `report_audience`，但 `useChat.ts:485` 通过展开 `...(targetAudience ? { report_audience: targetAudience } : {})` 发送——类型与行为不一致。
7. `tsconfig.json` 未开 `noUnusedLocals`，上述死导入/死状态不会被 `npm run typecheck` 拦下。

### 6.5 一致性风险（值得优先确认）

1. **菜单权限键不一致**：`side-bar.tsx:32-44` 有 11 个 key（含 `anomaly-alerts`、`anomaly-rules`），`permission/menu.tsx:6-16` 只列 9 个 → 「异常提醒/异常规则」在菜单权限页无法配置，只能靠 edu_role 硬编码隐藏。
2. 侧栏顶部工作空间切换会 `setMainContentKey(k+1)` 强制 remount 主内容区（`_app.tsx:171,192`），但 `SideBar` 自身不 remount，其 `useEffect` 依赖仅为 `pathname`（`side-bar.tsx:107`）——切 workspace 后会话历史的刷新依赖 `workspace:changed` 事件（`:113`），两条刷新路径并存。
3. 聊天页三处竞态防护（`pages/chat/index.tsx:52-55,100-114,135-146`：`consumedExploreQueryRef`/`ignoreExploreQueryRef`/`loadedConversationIdRef`）说明 SSE 与路由 query 的竞态是已知痛点，改这块要格外小心。
4. `next.config.js:7` 关闭 gzip 是 SSE 可用性的前提；若将来加 Nginx 压缩，必须排除 `/api/v1/chat/chat-stream`。

---

## 7. 新人改前端时先看这 5 个文件

1. **`frontend-react/src/hooks/useChat.ts`** — 前端大脑：状态机、SSE 回调落地、会话加载、报告审核持久化。任何“聊天行为不对”的问题 90% 在这里。
2. **`frontend-react/src/api/adapter/chatAdapter.ts`** — 后端契约的唯一入口：20 个 SSE 事件名 + 会话/报告/问答 REST 路径 + 类型定义。改协议先改它。
3. **`frontend-react/pages/chat/index.tsx`** — 主工作台装配与路由/竞态逻辑（`?q=`、`?conversation_id=`、`chat:new-task`），也是“哪套组件在用”的权威样本。
4. **`frontend-react/src/components/chat/ChatExecutionPanel.tsx`** — 右栏全部展示与导出的实现（步骤/图表/报告 iframe/Word/PDF/审核），导出类需求只改这里 + `utils/exportReportWord.ts`。
5. **`frontend-react/src/api/client.ts`** — baseURL、鉴权头、workspace 头、401 跳转的唯一约定；新增 API 模块必须复用它（不要再写裸 `fetch`）。

辅助查阅：`src/utils/toolLabels.ts`（中文文案映射，改文案/加工具必看）、`src/api/education.ts`（教育模块接口全景）、`src/components/layout/side-bar.tsx`（菜单与权限过滤）。
