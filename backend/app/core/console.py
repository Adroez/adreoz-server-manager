"""WebSocket console management for Minecraft servers.

Tracks WebSocket clients subscribed to each server's console output and
maintains a bounded recent-log buffer so newly connected clients receive
immediate history. All methods are safe to call from asyncio tasks.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from typing import Optional

from starlette.websockets import WebSocket, WebSocketState

logger = logging.getLogger("asm.console")

# Maximum number of recent log lines retained per server for new clients.
MAX_LOG_BUFFER = 500

# Maximum concurrent console clients per server. Each client holds an open
# WebSocket plus a copy of the history during replay, so this is bounded.
MAX_CLIENTS_PER_SERVER = 5

# Maximum number of buffered lines replayed to a newly connected client.
MAX_HISTORY_REPLAY = 200

# Seconds to wait for a single client to accept one console message.
MAX_SEND_SECONDS = 5.0


class ConsoleManager:
    """Manages WebSocket console connections and log buffering per server."""

    def __init__(self) -> None:
        self._clients: dict[int, set[WebSocket]] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        self._log_buffers: dict[int, deque] = {}

    def _lock_for(self, server_id: int) -> asyncio.Lock:
        if server_id not in self._locks:
            self._locks[server_id] = asyncio.Lock()
        return self._locks[server_id]

    async def connect(
        self, server_id: int, websocket: WebSocket
    ) -> Optional[list[tuple[str, str]]]:
        """Register a WebSocket client and return recent log history.

        Returns a list of ``(stream, line)`` tuples to replay to the client,
        or ``None`` when the per-server connection limit is reached.
        """

        async with self._lock_for(server_id):
            clients = self._clients.setdefault(server_id, set())
            if len(clients) >= MAX_CLIENTS_PER_SERVER:
                logger.warning(
                    "Rejecting console connection for server %s: limit of %s "
                    "clients reached.",
                    server_id,
                    MAX_CLIENTS_PER_SERVER,
                )
                return None
            clients.add(websocket)
            buffer = self._log_buffers.setdefault(
                server_id, deque(maxlen=MAX_LOG_BUFFER)
            )
            history = list(buffer)[-MAX_HISTORY_REPLAY:]
        return history

    async def disconnect(self, server_id: int, websocket: WebSocket) -> None:
        """Unregister a WebSocket client."""

        async with self._lock_for(server_id):
            clients = self._clients.get(server_id)
            if clients:
                clients.discard(websocket)
                if not clients:
                    self._clients.pop(server_id, None)

    def client_count(self, server_id: int) -> int:
        """Return the number of connected clients for a server."""

        clients = self._clients.get(server_id)
        return len(clients) if clients else 0

    async def broadcast(self, server_id: int, stream: str, line: str) -> None:
        """Send a log line to all connected clients and buffer it."""

        # Store in buffer under lock.
        async with self._lock_for(server_id):
            if server_id not in self._log_buffers:
                self._log_buffers[server_id] = deque(maxlen=MAX_LOG_BUFFER)
            self._log_buffers[server_id].append((stream, line))
            clients = list(self._clients.get(server_id, set()))

        # Deliver to clients outside the lock to avoid blocking producers.
        if clients:
            message = {"stream": stream, "line": line}
            disconnected: list[WebSocket] = []
            for ws in clients:
                try:
                    if ws.client_state == WebSocketState.CONNECTED:
                        # A stalled client must not block the producer forever.
                        await asyncio.wait_for(
                            ws.send_json(message),
                            timeout=MAX_SEND_SECONDS,
                        )
                    else:
                        disconnected.append(ws)
                except Exception:  # pragma: no cover - send resilience
                    logger.warning(
                        "Dropping unresponsive console client for server %s",
                        server_id,
                    )
                    disconnected.append(ws)
            for ws in disconnected:
                await self.disconnect(server_id, ws)
