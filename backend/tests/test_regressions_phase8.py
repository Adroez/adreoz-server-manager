"""Regression tests for the Phase 8 security and reliability fixes.

Every test here exists because a specific defect was found in the audit. They
are intentionally narrow: one behaviour per test, exercising the real code path
(route, service, or process manager) rather than a mock of it.
"""

from __future__ import annotations

import asyncio
import io
import os
import subprocess
import tarfile
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.services import operations
from app.core.config import Settings
from app.core.console import MAX_CLIENTS_PER_SERVER, MAX_HISTORY_REPLAY, ConsoleManager
from app.core.jvm_args import JvmArgsError, parse_jvm_args, validate_jvm_args
from app.core.minecraft_software import (
    MAX_DOWNLOAD_BYTES,
    SoftwareDownloadError,
    SoftwareInstaller,
)
from app.core.process import MAX_LINE_CHARS, ProcessManager
from app.models.server import Server
from app.services import servers as servers_service
from app.services.errors import ConflictError
from tests.conftest import PLAYER_PASSWORD, csrf_headers


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_server(
    client: TestClient,
    *,
    name: str,
    port: int,
    directory: str | None = None,
    jvm_args: str | None = None,
) -> dict:
    body = {
        "name": name,
        "software_type": "vanilla",
        "version": "1.20.1",
        "port": port,
        "max_players": 10,
        "online_mode": True,
        "difficulty": "normal",
        "gamemode": "survival",
        "view_distance": 10,
        "motd": "Regression server",
    }
    if directory is not None:
        body["directory"] = directory
    if jvm_args is not None:
        body["jvm_args"] = jvm_args
    return client.post("/api/servers", headers=csrf_headers(client), json=body)


def _set_status(app, server_id: int, status: str, process_id: int | None) -> None:
    with app.state.session_factory() as db:
        server = db.get(Server, server_id)
        assert server is not None
        server.status = status
        server.process_id = process_id
        db.commit()


def _get_status(app, server_id: int) -> str:
    with app.state.session_factory() as db:
        server = db.get(Server, server_id)
        assert server is not None
        return server.status


class _FakeStream:
    """Returns pre-baked bytes from ``read`` (drop-in for a subprocess pipe)."""

    def __init__(self, data: bytes) -> None:
        self._buffer = io.BytesIO(data)

    async def read(self, size: int = -1) -> bytes:
        return self._buffer.read(size)


class _FakeProcess:
    def __init__(self, stdout: bytes = b"", stderr: bytes = b"") -> None:
        self.pid = 12345
        self.returncode: int | None = None
        self.stdout = _FakeStream(stdout)
        self.stderr = _FakeStream(stderr)

    async def wait(self) -> int:
        assert self.returncode is not None
        return self.returncode


class _FakeWebSocket:
    """Hashable stand-in used by ConsoleManager."""

    def __init__(self) -> None:
        self.client_state = None
        self.messages: list[dict] = []

    async def send_json(self, payload: dict) -> None:
        self.messages.append(payload)

    async def close(self, code: int = 1000) -> None:
        self.closed = code


# ---------------------------------------------------------------------------
# C1/C2: server directory containment
# ---------------------------------------------------------------------------


def test_create_rejects_servers_base_directory(
    client: TestClient, admin_ready: dict
) -> None:
    response = _create_server(client, name="dot-dir", port=25601, directory=".")
    assert response.status_code == 400, response.text
    assert "base directory" in response.json()["detail"].lower()


def test_create_rejects_parent_traversal(
    client: TestClient, admin_ready: dict
) -> None:
    response = _create_server(client, name="escape", port=25602, directory="../escape")
    assert response.status_code == 400, response.text
    # The absolute host path must not leak back to the client.
    assert "configured servers directory" in response.json()["detail"]


def test_create_rejects_absolute_path_outside_base(
    client: TestClient, admin_ready: dict, tmp_path: Path
) -> None:
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    response = _create_server(
        client, name="absolute", port=25603, directory=str(outside)
    )
    assert response.status_code == 400, response.text


def test_create_rejects_symlink_escape(
    client: TestClient, admin_ready: dict, settings: Settings, tmp_path: Path
) -> None:
    settings.servers_dir.mkdir(parents=True, exist_ok=True)
    target = tmp_path / "outside-symlink-target"
    target.mkdir()
    link = settings.servers_dir / "linked"
    link.symlink_to(target, target_is_directory=True)

    response = _create_server(
        client, name="symlink", port=25604, directory="linked"
    )
    assert response.status_code == 400, response.text


