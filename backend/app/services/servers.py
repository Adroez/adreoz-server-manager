"""Minecraft server lifecycle management.

All server operations enforce ownership: normal users can only act on servers
they own; administrators can act on any server. Server directories are
resolved relative to the configured servers base directory and are strictly
contained to prevent directory traversal.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import socket
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, selectinload

from app.core.config import Settings
from app.core.process import ProcessManager, ProcessError
from app.core.validation import ServerNameError, validate_server_name
from app.models.server import Server, ServerLog
from app.models.user import User
from app.schemas.server import (
    SERVER_DIFFICULTIES,
    SERVER_GAMEMODES,
    SERVER_SOFTWARE_TYPES,
    ServerCreateRequest,
    ServerUpdateRequest,
)
from app.services.audit import RequestMeta, record_audit
from app.services.errors import ConflictError, DomainError, ForbiddenError, NotFoundError

logger = logging.getLogger("asm.servers")

# Maximum number of log lines retained per server in the database.
MAX_LOG_LINES = 5000


def _resolve_server_directory(
    settings: Settings, directory: str | None, server_name: str | None = None
) -> Path:
    """Resolve and validate a server directory path.

    The resolved directory must live inside the configured servers base
    directory. Relative names are joined under the base directory. Absolute
    paths are rejected unless they are already inside the base directory.
    """

    base_dir = settings.servers_dir.resolve()

    if directory:
        candidate = Path(directory)
        if not candidate.is_absolute():
            candidate = base_dir / candidate
        resolved = candidate.resolve()
    elif server_name:
        # Default: <servers_dir>/<server_name>
        resolved = base_dir / server_name
    else:
        raise DomainError("A server directory or name is required.")

    # Prevent directory traversal: the resolved path must be inside base_dir.
    try:
        resolved.relative_to(base_dir)
    except ValueError:
        raise DomainError(
            f"Server directory must be inside {base_dir}."
        ) from None

    return resolved


def _is_port_available(port: int) -> bool:
    """Best-effort check whether a TCP port is available on localhost."""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) != 0


def _default_software_properties(software_type: str) -> dict:
    """Return default version for a software type."""

    return {
        "vanilla": "1.20.1",
        "paper": "1.20.1",
        "fabric": "1.20.1",
        "forge": "1.20.1",
    }.get(software_type, "1.20.1")


def _validate_create_request(
    db: Session,
    settings: Settings,
    payload: ServerCreateRequest,
) -> None:
    """Validate a server creation request without side effects."""

    # Unique name (case-insensitive).
    existing = db.scalar(
        select(Server).where(func.lower(Server.name) == func.lower(payload.name))
    )
    if existing is not None:
        raise ConflictError(f"A server named '{payload.name}' already exists.")

    # Directory.
    directory = _resolve_server_directory(settings, payload.directory, payload.name)
    if directory.exists() and not directory.is_dir():
        raise DomainError(f"Server path '{directory}' exists but is not a directory.")

    # Port uniqueness across all servers.
    existing_port = db.scalar(select(Server).where(Server.port == payload.port))
    if existing_port is not None:
        raise ConflictError(f"Port {payload.port} is already in use by another server.")

    # Port availability best-effort.
    if not _is_port_available(payload.port):
        raise ConflictError(f"Port {payload.port} is not available on this host.")


def create_server(
    db: Session,
    *,
    settings: Settings,
    payload: ServerCreateRequest,
    owner: User,
    meta: RequestMeta | None = None,
) -> Server:
    """Create a new server record and initialize its directory."""

    _validate_create_request(db, settings, payload)

    directory = _resolve_server_directory(settings, payload.directory, payload.name)
    directory.mkdir(parents=True, exist_ok=True)
    # Owner-only permissions.
    directory.chmod(0o700)

    unique_id = str(uuid.uuid4())
    version = payload.version or _default_software_properties(payload.software_type)

    server = Server(
        owner_id=owner.id,
        unique_id=unique_id,
        name=payload.name,
        software_type=payload.software_type,
        version=version,
        directory=str(directory),
        port=payload.port,
        max_players=payload.max_players,
        online_mode=payload.online_mode,
        difficulty=payload.difficulty,
        gamemode=payload.gamemode,
        view_distance=payload.view_distance,
        motd=payload.motd,
        status="stopped",
    )
    db.add(server)
    try:
        db.flush()
    except Exception as exc:
        db.rollback()
        raise ConflictError("Failed to create server record.") from exc

    record_audit(
        db,
        action="server.created",
        actor=owner,
        detail=f"Created server '{server.name}' ({payload.software_type} {version}).",
        meta=meta,
    )
    db.commit()
    db.refresh(server)
    return server


def get_server(db: Session, server_id: int) -> Server | None:
    return db.get(Server, server_id)


def get_server_or_404(db: Session, server_id: int) -> Server:
    server = get_server(db, server_id)
    if server is None:
        raise NotFoundError("Server not found.")
    return server


def _check_server_access(
    server: Server,
    actor: User,
) -> None:
    """Enforce ownership and admin access rules."""

    is_owner = server.owner_id == actor.id
    is_admin = actor.role == "admin"

    if not is_owner and not is_admin:
        raise ForbiddenError("You do not have access to this server.")


def list_servers(
    db: Session,
    *,
    actor: User,
    search: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[Server], int]:
    """List servers visible to the actor.

    Administrators see all servers; normal users see only their own.
    """

    if actor.role == "admin":
        stmt = select(Server)
        count_stmt = select(func.count(Server.id))
    else:
        stmt = select(Server).where(Server.owner_id == actor.id)
        count_stmt = select(func.count(Server.id)).where(Server.owner_id == actor.id)

    if search:
        term = f"%{search.strip().lower()[:64]}%"
        like = Server.name.ilike(term)
        stmt = stmt.where(like)
        count_stmt = count_stmt.where(like)

    stmt = stmt.order_by(Server.created_at.desc()).limit(limit).offset(offset)
    total = int(db.scalar(count_stmt) or 0)
    return list(db.scalars(stmt).all()), total


def update_server(
    db: Session,
    *,
    server: Server,
    actor: User,
    payload: ServerUpdateRequest,
    meta: RequestMeta | None = None,
) -> Server:
    """Update configuration of a stopped server."""

    _check_server_access(server, actor)

    if server.status != "stopped":
        raise DomainError("Server configuration can only be changed when stopped.")

    changes: list[str] = []
    data = payload.model_dump(exclude_unset=True)

    if "name" in data and data["name"] is not None and data["name"] != server.name:
        existing = db.scalar(
            select(Server).where(func.lower(Server.name) == func.lower(data["name"]))
        )
        if existing is not None:
            raise ConflictError(f"A server named '{data['name']}' already exists.")
        server.name = data["name"]
        changes.append(f"name={data['name']}")

    if "port" in data and data["port"] is not None and data["port"] != server.port:
        existing_port = db.scalar(select(Server).where(Server.port == data["port"]))
        if existing_port is not None:
            raise ConflictError(f"Port {data['port']} is already in use.")
        if not _is_port_available(data["port"]):
            raise ConflictError(f"Port {data['port']} is not available on this host.")
        server.port = data["port"]
        changes.append(f"port={data['port']}")

    if "max_players" in data and data["max_players"] is not None:
        server.max_players = data["max_players"]
        changes.append(f"max_players={data['max_players']}")

    if "online_mode" in data and data["online_mode"] is not None:
        server.online_mode = data["online_mode"]
        changes.append(f"online_mode={str(data['online_mode']).lower()}")

    if "difficulty" in data and data["difficulty"] is not None:
        server.difficulty = data["difficulty"]
        changes.append(f"difficulty={data['difficulty']}")

    if "gamemode" in data and data["gamemode"] is not None:
        server.gamemode = data["gamemode"]
        changes.append(f"gamemode={data['gamemode']}")

    if "view_distance" in data and data["view_distance"] is not None:
        server.view_distance = data["view_distance"]
        changes.append(f"view_distance={data['view_distance']}")

    if "motd" in data and data["motd"] is not None:
        server.motd = data["motd"]
        changes.append(f"motd={data['motd']}")

    if not changes:
        return server

    record_audit(
        db,
        action="server.updated",
        actor=actor,
        detail=f"Updated server '{server.name}': {', '.join(changes)}.",
        meta=meta,
    )
    db.commit()
    db.refresh(server)
    return server


def delete_server(
    db: Session,
    *,
    server: Server,
    actor: User,
    meta: RequestMeta | None = None,
) -> None:
    """Delete a server record and its directory contents.

    The server must be stopped. Its directory is removed from disk.
    """

    _check_server_access(server, actor)

    if server.status != "stopped":
        raise DomainError("Only stopped servers can be deleted.")

    server_name = server.name
    server_id = server.id
    directory = Path(server.directory)

    db.delete(server)
    record_audit(
        db,
        action="server.deleted",
        actor=actor,
        detail=f"Deleted server '{server_name}' (id={server_id}).",
        meta=meta,
    )
    db.commit()

    # Remove the server directory from disk after the DB commit succeeds.
    if directory.exists() and directory.is_dir():
        try:
            shutil.rmtree(directory)
        except OSError as exc:
            logger.exception("Failed to remove server directory %s", directory)
            raise DomainError(
                f"Server record deleted but directory could not be removed: {exc}"
            ) from exc


def write_server_properties(server: Server) -> None:
    """Write server.properties from the server record.

    Validates all values before writing. Never accepts arbitrary executable
    commands from API parameters.
    """

    directory = Path(server.directory)
    if not directory.exists():
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o700)

    properties = {
        "motd": server.motd,
        "gamemode": server.gamemode,
        "difficulty": server.difficulty,
        "view-distance": str(server.view_distance),
        "max-players": str(server.max_players),
        "online-mode": str(server.online_mode).lower(),
        "server-port": str(server.port),
    }

    # Validate each value before writing.
    _validate_property_value("motd", properties["motd"], max_length=128)
    _validate_property_value("gamemode", properties["gamemode"], allowed=SERVER_GAMEMODES)
    _validate_property_value("difficulty", properties["difficulty"], allowed=SERVER_DIFFICULTIES)
    _validate_property_value("view-distance", properties["view-distance"], is_int=True, min_v=2, max_v=32)
    _validate_property_value("max-players", properties["max-players"], is_int=True, min_v=1, max_v=1000)
    _validate_property_value("online-mode", properties["online-mode"], allowed=("true", "false"))
    _validate_property_value("server-port", properties["server-port"], is_int=True, min_v=1024, max_v=65535)

    lines = [f"{key}={value}" for key, value in properties.items()]
    properties_path = directory / "server.properties"
    properties_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    properties_path.chmod(0o600)

    # Write eula.txt for all supported software types.
    eula_path = directory / "eula.txt"
    if not eula_path.exists():
        eula_path.write_text("eula=true\n", encoding="utf-8")
        eula_path.chmod(0o600)


def _validate_property_value(
    key: str,
    value: str,
    *,
    allowed: tuple[str, ...] | None = None,
    is_int: bool = False,
    min_v: int | None = None,
    max_v: int | None = None,
    max_length: int | None = None,
) -> None:
    if max_length is not None and len(value) > max_length:
        raise DomainError(f"{key} is too long (max {max_length} characters).")
    if allowed is not None and value.lower() not in allowed:
        raise DomainError(f"{key} must be one of: {', '.join(allowed)}.")
    if is_int:
        try:
            int_value = int(value)
        except ValueError:
            raise DomainError(f"{key} must be an integer.") from None
        if min_v is not None and int_value < min_v:
            raise DomainError(f"{key} must be at least {min_v}.")
        if max_v is not None and int_value > max_v:
            raise DomainError(f"{key} must be at most {max_v}.")


async def _launch_process(
    server: Server,
    process_manager: ProcessManager,
    output_callback: Callable[[str], None] | None = None,
) -> None:
    """Launch the Minecraft server Java process."""

    directory = Path(server.directory)
    jar_path = directory / "server.jar"

    cmd = [
        "java",
        "-Xms512M",
        "-Xmx1G",
        "-jar",
        str(jar_path),
        "nogui",
    ]

    process = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(directory),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    async def pump_stdout() -> None:
        assert process.stdout is not None
        while True:
            line = await process.stdout.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").rstrip("\n")
            if output_callback:
                try:
                    output_callback(text)
                except Exception:  # pragma: no cover
                    pass

    async def pump_stderr() -> None:
        assert process.stderr is not None
        while True:
            line = await process.stderr.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").rstrip("\n")
            if output_callback:
                try:
                    output_callback(text)
                except Exception:  # pragma: no cover
                    pass

    process_manager.register(
        server.id,
        process,
        stdout_callback=lambda line: None,  # Captured by pump tasks below
        stderr_callback=lambda line: None,
    )

    # Start pump tasks.
    handle = process_manager.get(server.id)
    if handle is not None:
        handle.stdout_task = asyncio.create_task(pump_stdout())
        handle.stderr_task = asyncio.create_task(pump_stderr())


def start_server(
    db: Session,
    *,
    server: Server,
    actor: User,
    process_manager: ProcessManager,
    settings: Settings,
    meta: RequestMeta | None = None,
    output_callback: Callable[[str], None] | None = None,
) -> Server:
    """Start a Minecraft server process.

    The server must be stopped. A Java process is launched with the server
    JAR. Output is captured via ``output_callback``.
    """

    _check_server_access(server, actor)

    if process_manager.has(server.id):
        handle = process_manager.get(server.id)
        if handle is not None and handle.is_running:
            raise ConflictError(f"Server '{server.name}' is already running.")

    if server.status not in ("stopped", "error"):
        raise DomainError(f"Cannot start server in '{server.status}' state.")

    directory = Path(server.directory)
    if not directory.exists():
        raise NotFoundError(f"Server directory '{directory}' does not exist.")

    jar_path = directory / "server.jar"
    if not jar_path.exists():
        raise DomainError(
            f"Server software (server.jar) is not installed in {directory}. "
            "Import or install software before starting."
        )

    # Write server.properties before starting.
    write_server_properties(server)

    loop = asyncio.get_event_loop()
    loop.create_task(_launch_process(server, process_manager, output_callback))

    server.status = "starting"
    record_audit(
        db,
        action="server.started",
        actor=actor,
        detail=f"Start requested for server '{server.name}'.",
        meta=meta,
    )
    db.commit()
    db.refresh(server)
    return server


async def _stop_process(process_manager: ProcessManager, server_id: int) -> int:
    return await process_manager.stop(server_id)


def stop_server(
    db: Session,
    *,
    server: Server,
    actor: User,
    process_manager: ProcessManager,
    meta: RequestMeta | None = None,
) -> Server:
    """Stop a running Minecraft server process."""

    _check_server_access(server, actor)

    handle = process_manager.get(server.id)
    if handle is None or not handle.is_running:
        server.status = "stopped"
        server.process_id = None
        server.stopped_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.commit()
        db.refresh(server)
        return server

    loop = asyncio.get_event_loop()
    # Schedule the async stop; we don't block the route handler on it.
    loop.create_task(_stop_process(process_manager, server.id))

    server.status = "stopping"
    record_audit(
        db,
        action="server.stopped",
        actor=actor,
        detail=f"Stop requested for server '{server.name}'.",
        meta=meta,
    )
    db.commit()
    db.refresh(server)
    return server


def restart_server(
    db: Session,
    *,
    server: Server,
    actor: User,
    process_manager: ProcessManager,
    settings: Settings,
    meta: RequestMeta | None = None,
    output_callback: Callable[[str], None] | None = None,
) -> Server:
    """Restart a server: stop then start."""

    _check_server_access(server, actor)

    if server.status not in ("running", "stopped", "error"):
        raise DomainError(f"Cannot restart server in '{server.status}' state.")

    # Stop first if running.
    if server.status == "running":
        stop_server(
            db, server=server, actor=actor,
            process_manager=process_manager, meta=meta,
        )

    return start_server(
        db,
        server=server,
        actor=actor,
        process_manager=process_manager,
        settings=settings,
        meta=meta,
        output_callback=output_callback,
    )


def capture_log(
    db: Session,
    server_id: int,
    stream: str,
    line: str,
) -> None:
    """Persist a log line for a server."""

    try:
        log_entry = ServerLog(server_id=server_id, stream=stream, line=line)
        db.add(log_entry)
        db.commit()
        # Trim old logs.
        db.execute(
            delete(ServerLog).where(
                ServerLog.server_id == server_id,
                ServerLog.id.notin_(
                    select(ServerLog.id)
                    .where(ServerLog.server_id == server_id)
                    .order_by(ServerLog.created_at.desc())
                    .limit(MAX_LOG_LINES)
                ),
            )
        )
        db.commit()
    except Exception:  # pragma: no cover - logging resilience
        db.rollback()
        logger.exception("Failed to capture log line for server %s", server_id)


def reconcile_processes(
    db: Session,
    process_manager: ProcessManager,
) -> list[int]:
    """Reconcile in-memory process state with actual subprocess state.

    Returns a list of server IDs whose status changed.
    """

    changed: list[int] = []
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    for server_id in list(process_manager.running_ids()):
        handle = process_manager.get(server_id)
        if handle is None:
            continue
        process = handle.process
        if process.returncode is not None:
            # Process has terminated.
            handle.exit_code = process.returncode
            handle.terminated = True

            server = db.get(Server, server_id)
            if server is not None:
                if process.returncode == 0:
                    server.status = "stopped"
                else:
                    server.status = "error"
                server.stopped_at = now
                db.commit()
                changed.append(server_id)

            # Drain tasks.
            for task in (handle.stdout_task, handle.stderr_task):
                if task is not None and not task.done():
                    task.cancel()

            process_manager.unregister(server_id)

    return changed
