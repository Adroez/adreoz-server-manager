"""Minecraft server lifecycle management.

All server operations enforce ownership: normal users can only act on servers
they own; administrators can act on any server. Server directories are
resolved relative to the configured servers base directory and are strictly
contained to prevent directory traversal.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import signal
import socket
import subprocess
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, selectinload

from app.core.config import Settings
from app.core.java import JavaRuntimeManager
from app.core.jvm_args import parse_jvm_args
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
from app.services.compat import check_compatibility
from app.services.errors import ConflictError, DomainError, ForbiddenError, NotFoundError
from app.services.operations import OperationType, acquire_operation_lock
from app.services.software_install import SoftwareInstallService

# Maximum number of log lines retained per server in the database.
MAX_LOG_LINES = 5000

logger = logging.getLogger("asm.servers")


def _resolve_server_directory(
    settings: Settings, directory: str | None, server_name: str | None = None
) -> Path:
    """Resolve and validate a server directory path.

    The resolved directory must live strictly inside the configured servers
    base directory. Relative names are joined under the base directory.
    Absolute paths are rejected unless they are already inside the base
    directory. The base directory itself and paths that escape via symlinks
    are rejected.
    """

    base_dir = settings.servers_dir.resolve()

    if directory:
        candidate = Path(directory)
        if not candidate.is_absolute():
            candidate = base_dir / candidate
        resolved = candidate.resolve()
    elif server_name:
        # Default: <servers_dir>/<server_name> - must resolve to detect symlinks.
        resolved = (base_dir / server_name).resolve()
    else:
        raise DomainError("A server directory or name is required.")

    # Prevent directory traversal: the resolved path must be strictly inside
    # base_dir (not equal to it). This rejects ".", "..", and symlink escapes.
    try:
        resolved.relative_to(base_dir)
    except ValueError:
        # Do not echo the absolute host path back to the client.
        raise DomainError(
            "Server directory must be inside the configured servers directory."
        ) from None

    # Reject the base directory itself (e.g., directory=".").
    if resolved == base_dir:
        raise DomainError("Server directory cannot be the servers base directory.")

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
        raise DomainError(
            "The requested server path exists but is not a directory."
        )

    # Prevent overlapping/duplicate server directories.
    # Check against all existing server directories (resolved).
    for existing_server in db.scalars(select(Server)).all():
        existing_dir = Path(existing_server.directory).resolve()
        try:
            # Reject if the new directory is inside an existing one,
            # or an existing one is inside the new directory, or they are equal.
            directory.relative_to(existing_dir)
            raise ConflictError(
                "The requested server directory overlaps the directory of "
                f"existing server '{existing_server.name}'."
            )
        except ValueError:
            pass
        try:
            existing_dir.relative_to(directory)
            raise ConflictError(
                f"The directory of existing server '{existing_server.name}' "
                "overlaps the requested server directory."
            )
        except ValueError:
            pass

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
    session_factory,
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

    # Validate the selected Java runtime, if any.
    java_runtime_id = payload.java_runtime_id
    if java_runtime_id is not None:
        from app.models.java_runtime import JavaRuntime as JavaRuntimeModel

        runtime = db.get(JavaRuntimeModel, java_runtime_id)
        if runtime is None:
            raise NotFoundError("Selected Java runtime not found.")

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
        java_runtime_id=java_runtime_id,
        software_version=payload.software_version if hasattr(payload, 'software_version') else None,
        install_status="pending",
        jvm_args=payload.jvm_args or None,
    )
    db.add(server)
    try:
        db.flush()
    except Exception as exc:
        db.rollback()
        raise ConflictError("Failed to create server record.") from exc

    # Install server software (download JAR, verify, etc.).
    try:
        software_service = SoftwareInstallService(settings, session_factory)
        install_result = software_service.install_software(
            db,
            server=server,
            software_type=payload.software_type,
            version=version,
            build_version=getattr(payload, 'build_version', None),
            actor=owner,
            meta=meta,
        )
        # Refresh to get updated fields.
        db.refresh(server)
    except Exception as exc:
        # Mark installation as failed but keep the server record. The server
        # was created successfully; only software installation failed. Callers
        # can retry installation via the software install endpoint.
        server.install_status = "failed"
        server.install_error = str(exc)
        db.commit()
        db.refresh(server)
        logger.warning(
            "Server '%s' created but software installation failed: %s",
            server.name,
            exc,
        )

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

    if "java_runtime_id" in data:
        new_runtime_id = data["java_runtime_id"]
        if new_runtime_id != server.java_runtime_id:
            if new_runtime_id is not None:
                from app.models.java_runtime import JavaRuntime as JavaRuntimeModel

                runtime = db.get(JavaRuntimeModel, new_runtime_id)
                if runtime is None:
                    raise NotFoundError("Java runtime not found.")
            server.java_runtime_id = new_runtime_id
            changes.append(f"java_runtime_id={new_runtime_id}")

    if "jvm_args" in data:
        # ``None`` or an empty string clears the override.
        new_jvm_args = data["jvm_args"] or None
        if new_jvm_args != server.jvm_args:
            server.jvm_args = new_jvm_args
            changes.append(
                f"jvm_args={new_jvm_args if new_jvm_args is not None else '(cleared)'}"
            )

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

    This operation is serialized with all other lifecycle and backup operations.
    """

    _check_server_access(server, actor)

    if server.status != "stopped":
        raise DomainError("Only stopped servers can be deleted.")

    with acquire_operation_lock(server.id, OperationType.DELETE):
        # Re-check status after acquiring lock.
        db.refresh(server)
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
    # The EULA is never auto-accepted; the server operator must review and
    # accept Mojang's EULA manually before the server can start.
    eula_path = directory / "eula.txt"
    if not eula_path.exists():
        eula_path.write_text("eula=false\n", encoding="utf-8")
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