def test_create_rejects_overlapping_directory(
    client: TestClient, admin_ready: dict
) -> None:
    first = _create_server(client, name="first-server", port=25605)
    assert first.status_code == 201, first.text
    assert Path(first.json()["relative_directory"]) == Path("first-server")

    nested = _create_server(
        client,
        name="nested-server",
        port=25606,
        directory="first-server/nested",
    )
    assert nested.status_code == 409, nested.text


def test_server_response_reports_relative_directory(
    client: TestClient, admin_ready: dict
) -> None:
    created = _create_server(client, name="relative-dir", port=25607)
    assert created.status_code == 201, created.text
    data = created.json()
    assert data["relative_directory"] == "relative-dir"

    detail = client.get(f"/api/servers/{data['id']}", headers=csrf_headers(client))
    assert detail.status_code == 200, detail.text
    assert detail.json()["relative_directory"] == "relative-dir"


# ---------------------------------------------------------------------------
# C4: archive extraction safety
# ---------------------------------------------------------------------------


def _make_tar(path: Path, name: str, *, link: bool = False) -> None:
    with tarfile.open(path, "w:gz") as tar:
        info = tarfile.TarInfo(name)
        if link:
            info.type = tarfile.SYMTYPE
            info.linkname = "/etc/passwd"
            tar.addfile(info)
        else:
            payload = b"data"
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))


def test_tar_extraction_rejects_traversal(tmp_path: Path) -> None:
    archive = tmp_path / "evil.tar.gz"
    _make_tar(archive, "../escaped.txt")
    dest = tmp_path / "dest"
    dest.mkdir()
    with pytest.raises(SoftwareDownloadError):
        SoftwareInstaller._safe_extract_archive(archive, dest)
    assert not (tmp_path / "escaped.txt").exists()


def test_tar_extraction_rejects_symlink_member(tmp_path: Path) -> None:
    archive = tmp_path / "link.tar.gz"
    _make_tar(archive, "innocent.txt", link=True)
    dest = tmp_path / "dest"
    dest.mkdir()
    with pytest.raises(SoftwareDownloadError):
        SoftwareInstaller._safe_extract_archive(archive, dest)


def test_zip_extraction_rejects_symlink_member(tmp_path: Path) -> None:
    archive = tmp_path / "link.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        info = zipfile.ZipInfo("link")
        # Unix symlink mode in the high 16 bits of external_attr.
        info.external_attr = (0o120777 << 16)
        zf.writestr(info, "/etc/passwd")
    dest = tmp_path / "dest"
    dest.mkdir()
    with pytest.raises(SoftwareDownloadError):
        SoftwareInstaller._safe_extract_archive(archive, dest)


# ---------------------------------------------------------------------------
# H4: control characters in file names + root protection
# ---------------------------------------------------------------------------


def test_file_name_with_control_char_rejected(
    client: TestClient, admin_ready: dict
) -> None:
    server = _create_server(client, name="control-chars", port=25608).json()
    response = client.post(
        f"/api/servers/{server['id']}/files/folder",
        headers=csrf_headers(client),
        json={"path": "bad\x01name"},
    )
    assert response.status_code == 400, response.text


def test_delete_server_root_directory_rejected(
    client: TestClient, admin_ready: dict
) -> None:
    server = _create_server(client, name="root-delete", port=25609).json()
    response = client.request(
        "DELETE",
        f"/api/servers/{server['id']}/files",
        headers=csrf_headers(client),
        json={"path": "."},
    )
    assert response.status_code == 400, response.text
    assert Path(server["directory"]).exists()


def test_rename_server_root_directory_rejected(
    client: TestClient, admin_ready: dict
) -> None:
    server = _create_server(client, name="root-rename", port=25610).json()
    response = client.post(
        f"/api/servers/{server['id']}/files/rename",
        headers=csrf_headers(client),
        json={"old_path": ".", "new_path": "renamed"},
    )
    assert response.status_code == 400, response.text


# ---------------------------------------------------------------------------
# H1: intentional stop is not recorded as a crash
# ---------------------------------------------------------------------------


def _run_monitor(app, server_id: int, returncode: int, stop_requested: bool):
    manager = app.state.process_manager
    process = _FakeProcess()
    process.returncode = returncode

    async def scenario() -> None:
        handle = manager.register(server_id, process)
        handle.stop_requested = stop_requested
        # ``wait`` returns immediately because returncode is already set.
        await servers_service._monitor_process(
            server_id, process, manager, app.state.session_factory
        )
        assert handle.stdout_task is not None
        assert handle.stderr_task is not None
        await asyncio.gather(handle.stdout_task, handle.stderr_task)

    asyncio.run(scenario())


