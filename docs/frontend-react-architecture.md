# awesome-project frontend-react 前端架构深度文档

> 面向主力开发：NL2SQL + 教育成绩分析平台 React 前端（Next.js 14 pages router + React 18 + Ant Design 5 + @antv/g2 v5 + Tailwind 3 + TypeScript strict）。
> 源码根：`frontend-react/`。所有路径均相对该目录（文中用 `pages/...`、`src/...` 表示）。
> 代码规模：`pages/` 约 40 个页面文件，`src/` 约 50 个 ts/tsx 文件，核心 `src/hooks/useChat.ts`（1198 行）、`src/components/chat/ChatExecutionPanel.tsx`（1126 行）、`src/api/education.ts`（757 行）为最重三处。

---

## 1. 目录结构与页面清单

```
frontend-react/
├── package.json / tsconfig.json / next.config.js / tailwind.config.js / postcss.config.js
├── .env.local / .env.example          # 环境变量（见 §2）
├── pages/                             # Next.js pages router（无 app router）
│   ├── _app.tsx / _document.tsx
│   ├── index.tsx                      # 探索广场（首页提问）
│   ├── login.tsx                      # 登录页
│   ├── chat/index.tsx                 # 主聊天页（桌面双栏）
│   ├── conversations/index.tsx        # 占位 stub（3 条假数据）
│   ├── share/[token].tsx              # 分享页 stub（只显示 token）
│   ├── mobile/chat/index.tsx          # 移动端 stub（复用旧组件）
│   ├── construct/                     # 管理台（真实实现集中在 database/analysis/education/permission）
│   └── system/log/                    # 审计日志三页
├── src/
│   ├── api/           # 接口层（client / auth / datasource / system / permission / audit / education / adapter/chatAdapter）
│   ├── app/chat-context.tsx           # 全局主题/侧栏 Context
│   ├── auth/session.ts                # token localStorage 读写
│   ├── components/chat/               # 聊天核心组件（旧版+桌面版）
│   ├── components/layout/             # side-bar（真实使用）、MainShell（死代码）
│   ├── hooks/useChat.ts               # 聊天状态机（核心）
│   ├── new-components/chat/           # 新版聊天壳（context/assistant/content/input/header/sider）
│   └── utils/                         # sse / toolLabels / agentTeam / runMetrics / exportReportWord 等
├── public/                            # 静态资源 + education_skills.json（技能市场配置）
└── styles/                            # globals.css + theme.css + opencode-theme.css + gov-theme.css
```

### 1.1 pages 路由 → 功能模块映射

