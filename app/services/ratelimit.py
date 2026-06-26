"""Sliding-window rate limiter, per-key and per-IP.

Two backends behind one interface:
  - MemoryRateLimiter : in-process deque per identity (dev / single worker).
  - RedisRateLimiter  : atomic sliding-window via a Lua script (prod / multi-worker).

Tiers map to a daily request budget. 'enterprise' is unlimited.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass

from app.core.config import settings

_WINDOW_SECONDS = 86_400  # 1 day

TIER_LIMITS: dict[str, int | None] = {
    "free": 100,
    "pro": 10_000,
    "enterprise": None,  # unlimited
}


@dataclass(slots=True)
class RateLimitResult:
    allowed: bool
    remaining: int | None
    retry_after: int  # seconds until a slot frees up (0 if allowed)


def limit_for_tier(tier: str) -> int | None:
    return TIER_LIMITS.get(tier, TIER_LIMITS["free"])


class MemoryRateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    async def check(self, identity: str, limit: int | None) -> RateLimitResult:
        if limit is None:
            return RateLimitResult(True, None, 0)
        now = time.time()
        window_start = now - _WINDOW_SECONDS
        dq = self._hits[identity]
        while dq and dq[0] < window_start:
            dq.popleft()
        if len(dq) >= limit:
            retry_after = int(dq[0] + _WINDOW_SECONDS - now) + 1
            return RateLimitResult(False, 0, retry_after)
        dq.append(now)
        return RateLimitResult(True, limit - len(dq), 0)

    async def close(self) -> None:  # symmetry with redis backend
        return None


_REDIS_LUA = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
local count = redis.call('ZCARD', key)
if count >= limit then
  local oldest = tonumber(redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')[2])
  return {0, 0, math.ceil(oldest + window - now)}
end
redis.call('ZADD', key, now, now .. '-' .. math.random())
redis.call('EXPIRE', key, window)
return {1, limit - count - 1, 0}
"""


class RedisRateLimiter:
    def __init__(self, url: str) -> None:
        import redis.asyncio as redis  # local import: optional dep

        self._redis = redis.from_url(url, decode_responses=True)
        self._script = self._redis.register_script(_REDIS_LUA)

    async def check(self, identity: str, limit: int | None) -> RateLimitResult:
        if limit is None:
            return RateLimitResult(True, None, 0)
        now = time.time()
        allowed, remaining, retry_after = await self._script(
            keys=[f"rl:{identity}"], args=[now, _WINDOW_SECONDS, limit]
        )
        return RateLimitResult(bool(allowed), int(remaining), int(retry_after))

    async def close(self) -> None:
        await self._redis.aclose()


def build_rate_limiter() -> MemoryRateLimiter | RedisRateLimiter:
    if settings.rate_limit_backend == "redis":
        return RedisRateLimiter(settings.redis_url)
    return MemoryRateLimiter()