def test_stop_requested_exit_is_recorded_as_stopped(
    client: TestClient, admin_ready: dict, app
) -> None:
    server = _create_server(client, name="stop-graceful", port=25611).json()
    _set_status(app, server["id"], "running", 4242)
    _run_monitor(app, server["id"], -15, stop_requested=True)
    assert _get_status(app, server["id"]) == "stopped"


def test_unrequested_nonzero_exit_is_recorded_as_error(
    client: TestClient, admin_ready: dict, app
) -> None:
    server = _create_server(client, name="crash-nonzero", port=25612).json()
    _set_status(app, server["id"], "running", 4242)
    _run_monitor(app, server["id"], 1, stop_requested=False)
    assert _get_status(app, server["id"]) == "error"


# ---------------------------------------------------------------------------
# H2: startup reconciliation
# ---------------------------------------------------------------------------


def test_reconcile_marks_dead_pid_as_error(
    client: TestClient, admin_ready: dict, app
) -> None:
    server = _create_server(client, name="dead-pid", port=25613).json()
    _set_status(app, server["id"], "running", 999_999_999)
    with app.state.session_factory() as db:
        changed = servers_service.reconcile_processes(db, app.state.process_manager)
    assert server["id"] in changed
    assert _get_status(app, server["id"]) == "error"


def test_reconcile_stopping_without_process_becomes_stopped(
    client: TestClient, admin_ready: dict, app
) -> None:
    server = _create_server(client, name="stopping-gone", port=25614).json()
    _set_status(app, server["id"], "stopping", 999_999_998)
    with app.state.session_factory() as db:
        servers_service.reconcile_processes(db, app.state.process_manager)
    assert _get_status(app, server["id"]) == "stopped"


def test_reconcile_rejects_recycled_pid_from_other_program(
    client: TestClient, admin_ready: dict, app
) -> None:
    """The current Python PID is alive but was not launched for this server."""

    server = _create_server(client, name="pid-reuse", port=25615).json()
    _set_status(app, server["id"], "running", os.getpid())
    with app.state.session_factory() as db:
        servers_service.reconcile_processes(db, app.state.process_manager)
    assert _get_status(app, server["id"]) == "error"


def test_reconcile_keeps_genuinely_running_server(
    client: TestClient, admin_ready: dict, app
) -> None:
    server = _create_server(client, name="really-running", port=25616).json()
    directory = Path(server["directory"])
    directory.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(["sleep", "30"], cwd=directory)
    try:
        _set_status(app, server["id"], "running", proc.pid)
        with app.state.session_factory() as db:
            changed = servers_service.reconcile_processes(
                db, app.state.process_manager
            )
        assert server["id"] not in changed
        assert _get_status(app, server["id"]) == "running"
    finally:
        proc.terminate()
        proc.wait(timeout=10)


# ---------------------------------------------------------------------------
# H7: download cap without Content-Length
# ---------------------------------------------------------------------------


class _ChunkedResponse:
    """Response without a Content-Length header that keeps streaming data."""

    headers: dict[str, str] = {}

    def __init__(self, chunk: bytes, count: int) -> None:
        self._chunk = chunk
        self._remaining = count

    def read(self, _size: int) -> bytes:
        if self._remaining <= 0:
            return b""
        self._remaining -= 1
        return self._chunk

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        return None


def test_chunked_download_over_cap_is_rejected_and_cleaned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installer = SoftwareInstaller(timeout_seconds=1)
    destination = tmp_path / "server.jar"

    response = _ChunkedResponse(b"x" * 1024, count=50)
    monkeypatch.setattr(
        "app.core.minecraft_software.urlopen", lambda *_a, **_k: response
    )
    monkeypatch.setattr(
        "app.core.minecraft_software.MAX_DOWNLOAD_BYTES", 8 * 1024
    )

    with pytest.raises(SoftwareDownloadError):
        installer.download_file(
            "https://api.papermc.io/v2/projects/paper", destination
        )
    assert not destination.exists(), "partial download must be removed"


def test_download_cap_constant_is_reasonable() -> None:
    # Guards against an accidental change to an unbounded value.
    assert 0 < MAX_DOWNLOAD_BYTES <= 4 * 1024 * 1024 * 1024


# ---------------------------------------------------------------------------
# H9/H8: process output capture
# ---------------------------------------------------------------------------


