"""User-facing schemas."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from app.core.clock import UtcDateTime
from app.core.roles import Role
from app.schemas.common import StrictRequest


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    email: EmailStr | None
    role: Role
    is_active: bool
    is_admin: bool
    created_at: UtcDateTime
    updated_at: UtcDateTime
    last_login_at: UtcDateTime | None


class UserCreateRequest(StrictRequest):
    username: str = Field(min_length=3, max_length=32)
    email: EmailStr | None = None
    password: str = Field(min_length=1, max_length=128)
    role: Role = Role.USER

    @field_validator("username")
    @classmethod
    def strip_username(cls, value: str) -> str:
        return value.strip()


class UserUpdateRequest(StrictRequest):
    role: Role | None = None
    is_active: bool | None = None

    @model_validator(mode="after")
    def require_change(self) -> "UserUpdateRequest":
        if self.role is None and self.is_active is None:
            raise ValueError("Provide at least one field to update.")
        return self


class ResetPasswordRequest(StrictRequest):
    new_password: str = Field(min_length=1, max_length=128)


class UserListResponse(BaseModel):
    items: list[UserOut]
    total: int
    limit: int
    offset: int
