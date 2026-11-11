"""Server software installation endpoints.

Provides endpoints to download and install Minecraft server software
(vanilla, paper, fabric, forge) for a server.
"""

from __future__ import annotations

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.orm import Session

from app.api.deps import (
    AuthContext,
    get_current_auth,
    get_db,
    get_settings,
    require_csrf,
)
from app.core.config import Settings
from app.core.minecraft_software import SoftwareDownloadError
from app.models.server import Server
from app.schemas.software import (
    SoftwareInstallRequest,
    SoftwareInstallResponse,
    SoftwareInstallStatus,
    SoftwareVersionListResponse,
    SoftwareBuildListResponse,
)
from app.services.audit import request_meta
from app.services.errors import (
    ConflictError,
    DomainError,
    ForbiddenError,
    NotFoundError,
    UpstreamError,
)
from app.services.software_install import SoftwareInstallService

router = APIRouter(prefix="/servers", tags=["software-install"])


def _software_service(db: Session, settings: Settings) -> SoftwareInstallService:
    """Build a SoftwareInstallService bound to the current request's session factory."""
    import sqlalchemy.orm as orm

    engine = db.bind
    factory = orm.sessionmaker(bind=engine, expire_on_commit=False)
    return SoftwareInstallService(settings, factory)


@router.post(
    "/{server_id}/software/install",
    response_model=SoftwareInstallResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def install_software(
    server_id: int,
    request: Request,
    payload: SoftwareInstallRequest,
    auth: Annotated[AuthContext, Depends(require_csrf)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> SoftwareInstallResponse:
    """Install Minecraft server software for a server.

    Downloads and verifies the server JAR for the specified software type
    and version. The server must be stopped.
    """
    server = db.get(Server, server_id)
    if server is None:
        raise NotFoundError("Server not found.")

    is_owner = server.owner_id == auth.user.id
    is_admin = auth.user.role == "admin"
    if not is_owner and not is_admin:
        raise ForbiddenError("You do not have access to this server.")

    if server.status != "stopped":
        raise DomainError("Software can only be installed on a stopped server.")

    service = _software_service(db, settings)
    meta = request_meta(request)

    result = service.install_software(
        db,
        server=server,
        software_type=payload.software_type,
        version=payload.version,
        build_version=payload.build_version,
        actor=auth.user,
        meta=meta,
    )
    return SoftwareInstallResponse(
        message=f"Installed {payload.software_type} {payload.version} for server '{server.name}'.",
        server_id=server.id,
        software_type=payload.software_type,
        version=result["version"],
        software_version=result["software_version"],
        install_status=result["install_status"],
        path=result["path"],
        size_bytes=result["size_bytes"],
        sha256=result.get("sha256"),
    )


@router.get(
    "/{server_id}/software/install",
    response_model=SoftwareInstallStatus,
)
def get_install_status(
    server_id: int,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> SoftwareInstallStatus:
    """Get the software installation status for a server."""
    server = db.get(Server, server_id)
    if server is None:
        raise NotFoundError("Server not found.")

    is_owner = server.owner_id == auth.user.id
    is_admin = auth.user.role == "admin"
    if not is_owner and not is_admin:
        raise ForbiddenError("You do not have access to this server.")

    service = _software_service(db, settings)
    status_info = service.get_install_status(server)
    return SoftwareInstallStatus(
        server_id=server.id,
        software_type=server.software_type,
        version=server.version,
        software_version=server.software_version,
        install_status=status_info["install_status"],
        install_error=server.install_error,
        jar_path=status_info["jar_path"],
        jar_exists=status_info["jar_exists"],
    )


@router.get(
    "/software/versions/{software_type}",
    response_model=SoftwareVersionListResponse,
)
def list_software_versions(
    software_type: str,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> SoftwareVersionListResponse:
    """List available Minecraft versions for a software type."""
    service = _software_service(db, settings)

    try:
        if software_type == "vanilla":
            versions = service.installer.get_vanilla_versions()
        elif software_type == "paper":
            versions = service.installer.get_paper_versions()
        elif software_type == "fabric":
            versions = service.installer.get_fabric_versions()
        elif software_type == "forge":
            versions = service.installer.get_forge_versions()
        else:
            raise DomainError(f"Unsupported software type: {software_type}.")
    except SoftwareDownloadError as exc:
        raise UpstreamError(str(exc)) from exc

    return SoftwareVersionListResponse(
        software_type=software_type,
        versions=versions,
    )


@router.get(
    "/software/builds/{software_type}/{version}",
    response_model=SoftwareBuildListResponse,
)
def list_software_builds(
    software_type: str,
    version: str,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> SoftwareBuildListResponse:
    """List available builds for a software type and version."""
    service = _software_service(db, settings)

    try:
        if software_type == "paper":
            builds = service.installer.get_paper_builds(version)
        elif software_type == "fabric":
            builds = service.installer.get_fabric_loader_versions(version)
        elif software_type == "forge":
            builds = service.installer.get_forge_builds(version)
        else:
            raise DomainError(
                f"Build listing is not supported for software type: {software_type}."
            )
    except SoftwareDownloadError as exc:
        raise UpstreamError(str(exc)) from exc

    return SoftwareBuildListResponse(
        software_type=software_type,
        version=version,
        builds=builds,
    )
