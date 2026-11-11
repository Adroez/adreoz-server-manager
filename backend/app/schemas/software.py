"""Server software installation schemas."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class SoftwareInstallRequest(BaseModel):
    """Request to install Minecraft server software for a server."""

    software_type: str = Field(
        ..., min_length=1, max_length=32
    )
    version: str = Field(..., min_length=1, max_length=64)
    build_version: Optional[str] = Field(
        default=None, max_length=64
    )


class SoftwareInstallResponse(BaseModel):
    """Response for software installation."""

    message: str
    server_id: int
    software_type: str
    version: str
    software_version: str
    install_status: str
    path: str
    size_bytes: int
    sha256: Optional[str] = None


class SoftwareInstallStatus(BaseModel):
    """Status of software installation for a server."""

    server_id: int
    software_type: str
    version: str
    software_version: Optional[str] = None
    install_status: str
    install_error: Optional[str] = None
    jar_path: str
    jar_exists: bool


class SoftwareVersionOut(BaseModel):
    """A single Minecraft version entry."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    type: str
    release_time: Optional[str] = None


class SoftwareVersionListResponse(BaseModel):
    """List of available Minecraft versions for a software type."""

    software_type: str
    versions: list[SoftwareVersionOut]


class SoftwareBuildOut(BaseModel):
    """A single build/loader version entry."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    time: Optional[str] = None


class SoftwareBuildListResponse(BaseModel):
    """List of available builds for a software type and version."""

    software_type: str
    version: str
    builds: list[SoftwareBuildOut]