| 路由 | 文件 | 功能模块 | 实现程度 |
|---|---|---|---|
| `/` | `pages/index.tsx` | 探索广场：提问框 + 4 张教育示例卡片；`?q=&ds=` 跳 `/chat`；sessionStorage `prefill_prompt` 预填 | ✅ 真实 |
| `/login` | `pages/login.tsx` | 账号密码登录（OAuth2 form-urlencoded），`?redirect=` 回跳 | ✅ 真实 |
| `/chat` | `pages/chat/index.tsx` | 主聊天：左对话流 + 右专家执行面板；支持 `?q`/`?ds` 自动提问、`?conversation_id` 加载历史 | ✅ 核心 |
| `/conversations` | `pages/conversations/index.tsx` | 会话列表（**假数据 stub**） | ⚠️ 占位 |
| `/share/[token]` | `pages/share/[token].tsx` | 分享页（**只渲染 token 字符串**） | ⚠️ 占位 |
| `/mobile/chat` | `pages/mobile/chat/index.tsx` | 移动端聊天（复用旧组件集） | ⚠️ 占位 |
| `/construct` | `pages/construct/index.tsx` | 管理台导航卡片 | ⚠️ 简易 |
| `/construct/database` | `pages/construct/database/index.tsx` | 数据源 CRUD + 测试连接 + 编辑弹窗（密码留空不修改） | ✅ 真实 |
| `/construct/analysis` | `pages/construct/analysis/index.tsx` | **分析工具**（921 行）：9 类报告生成表单 + iframe 预览 + HTML/PDF/Word 导出 + 批量多班级生成 | ✅ 核心教育 |
| `/construct/analysis/history` | `pages/construct/analysis/history.tsx` | **报告历史**：列表 + 预览（iframe srcDoc）+ 下载 HTML + 删除 | ✅ 教育 |
| `/construct/education/score-import` | `pages/construct/education/score-import.tsx` | 成绩导入向导（3 步：数据源→总分→明细），preview/execute 两段式 | ✅ 教育 |
| `/construct/education/anomaly-alerts` | `pages/construct/education/anomaly-alerts.tsx` | 异常提醒：临界生/大幅退步/偏科列表 + 详情 Drawer + 确认处理；`accessible=false` 时教育局/学生账号显示拦截文案 | ✅ 教育 |
| `/construct/education/anomaly-rules` | `pages/construct/education/anomaly-rules.tsx` | 异常规则配置（及格/优秀百分比、满分兜底、临界半径、退步阈值、偏科分差 + 五类规则预览表） | ✅ 教育 |
| `/construct/education/fraction-bar` | `pages/construct/education/fraction-bar.tsx` | 预测分数线维护（物理类/历史类分线列分组表）+ 达线指标重算 | ✅ 教育 |
| `/construct/education/line-reach` | `pages/construct/education/line-reach.tsx` | 达线看板：KPI 卡 + 各区达线率 G2Chart 柱图 + 区县/学校嵌套展开表 | ✅ 教育 |
| `/construct/skills` | `pages/construct/skills/index.tsx` | 技能市场：读 `public/education_skills.json`，复制 prompt / 一键生成（跳分析工具 `?report_type=`）/ 探索广场打开 | ✅ 教育 |
| `/construct/permission` | `pages/construct/permission/index.tsx` → re-export `config.tsx` → re-export `data-rules.tsx` | **数据权限**（746 行）：规则组卡片 + 行/列权限编辑（expression_tree JSON 可视化 + 列拒绝列表）+ 受限用户 | ✅ 真实 |
| `/construct/permission/edu` | `pages/construct/permission/edu.tsx` → `edu-permission.tsx` | **教育权限**（643 行）：edu_role（bureau_admin/school_admin/teacher/student）四级范围配置、CSV 批量绑定、生效权限 SQL 预览 | ✅ 教育 |
| `/construct/permission/users` | `pages/construct/permission/users.tsx` | 用户管理（增用户、展示 edu_role 标签） | ✅ 真实 |
| `/construct/permission/workspaces` | `pages/construct/permission/workspaces.tsx` | 工作空间管理 + 成员增删/管理员切换 | ✅ 真实 |
| `/construct/permission/members` | `pages/construct/permission/members.tsx` | 成员列表（只读） | ✅ 真实 |
| `/construct/permission/menu` | `pages/construct/permission/menu.tsx` | 菜单可见性开关（`GET/POST /permission/menu-visibility`） | ✅ 真实 |
| `/construct/permission/roles` | `pages/construct/permission/roles.tsx` | 角色定义 + 用户角色映射（只读） | ✅ 真实 |
| `/construct/permission/resources` | `pages/construct/permission/resources.tsx` | 资源授权列表（只读） | ✅ 真实 |
| `/construct/app` `/construct/app/extra` `/construct/dbgpts` `/construct/flow` `/construct/knowledge` `/construct/models` `/construct/prompt` `/construct/prompt/[type]` | 各 `index.tsx` | **全部是 `SectionPlaceholder` 占位页** | ⚠️ 占位 |
| `/system/log/access` | `pages/system/log/access.tsx` | 访问日志（分页 + 时间范围 + 成功/关键字筛选） | ✅ 真实 |
| `/system/log/operation` | `pages/system/log/operation.tsx` | 操作日志 | ✅ 真实 |
| `/system/log/login` | `pages/system/log/login.tsx` | 登录日志 | ✅ 真实 |

### 1.2 关键组件清单（src/）

