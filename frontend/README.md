# Frontend (Phase 3)

`awesome-project` 的前端实现，覆盖 `MVP_PLAN.md` 中 Phase 3 的核心范围：

- 对话 UI（会话列表、提问输入、结果展示）
- 数据源配置界面（列表、增删改、连接测试）
- 查询结果表格与前端分页

> 工程结构、技术栈、`utils/request.ts` 的拦截器约定、以及 `views/chat`、`api`、`stores`、`components/layout` 的目录划分均参考 [SQLBot frontend](https://github.com/dataease/SQLBot) 同构落地，仅保留 Phase 3 所需子集。

## 技术栈

- Vue 3 + TypeScript
- Vite 5
- Vue Router 4（`createWebHashHistory`）
- Pinia
- Element Plus（按需 `unplugin-vue-components` + `unplugin-auto-import`）
- vue-i18n 9（`zh-CN`）
- Less 预处理器
- Axios（`HttpService` 类封装拦截器）
- web-storage-cache、dayjs、vite-svg-loader

## 目录结构

```
frontend/src
├── api/                  # 后端 API 封装（chat、datasource）
├── assets/               # 静态资源（svg 等）
├── components/
│   └── layout/           # 应用框架（Menu / MenuItem / Person / index）
├── i18n/                 # vue-i18n 配置与多语言资源
├── router/               # 路由表（hash 路由 + Layout 包裹）
├── stores/               # Pinia store（user 等）
├── utils/                # request、useCache、utils
├── views/
│   ├── chat/             # 数据问答页面（ChatList / ChatRecord / ChatInput）
│   └── datasource/       # 数据源管理页面
├── App.vue               # ElConfigProvider + i18n locale
├── main.ts               # 应用入口
└── style.less            # 全局样式与 CSS 变量
```

## 快速启动

```bash
cd frontend
npm install
npm run dev
```

默认访问地址：`http://localhost:5173`，路由使用 hash 模式，例如 `http://localhost:5173/#/chat`。

## 生产构建

```bash
cd frontend
npm run build
npm run preview
```

构建产物输出到 `frontend/dist/`。

## 环境变量

通过 `VITE_API_BASE_URL` 指定后端 API 地址，未设置时默认使用 `http://localhost:8000/api/v1`。

可在 `frontend/.env.local` 中配置：

```env
VITE_API_BASE_URL=http://localhost:8000/api/v1
```

## 页面说明

- `views/chat/index.vue`
  - 左侧 `ChatList` 会话列表（新建 / 选择 / 删除）
  - 顶部数据源选择（首条消息发送后锁定）
  - 中部 `ChatRecord` 消息流，渲染问题、SQL、结果表格、错误信息
  - 底部 `ChatInput` 输入框（Enter 发送，Shift+Enter 换行）
  - 表格结果支持前端分页（每页 20 行）
- `views/datasource/index.vue`
  - 数据源列表
  - 新增 / 编辑 / 删除
  - 行内 / 表单内连接测试

## API 对应关系

- Chat
  - `GET    /chat/conversations`
  - `POST   /chat/conversations`
  - `GET    /chat/conversations/{id}`
  - `PUT    /chat/conversations/{id}`
  - `DELETE /chat/conversations/{id}`
  - `POST   /chat/execute-sql`
- Datasource
  - `GET    /datasource`
  - `GET    /datasource/{id}`
  - `POST   /datasource`
  - `PUT    /datasource/{id}`
  - `DELETE /datasource/{id}`
  - `POST   /datasource/{id}/test-connection`

## 与 SQLBot frontend 的差异（已显式裁剪的部分）

为了保持 MVP Phase 3 的最小可用范围，下列模块没有引入：

- 登录 / 鉴权 / OAuth / LDAP（`views/login`、`api/login`、`stores/user.login`）
- Dashboard / Canvas / 嵌入式（`views/dashboard`、`views/embedded`、`x-pack`）
- 系统管理（成员、权限、参数、外观、平台、API Key、训练等）
- 多 Workspace / 顶部布局切换 / 折叠主题
- SSE 流式问答、图表回答、推荐问题、TinyMCE 等

后续阶段如需扩展，可在 `router`、`views`、`api`、`stores` 同构追加，无需重构现有结构。
