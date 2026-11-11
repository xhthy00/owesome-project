"""Central router aggregation."""

from audit.api.audit import router as audit_router
from chat.api.chat import router as chat_router
from datasource.api.datasource import router as datasource_router
from datasource.api.permission import router as ds_permission_router
from src.agent.education.api import router as education_router
from src.agent.education.exam_library_api import router as exam_library_router
from src.agent.education.knowledge_api import router as knowledge_router
from src.agent.education.school_api import router as school_router
from src.agent.education.score_library_api import router as score_library_router
from system.api.docs import router as system_docs_router
from system.api.edu_permission import router as edu_permission_router
from system.api.edu_permission_v2 import router as edu_permission_v2_router
from system.api.permission import router as permission_router
from system.api.system import router as system_router
from system.api.user import router as user_router
from system.api.workspace import router as workspace_router


def get_all_routers() -> list:
    """Get all API routers."""
    return [
        system_router,
        system_docs_router,
        user_router,
        permission_router,
        edu_permission_router,
        edu_permission_v2_router,
        workspace_router,
        datasource_router,
        ds_permission_router,
        chat_router,
        education_router,
        exam_library_router,
        score_library_router,
        knowledge_router,
        school_router,
        audit_router,
    ]


def register_routers(app) -> None:
    """Register all routers to the FastAPI app."""
    for router in get_all_routers():
        app.include_router(router, prefix="/api/v1")
