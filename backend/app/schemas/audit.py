"""Audit log schemas."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from app.core.clock import UtcDateTime


class AuditLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    action: str
    actor_username: str | None
    detail: str | None
    ip_address: str | None
    created_at: UtcDateTime
