# Phase 2.4: Chat 对话接口

## 概述

Chat 对话接口模块提供完整的对话管理功能，包括对话历史存储、会话管理、简单的 Chat API 接口。基于 SQLBot 参考实现，支持对话创建、查询、更新、删除，以及对话记录的存储和检索。

## 架构设计

### 组件结构

```
┌─────────────────────────────────────────────────────────────────┐
│                     API 层                                        │
│  /api/v1/chat/conversations     - 对话管理                      │
│  /api/v1/chat/generate-sql      - SQL生成                       │
│  /api/v1/chat/execute-sql       - SQL执行                       │
│  /api/v1/chat/validate-sql       - SQL验证                       │
│  /api/v1/chat/format-sql         - SQL格式化                     │
│  /api/v1/chat/chat-stream        - 流式对话（SSE）               │
│  /api/v1/chat/recent-questions  - 最近问题                       │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                  CRUD 层 (chat/crud/chat.py)                      │
│  - create_conversation()       - 创建对话                       │
│  - get_conversation_by_id()   - 获取对话                       │
│  - list_conversations()       - 对话列表                       │
│  - update_conversation()      - 更新对话                       │
│  - delete_conversation()       - 删除对话                       │
│  - create_conversation_record() - 创建记录                     │
│  - get_conversation_records()  - 获取记录                       │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                  Model 层 (chat/models/)                          │
│  - Conversation           - 对话模型                             │
│  - ConversationRecord    - 对话记录模型                         │
└─────────────────────────────────────────────────────────────────┘
```

## 数据模型

### Conversation 对话模型

```python
class Conversation(SQLModel, table=True):
    __tablename__ = "chat_conversation"

    id: Optional[int]           # 对话ID
    user_id: int                # 用户ID
    title: str                  # 对话标题
    datasource_id: Optional[int] # 关联数据源ID
    datasource_name: str         # 数据源名称
    db_type: str                # 数据库类型
    create_time: datetime        # 创建时间
    update_time: datetime        # 更新时间
    is_deleted: bool            # 软删除标记
```

### ConversationRecord 对话记录模型

```python
class ConversationRecord(SQLModel, table=True):
    __tablename__ = "chat_conversation_record"

    id: Optional[int]           # 记录ID
    conversation_id: int         # 对话ID
    user_id: int                # 用户ID
    question: str                # 用户问题
    sql: Optional[str]          # 生成的SQL
    sql_answer: Optional[str]   # SQL回答
    sql_error: Optional[str]    # SQL错误
    exec_result: Optional[str]  # 执行结果(JSON)
    chart_type: str             # 图表类型
    chart_config: Optional[str] # 图表配置(JSON)
    is_success: bool            # 是否成功
    finish_time: datetime       # 完成时间
    create_time: datetime        # 创建时间
```

## API 接口

### 对话管理

#### POST /api/v1/chat/conversations - 创建对话

**请求：**
```json
{
    "title": "学生成绩分析",
    "datasource_id": 1
}
```

**响应：**
```json
{
    "code": 200,
    "message": "Conversation created successfully",
    "data": {
        "id": 1,
        "user_id": 1,
        "title": "学生成绩分析",
        "datasource_id": 1,
        "datasource_name": "",
        "db_type": "",
        "create_time": "2024-01-15T10:30:00",
        "update_time": "2024-01-15T10:30:00"
    }
}
```

#### GET /api/v1/chat/conversations - 获取对话列表

**响应：**
```json
{
    "code": 200,
    "message": "Conversations retrieved successfully",
    "data": {
        "total": 2,
        "items": [
            {
                "id": 1,
                "user_id": 1,
                "title": "学生成绩分析",
                "datasource_id": 1,
                "create_time": "2024-01-15T10:30:00",
                "update_time": "2024-01-15T10:30:00"
            }
        ]
    }
}
```

#### GET /api/v1/chat/conversations/{conversation_id} - 获取对话详情

**响应：**
```json
{
    "code": 200,
    "message": "Conversation retrieved successfully",
    "data": {
        "id": 1,
        "user_id": 1,
        "title": "学生成绩分析",
        "datasource_id": 1,
        "datasource_name": "",
        "db_type": "",
        "create_time": "2024-01-15T10:30:00",
        "update_time": "2024-01-15T10:30:00",
        "records": [
            {
                "id": 1,
                "conversation_id": 1,
                "question": "查询学生成绩",
                "sql": "SELECT * FROM student_score LIMIT 1000",
                "sql_answer": "成功查询到100条记录",
                "chart_type": "table",
                "is_success": true,
                "create_time": "2024-01-15T10:31:00"
            }
        ]
    }
}
```

#### PUT /api/v1/chat/conversations/{conversation_id} - 更新对话

**请求：**
```json
{
    "title": "新的标题"
}
```

#### DELETE /api/v1/chat/conversations/{conversation_id} - 删除对话

### SQL 操作

