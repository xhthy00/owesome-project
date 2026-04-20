# Phase 1.3 数据源管理 CRUD

## 概述

本模块实现了数据源的增删改查（CRUD）功能，包括：

- 创建数据源（密码 AES 加密存储）
- 查询数据源列表
- 更新数据源信息
- 删除数据源
- 连接测试接口

---

## 目录结构

```
src/
├── main.py                          # FastAPI 入口（已注册路由）
├── common/
│   └── utils/
│       └── aes.py                   # AES 加密工具
└── datasource/                      # 数据源模块
    ├── schemas.py                   # Pydantic 模型
    ├── models/
    │   └── datasource.py            # SQLModel 模型（已存在）
    ├── crud/
    │   ├── __init__.py
    │   └── crud_datasource.py      # CRUD 操作
    ├── api/
    │   ├── __init__.py
    │   └── datasource.py            # API 路由
    └── db/
        ├── __init__.py
        └── db.py                    # 数据库连接测试
```

---

## 实现逻辑详解

### 1. AES 加密工具 (`src/common/utils/aes.py`)

用于对数据源配置（包含密码）进行 AES 加密存储。

```python
from src.common.utils.aes import encrypt_conf, decrypt_conf

# 加密配置
encrypted = encrypt_conf({
    "host": "localhost",
    "port": 5432,
    "username": "postgres",
    "password": "secret123",
    "database": "mydb"
})

# 解密配置
config = decrypt_conf(encrypted)
```

**加密原理：**
- 算法：AES-256-CBC
- 密钥：使用 `JWT_SECRET_KEY` 的前 32 字节
- IV：密钥的前 16 字节
- 输出：Base64 编码（IV + 加密数据）

**为什么用 AES：**
- 配置中包含数据库密码，不能明文存储
- AES 是对称加密，加解密使用相同密钥
- CBC 模式提供随机化，相同明文加密结果不同

---

### 2. Pydantic Schema (`src/datasource/schemas.py`)

定义请求/响应的数据校验模型。

```python
class DatasourceConfig(BaseModel):
    """数据源连接配置"""
    host: str
    port: int
    username: str
    password: str
    database: str
    driver: str = ""
    extraJdbc: str = ""
    dbSchema: str = ""
    timeout: int = 30
    ssl: bool = False
    type: str = "pg"  # mysql 或 pg

class DatasourceCreate(BaseModel):
    """创建数据源请求"""
    name: str = Field(..., min_length=1, max_length=128)
    description: str = ""
    type: str  # mysql / pg
    config: DatasourceConfig

class DatasourceUpdate(BaseModel):
    """更新数据源请求（可选字段）"""
    name: Optional[str] = None
    description: Optional[str] = None
    type: Optional[str] = None
    config: Optional[DatasourceConfig] = None

class DatasourceResponse(BaseModel):
    """数据源响应"""
    id: int
    name: str
    description: Optional[str]
    type: str
    type_name: Optional[str]
    status: Optional[str]
    create_time: Optional[datetime]
    create_by: Optional[int]

class ConnectionTestResult(BaseModel):
    """连接测试结果"""
    success: bool
    message: str
    version: Optional[str] = None
```

---

### 3. CRUD 操作 (`src/datasource/crud/crud_datasource.py`)

封装数据源相关的数据库操作。

```python
def create_datasource(session, name, type, config, **kwargs) -> CoreDatasource:
    """创建数据源"""
    # 配置加密存储
    encrypted_config = encrypt_conf(config)
    datasource = CoreDatasource(
        name=name,
        type=type,
        configuration=encrypted_config,  # 加密后的配置
        status="active",
        ...
    )
    session.add(datasource)
    session.commit()
    return datasource

def update_datasource(session, datasource_id, **kwargs) -> Optional[CoreDatasource]:
    """更新数据源"""
    # 如果更新配置，需要重新加密
    if "config" in kwargs:
        kwargs["configuration"] = encrypt_conf(kwargs.pop("config"))
    ...

def get_decrypted_config(session, datasource_id) -> Optional[dict]:
    """获取解密后的配置（用于连接测试）"""
    datasource = get_datasource_by_id(session, datasource_id)
    if not datasource:
        return None
    return decrypt_conf(datasource.configuration)
```

**关键设计：**
- 密码在 CRUD 层加密，保证安全性
- API 层处理明文配置，CRUD 层处理加密
- `get_decrypted_config` 单独提供，用于连接测试

---

### 4. 数据库连接测试 (`src/datasource/db/db.py`)

```python
def test_db_connection(db_type: str, config: dict) -> Tuple[bool, str, Optional[str]]:
    """测试数据库连接"""
    if db_type == "pg":
        return test_postgresql_connection(config)
    elif db_type == "mysql":
        return test_mysql_connection(config)
    else:
        return False, f"Unsupported: {db_type}", None

def test_postgresql_connection(config: dict) -> Tuple[bool, str, Optional[str]]:
    """测试 PostgreSQL 连接"""
    import psycopg2
    conn = psycopg2.connect(
        host=config["host"],
        port=config["port"],
        user=config["username"],
        password=config["password"],
        database=config["database"],
        connect_timeout=config.get("timeout", 30),
    )
    cursor = conn.cursor()
    cursor.execute("SELECT version()")
    version = cursor.fetchone()[0]
    cursor.close()
    conn.close()
    return True, "Success", version
```

**支持类型：** PostgreSQL、MySQL

---

### 5. API 路由 (`src/datasource/api/datasource.py`)

