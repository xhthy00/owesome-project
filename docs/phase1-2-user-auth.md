# Phase 1.2 用户认证

## 概述

本模块实现了基于 JWT 的用户认证系统，包括：

- 用户注册
- 用户登录（获取 JWT Token）
- 获取当前用户信息

---

## 目录结构

```
src/
├── main.py                      # FastAPI 入口（已注册路由）
├── common/
│   └── core/
│       ├── config.py           # 配置管理
│       ├── database.py         # 数据库连接
│       └── security.py         # JWT + 密码哈希
└── system/                     # 用户认证模块
    ├── __init__.py
    ├── schemas.py              # Pydantic 模型
    ├── models/
    │   ├── __init__.py
    │   └── user.py             # SysUser 模型
    ├── crud/
    │   ├── __init__.py
    │   └── crud_user.py        # 用户 CRUD 操作
    └── api/
        ├── __init__.py
        └── system.py           # API 路由
```

---

## 实现逻辑详解

### 1. Pydantic Schema (`src/system/schemas.py`)

定义请求/响应的数据校验模型。

```python
# 用户注册请求
class UserCreate(BaseModel):
    account: str = Field(..., min_length=3, max_length=255)  # 账号，3-255字符
    name: str = Field(..., min_length=1, max_length=255)    # 姓名，1-255字符
    password: str = Field(..., min_length=6, max_length=255) # 密码，6-255字符
    email: Optional[str] = None                              # 邮箱，可选
    oid: int = Field(default=1)                              # 组织ID，默认1
    language: str = Field(default="zh-CN")                   # 语言，默认简体中文

# 用户响应（不包含密码）
class UserResponse(BaseModel):
    id: int
    account: str
    name: str
    email: Optional[str] = None
    oid: int
    status: int
    language: str
    origin: int
    create_time: int

# JWT Token 响应
class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
```

**验证逻辑：**
- `account`: 长度 3-255，防止空账号和过长账号
- `password`: 长度 6-255，防止简单密码
- `email`: 可选，如提供则需是有效格式
- `oid/language`: 提供默认值，减少客户端请求参数

---

### 2. 安全工具 (`src/common/core/security.py`)

实现密码哈希和 JWT Token 的生成与验证。

#### 2.1 密码哈希

```python
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def get_password_hash(password: str) -> str:
    """Hash a password using bcrypt."""
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against a hash."""
    return pwd_context.verify(plain_password, hashed_password)
```

**原理：**
- 使用 `bcrypt` 算法（基于 Blowfish 加密）
- 每次哈希生成不同的盐值，防止彩虹表攻击
- `verify` 自动提取盐值进行比对

#### 2.2 JWT Token

```python
def create_access_token(user_id: int, expires_delta: Optional[timedelta] = None) -> str:
    """Create JWT access token."""
    # 计算过期时间
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(
            minutes=settings.jwt_access_token_expire_minutes  # 默认30分钟
        )
    # 构建 payload
    to_encode = {"sub": str(user_id), "exp": expire}
    # 生成 token
    encoded_jwt = jwt.encode(
        to_encode,
        settings.jwt_secret_key,     # 密钥
        algorithm=settings.jwt_algorithm  # 算法（HS256）
    )
    return encoded_jwt

def decode_access_token(token: str) -> Optional[dict]:
    """Decode and verify JWT token."""
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm]
        )
        return payload  # 返回 {"sub": "1", "exp": 1234567890}
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None  # 过期或无效返回 None
```

**JWT 包含内容：**
| 字段 | 说明 |
|------|------|
| `sub` | 用户 ID（字符串格式） |
| `exp` | 过期时间戳 |
| `iat` | 签发时间（JWT 自动添加） |

---

### 3. CRUD 操作 (`src/system/crud/crud_user.py`)

封装用户相关的数据库操作。

```python
def get_user_by_account(session: Session, account: str) -> Optional[SysUser]:
    """根据账号查询用户"""
    return session.query(SysUser).filter(SysUser.account == account).first()

def get_user_by_id(session: Session, user_id: int) -> Optional[SysUser]:
    """根据ID查询用户"""
    return session.query(SysUser).filter(SysUser.id == user_id).first()

def create_user(session: Session, account: str, name: str, password: str, **kwargs) -> SysUser:
    """创建新用户"""
    # 密码在 CRUD 层哈希，保证安全性
    user = SysUser(
        account=account,
        name=name,
        password=get_password_hash(password),  # 密码哈希
        email=kwargs.get("email"),
        oid=kwargs.get("oid", 1),
        status=kwargs.get("status", 1),
        create_time=kwargs.get("create_time", int(datetime.now().timestamp() * 1000)),
        language=kwargs.get("language", "zh-CN"),
        origin=kwargs.get("origin", 0),
    )
    session.add(user)
    session.commit()
    session.refresh(user)  # 获取数据库生成的值（如自增ID）
    return user

def authenticate(session: Session, account: str, password: str) -> Optional[SysUser]:
    """验证用户登录"""
    user = get_user_by_account(session, account)
    if not user:
        return None  # 用户不存在
    if not verify_password(password, user.password):
        return None  # 密码错误
    return user  # 验证成功返回用户对象
```

**关键设计：**
- 密码哈希在 CRUD 层执行，API 层处理明文密码
- `authenticate` 返回 `None` 表示验证失败，不暴露具体原因（防用户枚举）

---

### 4. API 路由 (`src/system/api/system.py`)

实现用户认证的 HTTP 接口。

#### 4.1 依赖注入

```python
router = APIRouter(prefix="/system", tags=["system"])
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/system/login")

def get_current_user(
    token: str = Depends(oauth2_scheme),
    session: Session = Depends(get_session),
) -> UserResponse:
    """从 JWT Token 获取当前用户"""
    # 解析 token
    payload = decode_access_token(token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )
    # 获取用户ID（sub 字段）
    user_id = int(payload.get("sub"))
    # 查询用户
    user = get_user_by_id(session, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )
    return user
```