#### POST /api/v1/chat/generate-sql - 生成SQL

**请求：**
```json
{
    "question": "查询所有学生成绩",
    "datasource_id": 1
}
```

#### POST /api/v1/chat/execute-sql - 执行SQL

**请求：**
```json
{
    "question": "查询学生成绩",
    "datasource_id": 1
}
```

**响应：**
```json
{
    "code": 200,
    "message": "Query executed successfully",
    "data": {
        "record_id": 1,
        "sql": "SELECT * FROM student_score LIMIT 1000",
        "result": {
            "columns": ["id", "name", "score"],
            "rows": [[1, "张三", 85.5], [2, "李四", 92.0]],
            "row_count": 2
        },
        "chart_type": "table",
        "error": ""
    }
}
```

### 其他接口

#### POST /api/v1/chat/validate-sql - 验证SQL

#### POST /api/v1/chat/format-sql - 格式化SQL

#### GET /api/v1/chat/recent-questions/{datasource_id} - 获取最近问题

### 流式输出接口

#### POST /api/v1/chat/chat-stream - 流式对话（SSE）

流式对话接口使用 Server-Sent Events（SSE）协议，支持实时返回 SQL 生成和执行状态。

**请求：**
```json
{
    "question": "查询学生成绩",
    "datasource_id": 1
}
```

**SSE 事件流格式：**

```
event: status
data: {"status": "generating_sql", "message": "正在生成SQL..."}

event: sql
data: {"sql": "SELECT * FROM student_score LIMIT 1000", "formatted_sql": "SELECT *\nFROM student_score\nLIMIT 1000", "tables": ["student_score"], "chart_type": "table"}

event: status
data: {"status": "executing_sql", "message": "正在执行SQL..."}

event: result
data: {"columns": ["id", "name", "score"], "rows": [[1, "张三", 85.5], [2, "李四", 92.0]], "row_count": 2}

event: status
data: {"status": "completed", "message": "完成"}

event: done
data: {}
```

**事件类型：**

| 事件 | 说明 | data 内容 |
|------|------|-----------|
| `status` | 状态更新 | `{"status": "...", "message": "..."}` |
| `sql` | SQL 生成完成 | `{"sql": "...", "formatted_sql": "...", "tables": [...], "chart_type": "..."}` |
| `result` | SQL 执行结果 | `{"columns": [...], "rows": [...], "row_count": N}` |
| `error` | 错误信息 | `{"error": "..."}` |
| `done` | 结束信号 | `{}` |

**状态值：**

| 状态 | 说明 |
|------|------|
| `generating_sql` | 正在生成 SQL |
| `executing_sql` | 正在执行 SQL |
| `completed` | 完成 |
| `error` | 发生错误 |

**curl 测试命令：**
```bash
curl -X POST http://localhost:8000/api/v1/chat/chat-stream \
  -H "Content-Type: application/json" \
  -H "Accept: text/event-stream" \
  -d '{"question": "查询学生成绩", "datasource_id": 1}'
```

## CRUD 操作

### 创建对话

```python
def create_conversation(
    session: Session,
    user_id: int,
    title: str = "",
    datasource_id: Optional[int] = None,
    datasource_name: str = "",
    db_type: str = ""
) -> Conversation:
    """创建新对话"""
    conversation = Conversation(
        user_id=user_id,
        title=title or datetime.now().strftime("%Y-%m-%d %H:%M"),
        datasource_id=datasource_id,
        datasource_name=datasource_name,
        db_type=db_type,
        create_time=datetime.now(),
        update_time=datetime.now(),
    )
    session.add(conversation)
    session.commit()
    session.refresh(conversation)
    return conversation
```

### 创建对话记录

```python
def create_conversation_record(
    session: Session,
    conversation_id: int,
    user_id: int,
    question: str,
    sql: Optional[str] = None,
    sql_answer: Optional[str] = None,
    sql_error: Optional[str] = None,
    exec_result: Optional[Any] = None,
    chart_type: str = "table",
    chart_config: Optional[dict] = None,
    is_success: bool = True
) -> ConversationRecord:
    """创建新对话记录"""
    record = ConversationRecord(
        conversation_id=conversation_id,
        user_id=user_id,
        question=question,
        sql=sql,
        sql_answer=sql_answer,
        sql_error=sql_error,
        exec_result=json.dumps(exec_result) if exec_result else None,
        chart_type=chart_type,
        chart_config=json.dumps(chart_config) if chart_config else None,
        is_success=is_success,
        finish_time=datetime.now(),
        create_time=datetime.now(),
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    return record
```

### 获取最近问题

```python
def get_recent_questions(
    session: Session,
    datasource_id: int,
    user_id: int,
    limit: int = 10
) -> List[str]:
    """获取最近的问题列表，用于推荐追问"""
    statement = (
        select(ConversationRecord.question)
        .where(
            and_(
                ConversationRecord.user_id == user_id,
                ConversationRecord.sql.isnot(None),
                ConversationRecord.sql_error.is_(None)
            )
        )
        .order_by(desc(ConversationRecord.create_time))
        .limit(limit)
    )
    return session.exec(statement).all()
```