def test_long_output_line_is_split_not_dropped() -> None:
    captured: list[str] = []

    def callback(_server_id: int, _stream: str, line: str) -> None:
        captured.append(line)

    async def scenario() -> None:
        manager = ProcessManager()
        process = _FakeProcess(stdout=b"a" * (MAX_LINE_CHARS * 2 + 10) + b"\n")
        handle = manager.register(1, process, output_callback=callback)
        assert handle.stdout_task is not None
        assert handle.stderr_task is not None
        await asyncio.gather(handle.stdout_task, handle.stderr_task)

    asyncio.run(scenario())
    assert len(captured) >= 3
    assert all(len(chunk) <= MAX_LINE_CHARS for chunk in captured)
    assert "".join(captured) == "a" * (MAX_LINE_CHARS * 2 + 10)


def test_async_output_callback_is_awaited() -> None:
    captured: list[str] = []

    async def callback(_server_id: int, _stream: str, line: str) -> None:
        await asyncio.sleep(0)
        captured.append(line)

    async def scenario() -> None:
        manager = ProcessManager()
        process = _FakeProcess(stdout=b"first\nsecond\n")
        handle = manager.register(2, process, output_callback=callback)
        assert handle.stdout_task is not None
        assert handle.stderr_task is not None
        await asyncio.gather(handle.stdout_task, handle.stderr_task)

    asyncio.run(scenario())
    assert captured == ["first", "second"]


# ---------------------------------------------------------------------------
# H10: cross-process operation locking
# ---------------------------------------------------------------------------


def test_file_lock_is_exclusive_and_released(tmp_path: Path) -> None:
    operations.configure_lock_directory(tmp_path / "locks")
    try:
        fd = operations._acquire_file_lock(7)
        assert fd is not None
        with pytest.raises(ConflictError):
            operations._acquire_file_lock(7)
        operations._release_file_lock(fd)
        fd_again = operations._acquire_file_lock(7)
        assert fd_again is not None
        operations._release_file_lock(fd_again)
    finally:
        operations.configure_lock_directory(None)


def test_stop_is_allowed_while_another_worker_holds_the_lock(
    tmp_path: Path,
) -> None:
    """A stuck start/backup in another worker must not block Stop."""

    operations.configure_lock_directory(tmp_path / "locks")
    try:
        foreign_fd = operations._acquire_file_lock(8)
        assert foreign_fd is not None
        # Would raise ConflictError for a non-stop operation; stop proceeds.
        with operations.acquire_operation_lock(8, operations.OperationType.STOP):
            pass
        operations._release_file_lock(foreign_fd)
    finally:
        operations.configure_lock_directory(None)


# ---------------------------------------------------------------------------
# JVM argument validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "-javaagent:/tmp/evil.jar",
        "-agentlib:jdwp=transport=dt_socket",
        "@argfile.txt",
        "-Xbootclasspath/p:/tmp/x.jar",
        "-Djava.class.path=/tmp/x",
        "-XX:OnError=/tmp/hook.sh",
        "-XX:OnOutOfMemoryError=/tmp/hook.sh",
        "-XX:HeapDumpPath=/tmp/out",
        "-Xrunjdwp:transport=dt_socket",
        '"quoted"',
        "not-a-flag",
        "-Xmx2G\n-XX:+UseG1GC",
    ],
)
def test_parse_jvm_args_rejects_unsafe_or_malformed(value: str) -> None:
    with pytest.raises(JvmArgsError):
        parse_jvm_args(value)


def test_parse_jvm_args_normalises_whitespace() -> None:
    parsed = parse_jvm_args("  -Xmx2G   -XX:+UseG1GC  ")
    assert parsed.tokens == ("-Xmx2G", "-XX:+UseG1GC")
    assert parsed.normalized == "-Xmx2G -XX:+UseG1GC"


def test_parse_jvm_args_rejects_too_many_tokens() -> None:
    with pytest.raises(JvmArgsError):
        parse_jvm_args(" ".join(f"-Dx{i}=1" for i in range(40)))


def test_validate_jvm_args_returns_none_when_blank() -> None:
    assert validate_jvm_args(None) is None
    assert validate_jvm_args("   ") is None


