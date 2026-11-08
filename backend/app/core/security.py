"""Password hashing and opaque token generation.

Passwords are hashed with scrypt from Python's standard library. The encoded
format is self-describing so parameters can be raised in the future and old
hashes upgraded on a successful login without a schema change.

Sessions use random opaque tokens. Only a SHA-256 digest of the session token
is persisted, so a database leak does not hand an attacker usable cookies.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from functools import lru_cache

SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32
SCRYPT_MAXMEM = 64 * 1024 * 1024


class PasswordPolicyError(ValueError):
    """Raised when a password does not meet the minimum policy."""


def _b64encode(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _b64decode(value: str) -> bytes:
    return base64.b64decode(value.encode("ascii"))


def validate_password(password: str) -> None:
    """Enforce the password policy shared by every account operation."""

    if len(password) < 10:
        raise PasswordPolicyError("Password must be at least 10 characters long.")
    if len(password) > 128:
        raise PasswordPolicyError("Password must be at most 128 characters long.")
    if not any(char.isalpha() for char in password):
        raise PasswordPolicyError("Password must contain at least one letter.")
    if not any(char.isdigit() for char in password):
        raise PasswordPolicyError("Password must contain at least one number.")


def hash_password(password: str) -> str:
    """Return a versioned scrypt hash for ``password``."""

    validate_password(password)
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_DKLEN,
        maxmem=SCRYPT_MAXMEM,
    )
    return "scrypt$1${}${}${}${}${}".format(
        SCRYPT_N,
        SCRYPT_R,
        SCRYPT_P,
        _b64encode(salt),
        _b64encode(digest),
    )


def verify_password(password: str, encoded: str) -> bool:
    """Verify a password against an encoded scrypt hash in constant time."""

    try:
        scheme, version, n, r, p, salt_b64, digest_b64 = encoded.split("$")
        if scheme != "scrypt" or version != "1":
            return False
        salt = _b64decode(salt_b64)
        expected = _b64decode(digest_b64)
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
            maxmem=max(SCRYPT_MAXMEM, 128 * int(n) * int(r) * int(p) * 2),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


@lru_cache(maxsize=1)
def dummy_password_hash() -> str:
    """A valid hash used to equalize timing for unknown usernames.

    ``hash_password`` validates the password, so use a fixed dummy input that
    satisfies the policy.
    """

    return hash_password("not-a-real-password-0")


def verify_dummy_password(password: str) -> bool:
    """Burn the same work as a real verification without an account."""

    return verify_password(password, dummy_password_hash())


def generate_session_token() -> str:
    return secrets.token_urlsafe(32)


def generate_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def constant_time_equals(left: str, right: str) -> bool:
    return hmac.compare_digest(left, right)
