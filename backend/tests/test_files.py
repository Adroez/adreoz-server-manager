"""Minecraft file manager and configuration editor tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

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


def test_file_manager_list_root(client: TestClient, admin_ready: dict, app) -> None:
    server = _setup_player_and_server(
        client, player_username="fileowner", server_name="file-server", server_port=25590
    )
    server_id = server["id"]

    response = client.get(
        f"/api/servers/{server_id}/files", headers=csrf_headers(client)
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["path"] == ""
    assert payload["parent"] is None
    assert payload["items"] == []


def test_file_manager_create_folder_and_list(client: TestClient, admin_ready: dict) -> None:
    server = _setup_player_and_server(
        client, player_username="fileowner", server_name="folder-server", server_port=25591
    )
    server_id = server["id"]

    # Create a folder.
    create = client.post(
        f"/api/servers/{server_id}/files/folder",
        headers=csrf_headers(client),
        json={"path": "world"},
    )
    assert create.status_code == 200, create.text
    assert create.json()["path"] == "world"

    # List root - should see the folder.
    response = client.get(
        f"/api/servers/{server_id}/files", headers=csrf_headers(client)
    )
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["name"] == "world"
    assert items[0]["type"] == "directory"


def test_file_manager_create_file_and_read(client: TestClient, admin_ready: dict) -> None:
    server = _setup_player_and_server(
        client, player_username="fileowner", server_name="file-read-server", server_port=25592
    )
    server_id = server["id"]

    # Create a text file.
    content = "Hello, Adreoz!"
    write = client.post(
        f"/api/servers/{server_id}/files/write",
        headers=csrf_headers(client),
        json={"path": "notes.txt", "content": content},
    )
    assert write.status_code == 200, write.text

    # Read the file back.
    read = client.get(
        f"/api/servers/{server_id}/files/read?fp=notes.txt",
        headers=csrf_headers(client),
    )
    assert read.status_code == 200, read.text
    payload = read.json()
    assert payload["content"] == content
    assert payload["mime_type"] == "text/plain"


def test_file_manager_rename_file(client: TestClient, admin_ready: dict) -> None:
    server = _setup_player_and_server(
        client, player_username="fileowner", server_name="rename-server", server_port=25593
    )
    server_id = server["id"]

    # Create a file.
    client.post(
        f"/api/servers/{server_id}/files/write",
        headers=csrf_headers(client),
        json={"path": "original.txt", "content": "original"},
    )

    # Rename it.
    rename = client.post(
        f"/api/servers/{server_id}/files/rename",
        headers=csrf_headers(client),
        json={"old_path": "original.txt", "new_path": "renamed.txt"},
    )
    assert rename.status_code == 200, rename.text

    # Verify the old name is gone and new name exists.
    read = client.get(
        f"/api/servers/{server_id}/files/read?fp=renamed.txt",
        headers=csrf_headers(client),
    )
    assert read.status_code == 200
    assert read.json()["content"] == "original"

    read_old = client.get(
        f"/api/servers/{server_id}/files/read?fp=original.txt",
        headers=csrf_headers(client),
    )
    assert read_old.status_code == 404


def test_file_manager_delete_file(client: TestClient, admin_ready: dict) -> None:
    server = _setup_player_and_server(
        client, player_username="fileowner", server_name="delete-server", server_port=25594
    )
    server_id = server["id"]

    # Create a file.
    client.post(
        f"/api/servers/{server_id}/files/write",
        headers=csrf_headers(client),
        json={"path": "temporary.txt", "content": "temp"},
    )

    # Delete it.
    delete = client.request(
        "DELETE",
        f"/api/servers/{server_id}/files",
        headers={**csrf_headers(client), "Content-Type": "application/json"},
        content='{"path": "temporary.txt"}',
    )
    assert delete.status_code == 200, delete.text

    # Verify it's gone.
    read = client.get(
        f"/api/servers/{server_id}/files/read?fp=temporary.txt",
        headers=csrf_headers(client),
    )
    assert read.status_code == 404


def test_file_manager_directory_traversal_rejected(client: TestClient, admin_ready: dict) -> None:
    server = _setup_player_and_server(
        client, player_username="fileowner", server_name="traversal-server", server_port=25595
    )
    server_id = server["id"]

    # Try directory traversal via write.
    write = client.post(
        f"/api/servers/{server_id}/files/write",
        headers=csrf_headers(client),
        json={"path": "../../../etc/passwd", "content": "malicious"},
    )
    assert write.status_code == 400, write.text

    # Try directory traversal via read.
    read = client.get(
        f"/api/servers/{server_id}/files/read?fp=../../../etc/passwd",
        headers=csrf_headers(client),
    )
    assert read.status_code == 400, read.text


def test_file_manager_cross_user_access_denied(
    client: TestClient, admin_ready: dict, app
) -> None:
    # Player1 creates a server with a file.
    server = _setup_player_and_server(
        client, player_username="playerone", server_name="private-server", server_port=25596
    )
    server_id = server["id"]

    client.post(
        f"/api/servers/{server_id}/files/write",
        headers=csrf_headers(client),
        json={"path": "secret.txt", "content": "private data"},
    )

    # Switch to admin to create player2.
    _switch_to_admin(client)
    _create_player_as_admin(client, username="playertwo")

    # Login as player2.
    client.post("/api/auth/logout", headers=csrf_headers(client))
    _login_as(client, "playertwo")
    headers = csrf_headers(client)

    # Player2 tries to read player1's file.
    read = client.get(
        f"/api/servers/{server_id}/files/read?fp=secret.txt",
        headers=headers,
    )
    assert read.status_code == 403

    # Player2 tries to write to player1's server.
    write = client.post(
        f"/api/servers/{server_id}/files/write",
        headers=headers,
        json={"path": "hack.txt", "content": "hacked"},
    )
    assert write.status_code == 403


def test_file_manager_invalid_filename(client: TestClient, admin_ready: dict) -> None:
    server = _setup_player_and_server(
        client, player_username="fileowner", server_name="invalid-filename-server", server_port=25597
    )
    server_id = server["id"]

    # Invalid filename with <> characters.
    write = client.post(
        f"/api/servers/{server_id}/files/write",
        headers=csrf_headers(client),
        json={"path": "bad<>name.txt", "content": "test"},
    )
    assert write.status_code == 400, write.text


def test_config_properties_read_and_write(client: TestClient, admin_ready: dict) -> None:
    server = _setup_player_and_server(
        client, player_username="fileowner", server_name="config-server", server_port=25598
    )
    server_id = server["id"]

    # Read properties (may be empty if server.properties doesn't exist yet).
    read = client.get(
        f"/api/servers/{server_id}/config/properties",
        headers=csrf_headers(client),
    )
    assert read.status_code == 200, read.text

    # Write properties.
    write = client.patch(
        f"/api/servers/{server_id}/config/properties",
        headers=csrf_headers(client),
        json={
            "properties": {
                "motd": "A custom MOTD",
                "difficulty": "hard",
                "max-players": "20",
                "pvp": "true",
            }
        },
    )
    assert write.status_code == 200, write.text
    payload = write.json()
    assert payload["properties"]["motd"] == "A custom MOTD"
    assert payload["properties"]["difficulty"] == "hard"
    assert payload["properties"]["max-players"] == "20"
    assert payload["properties"]["pvp"] == "true"

    # Read back and verify persistence.
    read2 = client.get(
        f"/api/servers/{server_id}/config/properties",
        headers=csrf_headers(client),
    )
    assert read2.status_code == 200
    assert read2.json()["properties"]["motd"] == "A custom MOTD"
    assert read2.json()["properties"]["difficulty"] == "hard"


def test_config_properties_invalid_values(client: TestClient, admin_ready: dict) -> None:
    server = _setup_player_and_server(
        client, player_username="fileowner", server_name="config-invalid-server", server_port=25599
    )
    server_id = server["id"]

    # Invalid difficulty.
    write = client.patch(
        f"/api/servers/{server_id}/config/properties",
        headers=csrf_headers(client),
        json={"properties": {"difficulty": "insane"}},
    )
    assert write.status_code == 400, write.text

    # Invalid max-players (too high).
    write2 = client.patch(
        f"/api/servers/{server_id}/config/properties",
        headers=csrf_headers(client),
        json={"properties": {"max-players": "9999"}},
    )
    assert write2.status_code == 400, write2.text

    # Invalid boolean.
    write3 = client.patch(
        f"/api/servers/{server_id}/config/properties",
        headers=csrf_headers(client),
        json={"properties": {"pvp": "yes"}},
    )
    assert write3.status_code == 400, write3.text


def test_config_validation_info(client: TestClient, admin_ready: dict) -> None:
    server = _setup_player_and_server(
        client, player_username="fileowner", server_name="config-validation-server", server_port=25591
    )
    server_id = server["id"]

    validation = client.get(
        f"/api/servers/{server_id}/config/validation",
        headers=csrf_headers(client),
    )
    assert validation.status_code == 200, validation.text
    payload = validation.json()
    assert "boolean" in payload
    assert "enum" in payload
    assert "integer" in payload
    assert "text" in payload
    assert "online-mode" in payload["boolean"]
    assert "difficulty" in payload["enum"]
    assert "max-players" in payload["integer"]
    assert payload["integer"]["max-players"]["min"] == 1
    assert payload["integer"]["max-players"]["max"] == 1000


def test_file_manager_unauthenticated(client: TestClient, admin_ready: dict, app) -> None:
    # Create a server as admin first.
    resp = client.post(
        "/api/servers",
        headers=csrf_headers(client),
        json={
            "name": "unauth-server",
            "software_type": "vanilla",
            "version": "1.20.1",
            "port": 25600,
            "max_players": 10,
            "online_mode": True,
            "difficulty": "normal",
            "gamemode": "survival",
            "view_distance": 10,
            "motd": "Unauth test",
        },
    )
    assert resp.status_code == 201
    server_id = resp.json()["id"]

    # Try to access without any session cookie - should be rejected.
    unauth_client = TestClient(app)
    response = unauth_client.get(f"/api/servers/{server_id}/files")
    assert response.status_code == 401
    unauth_client.close()


def test_config_properties_unknown_property_preserved(client: TestClient, admin_ready: dict) -> None:
    server = _setup_player_and_server(
        client, player_username="fileowner", server_name="config-unknown-server", server_port=25592
    )
    server_id = server["id"]

    # First write some properties including an unknown one.
    client.patch(
        f"/api/servers/{server_id}/config/properties",
        headers=csrf_headers(client),
        json={
            "properties": {
                "motd": "Test server",
                "unknown-prop": "custom-value",
            }
        },
    )

    # Read back - unknown property should be preserved.
    read = client.get(
        f"/api/servers/{server_id}/config/properties",
        headers=csrf_headers(client),
    )
    assert read.status_code == 200
    assert read.json()["properties"]["unknown-prop"] == "custom-value"

    # Now update only motd - unknown-prop should still be there.
    client.patch(
        f"/api/servers/{server_id}/config/properties",
        headers=csrf_headers(client),
        json={"properties": {"motd": "Updated MOTD"}},
    )

    read2 = client.get(
        f"/api/servers/{server_id}/config/properties",
        headers=csrf_headers(client),
    )
    assert read2.status_code == 200
    assert read2.json()["properties"]["motd"] == "Updated MOTD"
    assert read2.json()["properties"]["unknown-prop"] == "custom-value"


def test_config_properties_etag_returned(client: TestClient, admin_ready: dict) -> None:
    """Reading properties returns an etag."""
    server = _setup_player_and_server(
        client, player_username="fileowner", server_name="config-etag-server", server_port=25601
    )
    server_id = server["id"]

    read = client.get(
        f"/api/servers/{server_id}/config/properties",
        headers=csrf_headers(client),
    )
    assert read.status_code == 200, read.text
    payload = read.json()
    assert "properties" in payload
    assert "etag" in payload
    # etag is None when file doesn't exist yet, or a string when it does.
    assert payload["etag"] is None or isinstance(payload["etag"], str)


def test_config_properties_write_with_correct_etag(client: TestClient, admin_ready: dict) -> None:
    """Writing with a current etag succeeds."""
    server = _setup_player_and_server(
        client, player_username="fileowner", server_name="config-etag-ok-server", server_port=25602
    )
    server_id = server["id"]

    # First write without etag to create the file.
    write1 = client.patch(
        f"/api/servers/{server_id}/config/properties",
        headers=csrf_headers(client),
        json={"properties": {"motd": "First write"}},
    )
    assert write1.status_code == 200, write1.text

    # Read back to get the etag.
    read1 = client.get(
        f"/api/servers/{server_id}/config/properties",
        headers=csrf_headers(client),
    )
    assert read1.status_code == 200
    etag = read1.json()["etag"]
    assert etag is not None

    # Write with the correct etag.
    write2 = client.patch(
        f"/api/servers/{server_id}/config/properties",
        headers=csrf_headers(client),
        json={"properties": {"motd": "Second write"}, "if_match": etag},
    )
    assert write2.status_code == 200, write2.text

    # Verify the change persisted.
    read2 = client.get(
        f"/api/servers/{server_id}/config/properties",
        headers=csrf_headers(client),
    )
    assert read2.status_code == 200
    assert read2.json()["properties"]["motd"] == "Second write"


def test_config_properties_write_with_stale_etag(client: TestClient, admin_ready: dict, app) -> None:
    """Writing with a stale etag returns 409 Conflict."""
    server = _setup_player_and_server(
        client, player_username="fileowner", server_name="config-stale-server", server_port=25603
    )
    server_id = server["id"]

    # First write without etag to create the file.
    write1 = client.patch(
        f"/api/servers/{server_id}/config/properties",
        headers=csrf_headers(client),
        json={"properties": {"motd": "Original"}},
    )
    assert write1.status_code == 200, write1.text

    # Read to get the original etag.
    read1 = client.get(
        f"/api/servers/{server_id}/config/properties",
        headers=csrf_headers(client),
    )
    assert read1.status_code == 200
    stale_etag = read1.json()["etag"]
    assert stale_etag is not None

    # Modify the file directly (simulating another user's change).
    with app.state.session_factory() as db:
        from app.models.server import Server
        from app.models.user import User
        from app.services.config import write_properties
        db_server = db.get(Server, server_id)
        admin_user = db.get(User, admin_ready["user"]["id"])
        write_properties(
            db,
            db_server,
            actor=admin_user,
            properties={"motd": "Changed by another user"},
        )

    # Try to write with the stale etag.
    write2 = client.patch(
        f"/api/servers/{server_id}/config/properties",
        headers=csrf_headers(client),
        json={"properties": {"motd": "Stale write"}, "if_match": stale_etag},
    )
    assert write2.status_code == 409, write2.text
    assert "another user" in write2.json()["detail"].lower()


def test_config_properties_write_without_etag(client: TestClient, admin_ready: dict) -> None:
    """Writing without etag succeeds (backward compatible)."""
    server = _setup_player_and_server(
        client, player_username="fileowner", server_name="config-noetag-server", server_port=25604
    )
    server_id = server["id"]

    # Write without etag - should succeed.
    write = client.patch(
        f"/api/servers/{server_id}/config/properties",
        headers=csrf_headers(client),
        json={"properties": {"motd": "No etag write"}},
    )
    assert write.status_code == 200, write.text

    # Read back to verify.
    read = client.get(
        f"/api/servers/{server_id}/config/properties",
        headers=csrf_headers(client),
    )
    assert read.status_code == 200
    assert read.json()["properties"]["motd"] == "No etag write"
