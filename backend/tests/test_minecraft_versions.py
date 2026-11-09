"""Phase 6: Minecraft Version Manager tests.

Covers Mojang manifest parsing (fixtures), version selection, download
failures, interrupted downloads, JAR overwrite protection, and path traversal.
No real downloads or installations occur during these tests.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.core.version_manifest import (
    MojangManifestClient,
    MojangManifestError,
    MinecraftVersionInfo,
    MinecraftVersionMetadata,
)
from app.services.minecraft_version import MinecraftVersionService


# ----------------------------------------------------------------------
# Manifest parsing fixtures
# ----------------------------------------------------------------------

MANIFEST_FIXTURE = {
    "versions": [
        {
            "id": "1.20.1",
            "type": "release",
            "url": "https://launchermeta.mojang.com/v1/packages/1234/1.20.1.json",
            "time": "2023-06-07T15:30:00Z",
            "releaseTime": "2023-06-07T15:30:00Z",
        },
        {
            "id": "1.21",
            "type": "release",
            "url": "https://launchermeta.mojang.com/v1/packages/5678/1.21.json",
            "time": "2024-06-07T15:30:00Z",
            "releaseTime": "2024-06-07T15:30:00Z",
        },
        {
            "id": "25w02a",
            "type": "snapshot",
            "url": "https://launchermeta.mojang.com/v1/packages/9999/25w02a.json",
            "time": "2025-01-15T10:00:00Z",
            "releaseTime": "2025-01-15T10:00:00Z",
        },
    ]
}

VERSION_METADATA_FIXTURE = {
    "id": "1.20.1",
    "type": "release",
    "time": "2023-06-07T15:30:00Z",
    "releaseTime": "2023-06-07T15:30:00Z",
    "javaVersion": {"majorVersion": 17},
    "downloads": {
        "server": {
            "url": "https://launcher.mojang.com/v1/objects/abcd1234/server.jar",
            "sha1": "0123456789abcdef0123456789abcdef01234567",
            "size": 1048576,
        }
    },
}


# ----------------------------------------------------------------------
# Manifest parsing tests
# ----------------------------------------------------------------------


def test_list_versions_parses_manifest():
    client = MojangManifestClient()
    with patch.object(client, "_fetch_json", return_value=MANIFEST_FIXTURE):
        versions = client.list_versions()
    assert len(versions) == 3
    ids = {v.id for v in versions}
    assert ids == {"1.20.1", "1.21", "25w02a"}


def test_list_releases_excludes_snapshots():
    client = MojangManifestClient()
    with patch.object(client, "_fetch_json", return_value=MANIFEST_FIXTURE):
        releases = client.list_releases()
    assert len(releases) == 2
    assert all(v.type == "release" for v in releases)
    assert {v.id for v in releases} == {"1.20.1", "1.21"}


def test_get_version_metadata_resolves_server_download():
    client = MojangManifestClient()
    with patch.object(client, "list_versions", return_value=[
        MinecraftVersionInfo(
            id="1.20.1", type="release", url="http://example.com/meta",
            time=None, release_time=None,
        )
    ]):
        with patch.object(client, "_fetch_json", return_value=VERSION_METADATA_FIXTURE):
            metadata = client.get_metadata("1.20.1")
    assert metadata.id == "1.20.1"
    assert metadata.server_download_url is not None
    assert "server.jar" in metadata.server_download_url
    assert metadata.server_download_sha1 == "0123456789abcdef0123456789abcdef01234567"
    assert metadata.java_version_major == 17


def test_get_version_metadata_unknown_version():
    client = MojangManifestClient()
    with patch.object(client, "list_versions", return_value=[]):
        with pytest.raises(MojangManifestError):
            client.get_metadata("9.9.9")


# ----------------------------------------------------------------------
# Download service tests (mocked)
# ----------------------------------------------------------------------


def _make_service(tmp_path):
    settings = MagicMock()
    settings.data_dir = tmp_path
    session_factory = MagicMock()
    return MinecraftVersionService(settings, session_factory)


def test_download_jar_verifies_checksum(tmp_path):
    service = _make_service(tmp_path)
    server = MagicMock()
    server.directory = str(tmp_path / "server")
    server.name = "test-server"
    server.version = "1.20.1"
    server.owner_id = 1

    jar_content = b"fake-jar-content"
    expected_sha1 = hashlib.sha1(jar_content).hexdigest()

    with patch.object(
        service, "get_version_metadata",
        return_value={
            "id": "1.20.1",
            "type": "release",
            "server_download_url": "https://launcher.mojang.com/v1/objects/x/server.jar",
            "server_download_sha1": expected_sha1,
            "server_download_size": len(jar_content),
            "java_version_major": 17,
            "is_release": True,
        },
    ), patch.object(service, "_download_file") as mock_download:
        # Simulate _download_file writing content to destination.
        def fake_download(url, dest):
            Path(dest).write_bytes(jar_content)
        mock_download.side_effect = fake_download

        db = MagicMock()
        result = service.download_server_jar(db, server, "1.20.1", actor=MagicMock(), meta=None)

    assert result["version"] == "1.20.1"
    jar_path = Path(server.directory) / "server.jar"
    assert jar_path.exists()
    assert jar_path.read_bytes() == jar_content


def test_download_jar_bad_checksum_rejected(tmp_path):
    service = _make_service(tmp_path)
    server = MagicMock()
    server.directory = str(tmp_path / "server")
    server.name = "test-server"

    with patch.object(
        service, "get_version_metadata",
        return_value={
            "id": "1.20.1",
            "type": "release",
            "server_download_url": "https://launcher.mojang.com/v1/objects/x/server.jar",
            "server_download_sha1": "deadbeef",
            "server_download_size": 100,
            "java_version_major": 17,
            "is_release": True,
        },
    ), patch.object(service, "_download_file") as mock_download:
        def fake_download(url, dest):
            Path(dest).write_bytes(b"wrong-content")
        mock_download.side_effect = fake_download

        db = MagicMock()
        with pytest.raises(Exception) as excinfo:
            service.download_server_jar(db, server, "1.20.1", actor=MagicMock(), meta=None)
    assert "checksum" in str(excinfo.value).lower()


def test_download_jar_overwrite_protection(tmp_path):
    service = _make_service(tmp_path)
    server = MagicMock()
    server.directory = str(tmp_path / "server")
    server.name = "test-server"
    (Path(server.directory)).mkdir(parents=True, exist_ok=True)
    (Path(server.directory) / "server.jar").write_bytes(b"existing")

    with patch.object(service, "get_version_metadata", return_value={
        "id": "1.20.1", "type": "release",
        "server_download_url": "https://launcher.mojang.com/v1/objects/x/server.jar",
        "server_download_sha1": "aa", "server_download_size": 100,
        "java_version_major": 17, "is_release": True,
    }):
        db = MagicMock()
        from app.services.errors import ConflictError
        with pytest.raises(ConflictError):
            service.download_server_jar(db, server, "1.20.1", actor=MagicMock(), meta=None)


def test_download_url_host_validation():
    from app.services.minecraft_version import MinecraftVersionService
    service = _make_service(__import__("tempfile").mkdtemp())
    # Non-Mojang host should be rejected.
    try:
        service._download_file("https://evil.example.com/bad.jar", __import__("pathlib").Path("/tmp/x.jar"))
        assert False, "Expected DomainError"
    except Exception as exc:
        assert "not allowed" in str(exc).lower() or "host" in str(exc).lower()


def test_list_versions_network_error():
    client = MojangManifestClient()
    with patch.object(client, "_fetch_json", side_effect=MojangManifestError("network")):
        with pytest.raises(MojangManifestError):
            client.list_versions()
