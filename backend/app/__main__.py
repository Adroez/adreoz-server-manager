"""Run the panel with automatic database migrations.

Usage (from ``backend/``)::

    python -m app            # http://127.0.0.1:8080
    python -m app --reload   # development reload mode
"""

from __future__ import annotations

import os
import sys

import uvicorn
from alembic import command
from alembic.config import Config as AlembicConfig

from app.core.config import PROJECT_ROOT, get_settings


def main() -> None:
    settings = get_settings()

    if hasattr(os, "geteuid") and os.geteuid() == 0 and not settings.allow_root:
        sys.exit(
            "Refusing to run as root. Create a dedicated unprivileged user, or set "
            "ASM_ALLOW_ROOT=true only if you accept the risk."
        )

    backend_dir = PROJECT_ROOT / "backend"
    alembic_cfg = AlembicConfig(str(backend_dir / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    alembic_cfg.set_main_option("sqlalchemy.url", settings.database_uri)
    alembic_cfg.set_main_option("prepend_sys_path", str(backend_dir))
    command.upgrade(alembic_cfg, "head")

    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        server_header=False,
        date_header=False,
        reload="--reload" in sys.argv,
    )


if __name__ == "__main__":
    main()
