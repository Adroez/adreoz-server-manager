"""Request dependencies shared by API routes."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.core.config import Settings
from app.core.security import constant_time_equals
from app.models.auth_session import AuthSession
from app.models.user import User
from app.services.audit import RequestMeta, request_meta
from app.services.auth import get_session_by_token


def get_db(request: Request) -> Iterator[Session]:
    session_factory = request.app.state.session_factory
    db: Session = session_factory()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_session_factory(request: Request):
    """Return the application's single session factory.

    Services that need their own sessions (background work, per-line log
    writes) must use this instead of constructing an ad-hoc ``sessionmaker``,
    so every session shares the same engine settings (autoflush disabled,
    ``expire_on_commit=False``) and connection pool.
    """

    return request.app.state.session_factory


@dataclass(frozen=True)
class AuthContext:
    user: User
    session: AuthSession
    meta: RequestMeta


def _unauthorized(message: str = "Authentication required.") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=message,
    )


def get_current_auth(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AuthContext:
    raw_token = request.cookies.get(settings.session_cookie_name)
    if not raw_token:
        raise _unauthorized()

    auth_session = get_session_by_token(db, raw_token)
    if auth_session is None:
        raise _unauthorized("Your session is no longer valid. Please sign in again.")

    now = utcnow()
    if auth_session.is_expired(now):
        db.delete(auth_session)
        db.commit()
        raise _unauthorized("Your session has expired. Please sign in again.")

    user = auth_session.user
    if not user.is_active:
        db.delete(auth_session)
        db.commit()
        raise _unauthorized("This account is disabled.")

    # Avoid a write on every request while keeping activity reasonably fresh.
    if (now - auth_session.last_seen_at).total_seconds() > 60:
        auth_session.last_seen_at = now
        db.commit()

    return AuthContext(user=user, session=auth_session, meta=request_meta(request))


def require_csrf(
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    x_csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> AuthContext:
    if not x_csrf_token or not constant_time_equals(
        x_csrf_token, auth.session.csrf_token
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or missing CSRF token.",
        )
    return auth


def require_admin(
    auth: Annotated[AuthContext, Depends(get_current_auth)],
) -> AuthContext:
    if not auth.user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator access is required.",
        )
    return auth


def require_admin_write(
    admin: Annotated[AuthContext, Depends(require_admin)],
    _csrf: Annotated[AuthContext, Depends(require_csrf)],
) -> AuthContext:
    """Administrator authorization plus CSRF validation."""

    return admin


def rate_limit_key(request: Request, bucket: str) -> str:
    meta = request_meta(request)
    return f"{bucket}:{meta.ip_address or 'unknown'}"


def enforce_rate_limit(request: Request, bucket: str) -> None:
    limiter = request.app.state.login_limiter
    allowed, retry_after = limiter.hit(rate_limit_key(request, bucket))
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many attempts. Please wait before trying again.",
            headers={"Retry-After": str(retry_after)},
        )