| 方法 | 路径 | 说明 | 认证 |
|------|------|------|------|
| GET | `/api/v1/datasource` | 查询数据源列表 | 是 |
| GET | `/api/v1/datasource/{id}` | 获取单个数据源 | 是 |
| POST | `/api/v1/datasource` | 创建数据源 | 是 |
| PUT | `/api/v1/datasource/{id}` | 更新数据源 | 是 |
| DELETE | `/api/v1/datasource/{id}` | 删除数据源 | 是 |
| POST | `/api/v1/datasource/{id}/test-connection` | 测试连接 | 是 |

---

## 请求/响应示例

### 创建数据源

**请求：**
```bash
curl -X POST http://localhost:8000/api/v1/datasource \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "My PostgreSQL",
    "description": "测试数据库",
    "type": "pg",
    "config": {
      "host": "localhost",
      "port": 5432,
      "username": "postgres",
      "password": "123456",
      "database": "testdb"
    }
  }'
```

**响应：**
```json
{
  "id": 1,
  "name": "My PostgreSQL",
  "description": "测试数据库",
  "type": "pg",
  "type_name": null,
  "status": "active",
  "create_time": "2024-01-01T00:00:00",
  "create_by": 1
}
```

### 查询数据源列表

**请求：**
```bash
curl http://localhost:8000/api/v1/datasource \
  -H "Authorization: Bearer <token>"
```

**响应：**
```json
{
  "total": 1,
  "items": [
    {
      "id": 1,
      "name": "My PostgreSQL",
      "description": "测试数据库",
      "type": "pg",
      "type_name": null,
      "status": "active",
      "create_time": "2024-01-01T00:00:00",
      "create_by": 1
    }
  ]
}
```

### 测试连接

**请求：**
```bash
curl -X POST http://localhost:8000/api/v1/datasource/1/test-connection \
  -H "Authorization: Bearer <token>"
```

**响应：**
```json
{
  "success": true,
  "message": "Connection successful",
  "version": "PostgreSQL 16.1"
}
```

### 更新数据源

**请求：**
```bash
curl -X PUT http://localhost:8000/api/v1/datasource/1 \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "My PostgreSQL Updated",
    "config": {
      "host": "localhost",
      "port": 5432,
      "username": "postgres",
      "password": "newpassword",
      "database": "newdb"
    }
  }'
```

### 删除数据源

**请求：**
```bash
curl -X DELETE http://localhost:8000/api/v1/datasource/1 \
  -H "Authorization: Bearer <token>"
```

---

## 错误响应

| 状态码 | 说明 |
|--------|------|
| 400 | 参数错误 |
| 401 | 未认证 / Token 无效 |
| 404 | 数据源不存在 |

---

## 数据库表结构

### core_datasource

```sql
CREATE TABLE "public"."core_datasource" (
  "id" int8 NOT NULL GENERATED ALWAYS AS IDENTITY (...),
  "name" varchar(128) NOT NULL,
  "description" varchar(512),
  "type" varchar(64) NOT NULL,
  "type_name" varchar(64),
  "configuration" text NOT NULL,  -- AES 加密存储
  "create_time" timestamp(6),
  "create_by" int8,
  "status" varchar(64),
  "num" varchar(256),
  "oid" int8,
  "table_relation" jsonb,
  "embedding" text,
  "recommended_config" int8,
  CONSTRAINT "core_datasource_pkey" PRIMARY KEY ("id")
);
```

### core_table

```sql
CREATE TABLE "public"."core_table" (
  "id" int8 NOT NULL GENERATED ALWAYS AS IDENTITY (...),
  "ds_id" int8,
  "checked" bool NOT NULL DEFAULT true,
  "table_name" text,
  "table_comment" text,
  "custom_comment" text,
  "embedding" text,
  CONSTRAINT "core_table_pkey" PRIMARY KEY ("id")
);
```

### core_field

```sql
CREATE TABLE "public"."core_field" (
  "id" int8 NOT NULL GENERATED ALWAYS AS IDENTITY (...),
  "ds_id" int8,
  "table_id" int8,
  "checked" bool NOT NULL DEFAULT true,
  "field_name" text,
  "field_type" varchar(128),
  "field_comment" text,
  "custom_comment" text,
  "field_index" int8,
  CONSTRAINT "core_field_pkey" PRIMARY KEY ("id")
);
```

---

## 配置说明

AES 加密使用 `.env` 中的 `JWT_SECRET_KEY` 作为密钥：

```env
JWT_SECRET_KEY=your-secret-key-change-in-production
```

**注意：** 生产环境请使用足够复杂的密钥，并妥善保管。

---

## 验证

### 测试 AES 加密

```bash
python3 -c "
from src.common.utils.aes import encrypt_conf, decrypt_conf

config = {'host': 'localhost', 'port': 5432, 'password': 'secret'}
encrypted = encrypt_conf(config)
decrypted = decrypt_conf(encrypted)
print('Match:', config == decrypted)
"
```

### 测试 API

```bash
# 1. 创建数据源
curl -X POST http://localhost:8000/api/v1/datasource \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"name":"test","type":"pg","config":{"host":"localhost","port":5432,"username":"postgres","password":"","database":"postgres"}}'

# 2. 查询列表
curl http://localhost:8000/api/v1/datasource \
  -H "Authorization: Bearer <token>"

# 3. 测试连接
curl -X POST http://localhost:8000/api/v1/datasource/1/test-connection \
  -H "Authorization: Bearer <token>"
```

---

## 后续开发

Phase 1.3 完成后的开发检查清单：

- [x] 数据源相关模型
- [x] 创建 / 查询 / 更新 / 删除数据源
- [x] 密码 AES 加密存储
- [x] 连接测试接口

下一阶段（Phase 1.4）将实现基础 API 框架。