async def _monitor_process(
    server_id: int,
    process: asyncio.subprocess.Process,
    process_manager: ProcessManager,
    session_factory,
) -> None:
    """Update the database when a launched process exits.

    The check against the currently registered handle prevents an old monitor
    from overwriting state after a restart has already registered a new
    process for the same server.
    """

    try:
        returncode = await process.wait()
    except Exception:  # pragma: no cover - monitor resilience
        logger.exception("Failed to monitor process for server %s", server_id)
        return

    handle = process_manager.get(server_id)
    if handle is not None and handle.process is not process:
        # A newer process replaced this one (restart); ignore the stale exit.
        return

    # Capture the intent before mutating the handle: ``terminated`` below is
    # set unconditionally and therefore cannot tell us why the process exited.
    stop_requested = handle is not None and handle.stop_requested

    if handle is not None:
        handle.exit_code = returncode
        handle.terminated = True
    process_manager.unregister(server_id)

    try:
        with session_factory() as db:
            server = db.get(Server, server_id)
            if server is None:
                return
            # A graceful shutdown (panel stop, SIGTERM) must be recorded as
            # "stopped". A non-zero exit the panel did not ask for is a crash.
            is_sigterm = returncode == -signal.SIGTERM
            if returncode == 0 or stop_requested or is_sigterm:
                server.status = "stopped"
            else:
                server.status = "error"
            server.process_id = None
            server.stopped_at = datetime.now(timezone.utc).replace(tzinfo=None)
            db.commit()
    except Exception:  # pragma: no cover - monitor resilience
        logger.exception("Failed to record exit for server %s", server_id)


def _jar_filename_for_server(server: Server) -> str:
    """Return the expected JAR filename for a server's software type.

    Vanilla: server.jar
    Paper: paper-{version}-{build}.jar
    Fabric: fabric-server-launch-{loader}.jar
    Forge: forge-{version}-{forge}-installer.jar (installer; run on first launch)
    """
    software_type = server.software_type
    version = server.version
    build = server.software_version

    if software_type == "vanilla":
        return "server.jar"
    if software_type == "paper":
        return f"paper-{version}-{build}.jar" if build else f"paper-{version}.jar"
    if software_type == "fabric":
        return f"fabric-loader-{build}.jar" if build else "fabric-loader.jar"
    if software_type == "forge":
        return f"forge-{version}-{build}-installer.jar" if build else "forge-installer.jar"
    return "server.jar"


