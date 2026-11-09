"""Server lifecycle schemas."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.clock import UtcDateTime
from app.core.validation import validate_server_name
from app.schemas.common import StrictRequest


# Valid software types supported in Phase 2.
SERVER_SOFTWARE_TYPES = ("vanilla", "paper", "fabric", "forge")

# Valid difficulties.
SERVER_DIFFICULTIES = ("peaceful", "easy", "normal", "hard")

# Valid gamemodes.
SERVER_GAMEMODES = ("survival", "creative", "adventure", "spectator")


class ServerBase(StrictRequest):
    name: str = Field(min_length=1, max_length=64)
    software_type: str
    version: str = Field(min_length=1, max_length=64)
    port: int = Field(ge=1024, le=65535)
    max_players: int = Field(ge=1, le=1000)
    online_mode: bool = True
    difficulty: str = "normal"
    gamemode: str = "survival"
    view_distance: int = Field(ge=2, le=32)
    motd: str = Field(min_length=1, max_length=128)

    @field_validator("software_type")
    @classmethod
    def validate_software_type(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in SERVER_SOFTWARE_TYPES:
            raise ValueError(
                f"Unsupported software type '{value}'. "
                f"Allowed: {', '.join(SERVER_SOFTWARE_TYPES)}."
            )
        return value

    @field_validator("difficulty")
    @classmethod
    def validate_difficulty(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in SERVER_DIFFICULTIES:
            raise ValueError(
                f"Invalid difficulty '{value}'. "
                f"Allowed: {', '.join(SERVER_DIFFICULTIES)}."
            )
        return value

    @field_validator("gamemode")
    @classmethod
    def validate_gamemode(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in SERVER_GAMEMODES:
            raise ValueError(
                f"Invalid gamemode '{value}'. "
                f"Allowed: {', '.join(SERVER_GAMEMODES)}."
            )
        return value

    @field_validator("motd")
    @classmethod
    def strip_motd(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def validate_name_and_directory(self) -> "ServerBase":
        validate_server_name(self.name)
        return self


class ServerCreateRequest(ServerBase):
    """Request to create a new Minecraft server."""

    directory: str | None = Field(default=None, max_length=512)


class ServerUpdateRequest(StrictRequest):
    """Request to update server configuration (stopped servers only)."""

    name: str | None = Field(default=None, min_length=1, max_length=64)
    port: int | None = Field(default=None, ge=1024, le=65535)
    max_players: int | None = Field(default=None, ge=1, le=1000)
    online_mode: bool | None = None
    difficulty: str | None = None
    gamemode: str | None = None
    view_distance: int | None = Field(default=None, ge=2, le=32)
    motd: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("difficulty")
    @classmethod
    def validate_difficulty(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip().lower()
        if value not in SERVER_DIFFICULTIES:
            raise ValueError(
                f"Invalid difficulty '{value}'. "
                f"Allowed: {', '.join(SERVER_DIFFICULTIES)}."
            )
        return value

    @field_validator("gamemode")
    @classmethod
    def validate_gamemode(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip().lower()
        if value not in SERVER_GAMEMODES:
            raise ValueError(
                f"Invalid gamemode '{value}'. "
                f"Allowed: {', '.join(SERVER_GAMEMODES)}."
            )
        return value

    @field_validator("motd")
    @classmethod
    def strip_motd(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip()

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        validate_server_name(value)
        return value


class ServerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    owner_id: int
    unique_id: str
    name: str
    software_type: str
    version: str
    directory: str
    port: int
    max_players: int
    online_mode: bool
    difficulty: str
    gamemode: str
    view_distance: int
    motd: str
    status: str
    process_id: int | None
    created_at: UtcDateTime
    updated_at: UtcDateTime
    started_at: UtcDateTime | None
    stopped_at: UtcDateTime | None


class ServerListResponse(BaseModel):
    items: list[ServerOut]
    total: int
    limit: int
    offset: int


class ServerActionResponse(BaseModel):
    message: str
    status: str


class ServerLogEntry(BaseModel):
    id: int
    stream: str
    line: str
    created_at: UtcDateTime


class ServerLogListResponse(BaseModel):
    items: list[ServerLogEntry]
    total: int
    limit: int
    offset: int
