"""Shared pytest fixtures."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import PROJECT_ROOT, Settings
from app.database.base import Base
from app.main import create_app

ADMIN_CREDENTIALS = {
    "username": "owner",
    "email": "owner@example.com",
    "password": "AdreozPass123",
}
PLAYER_PASSWORD = "PlayerPass123"


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        environment="test",
        data_dir=tmp_path / "data",
        frontend_dir=PROJECT_ROOT / "frontend",
        database_url=f"sqlite:///{tmp_path / 'asm-test.db'}",
        login_attempt_limit=5,
        login_attempt_window_seconds=60,
    )


@pytest.fixture()
def app(settings: Settings) -> FastAPI:
    application = create_app(settings)
    Base.metadata.create_all(application.state.engine)
    return application


@pytest.fixture()
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def admin_ready(client: TestClient) -> dict:
    response = client.post("/api/setup", json=ADMIN_CREDENTIALS)
    assert response.status_code == 201, response.text
    return response.json()


def csrf_token(client: TestClient) -> str:
    response = client.get("/api/auth/me")
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


def csrf_headers(client: TestClient) -> dict[str, str]:
    return {"X-CSRF-Token": csrf_token(client)}
