"""File manager service for Minecraft servers.

All file operations are scoped to a single server's directory under
``data/servers/``. Paths are resolved and validated before every operation to
prevent directory traversal and symlink-based escapes.
"""

from __future__ import annotations

import logging
import os
import shutil
import stat
import tempfile
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.server import Server
from app.services.errors import ConflictError, DomainError, ForbiddenError, NotFoundError

logger = logging.getLogger("asm.files")

# Maximum upload size: 100 MB.
MAX_UPLOAD_SIZE = 100 * 1024 * 1024

# Maximum size for a single file read via the API: 16 MB.
MAX_READ_SIZE = 16 * 1024 * 1024

# Characters not allowed in file/folder names.
INVALID_FILENAME_CHARS = set("<>:\"|?*")


class FileSecurityError(DomainError):
    """Raised when a file operation would escape the server directory."""


class FileSizeError(DomainError):
    """Raised when a file exceeds the allowed size."""


def _check_server_access(server: Server, actor) -> None:
    """Enforce ownership and admin access rules."""

    is_owner = server.owner_id == actor.id
    is_admin = actor.role == "admin"

    if not is_owner and not is_admin:
        raise ForbiddenError("You do not have access to this server.")


def _resolve_file_path(
    settings: Settings,
    server: Server,
    relative_path: str,
) -> Path:
    """Resolve and validate a file path within a server's directory.

    The resolved path must live inside the server's directory. Symlinks are
    followed by ``Path.resolve()``; if the target escapes the server
    directory, the request is rejected. This prevents symlink-based directory
    traversal attacks.

    ``relative_path`` must not be absolute and must not contain null bytes.
    An empty string refers to the server's root directory.
    """

    if "\x00" in relative_path:
        raise DomainError("Invalid file path.")

    # Reject absolute paths.
    if os.path.isabs(relative_path):
        raise DomainError("File path must be relative.")

    # Reject path components that are clearly unsafe.
    for part in Path(relative_path).parts:
        if part in ("..",):
            raise DomainError("File path must not traverse parent directories.")

    server_dir = Path(server.directory).resolve()

    # Join the relative path with the server directory and resolve.
    candidate = (server_dir / relative_path).resolve()

    # Ensure the resolved path is inside the server directory.
    try:
        candidate.relative_to(server_dir)
    except ValueError:
        raise FileSecurityError(
            "File path is outside the server directory."
        ) from None

    return candidate


def _validate_filename(name: str) -> None:
    """Validate a file or folder name."""

    if not name or name.strip() != name:
        raise DomainError("Invalid file or folder name.")
    if name in (".", ".."):
        raise DomainError("Invalid file or folder name.")
    if len(name) > 255:
        raise DomainError("File or folder name is too long.")
    if any(ch in INVALID_FILENAME_CHARS for ch in name):
        raise DomainError(
            "File or folder name contains invalid characters."
        )


def list_directory(
    settings: Settings,
    server: Server,
    actor,
    relative_path: str = "",
) -> list[dict]:
    """List files and folders in a server directory.

    Returns a list of entries with name, type, size, and modification time.
    """

    _check_server_access(server, actor)

    target = _resolve_file_path(settings, server, relative_path)

    if not target.exists():
        raise NotFoundError("Directory not found.")
    if not target.is_dir():
        raise DomainError("Path is not a directory.")

    entries: list[dict] = []
    for entry in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        try:
            stat_info = entry.stat()
        except OSError:
            continue
        is_dir = entry.is_dir()
        entries.append(
            {
                "name": entry.name,
                "type": "directory" if is_dir else "file",
                "size": stat_info.st_size if not is_dir else 0,
                "modified_at": stat_info.st_mtime,
                "is_symlink": entry.is_symlink(),
            }
        )

    return entries


def read_file(
    settings: Settings,
    server: Server,
    actor,
    relative_path: str,
) -> tuple[str, str]:
    """Read a text file's contents.

    Returns ``(content, mime_type)``. Rejects files larger than
    ``MAX_READ_SIZE`` and binary files (detected by null bytes).
    """

    _check_server_access(server, actor)

    target = _resolve_file_path(settings, server, relative_path)

    if not target.exists():
        raise NotFoundError("File not found.")
    if not target.is_file():
        raise DomainError("Path is not a file.")

    size = target.stat().st_size
    if size > MAX_READ_SIZE:
        raise FileSizeError(
            f"File is too large to read (max {MAX_READ_SIZE // (1024 * 1024)} MB)."
        )

    raw = target.read_bytes()

    # Detect binary files by checking for null bytes.
    if b"\x00" in raw:
        raise DomainError("File appears to be binary and cannot be displayed as text.")

    content = raw.decode("utf-8", errors="replace")

    # Guess a simple MIME type based on extension.
    suffix = target.suffix.lower()
    mime_map = {
        ".txt": "text/plain",
        ".properties": "text/plain",
        ".json": "application/json",
        ".yml": "text/yaml",
        ".yaml": "text/yaml",
        ".toml": "text/toml",
        ".cfg": "text/plain",
        ".ini": "text/plain",
        ".log": "text/plain",
        ".md": "text/markdown",
        ".html": "text/html",
        ".css": "text/css",
        ".js": "text/javascript",
    }
    mime_type = mime_map.get(suffix, "text/plain")

    return content, mime_type


