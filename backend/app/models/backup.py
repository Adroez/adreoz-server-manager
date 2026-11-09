"""Backup model for Minecraft server backups."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.clock import utcnow
from app.database.base import Base


class ServerBackup(Base):
    """A managed backup archive of a Minecraft server."""

    __tablename__ = "server_backups"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    server_id: Mapped[int] = mapped_column(
        ForeignKey("servers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # UUID assigned at creation; stable identifier for API clients.
    unique_id: Mapped[str] = mapped_column(
        String(36), nullable=False, unique=True, index=True
    )
    # Filename of the backup archive inside the backups directory. Never an
    # absolute path, and never derived from user input.
    filename: Mapped[str] = mapped_column(String(256), nullable=False)
    size_bytes: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )
    # Status: creating, completed, failed, restoring.
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    # Sanitized failure reason shown in the UI. Never contains host paths.
    error_message: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, index=True
    )

    server = relationship("Server")  # noqa: F821
    owner = relationship("User")  # noqa: F821

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return (
            f"<ServerBackup id={self.id} server_id={self.server_id} "
            f"status={self.status!r}>"
        )
