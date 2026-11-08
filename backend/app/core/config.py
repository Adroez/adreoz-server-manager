"""Application configuration.

Settings are loaded from environment variables prefixed with ``ASM_`` and an
optional ``.env`` file in the project root. Every setting has a safe,
local-first default: the management API binds to loopback and cookies are not
marked ``Secure`` so the panel works over plain HTTP on localhost.

Production deployments behind a TLS reverse proxy should set
``ASM_COOKIE_SECURE=true``.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """Runtime configuration for Adreoz Server Manager."""

    model_config = SettingsConfigDict(
        env_prefix="ASM_",
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Adreoz Server Manager"
    version: str = "0.1.0"
    environment: str = "development"

    host: str = "127.0.0.1"
    port: int = Field(default=8080, ge=1, le=65535)
    log_level: str = "info"

    data_dir: Path = PROJECT_ROOT / "data"
    frontend_dir: Path = PROJECT_ROOT / "frontend"
    database_url: str = ""

    session_cookie_name: str = "asm_session"
    cookie_secure: bool = False
    cookie_samesite: Literal["lax", "strict"] = "lax"
    session_lifetime_hours: int = Field(default=24, ge=1, le=720)

    login_attempt_limit: int = Field(default=10, ge=1, le=1000)
    login_attempt_window_seconds: int = Field(default=300, ge=10, le=86400)

    trust_proxy_headers: bool = False
    # Comma-separated Host header allowlist. Reverse-proxy deployments must add
    # their public hostname here.
    trusted_hosts: str = "127.0.0.1,localhost,[::1]"
    allow_root: bool = False

    @property
    def allowed_hosts(self) -> list[str]:
        hosts = [host.strip() for host in self.trusted_hosts.split(",") if host.strip()]
        return hosts or ["127.0.0.1", "localhost"]

    @property
    def database_uri(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{self.data_dir / 'asm.db'}"

    @property
    def servers_dir(self) -> Path:
        return self.data_dir / "servers"

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    def ensure_directories(self) -> None:
        """Create the runtime directories with owner-only permissions.

        The data directory contains password hashes, session digests, and the
        audit trail, so it must not be readable by other local users.
        """

        for directory in (
            self.data_dir,
            self.servers_dir,
            self.backups_dir,
            self.logs_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            directory.chmod(0o700)

    def secure_database_permissions(self) -> None:
        """Restrict a file-based SQLite database to the service user."""

        if not self.database_uri.startswith("sqlite"):
            return
        database = make_url(self.database_uri).database
        if not database or database == ":memory:":
            return
        path = Path(database)
        if path.is_file():
            path.chmod(0o600)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings instance."""

    return Settings()
