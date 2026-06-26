"""ORM models (prototype track): api_keys, request_logs, settings.

Corrects the diagram: api_keys stores key_hash + key_prefix, NEVER a raw key.
All tables carry soft-delete + audit columns to stay enterprise-compatible.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )
    created_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ApiKey(TimestampMixin, Base):
    __tablename__ = "api_keys"
    __table_args__ = (
        UniqueConstraint("key_hash", name="uq_api_keys_key_hash"),
        Index("ix_api_keys_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Stored secret material — hash (for lookup) + non-secret display prefix.
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    # Reversibly-encrypted full key so the admin UI can re-copy it. Never plaintext.
    key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default="active", nullable=False
    )  # active | disabled | expired
    tier: Mapped[str] = mapped_column(String(20), default="free", nullable=False)
    rate_limit: Mapped[int] = mapped_column(Integer, default=1000, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_used: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Optional comma-separated IP allow-list. Empty/NULL = any IP allowed.
    ip_whitelist: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    @property
    def is_active(self) -> bool:
        if self.is_deleted or self.status != "active":
            return False
        if self.expires_at and self.expires_at < _utcnow():
            return False
        return True


class RequestLog(TimestampMixin, Base):
    __tablename__ = "request_logs"
    __table_args__ = (
        Index("ix_request_logs_created_date", "created_date"),
        Index("ix_request_logs_api_key_id", "api_key_id"),
        Index("ix_request_logs_model", "model"),
    )

    # BIGINT on SQL Server; INTEGER on SQLite so rowid autoincrement works.
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    api_key_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    key_prefix: Mapped[str | None] = mapped_column(String(16), nullable=True)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    endpoint: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    response: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    response_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(400), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class Setting(TimestampMixin, Base):
    __tablename__ = "settings"
    __table_args__ = (UniqueConstraint("setting_key", name="uq_settings_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    setting_key: Mapped[str] = mapped_column(String(100), nullable=False)
    setting_value: Mapped[str | None] = mapped_column(Text, nullable=True)
