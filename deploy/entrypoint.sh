#!/bin/sh
set -e

# 容器入口：先对外部 PG 跑 alembic 迁移，再起 supervisord 守护前后端。
# 环境变量（DATABASE_URL / JWT_* / LLM_* 等）由 docker compose env_file 注入。

echo "[entrypoint] running alembic upgrade head against DATABASE_URL…"
cd /app
alembic upgrade head

echo "[entrypoint] starting supervisord (backend + frontend)…"
exec "$@"