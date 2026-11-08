"""FastAPI routers."""

from app.api.routes import (
    auth,
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
    "consoles",
    "dashboard",
    "files",
    "health",
    "servers",
    "settings",
    "setup",
    "users",
]
