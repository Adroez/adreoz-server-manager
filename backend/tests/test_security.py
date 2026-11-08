"""Password hashing and validation unit tests."""

from __future__ import annotations

import hashlib

import pytest

from app.core.security import (
    PasswordPolicyError,
    hash_password,
    hash_session_token,
    verify_password,
    validate_password,
)


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
