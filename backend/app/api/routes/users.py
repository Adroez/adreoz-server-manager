"""Administrator user management endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.deps import (
    AuthContext,
    get_db,
    require_admin,
    require_admin_write,
)
from app.schemas.common import MessageResponse
from app.schemas.user import (
    ResetPasswordRequest,
    UserCreateRequest,
    UserListResponse,
    UserOut,
    UserUpdateRequest,
)
from app.services.errors import DomainError
from app.services.users import (
    create_user,
    delete_user,
    get_user_or_404,
    list_users,
    reset_password,
    update_user,
)

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=UserListResponse)
def list_users_endpoint(
    admin: Annotated[AuthContext, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    search: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> UserListResponse:
    users, total = list_users(db, search=search, limit=limit, offset=offset)
    return UserListResponse(
        items=[UserOut.model_validate(user) for user in users],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user_endpoint(
    payload: UserCreateRequest,
    admin: Annotated[AuthContext, Depends(require_admin_write)],
    db: Annotated[Session, Depends(get_db)],
) -> UserOut:
    user = create_user(
        db,
        username=payload.username,
        email=str(payload.email) if payload.email else None,
        password=payload.password,
        role=payload.role,
        actor=admin.user,
        meta=admin.meta,
    )
    return UserOut.model_validate(user)


@router.get("/{user_id}", response_model=UserOut)
def get_user_endpoint(
    user_id: int,
    admin: Annotated[AuthContext, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> UserOut:
    return UserOut.model_validate(get_user_or_404(db, user_id))


@router.patch("/{user_id}", response_model=UserOut)
def update_user_endpoint(
    user_id: int,
    payload: UserUpdateRequest,
    admin: Annotated[AuthContext, Depends(require_admin_write)],
    db: Annotated[Session, Depends(get_db)],
) -> UserOut:
    target = get_user_or_404(db, user_id)
    updated = update_user(
        db,
        target=target,
        actor=admin.user,
        role=payload.role,
        is_active=payload.is_active,
        meta=admin.meta,
    )
    return UserOut.model_validate(updated)


@router.delete("/{user_id}", response_model=MessageResponse)
def delete_user_endpoint(
    user_id: int,
    admin: Annotated[AuthContext, Depends(require_admin_write)],
    db: Annotated[Session, Depends(get_db)],
) -> MessageResponse:
    target = get_user_or_404(db, user_id)
    username = target.username
    delete_user(db, target=target, actor=admin.user, meta=admin.meta)
    return MessageResponse(message=f"Deleted user '{username}'.")


@router.post("/{user_id}/password", response_model=MessageResponse)
def reset_password_endpoint(
    user_id: int,
    payload: ResetPasswordRequest,
    admin: Annotated[AuthContext, Depends(require_admin_write)],
    db: Annotated[Session, Depends(get_db)],
) -> MessageResponse:
    target = get_user_or_404(db, user_id)
    if target.id == admin.user.id:
        raise DomainError("Use the account page to change your own password.")
    reset_password(
        db,
        target=target,
        new_password=payload.new_password,
        actor=admin.user,
        meta=admin.meta,
    )
    return MessageResponse(
        message=f"Password reset for '{target.username}'. Their sessions were revoked."
    )
