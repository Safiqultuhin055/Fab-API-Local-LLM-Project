"""API-key lifecycle and verification (repository + service combined for prototype)."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import false, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import security
from app.db.models import ApiKey


async def create_key(
    db: AsyncSession,
    *,
    owner_name: str,
    tier: str,
    rate_limit: int,
    expires_at: datetime | None,
    ip_whitelist: str | None = None,
    created_by: str | None = "admin",
) -> tuple[ApiKey, str]:
    """Create a key. Returns (row, full_raw_key). Raw key is exposed only here."""
    full_key = security.generate_api_key()
    row = ApiKey(
        key_hash=security.hash_api_key(full_key),
        key_prefix=security.key_display_prefix(full_key),
        key_encrypted=security.encrypt_secret(full_key),
        owner_name=owner_name,
        tier=tier,
        rate_limit=rate_limit,
        expires_at=expires_at,
        ip_whitelist=ip_whitelist,
        created_by=created_by,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row, full_key


async def get_by_raw_key(db: AsyncSession, full_key: str) -> ApiKey | None:
    """Look up an active key by its raw value (hashed for the query)."""
    key_hash = security.hash_api_key(full_key)
    stmt = select(ApiKey).where(
        ApiKey.key_hash == key_hash, ApiKey.is_deleted == false()
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None or not row.is_active:
        return None
    return row


async def touch_last_used(db: AsyncSession, row: ApiKey) -> None:
    row.last_used = datetime.now(timezone.utc)
    await db.commit()


async def list_keys(db: AsyncSession) -> list[ApiKey]:
    stmt = (
        select(ApiKey)
        .where(ApiKey.is_deleted == false())
        .order_by(ApiKey.created_date.desc())
    )
    return list((await db.execute(stmt)).scalars().all())


async def disable_key(db: AsyncSession, key_id: int) -> ApiKey | None:
    row = await db.get(ApiKey, key_id)
    if row is None or row.is_deleted:
        return None
    row.status = "disabled"
    await db.commit()
    await db.refresh(row)
    return row


async def delete_key(db: AsyncSession, key_id: int) -> bool:
    """Soft delete."""
    row = await db.get(ApiKey, key_id)
    if row is None or row.is_deleted:
        return False
    row.is_deleted = True
    row.deleted_at = datetime.now(timezone.utc)
    row.status = "disabled"
    await db.commit()
    return True


async def count_keys(db: AsyncSession) -> tuple[int, int]:
    total = await db.scalar(
        select(func.count()).select_from(ApiKey).where(ApiKey.is_deleted == false())
    )
    active = await db.scalar(
        select(func.count())
        .select_from(ApiKey)
        .where(ApiKey.is_deleted == false(), ApiKey.status == "active")
    )
    return int(total or 0), int(active or 0)
