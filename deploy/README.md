# awesome-data 部署手册（Ubuntu 24.04 · 单镜像 · 构建与运行解耦）

目标：把 FastAPI 后端 + Next.js 前端装进**同一个 Docker 镜像**，由 supervisord 守护两进程；
外部 PostgreSQL、LLM 均走云端/外部；宿主机已部署的 Nginx 反代到容器仅暴露的 `3001` 端口。

> 本手册反映 `deploy/` 目录下 Dockerfile / docker-compose.yml / supervisord.conf / entrypoint.sh 的**当前实际状态**。
> 核心设计：**构建与运行解耦**——镜像单独 `docker build`，运行只 `docker compose up`，compose 不再内嵌 build。

```
┌─────────────────┐   80    ┌─────────────────────────────────────────────┐
│  宿主 Nginx     │──80──►  │ 单镜像 awesome-data                          │
│ (已部署)        │  /api,  │  supervisord:                                │
└─────────────────┘  /_next │   ├─ uvicorn :8000(127.0.0.1) 后端单 worker  │
                             │   └─ next start :3001(0.0.0.0)   前端       │
                             │  仅对外暴露 3001                              │
                             └────┬──────────────────────────────────────────┘
                                  │ DATABASE_URL → 外部 PG(已装 pgvector)
                                  │ LLM_BASE_URL → 云端 LLM(MiniMax 等)
```

## 0. 目录与文件归属

部署涉及两个**物理隔离**的目录，职责分明：

| 目录 | 角色 | 内容 |
|---|---|---|
| **源码目录**（如 `/opt/awesome-data`） | 构建 | 项目源码 + `deploy/Dockerfile` 等构建所需文件。**只在这里 `docker build`** |
| **部署目录**（如 `/data/docker/awesome-data`） | 运行 | `docker-compose.yml` + `.env`（二者同目录）。**只在这里 `docker compose up`** |

> 镜像一旦构建好，源码目录可以不再参与运行；部署目录只需要 compose + .env + 镜像即可拉起服务。
> 两者可以不在同一台机器（CI 构建镜像 → 推 registry → 部署机拉取运行）。

`deploy/` 目录下的部署文件清单：

| 文件 | 作用 | 何时需要改 |
|---|---|---|
| `Dockerfile` | 多阶段构建定义（前端→后端→运行时） | 改依赖、改构建逻辑时 |
| `docker-compose.yml` | 运行编排（引用镜像、端口、env、健康检查） | 改端口/环境/资源限制时 |
| `entrypoint.sh` | 容器入口：先跑 alembic 迁移，再 exec supervisord | 改启动前初始化逻辑时 |
| `supervisord.conf` | 容器内进程守护：backend(uvicorn) + frontend(next start) | 改进程命令/环境时 |
| `nginx.conf` | 宿主 Nginx 反代片段（反代到 3001，SSE 关 buffering） | 接入宿主 Nginx 时参考 |
| `.env.example` | 环境变量模板（**不含真实值**） | 新增配置项时 |
| `.env` | 真实环境变量（**不进镜像、不进 git**，由 compose `env_file` 运行时注入） | 部署时填值 |

## 1. 前置准备

- Ubuntu 24.04 LTS，已装 Docker + Compose v2（`docker compose version` 可见）。
  若未装：
  ```bash
  curl -fsSL https://get.docker.com | sh
  sudo usermod -aG docker $USER   # 重新登录生效
  ```
- 外部 PostgreSQL **已装 pgvector 扩展**（项目用 pgvector / sentence-transformers）：
  ```bash
  psql -h <pg-host> -U root -d awesome -c "CREATE EXTENSION IF NOT EXISTS vector;"
  ```
  且该 PG 端口对这台 Ubuntu 可达。
- 服务器**无 GPU 也能跑**：镜像里的 torch 是 CPU 专用 wheel（`torch==2.13.0+cpu`），不依赖 CUDA/nvidia 驱动。
  这由 `pyproject.toml` 的 `index-strategy = "unsafe-best-match"` + `pytorch-cpu` index 保证（见 §7 说明）。

## 2. 取代码到源码目录

```bash
sudo mkdir -p /opt/awesome-data && sudo chown $USER /opt/awesome-data
cd /opt/awesome-data
# git clone <你的仓库> .   # 或把当前代码上传到此目录
```

源码目录需包含：`src/`、`frontend-react/`、`alembic/`、`alembic.ini`、`pyproject.toml`、`uv.lock`、`deploy/`。

