"""Backup and restore request/response schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Automatic backup intervals accepted by the API, in hours.
# 1h, 6h, 12h, 1d, 3d, 7d.
SUPPORTED_BACKUP_INTERVALS: tuple[int, ...] = (1, 6, 12, 24, 72, 168)

# Retention bounds.
MIN_BACKUP_RETENTION = 1
MAX_BACKUP_RETENTION = 100


class BackupOut(BaseModel):
    """Public metadata for a single backup.

    ``filename`` is the managed archive name only; host filesystem paths are
    never exposed.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    unique_id: str
    server_id: int
    filename: str
    size_bytes: int
    status: str
    error: str | None = None
    created_at: datetime


class BackupListResponse(BaseModel):
    """A server's backup listing."""

    items: list[BackupOut]
    total: int


class BackupActionResponse(BaseModel):
    """Generic response for backup mutations."""

    message: str
    status: str = "ok"


class BackupSettingsOut(BaseModel):
    """Per-server automatic backup configuration."""

    enabled: bool
    interval_hours: int
    max_backups: int
    supported_intervals: list[int] = Field(
        default_factory=lambda: list(SUPPORTED_BACKUP_INTERVALS)
    )


class BackupSettingsUpdateRequest(BaseModel):
    """Patch a server's automatic backup configuration."""

    enabled: bool | None = None
    interval_hours: int | None = Field(default=None, ge=1, le=8760)
    max_backups: int | None = Field(
        default=None, ge=MIN_BACKUP_RETENTION, le=MAX_BACKUP_RETENTION
    )

    @field_validator("interval_hours")
    @classmethod
    def _supported_interval(cls, value: int | None) -> int | None:
        if value is not None and value not in SUPPORTED_BACKUP_INTERVALS:
            supported = ", ".join(str(hour) for hour in SUPPORTED_BACKUP_INTERVALS)
            raise ValueError(
                f"interval_hours must be one of: {supported}."
            )
        return value
