"""User account management and initial administrator bootstrap."""

from __future__ import annotations

from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.roles import Role
from app.core.security import PasswordPolicyError, hash_password, validate_password
from app.core.validation import UsernameError, normalize_username, validate_username
from app.models.auth_session import AuthSession
from app.models.setting import AppSetting
from app.models.user import User
from app.services.audit import RequestMeta, record_audit
from app.services.errors import ConflictError, DomainError, NotFoundError


def count_users(db: Session) -> int:
    return int(db.scalar(select(func.count(User.id))) or 0)


def is_setup_complete(db: Session) -> bool:
    """Setup is complete once the bootstrap marker exists or users exist.

    The marker is preferred so a half-manual database state cannot silently
    reopen first-run setup.
    """

    if db.get(AppSetting, "setup_complete") is not None:
        return True
    return count_users(db) > 0


def count_admins(db: Session) -> int:
    return int(
        db.scalar(select(func.count(User.id)).where(User.role == Role.ADMIN.value))
        or 0
    )


def count_active_admins(db: Session) -> int:
    return int(
        db.scalar(
            select(func.count(User.id)).where(
                User.role == Role.ADMIN.value,
                User.is_active.is_(True),
            )
        )
        or 0
    )


def get_user(db: Session, user_id: int) -> User | None:
    return db.get(User, user_id)


def get_user_or_404(db: Session, user_id: int) -> User:
    user = get_user(db, user_id)
    if user is None:
        raise NotFoundError("User not found.")
    return user


def list_users(
    db: Session,
    *,
    search: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[User], int]:
    conditions = []
    if search:
        term = f"%{search.strip().lower()[:64]}%"
        conditions.append(
            or_(
                func.lower(User.username_normalized).like(term),
                func.lower(User.email).like(term),
            )
        )

    count_stmt = select(func.count(User.id))
    rows_stmt = select(User).order_by(User.id.asc()).limit(limit).offset(offset)
    if conditions:
        count_stmt = count_stmt.where(*conditions)
        rows_stmt = rows_stmt.where(*conditions)

    total = int(db.scalar(count_stmt) or 0)
    return list(db.scalars(rows_stmt).all()), total


def _validated_identity(username: str, password: str) -> tuple[str, str]:
    try:
        display_name = validate_username(username)
    except UsernameError as exc:
        raise DomainError(str(exc)) from exc
    try:
        validate_password(password)
    except PasswordPolicyError as exc:
        raise DomainError(str(exc)) from exc
    return display_name, normalize_username(display_name)


def _normalized_email(email: str | None) -> str | None:
    if email is None:
        return None
    cleaned = email.strip().lower()
    return cleaned or None


def _ensure_unique(
    db: Session,
    *,
    username_normalized: str,
    email: str | None,
    exclude_user_id: int | None = None,
) -> None:
    existing = db.scalar(
        select(User).where(User.username_normalized == username_normalized)
    )
    if existing is not None and existing.id != exclude_user_id:
        raise ConflictError("That username is already taken.")

    if email is not None:
        existing_email = db.scalar(select(User).where(User.email == email))
        if existing_email is not None and existing_email.id != exclude_user_id:
            raise ConflictError("That email address is already in use.")


def create_user(
    db: Session,
    *,
    username: str,
    email: str | None,
    password: str,
    role: Role = Role.USER,
    actor: User | None,
    meta: RequestMeta | None = None,
    audit_action: str = "user.created",
) -> User:
    display_name, normalized = _validated_identity(username, password)
    normalized_email = _normalized_email(email)
    _ensure_unique(db, username_normalized=normalized, email=normalized_email)

    user = User(
        username=display_name,
        username_normalized=normalized,
        email=normalized_email,
        password_hash=hash_password(password),
        role=role.value,
        is_active=True,
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError("That username or email is already in use.") from exc

    if actor is not None:
        record_audit(
            db,
            action=audit_action,
            actor=actor,
            detail=f"Created account '{user.username}' with role '{user.role}'.",
            meta=meta,
        )
    db.commit()
    db.refresh(user)
    return user


def create_initial_admin(
    db: Session,
    *,
    username: str,
    email: str | None,
    password: str,
    meta: RequestMeta | None = None,
) -> User:
    if is_setup_complete(db):
        raise ConflictError("Setup has already been completed.")

    display_name, normalized = _validated_identity(username, password)
    normalized_email = _normalized_email(email)
    _ensure_unique(db, username_normalized=normalized, email=normalized_email)

    # A unique bootstrap marker closes the race where two first-run requests
    # both observe an empty users table. SQLite serializes the competing
    # inserts, so exactly one setup can commit.
    db.add(AppSetting(key="setup_complete", value="1"))
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError("Setup has already been completed.") from exc

    user = User(
        username=display_name,
        username_normalized=normalized,
        email=normalized_email,
        password_hash=hash_password(password),
        role=Role.ADMIN.value,
        is_active=True,
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError("Setup has already been completed.") from exc

    record_audit(
        db,
        action="setup.completed",
        actor=user,
        detail="Initial administrator account created.",
        meta=meta,
    )
    db.commit()
    db.refresh(user)
    return user


def update_user(
    db: Session,
    *,
    target: User,
    actor: User,
    role: Role | None,
    is_active: bool | None,
    meta: RequestMeta | None = None,
) -> User:
    if target.id == actor.id:
        if role is not None and role.value != target.role:
            raise DomainError("You cannot change your own role.")
        if is_active is not None and not is_active:
            raise DomainError("You cannot deactivate your own account.")

    changes: list[str] = []

    if role is not None and role.value != target.role:
        if target.role == Role.ADMIN.value and role == Role.USER:
            if count_active_admins(db) <= 1:
                raise ConflictError("At least one active administrator is required.")
        target.role = role.value
        changes.append(f"role={role.value}")

    if is_active is not None and is_active != target.is_active:
        if not is_active and target.role == Role.ADMIN.value:
            if count_active_admins(db) <= 1:
                raise ConflictError("At least one active administrator is required.")
        target.is_active = is_active
        changes.append(f"active={str(is_active).lower()}")
        if not is_active:
            db.execute(
                delete(AuthSession).where(AuthSession.user_id == target.id)
            )

    if not changes:
        return target

    record_audit(
        db,
        action="user.updated",
        actor=actor,
        detail=f"Updated '{target.username}': {', '.join(changes)}.",
        meta=meta,
    )
    db.commit()
    db.refresh(target)
    return target


def delete_user(
    db: Session,
    *,
    target: User,
    actor: User,
    meta: RequestMeta | None = None,
) -> None:
    if target.id == actor.id:
        raise DomainError("You cannot delete your own account.")
    if target.role == Role.ADMIN.value and count_admins(db) <= 1:
        raise ConflictError("At least one administrator is required.")

    target_name = target.username
    target_id = target.id
    db.delete(target)
    record_audit(
        db,
        action="user.deleted",
        actor=actor,
        detail=f"Deleted account '{target_name}' (id={target_id}).",
        meta=meta,
    )
    db.commit()


def reset_password(
    db: Session,
    *,
    target: User,
    new_password: str,
    actor: User,
    meta: RequestMeta | None = None,
) -> None:
    try:
        validate_password(new_password)
    except PasswordPolicyError as exc:
        raise DomainError(str(exc)) from exc

    target.password_hash = hash_password(new_password)
    db.execute(delete(AuthSession).where(AuthSession.user_id == target.id))
    record_audit(
        db,
        action="user.password_reset",
        actor=actor,
        detail=f"Password reset for '{target.username}'; sessions revoked.",
        meta=meta,
    )
    db.commit()
