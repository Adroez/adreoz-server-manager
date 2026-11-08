"""Dashboard summary built from live host metrics and database state."""

from __future__ import annotations

import os
import platform
import socket
import time
from datetime import datetime

import psutil
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.audit import AuditLog
from app.models.user import User
from app.schemas.audit import AuditLogOut
from app.schemas.dashboard import (
    AccountInfo,
    DashboardSummary,
    DiskInfo,
    MemoryInfo,
    PlatformInfo,
    SystemInfo,
)
from app.schemas.user import UserOut
from app.services.auth import count_active_sessions
from app.services.users import count_active_admins, count_users


def _load_averages() -> tuple[float | None, float | None, float | None]:
    try:
        return os.getloadavg()
    except (AttributeError, OSError):
        return None, None, None


def build_dashboard(
    db: Session,
    *,
    user: User,
    session_expires_at: datetime,
    settings: Settings,
    panel_started_monotonic: float,
) -> DashboardSummary:
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage(str(settings.data_dir))
    load_1, load_5, load_15 = _load_averages()

    system = SystemInfo(
        hostname=socket.gethostname(),
        platform=f"{platform.system()} {platform.release()}",
        cpu_count=psutil.cpu_count(logical=True) or 1,
        cpu_percent=round(psutil.cpu_percent(interval=None), 1),
        load_1=round(load_1, 2) if load_1 is not None else None,
        load_5=round(load_5, 2) if load_5 is not None else None,
        load_15=round(load_15, 2) if load_15 is not None else None,
        memory=MemoryInfo(
            total_bytes=memory.total,
            used_bytes=memory.used,
            available_bytes=memory.available,
            percent=round(float(memory.percent), 1),
        ),
        disk=DiskInfo(
            total_bytes=disk.total,
            used_bytes=disk.used,
            free_bytes=disk.free,
            percent=round(float(disk.percent), 1),
        ),
        host_uptime_seconds=max(0, int(time.time() - psutil.boot_time())),
        panel_uptime_seconds=max(0, int(time.monotonic() - panel_started_monotonic)),
    )

    account = AccountInfo(
        active_sessions=count_active_sessions(db, user),
        session_expires_at=session_expires_at,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
    )

    platform_info: PlatformInfo | None = None
    if user.is_admin:
        total_users = count_users(db)
        active_users = int(
            db.scalar(select(func.count(User.id)).where(User.is_active.is_(True))) or 0
        )
        recent = list(
            db.scalars(
                select(AuditLog).order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(8)
            ).all()
        )
        platform_info = PlatformInfo(
            total_users=total_users,
            active_users=active_users,
            admins=count_active_admins(db),
            recent_activity=[
                AuditLogOut.model_validate(entry, from_attributes=True) for entry in recent
            ],
        )

    return DashboardSummary(
        user=UserOut.model_validate(user),
        system=system,
        account=account,
        platform=platform_info,
    )
