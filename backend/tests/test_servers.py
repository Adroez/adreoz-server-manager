"""Minecraft server lifecycle tests."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from fastapi.testclient import TestClient

from app.models.server import Server
from tests.conftest import ADMIN_LOGIN, PLAYER_PASSWORD, csrf_headers


class _FakeStream:
    """Empty async stream used by the fake subprocess."""

    async def readline(self) -> bytes:
        return b""


class _FakeProcess:
    """Minimal asyncio subprocess stand-in for lifecycle tests."""

    def __init__(self, exit_code: int = 0) -> None:
        self.pid = 4242
        self.returncode: int | None = None
        self.stdout = _FakeStream()
        self.stderr = _FakeStream()
        self._exit_code = exit_code
        self._exited = asyncio.Event()

    async def wait(self) -> int:
        await self._exited.wait()
        assert self.returncode is not None
        return self.returncode

    def _finish(self, code: int) -> None:
        if self.returncode is None:
            self.returncode = code
            self._exited.set()

    def terminate(self) -> None:
        self._finish(self._exit_code)

    def kill(self) -> None:
        self._finish(-9)


def _wait_for_status(app, server_id: int, expected: str, timeout: float = 10.0) -> str:
    """Poll the database until the server reaches ``expected`` or time runs out."""

    deadline = time.monotonic() + timeout
    status = ""
    while time.monotonic() < deadline:
        with app.state.session_factory() as db:
            server = db.get(Server, server_id)
            status = server.status if server is not None else ""
        if status == expected:
            return status
        time.sleep(0.05)
    return status


def _login_as(client: TestClient, username: str, password: str = PLAYER_PASSWORD) -> None:
    response = client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200, response.text


def _create_player_as_admin(client: TestClient, username: str = "player") -> dict:
    """Create a player using the currently authenticated admin client."""
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
    player_username: str,
    name: str = "test-server",
    port: int = 25565,
) -> dict:
    """Helper: already logged in as player, create a server, return the server JSON."""
    headers = csrf_headers(client)
    response = client.post(
        "/api/servers",
        headers=headers,
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
    """Full flow: create player, login as player, create server."""
    # Client must be authenticated as admin to create the player.
    _create_player_as_admin(client, username=player_username)
    # Switch to player.
    client.post("/api/auth/logout", headers=csrf_headers(client))
    _login_as(client, player_username)
    # Create the server.
    return _create_server_as_player(client, player_username, name=server_name, port=server_port)


def test_server_list_empty(client: TestClient, admin_ready: dict) -> None:
    response = client.get("/api/servers", headers=csrf_headers(client))
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 0
    assert payload["items"] == []


def test_server_create_and_list(client: TestClient, admin_ready: dict) -> None:
    server = _setup_player_and_server(
        client, player_username="serverowner", server_name="my-survival", server_port=25565
    )

    # List servers as the player (who owns it).
    response = client.get("/api/servers", headers=csrf_headers(client))
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["name"] == "my-survival"
    assert payload["items"][0]["software_type"] == "vanilla"
    assert payload["items"][0]["version"] == "1.20.1"
    assert payload["items"][0]["status"] == "stopped"
    assert payload["items"][0]["port"] == 25565


def test_server_create_duplicate_name(client: TestClient, admin_ready: dict) -> None:
    _setup_player_and_server(
        client, player_username="serverowner", server_name="dup-server", server_port=25565
    )

    # Try to create another server with the same name (case-insensitive).
    duplicate = client.post(
        "/api/servers",
        headers=csrf_headers(client),
        json={
            "name": "DUP-SERVER",
            "software_type": "vanilla",
            "version": "1.20.1",
            "port": 25566,
            "max_players": 10,
            "online_mode": True,
            "difficulty": "normal",
            "gamemode": "survival",
            "view_distance": 10,
            "motd": "Duplicate",
        },
    )
    assert duplicate.status_code == 409


def test_server_create_invalid_config(client: TestClient, admin_ready: dict) -> None:
    # Setup: create player and login as them.
    _create_player_as_admin(client, username="serverowner")
    client.post("/api/auth/logout", headers=csrf_headers(client))
    _login_as(client, "serverowner")

    # Invalid port (below 1024)
    bad_port = client.post(
        "/api/servers",
        headers=csrf_headers(client),
        json={
            "name": "bad-port",
            "software_type": "vanilla",
            "version": "1.20.1",
            "port": 80,
            "max_players": 10,
            "online_mode": True,
            "difficulty": "normal",
            "gamemode": "survival",
            "view_distance": 10,
            "motd": "Bad port",
        },
    )
    assert bad_port.status_code == 422

    # Invalid difficulty
    bad_diff = client.post(
        "/api/servers",
        headers=csrf_headers(client),
        json={
            "name": "bad-diff",
            "software_type": "vanilla",
            "version": "1.20.1",
            "port": 25567,
            "max_players": 10,
            "online_mode": True,
            "difficulty": "insane",
            "gamemode": "survival",
            "view_distance": 10,
            "motd": "Bad difficulty",
        },
    )
    assert bad_diff.status_code == 422

    # Invalid software type
    bad_software = client.post(
        "/api/servers",
        headers=csrf_headers(client),
        json={
            "name": "bad-software",
            "software_type": "bukkit",
            "version": "1.20.1",
            "port": 25568,
            "max_players": 10,
            "online_mode": True,
            "difficulty": "normal",
            "gamemode": "survival",
            "view_distance": 10,
            "motd": "Bad software",
        },
    )
    assert bad_software.status_code == 422


def test_server_ownership_enforcement(client: TestClient, admin_ready: dict) -> None:
    # Player creates a server (client is logged in as player after setup).
    server = _setup_player_and_server(
        client, player_username="serverowner", server_name="owned-server", server_port=25569
    )
    server_id = server["id"]

    # Switch to admin (different user).
    _switch_to_admin(client)
    admin_headers = csrf_headers(client)

    # Admin can see the server.
    response = client.get(f"/api/servers/{server_id}", headers=admin_headers)
    assert response.status_code == 200

    # Admin can update it.
    update = client.patch(
        f"/api/servers/{server_id}",
        headers=admin_headers,
        json={"motd": "Updated by admin"},
    )
    assert update.status_code == 200

    # Admin can delete it.
    delete = client.delete(f"/api/servers/{server_id}", headers=admin_headers)
    assert delete.status_code == 200


def test_regular_user_cannot_access_other_server(
    client: TestClient, admin_ready: dict, app
) -> None:
    # Player1 creates a server.
    player1_server = _setup_player_and_server(
        client, player_username="playerone", server_name="player1-server", server_port=25570
    )
    player1_server_id = player1_server["id"]

    # Switch to admin to create player2.
    _switch_to_admin(client)
    _create_player_as_admin(client, username="playertwo")

    # Login as player2.
    client.post("/api/auth/logout", headers=csrf_headers(client))
    _login_as(client, "playertwo")
    headers = csrf_headers(client)

    # Player2 tries to access player1's server.
    response = client.get(f"/api/servers/{player1_server_id}", headers=headers)
    assert response.status_code == 403

    # Player2 tries to delete player1's server.
    delete = client.delete(f"/api/servers/{player1_server_id}", headers=headers)
    assert delete.status_code == 403

    # Player2 tries to start player1's server.
    start = client.post(f"/api/servers/{player1_server_id}/start", headers=headers)
    assert start.status_code == 403


def test_server_get_details(client: TestClient, admin_ready: dict) -> None:
    server = _setup_player_and_server(
        client, player_username="serverowner", server_name="detail-server", server_port=25571
    )

    response = client.get(f"/api/servers/{server['id']}", headers=csrf_headers(client))
    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == server["id"]
    assert payload["unique_id"] == server["unique_id"]
    assert payload["name"] == "detail-server"
    assert payload["status"] == "stopped"


def test_server_update_config(client: TestClient, admin_ready: dict) -> None:
    server = _setup_player_and_server(
        client, player_username="serverowner", server_name="config-server", server_port=25572
    )

    update = client.patch(
        f"/api/servers/{server['id']}",
        headers=csrf_headers(client),
        json={
            "motd": "Updated MOTD",
            "max_players": 20,
            "difficulty": "hard",
        },
    )
    assert update.status_code == 200
    payload = update.json()
    assert payload["motd"] == "Updated MOTD"
    assert payload["max_players"] == 20
    assert payload["difficulty"] == "hard"


def test_server_update_while_running_rejected(client: TestClient, admin_ready: dict, app) -> None:
    server = _setup_player_and_server(
        client, player_username="serverowner", server_name="running-config", server_port=25573
    )

    # Manually set status to running to simulate a running server.
    with app.state.session_factory() as db:
        db_server = db.get(Server, server["id"])
        assert db_server is not None
        db_server.status = "running"
        db.commit()

    update = client.patch(
        f"/api/servers/{server['id']}",
        headers=csrf_headers(client),
        json={"motd": "Should fail"},
    )
    assert update.status_code == 400


def test_server_delete_only_when_stopped(client: TestClient, admin_ready: dict, app) -> None:
    server = _setup_player_and_server(
        client, player_username="serverowner", server_name="delete-server", server_port=25574
    )

    # Manually set status to running.
    with app.state.session_factory() as db:
        db_server = db.get(Server, server["id"])
        assert db_server is not None
        db_server.status = "running"
        db.commit()

    delete = client.delete(f"/api/servers/{server['id']}", headers=csrf_headers(client))
    assert delete.status_code == 400


def test_server_unknown_fields_rejected(client: TestClient, admin_ready: dict) -> None:
    # Setup: create player and login as them.
    _create_player_as_admin(client, username="serverowner")
    client.post("/api/auth/logout", headers=csrf_headers(client))
    _login_as(client, "serverowner")

    response = client.post(
        "/api/servers",
        headers=csrf_headers(client),
        json={
            "name": "unknown-fields",
            "software_type": "vanilla",
            "version": "1.20.1",
            "port": 25575,
            "max_players": 10,
            "online_mode": True,
            "difficulty": "normal",
            "gamemode": "survival",
            "view_distance": 10,
            "motd": "Test",
            "role": "admin",  # Unknown field
        },
    )
    assert response.status_code == 422


def test_server_create_unauthenticated(client: TestClient, admin_ready: dict) -> None:
    # No CSRF token - not authenticated at all.
    response = client.post(
        "/api/servers",
        json={
            "name": "no-auth",
            "software_type": "vanilla",
            "version": "1.20.1",
            "port": 25576,
            "max_players": 10,
            "online_mode": True,
            "difficulty": "normal",
            "gamemode": "survival",
            "view_distance": 10,
            "motd": "No auth",
        },
    )
    assert response.status_code == 403


def test_server_software_types_supported(client: TestClient, admin_ready: dict) -> None:
    """All four supported software types can be created."""
    ports = [25577, 25578, 25579, 25580]
    # Create all servers as admin to avoid auth-switching complexity.
    for i, software_type in enumerate(["vanilla", "paper", "fabric", "forge"]):
        response = client.post(
            "/api/servers",
            headers=csrf_headers(client),
            json={
                "name": f"{software_type}-server",
                "software_type": software_type,
                "version": "1.20.1",
                "port": ports[i],
                "max_players": 10,
                "online_mode": True,
                "difficulty": "normal",
                "gamemode": "survival",
                "view_distance": 10,
                "motd": f"A {software_type} server",
            },
        )
        assert response.status_code == 201, f"Failed to create {software_type}: {response.text}"

    response = client.get("/api/servers", headers=csrf_headers(client))
    assert response.status_code == 200
    payload = response.json()
    types = {item["software_type"] for item in payload["items"]}
    assert types == {"vanilla", "paper", "fabric", "forge"}


def test_server_start_requires_installed_jar(
    client: TestClient, admin_ready: dict
) -> None:
    server = _setup_player_and_server(
        client, player_username="serverowner", server_name="no-jar", server_port=25590
    )

    response = client.post(
        f"/api/servers/{server['id']}/start", headers=csrf_headers(client)
    )
    assert response.status_code == 400
    assert "server.jar" in response.json()["detail"]

    detail = client.get(f"/api/servers/{server['id']}", headers=csrf_headers(client))
    assert detail.json()["status"] == "stopped"


def test_server_start_stop_launches_and_monitors_process(
    client: TestClient, admin_ready: dict, app, monkeypatch
) -> None:
    server = _setup_player_and_server(
        client, player_username="serverowner", server_name="lifecycle", server_port=25591
    )
    with app.state.session_factory() as db:
        detail = db.get(Server, server["id"])
        assert detail is not None
        Path(detail.directory, "server.jar").write_bytes(b"")

    fake = _FakeProcess(exit_code=0)

    async def fake_exec(*args, **kwargs):
        return fake

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    start = client.post(
        f"/api/servers/{server['id']}/start", headers=csrf_headers(client)
    )
    assert start.status_code == 200, start.text
    assert start.json()["status"] == "running"

    process_manager = app.state.settings._process_manager
    handle = process_manager.get(server["id"])
    assert handle is not None
    assert handle.process is fake

    stop = client.post(
        f"/api/servers/{server['id']}/stop", headers=csrf_headers(client)
    )
    assert stop.status_code == 200, stop.text
    assert stop.json()["status"] in ("stopping", "stopped")
    assert _wait_for_status(app, server["id"], "stopped") == "stopped"

    detail = client.get(f"/api/servers/{server['id']}", headers=csrf_headers(client))
    assert detail.json()["status"] == "stopped"
    assert detail.json()["process_id"] is None


def test_server_restart_replaces_running_process(
    client: TestClient, admin_ready: dict, app, monkeypatch
) -> None:
    server = _setup_player_and_server(
        client, player_username="serverowner", server_name="restartable", server_port=25592
    )
    with app.state.session_factory() as db:
        detail = db.get(Server, server["id"])
        assert detail is not None
        Path(detail.directory, "server.jar").write_bytes(b"")

    launched: list[_FakeProcess] = []

    async def fake_exec(*args, **kwargs):
        fake = _FakeProcess(exit_code=0)
        launched.append(fake)
        return fake

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    start = client.post(
        f"/api/servers/{server['id']}/start", headers=csrf_headers(client)
    )
    assert start.status_code == 200, start.text

    restart = client.post(
        f"/api/servers/{server['id']}/restart", headers=csrf_headers(client)
    )
    assert restart.status_code == 200, restart.text
    assert restart.json()["status"] == "running"
    assert len(launched) == 2

    process_manager = app.state.settings._process_manager
    handle = process_manager.get(server["id"])
    assert handle is not None
    assert handle.process is launched[1]

    stop = client.post(
        f"/api/servers/{server['id']}/stop", headers=csrf_headers(client)
    )
    assert stop.status_code == 200, stop.text
    assert _wait_for_status(app, server["id"], "stopped") == "stopped"


def _create_java_runtime(db) -> int:
    """Insert a managed Java runtime record directly into the DB for testing."""

    from app.models.java_runtime import JavaRuntime as JavaRuntimeModel

    runtime = JavaRuntimeModel(
        name="Temurin 17",
        vendor="Eclipse Temurin",
        version="17.0.13+11",
        major_version=17,
        executable_path="/home/suriya/Adreoz Server Manager/data/runtimes/temurin/bin/java",
        source_url="https://example.com/temurin.tar.gz",
        checksum="deadbeef",
        checksum_algorithm="sha256",
        install_status="installed",
        is_managed=True,
    )
    db.add(runtime)
    db.flush()
    db.refresh(runtime)
    return runtime.id


def test_server_create_without_java_runtime(
    client: TestClient, admin_ready: dict
) -> None:
    """Creating a server without java_runtime_id defaults to system default."""

    server = _setup_player_and_server(
        client, player_username="serverowner", server_name="no-runtime", server_port=25593
    )

    detail = client.get(f"/api/servers/{server['id']}", headers=csrf_headers(client))
    assert detail.status_code == 200, detail.text
    assert detail.json()["java_runtime_id"] is None


def test_server_create_with_valid_java_runtime(
    client: TestClient, admin_ready: dict, app
) -> None:
    """Creating a server with a valid Java runtime ID persists the selection."""

    with app.state.session_factory() as db:
        runtime_id = _create_java_runtime(db)
        db.commit()

    server = _setup_player_and_server(
        client,
        player_username="serverowner",
        server_name="with-runtime",
        server_port=25594,
    )

    # Override the just-created server with a java_runtime_id via PATCH.
    update = client.patch(
        f"/api/servers/{server['id']}",
        headers=csrf_headers(client),
        json={"java_runtime_id": runtime_id},
    )
    assert update.status_code == 200, update.text
    assert update.json()["java_runtime_id"] == runtime_id

    detail = client.get(f"/api/servers/{server['id']}", headers=csrf_headers(client))
    assert detail.status_code == 200, detail.text
    assert detail.json()["java_runtime_id"] == runtime_id


def test_server_create_rejects_nonexistent_java_runtime(
    client: TestClient, admin_ready: dict
) -> None:
    """Creating a server with a nonexistent java_runtime_id is rejected."""

    server = _setup_player_and_server(
        client, player_username="serverowner", server_name="bad-runtime", server_port=25595
    )

    update = client.patch(
        f"/api/servers/{server['id']}",
        headers=csrf_headers(client),
        json={"java_runtime_id": 999999},
    )
    assert update.status_code == 404, update.text
    assert "not found" in update.json()["detail"].lower()


def test_server_start_validates_selected_runtime_exists(
    client: TestClient, admin_ready: dict, app
) -> None:
    """Starting a server with a missing Java runtime fails gracefully."""

    server = _setup_player_and_server(
        client,
        player_username="serverowner",
        server_name="runtime-delete",
        server_port=25596,
    )

    # Place a fake jar so start reaches runtime validation.
    with app.state.session_factory() as db:
        detail = db.get(Server, server["id"])
        Path(detail.directory, "server.jar").write_bytes(b"")

    # Directly exercise start_server with a mocked session that reports the
    # selected runtime as missing.
    from unittest.mock import patch

    import asyncio

    from app.services.servers import start_server
    from app.core.process import ProcessManager
    from app.models.java_runtime import JavaRuntime as JavaRuntimeModel
    from app.models.user import User

    process_manager = ProcessManager()

    async def _run_start():
        with app.state.session_factory() as db:
            server_row = db.get(Server, server["id"])
            actor = db.query(User).first()
            server_row.java_runtime_id = 999999

            original_get = type(db).get

            def fake_get(entity, ident, *args, **kwargs):
                if entity is JavaRuntimeModel:
                    return None
                return original_get(entity, ident, *args, **kwargs)

            with patch.object(type(db), "get", fake_get):
                try:
                    await start_server(
                        db,
                        server=server_row,
                        actor=actor,
                        process_manager=process_manager,
                        settings=app.state.settings,
                        session_factory=app.state.session_factory,
                        meta=None,
                    )
                    assert False, "Expected NotFoundError for missing runtime"
                except Exception as exc:
                    assert "not found" in str(exc).lower()

    asyncio.run(_run_start())
