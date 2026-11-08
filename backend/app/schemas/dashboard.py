"""Dashboard response schemas built from live host metrics."""

from __future__ import annotations

from pydantic import BaseModel

from app.core.clock import UtcDateTime
from app.schemas.audit import AuditLogOut
from app.schemas.user import UserOut


class MemoryInfo(BaseModel):
    total_bytes: int
    used_bytes: int
    available_bytes: int
    percent: float


class DiskInfo(BaseModel):
    total_bytes: int
    used_bytes: int
    free_bytes: int
    percent: float


class SystemInfo(BaseModel):
    hostname: str
    platform: str
    cpu_count: int
    cpu_percent: float
    load_1: float | None
    load_5: float | None
    load_15: float | None
    memory: MemoryInfo
    disk: DiskInfo
    host_uptime_seconds: int
    panel_uptime_seconds: int


class AccountInfo(BaseModel):
    active_sessions: int
    session_expires_at: UtcDateTime
    last_login_at: UtcDateTime | None
    created_at: UtcDateTime


class PlatformInfo(BaseModel):
    total_users: int
    active_users: int
    admins: int
    recent_activity: list[AuditLogOut]


class DashboardSummary(BaseModel):
    user: UserOut
    system: SystemInfo
    account: AccountInfo
    platform: PlatformInfo | None = None
