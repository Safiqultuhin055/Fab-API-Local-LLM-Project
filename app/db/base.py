"""Async SQLAlchemy 2.0 engine, session factory, and declarative base.

Database selection is automatic: if SQL Server is configured (MSSQL_HOST +
MSSQL_PASSWORD) and reachable at startup, the gateway uses it; otherwise it
falls back to the local SQLite `DATABASE_URL`. `init_db` creates tables for the
prototype; Alembic owns schema in the enterprise track.
"""
from __future__ import annotations

import socket
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def _tcp_reachable(host: str, port: int, timeout: float) -> bool:
    """True if a TCP connection to host:port opens within `timeout` seconds."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def resolve_database_url() -> str:
    """Pick SQL Server if configured + reachable, else the local SQLite URL."""
    mssql = settings.mssql_url
    if mssql:
        if _tcp_reachable(settings.mssql_host, settings.mssql_port, settings.db_probe_timeout):
            logger.info(
                "DB: SQL Server @ %s:%s/%s",
                settings.mssql_host, settings.mssql_port, settings.mssql_database,
            )
            return mssql
        logger.warning(
            "SQL Server %s:%s unreachable — falling back to local SQLite",
            settings.mssql_host, settings.mssql_port,
        )
    else:
        logger.info("DB: local SQLite (SQL Server not configured)")
    return settings.database_url


engine = create_async_engine(
    resolve_database_url(),
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
