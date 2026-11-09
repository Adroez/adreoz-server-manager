"""Backup and restore endpoints for Minecraft servers."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import (
    AuthContext,
    get_current_auth,
    get_db,
    get_settings,
    require_csrf,
)
from app.core.config import Settings
from app.models.server import Server
from app.schemas.backups import (
    BackupActionResponse,
    BackupListResponse,
    BackupOut,
    BackupSettingsOut,
    BackupSettingsUpdateRequest,
)
from app.services.audit import request_meta
from app.services.backup import (
    create_backup,
    delete_backup,
    download_backup,
    get_backup,
    get_backup_settings,
    list_backups,
    restore_backup,
    update_backup_settings,
)
from app.services.errors import ForbiddenError
from app.services.servers import get_server_or_404

router = APIRouter(prefix="/servers", tags=["backups"])


def _check_server_ownership(server: Server, auth: AuthContext) -> None:
    is_owner = server.owner_id == auth.user.id
    is_admin = auth.user.role == "admin"
    if not is_owner and not is_admin:
        raise ForbiddenError("You do not have access to this server.")


# ---------------------------------------------------------------------------
# Automatic backup settings. Declared before /{backup_id} so "settings" is not
# interpreted as a backup ID.
# ---------------------------------------------------------------------------


@router.get("/{server_id}/backups/settings", response_model=BackupSettingsOut)
def get_backup_settings_endpoint(
    server_id: int,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    db: Annotated[Session, Depends(get_db)],
) -> BackupSettingsOut:
    server = get_server_or_404(db, server_id)
    _check_server_ownership(server, auth)
    return BackupSettingsOut(**get_backup_settings(db, server.id))


@router.patch("/{server_id}/backups/settings", response_model=BackupSettingsOut)
def update_backup_settings_endpoint(
    server_id: int,
    payload: BackupSettingsUpdateRequest,
    request: Request,
    auth: Annotated[AuthContext, Depends(require_csrf)],
    db: Annotated[Session, Depends(get_db)],
) -> BackupSettingsOut:
    server = get_server_or_404(db, server_id)
    _check_server_ownership(server, auth)

    result = update_backup_settings(
        db,
        server=server,
        actor=auth.user,
        enabled=payload.enabled,
        interval_hours=payload.interval_hours,
        max_backups=payload.max_backups,
        meta=request_meta(request),
    )
    return BackupSettingsOut(**result)


# ---------------------------------------------------------------------------
# Listing and creation
# ---------------------------------------------------------------------------


@router.get("/{server_id}/backups", response_model=BackupListResponse)
def list_backups_endpoint(
    server_id: int,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    db: Annotated[Session, Depends(get_db)],
) -> BackupListResponse:
    server = get_server_or_404(db, server_id)
    _check_server_ownership(server, auth)

    items = list_backups(db, server=server, actor=auth.user)
    return BackupListResponse(
        items=[BackupOut(**item) for item in items],
        total=len(items),
    )


@router.post(
    "/{server_id}/backups",
    response_model=BackupOut,
    status_code=status.HTTP_201_CREATED,
)
def create_backup_endpoint(
    server_id: int,
    request: Request,
    auth: Annotated[AuthContext, Depends(require_csrf)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> BackupOut:
    server = get_server_or_404(db, server_id)
    _check_server_ownership(server, auth)

    result = create_backup(
        settings,
        server,
        actor=auth.user,
        session_factory=request.app.state.session_factory,
        process_manager=request.app.state.process_manager,
        meta=request_meta(request),
    )
    return BackupOut(**result)


# ---------------------------------------------------------------------------
# Details, download, deletion, restore
# ---------------------------------------------------------------------------


@router.get("/{server_id}/backups/{backup_id}", response_model=BackupOut)
def get_backup_endpoint(
    server_id: int,
    backup_id: int,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    db: Annotated[Session, Depends(get_db)],
) -> BackupOut:
    server = get_server_or_404(db, server_id)
    _check_server_ownership(server, auth)
    return BackupOut(**get_backup(db, server=server, actor=auth.user, backup_id=backup_id))


@router.get("/{server_id}/backups/{backup_id}/download")
def download_backup_endpoint(
    server_id: int,
    backup_id: int,
    request: Request,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
):
    server = get_server_or_404(db, server_id)
    _check_server_ownership(server, auth)

    target, filename = download_backup(
        settings,
        server,
        actor=auth.user,
        backup_id=backup_id,
        session_factory=request.app.state.session_factory,
    )

    file_size = target.stat().st_size

    def iter_file():
        with open(target, "rb") as handle:
            while chunk := handle.read(64 * 1024):
                yield chunk

    return StreamingResponse(
        iter_file(),
        media_type="application/gzip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(file_size),
        },
    )


@router.delete(
    "/{server_id}/backups/{backup_id}",
    response_model=BackupActionResponse,
)
def delete_backup_endpoint(
    server_id: int,
    backup_id: int,
    request: Request,
    auth: Annotated[AuthContext, Depends(require_csrf)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> BackupActionResponse:
    server = get_server_or_404(db, server_id)
    _check_server_ownership(server, auth)

    delete_backup(
        settings,
        server,
        actor=auth.user,
        backup_id=backup_id,
        session_factory=request.app.state.session_factory,
        meta=request_meta(request),
    )
    return BackupActionResponse(
        message=f"Deleted backup '{backup_id}'.",
        status="deleted",
    )


@router.post(
    "/{server_id}/backups/{backup_id}/restore",
    response_model=BackupOut,
)
def restore_backup_endpoint(
    server_id: int,
    backup_id: int,
    request: Request,
    auth: Annotated[AuthContext, Depends(require_csrf)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> BackupOut:
    server = get_server_or_404(db, server_id)
    _check_server_ownership(server, auth)

    result = restore_backup(
        settings,
        server,
        actor=auth.user,
        backup_id=backup_id,
        session_factory=request.app.state.session_factory,
        process_manager=request.app.state.process_manager,
        meta=request_meta(request),
    )
    return BackupOut(**result)
