"""WebSocket console endpoints for Minecraft servers.

Provides real-time console output streaming and command input for running
servers. Connections are authenticated via the session cookie and enforce
server ownership (admin access all servers).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_settings
from app.core.console import ConsoleManager
from app.core.config import Settings
from app.models.server import Server
from app.services.auth import get_session_by_token
from app.services.errors import ForbiddenError, NotFoundError
from app.services.servers import _check_server_access

router = APIRouter(tags=["consoles"])

logger = logging.getLogger("asm.consoles")


def _get_console_manager(settings: Settings) -> ConsoleManager:
    cm = getattr(settings, "_console_manager", None)
    if cm is None:
        cm = ConsoleManager()
        settings._console_manager = cm
    return cm


def _check_server_ownership(server: Server, user) -> None:
    is_owner = server.owner_id == user.id
    is_admin = user.role == "admin"
    if not is_owner and not is_admin:
        raise ForbiddenError("You do not have access to this server.")


async def _authenticate_websocket(
    websocket: WebSocket,
    db: Session,
    settings: Settings,
) -> Optional[tuple]:
    """Authenticate a WebSocket connection via the session cookie.

    Returns ``(user, auth_session)`` on success or ``None`` on failure.
    """

    raw_token = websocket.cookies.get(settings.session_cookie_name)
    if not raw_token:
        return None

    auth_session = get_session_by_token(db, raw_token)
    if auth_session is None:
        return None

    from app.core.clock import utcnow

    now = utcnow()
    if auth_session.is_expired(now):
        return None

    user = auth_session.user
    if not user.is_active:
        return None

    return user, auth_session


@router.websocket("/servers/{server_id}/console")
async def console_websocket(
    websocket: WebSocket,
    server_id: int,
) -> None:
    """WebSocket endpoint for live server console access.

    Client connects with the session cookie (sent automatically by browsers).
    After successful authentication and ownership check, the server sends the
    recent log history, then streams live stdout/stderr. Client text messages
    are written to the server process stdin as commands.
    """

    settings = websocket.app.state.settings
    session_factory = websocket.app.state.session_factory
    process_manager = websocket.app.state.process_manager
    console_manager = _get_console_manager(settings)

    db = session_factory()
    try:
        result = await _authenticate_websocket(websocket, db, settings)
        if result is None:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

        user, _auth_session = result

        server = db.get(Server, server_id)
        if server is None:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

        try:
            _check_server_ownership(server, user)
        except ForbiddenError:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

        await websocket.accept()

        # Send recent log history to the newly connected client.
        history = await console_manager.connect(server_id, websocket)
        for stream, line in history:
            try:
                if websocket.client_state.__class__.__name__ == "CONNECTED":
                    await websocket.send_json({"stream": stream, "line": line})
            except Exception:  # pragma: no cover - send resilience
                break

        # Main loop: receive commands from the client and forward to stdin.
        while True:
            try:
                message = await websocket.receive_text()
            except WebSocketDisconnect:
                break

            if not message or not message.strip():
                continue

            handle = process_manager.get(server_id)
            process = handle.process if handle else None
            if (
                handle is None
                or not handle.is_running
                or process is None
                or process.stdin is None
            ):
                try:
                    await websocket.send_json(
                        {
                            "stream": "system",
                            "line": "Server is not running or cannot accept commands.",
                        }
                    )
                except Exception:  # pragma: no cover - send resilience
                    break
                continue

            command = message.strip()
            try:
                process.stdin.write((command + "\n").encode("utf-8"))
                await process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError, RuntimeError) as exc:
                logger.warning(
                    "Failed to send command to server %s: %s", server_id, exc
                )
                try:
                    await websocket.send_json(
                        {
                            "stream": "system",
                            "line": f"Failed to deliver command: {exc}",
                        }
                    )
                except Exception:  # pragma: no cover - send resilience
                    break
    except WebSocketDisconnect:
        pass
    except Exception:  # pragma: no cover - connection resilience
        logger.exception("Console WebSocket error for server %s", server_id)
    finally:
        await console_manager.disconnect(server_id, websocket)
        db.close()
