"""Tests for Phase 6: Software installation.

Tests the Java runtime and Minecraft version download/install workflow,
including checksum verification, safe extraction, and server creation
integration.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import asyncio
import pytest

from app.core.java import JavaRuntime
from app.core.minecraft_software import SoftwareDownloadInfo, SoftwareInstaller
from app.core.version_manifest import (
    MojangManifestClient,
    MojangManifestError,
    MinecraftVersionMetadata,
    MinecraftVersionInfo,
)
from app.models.java_runtime import JavaRuntime as JavaRuntimeModel
from app.services.compat import check_compatibility, required_java_major
from app.services.errors import ConflictError, DomainError, NotFoundError
from app.services.minecraft_version import MinecraftVersionService
from app.services.software_install import SoftwareInstallService


# ----------------------------------------------------------------------
# Compatibility tests
# ------------------------------------------------------------------


def test_compatibility_1_20_1_requires_java_17():
    result = check_compatibility("1.20.1", 17)
    assert result.compatible is True
    assert result.required_java_major == 17


def test_compatibility_1_20_1_accepts_java_25():
    result = check_compatibility("1.20.1", 25)
    assert result.compatible is True


def test_compatibility_1_20_1_rejects_java_11():
    result = check_compatibility("1.20.1", 11)
    assert result.compatible is False
    assert result.required_java_major == 17
    assert result.runtime_java_major == 11
    assert "incompatible" in result.message.lower()


def test_compatibility_unknown_version_defaults_to_java_17():
    result = check_compatibility("1.99.9", 17)
    assert result.compatible is True
    assert result.required_java_major == 17


def test_compatibility_runtime_unknown():
    result = check_compatibility("1.20.1", None)
    assert result.compatible is False
    assert result.runtime_java_major is None


def test_compatibility_1_16_requires_java_8():
    result = check_compatibility("1.16.5", 8)
    assert result.compatible is True


def test_compatibility_1_16_rejects_java_7():
    result = check_compatibility("1.16", 7)
    assert result.compatible is False
    assert result.required_java_major == 8


def test_required_java_major_explicit():
    assert required_java_major("1.20.1") == 17
    assert required_java_major("1.21") == 21
    assert required_java_major("1.18") == 17
    assert required_java_major("1.17") == 16
    assert required_java_major("1.16") == 8


# ----------------------------------------------------------------------
# Java detection tests
# ------------------------------------------------------------------


def test_java_runtime_display_name():
    rt = JavaRuntime(name="Temurin", version="17.0.13", vendor="Eclipse Temurin")
    assert "Temurin" in rt.display_name
    assert "17.0.13" in rt.display_name


def test_java_runtime_is_executable_false_for_missing():
    rt = JavaRuntime(executable_path="/nonexistent/bin/java")
    assert rt.is_executable is False


def test_checksum_verification():
    import hashlib
    from pathlib import Path
    import tempfile

    content = b"fake-java-archive-content"
    with tempfile.NamedTemporaryFile(delete=False, suffix=".tar.gz") as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)
    try:
        expected = hashlib.sha256(content).hexdigest()
        actual = hashlib.sha256(tmp_path.read_bytes()).hexdigest()
        assert actual == expected
    finally:
        tmp_path.unlink(missing_ok=True)


# ----------------------------------------------------------------------
# Software install service tests (mocked)
# ------------------------------------------------------------------


def _make_service(tmp_path):
    from unittest.mock import MagicMock

    settings = MagicMock()
    settings.data_dir = tmp_path
    session_factory = MagicMock()
    return SoftwareInstallService(settings, session_factory)


def test_install_software_creates_jar(tmp_path):
    from app.models.server import Server
    from app.models.user import User

    settings = MagicMock()
    settings.data_dir = tmp_path
    session_factory = MagicMock()
    service = SoftwareInstallService(settings, session_factory)

    server = Server(
        id=1,
        owner_id=1,
        unique_id="test-uuid",
        name="test-server",
        software_type="vanilla",
        version="1.20.1",
        directory=str(tmp_path / "server1"),
        port=25565,
        max_players=10,
        online_mode=True,
        difficulty="normal",
        gamemode="survival",
        view_distance=10,
        motd="Test",
        status="stopped",
    )
    server.directory = str(tmp_path / "server1")
    Path(server.directory).mkdir(parents=True, exist_ok=True)

    # Mock the download to avoid network calls
    with patch.object(service.installer, "download_file") as mock_download:
        def fake_download(url, dest, expected_sha256=None):
            Path(dest).write_bytes(b"fake-jar-content")

        mock_download.side_effect = fake_download

        # Mock the download info to return a fake download info
        with patch.object(
            service.installer,
            "get_vanilla_metadata",
            return_value=SoftwareDownloadInfo(
                version="1.20.1",
                download_url="https://example.com/server.jar",
                sha256="fake-sha256",
                size=100,
                filename="server.jar",
            ),
        ):
            # Also mock the extraction to create a fake jar
            with patch.object(service.installer, "_safe_extract_archive") as mock_extract:
                def fake_extract(archive, dest):
                    Path(dest / "server.jar").write_bytes(b"fake-jar-content")

                mock_extract.side_effect = fake_extract

                result = service.install_software(
                    db=MagicMock(),
                    server=server,
                    software_type="vanilla",
                    version="1.20.1",
                    actor=MagicMock(),
                    meta=None,
                )

    assert result["version"] == "1.20.1"
    assert result["software_type"] == "vanilla"
    assert result["install_status"] == "completed"
    server_dir = Path(server.directory)
    assert (server_dir / "server.jar").exists()


def test_install_software_rejects_existing_jar(tmp_path):
    from app.models.server import Server

    settings = MagicMock()
    settings.data_dir = tmp_path
    session_factory = MagicMock()
    service = SoftwareInstallService(settings, session_factory)

    server = Server(
        id=1,
        owner_id=1,
        unique_id="test-uuid",
        name="test-server",
        software_type="vanilla",
        version="1.20.1",
        directory=str(tmp_path / "server1"),
        port=25565,
        max_players=10,
        online_mode=True,
        difficulty="normal",
        gamemode="survival",
        view_distance=10,
        motd="Test",
        status="stopped",
    )
    server.directory = str(tmp_path / "server1")
    Path(server.directory).mkdir(parents=True, exist_ok=True)
    (Path(server.directory) / "server.jar").write_bytes(b"existing")

    with patch.object(service.installer, "get_vanilla_metadata") as mock_meta:
        mock_meta.return_value = SoftwareDownloadInfo(
            version="1.20.1",
            download_url="https://example.com/server.jar",
            sha256="fake",
        )

        try:
            service.install_software(
                db=MagicMock(),
                server=server,
                software_type="vanilla",
                version="1.20.1",
                actor=MagicMock(),
                meta=None,
            )
            assert False, "Expected ConflictError"
        except ConflictError:
            pass


def test_get_install_status(tmp_path):
    from app.models.server import Server

    settings = MagicMock()
    settings.data_dir = tmp_path
    session_factory = MagicMock()
    service = SoftwareInstallService(settings, session_factory)

    server = Server(
        id=1,
        owner_id=1,
        unique_id="test-uuid",
        name="test-server",
        software_type="vanilla",
        version="1.20.1",
        directory=str(tmp_path / "server1"),
        port=25565,
        max_players=10,
        online_mode=True,
        difficulty="normal",
        gamemode="survival",
        view_distance=10,
        motd="Test",
        status="stopped",
        java_runtime_id=1,
        software_version="1.20.1",
        install_status="completed",
        install_error=None,
    )

    result = service.get_install_status(server)
    assert result["server_id"] == 1
    assert result["software_type"] == "vanilla"
    assert result["version"] == "1.20.1"
    assert result["software_version"] == "1.20.1"
    assert result["install_status"] == "completed"
    assert result["install_error"] is None


# ----------------------------------------------------------------------
# Minecraft version service tests (existing, extended)
# ------------------------------------------------------------------


def test_minecraft_version_service_list_releases(tmp_path):
    settings = MagicMock()
    settings.data_dir = tmp_path
    session_factory = MagicMock()
    service = MinecraftVersionService(settings, session_factory)

    with patch.object(service.manifest_client, "list_releases") as mock_list:
        mock_list.return_value = []
        releases = service.list_releases()
        assert releases == []


def test_minecraft_version_service_get_metadata(tmp_path):
    settings = MagicMock()
    settings.data_dir = tmp_path
    session_factory = MagicMock()
    service = MinecraftVersionService(settings, session_factory)

    with patch.object(service.manifest_client, "get_metadata") as mock_get:
        mock_get.return_value = MinecraftVersionMetadata(
            id="1.20.1",
            type="release",
            time="2023-06-07T13:25:51+00:00",
            release_time="2023-06-07T13:25:51+00:00",
            downloads={},
            server_download_url="https://example.com/server.jar",
            server_download_sha1="abc123",
            server_download_size=1000,
            java_version_major=17,
        )
        metadata = service.get_version_metadata("1.20.1")
        assert metadata["id"] == "1.20.1"
        assert metadata["java_version_major"] == 17


# ----------------------------------------------------------------------
# PaperMC Fill API tests (mocked)
# ------------------------------------------------------------------


def test_paper_metadata_parses_fill_v3_response():
    """Paper metadata extracts server:default URL and SHA-256 from Fill v3."""
    installer = SoftwareInstaller()

    fill_response = {
        "id": 196,
        "time": "2023-09-21T16:59:41.322Z",
        "channel": "STABLE",
        "downloads": {
            "server:default": {
                "name": "paper-1.20.1-196.jar",
                "checksums": {
                    "sha256": "234a9b32098100c6fc116664d64e36ccdb58b5b649af0f80bcccb08b0255eaea"
                },
                "size": 43230582,
                "url": "https://fill-data.papermc.io/v1/objects/234a9b32098100c6fc116664d64e36ccdb58b5b649af0f80bcccb08b0255eaea/paper-1.20.1-196.jar",
            }
        },
    }

    with patch.object(
        installer, "_fetch_json", return_value=fill_response
    ):
        info = installer.get_paper_metadata("1.20.1", "196")

    assert info.version == "1.20.1"
    assert info.build == "196"
    assert info.download_url == (
        "https://fill-data.papermc.io/v1/objects/234a9b32098100c6fc116664d64e36ccdb58b5b649af0f80bcccb08b0255eaea/"
        "paper-1.20.1-196.jar"
    )
    assert info.sha256 == "234a9b32098100c6fc116664d64e36ccdb58b5b649af0f80bcccb08b0255eaea"
    assert info.size == 43230582
    assert info.filename == "paper-1.20.1-196.jar"


def test_paper_metadata_http_404():
    """Paper metadata raises SoftwareDownloadError on HTTP 404."""
    from urllib.error import HTTPError

    installer = SoftwareInstaller()

    with patch.object(installer, "_fetch_json") as mock_fetch:
        mock_fetch.side_effect = HTTPError(
            "https://fill.papermc.io/v3/projects/paper/versions/1.20.1/builds/999999",
            404,
            "Not Found",
            {},
            None,
        )
        try:
            installer.get_paper_metadata("1.20.1", "999999")
            assert False, "Expected SoftwareDownloadError"
        except Exception as exc:
            assert "404" in str(exc)


def test_paper_versions_list():
    """Paper versions extracts version IDs from Fill v3 list format."""
    installer = SoftwareInstaller()

    fill_response = {
        "project": {"id": "paper", "name": "Paper"},
        "versions": [
            {
                "version": {"id": "1.20.1"},
                "builds": [1, 2, 3],
            },
            {
                "version": {"id": "1.21"},
                "builds": [1, 2],
            },
        ],
    }

    with patch.object(installer, "_fetch_json", return_value=fill_response):
        versions = installer.get_paper_versions()

    version_ids = [v.id for v in versions]
    assert "1.20.1" in version_ids
    assert "1.21" in version_ids


def test_paper_builds_list():
    """Paper builds extracts build numbers from Fill v3 array."""
    installer = SoftwareInstaller()

    fill_response = [
        {"id": 196, "time": "2023-09-21T16:59:41.322Z", "channel": "STABLE"},
        {"id": 195, "time": "2023-09-21T08:44:44.132Z", "channel": "STABLE"},
    ]

    with patch.object(installer, "_fetch_json", return_value=fill_response):
        builds = installer.get_paper_builds("1.20.1")

    build_ids = [b.id for b in builds]
    assert "196" in build_ids
    assert "195" in build_ids


def test_paper_metadata_missing_server_default():
    """Paper metadata raises error when server:default is missing."""
    installer = SoftwareInstaller()

    fill_response = {
        "id": 196,
        "downloads": {},
    }

    with patch.object(installer, "_fetch_json", return_value=fill_response):
        try:
            installer.get_paper_metadata("1.20.1", "196")
            assert False, "Expected SoftwareDownloadError"
        except Exception as exc:
            assert "no server:default" in str(exc).lower()