| 组件 | 职责（一句话） |
|---|---|
| `src/components/layout/side-bar.tsx` | 全局侧边栏：12 个路由 + 权限/日志子菜单折叠、`menuVisibility` 显隐、按 `edu_scope.edu_role` 过滤（bureau_admin/student 隐藏异常提醒，student 隐藏达线看板/分数线）、历史会话按「今天/昨天/日期」分组 | 
| `src/components/chat/ChatContentContainer.tsx` | 左侧对话流：按 user 消息 runId 聚合成「轮次」，滚动跟随/贴底、欢迎面板、followup 建议 |
| `src/components/chat/ChatExecutionPanel.tsx` | 右侧专家执行面板（1126 行）：分析过程/摘要两 Tab、四角色分组步骤树、单 Agent 扁平时间线、报告 iframe + 多报告下拉切换、复制/下载 HTML、PDF/Word 导出、建议编辑、审核、查询结果 chart/data/sql 三视图 |
| `src/components/chat/G2Chart.tsx` | @antv/g2 v5 声明式封装：column/bar/line/pie 四类型、自动推断 x/y 轴、单色 dashboard 模式、% 域上限、数值标签 |
| `src/components/chat/DatasourcePicker.tsx` | 数据源选择 Popover（搜索 + 「管理数据库」跳转） |
| `src/components/chat/AgentTeamStrip.tsx` | 四角色（Planner→DataAnalyst→Charter→Summarizer）协作条：每角色独立计时、状态标签、流水线箭头 |
| `src/new-components/chat/context.ts` | `ChatContentContext`：replyLoading/handleChat/stopReply/temperature/model/agentMode 等输入区上下文（默认值全空函数） |
| `src/new-components/chat/content/OpenCodeSessionTurn.tsx` | 单轮对话卡片：user 气泡 + 助手 Markdown 回答 + think 折叠 + 进度 phases + metrics 条 |
| `src/new-components/chat/input/ChatInputPanel.tsx` | 桌面输入框（Enter 发送、中文输入法 composition 保护）+ `ToolsBar`（数据源、team/agent Segmented 切换） |
| `src/new-components/chat/input/{ModelSwitcher,Temperature,MaxNewTokens,Resource}.tsx` | 输入区参数控件（**均未真正传后端，见 §6 债 8**） |
| `src/components/chat/ChatHeader.tsx` / `ChatSider.tsx` / `PromptBot.tsx` / `ChatInputPanel.tsx` / `src/components/layout/MainShell.tsx` / `src/new-components/chat/content/{OpenCodeChatCompletion,ChatDefault}.tsx` | 旧版/遗留组件：仅移动端页使用 ChatHeader/ChatInputPanel/PromptBot；MainShell、ChatSider、OpenCodeChatCompletion、ChatDefault 为死代码 |
| `src/components/layout/SectionPlaceholder.tsx` | 占位页统一卡片 |

---

## 2. 技术栈与构建配置

### 2.1 依赖（`package.json`）
- **运行时**：`next@^14.2.33`、`react@^18.3.1`、`antd@^5.27.6`、`@ant-design/icons@^6.0.1`、`@antv/g2@^5.4.8`、`html2canvas@^1.4.1`、`jspdf@^2.5.2`、`react-markdown@^8.0.7` + `remark-gfm@^3.0.1`
- **开发**：typescript@^5.8.3、tailwindcss@^3.4.18、autoprefixer、postcss
- **脚本**：`dev`（`WATCHPACK_POLLING=true next dev -p 3001`，WSL/共享目录用）、`devW`（Windows）、`build`、`start -p 3001`、`lint`（`next lint`）、`typecheck`（`tsc --noEmit`）
- **没有**测试框架、没有 swr/react-query/redux/zustand、没有 axios。

### 2.2 Next.js 配置（`next.config.js`）
1. `compress: false` — **关键**：关闭 Next 内置 zlib gzip，因为 gzip 是缓冲式压缩，会攒住 rewrites 代理的 SSE 小帧直到流结束，导致 `chat-stream` 线上「很久后一次性蹦出」。注释明确未来可交给 Nginx 对非 SSE 路径压缩。
2. `reactStrictMode: true`。
3. `transpilePackages` — 把 antd 及所有 `rc-*` 组件加入转译（处理 ESM/SSR 兼容）。
4. `rewrites()` — 若存在 `BACKEND_URL` 环境变量，把 `/api/:path*` 代理到 `${backend}/:path*`（同源 SSE 代理路径）。

### 2.3 API base URL 注入
- `src/api/client.ts:8`：`const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "/api/v1"`。
- `.env.local`（本机）：`BACKEND_URL=http://localhost:8000/api/v1`、`NEXT_PUBLIC_API_BASE_URL=http://localhost:8000/api/v1`、`NEXT_PUBLIC_DEFAULT_DATASOURCE_ID=12`、`NEXT_PUBLIC_AGENT_MODE=team`。
- `.env.example` 默认 `NEXT_PUBLIC_API_BASE_URL=/api`（走 next.config rewrites 同源代理）。
- 兜底 `/api/v1` 与后端 `common/router.py` 前缀对齐。⚠️ 生产构建走 `.dockerignore`（排除 `.env.local`/`BACKEND_URL` 缺失）时 rewrites 不生效，`NEXT_PUBLIC_*` 需构建期注入或依赖反代，见 §6 债 1。

### 2.4 TypeScript 配置（`tsconfig.json`）
- `strict: true`、`target ES2022`、`moduleResolution: "bundler"`、`noEmit`、`incremental`（产生 `tsconfig.tsbuildinfo`，已被 gitignore）。
- 路径别名：`"@/*": ["src/*"]`（**代码里全部用 `@/` 导入，没有相对导入**）。
- `jsx: preserve`（Next 接管）。

