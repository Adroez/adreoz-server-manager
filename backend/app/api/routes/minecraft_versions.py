"""Minecraft version management endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.orm import Session

from app.api.deps import (
    AuthContext,
    get_current_auth,
    get_db,
    get_settings,
    require_admin_write,
    require_csrf,
)
from app.core.config import Settings
from app.models.server import Server
from app.schemas.minecraft_version import (
    MinecraftVersionListResponse,
    MinecraftVersionOut,
    MinecraftVersionMetadataOut,
    MinecraftVersionDownloadRequest,
    MinecraftVersionDownloadResponse,
)
from app.services.audit import request_meta
from app.services.errors import ConflictError, DomainError, NotFoundError
from app.services.minecraft_version import MinecraftVersionService

router = APIRouter(prefix="/minecraft/versions", tags=["minecraft-versions"])


def _version_service(db: Session, settings: Settings) -> MinecraftVersionService:
    import sqlalchemy.orm as orm

    engine = db.bind
    factory = orm.sessionmaker(bind=engine, expire_on_commit=False)
    return MinecraftVersionService(settings, factory)


@router.get("", response_model=MinecraftVersionListResponse)
def list_versions(
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    releases_only: bool = Query(default=False),
) -> MinecraftVersionListResponse:
    service = _version_service(db, settings)
    if releases_only:
        items = service.list_releases()
    else:
        items = service.list_versions()
    return MinecraftVersionListResponse(
        items=[MinecraftVersionOut.model_validate(i) for i in items],
        total=len(items),
    )


@router.get("/{version_id}", response_model=MinecraftVersionMetadataOut)
def get_version(
    version_id: str,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> MinecraftVersionMetadataOut:
    service = _version_service(db, settings)
    metadata = service.get_version_metadata(version_id)
    return MinecraftVersionMetadataOut.model_validate(metadata)


@router.post(
    "/{version_id}/download",
    response_model=MinecraftVersionDownloadResponse,
    status_code=status.HTTP_201_CREATED,
)
def download_server_jar(
    version_id: str,
    request: Request,
    payload: MinecraftVersionDownloadRequest,
    auth: Annotated[AuthContext, Depends(require_admin_write)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> MinecraftVersionDownloadResponse:
    server_id = payload.server_id
    server = db.get(Server, server_id)
    if server is None:
        raise NotFoundError("Server not found.")

    is_owner = server.owner_id == auth.user.id
    is_admin = auth.user.role == "admin"
    if not is_owner and not is_admin:
        raise DomainError("You do not have access to this server.")

    service = _version_service(db, settings)
    meta = request_meta(request)

    result = service.download_server_jar(
        db, server=server, version_id=version_id, actor=auth.user, meta=meta
    )
    return MinecraftVersionDownloadResponse(
        message=f"Minecraft {version_id} server JAR downloaded.",
        version=result["version"],
        path=result["path"],
        size_bytes=result["size_bytes"],
        sha1=result["sha1"],
    )
