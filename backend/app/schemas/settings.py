"""Application settings schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field


class AppSettingsOut(BaseModel):
    session_lifetime_hours: int
    version: str
    environment: str
    database_backend: str
    cookie_secure: bool
    setup_complete: bool


class AppSettingsUpdate(BaseModel):
    session_lifetime_hours: int = Field(ge=1, le=720)