### 2.5 Tailwind（`tailwind.config.js`）
- content 覆盖 `pages/**` 与 `src/**`。
- 主题扩展：`theme.primary #0069fe`、`dark #151622`、`gradientL/gradientR`、`backgroundImage: gradient-light/dark/button-gradient`。
- `important: true`（全局重要标记，覆盖 antd 样式）、`darkMode: "class"`（`_app.tsx` 给 `document.body` 切 `dark`/`light` class）。
- 大量页面用 `oc-*`/`gov-*` CSS 变量（`styles/theme.css`、`styles/gov-theme.css`），深浅色自动切换；另有 `dbgpt-*` 字体工具类（`styles/globals.css` 手写，非 tailwind 配置）。

### 2.6 @antv/g2 图表接入方式
- **唯一封装**：`src/components/chat/G2Chart.tsx`（声明式组件，`Chart` 实例在 `useEffect` 中创建/销毁）。
- 两处使用：① `ChatExecutionPanel.tsx` 查询结果「图表」Tab（用户可切 column/bar/line/pie + 标签显隐）；② `pages/construct/education/line-reach.tsx` 各区达线率柱图（单色 accent + `%` 后缀）。
- 报告内部图表**不经过 G2**：后端渲染进 HTML（Jinja2 模板内嵌 ECharts/G2 script），前端只用 `iframe srcDoc` 展示。

---

## 3. 与后端交互

### 3.1 统一请求封装（`src/api/client.ts`）
- `apiRequest<T>(path, init)`：自动注入 `Authorization: Bearer <token>` + `X-Workspace-Oid: <oid>` 头；识别 `{code,message,data}` 统一信封，`code!==200` 抛错，`code===401` 触发 `handleUnauthorizedRedirect()`（清 token + sessionStorage 标记 `auth_expired_tip=1` + 整页跳 `/login?redirect=`）。
- `getApiBaseUrl()` 暴露给手写 fetch 的模块复用。

### 3.2 SSE 流式聊天（`src/utils/sse.ts` + `src/api/adapter/chatAdapter.ts`）
- `streamSSE(opts)`：原生 `fetch` POST + `ReadableStream` reader，按 `\r?\n\r?\n` 切帧，逐行解析 `event:`/`data:`，`data` 优先 `JSON.parse` 失败回退原文；支持 `AbortSignal`（`AbortError` 返回 `{aborted:true}` 不报错）。
- `sendMessageStream(payload, handlers, signal)`（`chatAdapter.ts`）：POST `${base}/chat/chat-stream`，body `{question, datasource_id, conversation_id?, agent_mode, enable_tool_agent, report_audience?}`；`switch(evt.event)` 分发 16 种事件：`step / plan / plan_update / reasoning / sql / result / agent_thought / tool_call / tool_result / final_answer / agent_speak / chart / report / summary / usage / error / done`，每个事件有独立 handler 回调类型。
- 会话 CRUD：`createConversation` / `listConversations` / `getConversationDetail`（`ConversationDetail.records[]` 含 `tool_calls`、`reports`、`plans`、`plan_states`、`exec_result` 等）；报告审核 `updateReportReview`（PATCH `records/{rid}/reports/{idx}`）与 `replaceRecordReports`（PUT 整体覆盖）。
- 流式消费在 `src/hooks/useChat.ts::send()`：所有事件汇总成 `messages/executionSteps/reports/queryResults/summaryByRunId/metricsByRunId` 六路状态；`onDone` 后拉取 `getConversationDetail` 用服务端落库数据校准 token/耗时并 `replaceRecordReports` 回写报告（含编辑后的建议区 HTML）。

### 3.3 鉴权（`src/auth/session.ts`）
- token 存 **localStorage** key `sqlbot_access_token`（明文，无过期校验）；登录后 `setAccessToken`；路由守卫在 `_app.tsx`：无 token 且非公开页（`/login`、`/share/*`、`/mobile/*`）→ 跳登录；有 token 访问 `/login` → 跳首页；`getCurrentUser()` 失败即登出。
- 工作空间：localStorage key `frontend_react_workspace_oid` + `X-Workspace-Oid` 请求头 + 全局 `CustomEvent("workspace:changed")`（`_app.tsx` 派发，`side-bar.tsx`、`data-rules.tsx` 监听重载数据）。
- 登录接口例外：`login()` 用 `application/x-www-form-urlencoded` 手写 fetch（OAuth2 密码模式），不走 `apiRequest`。

