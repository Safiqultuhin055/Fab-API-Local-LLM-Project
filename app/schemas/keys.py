"""API-key admin schemas."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ApiKeyCreate(BaseModel):
    owner_name: str = Field(..., min_length=1, max_length=200, examples=["My Project"])
    tier: Literal["free", "pro", "enterprise"] = "free"
    rate_limit: int = Field(1000, ge=1)
    expires_at: datetime | None = None
    ip_whitelist: str | None = Field(
        None,
        description="Comma-separated allowed IPs. Empty = any IP.",
        examples=["127.0.0.1,10.0.0.5"],
    )


class ApiKeyCreated(BaseModel):
    """Returned ONCE at creation — the only time the raw key is exposed."""

    success: bool = True
    id: int
    api_key: str = Field(..., description="Full key — store it now; not retrievable later")
    key_prefix: str
    owner_name: str
    tier: str
    rate_limit: int
    created_date: datetime


class ApiKeyOut(BaseModel):
    id: int
    key_prefix: str
    owner_name: str
    status: str
    tier: str
    rate_limit: int
    expires_at: datetime | None
    last_used: datetime | None
    ip_whitelist: str | None
    created_date: datetime

    model_config = {"from_attributes": True}