def _forge_server_jar(directory: Path, version: str, build: str | None) -> Path | None:
    """Return the path to the generated Forge server JAR, if the installer
    has already been run. Otherwise return None.

    Modern Forge installers (1.13+) generate a shim JAR + libraries/ directory.
    We look for common patterns: universal jar, shim jar, or the run.sh script.
    """
    if build is None:
        return None

    # Pattern 1: universal jar (legacy)
    candidate = directory / f"forge-{version}-{build}-universal.jar"
    if candidate.is_file():
        return candidate

    # Pattern 2: shim jar (modern)
    candidate = directory / f"forge-{version}-{build}.jar"
    if candidate.is_file():
        return candidate

    # Pattern 3: forge-{version}-{build}-server.jar (some versions)
    candidate = directory / f"forge-{version}-{build}-server.jar"
    if candidate.is_file():
        return candidate

    return None


def _merge_jvm_defaults(extra: list[str]) -> list[str]:
    """Return the default heap flags that the caller has not overridden."""

    lowered = [token.lower() for token in extra]
    defaults: list[str] = []
    if not any(token.startswith("-xms") for token in lowered):
        defaults.append("-Xms512M")
    if not any(token.startswith("-xmx") for token in lowered):
        defaults.append("-Xmx1G")
    return defaults


def _write_forge_user_jvm_args(directory: Path, jvm_args: list[str]) -> None:
    """Write managed JVM arguments to Forge's ``user_jvm_args.txt``."""

    path = directory / "user_jvm_args.txt"
    content = "# Managed by Adreoz Server Manager (per-server JVM arguments).\n"
    content += "\n".join(jvm_args) + "\n"
    try:
        path.write_text(content, encoding="utf-8")
        path.chmod(0o600)
    except OSError:
        logger.exception("Failed to write Forge user_jvm_args.txt in %s", directory)


def _forge_launch_command(
    directory: Path,
    version: str,
    build: str | None,
    java_executable: str,
    jvm_args: list[str] | None = None,
) -> tuple[list[str], dict[str, str]] | None:
    """Build the launch command and environment for a Forge server.

    Returns a tuple of (command_list, env_dict), or None if the server is
    not properly installed. Supports both legacy (universal jar) and modern
    (run.sh + argument files) Forge installations.
    """
    if build is None:
        return None

    extra = list(jvm_args or [])

    # ``run.sh`` invokes a bare ``java``. When a managed runtime was selected,
    # make it the first entry on PATH (and expose JAVA_HOME) so the script
    # launches the right JVM.
    script_env: dict[str, str] = {}
    java_path = Path(java_executable)
    if java_path.is_absolute():
        script_env["JAVA_HOME"] = str(java_path.parent.parent)
        script_env["PATH"] = f"{java_path.parent}{os.pathsep}{os.environ.get('PATH', '')}"

    # Check for run.sh script (modern Forge 1.17+)
    run_sh = directory / "run.sh"
    if run_sh.is_file():
        # Modern Forge reads JVM options from user_jvm_args.txt.
        if extra:
            _write_forge_user_jvm_args(directory, extra)
        return (["bash", str(run_sh), "nogui"], script_env)

    # Check for run.bat on Windows (not applicable here but for completeness)
    run_bat = directory / "run.bat"
    if run_bat.is_file():
        if extra:
            _write_forge_user_jvm_args(directory, extra)
        return (["cmd", "/c", str(run_bat), "nogui"], script_env)

    # Fallback: look for a server JAR (legacy or modern shim)
    server_jar = _forge_server_jar(directory, version, build)
    if server_jar is not None and server_jar.is_file():
        return (
            [
                java_executable,
                *_merge_jvm_defaults(extra),
                *extra,
                "-jar",
                str(server_jar),
                "nogui",
            ],
            script_env,
        )

    # Check for argument files (modern Forge without run.sh)
    # Some versions create @user_jvm_args.txt and @forge-*.txt
    libraries_dir = directory / "libraries"
    if libraries_dir.is_dir():
        # Try to find the main class and build classpath from libraries
        # This is complex; for now, fall back to run.sh expectation.
        logger.warning(
            "Forge installation at %s has libraries/ but no run.sh. "
            "Run the installer with --installServer to generate run.sh.",
            directory,
        )

    return None


