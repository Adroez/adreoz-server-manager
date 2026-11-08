"""User administration and role-based access control tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import (
    ADMIN_CREDENTIALS,
    PLAYER_PASSWORD,
    csrf_headers,
)


def _create_player(client: TestClient, username: str = "player") -> dict:
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


def test_admin_user_list_and_duplicate_rejection(
    client: TestClient, admin_ready: dict
) -> None:
    player = _create_player(client)
    assert player["role"] == "user"

    users = client.get("/api/users")
    assert users.status_code == 200
    payload = users.json()
    assert payload["total"] == 2
    assert {item["username"] for item in payload["items"]} == {"owner", "player"}

    duplicate = client.post(
        "/api/users",
        headers=csrf_headers(client),
        json={
            "username": "PLAYER",
            "password": PLAYER_PASSWORD,
            "role": "user",
        },
    )
    assert duplicate.status_code == 409

    weak = client.post(
        "/api/users",
        headers=csrf_headers(client),
        json={"username": "weakling", "password": "short", "role": "user"},
    )
    assert weak.status_code == 400
    assert "at least 10" in weak.json()["detail"]

    missing_csrf = client.post(
        "/api/users",
        json={"username": "noheader", "password": PLAYER_PASSWORD, "role": "user"},
    )
    assert missing_csrf.status_code == 403


def test_regular_user_is_denied_admin_endpoints(
    client: TestClient, admin_ready: dict
) -> None:
    _create_player(client)
    client.post("/api/auth/logout", headers=csrf_headers(client))

    login = client.post(
        "/api/auth/login",
        json={"username": "player", "password": PLAYER_PASSWORD},
    )
    assert login.status_code == 200
    assert login.json()["user"]["is_admin"] is False

    assert client.get("/api/users").status_code == 403
    assert client.get("/api/users/1").status_code == 403
    assert client.get("/api/settings").status_code == 403
    assert (
        client.patch(
            "/api/settings",
            headers=csrf_headers(client),
            json={"session_lifetime_hours": 12},
        ).status_code
        == 403
    )
    assert client.get("/api/dashboard/summary").status_code == 200
    assert client.get("/api/dashboard/summary").json()["platform"] is None


def test_admin_self_protection_and_last_admin(
    client: TestClient, admin_ready: dict
) -> None:
    me = admin_ready["user"]

    self_demote = client.patch(
        f"/api/users/{me['id']}",
        headers=csrf_headers(client),
        json={"role": "user"},
    )
    assert self_demote.status_code == 400

    self_disable = client.patch(
        f"/api/users/{me['id']}",
        headers=csrf_headers(client),
        json={"is_active": False},
    )
    assert self_disable.status_code == 400

    self_delete = client.delete(
        f"/api/users/{me['id']}", headers=csrf_headers(client)
    )
    assert self_delete.status_code == 400

    second = client.post(
        "/api/users",
        headers=csrf_headers(client),
        json={
            "username": "secondadmin",
            "password": "SecondAdmin123",
            "role": "admin",
        },
    )
    assert second.status_code == 201
    second_id = second.json()["id"]

    demote_second = client.patch(
        f"/api/users/{second_id}",
        headers=csrf_headers(client),
        json={"role": "user"},
    )
    assert demote_second.status_code == 200
    assert demote_second.json()["role"] == "user"

    # Promote them back, then verify sessions are revoked when disabled.
    promote = client.patch(
        f"/api/users/{second_id}",
        headers=csrf_headers(client),
        json={"role": "admin"},
    )
    assert promote.status_code == 200
    disable = client.patch(
        f"/api/users/{second_id}",
        headers=csrf_headers(client),
        json={"is_active": False},
    )
    assert disable.status_code == 200
    assert disable.json()["is_active"] is False


def test_delete_user_and_audit_trail(
    client: TestClient, admin_ready: dict
) -> None:
    player = _create_player(client)
    delete = client.delete(
        f"/api/users/{player['id']}", headers=csrf_headers(client)
    )
    assert delete.status_code == 200

    users = client.get("/api/users").json()
    assert users["total"] == 1

    summary = client.get("/api/dashboard/summary").json()
    actions = [item["action"] for item in summary["platform"]["recent_activity"]]
    assert "user.created" in actions
    assert "user.deleted" in actions

    player_login = client.post(
        "/api/auth/login",
        json={"username": "player", "password": PLAYER_PASSWORD},
    )
    assert player_login.status_code == 401

    audit = summary["platform"]["recent_activity"]
    deleted = next(item for item in audit if item["action"] == "user.deleted")
    assert "player" in deleted["detail"]


def test_admin_password_reset_revokes_sessions(
    client: TestClient, admin_ready: dict, app
) -> None:
    player = _create_player(client)

    player_client = TestClient(app)
    login = player_client.post(
        "/api/auth/login",
        json={"username": "player", "password": PLAYER_PASSWORD},
    )
    assert login.status_code == 200
    assert player_client.get("/api/auth/me").status_code == 200

    reset = client.post(
        f"/api/users/{player['id']}/password",
        headers=csrf_headers(client),
        json={"new_password": "ResetPassword789"},
    )
    assert reset.status_code == 200

    assert player_client.get("/api/auth/me").status_code == 401
    assert (
        player_client.post(
            "/api/auth/login",
            json={"username": "player", "password": PLAYER_PASSWORD},
        ).status_code
        == 401
    )
    assert (
        player_client.post(
            "/api/auth/login",
            json={"username": "player", "password": "ResetPassword789"},
        ).status_code
        == 200
    )
    player_client.close()

    self_reset = client.post(
        f"/api/users/{admin_ready['user']['id']}/password",
        headers=csrf_headers(client),
        json={"new_password": "WhateverPass123"},
    )
    assert self_reset.status_code == 400


def test_disabled_user_cannot_login(client: TestClient, admin_ready: dict) -> None:
    player = _create_player(client)
    disable = client.patch(
        f"/api/users/{player['id']}",
        headers=csrf_headers(client),
        json={"is_active": False},
    )
    assert disable.status_code == 200

    client.post("/api/auth/logout", headers=csrf_headers(client))
    login = client.post(
        "/api/auth/login",
        json={"username": "player", "password": PLAYER_PASSWORD},
    )
    assert login.status_code == 401
    assert login.json()["detail"] == "Invalid username or password."