### 3.4 报告展示（HTML 渲染 + 图表挂载）
- 报告 HTML 全部由后端 Jinja2 模板生成，前端**三处 iframe 展示**，统一 `sandbox="allow-scripts allow-same-origin"` + `referrerPolicy="no-referrer"` + `srcDoc`：
  1. `ChatExecutionPanel.tsx` 摘要区（高 360px）；
  2. 展开大图 Modal（`/construct/analysis` 与 ChatExecutionPanel 各一个，高 72~75vh）；
  3. 报告历史预览 Modal。
- 图表挂在 iframe 内部（报告自包含 JS），前端不干预；导出前需等 iframe 渲染完成（`exportReportAsWord` 用 double `requestAnimationFrame` 等一帧）。

### 3.5 PDF / docx 导出链路（**纯前端**，未调后端导出接口）
- **PDF**：`exportReportPdf`（`analysis/index.tsx:458` 与 `ChatExecutionPanel.tsx:381` 各一份几乎相同的实现）→ 动态 `import("html2canvas")` 截图 iframe body（`scale:2, useCORS:true`）→ `jspdf` A4 纵向，按 `pageH` 逐页平移切片 `addImage` 分页。
- **Word**：`src/utils/exportReportWord.ts::exportReportAsWord` → html2canvas 截全图 → `sliceCanvasToDataUrls` 按 ~A4 高度切片 → 拼 **MHTML（`application/msword`，Office XML header + `<img>` 列表）** 下载 `.doc`。**注意：导出的是整页截图图片，不是可编辑文本**；注释明确「Word 对 CSS Grid/Flex/渐变支持很差，DOM 直转无法与预览一致」。
- 后端 docx/xlsx（xlsxwriter/docx）导出能力存在（见 CLAUDE.md），但前端未调用任何后端导出接口；HTML 下载是 Blob 直下。

---

## 4. 教育模块前端（edu 分支特性）

### 4.1 页面与组件矩阵

| 分支特性 | 实现文件 | 关键函数/组件 |
|---|---|---|
| 9 类报告生成 | `pages/construct/analysis/index.tsx` | `OPEN_REPORT_TYPES`（白名单）、`fieldsForType()`（每类报告的表单字段矩阵）、`selectSkill()`、`onGenerate()`、`serializeExamFilter()`（多考试 `;;` 序列化，与后端 `_split_exam_filter` 对齐） |
| **受众切换** | 同上 | `AUDIENCE_OPTIONS`（default/principal/grade_head/head_teacher/subject_teacher/parent）+ Form `audience` 字段 → `generateReport({audience})`；聊天侧 `useChat` 的 `reportAudience` → 流式 payload `report_audience` |
| 批量多班级 | 同上 | `onBatchGenerate()` → `educationApi.batchReport({class_names, ...})`，结果表格展示每班 `html_length`/`error` |
| **报告历史** | `pages/construct/analysis/history.tsx` | `listReportHistory(100)`、`getReportHistoryDetail(record_id)`、`deleteReportHistory(conversation_id)`、iframe 预览 + HTML 下载 |
| **多报告分组** | `ChatExecutionPanel.tsx` + `useChat.ts` | 按 `runId` 的 `scopedReports`；`reportOptionLabels` 生成「子任务 N · 标题（最新）」下拉；`useChat::deriveReportsFromRecord()` 合并 `record.reports` 与 `tool_calls[].data.chunks` 双来源；`onDone` 里按 `title::subTaskIndex` 去重合并并 `replaceRecordReports` 回写 |
| 报告建议编辑/审核 | `ChatExecutionPanel.tsx` + `src/utils/reportRecommendations.ts` | `extractRecommendationsText/hasRecommendationsSection/replaceRecommendationsHtml`（`data-edu-section="recommendations"` 标记或 h2/h3 标题正则定位，与后端 `report_edit.py` 对齐）；审核通过后锁定编辑并开放导出 |
| 成绩导入 | `pages/construct/education/score-import.tsx` + `educationApi.postScoreImport` | 两步 wizard：`preview` 校验（error_rows/valid_rows/preview_sample）→ `execute` 导入（summary/anomaly_alerts）；下载模板 `downloadTemplate` |
| 异常提醒 | `pages/construct/education/anomaly-alerts.tsx` | `accessible` 权限位拦截；`payload.critical/regression/imbalanced` 三类学生表格；确认处理 `confirmAnomalyAlert(id, note)` |
| 异常规则配置 | `pages/construct/education/anomaly-rules.tsx` | `getReportConfig/updateReportConfig/resetReportConfig`；百分比↔ratio 换算（`/100`） |
| 预测分数线 | `pages/construct/education/fraction-bar.tsx` | `listFractionBar/upsertFractionBar/recomputeScoreIndicator`；**按数据源名称 "exam" 查找**（`EXAM_DS_NAME`） |
| 达线看板 | `pages/construct/education/line-reach.tsx` | `getLineReach`（all/physics/history 三视图）、KPI 卡 + G2Chart + 区县/学校嵌套表；同样依赖 "exam" 数据源 |
| 教育权限 | `pages/construct/permission/edu-permission.tsx` | `permissionApi.listEduRoles/getUserEduScope/updateUserEduScope/batchBindEduScope/previewEduEffective`；CSV 批量绑定（前端读文件为文本再 POST） |
| 数据权限 | `pages/construct/permission/data-rules.tsx` | 规则组 CRUD；行权限 `expression_tree`（relation/conditions JSON）可视化编辑器 + 列权限拒绝列表（`enable:false` 归一化） |
| 技能市场 | `pages/construct/skills/index.tsx` + `public/education_skills.json` | 9 个技能（9 类 report_type）；`?report_type=` 深链到分析工具；`prefill_prompt` 深链到首页 |

