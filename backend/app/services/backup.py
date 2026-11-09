"""Minecraft server backup and restore management.

Backups are ``tar.gz`` archives stored in ``data/backups/`` — deliberately
outside every server's browsable directory. All operations are scoped to a
single server, validate the resolved paths, and use a shared per-server
operation lock to coordinate with server lifecycle operations (start, stop,
restart, delete).

Security properties:

* Backup archives are written to a temporary ``.part`` file and atomically
  renamed, so a failed create never leaves a half-written archive.
* Restores extract into a staging directory first, reject absolute paths,
  ``..`` components, symlinks, hard links, and device entries, and only then
  swap the staging tree into place. If the swap fails the previous server
  directory is restored.
* No archive content is ever executed, and no shell command is used.
* Error messages returned to clients are sanitized: host filesystem paths and
  tracebacks are logged server-side only.
"""

from __future__ import annotations

import errno
import logging
import os
import shutil
import tarfile
import tempfile
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Iterator

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.clock import as_utc, utcnow
from app.core.config import Settings
from app.core.process import ProcessManager
from app.models.backup import ServerBackup
from app.models.server import Server
from app.models.setting import AppSetting
from app.models.user import User
from app.services.audit import RequestMeta, record_audit
from app.services.errors import ConflictError, DomainError, ForbiddenError, NotFoundError
from app.services.operations import OperationType, acquire_operation_lock

logger = logging.getLogger("asm.backups")

# Maximum size of a single backup archive: 4 GB.
MAX_BACKUP_SIZE = 4 * 1024 * 1024 * 1024

# Maximum expanded size of a backup archive when restored: 8 GB.
MAX_EXPANDED_BACKUP_SIZE = 8 * 1024 * 1024 * 1024

# Free space kept in reserve while writing an archive.
FREE_SPACE_MARGIN = 64 * 1024 * 1024

# Backup statuses.
BACKUP_STATUS_CREATING = "creating"
BACKUP_STATUS_COMPLETED = "completed"
BACKUP_STATUS_FAILED = "failed"

# Statuses eligible for retention cleanup.
PRUNABLE_STATUSES = (BACKUP_STATUS_COMPLETED, BACKUP_STATUS_FAILED)


def _check_server_access(server: Server, actor: User) -> None:
    """Enforce ownership and admin access rules."""

    is_owner = server.owner_id == actor.id
    is_admin = actor.role == "admin"

    if not is_owner and not is_admin:
        raise ForbiddenError("You do not have access to this server.")


def _resolve_backup_path(settings: Settings, filename: str) -> Path:
    """Resolve a managed archive path inside the backups directory."""

    base_dir = settings.backups_dir.resolve()
    candidate = (base_dir / filename).resolve()

    try:
        candidate.relative_to(base_dir)
    except ValueError:
        raise DomainError("Backup path is outside the backups directory.") from None

    return candidate


def _resolve_server_directory(server: Server, settings: Settings) -> Path:
    """Resolve a server directory and verify it lives under ``servers_dir``."""

    base_dir = settings.servers_dir.resolve()
    resolved = Path(server.directory).resolve()

    try:
        resolved.relative_to(base_dir)
    except ValueError:
        raise DomainError("Server directory is outside the servers directory.") from None

    return resolved


def _to_dict(backup: ServerBackup) -> dict:
    return {
        "id": backup.id,
        "unique_id": backup.unique_id,
        "server_id": backup.server_id,
        "filename": backup.filename,
        "size_bytes": backup.size_bytes,
        "status": backup.status,
        "error": backup.error_message,
        "created_at": as_utc(backup.created_at),
    }


def _sanitize_error(exc: BaseException) -> str:
    """Return a safe, user-facing failure message.

    Never echo raw exception text: it can contain absolute host paths.
    """

    if isinstance(exc, OSError) and exc.errno == errno.ENOSPC:
        return "Insufficient disk space to complete the backup operation."
    if isinstance(exc, tarfile.ReadError):
        return "The backup archive is corrupted or unreadable."
    if isinstance(exc, DomainError):
        return exc.message
    return "The backup operation failed."


