"""Java runtime service layer.

Handles listing runtimes, installing managed Java runtimes (Eclipse Temurin),
verifying checksums, safely extracting archives, and deleting managed runtimes.
Runtimes assigned to servers are protected from deletion.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import tarfile
import tempfile
import time
from dataclasses import asdict
from pathlib import Path
from typing import Optional
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

from app.core.java import JavaRuntime, JavaRuntimeManager, JavaRuntimeError
from app.core.config import Settings
from app.models.server import Server
from app.models.user import User
from app.services.audit import record_audit, RequestMeta
from app.services.errors import ConflictError, DomainError, ForbiddenError, NotFoundError
from app.services.operations import OperationType, acquire_operation_lock

logger = logging.getLogger("asm.java.runtime")

# Managed Java 17 (Eclipse Temurin) distribution.
# Checksums are pinned so tampered downloads are rejected.
MANAGED_JAVA_17 = {
    "name": "Temurin 17",
    "version": "17.0.13+11",
    "vendor": "Eclipse Temurin",
    "source_url": "https://github.com/adoptium/temurin17-binaries/releases/download/jdk-17.0.13%2B11/OpenJDK17U-jdk_x64_linux_hotspot_17.0.13_11.tar.gz",
    "checksum": "8682892fc02965930b9022c066fa164dd6f458ef4a5dc262016aa28333b30f49",
    "checksum_algorithm": "sha256",
}

# Maximum managed runtime archive size (200 MB).
MAX_RUNTIME_DOWNLOAD_BYTES = 200 * 1024 * 1024


class JavaRuntimeService:
    """Business logic for Java runtime management."""

    def __init__(self, settings: Settings, db_session_factory) -> None:
        self.settings = settings
        self.session_factory = db_session_factory
        self.manager = JavaRuntimeManager(settings)

    # ------------------------------------------------------------------
    # Listing
    # ------------------------------------------------------------------

    def list_runtimes(self, db) -> list[dict]:
        """List all known runtimes (system + managed) from the DB plus live detection."""

        from app.models.java_runtime import JavaRuntime as JavaRuntimeModel

        rows = db.query(JavaRuntimeModel).all()
        known: dict[str, dict] = {}
        for row in rows:
            known[row.executable_path] = {
                "id": row.id,
                "name": row.name,
                "vendor": row.vendor,
                "version": row.version,
                "major_version": row.major_version,
                "executable_path": row.executable_path,
                "source_url": row.source_url,
                "checksum": row.checksum,
                "checksum_algorithm": row.checksum_algorithm,
                "install_status": row.install_status,
                "installed_at": row.installed_at.isoformat() if row.installed_at else None,
                "is_managed": row.is_managed,
                "detected_live": False,
            }

        # Merge with live detection so newly installed JDKs appear without a DB write.
        for runtime in self.manager.detect_system_java():
            path = runtime.executable_path
            if path in known:
                known[path]["detected_live"] = True
                # Refresh live fields.
                known[path].update(
                    {
                        "name": runtime.name,
                        "vendor": runtime.vendor,
                        "version": runtime.version,
                        "major_version": runtime.major_version,
                        "is_executable": runtime.is_executable,
                    }
                )
            else:
                known[path] = {
                    "id": None,
                    "name": runtime.name,
                    "vendor": runtime.vendor,
                    "version": runtime.version,
                    "major_version": runtime.major_version,
                    "executable_path": runtime.executable_path,
                    "source_url": None,
                    "checksum": None,
                    "checksum_algorithm": None,
                    "install_status": "installed",
                    "installed_at": None,
                    "is_managed": runtime.is_managed,
                    "detected_live": True,
                    "is_executable": runtime.is_executable,
                }

        return list(known.values())

    # ------------------------------------------------------------------
    # Install managed Java 17
    # ------------------------------------------------------------------

    def install_managed_java_17(
        self,
        db,
        actor: User,
        meta: RequestMeta | None,
    ) -> dict:
        """Download, verify, extract, and register Eclipse Temurin Java 17."""

        distribution = MANAGED_JAVA_17
        runtime_dir_name = f"temurin-{distribution['version'].replace('+', '-')}"
        target_dir = self.manager.runtime_path(runtime_dir_name)

        if target_dir.exists() and (target_dir / "bin" / "java").exists():
            # Already installed; register if missing.
            existing = self._find_managed_runtime(db, target_dir)
            if existing is None:
                existing = self._register_managed_runtime(
                    db,
                    actor,
                    meta,
                    name=distribution["name"],
                    version=distribution["version"],
                    vendor=distribution["vendor"],
                    executable_path=str(target_dir / "bin" / "java"),
                    source_url=distribution["source_url"],
                    checksum=None,
                    checksum_algorithm=distribution["checksum_algorithm"],
                    installed_at=None,
                )
                db.commit()
            return self._runtime_to_dict(existing)

        # Download to a temp file, verify checksum, then extract atomically.
        tmp_path: Optional[Path] = None
        try:
            with tempfile.NamedTemporaryFile(
                prefix=".asm-java-download-", suffix=".tar.gz", delete=False, dir=str(self.settings.data_dir)
            ) as tmp:
                tmp_path = Path(tmp.name)
                self._download_file(distribution["source_url"], tmp_path)

            # Verify checksum if we have one.
            expected_checksum = distribution["checksum"]
            if expected_checksum and expected_checksum != "placeholder":
                actual = self._sha256(tmp_path)
                if actual.lower() != expected_checksum.lower():
                    raise DomainError(
                        "Java runtime checksum verification failed. Download may be corrupted or tampered."
                    )

            # Extract into the target directory atomically via a sibling temp dir.
            extract_target = self.settings.data_dir / f".{runtime_dir_name}.extract-{int(time.time())}"
            extract_target.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                self._safe_extract_tar_gz(tmp_path, extract_target)

                # The Temurin archive unpacks into a top-level jdk-... directory.
                # Find the directory containing bin/java.
                java_home = self._find_java_home(extract_target)
                if java_home is None:
                    raise DomainError("Extracted archive does not contain a Java runtime.")

                # Move into the final managed location.
                if target_dir.exists():
                    shutil.rmtree(target_dir)
                shutil.move(str(java_home), str(target_dir))

                runtime = self._register_managed_runtime(
                    db,
                    actor,
                    meta,
                    name=distribution["name"],
                    version=distribution["version"],
                    vendor=distribution["vendor"],
                    executable_path=str(target_dir / "bin" / "java"),
                    source_url=distribution["source_url"],
                    checksum=distribution["checksum"],
                    checksum_algorithm=distribution["checksum_algorithm"],
                    installed_at=None,
                )
                db.commit()
                return self._runtime_to_dict(runtime)
            finally:
                if extract_target.exists():
                    shutil.rmtree(extract_target, ignore_errors=True)
        finally:
            if tmp_path is not None and tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

    # ------------------------------------------------------------------
    # Delete managed runtime
    # ------------------------------------------------------------------

    def delete_managed_runtime(
        self,
        db,
        runtime_id: int,
        actor: User,
        meta: RequestMeta | None,
    ) -> None:
        """Delete a managed runtime, refusing if it is assigned to any server."""

        from app.models.java_runtime import JavaRuntime as JavaRuntimeModel

        runtime = db.get(JavaRuntimeModel, runtime_id)
        if runtime is None:
            raise NotFoundError("Java runtime not found.")
        if not runtime.is_managed:
            raise DomainError("Only managed runtimes can be deleted.")

        # Protect runtimes assigned to servers.
        assigned = (
            db.query(Server).filter(Server.java_runtime_id == runtime_id).first()
        )
        if assigned is not None:
            raise ConflictError(
                f"Java runtime '{runtime.name}' is assigned to server "
                f"'{assigned.name}' and cannot be deleted."
            )

        executable_path = Path(runtime.executable_path)
        # Safety: only delete inside the managed runtimes directory.
        try:
            executable_path.resolve().relative_to(self.manager.runtimes_dir.resolve())
        except ValueError:
            raise DomainError("Refusing to delete a runtime outside the managed directory.")

        db.delete(runtime)
        record_audit(
            db,
            action="java.runtime.deleted",
            actor=actor,
            detail=f"Deleted managed Java runtime '{runtime.name}'.",
            meta=meta,
        )
        db.commit()

        # Remove the directory from disk after DB commit.
        runtime_dir = executable_path.parent.parent
        if runtime_dir.exists() and runtime_dir.is_dir():
            try:
                shutil.rmtree(runtime_dir)
            except OSError:
                logger.exception("Failed to remove runtime directory %s", runtime_dir)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def cleanup_orphan_artifacts(self, max_age_seconds: int = 3600) -> int:
        """Remove leftovers from interrupted Java runtime installations.

        Interrupted downloads and extractions leave ``.asm-java-download-*``
        files and ``.<runtime>.extract-*`` directories in the data directory.
        They are never valid runtimes, so they are safe to delete once they are
        older than ``max_age_seconds`` (to avoid racing an install in progress).
        """

        data_dir = Path(self.settings.data_dir)
        if not data_dir.is_dir():
            return 0

        removed = 0
        cutoff = time.time() - max_age_seconds

        for entry in data_dir.iterdir():
            name = entry.name
            is_download = name.startswith(".asm-java-download-")
            is_extract = name.startswith(".") and ".extract-" in name
            if not (is_download or is_extract):
                continue
            try:
                if entry.stat().st_mtime > cutoff:
                    continue
                if entry.is_dir():
                    shutil.rmtree(entry, ignore_errors=True)
                else:
                    entry.unlink()
                removed += 1
                logger.info("Removed orphan Java runtime artifact %s", entry)
            except OSError:
                logger.exception("Failed to remove orphan artifact %s", entry)

        return removed

    def _download_file(self, url: str, destination: Path) -> None:
        """Download a file to ``destination`` with size limits and progress."""

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
                if total_size is not None and total_size > MAX_RUNTIME_DOWNLOAD_BYTES:
                    raise DomainError(
                        f"Download too large ({total_size // (1024*1024)} MB; max "
                        f"{MAX_RUNTIME_DOWNLOAD_BYTES // (1024*1024)} MB)."
                    )

                downloaded = 0
                with open(destination, "wb") as out_file:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        downloaded += len(chunk)
                        # Applies to chunked responses as well as ones that
                        # declare Content-Length.
                        if downloaded > MAX_RUNTIME_DOWNLOAD_BYTES:
                            raise DomainError("Download exceeded maximum size.")
                        out_file.write(chunk)
        except DomainError:
            # Remove a partial download so an interrupted install cannot be
            # mistaken for a complete archive.
            try:
                destination.unlink(missing_ok=True)
            except OSError:  # pragma: no cover - best effort
                logger.exception("Failed to remove partial download %s", destination)
            raise
        except HTTPError as exc:
            logger.error("Java download failed: HTTP %s", exc.code)
            raise DomainError(f"Failed to download Java runtime (HTTP {exc.code}).") from exc
        except URLError as exc:
            logger.error("Java download network error: %s", exc)
            raise DomainError("Unable to reach the Java distribution server.") from exc

    @staticmethod
    def _sha256(path: Path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()

    @staticmethod
    def _safe_extract_tar_gz(archive: Path, dest: Path) -> None:
        """Safely extract a tar.gz, preventing path traversal.

        Validates all members before extraction. Rejects symlinks, hard links,
        devices, FIFOs, and any entry that would escape the destination directory.
        Uses tarfile's filter="data" on Python 3.12+ for defense in depth.
        """

        with tarfile.open(archive, "r:gz") as tar:
            members = tar.getmembers()
            for member in members:
                # Reject unsafe member types: symlinks, hard links, devices, FIFOs.
                if member.isdir() or member.isfile():
                    pass
                else:
                    raise DomainError("Unsafe archive entry type detected during extraction.")
                member_path = Path(member.name)
                if member_path.is_absolute() or ".." in member_path.parts:
                    raise DomainError("Unsafe archive entry detected during extraction.")
                target = (dest / member_path).resolve()
                try:
                    target.relative_to(dest.resolve())
                except ValueError:
                    raise DomainError("Unsafe archive entry detected during extraction.")
            # Use filter="data" on Python 3.12+ for defense in depth.
            extract_kwargs = {"path": dest, "members": members}
            try:
                extract_kwargs["filter"] = "data"
            except TypeError:
                pass  # Older Python: filter not supported.
            tar.extractall(**extract_kwargs)

    @staticmethod
    def _find_java_home(extract_root: Path) -> Optional[Path]:
        """Find the directory containing bin/java inside an extracted archive."""

        for entry in extract_root.rglob("bin/java"):
            if entry.is_file() and os.access(entry, os.X_OK):
                # java home is the parent of bin.
                return entry.parent.parent
        return None

    def _find_managed_runtime(self, db, path: Path):
        """Find a managed runtime by its installation directory.

        The `path` parameter is the runtime directory (e.g., /data/runtimes/temurin-17...),
        but the DB stores the executable path (e.g., /data/runtimes/temurin-17.../bin/java).
        Both forms are matched exactly (no prefix matching, which could match a
        different runtime whose name merely starts with the same characters).
        """
        from app.models.java_runtime import JavaRuntime as JavaRuntimeModel

        resolved = path.resolve()
        candidates = {
            str(resolved),
            str(resolved / "bin" / "java"),
        }
        return (
            db.query(JavaRuntimeModel)
            .filter(JavaRuntimeModel.executable_path.in_(candidates))
            .first()
        )

    def _register_managed_runtime(
        self,
        db,
        actor: User,
        meta: RequestMeta | None,
        *,
        name: str,
        version: str,
        vendor: str,
        executable_path: str,
        source_url: Optional[str],
        checksum: Optional[str],
        checksum_algorithm: Optional[str],
        installed_at: Optional[str],
    ):
        from app.models.java_runtime import JavaRuntime as JavaRuntimeModel
        from app.core.clock import utcnow

        runtime = JavaRuntimeModel(
            name=name,
            vendor=vendor,
            version=version,
            major_version=17,
            executable_path=executable_path,
            source_url=source_url,
            checksum=checksum,
            checksum_algorithm=checksum_algorithm,
            install_status="installed",
            installed_at=utcnow(),
            is_managed=True,
        )
        db.add(runtime)
        db.flush()
        record_audit(
            db,
            action="java.runtime.installed",
            actor=actor,
            detail=f"Installed managed Java runtime '{name}' ({version}).",
            meta=meta,
        )
        return runtime

    @staticmethod
    def _runtime_to_dict(row) -> dict:
        return {
            "id": row.id,
            "name": row.name,
            "vendor": row.vendor,
            "version": row.version,
            "major_version": row.major_version,
            "executable_path": row.executable_path,
            "source_url": row.source_url,
            "checksum": row.checksum,
            "checksum_algorithm": row.checksum_algorithm,
            "install_status": row.install_status,
            "installed_at": row.installed_at.isoformat() if row.installed_at else None,
            "is_managed": row.is_managed,
        }
