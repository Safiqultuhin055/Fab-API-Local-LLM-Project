"""Async SQLAlchemy 2.0 engine, session factory, and declarative base.

Works against SQLite (dev) and SQL Server (prod) by swapping DATABASE_URL.
`init_db` creates tables for the prototype; Alembic owns schema in the enterprise track.
"""
from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=True,
)

SessionFactory = async_sessionmaker(
    bind=engine, expire_on_commit=False, autoflush=False
)


class Base(DeclarativeBase):
    pass


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding an async DB session."""
    async with SessionFactory() as session:
        yield session


async def init_db() -> None:
    """Create tables if absent (prototype convenience)."""
    # Import models so they register on Base.metadata before create_all.
    from app.db import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
