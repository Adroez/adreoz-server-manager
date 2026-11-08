"""Authentication and session lifecycle.

Sessions are server-side records. The browser only receives an opaque random
token whose SHA-256 digest is stored in the database. Every state-changing
request additionally requires the session's CSRF token in a custom header.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, joinedload

from app.core.clock import utcnow
from app.core.config import Settings
from app.core.security import (
    generate_csrf_token,
    generate_session_token,
    hash_password,
    hash_session_token,
    validate_password,
    verify_dummy_password,
    verify_password,
)
from app.models.auth_session import AuthSession
from app.models.user import User
from app.services.audit import RequestMeta, record_audit
from app.services.errors import DomainError
from app.services.settings import get_session_lifetime_hours


def find_user_for_login(db: Session, username: str) -> User | None:
    from app.core.validation import normalize_username

    return db.scalar(
        select(User).where(User.username_normalized == normalize_username(username))
    )


def authenticate(db: Session, username: str, password: str) -> User | None:
    user = find_user_for_login(db, username)
    if user is None:
        # Equalize roughly the cost of a failed lookup with a real hash check.
        verify_dummy_password(password)
        return None
    if not verify_password(password, user.password_hash):
        return None
    if not user.is_active:
        return None
    return user


def session_lifetime(db: Session, settings: Settings) -> timedelta:
    return timedelta(
        hours=get_session_lifetime_hours(db, settings.session_lifetime_hours)
    )


def create_session(
    db: Session,
    user: User,
    settings: Settings,
    *,
    meta: RequestMeta | None = None,
) -> tuple[AuthSession, str]:
    raw_token = generate_session_token()
    now = utcnow()
    auth_session = AuthSession(
        user_id=user.id,
        token_hash=hash_session_token(raw_token),
        csrf_token=generate_csrf_token(),
        created_at=now,
        expires_at=now + session_lifetime(db, settings),
        last_seen_at=now,
        ip_address=meta.ip_address if meta else None,
        user_agent=meta.user_agent if meta else None,
    )
    db.add(auth_session)
    return auth_session, raw_token


def get_session_by_token(db: Session, raw_token: str) -> AuthSession | None:
    return db.scalar(
        select(AuthSession)
        .options(joinedload(AuthSession.user))
        .where(AuthSession.token_hash == hash_session_token(raw_token))
    )


def revoke_session(db: Session, auth_session: AuthSession) -> None:
    db.delete(auth_session)


def revoke_other_sessions(db: Session, user: User, keep_session_id: int) -> int:
    result = db.execute(
        delete(AuthSession).where(
            AuthSession.user_id == user.id,
            AuthSession.id != keep_session_id,
        )
    )
    return int(result.rowcount or 0)


def revoke_all_sessions(db: Session, user: User) -> int:
    result = db.execute(delete(AuthSession).where(AuthSession.user_id == user.id))
    return int(result.rowcount or 0)


def count_active_sessions(db: Session, user: User) -> int:
    return int(
        db.scalar(
            select(func.count(AuthSession.id)).where(
                AuthSession.user_id == user.id,
                AuthSession.expires_at > utcnow(),
            )
        )
        or 0
    )


def purge_expired_sessions(db: Session) -> int:
    result = db.execute(delete(AuthSession).where(AuthSession.expires_at <= utcnow()))
    db.commit()
    return int(result.rowcount or 0)


def change_own_password(
    db: Session,
    user: User,
    *,
    current_password: str,
    new_password: str,
    meta: RequestMeta | None = None,
) -> None:
    """Change a password and invalidate every existing session.

    The caller must rotate the current browser session afterwards.
    """

    if not verify_password(current_password, user.password_hash):
        raise DomainError("Current password is incorrect.")
    if current_password == new_password:
        raise DomainError("The new password must be different from the current one.")
    validate_password(new_password)
    user.password_hash = hash_password(new_password)
    revoke_all_sessions(db, user)
    record_audit(
        db,
        action="auth.password_changed",
        actor=user,
        detail="Password changed; all sessions revoked.",
        meta=meta,
    )
