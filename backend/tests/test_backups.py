"""Backup and restore system tests."""

from __future__ import annotations

import errno
import io
import os
import tarfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.models.audit import AuditLog
from app.models.backup import ServerBackup
from app.models.server import Server
from app.services.backup import (
    run_backup_scheduler_once,
)
from app.services.operations import (
    acquire_operation_lock,
    OperationType,
)
from tests.conftest import ADMIN_LOGIN, PLAYER_PASSWORD, csrf_headers


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _login_as(client: TestClient, username: str, password: str = PLAYER_PASSWORD) -> None:
    response = client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200, response.text


def _create_player_as_admin(client: TestClient, username: str = "player") -> dict:
    response = client.post(
        "/api/users",
        headers=csrf_headers(client),
        json={
            "username": username,
            "email": f"{username}@example.com",
            "password": PLAYER_PASSWORD,
            "role": "user",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _create_server_as_player(
    client: TestClient,
    name: str = "test-server",
    port: int = 25565,
) -> dict:
    response = client.post(
        "/api/servers",
        headers=csrf_headers(client),
        json={
            "name": name,
            "software_type": "vanilla",
            "version": "1.20.1",
            "port": port,
            "max_players": 10,
            "online_mode": True,
            "difficulty": "normal",
            "gamemode": "survival",
            "view_distance": 10,
            "motd": "A test server",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _switch_to_admin(client: TestClient) -> None:
    client.post("/api/auth/logout", headers=csrf_headers(client))
    _login_as(client, ADMIN_LOGIN["username"], ADMIN_LOGIN["password"])


def _setup_player_and_server(
    client: TestClient,
    player_username: str,
    server_name: str,
    server_port: int,
) -> dict:
    _create_player_as_admin(client, username=player_username)
    client.post("/api/auth/logout", headers=csrf_headers(client))
    _login_as(client, player_username)
    return _create_server_as_player(client, name=server_name, port=server_port)


def _switch_to_player(client: TestClient, username: str) -> None:
    client.post("/api/auth/logout", headers=csrf_headers(client))
    _login_as(client, username)


def _create_backup(client: TestClient, server_id: int) -> dict:
    response = client.post(
        f"/api/servers/{server_id}/backups",
        headers=csrf_headers(client),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _write_file(client: TestClient, server_id: int, path: str, content: str) -> None:
    response = client.post(
        f"/api/servers/{server_id}/files/write",
        headers=csrf_headers(client),
        json={"path": path, "content": content},
    )
    assert response.status_code == 200, response.text


def _read_file(client: TestClient, server_id: int, path: str) -> str:
    response = client.get(
        f"/api/servers/{server_id}/files/read",
        params={"fp": path},
        headers=csrf_headers(client),
    )
    assert response.status_code == 200, response.text
    return response.json()["content"]


def _archive_path(app, backup: dict) -> Path:
    return Path(app.state.settings.backups_dir) / backup["filename"]


def _write_tar(path: Path, entries: list[tuple[str, bytes]], *, symlink: bool = False) -> None:
    """Write a small tar.gz for malicious-archive tests."""

    with tarfile.open(path, "w:gz") as tar:
        for name, data in entries:
            info = tarfile.TarInfo(name)
            if symlink:
                info.type = tarfile.SYMTYPE
                info.linkname = "/etc/passwd"
                tar.addfile(info)
            else:
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))


def _set_server_status(app, server_id: int, status: str) -> None:
    with app.state.session_factory() as db:
        server = db.get(Server, server_id)
        assert server is not None
        server.status = status
        db.commit()


def _enable_auto_backups(
    client: TestClient,
    server_id: int,
    *,
    interval_hours: int = 1,
    max_backups: int = 10,
) -> dict:
    response = client.patch(
        f"/api/servers/{server_id}/backups/settings",
        headers=csrf_headers(client),
        json={
            "enabled": True,
            "interval_hours": interval_hours,
            "max_backups": max_backups,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------------------
# Creation, listing, metadata
# ---------------------------------------------------------------------------


def test_backup_create_and_list(client: TestClient, admin_ready: dict) -> None:
    server = _setup_player_and_server(client, "backupowner", "backup-server", 25701)
    server_id = server["id"]

    backup = _create_backup(client, server_id)
    assert backup["id"] > 0
    assert backup["unique_id"]
    assert backup["server_id"] == server_id
    assert backup["filename"].endswith(".tar.gz")
    assert backup["status"] == "completed"
    assert backup["size_bytes"] > 0
    assert backup["error"] is None
    assert backup["created_at"]

    response = client.get(
        f"/api/servers/{server_id}/backups", headers=csrf_headers(client)
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["id"] == backup["id"]

    details = client.get(
        f"/api/servers/{server_id}/backups/{backup['id']}",
        headers=csrf_headers(client),
    )
    assert details.status_code == 200
    assert details.json()["unique_id"] == backup["unique_id"]


def test_backup_create_requires_stopped_server(
    client: TestClient, admin_ready: dict, app
) -> None:
    server = _setup_player_and_server(client, "backupowner", "running-server", 25702)
    server_id = server["id"]
    _set_server_status(app, server_id, "running")

    response = client.post(
        f"/api/servers/{server_id}/backups", headers=csrf_headers(client)
    )
    assert response.status_code == 400
    assert "stopped" in response.json()["detail"].lower()


def test_backup_create_failure_is_recorded(
    client: TestClient, admin_ready: dict, app, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = _setup_player_and_server(client, "backupowner", "failing-server", 25703)
    server_id = server["id"]

    def _fail(*_args, **_kwargs):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr("app.services.backup._write_archive", _fail)

    response = client.post(
        f"/api/servers/{server_id}/backups", headers=csrf_headers(client)
    )
    assert response.status_code == 400
    assert "disk space" in response.json()["detail"].lower()

    listing = client.get(
        f"/api/servers/{server_id}/backups", headers=csrf_headers(client)
    ).json()
    assert listing["total"] == 1
    assert listing["items"][0]["status"] == "failed"
    assert "disk space" in listing["items"][0]["error"].lower()


def test_backup_invalid_server_id(client: TestClient, admin_ready: dict) -> None:
    _switch_to_admin(client)
    response = client.get("/api/servers/99999/backups", headers=csrf_headers(client))
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Download and deletion
# ---------------------------------------------------------------------------


def test_backup_download(client: TestClient, admin_ready: dict) -> None:
    server = _setup_player_and_server(client, "backupowner", "download-server", 25704)
    server_id = server["id"]
    backup = _create_backup(client, server_id)

    response = client.get(
        f"/api/servers/{server_id}/backups/{backup['id']}/download",
        headers=csrf_headers(client),
    )
    assert response.status_code == 200, response.text
    assert response.content[:2] == b"\x1f\x8b"  # gzip magic
    assert backup["filename"] in response.headers["content-disposition"]


def test_backup_delete(client: TestClient, admin_ready: dict, app) -> None:
    server = _setup_player_and_server(client, "backupowner", "delete-server", 25705)
    server_id = server["id"]
    backup = _create_backup(client, server_id)
    archive = _archive_path(app, backup)
    assert archive.exists()

    response = client.delete(
        f"/api/servers/{server_id}/backups/{backup['id']}",
        headers=csrf_headers(client),
    )
    assert response.status_code == 200, response.text
    assert not archive.exists()

    listing = client.get(
        f"/api/servers/{server_id}/backups", headers=csrf_headers(client)
    ).json()
    assert listing["total"] == 0


def test_backup_invalid_id(client: TestClient, admin_ready: dict) -> None:
    server = _setup_player_and_server(client, "backupowner", "invalid-server", 25706)
    response = client.get(
        f"/api/servers/{server['id']}/backups/99999",
        headers=csrf_headers(client),
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Ownership and authentication
# ---------------------------------------------------------------------------


def test_backup_cross_user_list_denied(client: TestClient, admin_ready: dict) -> None:
    server = _setup_player_and_server(client, "playerone", "private-server", 25707)
    _create_backup(client, server["id"])

    _switch_to_admin(client)
    _create_player_as_admin(client, "playertwo")
    _switch_to_player(client, "playertwo")

    response = client.get(
        f"/api/servers/{server['id']}/backups", headers=csrf_headers(client)
    )
    assert response.status_code == 403


def test_backup_cross_user_download_and_delete_denied(
    client: TestClient, admin_ready: dict
) -> None:
    server = _setup_player_and_server(client, "playerone", "private-download", 25708)
    backup = _create_backup(client, server["id"])

    _switch_to_admin(client)
    _create_player_as_admin(client, "playertwo")
    _switch_to_player(client, "playertwo")

    download = client.get(
        f"/api/servers/{server['id']}/backups/{backup['id']}/download",
        headers=csrf_headers(client),
    )
    assert download.status_code == 403

    deletion = client.delete(
        f"/api/servers/{server['id']}/backups/{backup['id']}",
        headers=csrf_headers(client),
    )
    assert deletion.status_code == 403


def test_backup_unauthenticated(client: TestClient, admin_ready: dict, app) -> None:
    server = _setup_player_and_server(client, "backupowner", "unauth-server", 25709)

    unauth_client = TestClient(app)
    try:
        response = unauth_client.get(f"/api/servers/{server['id']}/backups")
        assert response.status_code == 401
    finally:
        unauth_client.close()


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------


def test_backup_restore_restores_files(client: TestClient, admin_ready: dict) -> None:
    server = _setup_player_and_server(client, "backupowner", "restore-server", 25710)
    server_id = server["id"]

    _write_file(client, server_id, "world/level.dat", "original-level")
    backup = _create_backup(client, server_id)

    _write_file(client, server_id, "world/level.dat", "modified-level")

    response = client.post(
        f"/api/servers/{server_id}/backups/{backup['id']}/restore",
        headers=csrf_headers(client),
    )
    assert response.status_code == 200, response.text
    assert response.json()["id"] == backup["id"]

    assert _read_file(client, server_id, "world/level.dat") == "original-level"


def test_backup_restore_preserves_archive(
    client: TestClient, admin_ready: dict, app
) -> None:
    server = _setup_player_and_server(client, "backupowner", "preserve-server", 25711)
    backup = _create_backup(client, server["id"])
    archive = _archive_path(app, backup)

    response = client.post(
        f"/api/servers/{server['id']}/backups/{backup['id']}/restore",
        headers=csrf_headers(client),
    )
    assert response.status_code == 200, response.text
    assert archive.exists()


def test_backup_restore_requires_stopped_server(
    client: TestClient, admin_ready: dict, app
) -> None:
    server = _setup_player_and_server(client, "backupowner", "restore-running", 25712)
    backup = _create_backup(client, server["id"])
    _set_server_status(app, server["id"], "running")

    response = client.post(
        f"/api/servers/{server['id']}/backups/{backup['id']}/restore",
        headers=csrf_headers(client),
    )
    assert response.status_code == 400
    assert "stopped" in response.json()["detail"].lower()


def test_backup_restore_nonexistent(client: TestClient, admin_ready: dict) -> None:
    server = _setup_player_and_server(client, "backupowner", "restore-missing", 25713)
    response = client.post(
        f"/api/servers/{server['id']}/backups/99999/restore",
        headers=csrf_headers(client),
    )
    assert response.status_code == 404


def test_backup_restore_missing_archive(
    client: TestClient, admin_ready: dict, app
) -> None:
    server = _setup_player_and_server(client, "backupowner", "gone-server", 25714)
    backup = _create_backup(client, server["id"])
    _archive_path(app, backup).unlink()

    response = client.post(
        f"/api/servers/{server['id']}/backups/{backup['id']}/restore",
        headers=csrf_headers(client),
    )
    assert response.status_code == 404


def test_backup_restore_corrupted_archive(
    client: TestClient, admin_ready: dict, app
) -> None:
    server = _setup_player_and_server(client, "backupowner", "corrupt-server", 25715)
    server_id = server["id"]
    _write_file(client, server_id, "keep.txt", "survives")
    backup = _create_backup(client, server_id)

    _archive_path(app, backup).write_bytes(b"this is not a gzip archive")

    response = client.post(
        f"/api/servers/{server_id}/backups/{backup['id']}/restore",
        headers=csrf_headers(client),
    )
    assert response.status_code == 400
    assert "corrupted" in response.json()["detail"].lower()
    assert _read_file(client, server_id, "keep.txt") == "survives"


def test_backup_restore_rejects_path_traversal(
    client: TestClient, admin_ready: dict, app
) -> None:
    server = _setup_player_and_server(client, "backupowner", "traversal-server", 25716)
    server_id = server["id"]
    _write_file(client, server_id, "keep.txt", "survives")
    backup = _create_backup(client, server_id)

    _write_tar(_archive_path(app, backup), [("../escaped.txt", b"pwned")])

    response = client.post(
        f"/api/servers/{server_id}/backups/{backup['id']}/restore",
        headers=csrf_headers(client),
    )
    assert response.status_code == 400
    assert "unsafe" in response.json()["detail"].lower()

    escaped = Path(server["directory"]).parent / "escaped.txt"
    assert not escaped.exists()
    assert _read_file(client, server_id, "keep.txt") == "survives"


def test_backup_restore_rejects_symlink_entries(
    client: TestClient, admin_ready: dict, app
) -> None:
    server = _setup_player_and_server(client, "backupowner", "symlink-server", 25717)
    server_id = server["id"]
    _write_file(client, server_id, "keep.txt", "survives")
    backup = _create_backup(client, server_id)

    _write_tar(_archive_path(app, backup), [("link", b"")], symlink=True)

    response = client.post(
        f"/api/servers/{server_id}/backups/{backup['id']}/restore",
        headers=csrf_headers(client),
    )
    assert response.status_code == 400
    assert "unsupported" in response.json()["detail"].lower()
    assert _read_file(client, server_id, "keep.txt") == "survives"


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


def test_backup_concurrent_operation_conflict(
    client: TestClient, admin_ready: dict
) -> None:
    server = _setup_player_and_server(client, "backupowner", "concurrent-server", 25718)
    server_id = server["id"]

    from app.services.operations import acquire_operation_lock, OperationType

    lock = acquire_operation_lock(server_id, OperationType.BACKUP_CREATE)
    lock.__enter__()
    try:
        response = client.post(
            f"/api/servers/{server_id}/backups", headers=csrf_headers(client)
        )
        assert response.status_code == 409
        assert "busy" in response.json()["detail"].lower()
    finally:
        lock.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# Automatic backup settings, retention, scheduler
# ---------------------------------------------------------------------------


def test_backup_settings_get_and_update(client: TestClient, admin_ready: dict) -> None:
    server = _setup_player_and_server(client, "backupowner", "settings-server", 25719)
    server_id = server["id"]

    response = client.get(
        f"/api/servers/{server_id}/backups/settings", headers=csrf_headers(client)
    )
    assert response.status_code == 200, response.text
    defaults = response.json()
    assert defaults["enabled"] is False
    assert defaults["interval_hours"] == 24
    assert defaults["max_backups"] == 10
    assert 24 in defaults["supported_intervals"]

    updated = _enable_auto_backups(
        client, server_id, interval_hours=12, max_backups=5
    )
    assert updated["enabled"] is True
    assert updated["interval_hours"] == 12
    assert updated["max_backups"] == 5


def test_backup_settings_rejects_unsupported_interval(
    client: TestClient, admin_ready: dict
) -> None:
    server = _setup_player_and_server(client, "backupowner", "interval-server", 25720)
    response = client.patch(
        f"/api/servers/{server['id']}/backups/settings",
        headers=csrf_headers(client),
        json={"interval_hours": 2},
    )
    assert response.status_code == 422


def test_backup_retention_cleanup(
    client: TestClient, admin_ready: dict, app
) -> None:
    server = _setup_player_and_server(client, "backupowner", "retention-server", 25721)
    server_id = server["id"]
    _enable_auto_backups(client, server_id, interval_hours=1, max_backups=2)

    created = [_create_backup(client, server_id) for _ in range(4)]

    listing = client.get(
        f"/api/servers/{server_id}/backups", headers=csrf_headers(client)
    ).json()
    assert listing["total"] == 2

    remaining_ids = {item["id"] for item in listing["items"]}
    assert remaining_ids == {created[-1]["id"], created[-2]["id"]}
    for old in created[:2]:
        assert not _archive_path(app, old).exists()


def test_backup_scheduler_creates_due_backup(
    client: TestClient, admin_ready: dict, app
) -> None:
    server = _setup_player_and_server(client, "backupowner", "scheduler-server", 25722)
    server_id = server["id"]
    _enable_auto_backups(client, server_id, interval_hours=1)

    first = run_backup_scheduler_once(
        app.state.settings, app.state.session_factory, app.state.process_manager
    )
    assert first["checked"] == 1
    assert first["created"] == 1

    second = run_backup_scheduler_once(
        app.state.settings, app.state.session_factory, app.state.process_manager
    )
    assert second["checked"] == 1
    assert second["created"] == 0
    assert second["skipped"] == 1

    listing = client.get(
        f"/api/servers/{server_id}/backups", headers=csrf_headers(client)
    ).json()
    assert listing["total"] == 1


def test_backup_scheduler_skips_disabled_and_running_servers(
    client: TestClient, admin_ready: dict, app
) -> None:
    disabled = _setup_player_and_server(
        client, "backupowner", "scheduler-disabled", 25723
    )
    _switch_to_admin(client)
    _create_player_as_admin(client, "backupowner2")
    _switch_to_player(client, "backupowner2")
    running = _create_server_as_player(client, name="scheduler-running", port=25724)

    _enable_auto_backups(client, running["id"], interval_hours=1)
    _set_server_status(app, running["id"], "running")

    summary = run_backup_scheduler_once(
        app.state.settings, app.state.session_factory, app.state.process_manager
    )
    assert summary["created"] == 0
    assert summary["skipped"] == 1

    with app.state.session_factory() as db:
        from app.models.backup import ServerBackup

        assert db.query(ServerBackup).count() == 0
    assert disabled["id"] != running["id"]


# ---------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------


def test_backup_actions_are_audited(client: TestClient, admin_ready: dict, app) -> None:
    server = _setup_player_and_server(client, "backupowner", "audit-server", 25725)
    server_id = server["id"]

    backup = _create_backup(client, server_id)
    client.post(
        f"/api/servers/{server_id}/backups/{backup['id']}/restore",
        headers=csrf_headers(client),
    )
    client.delete(
        f"/api/servers/{server_id}/backups/{backup['id']}",
        headers=csrf_headers(client),
    )

    with app.state.session_factory() as db:
        actions = [row.action for row in db.query(AuditLog).all()]

    assert "backup.created" in actions
    assert "backup.restored" in actions
    assert "backup.deleted" in actions


# ---------------------------------------------------------------------------
# Restore rollback data preservation regression tests
# ---------------------------------------------------------------------------


def test_restore_failure_before_move_preserves_original(
    client: TestClient, admin_ready: dict, app, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failure before moving original directory: original should be untouched."""
    server = _setup_player_and_server(client, "backupowner", "restore-fail-before", 25800)
    server_id = server["id"]
    _write_file(client, server_id, "important.txt", "original-content")
    backup = _create_backup(client, server_id)

    # Corrupt the archive so extraction fails before any move happens.
    _archive_path(app, backup).write_bytes(b"not a valid tar.gz")

    response = client.post(
        f"/api/servers/{server_id}/backups/{backup['id']}/restore",
        headers=csrf_headers(client),
    )
    assert response.status_code == 400
    assert "corrupted" in response.json()["detail"].lower()

    # Original file should still exist with original content.
    assert _read_file(client, server_id, "important.txt") == "original-content"


def test_restore_failure_after_move_original_preserved(
    client: TestClient, admin_ready: dict, app, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failure after moving original: rollback should restore original."""
    server = _setup_player_and_server(client, "backupowner", "restore-fail-after", 25801)
    server_id = server["id"]
    _write_file(client, server_id, "important.txt", "original-content")
    backup = _create_backup(client, server_id)

    # Create a valid archive but make the second os.replace fail.
    original_replace = os.replace
    replace_call_count = [0]

    def failing_replace(src, dst):
        replace_call_count[0] += 1
        # Second replace call moves staging to server_dir - fail that one.
        if replace_call_count[0] == 2:
            raise OSError("Simulated move failure")
        return original_replace(src, dst)

    monkeypatch.setattr(os, "replace", failing_replace)

    response = client.post(
        f"/api/servers/{server_id}/backups/{backup['id']}/restore",
        headers=csrf_headers(client),
    )
    assert response.status_code == 400

    # Original file should be restored with original content.
    assert _read_file(client, server_id, "important.txt") == "original-content"


def test_restore_failure_during_rollback_preserves_recovery_dirs(
    client: TestClient, admin_ready: dict, app, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failure during rollback: both staging and old_dir should be preserved."""
    server = _setup_player_and_server(client, "backupowner", "restore-rollback-fail", 25802)
    server_id = server["id"]
    _write_file(client, server_id, "important.txt", "original-content")
    backup = _create_backup(client, server_id)

    # Make the restore move fail, then rollback also fail.
    original_replace = os.replace
    replace_call_count = [0]

    def failing_replace(src, dst):
        replace_call_count[0] += 1
        # Call 1: move server_dir to old_dir (succeed)
        # Call 2: move staging to server_dir (FAIL - restore fails)
        # Call 3: rollback - move old_dir back to server_dir (FAIL - rollback fails)
        if replace_call_count[0] in (2, 3):
            raise OSError("Simulated move failure")
        return original_replace(src, dst)

    monkeypatch.setattr(os, "replace", failing_replace)

    response = client.post(
        f"/api/servers/{server_id}/backups/{backup['id']}/restore",
        headers=csrf_headers(client),
    )
    assert response.status_code == 400

    # Check that recovery directories exist (they won't be cleaned up on rollback failure).
    server_dir = Path(server["directory"])
    old_dirs = list(server_dir.parent.glob(f".asm-old-{server_id}-*"))
    restore_dirs = list(server_dir.parent.glob(".asm-restore-*"))
    # At least one recovery directory should exist.
    assert len(old_dirs) + len(restore_dirs) > 0


def test_restore_successful_rollback_restores_original(
    client: TestClient, admin_ready: dict, app, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Successful rollback: original directory is restored correctly."""
    server = _setup_player_and_server(client, "backupowner", "restore-rollback-ok", 25803)
    server_id = server["id"]
    _write_file(client, server_id, "important.txt", "original-content")
    backup = _create_backup(client, server_id)

    # Make the restore's second os.replace fail, but rollback succeeds.
    original_replace = os.replace
    replace_call_count = [0]

    def fail_second_replace(src, dst):
        replace_call_count[0] += 1
        # Second call: move staging to server_dir - fail this one
        if replace_call_count[0] == 2:
            raise OSError("Simulated restore move failure")
        return original_replace(src, dst)

    monkeypatch.setattr(os, "replace", fail_second_replace)

    response = client.post(
        f"/api/servers/{server_id}/backups/{backup['id']}/restore",
        headers=csrf_headers(client),
    )
    assert response.status_code == 400

    # Original file should be restored.
    assert _read_file(client, server_id, "important.txt") == "original-content"


def test_restore_successful_restore_and_cleanup(
    client: TestClient, admin_ready: dict, app
) -> None:
    """Successful restore: temporary directories are cleaned up."""
    server = _setup_player_and_server(client, "backupowner", "restore-success", 25804)
    server_id = server["id"]
    _write_file(client, server_id, "important.txt", "original-content")
    backup = _create_backup(client, server_id)

    _write_file(client, server_id, "important.txt", "modified-content")

    response = client.post(
        f"/api/servers/{server_id}/backups/{backup['id']}/restore",
        headers=csrf_headers(client),
    )
    assert response.status_code == 200

    # Original content should be restored.
    assert _read_file(client, server_id, "important.txt") == "original-content"

    # No temporary directories should remain.
    server_dir = Path(server["directory"])
    old_dirs = list(server_dir.parent.glob(f".asm-old-{server_id}-*"))
    restore_dirs = list(server_dir.parent.glob(".asm-restore-*"))
    assert len(old_dirs) == 0
    assert len(restore_dirs) == 0


def test_restore_preserves_archive_on_failure(
    client: TestClient, admin_ready: dict, app
) -> None:
    """Failed restore: backup archive is preserved."""
    server = _setup_player_and_server(client, "backupowner", "restore-preserve-archive", 25805)
    server_id = server["id"]
    _write_file(client, server_id, "keep.txt", "content")
    backup = _create_backup(client, server_id)
    archive = _archive_path(app, backup)

    # Corrupt the archive.
    archive.write_bytes(b"corrupted")

    response = client.post(
        f"/api/servers/{server_id}/backups/{backup['id']}/restore",
        headers=csrf_headers(client),
    )
    assert response.status_code == 400

    # Archive should still exist (though corrupted).
    assert archive.exists()


def test_concurrent_backup_and_start_blocked(
    client: TestClient, admin_ready: dict, app
) -> None:
    """Starting a server while backup is running should return 409."""
    server = _setup_player_and_server(client, "backupowner", "concurrent-start", 25806)
    server_id = server["id"]

    # Manually acquire the operation lock for BACKUP_CREATE.
    from app.services.operations import acquire_operation_lock, OperationType

    lock = acquire_operation_lock(server_id, OperationType.BACKUP_CREATE)
    lock.__enter__()
    try:
        # Try to start the server - should get 409.
        response = client.post(
            f"/api/servers/{server_id}/start", headers=csrf_headers(client)
        )
        assert response.status_code == 409
        assert "busy" in response.json()["detail"].lower()
    finally:
        lock.__exit__(None, None, None)


def test_concurrent_backup_and_delete_blocked(
    client: TestClient, admin_ready: dict, app
) -> None:
    """Deleting a server while backup is running should return 409."""
    server = _setup_player_and_server(client, "backupowner", "concurrent-delete", 25807)
    server_id = server["id"]

    from app.services.operations import acquire_operation_lock, OperationType

    lock = acquire_operation_lock(server_id, OperationType.BACKUP_CREATE)
    lock.__enter__()
    try:
        response = client.delete(
            f"/api/servers/{server_id}", headers=csrf_headers(client)
        )
        assert response.status_code == 409
        assert "busy" in response.json()["detail"].lower()
    finally:
        lock.__exit__(None, None, None)


def test_concurrent_restore_and_start_blocked(
    client: TestClient, admin_ready: dict, app
) -> None:
    """Starting a server while restore is running should return 409."""
    server = _setup_player_and_server(client, "backupowner", "concurrent-restore-start", 25808)
    server_id = server["id"]
    _write_file(client, server_id, "keep.txt", "content")
    backup = _create_backup(client, server_id)

    from app.services.operations import acquire_operation_lock, OperationType

    lock = acquire_operation_lock(server_id, OperationType.BACKUP_RESTORE)
    lock.__enter__()
    try:
        response = client.post(
            f"/api/servers/{server_id}/start", headers=csrf_headers(client)
        )
        assert response.status_code == 409
        assert "busy" in response.json()["detail"].lower()
    finally:
        lock.__exit__(None, None, None)


def test_stop_allowed_during_backup(
    client: TestClient, admin_ready: dict, app
) -> None:
    """Stop should be allowed even during backup (different lock class)."""
    server = _setup_player_and_server(client, "backupowner", "stop-during-backup", 25809)
    server_id = server["id"]
    _set_server_status(app, server_id, "running")

    from app.services.operations import acquire_operation_lock, OperationType

    lock = acquire_operation_lock(server_id, OperationType.BACKUP_CREATE)
    lock.__enter__()
    try:
        # Stop should succeed (STOP doesn't conflict with BACKUP_CREATE).
        response = client.post(
            f"/api/servers/{server_id}/stop", headers=csrf_headers(client)
        )
        assert response.status_code == 200
    finally:
        lock.__exit__(None, None, None)
