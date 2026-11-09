"""Minecraft server software download and installation.

Provides a unified interface for downloading server software from various
providers (Mojang, PaperMC, Fabric, Forge) with checksum verification and
safe archive extraction.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import tarfile
import tempfile
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError

logger = logging.getLogger("asm.minecraft.software")

# Maximum download size (1 GB).
MAX_DOWNLOAD_BYTES = 1024 * 1024 * 1024

# Download timeout in seconds.
DOWNLOAD_TIMEOUT_SECONDS = 120


@dataclass
class SoftwareDownloadInfo:
    """Metadata for a downloadable server software artifact."""

    version: str
    build: Optional[str] = None
    download_url: str = ""
    sha1: Optional[str] = None
    sha256: Optional[str] = None
    size: Optional[int] = None
    filename: str = ""
    forge_installer: bool = False
    fabric_needs_server_jar: bool = False
    fabric_launcher_class: Optional[str] = None


@dataclass
class SoftwareVersionInfo:
    """A single software version entry."""

    id: str
    type: str
    release_time: Optional[str] = None


@dataclass
class SoftwareBuildInfo:
    """A single software build/loader version entry."""

    id: str
    time: Optional[str] = None


class SoftwareDownloadError(RuntimeError):
    """Raised when a software download or verification fails."""


class SoftwareInstaller:
    """Downloads and verifies Minecraft server software from trusted sources."""

    def __init__(self, timeout_seconds: int = DOWNLOAD_TIMEOUT_SECONDS) -> None:
        self.timeout_seconds = timeout_seconds

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    def _fetch_json(self, url: str) -> dict:
        """Fetch and parse a JSON response from a URL.

        Includes the identifying User-Agent header required by providers.
        """
        import json
        import urllib.request
        from urllib.error import HTTPError, URLError

        try:
            request = Request(
                url,
                headers={
                    "User-Agent": "Adreoz-Server-Manager/0.1.0",
                    "Accept": "application/json",
                },
            )
            with urlopen(request, timeout=self.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise SoftwareDownloadError(
                f"HTTP {exc.code} for {url}."
            ) from exc
        except URLError as exc:
            raise SoftwareDownloadError(
                f"Unable to reach {url}."
            ) from exc
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Unexpected error fetching JSON from %s", url)
            raise SoftwareDownloadError(
                f"Failed to fetch JSON from {url}."
            ) from exc

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_vanilla_metadata(self, version: str) -> SoftwareDownloadInfo:
        """Fetch Mojang version metadata and return server download info."""
        # Lazy import to avoid circular imports.
        from app.core.version_manifest import MojangManifestClient, MojangManifestError

        client = MojangManifestClient()
        try:
            metadata = client.get_metadata(version)
        except MojangManifestError as exc:
            raise SoftwareDownloadError(str(exc)) from exc

        if not metadata.server_download_url:
            raise SoftwareDownloadError(
                f"Minecraft version {version} has no official server download."
            )

        return SoftwareDownloadInfo(
            version=version,
            download_url=metadata.server_download_url,
            sha1=metadata.server_download_sha1,
            size=metadata.server_download_size,
            filename=f"minecraft-server-{version}.jar",
        )

    def get_paper_metadata(self, version: str, build: str) -> SoftwareDownloadInfo:
        """Fetch PaperMC metadata via the official Fill v3 API.

        Uses https://fill.papermc.io/v3/projects/paper/versions/{version}/builds/{build}
        and returns the download URL and SHA-256 checksum published by the API.
        Download URLs are taken directly from the API response and are never
        constructed manually.
        """
        url = (
            f"https://fill.papermc.io/v3/projects/paper/versions/"
            f"{version}/builds/{build}"
        )

        try:
            data = self._fetch_json(url)
        except SoftwareDownloadError:
            raise

        downloads = data.get("downloads", {})
        server_default = downloads.get("server:default", {})
        download_url = server_default.get("url")
        sha256 = server_default.get("checksums", {}).get("sha256")
        size = server_default.get("size")
        filename = server_default.get(
            "name", f"paper-{version}-{build}.jar"
        )

        if not download_url:
            raise SoftwareDownloadError(
                f"PaperMC version {version} build {build} has no server:default "
                "download URL in Fill API response."
            )

        return SoftwareDownloadInfo(
            version=version,
            build=str(build),
            download_url=download_url,
            sha256=sha256,
            size=size,
            filename=filename,
        )

    def get_fabric_metadata(self, version: str, loader_version: str) -> SoftwareDownloadInfo:
        """Fetch Fabric metadata for a specific version and loader version."""
        import json
        import urllib.request
        from urllib.error import HTTPError, URLError

        # Fabric Meta API: https://meta.fabricmc.net/v2/versions/loader/{version}/{loader_version}/server/json
        url = f"https://meta.fabricmc.net/v2/versions/loader/{version}/{loader_version}/server/json"

        try:
            request = Request(
                url,
                headers={
                    "User-Agent": "Adreoz-Server-Manager/0.1.0",
                    "Accept": "application/json",
                },
            )
            with urlopen(request, timeout=self.timeout_seconds) as response:
                data = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise SoftwareDownloadError(
                f"Fabric API error: HTTP {exc.code} for version {version} loader {loader_version}."
            ) from exc
        except URLError as exc:
            raise SoftwareDownloadError(
                f"Unable to reach Fabric API for version {version} loader {loader_version}."
            ) from exc
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Unexpected error fetching Fabric metadata")
            raise SoftwareDownloadError(
                f"Failed to fetch Fabric metadata for {version} loader {loader_version}."
            ) from exc

        download_url = data.get("url")
        sha256 = data.get("sha256")
        size = data.get("size")
        filename = data.get("filename", f"fabric-server-launch-{loader_version}.jar")

        if not download_url:
            # The Fabric server JSON does not include a top-level download URL.
            # The Fabric Loader JAR is downloaded from Maven instead.
            download_url = (
                f"https://maven.fabricmc.net/net/fabricmc/fabric-loader/"
                f"{loader_version}/fabric-loader-{loader_version}.jar"
            )
            filename = f"fabric-loader-{loader_version}.jar"
            sha256 = None  # Checksum not published in the JSON
            size = None

        # Also requires the vanilla Minecraft server JAR from Mojang.
        fabric_needs_server_jar = True

        return SoftwareDownloadInfo(
            version=version,
            build=loader_version,
            download_url=download_url,
            sha256=sha256,
            size=size,
            filename=filename,
            fabric_needs_server_jar=fabric_needs_server_jar,
            fabric_launcher_class=data.get("mainClass"),
        )

    def get_forge_metadata(self, version: str, forge_version: str) -> SoftwareDownloadInfo:
        """Fetch Forge metadata for a specific Minecraft and Forge version.

        Forge uses an installer JAR workflow (not a universal JAR). The
        installer JAR must be executed with ``--installServer`` to generate
        the actual server JAR, libraries, and launch scripts. We download the
        installer and record that Forge requires installer execution before
        first launch.
        """
        import json
        import urllib.request
        from urllib.error import HTTPError, URLError

        # Forge installer JAR URL pattern.
        # Modern Forge (1.13+) does not distribute a universal JAR; the
        # installer JAR is the official artifact.
        url = (
            f"https://maven.minecraftforge.net/net/minecraftforge/forge/"
            f"{version}-{forge_version}/forge-{version}-{forge_version}-installer.jar"
        )

        try:
            request = Request(
                url,
                headers={
                    "User-Agent": "Adreoz-Server-Manager/0.1.0",
                    "Accept": "application/java-archive",
                },
            )
            with urlopen(request, timeout=self.timeout_seconds) as response:
                total = response.headers.get("Content-Length")
                total_size = int(total) if total else None
                sha256 = response.headers.get("X-Checksum-Sha256")
        except HTTPError as exc:
            raise SoftwareDownloadError(
                f"Forge download error: HTTP {exc.code} for {version} forge {forge_version}."
            ) from exc
        except URLError as exc:
            raise SoftwareDownloadError(
                f"Unable to reach Forge download for {version} forge {forge_version}."
            ) from exc
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Unexpected error fetching Forge metadata")
            raise SoftwareDownloadError(
                f"Failed to fetch Forge metadata for {version} forge {forge_version}."
            ) from exc

        return SoftwareDownloadInfo(
            version=version,
            build=forge_version,
            download_url=url,
            sha256=sha256,
            size=total_size,
            filename=f"forge-{version}-{forge_version}-installer.jar",
            forge_installer=True,
        )

    # ------------------------------------------------------------------
    # Download and verification
    # ------------------------------------------------------------------

    def fetch_download_info(
        self,
        software_type: str,
        version: str,
        build_version: Optional[str] = None,
    ) -> SoftwareDownloadInfo:
        """Dispatch to the correct metadata fetcher based on software type."""
        if software_type == "vanilla":
            return self.get_vanilla_metadata(version)
        if software_type == "paper":
            if not build_version:
                raise SoftwareDownloadError(
                    f"Paper software requires a build version for {version}."
                )
            return self.get_paper_metadata(version, build_version)
        if software_type == "fabric":
            if not build_version:
                raise SoftwareDownloadError(
                    f"Fabric software requires a loader version for {version}."
                )
            return self.get_fabric_metadata(version, build_version)
        if software_type == "forge":
            if not build_version:
                raise SoftwareDownloadError(
                    f"Forge software requires a Forge version for {version}."
                )
            return self.get_forge_metadata(version, build_version)
        raise SoftwareDownloadError(
            f"Unsupported software type: {software_type}."
        )

    def download_file(
        self, url: str, destination: Path, expected_sha256: Optional[str] = None
    ) -> None:
        """Download a file to ``destination`` with size limits and SHA-256 verification."""

        # Validate the download URL host.
        from urllib.parse import urlparse

        parsed = urlparse(url)
        allowed_hosts = {
            "launcher.mojang.com",
            "piston-data.mojang.com",
            "libraries.minecraft.net",
            "s3.amazonaws.com",
            "download.mojang.com",
            "api.papermc.io",
            "repo.papermc.io",
            "fill.papermc.io",
            "fill-data.papermc.io",
            "meta.fabricmc.net",
            "maven.fabricmc.net",
            "maven.minecraftforge.net",
            "files.minecraftforge.net",
        }
        if parsed.hostname not in allowed_hosts:
            raise SoftwareDownloadError(
                f"Download URL host not allowed: {parsed.hostname}"
            )

        request = Request(
            url,
            headers={
                "User-Agent": "Adreoz-Server-Manager/0.1.0",
                "Accept": "*/*",
            },
        )

        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                total = response.headers.get("Content-Length")
                total_size = int(total) if total else None
                if total_size is not None and total_size > MAX_DOWNLOAD_BYTES:
                    raise SoftwareDownloadError(
                        f"Download too large ({total_size // (1024 * 1024)} MB; max "
                        f"{MAX_DOWNLOAD_BYTES // (1024 * 1024)} MB)."
                    )

                downloaded = 0
                with open(destination, "wb") as out_file:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        out_file.write(chunk)
                        downloaded += len(chunk)
                        if total_size is not None and downloaded > MAX_DOWNLOAD_BYTES:
                            raise SoftwareDownloadError(
                                f"Download exceeded maximum size ({MAX_DOWNLOAD_BYTES // (1024 * 1024)} MB)."
                            )
        except HTTPError as exc:
            logger.error("Download failed: HTTP %s for %s", exc.code, url)
            raise SoftwareDownloadError(
                f"Failed to download software (HTTP {exc.code})."
            ) from exc
        except URLError as exc:
            logger.error("Download network error: %s", exc)
            raise SoftwareDownloadError(
                "Unable to reach the download server."
            ) from exc

        # Verify SHA-256 if provided.
        if expected_sha256:
            actual = self._sha256(destination)
            if actual.lower() != expected_sha256.lower():
                raise SoftwareDownloadError(
                    "Download checksum verification failed. Download may be corrupted or tampered."
                )

    @staticmethod
    def _sha256(path: Path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()

    @staticmethod
    def _safe_extract_archive(archive: Path, dest: Path) -> None:
        """Safely extract a zip or tar.gz archive, preventing path traversal."""
        try:
            if archive.suffix.lower() in (".zip", ".jar"):
                with zipfile.ZipFile(archive) as zip_file:
                    for member in zip_file.infolist():
                        member_path = Path(member.filename)
                        # Reject absolute paths and traversal.
                        if member_path.is_absolute() or ".." in member_path.parts:
                            raise SoftwareDownloadError(
                                "Unsafe archive entry detected during extraction."
                            )
                        target = (dest / member_path).resolve()
                        try:
                            target.relative_to(dest.resolve())
                        except ValueError:
                            raise SoftwareDownloadError(
                                "Unsafe archive entry detected during extraction."
                            )
                    zip_file.extractall(dest)
            elif archive.suffix.lower() in (".tar.gz", ".tgz"):
                with tarfile.open(archive, "r:gz") as tar:
                    for member in tar.getmembers():
                        member_path = Path(member.name)
                        if member_path.is_absolute() or ".." in member_path.parts:
                            raise SoftwareDownloadError(
                                "Unsafe archive entry detected during extraction."
                            )
                        target = (dest / member_path).resolve()
                        try:
                            target.relative_to(dest.resolve())
                        except ValueError:
                            raise SoftwareDownloadError(
                                "Unsafe archive entry detected during extraction."
                            )
                    tar.extractall(dest)
            else:
                raise SoftwareDownloadError(
                    f"Unsupported archive format: {archive.suffix}"
                )
        except (zipfile.BadZipFile, tarfile.TarError, OSError, EOFError) as exc:
            raise SoftwareDownloadError(
                f"Failed to extract archive: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Version listing
    # ------------------------------------------------------------------

    def get_vanilla_versions(self) -> list[SoftwareVersionInfo]:
        """Return available Minecraft vanilla versions from Mojang manifest."""
        from app.core.version_manifest import MojangManifestClient, MojangManifestError

        client = MojangManifestClient()
        try:
            versions = client.list_versions()
        except MojangManifestError as exc:
            raise SoftwareDownloadError(str(exc)) from exc

        return [
            SoftwareVersionInfo(
                id=v.id,
                type=v.type,
                release_time=v.release_time,
            )
            for v in versions
        ]

    def get_paper_versions(self) -> list[SoftwareVersionInfo]:
        """Return available Paper versions from the Fill v3 API.

        Uses https://fill.papermc.io/v3/projects/paper/versions which returns
        a list of version objects, each with a version.id field.
        """
        url = "https://fill.papermc.io/v3/projects/paper/versions"

        try:
            data = self._fetch_json(url)
        except SoftwareDownloadError:
            raise

        versions_list = data.get("versions", [])
        versions: list[str] = []
        if isinstance(versions_list, list):
            for entry in versions_list:
                if isinstance(entry, dict):
                    version_id = entry.get("version", {}).get("id", "")
                    if version_id:
                        versions.append(version_id)

        return [
            SoftwareVersionInfo(
                id=v,
                type="release",
                release_time=None,
            )
            for v in versions
        ]

    def get_fabric_versions(self) -> list[SoftwareVersionInfo]:
        """Return available Fabric versions from the Fabric Meta API."""
        import json
        import urllib.request
        from urllib.error import HTTPError, URLError

        url = "https://meta.fabricmc.net/v2/versions/loader"

        try:
            request = Request(
                url,
                headers={
                    "User-Agent": "Adreoz-Server-Manager/0.1.0",
                    "Accept": "application/json",
                },
            )
            with urlopen(request, timeout=self.timeout_seconds) as response:
                data = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise SoftwareDownloadError(
                f"Fabric API error: HTTP {exc.code}"
            ) from exc
        except URLError as exc:
            raise SoftwareDownloadError(
                "Unable to reach Fabric API."
            ) from exc
        except Exception as exc:
            logger.exception("Unexpected error fetching Fabric versions")
            raise SoftwareDownloadError(
                "Failed to fetch Fabric versions."
            ) from exc

        return [
            SoftwareVersionInfo(
                id=v.get("version", ""),
                type="release",
                release_time=None,
            )
            for v in data
        ]

    def get_forge_versions(self) -> list[SoftwareVersionInfo]:
        """Return available Forge versions from Forge metadata."""
        import json
        import urllib.request
        from urllib.error import HTTPError, URLError

        url = "https://maven.minecraftforge.net/net/minecraftforge/forge/_metadata/versions.json"

        try:
            request = Request(
                url,
                headers={
                    "User-Agent": "Adreoz-Server-Manager/0.1.0",
                    "Accept": "application/json",
                },
            )
            with urlopen(request, timeout=self.timeout_seconds) as response:
                data = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise SoftwareDownloadError(
                f"Forge API error: HTTP {exc.code}"
            ) from exc
        except URLError as exc:
            raise SoftwareDownloadError(
                "Unable to reach Forge metadata."
            ) from exc
        except Exception as exc:
            logger.exception("Unexpected error fetching Forge versions")
            raise SoftwareDownloadError(
                "Failed to fetch Forge versions."
            ) from exc

        versions = data.get("versions", [])
        return [
            SoftwareVersionInfo(
                id=v.get("version", ""),
                type="release",
                release_time=None,
            )
            for v in versions
        ]

    # ------------------------------------------------------------------
    # Build listing
    # ------------------------------------------------------------------

    def get_paper_builds(self, version: str) -> list[SoftwareBuildInfo]:
        """Return available Paper builds for a version from the Fill v3 API.

        Uses https://fill.papermc.io/v3/projects/paper/versions/{version}/builds
        which returns an array of build objects.
        """
        url = (
            f"https://fill.papermc.io/v3/projects/paper/versions/"
            f"{version}/builds"
        )

        try:
            data = self._fetch_json(url)
        except SoftwareDownloadError:
            raise

        builds = data if isinstance(data, list) else []
        return [
            SoftwareBuildInfo(
                id=str(b.get("id", "")),
                time=b.get("time"),
            )
            for b in builds
        ]

    def get_fabric_loader_versions(self, version: str) -> list[SoftwareBuildInfo]:
        """Return available Fabric loader versions for a Minecraft version."""
        import json
        import urllib.request
        from urllib.error import HTTPError, URLError

        url = f"https://meta.fabricmc.net/v2/versions/loader/{version}"

        try:
            request = Request(
                url,
                headers={
                    "User-Agent": "Adreoz-Server-Manager/0.1.0",
                    "Accept": "application/json",
                },
            )
            with urlopen(request, timeout=self.timeout_seconds) as response:
                data = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise SoftwareDownloadError(
                f"Fabric API error: HTTP {exc.code} for version {version}."
            ) from exc
        except URLError as exc:
            raise SoftwareDownloadError(
                "Unable to reach Fabric API."
            ) from exc
        except Exception as exc:
            logger.exception("Unexpected error fetching Fabric loader versions")
            raise SoftwareDownloadError(
                "Failed to fetch Fabric loader versions."
            ) from exc

        return [
            SoftwareBuildInfo(
                id=v["loader"]["version"],
                time=None,
            )
            for v in data
        ]

    def get_forge_builds(self, version: str) -> list[SoftwareBuildInfo]:
        """Return available Forge versions for a Minecraft version.

        Forge does not publish a public API for listing all Forge versions.
        Returns an empty list so callers can degrade gracefully. Users must
        specify the Forge version manually.
        """
        logger.warning(
            "Forge versions API is unavailable; no Forge builds listed for %s.",
            version,
        )
        return []