def _directory_size(path: Path) -> int:
    """Return the logical size of regular files below ``path``."""

    total = 0
    for root, _dirs, files in os.walk(path, followlinks=False):
        for name in files:
            candidate = Path(root) / name
            if candidate.is_symlink():
                continue
            try:
                total += candidate.stat().st_size
            except OSError:
                continue
    return total


def _ensure_disk_space(settings: Settings, server_dir: Path) -> None:
    """Fail early when the backups filesystem is clearly too small."""

    try:
        free = shutil.disk_usage(settings.backups_dir).free
    except OSError:
        return

    required = _directory_size(server_dir) + FREE_SPACE_MARGIN
    if free < required:
        raise DomainError("Insufficient disk space to create this backup.")


def _write_archive(server_dir: Path, target: Path) -> None:
    """Create a tar.gz archive of the server directory.

    Symlinks and special files are skipped so a backup can never capture a
    file outside the server directory through a link.
    """

    with tarfile.open(target, "w:gz") as tar:
        for root, dirs, files in os.walk(server_dir, followlinks=False):
            root_path = Path(root)
            relative_root = root_path.relative_to(server_dir)

            if relative_root != Path("."):
                tar.add(root_path, arcname=str(relative_root), recursive=False)

            # Never descend into symlinked directories.
            for directory in list(dirs):
                if (root_path / directory).is_symlink():
                    dirs.remove(directory)

            for filename in files:
                candidate = root_path / filename
                if candidate.is_symlink() or not candidate.is_file():
                    continue
                tar.add(
                    candidate,
                    arcname=str(candidate.relative_to(server_dir)),
                    recursive=False,
                )


def _validate_member(member: tarfile.TarInfo, staging: Path) -> None:
    """Reject unsafe archive entries before extracting anything."""

    if member.isdir() or member.isfile():
        pass
    else:
        # Symlinks, hard links, devices, and FIFOs are never needed in a
        # Minecraft save and are a classic archive escape vector.
        raise DomainError("Backup archive contains unsupported file entries.")

    name = member.name
    if not name or "\x00" in name:
        raise DomainError("Backup archive contains invalid paths.")
    if os.path.isabs(name):
        raise DomainError("Backup archive contains absolute paths.")

    parts = Path(name).parts
    if any(part in ("..",) for part in parts):
        raise DomainError("Backup archive contains unsafe paths.")

    target = (staging / name).resolve()
    try:
        target.relative_to(staging.resolve())
    except ValueError:
        raise DomainError("Backup archive contains unsafe paths.") from None


def _extract_archive(archive: Path, staging: Path) -> None:
    """Validate and extract an archive into a staging directory.

    Also calculates and validates the total expanded size before extraction.
    """

    try:
        with tarfile.open(archive, "r:gz") as tar:
            members = tar.getmembers()

            # Calculate total expanded size of regular files.
            total_size = 0
            for member in members:
                _validate_member(member, staging)
                if member.isfile():
                    if member.size < 0:
                        raise DomainError("Backup archive contains invalid file sizes.")
                    total_size += member.size

            if total_size > MAX_EXPANDED_BACKUP_SIZE:
                raise DomainError(
                    f"Backup archive exceeds maximum expanded size of "
                    f"{MAX_EXPANDED_BACKUP_SIZE // (1024**3)} GB."
                )

            # ``filter="data"`` adds defense in depth on Python 3.12+.
            tar.extractall(path=staging, members=members, filter="data")
    except tarfile.TarError as exc:
        raise DomainError("The backup archive is corrupted or unreadable.") from exc


def _get_setting(db: Session, key: str) -> str | None:
    return db.scalar(select(AppSetting.value).where(AppSetting.key == key))


def _set_setting(db: Session, key: str, value: str) -> None:
    setting = db.get(AppSetting, key)
    if setting is None:
        db.add(AppSetting(key=key, value=value))
    else:
        setting.value = value


