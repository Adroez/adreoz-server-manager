"""Password hashing, request hardening, and runtime permission tests."""

from __future__ import annotations

import hashlib
import stat
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url

from app.core.security import (
    PasswordPolicyError,
    hash_password,
    hash_session_token,
    verify_password,
    validate_password,
)
from app.database.base import Base
from app.main import create_app
from tests.conftest import ADMIN_CREDENTIALS


def test_hash_and_verify_roundtrip() -> None:
    encoded = hash_password("CorrectHorse123")
    assert encoded.startswith("scrypt$1$")
    assert verify_password("CorrectHorse123", encoded)
    assert not verify_password("WrongHorse123", encoded)


def test_hashes_are_salted() -> None:
    first = hash_password("CorrectHorse123")
    second = hash_password("CorrectHorse123")
    assert first != second


@pytest.mark.parametrize(
    "password",
    ["short1", "alllettersonly", "1234567890", "a1" + "x" * 200],
)
def test_password_policy_rejects_weak_values(password: str) -> None:
    with pytest.raises(PasswordPolicyError):
        validate_password(password)


def test_session_token_hash_is_sha256() -> None:
    token = "sample-token-value"
    assert hash_session_token(token) == hashlib.sha256(token.encode()).hexdigest()


def test_trusted_host_rejects_unknown_host(client: TestClient) -> None:
    response = client.get("/api/health", headers={"host": "evil.example"})
    assert response.status_code == 400
    assert response.text == "Invalid host header"
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert client.get("/api/health").status_code == 200


def test_request_bodies_reject_unknown_fields(client: TestClient) -> None:
    response = client.post(
        "/api/setup",
        json={**ADMIN_CREDENTIALS, "role": "admin"},
    )
    assert response.status_code == 422
    assert client.get("/api/setup/status").json()["setup_required"] is True


def test_runtime_directories_and_database_are_owner_only(
    client: TestClient, settings
) -> None:
    assert stat.S_IMODE(settings.data_dir.stat().st_mode) == 0o700
    for name in ("servers", "backups", "logs"):
        assert stat.S_IMODE((settings.data_dir / name).stat().st_mode) == 0o700
    database_path = make_url(settings.database_uri).database
    assert database_path and database_path != ":memory:"
    assert stat.S_IMODE(Path(database_path).stat().st_mode) == 0o600


def test_secure_cookie_and_hsts_when_configured(settings) -> None:
    secure_settings = settings.model_copy(
        update={"cookie_secure": True, "environment": "production"}
    )
    secure_app = create_app(secure_settings)
    Base.metadata.create_all(secure_app.state.engine)

    with TestClient(secure_app, base_url="https://testserver") as secure_client:
        response = secure_client.post("/api/setup", json=ADMIN_CREDENTIALS)
        assert response.status_code == 201, response.text

        cookie = response.headers["set-cookie"]
        assert "asm_session=" in cookie
        assert "HttpOnly" in cookie
        assert "Secure" in cookie
        assert response.headers["strict-transport-security"].startswith(
            "max-age=31536000"
        )
        assert secure_client.get("/api/auth/me").status_code == 200
        assert secure_client.get("/docs").status_code == 404
