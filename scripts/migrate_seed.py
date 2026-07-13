"""Migrate + seed the active database (SQL Server if reachable, else local).

Migration : SQLAlchemy `create_all` builds any missing tables (api_keys,
            request_logs, settings, users) — no Alembic in the prototype track.
Seeding   : idempotent. Creates the admin login user and a sensible default
            model setting only if absent; never overwrites existing rows.

Run:  python -m scripts.migrate_seed   (from the venv)
"""
from __future__ import annotations

import asyncio

from app.core.config import settings
from app.db.base import SessionFactory, engine, init_db, resolve_database_url
from app.services import settings_service, user_service


async def _seed_admin_user() -> str:
    """Ensure the admin login user exists. Returns a status string."""
    async with SessionFactory() as db:
        existing = await user_service.get_by_username(db, settings.admin_username)
        if existing is not None:
            return f"user '{settings.admin_username}' already exists — skipped"
        await user_service.create_user(
            db,
            username=settings.admin_username,
            password=settings.admin_password,
            full_name="Administrator",
            role="admin",
            created_by="seed",
        )
        return f"user '{settings.admin_username}' created"


async def _seed_default_model() -> str:
    """Seed the default-model setting if not already configured."""
    async with SessionFactory() as db:
        current = await settings_service.get_setting(db, "models.default")
        if current:
            return f"models.default already set ({current}) — skipped"
        await settings_service.set_default_model(db, settings.default_model)
        return f"models.default set to {settings.default_model}"


async def main() -> None:
    url = resolve_database_url()
    masked = url.split("://", 1)[0] + "://..."  # scheme only; hide credentials
    print(f"[migrate] target DB: {masked}")

    await init_db()
    print("[migrate] create_all complete (api_keys, request_logs, settings, users)")

    print("[seed] " + await _seed_admin_user())
    print("[seed] " + await _seed_default_model())

    await engine.dispose()
    print("[done] migration + seed finished")


if __name__ == "__main__":
    asyncio.run(main())