def write_file(
    settings: Settings,
    server: Server,
    actor,
    relative_path: str,
    content: str,
) -> None:
    """Create or overwrite a text file.

    Validates the filename and file size before writing.
    """

    _check_server_access(server, actor)

    # Validate the final path component.
    target = _resolve_file_path(settings, server, relative_path)
    _validate_filename(target.name)

    # If the path exists and is a directory, refuse.
    if target.exists() and target.is_dir():
        raise DomainError("A directory with that name already exists.")

    # Encode and check size.
    raw = content.encode("utf-8")
    if len(raw) > MAX_READ_SIZE:
        raise FileSizeError(
            f"File is too large to write (max {MAX_READ_SIZE // (1024 * 1024)} MB)."
        )

    # Create parent directories if needed.
    target.parent.mkdir(parents=True, exist_ok=True)

    target.write_text(content, encoding="utf-8")
    target.chmod(0o600)

    logger.info(
        "Wrote file %s for server %s (actor=%s)",
        relative_path,
        server.id,
        actor.id,
    )


def create_folder(
    settings: Settings,
    server: Server,
    actor,
    relative_path: str,
) -> None:
    """Create a new folder."""

    _check_server_access(server, actor)

    target = _resolve_file_path(settings, server, relative_path)
    _validate_filename(target.name)

    if target.exists():
        raise ConflictError("A file or folder with that name already exists.")

    target.mkdir(parents=True, exist_ok=False)
    target.chmod(0o700)

    logger.info(
        "Created folder %s for server %s (actor=%s)",
        relative_path,
        server.id,
        actor.id,
    )


def rename_file(
    settings: Settings,
    server: Server,
    actor,
    old_path: str,
    new_path: str,
) -> None:
    """Rename or move a file or folder."""

    _check_server_access(server, actor)

    old_target = _resolve_file_path(settings, server, old_path)
    new_target = _resolve_file_path(settings, server, new_path)

    if not old_target.exists():
        raise NotFoundError("Source file or folder not found.")

    _validate_filename(new_target.name)

    if new_target.exists():
        raise ConflictError("A file or folder with that name already exists.")

    # Ensure the new parent directory exists.
    new_target.parent.mkdir(parents=True, exist_ok=True)

    old_target.rename(new_target)

    logger.info(
        "Renamed %s to %s for server %s (actor=%s)",
        old_path,
        new_path,
        server.id,
        actor.id,
    )


def delete_file(
    settings: Settings,
    server: Server,
    actor,
    relative_path: str,
) -> None:
    """Delete a file or folder."""

    _check_server_access(server, actor)

    target = _resolve_file_path(settings, server, relative_path)

    if not target.exists():
        raise NotFoundError("File or folder not found.")

    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()

    logger.info(
        "Deleted %s for server %s (actor=%s)",
        relative_path,
        server.id,
        actor.id,
    )


def get_file_info(
    settings: Settings,
    server: Server,
    actor,
    relative_path: str,
) -> dict:
    """Get metadata for a file or folder."""

    _check_server_access(server, actor)

    target = _resolve_file_path(settings, server, relative_path)

    if not target.exists():
        raise NotFoundError("File or folder not found.")

    stat_info = target.stat()
    is_dir = target.is_dir()

    return {
        "name": target.name,
        "type": "directory" if is_dir else "file",
        "size": stat_info.st_size if not is_dir else 0,
        "modified_at": stat_info.st_mtime,
        "is_symlink": target.is_symlink(),
        "path": relative_path,
    }


def save_uploaded_file(
    settings: Settings,
    server: Server,
    actor,
    relative_path: str,
    file_data: bytes,
    filename: str,
) -> None:
    """Save an uploaded file to the server directory.

    Enforces size limits and filename validation. Uploaded files are stored
    as-is and are not executed automatically.
    """

    _check_server_access(server, actor)

    # Validate the target filename (not the upload filename).
    target = _resolve_file_path(settings, server, relative_path)
    _validate_filename(target.name)

    # Validate the original upload filename for safety.
    _validate_filename(filename)

    if len(file_data) > MAX_UPLOAD_SIZE:
        raise FileSizeError(
            f"Uploaded file is too large (max {MAX_UPLOAD_SIZE // (1024 * 1024)} MB)."
        )

    if target.exists() and target.is_dir():
        raise DomainError("A directory with that name already exists.")

    # Write to a temp file first, then move atomically.
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=target.parent, prefix=".asm-upload-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(file_data)
        os.replace(tmp_path, target)
    except Exception:
        # Clean up temp file on failure.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    target.chmod(0o600)

    logger.info(
        "Uploaded file %s for server %s (actor=%s, %s bytes)",
        relative_path,
        server.id,
        actor.id,
        len(file_data),
    )
