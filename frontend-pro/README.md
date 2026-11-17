# 学情智诊-AI分析平台前端

当前工程包含 Umi Max 与 Ant Design Pro 工作台、登录与工作空间、原 React 问数布局、学情总览和原 React 前端的业务路由。试卷、成绩、报告和权限等复杂页面沿用已有接口及业务操作。旧 React 工程保留用于并行联调和回切，详见 [开发落地方案](../docs/frontend-pro-refactor-plan.md)。

## 本地开发

使用 Node.js 22。先启动项目后端，然后运行：

```powershell
cd frontend-pro
npm ci
npm run dev
```

打开 [本地前端](http://127.0.0.1:4180/chat)。开发服务器将同源 `/api/v1` 转发到 `http://127.0.0.1:8000`。如已在其他地址运行后端，在启动前设置 `BACKEND_URL` 为后端源地址。需要指定默认数据源时，设置 `DEFAULT_DATASOURCE_ID`。

使用 `npm run typecheck` 执行 TypeScript 检查，使用 `npm run build` 生成 `dist/`。正式环境需要为页面路由配置入口 HTML 回退，并将 `/api/v1` 同源转发到后端。流式接口保留逐帧输出。

## 当前联调入口

- `/login` 对接现有账户体系。
- `/chat` 使用原问数的布局和运行状态。左侧最近任务由 Ant Design X 渲染。回答期间输入框边缘是一条绕圈的彩虹光带（移植 BoardUI Composer Loader：4 色渐变、清晰线 + 紧辉光 + 向内 bloom、两端 taper，每圈 4.5 秒），上方状态用 BoardUI Agent Thinking 的 infinity 彗星 + 流光文字 + 已工作计时；两者都在 `prefers-reduced-motion` 下静止。
- `/chat?conversation_id=…` 恢复历史会话。`/chat?q=…&ds=…` 承接外部提问。联调需使用真实账号和工作空间，验证 SSE、历史会话、停止和追问恢复。
- `/` 为学情总览（原探索广场已下线），以现有达线与异常接口展示真实指标。由于现有接口按学校、考试和分数线返回达线数据，总览以学校排名呈现。`/overview` 保留为重定向到 `/`，旧书签继续可用。`/construct/*` 与 `/system/log/*` 继续兼容原有链接。分享路径保留现有入口，当前项目尚无可消费的分享查询接口。
