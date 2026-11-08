"""server.properties configuration editor.

Parses and writes Minecraft server.properties files while preserving unknown
properties, comments, and blank lines. Validates values before writing.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.core.config import Settings
from app.models.server import Server
from app.services.errors import ConflictError, DomainError, ForbiddenError

logger = logging.getLogger("asm.config")

# Properties that have a fixed set of allowed values.
ALLOWED_VALUES = {
    "gamemode": ("survival", "creative", "adventure", "spectator"),
    "difficulty": ("peaceful", "easy", "normal", "hard"),
    "online-mode": ("true", "false"),
    "pvp": ("true", "false"),
    "allow-nether": ("true", "false"),
    "allow-flight": ("true", "false"),
    "announce-player-achievements": ("true", "false"),
    "force-gamemode": ("true", "false"),
    "hardcore": ("true", "false"),
    "enable-command-block": ("true", "false"),
    "enable-query": ("true", "false"),
    "enable-rcon": ("true", "false"),
    "hardcore": ("true", "false"),
    "generate-structures": ("true", "false"),
    "max-players": ("int", 1, 1000),
    "view-distance": ("int", 2, 32),
    "simulation-distance": ("int", 2, 32),
    "spawn-protection": ("int", 0, 1000),
    "server-port": ("int", 1024, 65535),
    "player-idle-timeout": ("int", 0, 60000),
    "max-tick-time": ("int", -1, 60000),
}

# Integer properties with their valid ranges.
INT_PROPERTIES = {
    "max-players": (1, 1000),
    "view-distance": (2, 32),
    "simulation-distance": (2, 32),
    "spawn-protection": (0, 1000),
    "server-port": (1024, 65535),
    "player-idle-timeout": (0, 60000),
    "max-tick-time": (-1, 60000),
}


class ConfigError(DomainError):
    """Raised when server.properties parsing or validation fails."""


def _check_server_access(server: Server, actor) -> None:
    """Enforce ownership and admin access rules."""

    is_owner = server.owner_id == actor.id
    is_admin = actor.role == "admin"

    if not is_owner and not is_admin:
        raise ForbiddenError("You do not have access to this server.")


def _properties_path(server: Server) -> Path:
    """Return the path to server.properties for a server."""

    return Path(server.directory) / "server.properties"


def read_properties(
    settings: Settings,
    server: Server,
    actor,
) -> tuple[dict[str, str], str | None]:
    """Read server.properties and return a dict of key/value pairs.

    Comments (lines starting with #) and blank lines are not included in the
    returned dict. Unknown properties are preserved in the dict.

    Returns a tuple of (properties, etag) where etag is the file's modification
    time in nanoseconds as a string, or None if the file does not exist. The
    etag can be used for optimistic concurrency control.
    """

    _check_server_access(server, actor)

    path = _properties_path(server)

    if not path.exists():
        return {}, None

    try:
        stat_info = path.stat()
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.exception("Failed to read server.properties for server %s", server.id)
        raise DomainError("Failed to read server.properties.") from exc

    etag = str(stat_info.st_mtime_ns)

    properties: dict[str, str] = {}
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("!"):
            continue
        if "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        value = value.strip()
        if key:
            properties[key] = value

    return properties, etag


def write_properties(
    settings: Settings,
    server: Server,
    actor,
    properties: dict[str, str],
    if_match: str | None = None,
) -> dict[str, str]:
    """Write server.properties from a dict of key/value pairs.

    Validates all values before writing. Unknown properties are preserved.
    Returns the dict of properties that were actually written.

    If ``if_match`` is provided, the write only proceeds if the current file's
    modification time (as a nanosecond string) matches ``if_match``. Otherwise
    a :class:`ConflictError` is raised, indicating that another user has
    modified the file since it was read.
    """

    _check_server_access(server, actor)

    path = _properties_path(server)

    # Check etag if provided.
    if if_match is not None:
        if not path.exists():
            raise ConflictError(
                "Server.properties no longer exists. Please reload and try again."
            )
        try:
            current_mtime = str(path.stat().st_mtime_ns)
        except OSError as exc:
            raise DomainError("Failed to read server.properties.") from exc
        if current_mtime != if_match:
            raise ConflictError(
                "Server.properties has been modified by another user. "
                "Please reload and try again."
            )

    # Validate all values before writing.
    validated: dict[str, str] = {}
    for key, value in properties.items():
        validated[key] = _validate_property(key, value)

    # Read the existing file to preserve comments, blank lines, and unknown
    # properties not in the update. If the file doesn't exist yet, start with
    # an empty list of lines.
    if path.exists():
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.exception("Failed to read server.properties for server %s", server.id)
            raise DomainError("Failed to read server.properties.") from exc
        lines = raw.splitlines()
    else:
        lines = []

    updated_keys = set(validated.keys())
    new_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("!"):
            new_lines.append(line)
            continue
        if "=" not in stripped:
            new_lines.append(line)
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        if key in updated_keys:
            new_lines.append(f"{key}={validated[key]}")
            updated_keys.discard(key)
        else:
            new_lines.append(line)

    # Append any new properties that weren't in the original file.
    for key in sorted(updated_keys):
        new_lines.append(f"{key}={validated[key]}")

    # Write back.
    try:
        path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        path.chmod(0o600)
    except OSError as exc:
        logger.exception("Failed to write server.properties for server %s", server.id)
        raise DomainError("Failed to write server.properties.") from exc

    logger.info(
        "Updated server.properties for server %s (actor=%s, keys=%s)",
        server.id,
        actor.id,
        ", ".join(sorted(validated.keys())),
    )

    return validated


def _validate_property(key: str, value: str) -> str:
    """Validate a single server.properties value."""

    key_lower = key.lower()

    # Boolean properties.
    if key_lower in ("online-mode", "pvp", "allow-nether", "allow-flight",
                     "announce-player-achievements", "force-gamemode",
                     "hardcore", "enable-command-block", "enable-query",
                     "enable-rcon", "generate-structures"):
        if value.lower() not in ("true", "false"):
            raise DomainError(f"{key} must be 'true' or 'false'.")
        return value.lower()

    # Enum properties.
    if key_lower in ALLOWED_VALUES:
        allowed = ALLOWED_VALUES[key_lower]
        if len(allowed) == 3 and allowed[0] == "int":
            # It's an int range property.
            _, min_v, max_v = allowed
            try:
                int_value = int(value)
            except ValueError:
                raise DomainError(f"{key} must be an integer.") from None
            if int_value < min_v or int_value > max_v:
                raise DomainError(
                    f"{key} must be between {min_v} and {max_v}."
                ) from None
            return str(int_value)
        else:
            if value.lower() not in allowed:
                raise DomainError(
                    f"{key} must be one of: {', '.join(allowed)}."
                ) from None
            return value.lower()

    # Integer properties with ranges.
    if key_lower in INT_PROPERTIES:
        min_v, max_v = INT_PROPERTIES[key_lower]
        try:
            int_value = int(value)
        except ValueError:
            raise DomainError(f"{key} must be an integer.") from None
        if int_value < min_v or int_value > max_v:
            raise DomainError(
                f"{key} must be between {min_v} and {max_v}."
            ) from None
        return str(int_value)

    # MOTD - no validation other than length.
    if key_lower == "motd":
        if len(value) > 128:
            raise DomainError("motd must be at most 128 characters.")
        return value

    # Level-name, level-seed, etc. - just validate no newlines.
    if "\n" in value or "\r" in value:
        raise DomainError(f"{key} must not contain newlines.")

    # Unknown properties are passed through as-is (but reject newlines).
    return value


def get_validation_info() -> dict:
    """Return validation metadata for all known properties.

    Used by the frontend to build the configuration editor UI.
    """

    return {
        "boolean": sorted(
            key for key in ALLOWED_VALUES
            if len(ALLOWED_VALUES[key]) == 2
            and ALLOWED_VALUES[key][0] in ("true", "false")
        ),
        "enum": {
            key: list(values)
            for key, values in ALLOWED_VALUES.items()
            if not (len(values) == 3 and values[0] == "int")
        },
        "integer": {
            key: {"min": min_v, "max": max_v}
            for key, (min_v, max_v) in INT_PROPERTIES.items()
        },
        "text": ["motd", "level-name", "level-seed"],
    }
