"""Java runtime schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field
from app.core.clock import UtcDateTime


class JavaRuntimeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: Optional[int] = None
    name: str
    vendor: str
    version: str
    major_version: int
    executable_path: str
    source_url: Optional[str] = None
    checksum: Optional[str] = None
    checksum_algorithm: Optional[str] = None
    install_status: str
    installed_at: Optional[UtcDateTime] = None
    is_managed: bool


class JavaRuntimeListResponse(BaseModel):
    items: list[JavaRuntimeOut]
    total: int


class JavaRuntimeInstallRequest(BaseModel):
    """Request to install a managed Java runtime."""

    name: Optional[str] = Field(default=None, max_length=128)
    version: Optional[str] = Field(default=None, max_length=32)
    vendor: Optional[str] = Field(default=None, max_length=64)


class JavaRuntimeInstallResponse(BaseModel):
    message: str
    runtime: JavaRuntimeOut
