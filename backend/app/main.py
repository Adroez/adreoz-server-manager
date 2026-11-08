"""Adreoz Server Manager application factory."""

from __future__ import annotations

import logging
import os
import time
from contextlib import asynccontextmanager

import psutil
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import inspect
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.routes import auth, dashboard, health, servers, setup, users
from app.api.routes import settings as settings_routes
from app.core.config import Settings, get_settings
from app.core.rate_limit import SlidingWindowLimiter
from app.core.process import ProcessManager
from app.database.session import (
    build_engine,
    build_session_factory,
    database_is_reachable,
)
from app.middleware.security import SecurityHeadersMiddleware
from app.services.auth import purge_expired_sessions
from app.services.errors import DomainError

logger = logging.getLogger("asm")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    settings.ensure_directories()

    if settings.environment == "production" and not settings.cookie_secure:
        logger.warning(
            "ASM_ENVIRONMENT=production but ASM_COOKIE_SECURE is disabled. "
            "Set ASM_COOKIE_SECURE=true when serving the panel over HTTPS."
        )

    engine = build_engine(settings)
    session_factory = build_session_factory(engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if hasattr(os, "geteuid") and os.geteuid() == 0 and not settings.allow_root:
            raise RuntimeError(
                "Adreoz Server Manager must not run as root. Set ASM_ALLOW_ROOT=true "
                "only if you understand the risk."
            )

        # Prime psutil so the first dashboard sample is not always zero.
        psutil.cpu_percent(interval=None)
        app.state.panel_started_monotonic = time.monotonic()

        if not database_is_reachable(engine):
            logger.error("Database is not reachable at %s", settings.database_uri)
        elif not inspect(engine).has_table("users"):
            settings.secure_database_permissions()
            logger.error(
                "Database schema is missing. Run 'alembic upgrade head' from backend/."
            )
        else:
            settings.secure_database_permissions()
            db = session_factory()
            try:
                purged = purge_expired_sessions(db)
                if purged:
                    logger.info("Purged %s expired session(s).", purged)
            except Exception:  # pragma: no cover - startup resilience
                db.rollback()
                logger.exception("Failed to purge expired sessions.")
            finally:
                db.close()

        yield
        # Gracefully stop all server processes on shutdown.
        try:
            await app.state.process_manager.stop_all(timeout=15)
        except Exception:  # pragma: no cover - shutdown resilience
            logger.exception("Failed to stop server processes during shutdown.")
        engine.dispose()

    app = FastAPI(
        title=settings.app_name,
        version=settings.version,
        docs_url="/docs" if settings.environment == "development" else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.environment == "development" else None,
        lifespan=lifespan,
    )

    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.panel_started_monotonic = time.monotonic()
    app.state.login_limiter = SlidingWindowLimiter(
        settings.login_attempt_limit, settings.login_attempt_window_seconds
    )
    app.state.process_manager = ProcessManager()

    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=settings.allowed_hosts,
    )
    app.add_middleware(SecurityHeadersMiddleware, cookie_secure=settings.cookie_secure)

    @app.exception_handler(DomainError)
    async def domain_error_handler(_request: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.message},
            headers={"Cache-Control": "no-store"},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # Never echo request input back; it can contain passwords.
        safe_errors = [
            {
                "loc": list(error.get("loc", ())),
                "msg": error.get("msg", "Invalid value."),
                "type": error.get("type", "value_error"),
            }
            for error in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content={"detail": safe_errors},
            headers={"Cache-Control": "no-store"},
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"detail": "An unexpected error occurred."},
            headers={"Cache-Control": "no-store"},
        )

    api_prefix = "/api"
    app.include_router(health.router, prefix=api_prefix)
    app.include_router(setup.router, prefix=api_prefix)
    app.include_router(auth.router, prefix=api_prefix)
    app.include_router(dashboard.router, prefix=api_prefix)
    app.include_router(users.router, prefix=api_prefix)
    app.include_router(settings_routes.router, prefix=api_prefix)
    app.include_router(servers.router, prefix=api_prefix)

    if settings.frontend_dir.is_dir():
        app.mount(
            "/",
            StaticFiles(directory=str(settings.frontend_dir), html=True),
            name="frontend",
        )
    else:  # pragma: no cover - misconfiguration
        logger.error("Frontend directory not found: %s", settings.frontend_dir)

        @app.get("/", include_in_schema=False)
        def missing_frontend() -> JSONResponse:
            return JSONResponse(
                status_code=503,
                content={"detail": "Frontend assets are not installed."},
            )

    return app


app = create_app()
