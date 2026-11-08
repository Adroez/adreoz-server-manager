"""FastAPI routers."""

from app.api.routes import (
    auth,
    consoles,
    dashboard,
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
    "health",
    "servers",
    "settings",
    "setup",
    "users",
]
