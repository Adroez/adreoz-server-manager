"""Shared validation for identifiers used across API and services."""

from __future__ import annotations

import re

USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,31}$")
SERVER_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
RESERVED_USERNAMES = {
    "root",
    "system",
    "adreoz",
    "asm",
    "support",
}
RESERVED_SERVER_NAMES = {
    "root",
    "system",
    "adreoz",
    "asm",
    "support",
    "localhost",
    "health",
}


class UsernameError(ValueError):
    """Raised when a username is malformed or reserved."""


class ServerNameError(ValueError):
    """Raised when a server name is malformed or reserved."""


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


def validate_server_name(name: str) -> str:
    """Validate a server name, returning the cleaned display form.

    Server names must be unique across the panel and safe to use as part of
    filesystem paths and API identifiers.
    """

    cleaned = name.strip()
    if not cleaned:
        raise ServerNameError("Server name is required.")
    if len(cleaned) > 64:
        raise ServerNameError("Server name must be at most 64 characters.")
    if not SERVER_NAME_PATTERN.fullmatch(cleaned):
        raise ServerNameError(
            "Server name may only contain letters, numbers, dots, "
            "underscores, and hyphens, and must start with a letter or number."
        )
    if cleaned.lower() in RESERVED_SERVER_NAMES:
        raise ServerNameError("That server name is reserved.")
    return cleaned
