"""Minecraft server lifecycle endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func
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
from app.core.process import ProcessManager
from app.models.server import Server, ServerLog
from app.schemas.server import (
    ServerActionResponse,
    ServerCreateRequest,
    ServerListResponse,
    ServerLogEntry,
    ServerLogListResponse,
    ServerOut,
    ServerUpdateRequest,
)
from app.services.audit import record_audit, request_meta
from app.services.errors import ForbiddenError, NotFoundError
from app.services.servers import (
    create_server,
    delete_server,
    get_server_or_404,
    list_servers as _list_servers,
    restart_server as _restart_server,
    start_server as _start_server,
    stop_server as _stop_server,
    update_server as _update_server,
)

router = APIRouter(prefix="/servers", tags=["servers"])


def _get_process_manager(settings: Settings) -> ProcessManager:
    pm = getattr(settings, "_process_manager", None)
    if pm is None:
        pm = ProcessManager()
        settings._process_manager = pm
    return pm


def _check_server_ownership(server: Server, auth: AuthContext) -> None:
    is_owner = server.owner_id == auth.user.id
    is_admin = auth.user.role == "admin"
    if not is_owner and not is_admin:
        raise ForbiddenError("You do not have access to this server.")


@router.get("", response_model=ServerListResponse)
def list_servers_endpoint(
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    db: Annotated[Session, Depends(get_db)],
    search: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> ServerListResponse:
    items, total = _list_servers(
        db,
        actor=auth.user,
        search=search,
        limit=limit,
        offset=offset,
    )
    return ServerListResponse(
        items=[ServerOut.model_validate(s) for s in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("", response_model=ServerOut, status_code=status.HTTP_201_CREATED)
def create_server_endpoint(
    payload: ServerCreateRequest,
    request: Request,
    auth: Annotated[AuthContext, Depends(require_csrf)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ServerOut:
    meta = request_meta(request)
    server = create_server(
        db,
        settings=settings,
        payload=payload,
        owner=auth.user,
        meta=meta,
    )
    return ServerOut.model_validate(server)


@router.get("/{server_id}", response_model=ServerOut)
def get_server_endpoint(
    server_id: int,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    db: Annotated[Session, Depends(get_db)],
) -> ServerOut:
    server = get_server_or_404(db, server_id)
    _check_server_ownership(server, auth)
    return ServerOut.model_validate(server)


@router.patch("/{server_id}", response_model=ServerOut)
def update_server_endpoint(
    server_id: int,
    payload: ServerUpdateRequest,
    request: Request,
    auth: Annotated[AuthContext, Depends(require_csrf)],
    db: Annotated[Session, Depends(get_db)],
) -> ServerOut:
    server = get_server_or_404(db, server_id)
    _check_server_ownership(server, auth)
    meta = request_meta(request)

    updated = _update_server(
        db,
        server=server,
        actor=auth.user,
        payload=payload,
        meta=meta,
    )
    return ServerOut.model_validate(updated)


@router.delete("/{server_id}", response_model=ServerActionResponse)
def delete_server_endpoint(
    server_id: int,
    request: Request,
    auth: Annotated[AuthContext, Depends(require_csrf)],
    db: Annotated[Session, Depends(get_db)],
) -> ServerActionResponse:
    server = get_server_or_404(db, server_id)
    _check_server_ownership(server, auth)
    meta = request_meta(request)

    delete_server(
        db,
        server=server,
        actor=auth.user,
        meta=meta,
    )
    return ServerActionResponse(
        message=f"Deleted server '{server.name}'.",
        status="deleted",
    )


@router.post("/{server_id}/start", response_model=ServerActionResponse)
def start_server_endpoint(
    server_id: int,
    request: Request,
    auth: Annotated[AuthContext, Depends(require_csrf)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ServerActionResponse:
    server = get_server_or_404(db, server_id)
    _check_server_ownership(server, auth)
    meta = request_meta(request)

    process_manager = _get_process_manager(settings)

    _start_server(
        db,
        server=server,
        actor=auth.user,
        process_manager=process_manager,
        settings=settings,
        meta=meta,
    )
    return ServerActionResponse(
        message=f"Start requested for server '{server.name}'.",
        status=server.status,
    )


@router.post("/{server_id}/stop", response_model=ServerActionResponse)
def stop_server_endpoint(
    server_id: int,
    request: Request,
    auth: Annotated[AuthContext, Depends(require_csrf)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ServerActionResponse:
    server = get_server_or_404(db, server_id)
    _check_server_ownership(server, auth)
    meta = request_meta(request)

    process_manager = _get_process_manager(settings)

    _stop_server(
        db,
        server=server,
        actor=auth.user,
        process_manager=process_manager,
        meta=meta,
    )
    return ServerActionResponse(
        message=f"Stop requested for server '{server.name}'.",
        status=server.status,
    )


@router.post("/{server_id}/restart", response_model=ServerActionResponse)
def restart_server_endpoint(
    server_id: int,
    request: Request,
    auth: Annotated[AuthContext, Depends(require_csrf)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ServerActionResponse:
    server = get_server_or_404(db, server_id)
    _check_server_ownership(server, auth)
    meta = request_meta(request)

    process_manager = _get_process_manager(settings)

    _restart_server(
        db,
        server=server,
        actor=auth.user,
        process_manager=process_manager,
        settings=settings,
        meta=meta,
    )
    return ServerActionResponse(
        message=f"Restart requested for server '{server.name}'.",
        status=server.status,
    )


@router.get("/{server_id}/logs", response_model=ServerLogListResponse)
def get_server_logs_endpoint(
    server_id: int,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    db: Annotated[Session, Depends(get_db)],
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> ServerLogListResponse:
    server = get_server_or_404(db, server_id)
    _check_server_ownership(server, auth)

    count = db.scalar(
        select(func.count(ServerLog.id)).where(ServerLog.server_id == server_id)
    )
    rows = (
        db.query(ServerLog)
        .filter(ServerLog.server_id == server_id)
        .order_by(ServerLog.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return ServerLogListResponse(
        items=[
            ServerLogEntry(
                id=r.id,
                stream=r.stream,
                line=r.line,
                created_at=r.created_at,
            )
            for r in rows
        ],
        total=int(count or 0),
        limit=limit,
        offset=offset,
    )
