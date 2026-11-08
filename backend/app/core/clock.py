"""UTC time helpers.

SQLite does not preserve timezone information for ``DATETIME`` columns, so the
application stores naive UTC timestamps and converts them to explicit UTC ISO
strings at the API boundary.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from pydantic import PlainSerializer


def utcnow() -> datetime:
    """Return the current UTC time as a naive datetime (database storage)."""

    return datetime.now(timezone.utc).replace(tzinfo=None)


def as_utc(value: datetime | None) -> datetime | None:
    """Attach UTC tzinfo to a value read back from SQLite."""

    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _serialize_utc(value: datetime | None) -> str | None:
    aware = as_utc(value)
    return aware.isoformat().replace("+00:00", "Z") if aware else None


UtcDateTime = Annotated[
    datetime,
    PlainSerializer(_serialize_utc, return_type=str, when_used="json"),
]