def get_backup_settings(db: Session, server_id: int) -> dict:
    """Read a server's automatic backup settings from the key/value store."""

    enabled_raw = _get_setting(db, f"backup_enabled_{server_id}")
    interval_raw = _get_setting(db, f"backup_interval_{server_id}")
    max_raw = _get_setting(db, f"backup_max_{server_id}")

    try:
        interval = int(interval_raw) if interval_raw is not None else 24
    except (TypeError, ValueError):
        interval = 24
    try:
        max_backups = int(max_raw) if max_raw is not None else 10
    except (TypeError, ValueError):
        max_backups = 10

    return {
        "enabled": enabled_raw == "true",
        "interval_hours": max(1, min(interval, 8760)),
        "max_backups": max(1, min(max_backups, 100)),
    }


def update_backup_settings(
    db: Session,
    *,
    server: Server,
    actor: User | None,
    enabled: bool | None = None,
    interval_hours: int | None = None,
    max_backups: int | None = None,
    meta: RequestMeta | None = None,
) -> dict:
    """Update a server's automatic backup settings."""

    if enabled is not None:
        _set_setting(db, f"backup_enabled_{server.id}", "true" if enabled else "false")
    if interval_hours is not None:
        _set_setting(db, f"backup_interval_{server.id}", str(int(interval_hours)))
    if max_backups is not None:
        _set_setting(db, f"backup_max_{server.id}", str(int(max_backups)))

    record_audit(
        db,
        action="backup.settings_updated",
        actor=actor,
        actor_username=None if actor is not None else "system",
        detail=f"Updated backup settings for server '{server.name}'.",
        meta=meta,
    )
    db.commit()
    return get_backup_settings(db, server.id)


def list_backups(
    db: Session,
    *,
    server: Server,
    actor: User,
) -> list[dict]:
    """List all backups belonging to a server."""

    _check_server_access(server, actor)

    rows = (
        db.query(ServerBackup)
        .filter(ServerBackup.server_id == server.id)
        .order_by(ServerBackup.created_at.desc(), ServerBackup.id.desc())
        .all()
    )
    return [_to_dict(row) for row in rows]


def get_backup(
    db: Session,
    *,
    server: Server,
    actor: User,
    backup_id: int,
) -> dict:
    """Return one backup's metadata, scoped to the server."""

    _check_server_access(server, actor)
    row = _find_backup(db, server.id, backup_id)
    return _to_dict(row)


def _find_backup(db: Session, server_id: int, backup_id: int) -> ServerBackup:
    row = (
        db.query(ServerBackup)
        .filter(
            ServerBackup.id == backup_id,
            ServerBackup.server_id == server_id,
        )
        .first()
    )
    if row is None:
        raise NotFoundError("Backup not found.")
    return row


