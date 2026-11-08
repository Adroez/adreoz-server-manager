"""Dashboard summary endpoint."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, get_current_auth, get_db
from app.schemas.dashboard import DashboardSummary
from app.services.dashboard import build_dashboard

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard/summary", response_model=DashboardSummary)
def dashboard_summary(
    request: Request,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    db: Annotated[Session, Depends(get_db)],
) -> DashboardSummary:
    return build_dashboard(
        db,
        user=auth.user,
        session_expires_at=auth.session.expires_at,
        settings=request.app.state.settings,
        panel_started_monotonic=request.app.state.panel_started_monotonic,
    )
