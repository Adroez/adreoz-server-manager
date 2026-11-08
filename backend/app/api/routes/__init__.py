"""FastAPI routers."""

from app.api.routes import auth, dashboard, health, servers, settings, setup, users

__all__ = ["auth", "dashboard", "health", "servers", "settings", "setup", "users"]
