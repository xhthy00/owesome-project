# awesome-project

基于 FastAPI + LLM 的智能问数系统。用户可以使用自然语言提问，系统自动生成 SQL、执行查询并返回结构化结果，支持普通执行与 SSE 流式对话。

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

## 可选：Agent 冒烟测试

用于直接验证 Agent + LLM + 数据源链路：

```bash
uv run python -m src.agent.smoke --datasource-id 1 "本月订单最多的前三名用户是谁"
```
