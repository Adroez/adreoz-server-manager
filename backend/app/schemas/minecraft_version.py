"""Minecraft version schemas."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field
from app.core.clock import UtcDateTime


class MinecraftVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    type: str
    url: str
    time: Optional[str] = None
    release_time: Optional[str] = None
    is_release: bool


class MinecraftVersionListResponse(BaseModel):
    items: list[MinecraftVersionOut]
    total: int


class MinecraftVersionMetadataOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    type: str
    time: Optional[str] = None
    release_time: Optional[str] = None
    server_download_url: Optional[str] = None
    server_download_sha1: Optional[str] = None
    server_download_size: Optional[int] = None
    java_version_major: Optional[int] = None
    is_release: bool


class MinecraftVersionDownloadRequest(BaseModel):
    """Request to download a Minecraft server JAR for a specific server."""

    server_id: int = Field(gt=0)


class MinecraftVersionDownloadResponse(BaseModel):
    message: str
    version: str
    path: str
    size_bytes: int
    sha1: Optional[str] = None
