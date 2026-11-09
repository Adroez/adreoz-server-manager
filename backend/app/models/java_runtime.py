"""Java runtime model."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.clock import utcnow
from app.database.base import Base


class JavaRuntime(Base):
    """A detected or managed Java runtime usable by Minecraft servers."""

    __tablename__ = "java_runtimes"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    vendor: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    major_version: Mapped[int] = mapped_column(Integer, nullable=False)
    executable_path: Mapped[str] = mapped_column(String(512), nullable=False, unique=True, index=True)
    source_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    checksum: Mapped[str | None] = mapped_column(String(128), nullable=True)
    checksum_algorithm: Mapped[str | None] = mapped_column(String(16), nullable=True)
    install_status: Mapped[str] = mapped_column(String(32), nullable=False, default="installed")
    installed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    is_managed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"<JavaRuntime id={self.id} name={self.name!r} version={self.version!r}>"
