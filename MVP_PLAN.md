# SQLBot MVP 开发规划

> 基于 LLM + RAG 的智能问数系统（awesome-project）

---

## 项目概述

awesome-project 是一个智能数据查询系统，用户可以用自然语言提问，系统自动生成 SQL 查询、执行并返回结果。

**核心链路**：
```
用户提问 → LLM生成SQL → 执行SQL → 返回结果
```

---

## 技术选型（简化版）

| 层级 | 技术方案 | 说明 |
|------|----------|------|
| 后端框架 | FastAPI + SQLModel | 简洁高效 |
| 数据库 | PostgreSQL | metadata存储 + SQL执行 |
| LLM | OpenAI API / vLLM / Ollama | 本地部署更省钱 |
| RAG | MVP阶段先不做 | 后续按需添加 |
| 缓存 | 内存缓存 | Redis后期再加 |
| 前端 | React/Vue 或 简单HTML | MVP快速实现 |

---

## MVP 功能范围

### 必须实现（MVP）

- [ ] 用户认证（JWT简单登录）
- [ ] 数据源管理（支持 MySQL / PostgreSQL）
- [ ] 自然语言转 SQL（基于 LLM）
- [ ] SQL 执行与结果返回ce
- [ ] 基础对话界面（Chat）

### 暂不实现（后续版本）

- [ ] 12+ 种数据库支持
- [ ] RAG 增强（Table embedding + 术语库 + 训练数据）
- [ ] 图表可视化
- [ ] 行级/列级权限控制
- [ ] 多 Workspace
- [ ] MCP Server
- [ ] 多语言 i18n

---

## 开发阶段

### Phase 1：核心骨架（预计 1-2 周）

#### 1.1 项目结构搭建
- [ ] 初始化 FastAPI 项目
- [ ] 配置 SQLModel + PostgreSQL 连接
- [ ] 配置 Alembic 数据库迁移


#### 1.2 用户认证
- [ ] JWT 密钥配置
- [ ] 用户注册 / 登录 API
- [ ] Token 验证中间件
- [ ] 简单的用户模型（id, username, password_hash）

#### 1.3 数据源管理 CRUD
- [ ] 数据源模型（id, name, type, host, port, database, username, password）
- [ ] 创建 / 查询 / 更新 / 删除数据源
- [ ] 密码 AES 加密存储
- [ ] 连接测试接口

#### 1.4 基础 API 框架
- [ ] API 路由聚合
- [ ] 统一的响应格式
- [ ] 错误处理中间件

---

### Phase 2：核心链路（预计 2-3 周）

#### 2.1 LLM 集成
- [ ] LLM 接口抽象（支持 OpenAI / vLLM / Ollama）
- [ ] Prompt 模板设计
- [ ] LLM 调用封装

#### 2.2 自然语言 → SQL 生成
- [ ] 根据数据源 Schema 构建 Prompt
- [ ] 调用 LLM 生成 SQL
- [ ] SQL 语法校验
- [ ] 生成结果缓存

#### 2.3 SQL 执行引擎
- [ ] 数据库连接池管理
- [ ] SQL 执行（支持 MySQL / PostgreSQL）
- [ ] 执行结果格式化
- [ ] 错误处理（SQL语法错误、连接超时等）

#### 2.4 Chat 对话接口
- [ ] 对话历史存储
- [ ] 简单的 Chat API
- [ ] 流式输出（可选）

---

### Phase 3：前端界面（预计 1-2 周）

#### 3.1 对话 UI
- [ ] 简单的 Web 界面
- [ ] 消息输入框
- [ ] 对话历史展示
- [ ] SQL 结果展示

#### 3.2 数据源配置界面
- [ ] 数据源列表
- [ ] 添加 / 编辑 / 删除数据源
- [ ] 连接测试

#### 3.3 结果展示
- [ ] 表格展示查询结果
- [ ] 简单的分页

---

### Phase 4：RAG 增强（可选，预计 1-2 周）

> MVP 跑通后再考虑

- [ ] Table Schema Embedding
- [ ] 术语库管理（业务术语 + 同义词）
- [ ] 相似度检索增强 SQL 生成
- [ ] SQL 样例训练数据

---

## 重点参考文件

复刻时重点参考 SQLBot 项目的这些文件：

### 后端核心

| 文件路径 | 说明 |
|----------|------|
| `main.py` | FastAPI 入口 |
| `apps/system/` | 用户认证参考 |
| `apps/datasource/` | 数据源管理参考 |
| `apps/datasource/curd/curd_datasource.py` | 数据源 CRUD |
| `apps/datasource/db/db.py` | SQL 执行引擎 |
| `apps/chat/task/llm.py` | LLM 生成 SQL |
| `apps/template/` | Prompt 模板 |
| `templates/template.yaml` | 系统 Prompt |
| `common/core/config.py` | 配置管理 |
| `common/core/security.py` | JWT 安全 |

### 模型层

| 文件路径 | 说明 |
|----------|------|
| `apps/datasource/models/` | 数据源、表的模型 |
| `apps/system/models/` | 用户、Workspace 模型 |
| `apps/chat/models/` | 对话模型 |

---

## 实施建议

1. **先跑通核心链路**
   - 连数据源 → 问问题 → LLM 生成 SQL → 执行 → 返回结果
   - 用最简单的 Prompt 先让流程跑通

2. **本地 LLM 优先**
   - 推荐使用 vLLM 或 Ollama 部署 Qwen / DeepSeek
   - 省钱 + 可控 + 方便调试

3. **MVP 先不做 RAG**
   - 用好的 Prompt 工程先让 SQL 生成 work 起来
   - RAG 是锦上添花，不是必需品

4. **参考 SQLBot 的模块划分**
   - SQLBot 的目录结构很清晰，直接借鉴
   - API / CRUD / Model / Service 分层

---

## 开发检查清单

### Phase 1 完成标准
- [ ] 能启动 FastAPI 服务
- [ ] 能注册/登录用户
- [ ] 能添加/查看/删除数据源
- [ ] 能测试数据源连接

### Phase 2 完成标准
- [ ] 能用自然语言生成 SQL
- [ ] 能执行生成的 SQL
- [ ] 能返回查询结果
- [ ] 能进行简单对话

### Phase 3 完成标准
- [ ] 有可用的 Web 界面
- [ ] 能在界面上提问并看到结果
- [ ] 能管理数据源

---

## 时间估算

| Phase | 内容 | 预计时间 |
|-------|------|----------|
| Phase 1 | 核心骨架 | 1-2 周 |
| Phase 2 | 核心链路 | 2-3 周 |
| Phase 3 | 前端界面 | 1-2 周 |
| **总计** | **MVP** | **4-7 周** |

---

## 后续扩展方向

- 多种数据库支持（ClickHouse、Elasticsearch 等）
- RAG 增强（Table embedding、术语库、训练数据）
- 图表可视化
- 权限控制系统
- 多租户 / 多 Workspace
- MCP Server 集成
- 多语言支持