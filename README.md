
![Awesome-DB logo](docs/img/logo-horizontal.svg)

[![github](https://img.shields.io/badge/Github%2FAwesome--DB-181717?logo=github)](https://github.com/xhthy00/owesome-project)
[![atomgit](https://img.shields.io/badge/AtomGit%2FAwesome--DB-2B5AED)](https://atomgit.com/xhthy00/awesome-data)
[![build](https://img.shields.io/badge/build-uv-lightgrey)](./pyproject.toml)
[![test](https://img.shields.io/badge/test-pytest-lightgrey)](./tests)
[![license](https://img.shields.io/badge/license-MIT-green)](./LICENSE)
[![python](https://img.shields.io/badge/python-3.11%2B-blue)](./pyproject.toml)

基于 FastAPI + LLM 的智能问数系统。用户可以使用自然语言提问，系统自动生成 SQL、执行查询并返回结构化结果、可视化分析报告，支持普通执行与 SSE 流式对话。

## 系统截图

### 首页

![首页截图](docs/img/home.png)

### Agent 模式

![Agent 模式截图](docs/img/agentic.png)

### 报告页

![报告页截图](docs/img/report.png)

## Why awesome-project

- 自然语言到 SQL 的端到端链路开箱即用
- 兼容多种 LLM 接入方式（OpenAI/Ollama 等）
- 提供 `legacy` / `agent` / `team` 三种对话执行模式
- 后端与前端分层清晰，便于二次开发与插件化扩展

## 核心能力

- 用户认证：注册、登录、`JWT` 鉴权、获取当前用户信息
- 数据源管理：数据源增删改查、连接测试、配置加解密
- 智能问数：自然语言生成 SQL、校验 SQL、格式化 SQL、执行 SQL
- 对话管理：会话 CRUD、历史记录持久化、最近问题检索
- 流式交互：`/api/v1/chat/chat-stream` 支持 `legacy` / `agent` / `team` 模式
- 前端控制台：提供聊天界面与数据源管理界面（`frontend/`）

## 技术栈

- 后端：`FastAPI`、`SQLModel`、`SQLAlchemy`、`Alembic`
- AI/Agent：`LangChain`、`LangGraph`、OpenAI/Ollama 兼容接入
- 数据库：PostgreSQL（默认），并支持多种数据源连接
- 前端：Vue 3 + TypeScript + Vite + Element Plus + Pinia
- 工具链：`uv`、`pytest`、`ruff`

## 架构概览

```text
┌──────────────────────────────────────┐
│              Frontend (Vue)          │
│   Chat UI / Datasource Management    │
└───────────────────┬──────────────────┘
                    │ HTTP / SSE
┌───────────────────▼──────────────────┐
│           FastAPI Backend            │
│  system   datasource   chat   agent  │
└───────────────────┬──────────────────┘
                    │
       ┌────────────┴────────────┐
       │                         │
┌──────▼──────┐          ┌───────▼────────┐
│   LLM API   │          │   Databases    │
│OpenAI/Ollama│          │PostgreSQL etc. │
└─────────────┘          └────────────────┘
```

## 请求链路

```text
User Question
    ↓
Prompt + Schema Construction
    ↓
LLM Generates SQL
    ↓
SQL Validation / Formatting
    ↓
Execute SQL on Datasource
    ↓
Return Result (JSON / SSE Events)
```

## AWEL 架构
```
┌─────────────────────────────────────────────────────────────────────────────────┐
│ POST /api/v1/chat/chat-stream  ← 统一入口（agent_mode: single | team）            │
└─────────────────────────────────────┬───────────────────────────────────────────┘
                                      │
          ┌───────────────────────────┴───────────────────────────┐
          ▼ agent_mode=single                                      ▼ agent_mode=team
   ┌──────────────┐                                       ┌───────────────────────┐
   │ SQLGenerator │ (legacy)                              │    ChatAwelTeam       │
   └──────────────┘                                       │ (AWELBaseManager)     │
                                                          └──────────┬────────────┘
                                                                     │
                        ┌──────────────┬──────────────┬──────────────┼──────────────┐
                        ▼              ▼              ▼              ▼              ▼
                     Planner     DataAnalyst      Charter      Summarizer   [ToolAgent*]
                   (PlanAction) (QuerySqlAction) (ChartAction) (SummaryAct) (ToolAction)
                        │              │              │              │              │
                        └──────────────┴──────────────┴──────────────┴──────────────┘
                                       │                        │
                                       ▼                        ▼
                      ┌────────────────────────────┐  ┌────────────────────┐
                      │  ResourcePack (per-agent)  │  │  ToolPack(registry)│
                      │  ├─ DatasourceResource     │  │  ├─ schema_tool    │
                      │  └─ ToolPack               │──┤  ├─ calc_tool      │
                      └────────────────────────────┘  │  ├─ embedding_tool │
                                                      │  └─ ... (@tool)    │
                                                      └────────────────────┘
                                       │
                                       ▼
                              GptsMemory(plans)  AgentMemory(short-term)
                                       │
                                       ▼
                        gpts_plan / gpts_message / tool_registry(DB)

```

## 项目结构

```text
awesome-project/
├── src/
│   ├── main.py              # FastAPI 入口
│   ├── system/              # 认证与用户模块
│   ├── datasource/          # 数据源管理与 SQL 执行
│   ├── chat/                # 问数接口、会话管理、SSE 流
│   ├── agent/               # ReAct/Team Agent 相关能力
│   └── common/              # 配置、数据库、异常、路由聚合等
├── tests/                   # 后端测试
├── frontend/                # Vue 前端
├── alembic/                 # 数据库迁移目录
├── .env.example             # 环境变量模板
└── pyproject.toml
```

## 环境准备

### 1) 安装依赖

```bash
uv sync
```

### 2) 配置环境变量

```bash
cp .env.example .env
```

常用配置如下（可按需调整）：

```env
APP_NAME=awesome-project
DEBUG=false
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/awesome
JWT_SECRET_KEY=your-secret-key-change-in-production
LLM_BASE_URL=http://localhost:11434/v1
LLM_API_KEY=ollama
LLM_MODEL=qwen2.5
```

## Quick Start (5 分钟)

```bash
# 1) 安装后端依赖
uv sync

# 2) 启动后端
uv run uvicorn src.main:app --reload --host 0.0.0.0 --port 8000

# 3) 新开终端启动前端
cd frontend
npm install
npm run dev
```

打开：

- 前端：`http://localhost:5173`
- 后端文档：`http://localhost:8000/docs`

## 启动服务

### 后端（FastAPI）

```bash
uv run uvicorn src.main:app --reload --host 0.0.0.0 --port 8000
```

- 健康检查：`GET http://localhost:8000/health`
- API 文档：`http://localhost:8000/docs`

### 前端（Vue）

```bash
cd frontend
npm install
npm run dev
```

前端默认地址：`http://localhost:5173`  
如需指定后端地址，可在 `frontend/.env.local` 配置：

```env
VITE_API_BASE_URL=http://localhost:8000/api/v1
```

## API 示例

### 1) 登录获取 token

```bash
curl -X POST "http://localhost:8000/api/v1/system/login" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=demo&password=demo123"
```

### 2) 生成并执行 SQL

```bash
curl -X POST "http://localhost:8000/api/v1/chat/execute-sql" \
  -H "Content-Type: application/json" \
  -d '{
    "question": "查询最近7天订单数量",
    "datasource_id": 1
  }'
```

### 3) SSE 流式问答

```bash
curl -N -X POST "http://localhost:8000/api/v1/chat/chat-stream" \
  -H "Content-Type: application/json" \
  -H "Accept: text/event-stream" \
  -d '{
    "question": "本月销售额最高的前三个区域",
    "datasource_id": 1,
    "agent_mode": "agent"
  }'
```

## 主要 API

统一前缀：`/api/v1`

- `system`
  - `POST /system/register`
  - `POST /system/login`
  - `GET /system/me`
- `datasource`
  - `GET /datasource`
  - `POST /datasource`
  - `PUT /datasource/{id}`
  - `DELETE /datasource/{id}`
  - `POST /datasource/{id}/test-connection`
- `chat`
  - `POST /chat/generate-sql`
  - `POST /chat/execute-sql`
  - `POST /chat/chat-stream`
  - `GET/POST/PUT/DELETE /chat/conversations...`

## 开发与测试

```bash
# 安装开发依赖
uv sync --extra dev

# 运行测试
uv run pytest

# 覆盖率
uv run pytest --cov=src --cov-report=term-missing

# 代码检查
uv run ruff check .
```

## 开发路线图

- [✅] 基础 API 框架（系统、数据源、问答）
- [✅] 会话管理与 SQL 执行链路
- [✅] SSE 流式对话接口
- [✅] 前端 MVP（聊天 + 数据源管理）
- [ ] 更完善的权限体系与多租户能力
- [✅] 更丰富的可视化结果与分析报告
- [ ] 更强的语义检索与知识增强能力

## 贡献指南

欢迎通过 Issue 和 PR 参与贡献。

1. Fork 仓库并创建特性分支
2. 保持改动聚焦，补充必要测试
3. 本地通过 `pytest` 与 `ruff` 校验
4. 提交 PR，描述变更动机与验证方式

建议在提交前执行：

```bash
uv sync --extra dev
uv run pytest
uv run ruff check .
```

## 可选：Agent 冒烟测试

用于直接验证 Agent + LLM + 数据源链路：

```bash
uv run python -m src.agent.smoke --datasource-id 1 "本月订单最多的前三名用户是谁"
```

## License

本项目采用 [MIT License](./LICENSE)。

## 致谢

感谢以下开源项目带来的启发：

- [DB-GPT](https://github.com/eosphoros-ai/DB-GPT)：为本项目提供了 AI Native 数据应用方向上的架构参考与工程实践启发。
- [SQLBot](https://github.com/dataease/SQLBot)：为本项目提供了自然语言问数链路（NL2SQL）的实现思路。

本项目在整体架构演进过程中，持续从以上项目中汲取设计经验，并结合自身场景进行裁剪、融合与落地。