async def _run_forge_installer(
    directory: Path,
    java_executable: str,
    installer_jar: str,
) -> None:
    """Run the Forge installer to generate server files.

    Executes ``java -jar <installer> --installServer`` in the server
    directory. This generates forge-{version}-{forge}.jar, libraries/,
    run scripts, and argument files. Raises DomainError on failure.
    """
    import subprocess

    installer_path = directory / installer_jar
    if not installer_path.is_file():
        raise DomainError(
            f"Forge installer JAR '{installer_jar}' not found. Reinstall software."
        )

    cmd = [
        java_executable,
        "-jar",
        str(installer_path),
        "--installServer",
    ]
    try:
        result = subprocess.run(
            cmd,
            cwd=str(directory),
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise DomainError(
            f"Failed to execute Forge installer: {exc}"
        ) from exc

    if result.returncode != 0:
        logger.error(
            "Forge installer failed (exit %d): %s",
            result.returncode,
            result.stderr.strip()[:500],
        )
        raise DomainError(
            f"Forge installer failed (exit {result.returncode}). "
            "Check server logs for details."
        )


async def _launch_process(
    server: Server,
    process_manager: ProcessManager,
    session_factory,
    console_manager=None,
    java_executable: str = "java",
) -> asyncio.subprocess.Process:
    """Launch the Minecraft server Java process.

    Captures stdout/stderr to the database via :func:`capture_log` and
    broadcasts live output to WebSocket console clients via ``console_manager``.

    Exactly one reader task per stream is created by ProcessManager.register.
    A monitor task records the exit status when the process terminates.

    ``java_executable`` is the resolved path to the Java binary for this
    server's selected runtime (defaults to system ``java``).

    The launch command is software-type specific:
      - Vanilla: java -jar server.jar nogui
      - Paper:   java -jar paper-{ver}-{build}.jar nogui
      - Fabric:  java -cp fabric-loader-{loader}.jar:server.jar
                 net.fabricmc.loader.impl.launch.knot.KnotServer nogui
      - Forge:   run installer (if needed), then
                 java -jar forge-{ver}-{forge}.jar nogui
    """

    directory = Path(server.directory)
    jar_filename = _jar_filename_for_server(server)
    jar_path = directory / jar_filename

    extra_env: dict[str, str] = {}

    # Per-server JVM arguments (validated when they were saved).
    jvm_extra = list(parse_jvm_args(server.jvm_args).tokens)

    # Forge requires running the installer before first launch. The installer
    # JAR was downloaded during software installation; running it generates
    # the server JAR, libraries/, and run.sh (modern Forge) or universal jar.
    if server.software_type == "forge":
        build = server.software_version
        # Try to get a launch command (supports run.sh, universal jar, shim jar).
        forge_result = _forge_launch_command(
            directory, server.version, build, java_executable, jvm_extra
        )
        if forge_result is None:
            # Installer has not been run yet; run it now.
            await _run_forge_installer(directory, java_executable, jar_filename)
            forge_result = _forge_launch_command(
                directory, server.version, build, java_executable, jvm_extra
            )
            if forge_result is None:
                raise DomainError(
                    "Forge installer did not generate a launchable server. "
                    "Expected run.sh or a server JAR."
                )
        cmd, extra_env = forge_result

    # Fabric requires both the Fabric Loader JAR and the vanilla server JAR.
    # The launch uses a classpath with both JARs and the Fabric main class.
    if server.software_type == "fabric":
        build = server.software_version
        loader_jar = directory / f"fabric-loader-{build}.jar" if build else directory / "fabric-loader.jar"
        server_jar = directory / "server.jar"
        if not loader_jar.is_file():
            raise DomainError(
                f"Fabric Loader JAR '{loader_jar.name}' is not installed. "
                "Install software before starting."
            )
        if not server_jar.is_file():
            raise DomainError(
                "Vanilla server JAR 'server.jar' is not installed. "
                "Install software before starting."
            )
        classpath = f"{loader_jar}:{server_jar}"
        cmd = [
            java_executable,
            *_merge_jvm_defaults(jvm_extra),
            *jvm_extra,
            "-cp",
            classpath,
            "net.fabricmc.loader.impl.launch.knot.KnotServer",
            "nogui",
        ]
    else:
        if not jar_path.is_file():
            raise DomainError(
                f"Server JAR '{jar_path.name}' is not installed. "
                "Install software before starting."
            )
        cmd = [
            java_executable,
            *_merge_jvm_defaults(jvm_extra),
            *jvm_extra,
            "-jar",
            str(jar_path),
            "nogui",
        ]

    # Merge extra environment variables (e.g., JAVA_HOME for Forge run.sh)
    env = os.environ.copy()
    env.update(extra_env)

    process = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(directory),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )

    async def output_callback(server_id: int, stream_name: str, text: str) -> None:
        """Broadcast a line to console clients, then persist it off-loop.

        Database writes run in a worker thread so a slow disk can never stall
        the event loop (and therefore every other request the panel serves).
        """

        if console_manager is not None:
            try:
                await console_manager.broadcast(server_id, stream_name, text)
            except Exception:  # pragma: no cover - broadcast resilience
                logger.exception(
                    "Failed to broadcast console line for server %s", server_id
                )

        await asyncio.to_thread(
            _persist_log, session_factory, server_id, stream_name, text
        )

    process_manager.register(server.id, process, output_callback=output_callback)
    asyncio.create_task(
        _monitor_process(server.id, process, process_manager, session_factory)
    )
    return process


