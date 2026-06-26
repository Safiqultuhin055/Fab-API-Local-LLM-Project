"""Request-log and stats schemas."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class RequestLogOut(BaseModel):
    id: int
    key_prefix: str | None
    model: str | None
    endpoint: str
    prompt: str | None
    response: str | None
    total_tokens: int | None
    status_code: int
    response_time_ms: int | None
    ip_address: str | None
    created_date: datetime

    model_config = {"from_attributes": True}


class Stats(BaseModel):
    total_api_keys: int
    active_api_keys: int
    total_requests: int
    today_requests: int
    error_count: int
    avg_response_time_ms: float
