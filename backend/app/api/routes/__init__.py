"""FastAPI routers."""

from app.api.routes import auth, dashboard, health, settings, setup, users

__all__ = ["auth", "dashboard", "health", "settings", "setup", "users"]