## 3. 准备 .env（部署目录）

部署目录与源码目录**分开**，先建部署目录并放 `.env`：

```bash
sudo mkdir -p /data/docker/awesome-data
cd /data/docker/awesome-data
cp /opt/awesome-data/.env.example ./.env      # 复制模板
# 生成强随机 JWT 密钥并写入 .env
python3 -c "import secrets;print(secrets.token_urlsafe(48))"
```

编辑 `.env`，至少填好以下字段（其余按 `.env.example` 注释）：

```ini
APP_NAME=awesome-project
DEBUG=false
API_PREFIX=/api/v1

# 指向你的外部 PG（注意端口、库名）
DATABASE_URL=postgresql://<用户>:<密码>@<pg-host>:<pg-port>/awesome

# 用上面 secrets.token_urlsafe(48) 的输出覆盖占位
JWT_SECRET_KEY=<强随机密钥>
JWT_ALGORITHM=HS256
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=30

# LLM（示例为 MiniMax；按你实际 provider 改）
LLM_BASE_URL=https://api.minimaxi.com/v1
LLM_API_KEY=<你的 key>
LLM_MODEL=MiniMax-M3
```

> `.env` 不会被构建进镜像（`.dockerignore` 已排除 `.env`），由 compose 的 `env_file` 运行时注入。
> `.env` 与 `docker-compose.yml` **必须在同一目录**（compose 里写的是 `env_file: ./.env`）。

## 4. 构建镜像（在源码目录）

> ⚠️ 解耦后 compose **不再负责构建**。镜像必须单独 `docker build`。

```bash
cd /opt/awesome-data
docker build -t awesome-data:latest -f deploy/Dockerfile .
```

要点：
- **必须在项目根目录执行**（build context = `.`），因为 Dockerfile 里 `COPY src/`、`COPY frontend-react/`、`COPY deploy/supervisord.conf` 都是相对根目录的。
- 多阶段构建：`frontend-build`（node:20，npm 装包+next build）→ `backend-build`（python:3.11，uv sync 装依赖）→ `runtime`（python:3.11 + 拷入 node 运行时 + 前后端产物 + supervisord）。
- 全程国内源，无需代理：
  - 基础镜像：华为云 `swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/library/*`
  - npm：`registry.npmmirror.com`
  - uv/PyPI：`pypi.tuna.tsinghua.edu.cn`
  - torch CPU wheel：`download.pytorch.org/whl/cpu`
- 构建耗时取决于网络，典型 8~15 分钟（首次）。后续若 `pyproject.toml`/`uv.lock`/`package.json` 未变，Docker 层缓存命中，仅重跑改动层。
- 构建日志可重定向落盘便于排查：`docker build -t awesome-data:latest -f deploy/Dockerfile . 2>&1 | tee build.log`

构建成功后确认镜像：

```bash
docker images awesome-data
# 期望看到 awesome-data:latest，约 4GB
```

## 5. 启动服务（在部署目录）

把解耦版 `docker-compose.yml` 放到部署目录（与 `.env` 同目录）：

```bash
# 从源码目录复制 compose 到部署目录
cp /opt/awesome-data/deploy/docker-compose.yml /data/docker/awesome-data/docker-compose.yml
cd /data/docker/awesome-data
ls                  # 应看到 docker-compose.yml 和 .env 两个文件
```

启动前先校验 `.env` 能被 compose 正确加载（**关键自检**）：

```bash
docker compose config | grep -E "DATABASE_URL|LLM_BASE_URL"
# 必须能看到你填的真实值；若只看到 TZ、看不到 DATABASE_URL，说明 env_file 路径不对（见 §8 故障排查）
```

启动：

```bash
docker compose up -d
docker compose ps              # 期望显示 Up (healthy)
docker compose logs -f         # 看启动日志，Ctrl-C 退出查看
```

`entrypoint.sh` 会先 `alembic upgrade head` 跑迁移（连外部 PG），再 `exec supervisord` 守护前后端。

## 6. 健康检查与验证

```bash
# 容器内链路自检
docker exec awesome-data supervisorctl status
#   backend   RUNNING   pid 8 ...
#   frontend  RUNNING   pid 9 ...

docker exec awesome-data curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/health   # 200
docker exec awesome-data curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:3001/          # 200

# 宿主机访问容器对外端口
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:3001/                                   # 200
```

