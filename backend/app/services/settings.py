"""Administrator-managed application settings."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.setting import AppSetting

SESSION_LIFETIME_KEY = "session_lifetime_hours"


def _get_raw(db: Session, key: str) -> str | None:
    return db.scalar(select(AppSetting.value).where(AppSetting.key == key))


def _set_raw(db: Session, key: str, value: str) -> None:
    setting = db.get(AppSetting, key)
    if setting is None:
        db.add(AppSetting(key=key, value=value))
    else:
        setting.value = value


def get_session_lifetime_hours(db: Session, default: int) -> int:
    raw = _get_raw(db, SESSION_LIFETIME_KEY)
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(1, min(value, 720))


def set_session_lifetime_hours(db: Session, hours: int) -> int:
    value = max(1, min(int(hours), 720))
    _set_raw(db, SESSION_LIFETIME_KEY, str(value))
    return value
