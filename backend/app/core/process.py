"""Asynchronous Minecraft server process management.

Servers are launched as child processes of the panel. The panel tracks each
running process in memory and captures stdout/stderr into the database. No
shell interpolation is ever used: the Java executable and its arguments are
passed as a list, preventing command injection.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

logger = logging.getLogger("asm.process")


class ProcessError(RuntimeError):
    """Raised when a server process cannot be managed."""


@dataclass
class ProcessHandle:
    """Tracks a running server process."""

    server_id: int
    process: asyncio.subprocess.Process
    stdout_task: asyncio.Task | None = None
    stderr_task: asyncio.Task | None = None
    started_at: float | None = None
    stopped_at: float | None = None
    exit_code: int | None = None
    terminated: bool = False

    @property
    def is_running(self) -> bool:
        return (
            self.process.returncode is None
            and not self.terminated
        )


class ProcessManager:
    """In-memory registry of server processes.

    The panel is single-process in Phase 2, so an in-memory registry keyed by
    server ID is sufficient. Each process is an asyncio subprocess whose
    output is captured line by line and forwarded to an optional callback.
    """

    def __init__(self) -> None:
        self._processes: dict[int, ProcessHandle] = {}

    def get(self, server_id: int) -> ProcessHandle | None:
        return self._processes.get(server_id)

    def has(self, server_id: int) -> bool:
        return server_id in self._processes

    def running_ids(self) -> set[int]:
        return {
            server_id
            for server_id, handle in self._processes.items()
            if handle.is_running
        }

    def register(
        self,
        server_id: int,
        process: asyncio.subprocess.Process,
        stdout_callback: Callable[[str], None] | None = None,
        stderr_callback: Callable[[str], None] | None = None,
    ) -> ProcessHandle:
        if self.has(server_id):
            raise ProcessError(f"Server {server_id} already has a tracked process.")

        handle = ProcessHandle(server_id=server_id, process=process)
        self._processes[server_id] = handle

        async def pump(stream, callback, tag: str) -> None:
            assert stream is not None
            while True:
                line = await stream.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip("\n")
                if callback:
                    try:
                        callback(text)
                    except Exception:  # pragma: no cover - callback resilience
                        logger.exception("Process output callback failed for server %s", server_id)

        handle.stdout_task = asyncio.create_task(
            pump(process.stdout, stdout_callback, "stdout")
        )
        handle.stderr_task = asyncio.create_task(
            pump(process.stderr, stderr_callback, "stderr")
        )
        handle.started_at = asyncio.get_event_loop().time()
        logger.info("Registered process for server %s (pid=%s)", server_id, process.pid)
        return handle

    async def stop(self, server_id: int, timeout: int = 30) -> int:
        """Gracefully stop a tracked server process.

        Sends SIGTERM, waits up to ``timeout`` seconds, then sends SIGKILL if
        the process has not exited. Returns the exit code.
        """

        handle = self._processes.get(server_id)
        if handle is None:
            raise ProcessError(f"No tracked process for server {server_id}.")

        process = handle.process
        if process.returncode is not None:
            handle.exit_code = process.returncode
            handle.stopped_at = asyncio.get_event_loop().time()
            return handle.exit_code

        try:
            process.terminate()  # SIGTERM
        except ProcessLookupError:
            pass

        try:
            await asyncio.wait_for(process.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning("Server %s did not stop within %ss; sending SIGKILL", server_id, timeout)
            try:
                process.kill()  # SIGKILL
            except ProcessLookupError:
                pass
            await process.wait()

        handle.exit_code = process.returncode
        handle.stopped_at = asyncio.get_event_loop().time()
        handle.terminated = True

        # Drain pump tasks.
        for task in (handle.stdout_task, handle.stderr_task):
            if task is not None and not task.done():
                task.cancel()

        logger.info(
            "Stopped server %s (pid=%s, exit_code=%s)",
            server_id,
            process.pid,
            handle.exit_code,
        )
        return handle.exit_code

    async def stop_all(self, timeout: int = 30) -> None:
        """Stop every tracked process. Used during backend shutdown."""

        for server_id in list(self._processes.keys()):
            try:
                await self.stop(server_id, timeout=timeout)
            except Exception:  # pragma: no cover - shutdown resilience
                logger.exception("Failed to stop server %s during shutdown", server_id)

    def unregister(self, server_id: int) -> None:
        self._processes.pop(server_id, None)
