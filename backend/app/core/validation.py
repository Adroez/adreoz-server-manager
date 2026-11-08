"""Shared validation for identifiers used across API and services."""

from __future__ import annotations

import re

USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,31}$")
RESERVED_USERNAMES = {
    "root",
    "system",
    "adreoz",
    "asm",
    "support",
}


class UsernameError(ValueError):
    """Raised when a username is malformed or reserved."""


def validate_username(username: str) -> str:
    """Normalize and validate a username, returning the display form."""

    cleaned = username.strip()
    if not USERNAME_PATTERN.fullmatch(cleaned):
        raise UsernameError(
            "Username must be 3-32 characters and may only contain letters, "
            "numbers, dots, underscores, and hyphens. It must start with a "
            "letter or number."
        )
    if cleaned.lower() in RESERVED_USERNAMES:
        raise UsernameError("That username is reserved.")
    return cleaned


def normalize_username(username: str) -> str:
    return username.strip().lower()
