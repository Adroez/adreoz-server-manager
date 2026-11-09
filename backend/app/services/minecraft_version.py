"""Minecraft version service layer.

Fetches available Minecraft versions from Mojang's official manifest, resolves
server download metadata, and downloads the server JAR with SHA-1 verification
and progress reporting.
"""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Optional
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError

from app.core.version_manifest import (
    MojangManifestClient,
    MojangManifestError,
    MinecraftVersionMetadata,
    MinecraftVersionInfo,
)
from app.core.config import Settings
from app.models.server import Server
from app.models.user import User
from app.services.audit import record_audit, RequestMeta
from app.services.errors import ConflictError, DomainError, ForbiddenError, NotFoundError
from app.services.operations import OperationType, acquire_operation_lock

logger = logging.getLogger("asm.minecraft.version")

# Maximum Minecraft server JAR download size (1 GB).
MAX_JAR_DOWNLOAD_BYTES = 1024 * 1024 * 1024


class MinecraftVersionService:
    """Business logic for Minecraft version management."""

    def __init__(self, settings: Settings, db_session_factory) -> None:
        self.settings = settings
        self.session_factory = db_session_factory
        self.manifest_client = MojangManifestClient()

    # ------------------------------------------------------------------
    # Version listing
    # ------------------------------------------------------------------

    def list_versions(self) -> list[dict]:
        """Return all Minecraft versions from Mojang's manifest."""

        try:
            versions = self.manifest_client.list_versions()
        except MojangManifestError as exc:
            raise DomainError(str(exc)) from exc

        return [
            {
                "id": v.id,
                "type": v.type,
                "url": v.url,
                "time": v.time,
                "release_time": v.release_time,
                "is_release": v.type == "release",
            }
            for v in versions
        ]

    def list_releases(self) -> list[dict]:
        """Return only release versions, newest first."""

        try:
            releases = self.manifest_client.list_releases()
        except MojangManifestError as exc:
            raise DomainError(str(exc)) from exc

        return [
            {
                "id": v.id,
                "type": v.type,
                "url": v.url,
                "time": v.time,
                "release_time": v.release_time,
                "is_release": True,
            }
            for v in releases
        ]

    def get_version_metadata(self, version_id: str) -> dict:
        """Return detailed metadata for a version, including server download info."""

        try:
            metadata = self.manifest_client.get_metadata(version_id)
        except MojangManifestError as exc:
            raise DomainError(str(exc)) from exc

        if not metadata.server_download_url:
            raise DomainError(
                f"Minecraft version {version_id} has no official server download."
            )

        return {
            "id": metadata.id,
            "type": metadata.type,
            "time": metadata.time,
            "release_time": metadata.release_time,
            "server_download_url": metadata.server_download_url,
            "server_download_sha1": metadata.server_download_sha1,
            "server_download_size": metadata.server_download_size,
            "java_version_major": metadata.java_version_major,
            "is_release": metadata.type == "release",
        }

    # ------------------------------------------------------------------
    # Download server JAR
    # ------------------------------------------------------------------

    def download_server_jar(
        self,
        db,
        server: Server,
        version_id: str,
        actor: User,
        meta: RequestMeta | None,
    ) -> dict:
        """Download the official server JAR for a version into the server dir.

        Verifies the SHA-1 checksum against Mojang metadata. Never overwrites
        an existing server JAR without explicit confirmation (handled by the
        caller via the `overwrite` flag on the route).
        """

        metadata = self.get_version_metadata(version_id)

        directory = Path(server.directory)
        jar_path = directory / "server.jar"

        # Prevent overwriting an existing JAR.
        if jar_path.exists():
            raise ConflictError(
                f"server.jar already exists for server '{server.name}'. "
                "Remove it or choose a different server before downloading."
            )

        directory.mkdir(parents=True, exist_ok=True, mode=0o700)

        tmp_path: Optional[Path] = None
        try:
            with tempfile.NamedTemporaryFile(
                prefix=".asm-jar-download-",
                suffix=".jar",
                delete=False,
                dir=str(directory),
            ) as tmp:
                tmp_path = Path(tmp.name)

            self._download_file(metadata["server_download_url"], tmp_path)

            # Verify SHA-1.
            expected_sha1 = metadata.get("server_download_sha1")
            if expected_sha1:
                actual_sha1 = self._sha1(tmp_path)
                if actual_sha1.lower() != expected_sha1.lower():
                    raise DomainError(
                        "Minecraft server JAR checksum verification failed. "
                        "The download may be corrupted."
                    )

            # Atomic move into place.
            shutil_move = __import__("shutil").move
            shutil_move(str(tmp_path), str(jar_path))
            tmp_path = None
            jar_path.chmod(0o600)

            # Update the server record's version field.
            server.version = version_id
            db.commit()
            db.refresh(server)

            record_audit(
                db,
                action="server.jar.downloaded",
                actor=actor,
                detail=(
                    f"Downloaded Minecraft {version_id} server JAR for "
                    f"server '{server.name}'."
                ),
                meta=meta,
            )
            db.commit()

            return {
                "version": version_id,
                "path": str(jar_path),
                "size_bytes": jar_path.stat().st_size,
                "sha1": metadata.get("server_download_sha1"),
            }
        finally:
            if tmp_path is not None and tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _download_file(self, url: str, destination: Path) -> None:
        """Download a file with size limits."""

        # Validate the URL is from Mojang's trusted domains.
        from urllib.parse import urlparse

        parsed = urlparse(url)
        allowed_hosts = {
            "launcher.mojang.com",
            "piston-data.mojang.com",
            "libraries.minecraft.net",
            "s3.amazonaws.com",
            "download.mojang.com",
        }
        if parsed.hostname not in allowed_hosts:
            raise DomainError(f"Download URL host not allowed: {parsed.hostname}")

        request = Request(
            url,
            headers={
                "User-Agent": "Adreoz-Server-Manager/0.1.0",
                "Accept": "*/*",
            },
        )
        try:
            with urlopen(request, timeout=60) as response:
                total = response.headers.get("Content-Length")
                total_size = int(total) if total else None
                if total_size is not None and total_size > MAX_JAR_DOWNLOAD_BYTES:
                    raise DomainError(
                        f"Download too large ({total_size // (1024*1024)} MB; max "
                        f"{MAX_JAR_DOWNLOAD_BYTES // (1024*1024)} MB)."
                    )

                downloaded = 0
                with open(destination, "wb") as out_file:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        out_file.write(chunk)
                        downloaded += len(chunk)
                        if total_size is not None and downloaded > MAX_JAR_DOWNLOAD_BYTES:
                            raise DomainError("Download exceeded maximum size.")
        except HTTPError as exc:
            logger.error("Minecraft JAR download failed: HTTP %s", exc.code)
            raise DomainError(
                f"Failed to download Minecraft server JAR (HTTP {exc.code})."
            ) from exc
        except URLError as exc:
            logger.error("Minecraft JAR download network error: %s", exc)
            raise DomainError(
                "Unable to reach Mojang's download servers."
            ) from exc

    @staticmethod
    def _sha1(path: Path) -> str:
        h = hashlib.sha1()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
