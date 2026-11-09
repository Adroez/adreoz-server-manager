"""File manager and configuration editor endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, get_current_auth, get_db, get_settings, require_csrf
from app.core.config import Settings
from app.models.server import Server
from app.schemas.files import (
    ConfigPropertiesResponse,
    ConfigPropertiesUpdateRequest,
    ConfigValidationInfo,
    FileDeleteRequest,
    FileEntry,
    FileListResponse,
    FileReadResponse,
    FileRenameRequest,
    FileWriteRequest,
    FolderCreateRequest,
    FileActionResponse,
)
from app.services.audit import record_audit, request_meta
from app.services.config import (
    get_validation_info,
    read_properties,
    write_properties,
)
from app.services.errors import ConflictError, DomainError, ForbiddenError, NotFoundError
from app.services.files import (
    create_folder,
    delete_file,
    list_directory,
    read_file,
    rename_file,
    save_uploaded_file,
    write_file,
    _resolve_file_path,
    MAX_UPLOAD_SIZE,
)
from app.services.servers import get_server_or_404

router = APIRouter(prefix="/servers", tags=["files"])


def _check_server_ownership(server: Server, auth: AuthContext) -> None:
    is_owner = server.owner_id == auth.user.id
    is_admin = auth.user.role == "admin"
    if not is_owner and not is_admin:
        raise ForbiddenError("You do not have access to this server.")


def _get_settings(request: Request) -> Settings:
    return request.app.state.settings


# ---------------------------------------------------------------------------
# Directory listing
# ---------------------------------------------------------------------------


@router.get("/{server_id}/files", response_model=FileListResponse)
def list_directory_endpoint(
    server_id: int,
    request: Request,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(_get_settings)],
) -> FileListResponse:
    server = get_server_or_404(db, server_id)
    _check_server_ownership(server, auth)

    fp = request.query_params.get("fp", "")

    entries = list_directory(settings, server, actor=auth.user, relative_path=fp)

    target_path = fp
    parent_path = None
    if target_path:
        parts = target_path.rstrip("/").split("/")
        if len(parts) > 1:
            parent_path = "/".join(parts[:-1])

    return FileListResponse(
        path=target_path,
        parent=parent_path,
        items=[FileEntry(**e) for e in entries],
    )


# ---------------------------------------------------------------------------
# Read file
# ---------------------------------------------------------------------------


@router.get("/{server_id}/files/read", response_model=FileReadResponse)
def read_file_endpoint(
    server_id: int,
    request: Request,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(_get_settings)],
    fp: str = Query(..., min_length=1),
) -> FileReadResponse:
    server = get_server_or_404(db, server_id)
    _check_server_ownership(server, auth)

    content, mime_type = read_file(settings, server, actor=auth.user, relative_path=fp)

    return FileReadResponse(
        path=fp,
        content=content,
        mime_type=mime_type,
        size=len(content.encode("utf-8")),
    )


# ---------------------------------------------------------------------------
# Write file
# ---------------------------------------------------------------------------


@router.post("/{server_id}/files/write", response_model=FileActionResponse)
def write_file_endpoint(
    server_id: int,
    payload: FileWriteRequest,
    request: Request,
    auth: Annotated[AuthContext, Depends(require_csrf)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(_get_settings)],
) -> FileActionResponse:
    server = get_server_or_404(db, server_id)
    _check_server_ownership(server, auth)
    meta = request_meta(request)

    write_file(
        settings,
        server,
        actor=auth.user,
        relative_path=payload.path,
        content=payload.content,
    )

    record_audit(
        db,
        action="file.written",
        actor=auth.user,
        detail=f"Wrote file '{payload.path}' on server '{server.name}'.",
        meta=meta,
    )
    db.commit()

    return FileActionResponse(
        message=f"Saved '{payload.path}'.",
        path=payload.path,
    )


# ---------------------------------------------------------------------------
# Create folder
# ---------------------------------------------------------------------------


@router.post("/{server_id}/files/folder", response_model=FileActionResponse)
def create_folder_endpoint(
    server_id: int,
    payload: FolderCreateRequest,
    request: Request,
    auth: Annotated[AuthContext, Depends(require_csrf)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(_get_settings)],
) -> FileActionResponse:
    server = get_server_or_404(db, server_id)
    _check_server_ownership(server, auth)
    meta = request_meta(request)

    create_folder(
        settings,
        server,
        actor=auth.user,
        relative_path=payload.path,
    )

    record_audit(
        db,
        action="folder.created",
        actor=auth.user,
        detail=f"Created folder '{payload.path}' on server '{server.name}'.",
        meta=meta,
    )
    db.commit()

    return FileActionResponse(
        message=f"Created folder '{payload.path}'.",
        path=payload.path,
    )


# ---------------------------------------------------------------------------
# Rename file/folder
# ---------------------------------------------------------------------------


@router.post("/{server_id}/files/rename", response_model=FileActionResponse)
def rename_file_endpoint(
    server_id: int,
    payload: FileRenameRequest,
    request: Request,
    auth: Annotated[AuthContext, Depends(require_csrf)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(_get_settings)],
) -> FileActionResponse:
    server = get_server_or_404(db, server_id)
    _check_server_ownership(server, auth)
    meta = request_meta(request)

    rename_file(
        settings,
        server,
        actor=auth.user,
        old_path=payload.old_path,
        new_path=payload.new_path,
    )

    record_audit(
        db,
        action="file.renamed",
        actor=auth.user,
        detail=f"Renamed '{payload.old_path}' to '{payload.new_path}' on server '{server.name}'.",
        meta=meta,
    )
    db.commit()

    return FileActionResponse(
        message=f"Renamed '{payload.old_path}' to '{payload.new_path}'.",
        path=payload.new_path,
    )


# ---------------------------------------------------------------------------
# Delete file/folder
# ---------------------------------------------------------------------------


@router.delete("/{server_id}/files", response_model=FileActionResponse)
def delete_file_endpoint(
    server_id: int,
    payload: FileDeleteRequest,
    request: Request,
    auth: Annotated[AuthContext, Depends(require_csrf)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(_get_settings)],
) -> FileActionResponse:
    server = get_server_or_404(db, server_id)
    _check_server_ownership(server, auth)
    meta = request_meta(request)

    delete_file(
        settings,
        server,
        actor=auth.user,
        relative_path=payload.path,
    )

    record_audit(
        db,
        action="file.deleted",
        actor=auth.user,
        detail=f"Deleted '{payload.path}' on server '{server.name}'.",
        meta=meta,
    )
    db.commit()

    return FileActionResponse(
        message=f"Deleted '{payload.path}'.",
        path=payload.path,
    )


# ---------------------------------------------------------------------------
# Download file
# ---------------------------------------------------------------------------


@router.get("/{server_id}/files/download")
def download_file_endpoint(
    server_id: int,
    request: Request,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(_get_settings)],
    fp: str = Query(..., min_length=1),
):
    server = get_server_or_404(db, server_id)
    _check_server_ownership(server, auth)

    target = _resolve_file_path(settings, server, fp)

    if not target.exists():
        raise NotFoundError("File not found.")
    if not target.is_file():
        raise DomainError("Path is not a file.")

    file_size = target.stat().st_size

    def iter_file():
        with open(target, "rb") as f:
            while chunk := f.read(64 * 1024):
                yield chunk

    return StreamingResponse(
        iter_file(),
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{target.name}"',
            "Content-Length": str(file_size),
        },
    )


# ---------------------------------------------------------------------------
# Upload file
# ---------------------------------------------------------------------------


@router.post("/{server_id}/files/upload", response_model=FileActionResponse)
async def upload_file_endpoint(
    server_id: int,
    request: Request,
    auth: Annotated[AuthContext, Depends(require_csrf)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(_get_settings)],
    fp: str = Query(..., min_length=1),
    filename: str = Query(..., min_length=1),
) -> FileActionResponse:
    server = get_server_or_404(db, server_id)
    _check_server_ownership(server, auth)
    meta = request_meta(request)

    # Stream the request body with a size limit to avoid buffering unlimited data.
    max_size = MAX_UPLOAD_SIZE
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > max_size:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"Uploaded file exceeds maximum size of {max_size // (1024 * 1024)} MB.",
            )

    save_uploaded_file(
        settings,
        server,
        actor=auth.user,
        relative_path=fp,
        file_data=bytes(body),
        filename=filename,
    )

    record_audit(
        db,
        action="file.uploaded",
        actor=auth.user,
        detail=f"Uploaded '{filename}' to '{fp}' on server '{server.name}'.",
        meta=meta,
    )
    db.commit()

    return FileActionResponse(
        message=f"Uploaded '{filename}'.",
        path=fp,
    )


# ---------------------------------------------------------------------------
# Configuration editor: read
# ---------------------------------------------------------------------------


@router.get("/{server_id}/config/properties", response_model=ConfigPropertiesResponse)
def read_properties_endpoint(
    server_id: int,
    request: Request,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(_get_settings)],
) -> ConfigPropertiesResponse:
    server = get_server_or_404(db, server_id)
    _check_server_ownership(server, auth)

    properties, etag = read_properties(settings, server, actor=auth.user)

    return ConfigPropertiesResponse(properties=properties, etag=etag)


# ---------------------------------------------------------------------------
# Configuration editor: write
# ---------------------------------------------------------------------------


@router.patch(
    "/{server_id}/config/properties",
    response_model=ConfigPropertiesResponse,
)
def write_properties_endpoint(
    server_id: int,
    payload: ConfigPropertiesUpdateRequest,
    request: Request,
    auth: Annotated[AuthContext, Depends(require_csrf)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(_get_settings)],
) -> ConfigPropertiesResponse:
    server = get_server_or_404(db, server_id)
    _check_server_ownership(server, auth)
    meta = request_meta(request)

    try:
        written = write_properties(
            settings,
            server,
            actor=auth.user,
            properties=payload.properties,
            if_match=payload.if_match,
        )
    except ConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=exc.message,
        )

    record_audit(
        db,
        action="config.updated",
        actor=auth.user,
        detail=f"Updated server.properties on server '{server.name}': {', '.join(sorted(written.keys()))}.",
        meta=meta,
    )
    db.commit()

    # Fetch the new etag after writing.
    _, new_etag = read_properties(settings, server, actor=auth.user)

    return ConfigPropertiesResponse(properties=written, etag=new_etag)


# ---------------------------------------------------------------------------
# Configuration validation info
# ---------------------------------------------------------------------------


@router.get("/{server_id}/config/validation", response_model=ConfigValidationInfo)
def config_validation_info_endpoint(
    server_id: int,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    db: Annotated[Session, Depends(get_db)],
) -> ConfigValidationInfo:
    server = get_server_or_404(db, server_id)
    _check_server_ownership(server, auth)

    info = get_validation_info()
    return ConfigValidationInfo(**info)
