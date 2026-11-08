"""Audit trail and request metadata helpers."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request
from sqlalchemy.orm import Session

from app.models.audit import AuditLog
from app.models.user import User


@dataclass(frozen=True)
class RequestMeta:
    ip_address: str | None
    user_agent: str | None


def request_meta(request: Request) -> RequestMeta:
    """Extract client metadata, only trusting forwarding headers when enabled."""

    settings = getattr(request.app.state, "settings", None)
    ip_address: str | None = None
    if settings is not None and settings.trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            ip_address = forwarded.split(",", 1)[0].strip()
    if not ip_address and request.client is not None:
        ip_address = request.client.host
    user_agent = request.headers.get("user-agent")
    return RequestMeta(
        ip_address=ip_address[:45] if ip_address else None,
        user_agent=user_agent[:255] if user_agent else None,
    )


def record_audit(
    db: Session,
    *,
    action: str,
    actor: User | None = None,
    actor_username: str | None = None,
    detail: str | None = None,
    meta: RequestMeta | None = None,
) -> AuditLog:
    """Add an audit row to the current unit of work.

    The caller commits together with the action being audited so a failure
    cannot silently produce a success without a trail.
    """

    entry = AuditLog(
        actor_user_id=actor.id if actor is not None else None,
        actor_username=(actor.username if actor is not None else actor_username),
        action=action[:64],
        detail=detail[:2000] if detail else None,
        ip_address=meta.ip_address if meta else None,
        user_agent=meta.user_agent if meta else None,
    )
    db.add(entry)
    return entry
