from app.api.v1.routers.analytics import router as analytics_router
from app.api.v1.routers.auth import router as auth_router
from app.api.v1.routers.dashboards import router as dashboards_router
from app.api.v1.routers.integrations import router as integrations_router
from app.api.v1.routers.organization import router as organization_router
from app.api.v1.routers.reports import router as reports_router
from app.api.v1.routers.scripts import router as scripts_router
from app.api.v1.routers.spreadsheets import router as spreadsheets_router
from app.api.v1.routers.sync import router as sync_router

__all__ = [
    "analytics_router",
    "auth_router",
    "dashboards_router",
    "integrations_router",
    "organization_router",
    "reports_router",
    "scripts_router",
    "spreadsheets_router",
    "sync_router",
]
