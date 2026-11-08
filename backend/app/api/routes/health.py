"""Public health endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response, status

from app.database.session import database_is_reachable

router = APIRouter(tags=["system"])


@router.get("/health")
def health(request: Request, response: Response) -> dict[str, str]:
    settings = request.app.state.settings
    if not database_is_reachable(request.app.state.engine):
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "degraded", "version": settings.version}
    return {"status": "ok", "version": settings.version}
