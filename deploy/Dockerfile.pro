FROM swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/python:3.11-slim-bookworm AS backend-build

# psycopg[lawn]/bcrypt/oracledb 等可能需要构建工具与 libpq。apt 走华为云镜像。
RUN if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
      sed -i 's|deb.debian.org|mirrors.huaweicloud.com|g; s|security.debian.org|mirrors.huaweicloud.com|g' /etc/apt/sources.list.d/debian.sources; \
    fi \
 && if [ -f /etc/apt/sources.list ]; then \
      sed -i 's|deb.debian.org|mirrors.huaweicloud.com|g; s|security.debian.org|mirrors.huaweicloud.com|g' /etc/apt/sources.list; \
    fi \
 && apt-get update && apt-get install -y --no-install-recommends \
        build-essential libpq-dev curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 安装 uv。清华 PyPI 在这台机器上返回空索引，改走华为云。
RUN pip install --no-cache-dir uv --index-url https://mirrors.huaweicloud.com/repository/pypi/simple --trusted-host mirrors.huaweicloud.com

WORKDIR /app
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_PYTHON_DOWNLOADS=never \
    UV_DEFAULT_INDEX=https://mirrors.huaweicloud.com/repository/pypi/simple \
    UV_INDEX_URL=https://mirrors.huaweicloud.com/repository/pypi/simple

# 先拷依赖描述以利用缓存
COPY pyproject.toml uv.lock ./
# 仅装生产依赖（--no-dev），按锁文件精确安装
RUN uv sync --frozen --no-dev --no-install-project

# 拷后端源码并安装本包
COPY src/ ./src/
COPY config/ ./config/
RUN uv sync --frozen --no-dev

# 拷 alembic 迁移脚本到运行时（构建阶段不需要）
# 注意：目标必须是 ./alembic.ini（文件），不能写成 alembic/（目录），
# 否则文件会落到 alembic/alembic.ini，runtime 阶段 COPY --from=backend-build /app/alembic.ini 会找不到。
COPY alembic.ini ./alembic.ini
COPY alembic ./alembic

FROM swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/python:3.11-slim-bookworm AS runtime

# libpq：psycopg 运行依赖。curl：健康检查。apt 走华为云镜像。
RUN if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
      sed -i 's|deb.debian.org|mirrors.huaweicloud.com|g; s|security.debian.org|mirrors.huaweicloud.com|g' /etc/apt/sources.list.d/debian.sources; \
    fi \
 && if [ -f /etc/apt/sources.list ]; then \
      sed -i 's|deb.debian.org|mirrors.huaweicloud.com|g; s|security.debian.org|mirrors.huaweicloud.com|g' /etc/apt/sources.list; \
    fi \
 && apt-get update && apt-get install -y --no-install-recommends \
        libpq5 curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

ENV UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
COPY --from=backend-build /app/.venv /app/.venv
COPY --from=backend-build /app/src ./src
COPY --from=backend-build /app/config ./config
COPY --from=backend-build /app/alembic.ini ./alembic.ini
COPY --from=backend-build /app/alembic ./alembic

COPY deploy/entrypoint.pro.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# 监听 0.0.0.0，宿主机才能把 127.0.0.1:8000 映射进来给 Nginx 反代。
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/health >/dev/null || exit 1

ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]