"""Authentication schemas."""

from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.core.clock import UtcDateTime
from app.schemas.common import StrictRequest
from app.schemas.user import UserOut


class SetupStatus(BaseModel):
    setup_required: bool
    app_name: str
    version: str


class SetupRequest(StrictRequest):
    username: str = Field(min_length=3, max_length=32)
    email: EmailStr | None = None
    password: str = Field(min_length=1, max_length=128)

    @field_validator("username")
    @classmethod
    def strip_username(cls, value: str) -> str:
        return value.strip()


class LoginRequest(StrictRequest):
    username: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("username")
    @classmethod
    def strip_username(cls, value: str) -> str:
        return value.strip()


class AuthResponse(BaseModel):
    user: UserOut
    csrf_token: str
    expires_at: UtcDateTime


class ChangePasswordRequest(StrictRequest):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=1, max_length=128)