## 文件结构

```
src/
├── chat/
│   ├── api/
│   │   ├── __init__.py
│   │   └── chat.py           # API端点
│   ├── crud/
│   │   ├── __init__.py
│   │   └── chat.py           # CRUD操作
│   ├── models/
│   │   ├── __init__.py
│   │   └── conversation.py    # 数据模型
│   ├── service/
│   │   ├── __init__.py
│   │   └── sql_generator.py  # SQL生成服务
│   ├── utils/
│   │   ├── __init__.py
│   │   └── sql_validator.py  # SQL验证
│   └── schemas.py             # Pydantic schemas
├── datasource/
│   ├── api/
│   │   └── datasource.py     # 数据源API
│   ├── crud/
│   │   └── crud_datasource.py  # 数据源CRUD
│   └── db/
│       └── db.py              # 数据库操作
└── common/
    ├── schemas/
    │   └── response.py        # 统一响应格式
    └── exceptions/
        └── base.py            # 异常定义
```

## 实现过程

### Phase 2.4 实现步骤

1. **创建数据模型**
   - 创建 `Conversation` 模型存储对话基本信息
   - 创建 `ConversationRecord` 模型存储对话记录

2. **实现 CRUD 操作**
   - 创建对话 `create_conversation`
   - 获取对话 `get_conversation_by_id`
   - 对话列表 `list_conversations`
   - 更新对话 `update_conversation`
   - 删除对话 `delete_conversation`
   - 创建记录 `create_conversation_record`
   - 获取记录 `get_conversation_records`

3. **实现 API 端点**
   - 对话管理 API（创建、列表、详情、更新、删除）
   - SQL 操作 API（生成、执行、验证、格式化）
   - 最近问题 API

4. **数据模型设计**
   - 使用软删除标记 `is_deleted`
   - JSON 格式存储执行结果和图表配置
   - 支持时间戳追踪

## 数据库表结构

### chat_conversation 表

```sql
CREATE TABLE chat_conversation (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    title TEXT DEFAULT '',
    datasource_id BIGINT,
    datasource_name TEXT DEFAULT '',
    db_type TEXT DEFAULT '',
    create_time TIMESTAMP,
    update_time TIMESTAMP,
    is_deleted BOOLEAN DEFAULT FALSE
);

CREATE INDEX idx_conversation_user_id ON chat_conversation(user_id);
```

### chat_conversation_record 表

```sql
CREATE TABLE chat_conversation_record (
    id BIGSERIAL PRIMARY KEY,
    conversation_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    question TEXT NOT NULL,
    sql TEXT,
    sql_answer TEXT,
    sql_error TEXT,
    exec_result TEXT,
    chart_type TEXT DEFAULT 'table',
    chart_config TEXT,
    is_success BOOLEAN DEFAULT TRUE,
    finish_time TIMESTAMP,
    create_time TIMESTAMP
);

CREATE INDEX idx_record_conversation_id ON chat_conversation_record(conversation_id);
CREATE INDEX idx_record_user_id ON chat_conversation_record(user_id);
```

## 测试

### 单元测试

```python
# 测试创建对话
from src.chat.crud import create_conversation, list_conversations

conversation = create_conversation(
    session=db,
    user_id=1,
    title="测试对话",
    datasource_id=1
)
assert conversation.id is not None

# 测试对话列表
conversations = list_conversations(session=db, user_id=1)
assert len(conversations) > 0
```

### API 测试

```bash
# 创建对话
curl -X POST http://localhost:8000/api/v1/chat/conversations \
  -H "Content-Type: application/json" \
  -d '{"title": "学生成绩分析", "datasource_id": 1}'

# 获取对话列表
curl -X GET http://localhost:8000/api/v1/chat/conversations

# 执行 SQL
curl -X POST http://localhost:8000/api/v1/chat/execute-sql \
  -H "Content-Type: application/json" \
  -d '{"question": "查询学生成绩", "datasource_id": 1}'

# 流式对话（测试 SSE 流式输出）
curl -X POST http://localhost:8000/api/v1/chat/chat-stream \
  -H "Content-Type: application/json" \
  -H "Accept: text/event-stream" \
  -d '{"question": "查询学生成绩", "datasource_id": 1}'
```

## 参考资料

- SQLBot Chat实现：`/Users/tanghaoyu/develop/git-repo/opensource/SQLBot-main/backend/apps/chat/`
- SQLBot Chat模型：`/Users/tanghaoyu/develop/git-repo/opensource/SQLBot-main/backend/apps/chat/models/chat_model.py`
- SQLBot Chat CRUD：`/Users/tanghaoyu/develop/git-repo/opensource/SQLBot-main/backend/apps/chat/curd/chat.py`