### 4.2 教育接口层（`src/api/education.ts`，757 行）
- 全部手写 fetch + 手动 `authHeaders()`（token + X-Workspace-Oid），**不走 `apiRequest`**；每个方法重复 401 检查；返回风格不统一（部分 `{ok,message,data}`，部分直接 throw）。
- 端点清单：`/education/score-import/templates/{type}`、`/education/score-import/{preview|execute}`、`/education/generate-report`、`/education/meta/options`、`/education/batch-report`、`/education/save-report-history`、`/education/report-history`(+`/{id}` GET/DELETE)、`/education/report-config`(GET/PUT/`/reset`)、`/education/anomaly-alerts`(+`/{id}`、`/{id}/confirm`)、`/education/dashboards/line-reach`(+`/meta`)、`/education/fraction-bar`(GET/PUT)、`/education/score-indicator/recompute`。
- 类型：`ReportConfig`（含 pass_percent/excellent_percent 与 ratio 双字段）、`AnomalyRuleItem`、`LineReach*`、`FractionBar*`、`ScoreImport*` 等均在文件底部导出。

---

## 5. 关键状态与数据流

### 5.1 状态管理：**仅 React 原生**（Context + useState + useRef）
- 无 Redux/zustand/swr/react-query。
- **`ChatContext`**（`src/app/chat-context.tsx`）：全局主题 mode（light/dark，localStorage `frontend_react_mode`）、侧栏展开态 `isMenuExpand`、`currentDialogInfo`（未使用）。
- **`ChatContentContext`**（`src/new-components/chat/context.ts`）：由 `pages/chat/index.tsx` 的 `pageContext` 注入，桥接输入区（ChatInputPanel/ToolsBar）与 `useChat`；temperature/model 等参数仅前端摆设（§6 债 8）。
- **`useChat()`**（`src/hooks/useChat.ts`）：**聊天全局状态机**，单页实例化（非全局单例），暴露 17 个状态/方法。核心状态：`messages`、`executionSteps`、`reports`（含 `runId/recordId/reportIndex/reviewStatus`）、`queryResults`、`summary/summaryByRunId`、`runMetrics/metricsByRunId`、`conversationId`、`datasourceId`、`agentMode`、`reportAudience`。内部用 `activeRunIdRef/sendingRef/abortRef/progressRef` 多个 ref 防竞态（多轮 SSE 交替、stop 后旧流误清新状态）。

### 5.2 登录态与权限在 UI 的体现
- **登录态** = localStorage token 存在性 + `_app.tsx` 首次 `getCurrentUser()` 探活；过期统一走 401 重定向（`apiRequest` 或手写 fetch 的 401 分支）。
- **工作空间**：顶栏 Select（`_app.tsx LayoutWrapper`）→ localStorage + `workspace:changed` 事件 + 主内容区 `key={mainContentKey}` 强制 remount 刷新全页数据；所有请求头带 `X-Workspace-Oid`。
- **平台管理员**：`side-bar.tsx` 用 `account==="admin" && id===1` 判定（绕过 `menuVisibility` 过滤）；`users.tsx` 同样硬编码。
- **教育角色**：`CurrentUser.edu_scope.edu_role`（`src/api/auth.ts:24`）驱动侧栏显隐（bureau_admin/student 隐藏异常提醒、student 隐藏达线看板/分数线）与异常提醒页 `accessible` 拦截。
- **菜单权限**：`permissionApi.getMenuVisibility()` 后端下发 `{menuKey: boolean}`，非平台管理员按此过滤侧栏。
- **行/列级数据权限**：前端只负责配置（data-rules/edu-permission），执行在服务端 SQL 注入（`previewEduEffective` 可预览合并后的 SQL）。