def create_backup(
    settings: Settings,
    server: Server,
    *,
    actor: User | None,
    session_factory,
    process_manager: ProcessManager | None = None,
    meta: RequestMeta | None = None,
) -> dict:
    """Create a tar.gz backup of a stopped server.

    ``actor`` is ``None`` for automatic scheduler backups, in which case the
    audit entry is attributed to ``system``.
    """

    if actor is not None:
        _check_server_access(server, actor)

    if server.status != "stopped":
        raise DomainError(
            "The server must be stopped before a backup can be created."
        )

    with acquire_operation_lock(server.id, OperationType.BACKUP_CREATE):
        # Re-check state after acquiring the lock.
        with session_factory() as db:
            db_server = db.get(Server, server.id)
            if db_server is None:
                raise NotFoundError("Server not found.")
            if db_server.status != "stopped":
                raise DomainError(
                    "The server must be stopped before a backup can be created."
                )
            server_dir = _resolve_server_directory(db_server, settings)
            server_name = db_server.name
            owner_id = actor.id if actor is not None else db_server.owner_id

            unique_id = str(uuid.uuid4())
            filename = f"server-{db_server.id}-{unique_id}.tar.gz"
            target = _resolve_backup_path(settings, filename)

            backup = ServerBackup(
                owner_id=owner_id,
                server_id=db_server.id,
                unique_id=unique_id,
                filename=filename,
                size_bytes=0,
                status=BACKUP_STATUS_CREATING,
            )
            db.add(backup)
            db.commit()
            db.refresh(backup)
            backup_id = backup.id

        temp_target = target.with_name(target.name + ".part")

        try:
            if not server_dir.exists() or not server_dir.is_dir():
                raise NotFoundError("Server directory not found.")

            _ensure_disk_space(settings, server_dir)
            _write_archive(server_dir, temp_target)

            size_bytes = temp_target.stat().st_size
            if size_bytes > MAX_BACKUP_SIZE:
                raise DomainError(
                    "Backup exceeds the maximum allowed size of 4 GB."
                )

            os.replace(temp_target, target)
            target.chmod(0o600)

            with session_factory() as db:
                row = db.get(ServerBackup, backup_id)
                if row is None:
                    raise NotFoundError("Backup record not found.")
                row.size_bytes = size_bytes
                row.status = BACKUP_STATUS_COMPLETED
                row.error_message = None
                record_audit(
                    db,
                    action="backup.created",
                    actor=actor,
                    actor_username=None if actor is not None else "system",
                    detail=(
                        f"Created backup '{row.unique_id}' for server "
                        f"'{server_name}' ({size_bytes} bytes)."
                    ),
                    meta=meta,
                )
                db.commit()
                db.refresh(row)
                result = _to_dict(row)

            # Retention runs after the new record is committed so the newest
            # backup is never a candidate for removal.
            with session_factory() as db:
                max_backups = get_backup_settings(db, server.id)["max_backups"]
            prune_old_backups(
                settings,
                server_id=server.id,
                session_factory=session_factory,
                max_backups=max_backups,
            )
            return result

        except Exception as exc:  # noqa: BLE001 - converted to a safe domain error
            for leftover in (temp_target, target):
                try:
                    if leftover.exists():
                        leftover.unlink()
                except OSError:
                    logger.exception("Failed to remove partial backup %s", leftover)

            message = _sanitize_error(exc)
            try:
                with session_factory() as db:
                    row = db.get(ServerBackup, backup_id)
                    if row is not None:
                        row.status = BACKUP_STATUS_FAILED
                        row.error_message = message
                        record_audit(
                            db,
                            action="backup.failed",
                            actor=actor,
                            actor_username=None if actor is not None else "system",
                            detail=(
                                f"Backup creation failed for server '{server_name}'."
                            ),
                            meta=meta,
                        )
                        db.commit()
            except Exception:  # pragma: no cover - best-effort bookkeeping
                logger.exception("Failed to record backup failure")

            logger.exception("Backup creation failed for server %s", server.id)
            if isinstance(exc, DomainError):
                raise
            raise DomainError(message) from exc


def prune_old_backups(
    settings: Settings,
    *,
    server_id: int,
    session_factory,
    max_backups: int,
) -> int:
    """Delete the oldest backups beyond the retention limit.

    Only completed/failed backups belonging to ``server_id`` are eligible.
    """

    keep = max(1, int(max_backups))
    removed = 0

    with session_factory() as db:
        rows = (
            db.query(ServerBackup)
            .filter(
                ServerBackup.server_id == server_id,
                ServerBackup.status.in_(PRUNABLE_STATUSES),
            )
            .order_by(ServerBackup.created_at.desc(), ServerBackup.id.desc())
            .all()
        )
        excess = rows[keep:]
        for row in excess:
            try:
                path = _resolve_backup_path(settings, row.filename)
                if path.exists():
                    path.unlink()
            except (OSError, DomainError):
                logger.exception("Failed to remove pruned backup file")
            db.delete(row)
            removed += 1

        if removed:
            record_audit(
                db,
                action="backup.retention_pruned",
                actor_username="system",
                detail=f"Removed {removed} old backup(s) for server id={server_id}.",
            )
        db.commit()

    return removed


