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


# Exclusive operations that conflict with each other.
# These cannot run concurrently with other exclusive operations.
EXCLUSIVE_OPERATIONS = {
    OperationType.START,
    OperationType.RESTART,
    OperationType.DELETE,
    OperationType.BACKUP_CREATE,
    OperationType.BACKUP_RESTORE,
}

# Compatibility matrix: for each operation, which other operations can run concurrently.
# Key: operation type, Value: set of operation types that are COMPATIBLE with it.
# If an operation is not in this set, it conflicts and cannot run concurrently.
COMPATIBLE_WITH: dict[OperationType, set[OperationType]] = {
    OperationType.START: {
        OperationType.STOP,  # STOP allowed during START (to kill stuck process)
    },
    OperationType.STOP: {
        OperationType.START,  # STOP allowed during START
        OperationType.RESTART,  # STOP allowed during RESTART
        OperationType.BACKUP_CREATE,  # STOP allowed during backup
        OperationType.BACKUP_RESTORE,  # STOP allowed during restore
    },
    OperationType.RESTART: {
        OperationType.STOP,  # STOP allowed during RESTART
    },
    OperationType.DELETE: {
        # DELETE is compatible with NOTHING
    },
    OperationType.BACKUP_CREATE: {
        OperationType.STOP,  # STOP allowed during backup
    },
    OperationType.BACKUP_RESTORE: {
        OperationType.STOP,  # STOP allowed during restore
    },
}


@dataclass
class OperationState:
    """Tracks the state of an in-progress operation."""

    operation: OperationType
    server_id: int
    owner: str  # Thread/async task identifier
    started_at: float
    metadata: dict = field(default_factory=dict)


# Per-server operation state tracking.
# Protected by _lock_registry_guard (RLock for reentrancy).
_lock_registry_guard = threading.RLock()
_server_operations: dict[int, Optional[OperationState]] = {}


def _get_current_operation(server_id: int) -> Optional[OperationState]:
    """Get the current operation for a server, if any."""
    with _lock_registry_guard:
        return _server_operations.get(server_id)


def _has_exclusive_operation(server_id: int) -> bool:
    """Check if there's an exclusive operation running for the server."""
    with _lock_registry_guard:
        current = _server_operations.get(server_id)
        return current is not None and current.operation in EXCLUSIVE_OPERATIONS


def _check_conflicts(server_id: int, operation: OperationType) -> None:
    """Check if the new operation conflicts with any currently running operation.

    Raises ConflictError if a conflict is detected.
    """
    current = _get_current_operation(server_id)
    if current is None:
        return

    # Check forward compatibility: is new operation compatible with current?
    compatible = COMPATIBLE_WITH.get(current.operation, set())
    if operation not in compatible:
        raise ConflictError(
            f"Cannot {operation.value}: server is busy with {current.operation.value}."
        )

    # Check reverse compatibility: is current operation compatible with new?
    compatible_with_new = COMPATIBLE_WITH.get(operation, set())
    if current.operation not in compatible_with_new:
        raise ConflictError(
            f"Cannot {operation.value}: server is busy with {current.operation.value}."
        )


@contextmanager
def acquire_operation_lock(
    server_id: int,
    operation: OperationType,
    *,
    owner: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> Iterator[None]:
    """Acquire a per-server operation lock with conflict detection.

    Exclusive operations (START, RESTART, DELETE, BACKUP_CREATE, BACKUP_RESTORE)
    cannot run concurrently with other exclusive operations.

    STOP is not exclusive and can run concurrently with exclusive operations,
    but still registers itself and checks for conflicts.

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

    with _lock_registry_guard:
        # For exclusive operations, check if another exclusive operation is running.
        if is_exclusive and _has_exclusive_operation(server_id):
            current = _server_operations.get(server_id)
            if current:
                raise ConflictError(
                    f"Server is busy with {current.operation.value} "
                    f"(started by {current.owner}). Please wait."
                )
            raise ConflictError(
                "Another operation is already in progress for this server. "
                "Please wait for it to finish."
            )

        # Check for conflicts with any currently running operation.
        _check_conflicts(server_id, operation)

        # Register this operation.
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