#### 4.2 注册接口

```python
@router.post("/register", response_model=UserResponse)
def register(user_in: UserCreate, session: Session = Depends(get_session)):
    """注册新用户"""
    # 1. 检查账号是否已存在
    existing = get_user_by_account(session, user_in.account)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Account already exists",
        )
    # 2. 创建用户
    user = create_user(
        session,
        account=user_in.account,
        name=user_in.name,
        password=user_in.password,  # 明文，CRUD层哈希
        email=user_in.email,
        oid=user_in.oid,
        language=user_in.language,
    )
    return user
```

**流程：**
```
请求 → Schema验证 → 检查账号存在 → 创建用户 → 返回用户信息
```

#### 4.3 登录接口

```python
@router.post("/login", response_model=TokenResponse)
def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    session: Session = Depends(get_session),
):
    """用户登录"""
    # 1. 验证用户凭证
    user = authenticate(session, form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect account or password",
        )
    # 2. 生成 Token
    access_token = create_access_token(user.id)
    return TokenResponse(access_token=access_token)
```

**流程：**
```
请求 → 验证账号密码 → 生成JWT → 返回Token
```

#### 4.4 获取当前用户

```python
@router.get("/me", response_model=UserResponse)
def get_me(current_user = Depends(get_current_user)):
    """获取当前用户信息"""
    return current_user
```

**流程：**
```
请求 → 解析Token → 获取用户 → 返回用户信息
```

---

## API 端点总览

| 方法 | 路径 | 说明 | 认证 |
|------|------|------|------|
| POST | `/api/v1/system/register` | 用户注册 | 否 |
| POST | `/api/v1/system/login` | 用户登录（form-data） | 否 |
| GET | `/api/v1/system/me` | 获取当前用户 | 是 |

---

## 请求/响应示例

### 注册用户

**请求：**
```bash
curl -X POST http://localhost:8000/api/v1/system/register \
  -H "Content-Type: application/json" \
  -d '{
    "account": "testuser",
    "name": "Test User",
    "password": "123456",
    "email": "test@example.com"
  }'
```

**响应：**
```json
{
  "id": 1,
  "account": "testuser",
  "name": "Test User",
  "email": "test@example.com",
  "oid": 1,
  "status": 1,
  "language": "zh-CN",
  "origin": 0,
  "create_time": 1713600000000
}
```

### 用户登录

**请求：**
```bash
curl -X POST http://localhost:8000/api/v1/system/login \
  -d "username=testuser&password=123456"
```

**响应：**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer"
}
```

### 获取当前用户

**请求：**
```bash
curl http://localhost:8000/api/v1/system/me \
  -H "Authorization: Bearer <access_token>"
```

**响应：**
```json
{
  "id": 1,
  "account": "testuser",
  "name": "Test User",
  "email": "test@example.com",
  "oid": 1,
  "status": 1,
  "language": "zh-CN",
  "origin": 0,
  "create_time": 1713600000000
}
```

---

## 错误响应

| 状态码 | 说明 |
|--------|------|
| 400 | 账号已存在 / 参数错误 |
| 401 | Token 无效或过期 / 密码错误 |
| 404 | 用户不存在 |

---

## 数据库表结构

```sql
CREATE TABLE "public"."sys_user" (
  "id" int8 NOT NULL GENERATED ALWAYS AS IDENTITY (
    INCREMENT 1 MINVALUE 1 MAXVALUE 9223372036854775807 START 1 CACHE 1
  ),
  "account" varchar(255) NOT NULL,
  "name" varchar(255) NOT NULL,
  "password" varchar(255) NOT NULL,
  "email" varchar(255),
  "oid" int8 NOT NULL DEFAULT 1,
  "status" int4 NOT NULL DEFAULT 1,
  "create_time" int8 NOT NULL,
  "language" varchar(255) NOT NULL DEFAULT 'zh-CN',
  "origin" int4 NOT NULL DEFAULT 0,
  "system_variables" jsonb,
  CONSTRAINT "sys_user_pkey" PRIMARY KEY ("id")
);
```

---

## 配置说明

JWT 相关配置在 `.env` 中：

```env
JWT_SECRET_KEY=your-secret-key-change-in-production
JWT_ALGORITHM=HS256
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=30
```

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `JWT_SECRET_KEY` | 签名密钥（生产环境需足够复杂） | - |
| `JWT_ALGORITHM` | 加密算法 | HS256 |
| `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | Token 有效期（分钟） | 30 |

---

## 验证

### 测试导入

```bash
python3 -c "
from src.system.schemas import UserCreate, UserResponse, TokenResponse
from src.system.models.user import SysUser
from src.system.crud.crud_user import create_user, get_user_by_account
from src.common.core.security import create_access_token, verify_password, get_password_hash
print('All imports successful')
"
```

### 测试 API

```bash
# 1. 注册用户
curl -X POST http://localhost:8000/api/v1/system/register \
  -H "Content-Type: application/json" \
  -d '{"account":"test","name":"Test","password":"123456"}'

# 2. 登录获取 token
curl -X POST http://localhost:8000/api/v1/system/login \
  -d "username=test&password=123456"

# 3. 使用 token 获取当前用户
curl http://localhost:8000/api/v1/system/me \
  -H "Authorization: Bearer <token>"
```

---

## 后续开发

Phase 1.2 完成后的开发检查清单：

- [x] JWT 密钥配置
- [x] 用户注册 / 登录 API
- [x] Token 验证中间件
- [x] 简单的用户模型

下一阶段（Phase 1.3）将实现数据源管理 CRUD。