### 5.3 关键数据流时序
```
提问 → useChat.send()
  → ensureConversation(建会话, 返回 conversation_id)
  → sendMessageStream(SSE)
      onPlan/onPlanUpdate → executionSteps(计划步骤+进度)
      onReasoning/onAgentThought → steps「Agent 思考」
      onToolCall/onToolResult → steps「调用工具/工具结果」+ queryResults(execute_sql) + reports(HTML)
      onReport/onChart → reports + steps「生成报告」
      onSummary/onFinalAnswer → 左侧助手气泡
      onUsage → runMetrics(token/耗时/进度)
      onDone → getConversationDetail 校准指标 + replaceRecordReports 回写报告
切换会话 → ?conversation_id → loadConversation() 全量重建六路状态（runId 统一为 `record-<id>`）
```

---

## 6. 技术债务与潜在坑点（按优先级）

1. **`.env.local` 被 git 跟踪**（`git ls-files` 含 `frontend-react/.env.local`，根 `.gitignore` 只忽略 `.env` 未忽略它）。其中 `BACKEND_URL` 指向本机 localhost:8000，`NEXT_PUBLIC_DEFAULT_DATASOURCE_ID=12` 是本机库 ID——**换环境必踩坑**，且是配置泄露面。建议加入 gitignore 并只用 `.env.example`。
2. **生产构建环境变量丢失**：`.dockerignore` 排除 `frontend-react/.env.local`，而 `next.config.js` rewrites 依赖 `BACKEND_URL`（构建期读取）→ 生产镜像里 rewrites 不生效，`NEXT_PUBLIC_API_BASE_URL` 必须由 CI 注入，否则前端请求打到自己 origin 的 `/api/v1` 404。
3. **SSE 事件契约硬编码**：`chatAdapter.ts` 的 16 种事件名与 `useChat.ts` 的消费逻辑强耦合后端 `chat-stream` 协议；后端加事件（如教育报告新类型）需前端同步加 case，否则静默丢弃。
4. **`useChat` 竞态复杂度高**：`activeRunIdRef`/`sendingRef`/`stop()` 交叉逻辑脆弱；`stop()` 主动中止后 `streamAborted` 分支直接 return，但 `sendingRef` 在旧 run 的 finally 中会被提前置 false（`activeRunIdRef !== runId` 时 return 前的顺序要小心）。改这里务必回归「连续提问 + 中途 stop + 立即再问」。
5. **多报告回写竞态**：`onDone` 内 `replaceRecordReports`（PUT 整体覆盖）与用户「编辑建议→保存」的 `updateReportReview`（PATCH）并发时可能互相覆盖；且 `deriveReportsFromRecord` 用 `title::subTaskIndex` 去重，同名报告会互相顶掉。
6. **导出代码三处重复**：PDF 导出在 `analysis/index.tsx` 与 `ChatExecutionPanel.tsx` 各一份几乎相同实现（html2canvas 分页逻辑），Word 导出被两处调用——改动需三处同步；且 `(jspdfMod as any).jsPDF` 类型绕过。建议抽 `src/utils/exportReport.ts`。
7. **日期显示时区不一致**：`pages/construct/database/index.tsx::formatDate` 用 `toISOString()`（UTC，比本地少 8 小时）；`anomaly-alerts.tsx` 用字符串截断 `v.replace("T"," ").slice(0,19)`；`history.tsx` 直接原样输出——同一数据三种展示，历史记录里时间可能差一天。
8. **输入区参数是摆设**：`temperatureValue/maxNewTokensValue/resourceValue/modelValue` 在 `pageContext` 里可调，但 `useChat.send()` 的流式 payload 只发 `question/datasource_id/conversation_id/agent_mode/enable_tool_agent/report_audience`，**temperature/model 等从未到后端**；`ModelSwitcher/Temperature/MaxNewTokens/Resource` 控件（new-components/chat/input）全部未挂载到桌面输入区（只有 ToolsBar 在用），是遗留 DB-GPT 风格代码。
9. **硬编码**：
   - `fraction-bar.tsx` / `line-reach.tsx`：按数据源名称 `"exam"` 精确匹配（`d.name.toLowerCase() === EXAM_DS_NAME`），改名即失效；
   - `edu-permission.tsx::previewEffective`：硬编码 `datasource_id: 1` + 示例 SQL；
   - 平台管理员判定 `admin && id===1` 出现在 `side-bar.tsx` 与 `users.tsx`；
   - `users.tsx` 新增用户 Modal 的 `oid` 下拉硬编码「默认工作空间(1)」；
   - `assistant.ts` 里 `ASSISTANT_SUGGESTIONS` 写死三条示例；`ChatDefault.tsx` 是「启明AI」旧文案残留。
