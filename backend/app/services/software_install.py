"""Server software installation service.

Handles downloading, verifying, extracting, and registering Minecraft server
software (vanilla, paper, fabric, forge) for a server. Integrates with the
Java Runtime Manager and server lifecycle.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
import time
from pathlib import Path
from typing import Optional

from app.core.config import Settings
from app.core.minecraft_software import (
    SoftwareDownloadError,
    SoftwareInstaller,
)
from app.models.server import Server
from app.models.user import User
from app.services.audit import record_audit, RequestMeta
from app.services.errors import ConflictError, DomainError, NotFoundError
from app.services.operations import OperationType, acquire_operation_lock

logger = logging.getLogger("asm.server.software")

# Maximum download size (1 GB).
MAX_DOWNLOAD_BYTES = 1024 * 1024 * 1024


class SoftwareInstallService:
    """Business logic for Minecraft server software installation."""

    def __init__(self, settings: Settings, db_session_factory) -> None:
        self.settings = settings
        self.session_factory = db_session_factory
        self.installer = SoftwareInstaller()

    # ------------------------------------------------------------------
    # Installation
    # ------------------------------------------------------------------

    def install_software(
        self,
        db: Session,
        server: Server,
        software_type: str,
        version: str,
        build_version: Optional[str] = None,
        actor: Optional[User] = None,
        meta: RequestMeta | None = None,
    ) -> dict:
        """Download, verify, extract, and register server software for a server.

        The server directory must already exist. The server JAR is downloaded
        to ``server.directory/server.jar`` (or a software-specific name).

        Raises:
            ConflictError: If a server.jar already exists.
            NotFoundError: If the server directory does not exist.
            DomainError: For download, checksum, or extraction failures.
        """

        directory = Path(server.directory)
        if not directory.exists() or not directory.is_dir():
            raise NotFoundError(
                f"Server directory '{directory}' does not exist."
            )

        # Determine the software JAR filename.
        jar_filename = self._jar_filename_for(software_type, version, build_version)
        jar_path = directory / jar_filename

        # Prevent overwriting an existing JAR.
        if jar_path.exists():
            raise ConflictError(
                f"server.jar already exists for server '{server.name}'. "
                "Remove it or choose a different server before installing software."
            )

        # Download to a temp file inside the server directory (same filesystem
        # for atomic move), verify checksum, then move into place.
        tmp_path: Optional[Path] = None
        try:
            with tempfile.NamedTemporaryFile(
                prefix=".asm-software-download-",
                suffix=".jar",
                delete=False,
                dir=str(directory),
            ) as tmp:
                tmp_path = Path(tmp.name)

            # Download the software artifact.
            info = self.installer.fetch_download_info(
                software_type, version, build_version
            )
            self.installer.download_file(
                info.download_url, tmp_path, expected_sha256=info.sha256
            )

            # Forge uses an installer JAR: do not extract. Save it directly
            # as the server JAR. The installer must be executed with
            # --installServer on first launch to generate server files.
            if info.forge_installer:
                shutil.move(str(tmp_path), str(jar_path))
                jar_path.chmod(0o600)
                tmp_path = None  # already moved
            elif info.fabric_needs_server_jar:
                # Fabric requires both the vanilla server JAR and the Fabric
                # Loader. Download the vanilla server JAR from Mojang first,
                # then the Fabric Loader JAR from Maven.
                from app.core.version_manifest import (
                    MojangManifestClient,
                    MojangManifestError,
                )

                try:
                    client = MojangManifestClient()
                    vanilla_metadata = client.get_metadata(version)
                except MojangManifestError as exc:
                    raise DomainError(
                        f"Failed to resolve vanilla server JAR for Fabric: {exc}"
                    ) from exc

                if not vanilla_metadata.server_download_url:
                    raise DomainError(
                        f"Minecraft version {version} has no server download "
                        "for Fabric."
                    )

                # Save vanilla server JAR as server.jar.
                vanilla_jar_path = directory / "server.jar"
                if vanilla_jar_path.exists():
                    raise ConflictError(
                        f"server.jar already exists for server '{server.name}'. "
                        "Remove it or choose a different server before installing "
                        "software."
                    )

                with tempfile.NamedTemporaryFile(
                    prefix=".asm-fabric-vanilla-",
                    suffix=".jar",
                    delete=False,
                    dir=str(directory),
                ) as vanilla_tmp:
                    vanilla_tmp_path = Path(vanilla_tmp.name)

                try:
                    self.installer.download_file(
                        vanilla_metadata.server_download_url,
                        vanilla_tmp_path,
                        expected_sha256=None,
                    )
                    shutil.move(str(vanilla_tmp_path), str(vanilla_jar_path))
                    vanilla_jar_path.chmod(0o600)
                finally:
                    if vanilla_tmp_path is not None and vanilla_tmp_path.exists():
                        try:
                            vanilla_tmp_path.unlink()
                        except OSError:
                            pass

                # Save the Fabric Loader JAR as the main JAR filename.
                if jar_path.exists():
                    raise ConflictError(
                        f"{jar_filename} already exists for server "
                        f"'{server.name}'. "
                        "Remove it or choose a different server before installing "
                        "software."
                    )
                shutil.move(str(tmp_path), str(jar_path))
                jar_path.chmod(0o600)
                tmp_path = None  # already moved
            else:
                # Extract into the server directory atomically.
                extract_target = directory / f".{jar_filename}.extract-{int(time.time())}"
                extract_target.mkdir(parents=True, exist_ok=True, mode=0o700)
                try:
                    self.installer._safe_extract_archive(tmp_path, extract_target)

                    # Find the JAR file inside the extracted archive.
                    jar_candidate = self._find_jar_in_archive(extract_target, jar_filename)
                    if jar_candidate is None:
                        raise DomainError(
                            f"Extracted archive does not contain a server JAR for {software_type}."
                        )

                    # Move into the final location.
                    shutil.move(str(jar_candidate), str(jar_path))
                    jar_path.chmod(0o600)

                finally:
                    if extract_target.exists():
                        shutil.rmtree(extract_target, ignore_errors=True)

        finally:
            if tmp_path is not None and tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

        # Update the server record.
        server.version = version
        server.software_version = info.build or version
        server.install_status = "completed"
        server.install_error = None
        db.commit()
        db.refresh(server)

        record_audit(
            db,
            action="server.software.installed",
            actor=actor,
            detail=(
                f"Installed {software_type} {version} "
                f"{f'(build {build_version})' if build_version else ''} "
                f"for server '{server.name}'."
            ),
            meta=meta,
        )
        db.commit()

        return {
            "version": version,
            "software_version": server.software_version,
            "software_type": software_type,
            "path": str(jar_path),
            "size_bytes": jar_path.stat().st_size,
            "sha256": info.sha256,
            "install_status": server.install_status,
        }

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def get_install_status(self, server: Server) -> dict:
        """Return installation status information for a server."""
        jar_filename = self._jar_filename_for(
            server.software_type, server.version, server.software_version
        )
        jar_path = Path(server.directory) / jar_filename
        return {
            "server_id": server.id,
            "software_type": server.software_type,
            "version": server.version,
            "software_version": server.software_version,
            "install_status": server.install_status or "none",
            "install_error": server.install_error,
            "jar_path": str(jar_path),
            "jar_exists": jar_path.exists(),
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _jar_filename_for(
        software_type: str, version: str, build_version: Optional[str] = None
    ) -> str:
        """Return the expected JAR filename for a software type and version."""
        if software_type == "vanilla":
            return "server.jar"
        if software_type == "paper":
            return f"paper-{version}-{build_version}.jar"
        if software_type == "fabric":
            return f"fabric-loader-{build_version}.jar" if build_version else "fabric-loader.jar"
        if software_type == "forge":
            return f"forge-{version}-{build_version}-installer.jar"
        raise DomainError(f"Unsupported software type: {software_type}.")

    @staticmethod
    def _find_jar_in_archive(extract_root: Path, expected_filename: str) -> Optional[Path]:
        """Find the JAR file inside an extracted archive."""
        # Look for any .jar file in the extracted archive.
        for jar_file in extract_root.rglob("*.jar"):
            if jar_file.is_file():
                return jar_file
        return None