def delete_backup(
    settings: Settings,
    server: Server,
    *,
    actor: User,
    backup_id: int,
    session_factory,
    meta: RequestMeta | None = None,
) -> None:
    """Delete a backup record and its archive."""

    _check_server_access(server, actor)

    with acquire_operation_lock(server.id, OperationType.BACKUP_RESTORE):
        with session_factory() as db:
            row = _find_backup(db, server.id, backup_id)
            target = _resolve_backup_path(settings, row.filename)
            try:
                if target.exists():
                    target.unlink()
            except OSError:
                logger.exception("Failed to delete backup archive")
                raise DomainError("The backup archive could not be deleted.") from None

            db.delete(row)
            record_audit(
                db,
                action="backup.deleted",
                actor=actor,
                detail=f"Deleted backup '{row.unique_id}' for server '{server.name}'.",
                meta=meta,
            )
            db.commit()


def download_backup(
    settings: Settings,
    server: Server,
    *,
    actor: User,
    backup_id: int,
    session_factory,
) -> tuple[Path, str]:
    """Return the validated archive path and managed filename for download."""

    _check_server_access(server, actor)

    with session_factory() as db:
        row = _find_backup(db, server.id, backup_id)

        if row.status != BACKUP_STATUS_COMPLETED:
            raise DomainError("This backup is not ready for download.")

        target = _resolve_backup_path(settings, row.filename)
        if not target.exists() or not target.is_file():
            raise NotFoundError("Backup archive not found.")

    return target, row.filename


def restore_backup(
    settings: Settings,
    server: Server,
    *,
    actor: User,
    backup_id: int,
    session_factory,
    process_manager: ProcessManager | None = None,
    meta: RequestMeta | None = None,
) -> dict:
    """Restore a backup into a stopped server.

    The archive is extracted into a staging directory first. Only after every
    member has been validated is the staging tree swapped into the server
    directory. On failure the original server directory is put back.

    Recovery directories are preserved on failure for manual recovery.
    """

    _check_server_access(server, actor)

    if server.status != "stopped":
        raise DomainError("The server must be stopped before a backup can be restored.")

    with acquire_operation_lock(server.id, OperationType.BACKUP_RESTORE):
        with session_factory() as db:
            db_server = db.get(Server, server.id)
            if db_server is None:
                raise NotFoundError("Server not found.")
            if db_server.status != "stopped":
                raise DomainError(
                    "The server must be stopped before a backup can be restored."
                )
            row = _find_backup(db, server.id, backup_id)
            if row.status != BACKUP_STATUS_COMPLETED:
                raise DomainError("This backup is not ready to be restored.")
            backup_unique_id = row.unique_id
            archive = _resolve_backup_path(settings, row.filename)
            if not archive.exists() or not archive.is_file():
                raise NotFoundError("Backup archive not found.")
            server_dir = _resolve_server_directory(db_server, settings)
            server_name = db_server.name
            result = _to_dict(row)

        server_dir.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(
            tempfile.mkdtemp(prefix=".asm-restore-", dir=server_dir.parent)
        )
        staging.chmod(0o700)

        old_dir = server_dir.parent / f".asm-old-{server.id}-{uuid.uuid4().hex}"
        moved_old = False
        staging_moved = False
        rollback_succeeded = False
        rollback_failed = False

        try:
            # Check available disk space before extraction.
            try:
                free = shutil.disk_usage(server_dir.parent).free
                required = FREE_SPACE_MARGIN
                if free < required:
                    raise DomainError(
                        "Insufficient disk space to restore this backup."
                    )
            except OSError:
                pass

            _extract_archive(archive, staging)

            if server_dir.exists():
                os.replace(server_dir, old_dir)
                moved_old = True

            os.replace(staging, server_dir)
            staging_moved = True
            server_dir.chmod(0o700)

            with session_factory() as db:
                db_server = db.get(Server, server.id)
                if db_server is not None:
                    db_server.status = "stopped"
                record_audit(
                    db,
                    action="backup.restored",
                    actor=actor,
                    detail=(
                        f"Restored backup '{backup_unique_id}' to server "
                        f"'{server_name}'."
                    ),
                    meta=meta,
                )
                db.commit()

            if moved_old and old_dir.exists():
                shutil.rmtree(old_dir, ignore_errors=True)

            return result

        except Exception as exc:  # noqa: BLE001 - converted to a safe domain error
            # Roll back a partially swapped restore.
            if staging_moved and server_dir.exists():
                try:
                    shutil.rmtree(server_dir, ignore_errors=True)
                except OSError:
                    logger.exception("Failed to remove failed restore directory")

            if moved_old and old_dir.exists():
                try:
                    os.replace(old_dir, server_dir)
                    rollback_succeeded = True
                except OSError:
                    rollback_failed = True
                    logger.exception(
                        "CRITICAL: Rollback failed for server %s. "
                        "Original server directory preserved at: %s",
                        server.id,
                        old_dir,
                    )

            # Log recovery directory locations for manual recovery.
            if not rollback_succeeded:
                logger.error(
                    "Restore failed for server %s. Staging directory: %s. "
                    "Original data directory: %s. "
                    "Error: %s",
                    server.id,
                    staging if staging.exists() else "removed",
                    old_dir if moved_old and old_dir.exists() else "unknown",
                    exc,
                )

            if isinstance(exc, DomainError):
                raise
            raise DomainError(_sanitize_error(exc)) from exc

        finally:
            # Only clean up if rollback succeeded AND we're not in a failure state
            # where we need to preserve recovery data.
            if rollback_succeeded and not rollback_failed:
                if staging.exists():
                    shutil.rmtree(staging, ignore_errors=True)
                if old_dir.exists():
                    shutil.rmtree(old_dir, ignore_errors=True)
            else:
                # Preserve recovery directories for manual intervention.
                logger.warning(
                    "Recovery directories preserved for server %s: "
                    "staging=%s old_dir=%s",
                    server.id,
                    staging if staging.exists() else "removed",
                    old_dir if old_dir.exists() else "removed",
                )


