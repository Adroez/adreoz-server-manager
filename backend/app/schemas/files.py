"""File manager and configuration editor schemas."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class FileEntry(BaseModel):
    """A file or directory entry in a server's file listing."""

    name: str
    type: str  # "file" or "directory"
    size: int
    modified_at: float
    is_symlink: bool = False


class FileListResponse(BaseModel):
    """Response for listing a server directory."""

    path: str
    parent: str | None
    items: list[FileEntry]


class FileReadResponse(BaseModel):
    """Response for reading a file."""

    path: str
    content: str
    mime_type: str
    size: int


class FileWriteRequest(BaseModel):
    """Request to create or overwrite a file."""

    path: str
    content: str

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        if not value or value.strip() != value:
            raise ValueError("Invalid file path.")
        if "\x00" in value:
            raise ValueError("Invalid file path.")
        return value


class FolderCreateRequest(BaseModel):
    """Request to create a folder."""

    path: str

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        if not value or value.strip() != value:
            raise ValueError("Invalid folder path.")
        if "\x00" in value:
            raise ValueError("Invalid folder path.")
        return value


class FileRenameRequest(BaseModel):
    """Request to rename a file or folder."""

    old_path: str
    new_path: str

    @field_validator("old_path", "new_path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        if not value or value.strip() != value:
            raise ValueError("Invalid file path.")
        if "\x00" in value:
            raise ValueError("Invalid file path.")
        return value


class FileDeleteRequest(BaseModel):
    """Request to delete a file or folder."""

    path: str

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        if not value or value.strip() != value:
            raise ValueError("Invalid file path.")
        if "\x00" in value:
            raise ValueError("Invalid file path.")
        return value


class FileActionResponse(BaseModel):
    """Response for file actions (create, rename, delete)."""

    message: str
    path: str


class ConfigPropertiesResponse(BaseModel):
    """Response for reading server.properties."""

    properties: dict[str, str]
    etag: str | None = None


class ConfigPropertiesUpdateRequest(BaseModel):
    """Request to update server.properties."""

    properties: dict[str, str]
    if_match: str | None = None


class ConfigValidationInfo(BaseModel):
    """Validation metadata for the configuration editor UI."""

    boolean: list[str]
    enum: dict[str, list[str]]
    integer: dict[str, dict[str, int]]
    text: list[str]
