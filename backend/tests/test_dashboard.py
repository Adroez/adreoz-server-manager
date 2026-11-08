"""Dashboard, settings, health, and header behavior."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.models.auth_session import AuthSession
from app.models.user import User
from tests.conftest import ADMIN_CREDENTIALS, ADMIN_LOGIN, csrf_headers


def test_dashboard_uses_live_system_data(
    client: TestClient, admin_ready: dict
) -> None:
    response = client.get("/api/dashboard/summary")
    assert response.status_code == 200
    payload = response.json()

    assert payload["user"]["username"] == "owner"
    assert payload["system"]["hostname"]
    assert payload["system"]["memory"]["total_bytes"] > 0
    assert payload["system"]["disk"]["total_bytes"] > 0
    assert payload["system"]["cpu_count"] >= 1
    assert payload["account"]["active_sessions"] == 1
    assert payload["platform"]["total_users"] == 1
    assert payload["platform"]["admins"] == 1
    assert payload["platform"]["recent_activity"][0]["action"] == "setup.completed"


def test_settings_update_applies_to_new_sessions(
    client: TestClient, admin_ready: dict
) -> None:
    settings = client.get("/api/settings")
    assert settings.status_code == 200
    body = settings.json()
    assert body["session_lifetime_hours"] == 24
    assert body["setup_complete"] is True
    assert body["database_backend"] == "sqlite"

    update = client.patch(
        "/api/settings",
        headers=csrf_headers(client),
        json={"session_lifetime_hours": 2},
    )
    assert update.status_code == 200
    assert update.json()["session_lifetime_hours"] == 2

    invalid = client.patch(
        "/api/settings",
        headers=csrf_headers(client),
        json={"session_lifetime_hours": 5000},
    )
    assert invalid.status_code == 422

    client.post("/api/auth/logout", headers=csrf_headers(client))
    login = client.post("/api/auth/login", json=ADMIN_LOGIN)
    assert login.status_code == 200
    expires = datetime.fromisoformat(
        login.json()["expires_at"].replace("Z", "+00:00")
    )
    now = datetime.now(timezone.utc)
    assert timedelta(hours=1, minutes=50) < expires - now <= timedelta(hours=2)


def test_health_endpoint(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_security_headers_and_cache_policy(client: TestClient) -> None:
    index = client.get("/")
    assert index.status_code == 200
    headers = index.headers
    assert "default-src 'self'" in headers["content-security-policy"]
    assert "frame-ancestors 'none'" in headers["content-security-policy"]
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    assert headers["referrer-policy"] == "no-referrer"
    assert headers["cache-control"] == "no-cache"

    api = client.get("/api/setup/status")
    assert api.headers["cache-control"] == "no-store"

    # Unknown API paths must not leak the SPA shell.
    missing = client.get("/api/does-not-exist")
    assert missing.status_code == 404


def test_index_contains_brand(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "Adreoz Server Manager" in response.text
    assert "/js/main.js" in response.text


def test_expired_session_is_rejected_and_removed(
    client: TestClient, admin_ready: dict, app
) -> None:
    with app.state.session_factory() as db:
        auth_session = db.scalar(select(AuthSession))
        assert auth_session is not None
        auth_session.expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1)
        db.commit()
        session_id = auth_session.id

    assert client.get("/api/auth/me").status_code == 401
    with app.state.session_factory() as db:
        assert db.get(AuthSession, session_id) is None


def test_setup_marker_prevents_reopening_setup(
    client: TestClient, admin_ready: dict, app
) -> None:
    with app.state.session_factory() as db:
        db.execute(delete(AuthSession))
        db.execute(delete(User))
        db.commit()

    status = client.get("/api/setup/status")
    assert status.status_code == 200
    assert status.json()["setup_required"] is False

    attempt = client.post("/api/setup", json=ADMIN_CREDENTIALS)
    assert attempt.status_code == 409
