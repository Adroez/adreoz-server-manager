"""Java runtime management endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
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
from app.models.java_runtime import JavaRuntime as JavaRuntimeModel
from app.schemas.java_runtime import (
    JavaRuntimeListResponse,
    JavaRuntimeOut,
    JavaRuntimeInstallRequest,
    JavaRuntimeInstallResponse,
)
from app.services.audit import record_audit, request_meta
from app.services.errors import ConflictError, DomainError, NotFoundError
from app.services.java_runtime import JavaRuntimeService

router = APIRouter(prefix="/java/runtimes", tags=["java-runtimes"])


def _java_service(db: Session, settings: Settings) -> JavaRuntimeService:
    """Build a JavaRuntimeService bound to the current request's session factory."""

    import sqlalchemy.orm as orm

    engine = db.bind
    factory = orm.sessionmaker(bind=engine, expire_on_commit=False)
    return JavaRuntimeService(settings, factory)


@router.get("", response_model=JavaRuntimeListResponse)
def list_runtimes(
    request: Request,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> JavaRuntimeListResponse:
    service = _java_service(db, settings)
    runtimes = service.list_runtimes(db)
    return JavaRuntimeListResponse(
        items=[JavaRuntimeOut.model_validate(r) for r in runtimes],
        total=len(runtimes),
    )


@router.post("/install", response_model=JavaRuntimeInstallResponse, status_code=status.HTTP_201_CREATED)
def install_managed_java_17(
    request: Request,
    payload: JavaRuntimeInstallRequest,
    auth: Annotated[AuthContext, Depends(require_admin_write)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> JavaRuntimeInstallResponse:
    service = _java_service(db, settings)
    meta = request_meta(request)

    installed = service.install_managed_java_17(db, actor=auth.user, meta=meta)
    return JavaRuntimeInstallResponse(
        message="Java 17 (Eclipse Temurin) installed.",
        runtime=installed,
    )


@router.delete("/{runtime_id}", response_model=dict)
def delete_managed_runtime(
    runtime_id: int,
    request: Request,
    auth: Annotated[AuthContext, Depends(require_admin_write)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict:
    service = _java_service(db, settings)
    meta = request_meta(request)

    service.delete_managed_runtime(db, runtime_id, actor=auth.user, meta=meta)
    return {"message": f"Java runtime {runtime_id} deleted."}


@router.get("/{runtime_id}", response_model=JavaRuntimeOut)
def get_runtime(
    runtime_id: int,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    db: Annotated[Session, Depends(get_db)],
) -> JavaRuntimeOut:
    runtime = db.get(JavaRuntimeModel, runtime_id)
    if runtime is None:
        raise NotFoundError("Java runtime not found.")
    return JavaRuntimeOut.model_validate(runtime)
