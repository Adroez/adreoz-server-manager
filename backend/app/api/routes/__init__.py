"""FastAPI routers."""

from app.api.routes import (
    auth,
    backups,
    consoles,
    dashboard,
    files,
    health,
    java_runtimes,
    minecraft_versions,
    servers,
    settings,
    setup,
    software,
    users,
)

__all__ = [
    "auth",
    "backups",
    "consoles",
    "dashboard",
    "files",
    "health",
    "java_runtimes",
    "minecraft_versions",
    "servers",
    "settings",
    "setup",
    "software",
    "users",
]