async def start_server(
    db: Session,
    *,
    server: Server,
    actor: User,
    process_manager: ProcessManager,
    settings: Settings,
    session_factory,
    console_manager=None,
    meta: RequestMeta | None = None,
) -> Server:
    """Start a Minecraft server process.

    The server must be stopped. A Java process is launched with the server
    JAR. Output is captured to the database and broadcast to WebSocket
    console clients.
    """

    _check_server_access(server, actor)

    with acquire_operation_lock(server.id, OperationType.START):
        if process_manager.has(server.id):
            handle = process_manager.get(server.id)
            if handle is not None and handle.is_running:
                raise ConflictError(f"Server '{server.name}' is already running.")

        if server.status not in ("stopped", "error"):
            raise DomainError(f"Cannot start server in '{server.status}' state.")

        directory = Path(server.directory)
        if not directory.exists():
            raise NotFoundError(
                "The server directory does not exist on this host."
            )

        jar_filename = _jar_filename_for_server(server)
        jar_path = directory / jar_filename
        if not jar_path.exists():
            raise DomainError(
                f"Server software ({jar_filename}) is not installed. "
                "Import or install software before starting."
            )

        # Resolve and validate the Java runtime for this server.
        java_runtime_id = server.java_runtime_id
        java_executable = "java"
        runtime_major = None
        if java_runtime_id is not None:
            from app.models.java_runtime import JavaRuntime as JavaRuntimeModel

            runtime = db.get(JavaRuntimeModel, java_runtime_id)
            if runtime is None:
                raise NotFoundError("Selected Java runtime not found.")
            java_executable = runtime.executable_path
            runtime_major = runtime.major_version
            if not Path(java_executable).is_file() or not os.access(
                java_executable, os.X_OK
            ):
                raise DomainError(
                    f"Selected Java runtime '{runtime.name}' is not executable "
                    f"at {java_executable}."
                )
        else:
            # System default: validate that java is available on PATH.
            import shutil

            if shutil.which("java") is None:
                raise DomainError(
                    "No Java runtime is available on the system PATH. "
                    "Install or select a Java runtime before starting."
                )

        # Check Java / Minecraft compatibility only when a specific runtime is
        # selected. When no runtime is selected (system default), we proceed on
        # a best-effort basis since the runtime version is unknown.
        if java_runtime_id is not None:
            compatibility = check_compatibility(server.version, runtime_major)
            if not compatibility.compatible:
                raise DomainError(compatibility.message)

        # Write server.properties before starting.
        write_server_properties(server)

        process = await _launch_process(
            server,
            process_manager,
            session_factory,
            console_manager,
            java_executable=java_executable,
        )

        server.status = "running"
        server.process_id = process.pid
        server.started_at = datetime.now(timezone.utc).replace(tzinfo=None)
        server.stopped_at = None
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


