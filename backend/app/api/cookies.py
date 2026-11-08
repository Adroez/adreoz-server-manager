"""Session cookie helpers."""

from __future__ import annotations

from datetime import datetime

from fastapi import Response

from app.core.clock import utcnow
from app.core.config import Settings


def set_session_cookie(
    response: Response,
    *,
    settings: Settings,
    token: str,
    expires_at: datetime,
) -> None:
    max_age = max(1, int((expires_at - utcnow()).total_seconds()))
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        max_age=max_age,
        path="/",
        secure=settings.cookie_secure,
        httponly=True,
        samesite=settings.cookie_samesite,
    )


def clear_session_cookie(response: Response, *, settings: Settings) -> None:
    response.delete_cookie(
        key=settings.session_cookie_name,
        path="/",
        secure=settings.cookie_secure,
        httponly=True,
        samesite=settings.cookie_samesite,
    )
