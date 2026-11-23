#!/bin/sh
set -e

# 后端容器入口：迁移数据库，然后起 uvicorn。
# 环境变量（DATABASE_URL / JWT_* / LLM_* 等）由 docker compose env_file 注入。
# 前端静态文件由 Nginx 直接提供，不从镜像发布。

echo "[entrypoint] running alembic upgrade head against DATABASE_URL…"
cd /app
alembic upgrade head

echo "[entrypoint] starting uvicorn…"
exec "$@"
