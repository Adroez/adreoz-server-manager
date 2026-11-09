"""Per-server operation coordination.

Provides a shared locking mechanism to coordinate server lifecycle operations
(start, stop, restart, delete) with backup/restore operations.

This is a single-process, in-memory coordination suitable for a single Uvicorn
worker. Multiple workers are NOT safely supported without external coordination
(e.g., Redis, database advisory locks, or a file-based lock on shared storage).
"""

from __future__ import annotations

import logging
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterator, Optional

from app.services.errors import ConflictError

logger = logging.getLogger("asm.ops")


class OperationType(Enum):
    """Types of operations that require coordination."""

    START = "start"
    STOP = "stop"
    RESTART = "restart"
    DELETE = "delete"
    BACKUP_CREATE = "backup_create"
    BACKUP_RESTORE = "backup_restore"


# Exclusive operations that conflict with each other and with DELETE.
# These require the mutex lock.
EXCLUSIVE_OPERATIONS = {
    OperationType.START,
    OperationType.RESTART,
    OperationType.DELETE,
    OperationType.BACKUP_CREATE,
    OperationType.BACKUP_RESTORE,
}

# STOP only conflicts with DELETE and other STOPs.
# It does NOT conflict with START, RESTART, BACKUP_CREATE, BACKUP_RESTORE.


@dataclass
class OperationState:
    """Tracks the state of an in-progress operation."""

    operation: OperationType
    server_id: int
    owner: str  # Thread/async task identifier
    started_at: float
    metadata: dict = field(default_factory=dict)


# Per-server operation locks and state tracking.
_lock_registry_guard = threading.Lock()
_server_operation_locks: dict[int, threading.Lock] = {}
_server_operations: dict[int, Optional[OperationState]] = {}


def _get_lock(server_id: int) -> threading.Lock:
    """Get or create the per-server operation lock (mutex for exclusive ops)."""
    with _lock_registry_guard:
        lock = _server_operation_locks.get(server_id)
        if lock is None:
            lock = threading.Lock()
            _server_operation_locks[server_id] = lock
        return lock


def _get_current_operation(server_id: int) -> Optional[OperationState]:
    """Get the current operation for a server, if any."""
    with _lock_registry_guard:
        return _server_operations.get(server_id)


def _set_current_operation(
    server_id: int, operation: Optional[OperationState]
) -> None:
    """Set the current operation for a server."""
    with _lock_registry_guard:
        if operation is None:
            _server_operations.pop(server_id, None)
        else:
            _server_operations[server_id] = operation


@contextmanager
def acquire_operation_lock(
    server_id: int,
    operation: OperationType,
    *,
    owner: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> Iterator[None]:
    """Acquire a per-server operation lock with conflict detection.

    For exclusive operations (START, RESTART, DELETE, BACKUP_CREATE, BACKUP_RESTORE),
    this acquires a mutex that prevents other exclusive operations from running.

    For STOP, this uses a lighter check that only conflicts with DELETE and other STOPs.
    STOP is allowed to proceed even when backup/restore/start/restart are running.

    Args:
        server_id: The server ID to lock.
        operation: The type of operation being performed.
        owner: Optional identifier for the lock owner (thread/task ID).
        metadata: Optional metadata about the operation.

    Raises:
        ConflictError: If a conflicting operation is in progress.

    Yields:
        None when the lock is acquired. The lock is released on exit.
    """
    if owner is None:
        owner = f"{threading.get_ident()}-{uuid.uuid4().hex[:8]}"

    is_exclusive = operation in EXCLUSIVE_OPERATIONS

    if is_exclusive:
        # Exclusive operations: acquire mutex and check conflicts.
        lock = _get_lock(server_id)
        if not lock.acquire(blocking=False):
            current = _get_current_operation(server_id)
            if current:
                raise ConflictError(
                    f"Server is busy with {current.operation.value} "
                    f"(started by {current.owner}). Please wait."
                )
            raise ConflictError(
                "Another operation is already in progress for this server. "
                "Please wait for it to finish."
            )

        # Check for conflicting exclusive operations.
        current = _get_current_operation(server_id)
        if current and current.operation in EXCLUSIVE_OPERATIONS:
            lock.release()
            raise ConflictError(
                f"Cannot {operation.value}: server is busy with {current.operation.value}."
            )

        # Register this operation.
        import time

        op_state = OperationState(
            operation=operation,
            server_id=server_id,
            owner=owner,
            started_at=time.monotonic(),
            metadata=metadata or {},
        )
        _set_current_operation(server_id, op_state)

        try:
            yield
        finally:
            current = _get_current_operation(server_id)
            if current is not None and current.owner == owner:
                _set_current_operation(server_id, None)
            lock.release()
    else:
        # STOP operation: no mutex, just check for DELETE and other STOPs.
        with _lock_registry_guard:
            current = _server_operations.get(server_id)
            if current:
                if current.operation == OperationType.DELETE:
                    raise ConflictError(
                        "Cannot stop: server is being deleted."
                    )
                if current.operation == OperationType.STOP:
                    raise ConflictError(
                        "Another stop operation is already in progress."
                    )

            # Register STOP operation.
            import time

            op_state = OperationState(
                operation=operation,
                server_id=server_id,
                owner=owner,
                started_at=time.monotonic(),
                metadata=metadata or {},
            )
            _server_operations[server_id] = op_state

        try:
            yield
        finally:
            # Clean up our operation state if we're still the owner.
            with _lock_registry_guard:
                current = _server_operations.get(server_id)
                if current is not None and current.owner == owner:
                    _server_operations.pop(server_id, None)


def get_server_operation_status(server_id: int) -> Optional[dict]:
    """Get the current operation status for a server, if any."""
    current = _get_current_operation(server_id)
    if current is None:
        return None
    import time

    return {
        "operation": current.operation.value,
        "owner": current.owner,
        "duration_seconds": time.monotonic() - current.started_at,
        "metadata": current.metadata,
    }


def is_server_busy(server_id: int) -> bool:
    """Check if a server has any operation in progress."""
    return _get_current_operation(server_id) is not None