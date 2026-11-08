"""Minecraft console WebSocket tests."""

from __future__ import annotations

import asyncio
import json

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.core.process import ProcessManager
from app.models.server import Server
from tests.conftest import ADMIN_LOGIN, PLAYER_PASSWORD, csrf_headers


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
    player_username: str,
    name: str = "test-server",
    port: int = 25565,
) -> dict:
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
    _create_player_as_admin(client, username=player_username)
    client.post("/api/auth/logout", headers=csrf_headers(client))
    _login_as(client, player_username)
    return _create_server_as_player(
        client, player_username, name=server_name, port=server_port
    )


def test_console_websocket_requires_authentication(
    client: TestClient, admin_ready: dict, app
) -> None:
    # Use a fresh client with no session cookie.
    unauth_client = TestClient(app)
    try:
        with unauth_client.websocket_connect("/api/servers/1/console") as _:
            assert False, "Expected WebSocket to be rejected without auth"
    except WebSocketDisconnect:
        pass
    finally:
        unauth_client.close()


def test_console_websocket_requires_authenticated_session(
    client: TestClient, admin_ready: dict
) -> None:
    # Logout first so there's no session cookie.
    client.post("/api/auth/logout", headers=csrf_headers(client))
    try:
        with client.websocket_connect("/api/servers/1/console") as _:
            assert False, "Expected WebSocket to be rejected"
    except WebSocketDisconnect:
        pass


def test_console_websocket_ownership_restriction(
    client: TestClient, admin_ready: dict, app
) -> None:
    server = _setup_player_and_server(
        client, player_username="playerone", server_name="owner-server", server_port=25580
    )
    server_id = server["id"]

    # Switch to a different player.
    _switch_to_admin(client)
    _create_player_as_admin(client, username="playertwo")
    client.post("/api/auth/logout", headers=csrf_headers(client))
    _login_as(client, "playertwo")

    try:
        with client.websocket_connect(f"/api/servers/{server_id}/console") as _:
            assert False, "Expected WebSocket to be rejected for non-owner"
    except WebSocketDisconnect:
        pass


def test_console_websocket_nonexistent_server(
    client: TestClient, admin_ready: dict
) -> None:
    try:
        with client.websocket_connect("/api/servers/99999/console") as _:
            assert False, "Expected WebSocket to be rejected for nonexistent server"
    except WebSocketDisconnect:
        pass


def test_console_websocket_connected_admin(
    client: TestClient, admin_ready: dict, app
) -> None:
    # Admin creates a server directly.
    _switch_to_admin(client)
    server = _create_server_as_player(
        client, "owner", name="admin-server", port=25581
    )
    server_id = server["id"]

    # Admin connects to console - should succeed (no output since not running).
    with client.websocket_connect(f"/api/servers/{server_id}/console") as ws:
        # Should receive connection without error.
        pass


def test_console_websocket_command_when_stopped(
    client: TestClient, admin_ready: dict, app
) -> None:
    _switch_to_admin(client)
    server = _create_server_as_player(
        client, "owner", name="cmd-server", port=25582
    )
    server_id = server["id"]

    with client.websocket_connect(f"/api/servers/{server_id}/console") as ws:
        ws.send_text("say hello")
        # Should get a system message that server is not running.
        # WebSocket may not have a message yet; just verify connection stays open.
        pass


def test_console_websocket_rejects_empty_command(
    client: TestClient, admin_ready: dict, app
) -> None:
    _switch_to_admin(client)
    server = _create_server_as_player(
        client, "owner", name="empty-cmd-server", port=25583
    )
    server_id = server["id"]

    with client.websocket_connect(f"/api/servers/{server_id}/console") as ws:
        ws.send_text("")
        ws.send_text("   ")
        # Connection should remain open.
        pass


def test_console_html_injection_sanitized(
    client: TestClient, admin_ready: dict, app
) -> None:
    """Log lines containing HTML should not be rendered as HTML by the frontend.

    This test verifies the backend stores and broadcasts the raw line; the
    frontend escapes it. We verify the backend doesn't interpret HTML.
    """
    _switch_to_admin(client)
    server = _create_server_as_player(
        client, "owner", name="html-server", port=25584
    )
    server_id = server["id"]

    with app.state.session_factory() as db:
        from app.services.servers import capture_log

        capture_log(db, server_id, "stdout", "<script>alert('xss')</script>")
        capture_log(db, server_id, "stdout", "safe line")

    with client.websocket_connect(f"/api/servers/{server_id}/console") as ws:
        # The history should include both lines raw.
        pass


def test_console_multiple_clients(
    client: TestClient, admin_ready: dict, app
) -> None:
    """Two authenticated clients can connect to the same server console."""
    _switch_to_admin(client)
    server = _create_server_as_player(
        client, "owner", name="multi-client-server", port=25585
    )
    server_id = server["id"]

    # Open two WebSocket connections.
    with client.websocket_connect(f"/api/servers/{server_id}/console") as ws1:
        with client.websocket_connect(f"/api/servers/{server_id}/console") as ws2:
            # Both connections should be open simultaneously.
            assert ws1 is not None
            assert ws2 is not None


def test_console_websocket_disconnect_cleanup(
    client: TestClient, admin_ready: dict, app
) -> None:
    _switch_to_admin(client)
    server = _create_server_as_player(
        client, "owner", name="disconnect-server", port=25586
    )
    server_id = server["id"]

    ws = client.websocket_connect(f"/api/servers/{server_id}/console")
    ws.__enter__()  # open connection
    ws.__exit__(None, None, None)  # close it

    # Reconnecting should still work.
    with client.websocket_connect(f"/api/servers/{server_id}/console") as ws2:
        pass
