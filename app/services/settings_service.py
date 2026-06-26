"""Key/value settings + model enable/disable + default-model selection.

Backed by the `settings` table (no new tables — honours the 3-table diagram).
Reserved keys:
  models.enabled : JSON array of enabled model names. Absent => all models allowed.
  models.default : default model name.
"""
from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings as cfg
from app.db.models import Setting

_ENABLED_KEY = "models.enabled"
_DEFAULT_KEY = "models.default"


async def get_setting(db: AsyncSession, key: str) -> str | None:
    row = (
        await db.execute(select(Setting).where(Setting.setting_key == key))
    ).scalar_one_or_none()
    return row.setting_value if row else None


async def set_setting(db: AsyncSession, key: str, value: str | None) -> None:
    row = (
        await db.execute(select(Setting).where(Setting.setting_key == key))
    ).scalar_one_or_none()
    if row is None:
        db.add(Setting(setting_key=key, setting_value=value))
    else:
        row.setting_value = value
    await db.commit()


async def get_enabled_models(db: AsyncSession) -> set[str] | None:
    """Return the enabled set, or None when no restriction is configured."""
    raw = await get_setting(db, _ENABLED_KEY)
    if not raw:
        return None
    try:
        return set(json.loads(raw))
    except (ValueError, TypeError):
        return None


async def is_model_allowed(db: AsyncSession, name: str) -> bool:
    enabled = await get_enabled_models(db)
    return enabled is None or name in enabled


async def enable_model(db: AsyncSession, name: str) -> None:
    enabled = await get_enabled_models(db) or set()
    enabled.add(name)
    await set_setting(db, _ENABLED_KEY, json.dumps(sorted(enabled)))


async def disable_model(db: AsyncSession, name: str) -> None:
    # Disabling starts an explicit allow-list seeded from current Ollama-known set
    # is overkill here; we simply ensure the name is removed from any enabled set.
    enabled = await get_enabled_models(db)
    if enabled is None:
        # No restriction yet: create one excluding this model is ambiguous without
        # the full model list, so we store an empty marker the caller can populate.
        enabled = set()
    enabled.discard(name)
    await set_setting(db, _ENABLED_KEY, json.dumps(sorted(enabled)))


async def get_default_model(db: AsyncSession) -> str:
    return (await get_setting(db, _DEFAULT_KEY)) or cfg.default_model


async def set_default_model(db: AsyncSession, name: str) -> None:
    await set_setting(db, _DEFAULT_KEY, name)
