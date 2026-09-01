"""FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from agent.resource.manager import install_default_resources
from common.core.config import get_settings
from common.core.database import init_db
from common.core.trace import install_trace_log_factory, new_trace_id, trace_scope
from common.middlewares.exception import register_exception_handlers
from common.router import register_routers

settings = get_settings()


def _configure_logging() -> None:
    """进程级日志配置：安装 trace_id LogRecord factory + 统一 format。

    放在 ``lifespan`` 之外（模块导入时执行）是为了让 ``init_db`` / router 注册
    等 lifespan 之前的日志也能带 trace_id（虽然此时多半是 "-"）。
    """
    install_trace_log_factory()
    fmt = "%(asctime)s %(levelname)s [%(trace_id)s] %(name)s: %(message)s"
    # force=True：覆盖 Python 默认的 "lastResort" handler，避免重复输出。
    # 只动 root——uvicorn 自己的 "uvicorn.access" 等 logger 有自己的 handler
    # 不会被我们覆盖，只是新增的 trace_id 属性对它们可见（不用不影响）。
    logging.basicConfig(level=logging.INFO, format=fmt, force=True)


_configure_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup/shutdown events."""
    init_db()
    # 幂等注册 Agent ToolPack 模板。放在 lifespan 里有两点好处：
    # (1) 测试场景可以 import main 而不触发工具注册，方便单独测 ResourceManager；
    # (2) 万一将来注册涉及 I/O（比如从配置加载自定义 pack），也不会阻塞模块导入。
    install_default_resources()
    yield


app = FastAPI(
    title=settings.app_name,
    debug=settings.debug,
    version="0.1.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def trace_id_middleware(request: Request, call_next):
    """每个 HTTP 请求进入时生成/绑定 trace_id，供日志与审计使用。

    trace_scope 通过 contextvars 传播；FastAPI 的 async 端点以及
    asyncio.to_thread 创建的后台线程都会自动继承该上下文。
    """
    with trace_scope(new_trace_id()) as tid:
        response = await call_next(request)
        # 把当前请求的 trace_id 暴露给前端，便于问题排查
        response.headers["x-trace-id"] = tid
        return response


# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["x-trace-id", "Content-Disposition"],
)

# Register exception handlers
register_exception_handlers(app)

# Register routers
register_routers(app)


@app.get("/health")
def health_check():
    """Health check endpoint."""
    return {"code": 200, "message": "ok", "data": {"status": "ok"}}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
