"""FastAPI routers."""

from app.api.routes import (
    auth,
    backups,
    consoles,
    dashboard,
    files,
    health,
    servers,
    settings,
    setup,
    users,
)

__all__ = [
    "auth",
    "backups",
    "consoles",
    "dashboard",
    "files",
    "health",
    "servers",
    "settings",
    "setup",
    "users",
]