10. **报告历史 API 语义混乱**：列表 `rowKey=record_id`，但删除用 `conversation_id`（`deleteReportHistory`），详情用 `record_id`——同一条记录在三个接口里主键口径不一，删除后列表可能残留。
11. **iframe 安全**：报告 HTML 以 `sandbox="allow-scripts allow-same-origin"` 渲染，依赖后端模板完全受控；若后端模板掺入外部脚本，等于同源执行。报告历史把 HTML 全文存库再回显，存在 XSS 放大面，建议后端对模板输出做 CSP/消毒审计。
12. **`X-Workspace-Oid` 与 localStorage key `frontend_react_workspace_oid` 在 6 处重复定义**（`_app.tsx`、`client.ts`、`chatAdapter.ts`、`education.ts`、`edu-permission.tsx`、`data-rules.tsx`），改 key 需全改，建议抽常量模块。
13. **教育 API 层风格分裂**：`education.ts` 手写 fetch 返回 `{ok,message,data}` 或 throw 不统一（`listMetaOptions/getReportConfig` throw，`generateReport/batchReport` 返回 ok:false），调用方错误处理写法五花八门；且每方法重复 401 检查，建议统一收敛到 `apiRequest` + 统一错误类型。
14. **dead code / 占位**：`MainShell`、`ChatSider`、`OpenCodeChatCompletion`、`ChatDefault`、`new-components/chat/input/{ModelSwitcher,Temperature,MaxNewTokens,Resource}` 无引用；`conversations/share/mobile/construct/app|dbgpts|flow|knowledge|models|prompt` 为 stub；`_app.tsx` 里 `ChatContext.isContract/currentDialogInfo` 未使用。
15. **antd API 新旧混用**：`destroyOnHidden`（antd 5.25+ 新名）与 `destroyOnClose`（旧名，仍有告警）并存于各 Modal。
16. **G2Chart 性能**：每次 columns/rows 引用变化即 `new Chart()` 全量重建（`useEffect` 依赖 `inferred` memo），大数据量查询结果切 Tab 会卡；`isNumericColumn` 对 `%` 字符串的处理只剥一个 `%`。
17. **无 ESLint 配置文件**：仓库内无 `.eslintrc*`/`eslint.config.*`，`npm run lint`（`next lint`）在 Next 14 下会因找不到配置报错或交互式询问；`package.json` 里也没有 eslint 依赖。
18. **无任何测试**：frontend-react 内无 test/spec 文件、无 jest/vitest 配置；`QA_CHECKLIST.md` 只是人工 checklist。教育报告逻辑（`serializeExamFilter`、`formatReportDisplayTitle`、`reportRecommendations` 的 HTML 正则）都没有单测保护，改动极易回归。

---

## 7. 测试与脚本

| 脚本 | 命令 | 现状 |
|---|---|---|
| dev | `npm run dev`（`WATCHPACK_POLLING=true next dev -p 3001`） | 可用；轮询模式为 WSL/共享目录设计 |
| devW | `npm run devW` | Windows 变体 |
| build | `next build` | 依赖构建期 env（见 §6 债 2） |
| start | `next start -p 3001` | 生产服务 |
| lint | `next lint` | ⚠️ 无 ESLint 配置文件，大概率不可直接跑 |
| typecheck | `tsc --noEmit` | 可用（strict）；`tsconfig.tsbuildinfo` 增量缓存 |
| 测试 | 无 | ❌ 无测试框架/用例 |

---

## 附：给主力开发的最重要三条

1. **改动聊天流先读 `useChat.send()` 的竞态护栏**（`activeRunIdRef`/`sendingRef`/`stop()`），并在 `onDone` 的回写链路里确认 `replaceRecordReports` 不会被编辑/审核并发覆盖。
2. **教育报告相关改动先跑三处同步**：`analysis/index.tsx`（表单+导出）、`ChatExecutionPanel.tsx`（聊天内报告预览/导出/审核）、`reportRecommendations.ts`（HTML 建议区正则）——它们各自实现了部分同一逻辑。
3. **新增后端接口尽量复用 `apiRequest`** 而非继续在 `education.ts` 里手写 fetch，并把 `X-Workspace-Oid`/localStorage key 抽成共享常量，消除 6 处重复定义。
