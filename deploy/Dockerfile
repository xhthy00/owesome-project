# 单镜像：后端(uvicorn) + 前端(Next.js) 由 supervisord 一并守护。
# base 镜像需含 Python3.11 + Node20 + supervisord。多阶段合并到一个最终镜像。

# ---------- stage 1: 前端构建 ----------
FROM swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/library/node:20-bookworm-slim AS frontend-build

WORKDIR /build-fe
# 配置 npm 淘宝镜像源
RUN npm config set registry https://registry.npmmirror.com

# ⚠️ rewrites() 在 next build 时求值并固化进 routes-manifest.json，运行时 next start 不会重新求值。
# 故必须在构建阶段就给出 BACKEND_URL，否则 rewrites 被烤成空 []，/api 不会被转发。
# 值用容器内回环：Next 与后端同容器，转发到 127.0.0.1:8000 即后端。
ENV BACKEND_URL=http://127.0.0.1:8000/api
# NEXT_PUBLIC_* 是构建期内联进客户端 bundle 的，运行时（supervisord.conf）设的值对已构建产物无效。
# 显式在构建期注入，使浏览器 fetch 走 /api/v1/* 相对路径（经 Next rewrites 到后端），与后端 /api/v1
# 前缀一致，不再依赖 client.ts 里 ?? "/api/v1" 的隐式兜底。
ENV NEXT_PUBLIC_API_BASE_URL=/api/v1

# 先拷 lock 以利用 npm 缓存
COPY frontend-react/package.json frontend-react/package-lock.json* ./
# 项目用 antv 系列，锁文件存在时用 ci；不存在则 install 兜底
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

# 拷源码并构建（不含 node_modules / .next）
COPY frontend-react/ ./
RUN npm run build


# ---------- stage 2: 依赖层（pip/uv 解析） ----------
FROM swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/python:3.11-slim-bookworm AS backend-build

# psycopg[lawn]/bcrypt/oracledb 等可能需要构建工具与 libpq
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libpq-dev curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 安装 uv（走清华 PyPI 源，避免 GitHub 直链/代理不稳定）
RUN pip install --no-cache-dir uv --index-url https://pypi.tuna.tsinghua.edu.cn/simple

WORKDIR /app
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_PYTHON_DOWNLOADS=never

# 先拷依赖描述以利用缓存
COPY pyproject.toml uv.lock ./
# 仅装生产依赖（--no-dev），按锁文件精确安装
RUN uv sync --frozen --no-dev --no-install-project

# 拷后端源码并安装本包
COPY src/ ./src/
RUN uv sync --frozen --no-dev

# 拷 alembic 迁移脚本到运行时（构建阶段不需要）
# 注意：目标必须是 ./alembic.ini（文件），不能写成 alembic/（目录），
# 否则文件会落到 alembic/alembic.ini，runtime 阶段 COPY --from=backend-build /app/alembic.ini 会找不到。
COPY alembic.ini ./alembic.ini
COPY alembic ./alembic


# ---------- stage 3: 运行时（前端产物 + 后端 venv + 守护进程） ----------
FROM swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/python:3.11-slim-bookworm AS runtime

# 运行期仍需 libpq（psycopg 运行依赖）+ supervisord + curl（健康检查）
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 supervisor curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 运行时还要跑 Next.js（npx next start），但 runtime base 是 python 镜像，没有 Node。
# 从 frontend-build 阶段（node:20）拷 Node 运行时过来：二进制 + npm/corepack 全局模块。
# npx 是软链 → ../lib/node_modules/npm/bin/npx-cli.js，所以 lib/node_modules 必须一起拷。
COPY --from=frontend-build /usr/local/bin/node /usr/local/bin/node
COPY --from=frontend-build /usr/local/bin/npm /usr/local/bin/npm
COPY --from=frontend-build /usr/local/bin/npx /usr/local/bin/npx
COPY --from=frontend-build /usr/local/lib/node_modules /usr/local/lib/node_modules

WORKDIR /app

# 后端：把构建阶段装好的 venv 原样拷过来
ENV UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
COPY --from=backend-build /app/.venv /app/.venv
COPY --from=backend-build /app/src ./src
COPY --from=backend-build /app/alembic.ini ./alembic.ini
COPY --from=backend-build /app/alembic ./alembic

# 前端：Next standalone 产物（用 next start，需 .next + node_modules 已被 tree-shake）
# 这里采用「整个 frontend-react 运行目录」策略，避免 standalone 改 next.config
WORKDIR /app/frontend-react
COPY --from=frontend-build /build-fe/package.json ./package.json
COPY --from=frontend-build /build-fe/node_modules ./node_modules
COPY --from=frontend-build /build-fe/.next ./.next
COPY --from=frontend-build /build-fe/public ./public
COPY frontend-react/next.config.js ./next.config.js
WORKDIR /app

# 守护进程与入口
COPY deploy/supervisord.conf /etc/supervisor/conf.d/app.conf
COPY deploy/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# 仅暴露前端端口；后端 uvicorn 仅监听容器内 127.0.0.1，由 Next 通过 rewrites 转发
EXPOSE 3001

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/health >/dev/null || exit 1

ENTRYPOINT ["/entrypoint.sh"]
CMD ["supervisord", "-n", "-c", "/etc/supervisor/supervisord.conf"]