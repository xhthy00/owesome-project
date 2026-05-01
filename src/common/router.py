"""Central router aggregation."""

from system.api.system import router as system_router
from system.api.permission import router as permission_router
from system.api.workspace import router as workspace_router
from system.api.user import router as user_router
from datasource.api.datasource import router as datasource_router
from datasource.api.permission import router as ds_permission_router
from chat.api.chat import router as chat_router


def get_all_routers() -> list:
    """Get all API routers."""
    return [
        system_router,
        user_router,
        permission_router,
        workspace_router,
        datasource_router,
        ds_permission_router,
        chat_router,
    ]


def register_routers(app) -> None:
    """Register all routers to the FastAPI app."""
    for router in get_all_routers():
        app.include_router(router, prefix="/api/v1")
