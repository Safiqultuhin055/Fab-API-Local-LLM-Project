"""Admin user accounts: creation, lookup, and password authentication.

Backs the admin-console login (previously env-only). Passwords are hashed via
`app.core.security` (Argon2id or PBKDF2 fallback); plaintext is never stored.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import security
from app.db.models import User


async def get_by_username(db: AsyncSession, username: str) -> User | None:
    # NB: `== False` renders `= 0` (valid T-SQL BIT); `.is_(False)` renders the
    # SQL-Server-invalid `IS 0`.
    return (
        await db.execute(
            select(User).where(
                User.username == username,
                User.is_deleted == False,  # noqa: E712
            )
        )
    ).scalar_one_or_none()


async def count_users(db: AsyncSession) -> int:
    return int(
        (await db.execute(select(func.count()).select_from(User))).scalar_one()
    )


async def create_user(
    db: AsyncSession,
    *,
    username: str,
    password: str,
    full_name: str | None = None,
    email: str | None = None,
    role: str = "admin",
    created_by: str | None = "seed",
) -> User:
    """Create a user with a hashed password. Caller ensures username is unique."""
    row = User(
        username=username,
        password_hash=security.hash_password(password),
        full_name=full_name,
        email=email,
        role=role,
        created_by=created_by,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def authenticate(db: AsyncSession, username: str, password: str) -> User | None:
    """Return the active user if the password verifies, else None."""
    row = await get_by_username(db, username)
    if row is None or not row.is_active:
        return None
    if not security.verify_password(password, row.password_hash):
        return None
    row.last_login = datetime.now(timezone.utc)
    await db.commit()
    return row