`/health` 是后端根路由（`src/main.py` 定义，不经 `/api/v1` 前缀），健康检查直接打它，最快确认后端与外部 PG 链路就绪。
`3001` 是前端，`/api/*` 由 Next 的 `rewrites` 在容器内转发给后端 `127.0.0.1:8000`。

## 7. 接入宿主 Nginx（对外 80/443）

把 `deploy/nginx.conf` 的 server 片段并入宿主已部署的 Nginx：

```bash
sudo cp /opt/awesome-data/deploy/nginx.conf /etc/nginx/conf.d/awesome-data.conf
# 编辑该文件：设 server_name / listen，按需加 443 + ssl
sudo nginx -t && sudo systemctl reload nginx
```

Nginx 反代到 `127.0.0.1:3001`，`/api/*` 与 `/_next/*` 都走 Next（Next 内部再转发给后端）。
**SSE 关键**：`proxy_buffering off` 已写好，否则报告生成（`chat-stream`）的流式输出会被 Nginx 缓冲，前端收不到增量。
如需更长的流式超时，改 `proxy_read_timeout`（默认 300s）。

## 8. 常用运维命令

```bash
cd /data/docker/awesome-data

docker compose ps                                   # 看状态
docker compose logs -f app                          # 跟随日志
docker compose restart app                          # 重启容器
docker compose down                                 # 停止并删除容器
docker compose up -d                                # 重新拉起

# 容器内进程级控制（不重启整容器）
docker exec awesome-data supervisorctl status
docker exec awesome-data supervisorctl restart backend
docker exec awesome-data supervisorctl restart frontend

# 手动重跑数据库迁移
docker exec awesome-data alembic upgrade head
docker exec awesome-data alembic current            # 看当前版本

# 更新镜像（改了代码后）
cd /opt/awesome-data
docker build -t awesome-data:latest -f deploy/Dockerfile .
cd /data/docker/awesome-data && docker compose up -d   # 用新镜像重建容器
```

## 9. 设计要点与已知限制

- **构建与运行解耦**：compose 只 `image: awesome-data:latest`，不含 `build:`。改 compose 不会触发重建；改代码只 rebuild 镜像。部署机可以没有源码，只要有镜像 + compose + .env。
- **单 worker**：`config_store` 是进程内覆盖，多 worker 会让阈值配置 API 只命中单进程，故 `uvicorn --workers 1`（见 `supervisord.conf`）。多实例横向扩展需先做 Phase 4 持久化。
- **torch CPU 专用**：`pyproject.toml` 配置 `index-strategy = "unsafe-best-match"` + `torch = { index = "pytorch-cpu" }` + `pytorch-cpu` index（`download.pytorch.org/whl/cpu`）。uv 据此在清华源(普通 torch)与 pytorch-cpu index(`+cpu` wheel)间优选 `torch==2.13.0+cpu`，其 metadata **不含 nvidia/cuda 依赖**，故镜像不会拉数百 MB 的 nvidia-* 包（无 GPU 服务器友好）。改 torch 版本后需在开发机 `uv lock` 重新生成 `uv.lock` 并提交。
- **runtime 镜像含 Node 运行时**：单镜像要同时跑 Python 后端 + Node 前端，但 runtime base 是 `python:3.11-slim`，故 Dockerfile 用 `COPY --from=frontend-build` 把 `node/npm/npx` 二进制 + `/usr/local/lib/node_modules` 拷进 runtime。
- **前端用绝对路径启动**：`supervisord.conf` 里 frontend 命令是 `node /app/frontend-react/node_modules/next/dist/bin/next start -H 0.0.0.0 -p 3001`，而非 `npx next start`。原因：① runtime 只拷了 node 二进制，npx 的 npm 模块路径不全；② supervisord 不走 shell、不解析软链，相对路径/软链会 `can't find command`。绝对路径 + 显式 `node` 最稳。
- **无 Redis 容器**：代码不强依赖 redis。若需用 redis，复用宿主已有的 redis 容器，在 `.env` 配 `REDIS_URL` 指向宿主，不必在 compose 里新起。
- **前端构建策略**：保留 `node_modules` 跑 `next start`，未改 `next.config.js` 走 standalone，以最小侵入保证 `transpilePackages` 等 antd 配置不变、`rewrites` 正常工作。镜像因此偏大（可接受）。
- **Next rewrites 是运行时能力**：`next.config.js` 的 `rewrites` 把 `/api/:path*` 在 Next 服务端转发到 `BACKEND_URL`，故前端必须以 `next start`(Node 服务器)形态运行，不能改成静态导出(`output: 'export'`)——否则 rewrites 失效。这也是单镜像需要 supervisord 同跑两进程的根因。

