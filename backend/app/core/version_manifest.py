"""Minecraft version manifest client.

Queries Mojang's official version manifest and version metadata endpoints to
discover available Minecraft releases and their official server download
metadata. No third-party download sites are used.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("asm.minecraft.manifest")

MOJANG_VERSION_MANIFEST_URL = "https://launchermeta.mojang.com/mc/game/version_manifest.json"
REQUEST_TIMEOUT_SECONDS = 30


@dataclass
class MinecraftVersionInfo:
    """A single Minecraft version entry from the manifest."""

    id: str
    type: str  # "release", "snapshot", "old_beta", "old_alpha"
    url: str  # version metadata URL
    time: Optional[str] = None
    release_time: Optional[str] = None


@dataclass
class MinecraftVersionMetadata:
    """Detailed metadata for a specific Minecraft version."""

    id: str
    type: str
    time: str
    release_time: str
    downloads: dict  # raw downloads block from Mojang
    server_download_url: Optional[str] = None
    server_download_sha1: Optional[str] = None
    server_download_size: Optional[int] = None
    java_version_major: Optional[int] = None


class MojangManifestError(RuntimeError):
    """Raised when the Mojang manifest cannot be fetched or parsed."""


class MojangManifestClient:
    """Client for Mojang's version manifest and version metadata."""

    def __init__(
        self,
        manifest_url: str = MOJANG_VERSION_MANIFEST_URL,
        timeout_seconds: int = REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        self.manifest_url = manifest_url
        self.timeout_seconds = timeout_seconds

    def _fetch_json(self, url: str) -> dict:
        """Fetch and parse JSON from a URL using only the stdlib."""

        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Adreoz-Server-Manager/0.1.0",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            logger.error("Mojang manifest request failed: %s %s", exc.code, url)
            raise MojangManifestError(
                f"Failed to fetch version metadata (HTTP {exc.code})."
            ) from exc
        except urllib.error.URLError as exc:
            logger.error("Mojang manifest network error: %s", exc)
            raise MojangManifestError(
                "Unable to reach Mojang version metadata. Check network connectivity."
            ) from exc
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Unexpected error fetching %s", url)
            raise MojangManifestError("Failed to fetch version metadata.") from exc

        try:
            return json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            logger.error("Mojang manifest returned invalid JSON from %s", url)
            raise MojangManifestError("Invalid version metadata from Mojang.") from exc

    def list_versions(self) -> list[MinecraftVersionInfo]:
        """Return all known Minecraft versions from the manifest."""

        data = self._fetch_json(self.manifest_url)
        versions = []
        for entry in data.get("versions", []):
            versions.append(
                MinecraftVersionInfo(
                    id=entry.get("id", ""),
                    type=entry.get("type", "unknown"),
                    url=entry.get("url", ""),
                    time=entry.get("time"),
                    release_time=entry.get("releaseTime"),
                )
            )
        return versions

    def list_releases(self) -> list[MinecraftVersionInfo]:
        """Return only release (non-snapshot) versions, newest first."""

        all_versions = self.list_versions()
        releases = [v for v in all_versions if v.type == "release"]
        releases.sort(key=lambda v: v.release_time or v.time or "", reverse=True)
        return releases

    def get_metadata(self, version_id: str) -> MinecraftVersionMetadata:
        """Fetch detailed metadata for a specific version."""

        # Find the version's metadata URL.
        version_info = None
        for entry in self.list_versions():
            if entry.id == version_id:
                version_info = entry
                break
        if version_info is None:
            raise MojangManifestError(f"Minecraft version {version_id} not found.")

        data = self._fetch_json(version_info.url)

        downloads = data.get("downloads", {})
        server_download = downloads.get("server", {})
        java_version = data.get("javaVersion", {})
        java_major = java_version.get("majorVersion")

        return MinecraftVersionMetadata(
            id=data.get("id", version_id),
            type=data.get("type", version_info.type),
            time=data.get("time", version_info.time or ""),
            release_time=data.get("releaseTime", version_info.release_time or ""),
            downloads=downloads,
            server_download_url=server_download.get("url"),
            server_download_sha1=server_download.get("sha1"),
            server_download_size=server_download.get("size"),
            java_version_major=java_major,
        )