async def stop_server(
    db: Session,
    *,
    server: Server,
    actor: User,
    process_manager: ProcessManager,
    meta: RequestMeta | None = None,
) -> Server:
    """Stop a running Minecraft server process.

    Stop operations are allowed to proceed even if other operations are in
    progress (e.g., backup, restore), as they may be needed to terminate
    a stuck process. Only concurrent stop/delete operations are blocked.
    """

    _check_server_access(server, actor)

    with acquire_operation_lock(server.id, OperationType.STOP):
        handle = process_manager.get(server.id)
        if handle is None or not handle.is_running:
            process_manager.unregister(server.id)
            server.status = "stopped"
            server.process_id = None
            server.stopped_at = datetime.now(timezone.utc).replace(tzinfo=None)
            db.commit()
            db.refresh(server)
            return server

        # Schedule the async stop; the process monitor records the final
        # stopped/error state once the process exits. Mark the stop intent
        # synchronously so an exit that wins the race is still classified as a
        # graceful stop rather than a crash.
        handle.stop_requested = True
        loop = asyncio.get_running_loop()
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


async def restart_server(
    db: Session,
    *,
    server: Server,
    actor: User,
    process_manager: ProcessManager,
    settings: Settings,
    session_factory,
    console_manager=None,
    meta: RequestMeta | None = None,
) -> Server:
    """Restart a server: stop then start.

    The entire restart operation is serialized with other lifecycle and
    backup operations via the shared operation lock.
    """

    _check_server_access(server, actor)

    if server.status not in ("running", "stopped", "error", "starting", "stopping"):
        raise DomainError(f"Cannot restart server in '{server.status}' state.")

    with acquire_operation_lock(server.id, OperationType.RESTART):
        # Stop any tracked process and wait for it to exit before launching a
        # replacement. The monitor task is superseded by the new handle.
        handle = process_manager.get(server.id)
        if handle is not None and handle.is_running:
            await process_manager.stop(server.id)
        if process_manager.has(server.id):
            process_manager.unregister(server.id)

        server.status = "stopped"
        server.process_id = None

        directory = Path(server.directory)
        if not directory.exists():
            raise NotFoundError(
                "The server directory does not exist on this host."
            )

        jar_filename = _jar_filename_for_server(server)
        jar_path = directory / jar_filename
        if not jar_path.exists():
            raise DomainError(
                f"Server software ({jar_filename}) is not installed. "
                "Import or install software before starting."
            )

        # Resolve and validate the Java runtime for this server.
        java_runtime_id = server.java_runtime_id
        java_executable = "java"
        runtime_major = None
        if java_runtime_id is not None:
            from app.models.java_runtime import JavaRuntime as JavaRuntimeModel

            runtime = db.get(JavaRuntimeModel, java_runtime_id)
            if runtime is None:
                raise NotFoundError("Selected Java runtime not found.")
            java_executable = runtime.executable_path
            runtime_major = runtime.major_version
            if not Path(java_executable).is_file() or not os.access(
                java_executable, os.X_OK
            ):
                raise DomainError(
                    f"Selected Java runtime '{runtime.name}' is not executable "
                    f"at {java_executable}."
                )
        else:
            # System default: validate that java is available on PATH.
            import shutil

            if shutil.which("java") is None:
                raise DomainError(
                    "No Java runtime is available on the system PATH. "
                    "Install or select a Java runtime before starting."
                )

        # Check Java / Minecraft compatibility only when a specific runtime is
        # selected. When no runtime is selected (system default), we proceed on
        # a best-effort basis since the runtime version is unknown.
        if java_runtime_id is not None:
            compatibility = check_compatibility(server.version, runtime_major)
            if not compatibility.compatible:
                raise DomainError(compatibility.message)

        # Write server.properties before starting.
        write_server_properties(server)

        process = await _launch_process(
            server,
            process_manager,
            session_factory,
            console_manager,
            java_executable=java_executable,
        )

        server.status = "running"
        server.process_id = process.pid
        server.started_at = datetime.now(timezone.utc).replace(tzinfo=None)
        server.stopped_at = None
        record_audit(
            db,
            action="server.started",
            actor=actor,
            detail=f"Start requested for server '{server.name}' (restart).",
            meta=meta,
        )
        db.commit()
        db.refresh(server)
    return server


