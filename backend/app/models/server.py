"""Minecraft server model."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.clock import utcnow
from app.database.base import Base


class Server(Base):
    """A managed Minecraft server instance owned by a user."""

    __tablename__ = "servers"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # UUID assigned at creation; stable identifier for API clients.
    unique_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    software_type: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    directory: Mapped[str] = mapped_column(String(512), nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False)
    max_players: Mapped[int] = mapped_column(Integer, nullable=False)
    online_mode: Mapped[bool] = mapped_column(Boolean, nullable=False)
    difficulty: Mapped[str] = mapped_column(String(16), nullable=False)
    gamemode: Mapped[str] = mapped_column(String(16), nullable=False)
    view_distance: Mapped[int] = mapped_column(Integer, nullable=False)
    motd: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    process_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    stopped_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    java_runtime_id: Mapped[int | None] = mapped_column(
        ForeignKey("java_runtimes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Software installation state.
    software_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    install_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending"
    )
    install_error: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # Optional extra JVM arguments (heap size, GC tuning, system properties).
    # Stored normalised (single-space separated) after validation.
    jvm_args: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    owner = relationship("User")  # noqa: F821
    java_runtime = relationship("JavaRuntime")  # noqa: F821
    logs = relationship(  # noqa: F821
        "ServerLog",
        back_populates="server",
        cascade="all, delete-orphan",
        order_by="ServerLog.created_at",
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"<Server id={self.id} name={self.name!r} status={self.status!r}>"


class ServerLog(Base):
    """Captured output line from a server process."""

    __tablename__ = "server_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    server_id: Mapped[int] = mapped_column(
        ForeignKey("servers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    stream: Mapped[str] = mapped_column(String(16), nullable=False)
    line: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, index=True
    )

    server = relationship("Server", back_populates="logs")  # noqa: F821

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"<ServerLog id={self.id} server_id={self.server_id} stream={self.stream!r}>"
