"""First-run administrator setup."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.api.cookies import set_session_cookie
from app.api.deps import enforce_rate_limit, get_db, rate_limit_key
from app.schemas.auth import AuthResponse, SetupRequest, SetupStatus
from app.schemas.user import UserOut
from app.services.audit import request_meta
from app.services.auth import create_session
from app.services.users import create_initial_admin, is_setup_complete

router = APIRouter(tags=["setup"])


@router.get("/setup/status", response_model=SetupStatus)
def setup_status(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> SetupStatus:
    settings = request.app.state.settings
    return SetupStatus(
        setup_required=not is_setup_complete(db),
        app_name=settings.app_name,
        version=settings.version,
    )


@router.post("/setup", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
def complete_setup(
    payload: SetupRequest,
    request: Request,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
) -> AuthResponse:
    settings = request.app.state.settings
    enforce_rate_limit(request, "setup")
    meta = request_meta(request)

    if is_setup_complete(db):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Setup has already been completed.",
        )

    user = create_initial_admin(
        db,
        username=payload.username,
        email=str(payload.email) if payload.email else None,
        password=payload.password,
        meta=meta,
    )
    user.touch_login()
    auth_session, raw_token = create_session(db, user, settings, meta=meta)
    db.commit()

    request.app.state.login_limiter.reset(rate_limit_key(request, "setup"))
    set_session_cookie(
        response,
        settings=settings,
        token=raw_token,
        expires_at=auth_session.expires_at,
    )
    return AuthResponse(
        user=UserOut.model_validate(user),
        csrf_token=auth_session.csrf_token,
        expires_at=auth_session.expires_at,
    )