## 10. 故障排查

| 现象 | 根因 / 排查 |
|---|---|
| `docker compose config` 看不到 `DATABASE_URL` | `.env` 与 `docker-compose.yml` 不在同目录；或 `env_file` 路径写错（应为 `./.env`）。compose 的 `env_file` 相对路径是**相对 compose 文件所在目录**解析 |
| 容器反复 `Restarting (1)`，日志 `connection to server at "localhost" port 5432 ... Connection refused` | `DATABASE_URL` 没注入容器，alembic 回退到 `alembic.ini` 的默认 `localhost:5432`。按上一行排查 env_file |
| 容器反复 `Restarting (1)`，日志 `relation "sys_workspace" already exists` | 外部 PG 已有历史表但 `alembic_version` 表未记录版本，`upgrade head` 从头建表冲突。解法：对**已有数据的库**执行 `alembic stamp head`（只写版本号、不建表），之后再 `up -d`。命令见下方 |
| `spawnerr: can't find command 'npx'` 或 `can't find command './node_modules/.bin/next'` | runtime 镜像缺 node 运行时，或 supervisord command 用了软链/相对路径。确认 Dockerfile 有 `COPY --from=frontend-build /usr/local/bin/node ...`，且 supervisord.conf frontend 命令是 `node /app/frontend-react/node_modules/next/dist/bin/next start ...` |
| `Error: Cannot find module '../lib/cli.js'` | npx 的 npm 模块未完整拷入。不要用 npx，改用上面的绝对路径 + node 直接调 next |
| `alembic upgrade` 卡住/超时 | 检查 `.env` 的 `DATABASE_URL` 是否可从容器内访问；容器内 `docker exec awesome-data python -c "import psycopg2;print(psycopg2.connect('...'))"` 验证 |
| 前端 `/api/...` 404 | 确认 `BACKEND_URL=http://127.0.0.1:8000/api` 已由 supervisord 注入前端进程（`docker exec awesome-data supervisorctl status` 看进程在跑；`next.config.js` rewrites 在 `BACKEND_URL` 为空时返回空，故必须有该环境变量） |
| 报告生成不流式 | Nginx 必须 `proxy_buffering off`（见 §7） |
| 后端起不来 | `docker compose logs app` 看后端 stderr；多因连不上外部 PG 或迁移失败 |
| bcrypt/numpy/torch 安装失败 | 确认 `uv.lock` 与 pyproject 同步（开发机 `uv lock`）；torch 应为 `+cpu` 变体（`grep -c nvidia uv.lock` 应为 0） |

### 已有数据的库做 alembic 对齐（`stamp head`）

仅当外部 PG **已有历史表**、但 `alembic_version` 表不存在或版本落后时使用。**只标记版本号，不建表、不改数据**：

```bash
# 用一次性容器对外部 PG 执行 stamp（不依赖正在运行的 app 容器）
docker run --rm \
  -e DATABASE_URL="$(grep ^DATABASE_URL= /data/docker/awesome-data/.env | cut -d= -f2-)" \
  -v /opt/awesome-data/alembic:/app/alembic:ro \
  -v /opt/awesome-data/alembic.ini:/app/alembic.ini:ro \
  -w /app --entrypoint bash awesome-data:latest \
  -c "alembic stamp head"

# 验证版本已记录
docker run --rm \
  -e DATABASE_URL="$(grep ^DATABASE_URL= /data/docker/awesome-data/.env | cut -d= -f2-)" \
  --entrypoint python awesome-data:latest -c \
  "import os,psycopg2;c=psycopg2.connect(os.getenv('DATABASE_URL'));cur=c.cursor();cur.execute('select version_num from alembic_version');print(cur.fetchall())"
```

完成后 `docker compose up -d`，entrypoint 的 `alembic upgrade head` 会变为 no-op，容器正常启动。

## 11. 更新部署（代码变更后的完整流程）

```bash
# 1) 在源码目录重建镜像
cd /opt/awesome-data
git pull                       # 若用 git 同步代码
docker build -t awesome-data:latest -f deploy/Dockerfile .

# 2) 在部署目录用新镜像重建容器
cd /data/docker/awesome-data
docker compose up -d            # compose 发现镜像变化会重建容器
docker compose logs -f app      # 确认 healthy
```

若改了 `deploy/docker-compose.yml` 本身，把它同步到部署目录后再 `up -d`。
