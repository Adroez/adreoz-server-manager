"""Authentication endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.api.cookies import clear_session_cookie, set_session_cookie
from app.api.deps import (
    AuthContext,
    enforce_rate_limit,
    get_current_auth,
    get_db,
    rate_limit_key,
    require_csrf,
)
from app.schemas.auth import AuthResponse, ChangePasswordRequest, LoginRequest
from app.schemas.common import MessageResponse
from app.schemas.user import UserOut
from app.services.audit import record_audit, request_meta
from app.services.auth import (
    authenticate,
    change_own_password,
    create_session,
    get_session_by_token,
    revoke_other_sessions,
    revoke_session,
)

router = APIRouter(prefix="/auth", tags=["authentication"])


def _auth_response(user, auth_session) -> AuthResponse:
    return AuthResponse(
        user=UserOut.model_validate(user),
        csrf_token=auth_session.csrf_token,
        expires_at=auth_session.expires_at,
    )


@router.post("/login", response_model=AuthResponse)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
) -> AuthResponse:
    settings = request.app.state.settings
    enforce_rate_limit(request, "login")
    meta = request_meta(request)

    user = authenticate(db, payload.username, payload.password)
    if user is None:
        record_audit(
            db,
            action="auth.login_failed",
            actor_username=payload.username[:32],
            detail="Invalid credentials or disabled account.",
            meta=meta,
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password.",
        )

    request.app.state.login_limiter.reset(rate_limit_key(request, "login"))
    user.touch_login()

    # Rotate away from any session already presented with this request. This
    # guarantees a login never reuses a pre-authentication token.
    previous = request.cookies.get(settings.session_cookie_name)
    if previous:
        existing = get_session_by_token(db, previous)
        if existing is not None:
            db.delete(existing)

    auth_session, raw_token = create_session(db, user, settings, meta=meta)
    record_audit(
        db,
        action="auth.login_succeeded",
        actor=user,
        detail="Signed in.",
        meta=meta,
    )
    db.commit()

    set_session_cookie(
        response,
        settings=settings,
        token=raw_token,
        expires_at=auth_session.expires_at,
    )
    return _auth_response(user, auth_session)


@router.get("/me", response_model=AuthResponse)
def me(auth: Annotated[AuthContext, Depends(get_current_auth)]) -> AuthResponse:
    return _auth_response(auth.user, auth.session)


@router.post("/logout", response_model=MessageResponse)
def logout(
    auth: Annotated[AuthContext, Depends(require_csrf)],
    request: Request,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
) -> MessageResponse:
    settings = request.app.state.settings
    record_audit(
        db,
        action="auth.logout",
        actor=auth.user,
        detail="Signed out.",
        meta=auth.meta,
    )
    revoke_session(db, auth.session)
    db.commit()
    clear_session_cookie(response, settings=settings)
    return MessageResponse(message="Signed out.")


@router.post("/password", response_model=AuthResponse)
def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    response: Response,
    auth: Annotated[AuthContext, Depends(require_csrf)],
    db: Annotated[Session, Depends(get_db)],
) -> AuthResponse:
    """Change the caller's password, revoking every old session.

    A fresh session is issued so the current browser stays signed in with new
    credentials and a new CSRF token.
    """

    settings = request.app.state.settings
    change_own_password(
        db,
        auth.user,
        current_password=payload.current_password,
        new_password=payload.new_password,
        meta=auth.meta,
    )
    new_session, raw_token = create_session(
        db, auth.user, settings, meta=auth.meta
    )
    db.commit()
    set_session_cookie(
        response,
        settings=settings,
        token=raw_token,
        expires_at=new_session.expires_at,
    )
    return _auth_response(auth.user, new_session)


@router.delete("/sessions", response_model=MessageResponse)
def revoke_other_sessions_endpoint(
    auth: Annotated[AuthContext, Depends(require_csrf)],
    db: Annotated[Session, Depends(get_db)],
) -> MessageResponse:
    revoked = revoke_other_sessions(db, auth.user, auth.session.id)
    record_audit(
        db,
        action="auth.other_sessions_revoked",
        actor=auth.user,
        detail=f"Revoked {revoked} other session(s).",
        meta=auth.meta,
    )
    db.commit()
    if revoked == 1:
        return MessageResponse(message="Signed out 1 other session.")
    return MessageResponse(message=f"Signed out {revoked} other sessions.")
