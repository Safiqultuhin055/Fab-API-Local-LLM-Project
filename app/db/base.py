"""Async SQLAlchemy 2.0 engine, session factory, and declarative base.

The gateway connects exclusively to Microsoft SQL Server (MSSQL_HOST +
MSSQL_PASSWORD). There is no SQLite fallback: if SQL Server is not configured or
is unreachable at startup, the app refuses to start. `init_db` creates tables for
the prototype; Alembic owns schema in the enterprise track.
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
    """Return the SQL Server URL, or raise if it's not configured/unreachable.

    There is no SQLite fallback — a misconfigured or unreachable database is a
    hard startup error so data never silently lands in a throwaway local DB.
    """
    mssql = settings.mssql_url
    if not mssql:
        raise RuntimeError(
            "SQL Server not configured: set MSSQL_HOST and MSSQL_PASSWORD in .env. "
            "In Docker, ensure .env reaches the container via env_file and is not "
            "overridden by empty environment: values."
        )
    if not _tcp_reachable(settings.mssql_host, settings.mssql_port, settings.db_probe_timeout):
        raise RuntimeError(
            f"SQL Server {settings.mssql_host}:{settings.mssql_port} unreachable. "
            "Check MSSQL_HOST/PORT/PASSWORD and network reachability from this host."
        )
    logger.info(
        "DB: SQL Server @ %s:%s/%s",
        settings.mssql_host, settings.mssql_port, settings.mssql_database,
    )
    return mssql


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
