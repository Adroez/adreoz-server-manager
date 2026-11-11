"""Domain-level exceptions mapped to HTTP responses by the app factory."""

from __future__ import annotations


class DomainError(Exception):
    status_code = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NotFoundError(DomainError):
    status_code = 404


class ConflictError(DomainError):
    status_code = 409


class ForbiddenError(DomainError):
    status_code = 403


class UpstreamError(DomainError):
    """An upstream provider failed to answer a metadata or download request."""

    status_code = 502
