"""First-run setup and authentication flow tests."""

from __future__ import annotations

import hashlib

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models.auth_session import AuthSession
from tests.conftest import (
    ADMIN_CREDENTIALS,
    csrf_headers,
    csrf_token,
)


def test_setup_status_then_complete_setup(client: TestClient) -> None:
    status_response = client.get("/api/setup/status")
    assert status_response.status_code == 200
    assert status_response.json()["setup_required"] is True

    response = client.post("/api/setup", json=ADMIN_CREDENTIALS)
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["user"]["username"] == "owner"
    assert payload["user"]["role"] == "admin"
    assert payload["user"]["is_admin"] is True
    assert payload["user"]["email"] == "owner@example.com"
    assert payload["csrf_token"]

    cookie = response.headers.get("set-cookie", "")
    assert "asm_session=" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie

    assert client.get("/api/setup/status").json()["setup_required"] is False
    assert client.get("/api/auth/me").status_code == 200

    second = client.post("/api/setup", json=ADMIN_CREDENTIALS)
    assert second.status_code == 409


def test_setup_validation_does_not_echo_input(client: TestClient) -> None:
    secret = "A1" + "x" * 200
    response = client.post(
        "/api/setup",
        json={"username": "owner", "password": secret},
    )
    assert response.status_code == 422
    assert secret not in response.text
    for item in response.json()["detail"]:
        assert set(item) == {"loc", "msg", "type"}


def test_login_logout_and_session_hashing(
    client: TestClient, admin_ready: dict, app
) -> None:
    raw_token = client.cookies.get("asm_session")
    assert raw_token

    with app.state.session_factory() as db:
        auth_session = db.scalar(select(AuthSession))
        assert auth_session is not None
        assert auth_session.token_hash != raw_token
        assert auth_session.token_hash == hashlib.sha256(raw_token.encode()).hexdigest()
        assert auth_session.csrf_token == admin_ready["csrf_token"]

    logout = client.post("/api/auth/logout", headers=csrf_headers(client))
    assert logout.status_code == 200
    assert logout.json()["message"] == "Signed out."
    assert client.get("/api/auth/me").status_code == 401

    bad_login = client.post(
        "/api/auth/login",
        json={"username": "owner", "password": "DefinitelyWrong123"},
    )
    assert bad_login.status_code == 401
    assert bad_login.json()["detail"] == "Invalid username or password."

    good_login = client.post(
        "/api/auth/login",
        json={
            "username": "owner",
            "password": ADMIN_CREDENTIALS["password"],
        },
    )
    assert good_login.status_code == 200
    assert good_login.json()["user"]["username"] == "owner"
    assert client.get("/api/auth/me").status_code == 200


def test_csrf_required_for_mutations(client: TestClient, admin_ready: dict) -> None:
    no_token = client.post("/api/auth/logout")
    assert no_token.status_code == 403
    assert client.get("/api/auth/me").status_code == 200

    wrong_token = client.post(
        "/api/auth/logout", headers={"X-CSRF-Token": "not-the-token"}
    )
    assert wrong_token.status_code == 403
    assert client.get("/api/auth/me").status_code == 200

    correct = client.post("/api/auth/logout", headers=csrf_headers(client))
    assert correct.status_code == 200
    assert client.get("/api/auth/me").status_code == 401


def test_password_change_rotates_session(
    client: TestClient, admin_ready: dict, app
) -> None:
    old_token = client.cookies.get("asm_session")
    old_csrf = csrf_token(client)
    assert old_token and old_csrf

    response = client.post(
        "/api/auth/password",
        headers={"X-CSRF-Token": old_csrf},
        json={
            "current_password": ADMIN_CREDENTIALS["password"],
            "new_password": "BrandNewPass456",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["csrf_token"] != old_csrf
    assert client.cookies.get("asm_session") != old_token
    assert client.get("/api/auth/me").status_code == 200

    # The old cookie must no longer work.
    stale_client = TestClient(app)
    stale_client.cookies.set("asm_session", old_token)
    assert stale_client.get("/api/auth/me").status_code == 401
    stale_client.close()

    old_password_login = client.post(
        "/api/auth/login",
        json={"username": "owner", "password": ADMIN_CREDENTIALS["password"]},
    )
    assert old_password_login.status_code == 401

    new_password_login = client.post(
        "/api/auth/login",
        json={"username": "owner", "password": "BrandNewPass456"},
    )
    assert new_password_login.status_code == 200


def test_login_rate_limit(client: TestClient, admin_ready: dict) -> None:
    for _ in range(5):
        response = client.post(
            "/api/auth/login",
            json={"username": "owner", "password": "WrongPassword123"},
        )
        assert response.status_code == 401

    limited = client.post(
        "/api/auth/login",
        json={"username": "owner", "password": ADMIN_CREDENTIALS["password"]},
    )
    assert limited.status_code == 429
    assert "Retry-After" in limited.headers

    client.app.state.login_limiter.clear()
    recovered = client.post(
        "/api/auth/login",
        json={"username": "owner", "password": ADMIN_CREDENTIALS["password"]},
    )
    assert recovered.status_code == 200


def test_revoke_other_sessions(client: TestClient, admin_ready: dict, app) -> None:
    second = TestClient(app)
    login = second.post(
        "/api/auth/login",
        json={"username": "owner", "password": ADMIN_CREDENTIALS["password"]},
    )
    assert login.status_code == 200
    second_csrf = login.json()["csrf_token"]

    response = client.delete(
        "/api/auth/sessions", headers={"X-CSRF-Token": second_csrf}
    )
    # The CSRF token belongs to the second session, not the first.
    assert response.status_code == 403

    response = client.delete("/api/auth/sessions", headers=csrf_headers(client))
    assert response.status_code == 200
    assert "1 other session" in response.json()["message"]
    assert second.get("/api/auth/me").status_code == 401
    assert client.get("/api/auth/me").status_code == 200
    second.close()