def run_backup_scheduler_once(
    settings: Settings,
    session_factory,
    process_manager: ProcessManager | None = None,
) -> dict:
    """Run one automatic-backup scheduling pass.

    Returns a summary used by tests and logging. Every server is handled
    independently: one failure can never abort the pass.
    """

    summary = {"checked": 0, "created": 0, "skipped": 0, "failed": 0}

    with session_factory() as db:
        server_ids = [server_id for (server_id,) in db.execute(select(Server.id)).all()]

    for server_id in server_ids:
        try:
            with session_factory() as db:
                server = db.get(Server, server_id)
                if server is None:
                    continue
                config = get_backup_settings(db, server_id)
                if not config["enabled"]:
                    continue

                summary["checked"] += 1

                if server.status != "stopped":
                    summary["skipped"] += 1
                    continue

                last_created = db.scalar(
                    select(func.max(ServerBackup.created_at)).where(
                        ServerBackup.server_id == server_id,
                        ServerBackup.status == BACKUP_STATUS_COMPLETED,
                    )
                )
                interval = timedelta(hours=config["interval_hours"])
                now = utcnow()
                if last_created is not None:
                    last_aware = as_utc(last_created)
                    if last_aware is not None and now - last_aware.replace(
                        tzinfo=None
                    ) < interval:
                        summary["skipped"] += 1
                        continue

                create_backup(
                    settings,
                    server,
                    actor=None,
                    session_factory=session_factory,
                    process_manager=process_manager,
                )
                summary["created"] += 1

        except ConflictError:
            summary["skipped"] += 1
        except Exception:  # noqa: BLE001 - scheduler must never crash
            summary["failed"] += 1
            logger.exception("Automatic backup failed for server %s", server_id)

    return summary


async def backup_scheduler_loop(
    settings: Settings,
    session_factory,
    process_manager: ProcessManager | None,
    interval_seconds: int,
) -> None:
    """Background loop for automatic backups.

    Runs synchronously inside a worker thread so archive creation cannot block
    the event loop. Exceptions are logged and swallowed.
    """

    import asyncio

    while True:
        await asyncio.sleep(max(30, int(interval_seconds)))
        try:
            summary = await asyncio.to_thread(
                run_backup_scheduler_once,
                settings,
                session_factory,
                process_manager,
            )
            if summary["created"] or summary["failed"]:
                logger.info("Automatic backup pass: %s", summary)
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - scheduler resilience
            logger.exception("Automatic backup scheduler pass failed")