def test_jvm_args_api_round_trip(client: TestClient, admin_ready: dict) -> None:
    server = _create_server(client, name="jvm-api", port=25617).json()
    response = client.patch(
        f"/api/servers/{server['id']}",
        headers=csrf_headers(client),
        json={"jvm_args": "  -Xmx2G   -XX:+UseG1GC "},
    )
    assert response.status_code == 200, response.text
    assert response.json()["jvm_args"] == "-Xmx2G -XX:+UseG1GC"

    cleared = client.patch(
        f"/api/servers/{server['id']}",
        headers=csrf_headers(client),
        json={"jvm_args": None},
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["jvm_args"] is None


def test_jvm_args_api_rejects_agent_flags(
    client: TestClient, admin_ready: dict
) -> None:
    server = _create_server(client, name="jvm-reject", port=25618).json()
    response = client.patch(
        f"/api/servers/{server['id']}",
        headers=csrf_headers(client),
        json={"jvm_args": "-javaagent:/tmp/evil.jar"},
    )
    assert response.status_code == 422, response.text


def test_player_cannot_access_another_players_server(
    client: TestClient, admin_ready: dict
) -> None:
    """JVM arguments and files are owner/admin only, never cross-tenant."""

    server = _create_server(client, name="admin-owned", port=25619).json()

    created_user = client.post(
        "/api/users",
        headers=csrf_headers(client),
        json={
            "username": "alice",
            "email": "alice@example.com",
            "password": PLAYER_PASSWORD,
            "role": "user",
        },
    )
    assert created_user.status_code == 201, created_user.text
    login = client.post(
        "/api/auth/login",
        json={"username": "alice", "password": PLAYER_PASSWORD},
    )
    assert login.status_code == 200, login.text

    detail = client.get(f"/api/servers/{server['id']}")
    assert detail.status_code == 403, detail.text

    patch = client.patch(
        f"/api/servers/{server['id']}",
        headers=csrf_headers(client),
        json={"jvm_args": "-Xmx1G"},
    )
    assert patch.status_code == 403, patch.text

    files = client.get(f"/api/servers/{server['id']}/files")
    assert files.status_code == 403, files.text


def test_launch_defaults_are_not_duplicated() -> None:
    assert servers_service._merge_jvm_defaults([]) == ["-Xms512M", "-Xmx1G"]
    assert servers_service._merge_jvm_defaults(["-Xmx2G"]) == ["-Xms512M"]
    assert servers_service._merge_jvm_defaults(["-Xms1G", "-Xmx2G"]) == []


def test_forge_launch_writes_user_jvm_args(tmp_path: Path) -> None:
    run_sh = tmp_path / "run.sh"
    run_sh.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")

    command = servers_service._forge_launch_command(
        tmp_path, "1.20.1", "47.2.0", "/usr/bin/java", ["-Xmx2G"]
    )
    assert command is not None
    cmd, _env = command
    assert cmd == ["bash", str(run_sh), "nogui"]
    content = (tmp_path / "user_jvm_args.txt").read_text(encoding="utf-8")
    assert "-Xmx2G" in content.splitlines()


def test_forge_launch_uses_jar_with_jvm_args(tmp_path: Path) -> None:
    jar = tmp_path / "forge-1.20.1-47.2.0-universal.jar"
    jar.write_bytes(b"stub")
    command = servers_service._forge_launch_command(
        tmp_path, "1.20.1", "47.2.0", "/usr/bin/java", ["-Xmx3G"]
    )
    assert command is not None
    cmd, _env = command
    assert cmd == [
        "/usr/bin/java",
        "-Xms512M",
        "-Xmx3G",
        "-jar",
        str(jar),
        "nogui",
    ]


# ---------------------------------------------------------------------------
# WebSocket console limits
# ---------------------------------------------------------------------------


def test_console_connection_limit_rejects_extra_clients() -> None:
    manager = ConsoleManager()

    async def scenario() -> None:
        accepted = [
            await manager.connect(1, _FakeWebSocket())
            for _ in range(MAX_CLIENTS_PER_SERVER)
        ]
        assert all(history is not None for history in accepted)

        rejected = await manager.connect(1, _FakeWebSocket())
        assert rejected is None
        assert manager.client_count(1) == MAX_CLIENTS_PER_SERVER

        # Disconnecting frees a slot again.
        clients = list(manager._clients[1])
        await manager.disconnect(1, clients[0])
        assert manager.client_count(1) == MAX_CLIENTS_PER_SERVER - 1
        assert await manager.connect(1, _FakeWebSocket()) is not None

    asyncio.run(scenario())


def test_console_history_replay_is_bounded() -> None:
    manager = ConsoleManager()

    async def scenario() -> list[tuple[str, str]]:
        for index in range(MAX_HISTORY_REPLAY + 50):
            await manager.broadcast(1, "stdout", f"line-{index}")
        history = await manager.connect(1, _FakeWebSocket())
        assert history is not None
        return history

    history = asyncio.run(scenario())
    assert len(history) == MAX_HISTORY_REPLAY
    # The newest lines survive; the oldest are dropped.
    assert history[-1] == ("stdout", f"line-{MAX_HISTORY_REPLAY + 49}")
