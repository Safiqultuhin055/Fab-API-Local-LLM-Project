"""FastAPI dependencies: shared singletons, API-key auth, admin auth, rate limiting."""
from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ForbiddenError, RateLimitError, UnauthorizedError
from app.db.base import get_session
from app.db.models import ApiKey
from app.services import key_service
from app.services.ollama_service import OllamaService
from app.services.ratelimit import limit_for_tier

DbSession = Annotated[AsyncSession, Depends(get_session)]


def get_ollama(request: Request) -> OllamaService:
    return request.app.state.ollama


def get_rag(request: Request):
    return request.app.state.rag


def get_rate_limiter(request: Request):
    return request.app.state.rate_limiter


async def require_api_key(
    request: Request,
    db: DbSession,
    x_api_key: Annotated[str | None, Header(alias="X-API-KEY")] = None,
) -> ApiKey:
    """Authenticate a public request via X-API-KEY, then enforce the rate limit."""
    if not x_api_key:
        raise UnauthorizedError("Missing X-API-KEY header")

    row = await key_service.get_by_raw_key(db, x_api_key)
    if row is None:
        raise UnauthorizedError("Invalid, disabled, or expired API key")

    client_ip = request.client.host if request.client else "unknown"

    # Optional per-key IP allow-list.
    if row.ip_whitelist:
        allowed = {ip.strip() for ip in row.ip_whitelist.split(",") if ip.strip()}
        if allowed and client_ip not in allowed:
            raise ForbiddenError(f"IP {client_ip} not allowed for this API key")

    # Per-key limit by tier; per-IP guard to blunt key-less abuse bursts.
    limiter = get_rate_limiter(request)
    tier_limit = limit_for_tier(row.tier)
    key_res = await limiter.check(f"key:{row.id}", tier_limit)
    if not key_res.allowed:
        raise RateLimitError("API key rate limit exceeded", key_res.retry_after)

    ip_res = await limiter.check(f"ip:{client_ip}", 10_000)
    if not ip_res.allowed:
        raise RateLimitError("Per-IP rate limit exceeded", ip_res.retry_after)

    await key_service.touch_last_used(db, row)
    request.state.api_key_row = row
    return row


async def require_admin(
    x_admin_key: Annotated[str | None, Header(alias="X-ADMIN-KEY")] = None,
) -> None:
    """Guard /admin/* with the static admin key (diagram: 'API Key (Admin)')."""
    if not x_admin_key:
        raise UnauthorizedError("Missing X-ADMIN-KEY header")
    if not hmac.compare_digest(x_admin_key, settings.admin_api_key):
        raise ForbiddenError("Invalid admin key")


ApiKeyAuth = Annotated[ApiKey, Depends(require_api_key)]
AdminAuth = Annotated[None, Depends(require_admin)]
OllamaDep = Annotated[OllamaService, Depends(get_ollama)]
RagDep = Annotated["object", Depends(get_rag)]
