# 前端复刻 DB-GPT 开发路线计划

> awesome-project 前端：React + TypeScript + Ant Design 5 + Tailwind CSS
> 复刻对象：DB-GPT Web (https://github.com/eosphoros-ai/DB-GPT)

---

## 一、DB-GPT Web 架构分析

### 1.1 技术栈

| 层面 | 技术 |
|------|------|
| 框架 | Next.js 13.4 (pages router) |
| UI 库 | React 18 + Ant Design 5 |
| CSS | Tailwind CSS 3.3 + CSS Modules |
| 状态管理 | React Context (ChatContext) |
| 数据请求 | ahooks useRequest + axios |
| 国际化 | react-i18next |
| 图表 | @antv/g2, @antv/gpt-vis |
| 编辑器 | Monaco Editor |
| 流式输出 | @microsoft/fetch-event-source (SSE) |

### 1.2 页面路由结构

```
/                           → 主页 Chat UI（Explore，ManusPanel 风格）
/chat                       → 特定 App 的 Chat 页
/construct                  → 管理设置 Hub
  /construct/app            → 应用管理
  /construct/flow           → AWEL 工作流
  /construct/models         → 模型管理
  /construct/database       → 数据源管理
  /construct/knowledge      → 知识库管理
  /construct/prompt         → Prompt 管理
  /construct/skills         → 技能管理
  /construct/dbgpts         → 社区市场
/conversations              → 全部对话列表
/share/[token]              → 分享页
```

### 1.3 布局架构

```
_app.tsx
  └─ ChatContextProvider          ← 全局状态上下文
     └─ CssWrapper               ← 主题 / 语言注入
        └─ LayoutWrapper         ← 整体布局
           ├─ SideBar            ← 左侧导航（可折叠 240px / 80px）
           ├─ MainContent        ← flex-1 主内容区
           └─ FloatHelper        ← 浮动帮助按钮
```

**SideBar 两种形态：**
- **收起** (`isMenuExpand=false`)：80px 宽，仅图标 + Tooltip 提示
- **展开** (`isMenuExpand=true`)：240px 宽，图标 + 文字 + 对话历史列表

### 1.4 组件层级

```
components/           ← 旧版组件（chat、flow、knowledge 等）
new-components/       ← 新版组件系统（核心参考）
  ├─ chat/
  │  ├─ content/      ← 消息渲染（ManusLeftPanel, ManusRightPanel, OpenCodeSessionTurn）
  │  ├─ header/       ← 对话顶部栏
  │  ├─ input/        ← 输入面板
  │  ├─ sider/        ← 对话右侧面板
  │  └─ tools/        ← 工具渲染
  ├─ layout/
  │  ├─ Construct.tsx   ← /construct 的 Tab 布局
  │  ├─ FloatHelper.tsx ← 浮动帮助按钮
  │  ├─ UserBar.tsx     ← 用户信息组件
  │  ├─ Header.tsx
  │  └─ Sider.tsx
  ├─ common/          ← 通用组件
  ├─ charts/          ← 图表组件
  └─ analysis/        ← 数据分析组件
```

### 1.5 核心状态（ChatContext）

```typescript
interface IChatContext {
  mode: 'dark' | 'light';         // 主题模式
  isMenuExpand: boolean;          // 侧栏展开状态
  scene: string;                  // 对话场景
  chatId: string;                 // 当前对话 ID
  model: string;                  // 当前模型
  modelList: string[];            // 可用模型列表
  history: ChatHistoryResponse;   // 对话历史
  agent: string;                  // 当前 agent
  dbParam?: string;               // 数据库参数
  adminList: UserInfoResponse[];  // 管理员列表
  currentDialogInfo: {            // 当前对话信息
    chat_scene: string;
    app_code: string;
  };
}
```

---

## 二、awesome-project 前端目标架构

### 2.1 技术选型

| 层面 | 选型 | 说明 |
|------|------|------|
| 构建工具 | Vite 5 | 当前项目已使用，纯 SPA 无需 Next.js SSR |
| 框架 | React 18 + TypeScript | 与 DB-GPT 一致 |
| 路由 | React Router v6 | SPA 路由，对标 Next.js pages |
| UI 库 | Ant Design 5 | 与 DB-GPT 一致 |
| CSS | Tailwind CSS 3 | 与 DB-GPT 一致 |
| 状态管理 | React Context | 与 DB-GPT 一致 |
| 数据请求 | ahooks useRequest + axios | 与 DB-GPT 一致 |
| 国际化 | react-i18next + i18next | 与 DB-GPT 一致（先中文） |
| 流式输出 | @microsoft/fetch-event-source | SSE 支持 |

### 2.2 路由映射

```
/                       → 主页 Chat UI（核心交互）
/chat/[id]              → 特定对话页
/construct              → 管理页
  /construct/database   → 数据源管理（core_datasource）
  /construct/knowledge  → 知识库/Embedding 管理
  /construct/models     → LLM 模型配置
  /construct/prompt     → Prompt 模板管理
  /construct/skills     → 技能管理
/conversations          → 全部对话列表（分页）
/login                  → 登录页
```

### 2.3 目录结构

```
fronted-react/
├── index.html
├── package.json
├── vite.config.ts
├── tailwind.config.js
├── postcss.config.js
├── tsconfig.json
├── tsconfig.node.json
├── public/
│   └── pictures/          ← 侧栏图标资源
└── src/
    ├── main.tsx
    ├── App.tsx            ← 路由 + Context Provider
    ├── app/
    │   ├── chat-context.tsx   ← 全局 ChatContext State
    │   └── i18n.ts            ← 国际化配置
    ├── pages/
    │   ├── index.tsx           ← 主页 Chat UI
    │   ├── chat/
    │   │   └── index.tsx       ← App Chat 页
    │   ├── construct/
    │   │   ├── index.tsx       ← Construct 首页
    │   │   ├── database.tsx    ← 数据源管理
    │   │   ├── knowledge/      ← 知识库管理
    │   │   ├── models/         ← 模型管理
    │   │   ├── prompt/         ← Prompt 管理
    │   │   └── skills/         ← 技能管理
    │   ├── conversations/
    │   │   └── index.tsx       ← 对话列表
    │   └── login/
    │       └── index.tsx       ← 登录页
    ├── components/
    │   ├── layout/
    │   │   ├── side-bar.tsx         ← 主侧栏（核心组件）
    │   │   ├── construct-layout.tsx ← /construct Tab 布局
    │   │   └── top-progress-bar.tsx
    │   ├── chat/
    │   │   ├── chat-container.tsx   ← Chat 主容器
    │   │   ├── chat-content/        ← 消息渲染
    │   │   ├── chat-header/         ← 对话头部
    │   │   ├── chat-input/          ← 输入面板
    │   │   └── chat-sider/          ← 对话侧面板
    │   ├── common/
    │   │   ├── float-helper.tsx     ← 浮动按钮
    │   │   ├── user-bar.tsx         ← 用户信息
    │   │   ├── model-selector.tsx   ← 模型选择器
    │   │   └── prompt-bot.tsx       ← Prompt 推荐
    │   └── icons/                   ← SVG 图标组件
    ├── api/
    │   ├── client.ts               ← axios 实例 + 拦截器
    │   ├── chat.ts                  ← 对话 API
    │   ├── datasource.ts            ← 数据源 API
    │   ├── auth.ts                  ← 认证 API
    │   └── types.ts                ← API 类型定义
    ├── hooks/
    │   ├── use-chat.ts             ← 核心聊天 Hook
    │   └── use-user.ts
    ├── types/
    │   ├── chat.ts
    │   ├── db.ts
    │   └── common.ts
    ├── utils/
    │   └── constants.ts
    └── styles/
        └── globals.css
```

---

## 三、分步实施计划

### Phase 1：项目脚手架搭建

**目标**：Vite + React + TS + Ant Design 5 + Tailwind 骨架跑通

**任务清单：**
1. 初始化 Vite + React + TypeScript 项目（`fronted-react/` 目录）
2. 安装核心依赖：
   - `antd` `@ant-design/icons` `@ant-design/cssinjs`
   - `react-router-dom`
   - `ahooks` `axios`
   - `react-i18next` `i18next`
   - `@microsoft/fetch-event-source`
   - `classnames` `dayjs`
3. 安装 Tailwind：`tailwindcss` `postcss` `autoprefixer`
4. 配置 `tailwind.config.js`（复用 DB-GPT 配置）
5. 配置 `postcss.config.js`
6. 配置全局 CSS（复用 DB-GPT 的 `styles/globals.css`）
7. 配置 `tsconfig.json` 路径别名 `@/` → `src/`
8. 创建 `main.tsx` + `App.tsx` 入口

**验证标准**：`npm run dev` 启动成功，Tailwind 类生效，Antd 组件正常渲染


### Phase 2：全局布局 + 侧栏

**目标**：实现与 DB-GPT 一致的全局布局骨架

**任务清单：**
1. **ChatContext 实现**
   - 创建 `src/app/chat-context.tsx`
   - 状态：mode（主题）、isMenuExpand（侧栏）、model/modelList、chatId、scene、history
   - 从 localStorage 恢复主题偏好

2. **SideBar 组件**（最核心）
   - 创建 `src/components/layout/side-bar.tsx`
   - **收起模式（80px）**：LOGO_SMALL + 导航图标 + Tooltip + Settings Popover
   - **展开模式（240px）**：LOGO + "新任务"按钮 + 导航文字 + 对话历史列表 + 用户栏
   - 导航项：Explore、Skills、Datasources、Knowledge
   - Settings Popover：应用管理、模型管理、AWEL工作流、Prompt、DB-GPTs社区、模型评估
   - 对话历史列表：相对时间、hover 删除按钮
   - 底部：用户头像、主题切换（dark/light）、语言切换、折叠按钮
   - 动画过渡：`animate-fade animate-duration-300`

3. **FloatHelper 组件**
   - 浮动帮助按钮组（右下角 Fixed）
   - 文档链接等

4. **UserBar 组件**
   - 用户头像 + 昵称展示
   - 从 localStorage 读取用户信息

5. **App.tsx 布局组装**
   - `ChatContextProvider` → `ConfigProvider`(Antd 主题) → `BrowserRouter`
   - 页面布局：`SideBar + main(flex-1) + FloatHelper`
   - 特殊页面判断（登录页、移动端不显示侧栏）
   - Antd ConfigProvider 主题色：`colorPrimary: '#0C75FC'`, 暗色/亮色算法

6. **顶部进度条**（可选）
   - 路由切换 NProgress 效果

7. **国际化基础配置**
   - `src/app/i18n.ts`
   - 中/英文切换，localStorage 持久化

**验证标准**：侧栏展开/收起动画流畅，导航跳转正确，主题切换 dark/light 生效


### Phase 3：主页 Chat UI（核心交互）

**目标**：实现完整对话界面——这是用户使用最频繁的页面

**任务清单：**
1. **Chat 输入面板**
   - TextArea 自适应高度输入框
   - 发送按钮
   - 快捷操作按钮（文件上传、知识库关联、数据源选择、技能选择）
   - 模型选择器（下拉菜单，显示当前模型）
   - 参考：`new-components/chat/input/ChatInputPanel.tsx`

2. **消息渲染组件**
   - 用户消息气泡（右侧对齐，头像 + 内容）
   - AI 回复渲染：
     - Markdown 渲染（react-markdown + remark-gfm + rehype-raw）
     - 代码块语法高亮（react-syntax-highlighter）
     - SQL 代码块特殊渲染（带"执行"按钮）
     - 表格数据渲染（Ant Design Table）
     - 思考过程折叠面板（Thinking/Reasoning）
     - 流式打字效果（SSE 实时追加字符）
   - 参考：`components/chat/chat-content/` 系列

3. **Chat 容器**
   - 消息列表滚动区域（overflow-y-auto）
   - 自动滚到底部逻辑：
     - 新消息 → force scroll（`behavior: 'smooth'`）
     - 流式更新 → 仅当用户在底部附近才自动滚
   - 滚动到顶部/底部按钮（右下角浮动）
   - 停止生成按钮
   - 参考：`new-components/chat/ChatContentContainer.tsx`

4. **useChat Hook**
   - 发送消息（POST，构建请求体：content + model + db_param 等）
   - SSE 流式接收（`@microsoft/fetch-event-source`）
   - 解析 SSE 数据流，更新 history
   - 中断生成（AbortController）
   - 对话 ID 管理（URL query ?id=xxx）
   - 对接 awesome-project 后端 `/api/chat` 接口
   - 参考：`hooks/use-chat.ts`

5. **ChatHeder 组件**（对话顶部信息栏）
   - App 名称 + 描述 + 标签
   - 收藏按钮（Star）
   - 分享按钮（复制链接）
   - 推荐问题 Tags（底部横排）
   - 吸顶模式（sticky，滚动后切换样式）
   - 参考：`new-components/chat/header/ChatHeader.tsx`

6. **ChatSider 组件**（对话右侧面板，可选）
   - 对话参数配置（Temperature、MaxTokens 等）
   - 数据源选择
   - 知识库选择

**验证标准**：发送消息 → SSE 流式渲染 → Markdown/SQL/Table 正确展示 → 中断生成 → 重新发送


### Phase 4：Construct 管理页面

**目标**：实现管理后台各子页面

**任务清单：**
1. **ConstructLayout 通用布局**
   - 顶部 Ant Design Tabs 导航
   - Tab 项：应用管理、工作流、模型管理、数据源、知识库、Prompt、技能、社区
   - 背景渐变（bg-gradient-light / bg-gradient-dark）
   - Tab 切换路由跳转
   - 参考：`new-components/layout/Construct.tsx`

2. **数据源管理页 (`/construct/database`)**
   - 数据源列表（Ant Table + 分页 + 搜索）
   - 添加/编辑 Modal 表单（类型选择、连接配置、描述）
   - 连接测试按钮 + 状态反馈（Loading/Success/Error）
   - 删除 Popconfirm 确认
   - 对接后端 API：`/api/datasources`
   - 参考：`pages/construct/database.tsx`

3. **模型管理页 (`/construct/models`)**
   - LLM 模型配置列表
   - 新增/编辑模型（API 地址、模型名、API Key 等）
   - 参考：`pages/construct/models/index.tsx`

4. **知识库管理 (`/construct/knowledge`)**
   - 知识空间列表 + CRUD
   - Chunk 管理子页
   - Embedding 状态展示

5. **Prompt 管理 (`/construct/prompt`)**
   - Prompt 模板列表
   - Markdown 编辑器编辑模板

6. **技能管理 (`/construct/skills`)**
   - 技能卡片列表

**验证标准**：数据源 CRUD 全流程、连接测试成功/失败展示、Table 分页/搜索正常工作


### Phase 5：对话历史页

**目标**：全部对话的分页列表展示

**任务清单：**
1. 对话列表（分页，每页 20 条）
2. 搜索过滤（debounce 300ms）
3. 相对时间显示（"3分钟前"、"2小时前"、"3天前"）
4. hover 显示删除按钮 + Popconfirm 确认
5. 点击跳转到对应对话 (`/?id=xxx`)
6. 空状态展示（Empty 组件）
7. 参考：`pages/conversations/index.tsx`

**验证标准**：分页切换、搜索过滤、删除确认、点击跳转均正常


### Phase 6：登录/认证页

**目标**：对接 awesome-project 的 JWT 认证体系

**任务清单：**
1. 登录表单页面（账号 + 密码）
2. JWT Token 存储到 localStorage
3. axios 请求拦截器：自动附加 `Authorization: Bearer xxx` Header
4. axios 响应拦截器：401 自动跳转登录页
5. 用户信息缓存
6. 注册页（可选，按需）

**验证标准**：登录 → Token 存储 → 后续请求自动附加 Token → 过期或 401 跳回登录页


### Phase 7：打磨优化

**目标**：细节完善和交互体验优化

**任务清单：**
1. 暗色/亮色主题切换无闪烁
2. 移动端响应式适配（简单处理）
3. 加载态（Skeleton/Spin）、空状态（Empty）、错误态完善
4. Antd 组件主题微调（对齐 DB-GPT 风格）
5. 对话恢复（URL ?id=xxx → 加载历史消息）
6. 滚动条自定义样式
7. 动画过渡完善（framer-motion 或 CSS transition）

---

## 四、关键文件映射

| DB-GPT 源文件 | awesome-project 目标文件 | 复用程度 |
|--------------|-------------------------|---------|
| `pages/_app.tsx` | `src/App.tsx` | 重构适配 |
| `app/chat-context.tsx` | `src/app/chat-context.tsx` | ~90% 复用 |
| `components/layout/side-bar.tsx` | `src/components/layout/side-bar.tsx` | ~85% 复用 |
| `new-components/layout/FloatHelper.tsx` | `src/components/common/float-helper.tsx` | ~95% 复用 |
| `new-components/layout/UserBar.tsx` | `src/components/common/user-bar.tsx` | ~90% 复用 |
| `new-components/chat/ChatContentContainer.tsx` | `src/components/chat/chat-container.tsx` | ~80% 复用 |
| `new-components/layout/Construct.tsx` | `src/components/layout/construct-layout.tsx` | ~85% 复用 |
| `pages/conversations/index.tsx` | `src/pages/conversations/index.tsx` | ~80% 复用 |
| `tailwind.config.js` | `tailwind.config.js` | ~95% 复用 |
| `styles/globals.css` | `src/styles/globals.css` | ~90% 复用 |
| `hooks/use-chat.ts` | `src/hooks/use-chat.ts` | ~60% 改写 |

---

## 五、注意事项

1. **SideBar 图标资源**：需从 DB-GPT `public/pictures/` 迁移 PNG/SVG 图标到本项目
2. **API 接口适配**：awesome-project 后端接口可能与 DB-GPT 不同，需调整 API 调用层和类型定义
3. **先跑通核心链路**：Phase 1-3 优先完成，保证 Chat 主流程可用后再做管理页面
4. **保持代码简洁**：不做过度抽象，三个相似的组件好过一个过早的泛化