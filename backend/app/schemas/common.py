"""Small shared response schemas."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class StrictRequest(BaseModel):
    """Request body that rejects unknown fields instead of ignoring them."""

    model_config = ConfigDict(extra="forbid")


class MessageResponse(BaseModel):
    message: str


class ValidationErrorItem(BaseModel):
    loc: list[str | int]
    msg: str
    type: str

    model_config = ConfigDict(extra="ignore")
