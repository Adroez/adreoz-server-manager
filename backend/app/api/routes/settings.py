"""Administrator application settings endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, get_db, require_admin, require_admin_write
from app.schemas.settings import AppSettingsOut, AppSettingsUpdate
from app.services.audit import record_audit
from app.services.settings import (
    get_session_lifetime_hours,
    set_session_lifetime_hours,
)
from app.services.users import is_setup_complete

router = APIRouter(prefix="/settings", tags=["settings"])


def _settings_payload(request: Request, db: Session) -> AppSettingsOut:
    settings = request.app.state.settings
    return AppSettingsOut(
        session_lifetime_hours=get_session_lifetime_hours(
            db, settings.session_lifetime_hours
        ),
        version=settings.version,
        environment=settings.environment,
        database_backend=settings.database_uri.split(":", 1)[0],
        cookie_secure=settings.cookie_secure,
        setup_complete=is_setup_complete(db),
    )


@router.get("", response_model=AppSettingsOut)
def read_settings(
    request: Request,
    admin: Annotated[AuthContext, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> AppSettingsOut:
    return _settings_payload(request, db)


@router.patch("", response_model=AppSettingsOut)
def update_settings(
    payload: AppSettingsUpdate,
    request: Request,
    admin: Annotated[AuthContext, Depends(require_admin_write)],
    db: Annotated[Session, Depends(get_db)],
) -> AppSettingsOut:
    previous = get_session_lifetime_hours(db, request.app.state.settings.session_lifetime_hours)
    updated = set_session_lifetime_hours(db, payload.session_lifetime_hours)
    record_audit(
        db,
        action="settings.updated",
        actor=admin.user,
        detail=f"session_lifetime_hours: {previous} -> {updated}",
        meta=admin.meta,
    )
    db.commit()
    return _settings_payload(request, db)