# How often the log table is trimmed, in inserted lines. Trimming is a
# comparatively expensive DELETE, so it is not done for every single line.
_LOG_TRIM_INTERVAL = 100
_log_insert_counts: dict[int, int] = {}
_log_count_lock = threading.Lock()


def _persist_log(session_factory, server_id: int, stream: str, line: str) -> None:
    """Persist one captured line. Runs in a worker thread, never on the loop."""

    db = session_factory()
    try:
        capture_log(db, server_id, stream, line)
    finally:
        db.close()


def capture_log(
    db: Session,
    server_id: int,
    stream: str,
    line: str,
) -> None:
    """Persist a log line for a server and keep the table bounded."""

    try:
        db.add(ServerLog(server_id=server_id, stream=stream, line=line))
        db.commit()

        with _log_count_lock:
            count = _log_insert_counts.get(server_id, 0) + 1
            should_trim = count >= _LOG_TRIM_INTERVAL
            _log_insert_counts[server_id] = 0 if should_trim else count

        if should_trim:
            # Trim old logs (keep the newest MAX_LOG_LINES rows).
            db.execute(
                delete(ServerLog).where(
                    ServerLog.server_id == server_id,
                    ServerLog.id.notin_(
                        select(ServerLog.id)
                        .where(ServerLog.server_id == server_id)
                        .order_by(ServerLog.created_at.desc(), ServerLog.id.desc())
                        .limit(MAX_LOG_LINES)
                    ),
                )
            )
            db.commit()
    except Exception:  # pragma: no cover - logging resilience
        db.rollback()
        logger.exception("Failed to capture log line for server %s", server_id)


def _process_matches_server(pid: int | None, directory: Path) -> bool:
    """Return True when ``pid`` is a live process launched for ``directory``.

    PIDs are recycled by the kernel, so a recorded PID alone is not proof that
    the server is still running. The process command line must reference the
    server directory (the JAR path or working directory).
    """

    if pid is None or pid <= 0:
        return False

    import psutil

    try:
        proc = psutil.Process(pid)
        if not proc.is_running() or proc.status() in (
            psutil.STATUS_ZOMBIE,
            psutil.STATUS_DEAD,
        ):
            return False
        try:
            cmdline = proc.cmdline()
        except (psutil.AccessDenied, psutil.ZombieProcess):
            return False
        if not cmdline:
            return False
        try:
            cwd_matches = Path(proc.cwd()).resolve() == directory.resolve()
        except (psutil.Error, OSError):
            cwd_matches = False
        if cwd_matches:
            return True
        joined = " ".join(cmdline)
        return str(directory) in joined
    except (psutil.Error, OSError):
        return False


def reconcile_processes(
    db: Session,
    process_manager: ProcessManager,
) -> list[int]:
    """Reconcile recorded server state with the real process table.

    Runs at panel startup. A server is only considered running when its
    persisted PID refers to a live process that was actually launched for that
    server's directory. Servers whose process vanished are marked ``error``
    (or ``stopped`` when they were already stopping), so the UI never offers
    Stop/Console for a process that no longer exists.

    Returns a list of server IDs whose status changed.
    """

    changed: list[int] = []
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    running_servers = (
        db.query(Server)
        .filter(Server.status.in_(("running", "starting", "stopping")))
        .all()
    )

    for server in running_servers:
        directory = Path(server.directory)
        if _process_matches_server(server.process_id, directory):
            continue

        previous = server.status
        previous_pid = server.process_id
        server.status = "stopped" if previous == "stopping" else "error"
        server.process_id = None
        server.stopped_at = now
        db.commit()
        changed.append(server.id)
        logger.warning(
            "Server %s (%s) was '%s' with pid %s but no matching process; "
            "marked '%s'",
            server.id,
            server.name,
            previous,
            previous_pid,
            server.status,
        )

    # Drop stale in-memory handles for servers that are no longer running.
    for server_id in list(process_manager.running_ids()):
        handle = process_manager.get(server_id)
        if handle is not None and handle.process.returncode is not None:
            process_manager.unregister(server_id)

    return changed
