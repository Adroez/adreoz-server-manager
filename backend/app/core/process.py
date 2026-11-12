"""Asynchronous Minecraft server process management.

Servers are launched as child processes of the panel. The panel tracks each
running process in memory and captures stdout/stderr into the database. No
shell interpolation is ever used: the Java executable and its arguments are
passed as a list, preventing command injection.

Exactly one reader task per stream (stdout/stderr) is created by
ProcessManager.register. The callbacks handle database persistence and
WebSocket broadcasting.
"""

from __future__ import annotations

import asyncio
import codecs
import inspect
import logging
import os
import signal
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger("asm.process")

# Longest single log line forwarded to callbacks. Longer lines are split into
# several entries instead of raising (as StreamReader.readline would).
MAX_LINE_CHARS = 8192

# Bytes read from a pipe per iteration.
READ_CHUNK_BYTES = 65536


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
    # Set *before* the panel asks the process to stop, so the exit monitor can
    # tell an intentional shutdown apart from a crash. ``terminated`` is set
    # after the process has actually exited and cannot be used for this.
    stop_requested: bool = False

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

    Exactly one reader task per stream is created per process.
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
        output_callback: Callable[[int, str, str], None] | None = None,
    ) -> ProcessHandle:
        """Register a process with a single output callback.

        Args:
            server_id: The server ID.
            process: The asyncio subprocess.
            output_callback: Called as callback(server_id, stream_name, line)
                for each line of output. stream_name is "stdout" or "stderr".
                If None, output is only logged.

        Returns:
            The ProcessHandle for the registered process.
        """
        if self.has(server_id):
            raise ProcessError(f"Server {server_id} already has a tracked process.")

        handle = ProcessHandle(server_id=server_id, process=process)
        self._processes[server_id] = handle

        async def emit(stream_name: str, text: str) -> None:
            """Forward one log line to the callback (sync or async)."""

            if not text:
                return
            if not output_callback:
                logger.debug("[server %s] %s", server_id, text)
                return
            try:
                result = output_callback(server_id, stream_name, text)
                if inspect.isawaitable(result):
                    await result
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover - callback resilience
                logger.exception(
                    "Process output callback failed for server %s", server_id
                )

        async def pump(stream, stream_name: str) -> None:
            """Read a pipe in chunks and split it into bounded lines.

            ``StreamReader.readline`` raises when a line exceeds the reader's
            64 KiB limit, which silently killed log capture for chatty or
            malformed servers. Reading raw chunks and splitting manually keeps
            capture alive for arbitrarily long output.
            """

            assert stream is not None
            decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            buffer = ""

            async def emit_line(line: str) -> None:
                """Emit one logical line, split into bounded chunks.

                A single newline-terminated line can be far longer than the
                limit (for example a stack trace or a serialised blob), so the
                chunking must apply after splitting on the newline as well.
                """

                if line.endswith("\r"):
                    line = line[:-1]
                for start in range(0, len(line), MAX_LINE_CHARS):
                    await emit(stream_name, line[start : start + MAX_LINE_CHARS])

            while True:
                try:
                    chunk = await stream.read(READ_CHUNK_BYTES)
                except (asyncio.IncompleteReadError, ConnectionResetError):
                    break
                if not chunk:
                    break
                buffer += decoder.decode(chunk)
                while True:
                    newline = buffer.find("\n")
                    if newline < 0:
                        break
                    line = buffer[:newline]
                    buffer = buffer[newline + 1 :]
                    await emit_line(line)
                # Emit oversized partial lines rather than buffering forever.
                while len(buffer) > MAX_LINE_CHARS:
                    await emit(stream_name, buffer[:MAX_LINE_CHARS])
                    buffer = buffer[MAX_LINE_CHARS:]
            buffer += decoder.decode(b"", final=True)
            if buffer:
                await emit_line(buffer)

        handle.stdout_task = asyncio.create_task(pump(process.stdout, "stdout"))
        handle.stderr_task = asyncio.create_task(pump(process.stderr, "stderr"))
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
        # Record the intent before signalling so the exit monitor can classify
        # this exit as a graceful stop rather than a crash.
        handle.stop_requested = True

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
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception:  # pragma: no cover
                    logger.exception("Pump task raised during cancellation for server %s", server_id)

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