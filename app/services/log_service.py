"""Privacy-aware request logging + analytics aggregations."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import Date, case, cast, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import ApiKey, RequestLog, UsageDaily
from app.schemas.logs import Stats


def _redact(text: str | None) -> str | None:
    """Apply privacy policy: drop or truncate bodies per settings."""
    if text is None:
        return None
    if not settings.log_prompt_bodies:
        return None
    if len(text) > settings.log_body_max_chars:
        return text[: settings.log_body_max_chars] + "…[truncated]"
    return text


async def write_log(db: AsyncSession, **fields: object) -> None:
    fields["prompt"] = _redact(fields.get("prompt"))  # type: ignore[arg-type]
    fields["response"] = _redact(fields.get("response"))  # type: ignore[arg-type]
    db.add(RequestLog(**fields))
    await db.commit()
    # Roll the request into the per-key daily usage table (best-effort: a usage
    # bookkeeping failure must never break the actual API response).
    try:
        await _bump_usage_daily(db, fields)
    except Exception:
        await db.rollback()


async def _bump_usage_daily(db: AsyncSession, fields: dict[str, object]) -> None:
    """Increment today's (api_key_id, date) rollup by this request's tokens."""
    kid = fields.get("api_key_id")
    if kid is None:
        return  # anonymous / unauthenticated request — nothing to attribute
    kid = int(kid)  # type: ignore[arg-type]
    day = datetime.now(timezone.utc).date()
    pt = int(fields.get("prompt_tokens") or 0)  # type: ignore[arg-type]
    ct = int(fields.get("completion_tokens") or 0)  # type: ignore[arg-type]
    tt = int(fields.get("total_tokens") or 0) or (pt + ct)  # type: ignore[arg-type]

    row = await db.scalar(
        select(UsageDaily).where(
            UsageDaily.api_key_id == kid, UsageDaily.usage_date == day
        )
    )
    if row is None:
        db.add(
            UsageDaily(
                api_key_id=kid,
                key_prefix=fields.get("key_prefix"),
                usage_date=day,
                request_count=1,
                prompt_tokens=pt,
                completion_tokens=ct,
                total_tokens=tt,
            )
        )
        try:
            await db.commit()
            return
        except IntegrityError:
            # Concurrent insert won the race — fall through to increment path.
            await db.rollback()
            row = await db.scalar(
                select(UsageDaily).where(
                    UsageDaily.api_key_id == kid, UsageDaily.usage_date == day
                )
            )
    if row is not None:
        row.request_count += 1
        row.prompt_tokens += pt
        row.completion_tokens += ct
        row.total_tokens += tt
        await db.commit()


async def list_logs(
    db: AsyncSession,
    *,
    page: int,
    page_size: int,
    model: str | None = None,
    key_prefix: str | None = None,
) -> tuple[list[RequestLog], int]:
    stmt = select(RequestLog)
    count_stmt = select(func.count()).select_from(RequestLog)
    if model:
        stmt = stmt.where(RequestLog.model == model)
        count_stmt = count_stmt.where(RequestLog.model == model)
    if key_prefix:
        stmt = stmt.where(RequestLog.key_prefix == key_prefix)
        count_stmt = count_stmt.where(RequestLog.key_prefix == key_prefix)

    total = int(await db.scalar(count_stmt) or 0)
    stmt = (
        stmt.order_by(RequestLog.created_date.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = list((await db.execute(stmt)).scalars().all())
    return rows, total


async def compute_stats(db: AsyncSession, *, total_keys: int, active_keys: int) -> Stats:
    today = datetime.now(timezone.utc) - timedelta(days=1)
    total_requests = int(
        await db.scalar(select(func.count()).select_from(RequestLog)) or 0
    )
    today_requests = int(
        await db.scalar(
            select(func.count())
            .select_from(RequestLog)
            .where(RequestLog.created_date >= today)
        )
        or 0
    )
    error_count = int(
        await db.scalar(
            select(func.count())
            .select_from(RequestLog)
            .where(RequestLog.status_code >= 400)
        )
        or 0
    )
    avg_rt = float(
        await db.scalar(select(func.avg(RequestLog.response_time_ms))) or 0.0
    )
    total_tokens = int(
        await db.scalar(select(func.coalesce(func.sum(UsageDaily.total_tokens), 0)))
        or 0
    )
    tokens_today = int(
        await db.scalar(
            select(func.coalesce(func.sum(UsageDaily.total_tokens), 0)).where(
                UsageDaily.usage_date == datetime.now(timezone.utc).date()
            )
        )
        or 0
    )
    return Stats(
        total_api_keys=total_keys,
        active_api_keys=active_keys,
        total_requests=total_requests,
        today_requests=today_requests,
        error_count=error_count,
        avg_response_time_ms=round(avg_rt, 2),
        total_tokens=total_tokens,
        tokens_today=tokens_today,
    )


# --- Analytics aggregations powering the dashboard charts ---
async def requests_per_day(db: AsyncSession, days: int = 14) -> list[dict[str, object]]:
    """Daily request counts for the last `days`, gap-filled with zeros."""
    since = datetime.now(timezone.utc) - timedelta(days=days - 1)
    day_col = cast(RequestLog.created_date, Date)
    rows = (
        await db.execute(
            select(day_col.label("d"), func.count().label("c"))
            .where(RequestLog.created_date >= since)
            .group_by(day_col)
        )
    ).all()
    counts: dict[date, int] = {}
    for d, c in rows:
        key = d if isinstance(d, date) else datetime.fromisoformat(str(d)).date()
        counts[key] = int(c)
    today = datetime.now(timezone.utc).date()
    out: list[dict[str, object]] = []
    for i in range(days - 1, -1, -1):
        d = today - timedelta(days=i)
        out.append({"label": d.strftime("%m/%d"), "count": counts.get(d, 0)})
    return out


async def requests_count_by_key(db: AsyncSession) -> dict[int, dict[str, object]]:
    """Per-key usage: total requests + error count, keyed by api_key_id."""
    rows = (
        await db.execute(
            select(
                RequestLog.api_key_id,
                func.count().label("total"),
                func.sum(
                    case((RequestLog.status_code >= 400, 1), else_=0)
                ).label("errors"),
            ).group_by(RequestLog.api_key_id)
        )
    ).all()
    out: dict[int, dict[str, object]] = {}
    for kid, total, errors in rows:
        if kid is not None:
            out[int(kid)] = {"total": int(total), "errors": int(errors or 0)}
    return out


async def requests_per_model(db: AsyncSession) -> list[dict[str, object]]:
    rows = (
        await db.execute(
            select(RequestLog.model, func.count().label("c"))
            .group_by(RequestLog.model)
            .order_by(func.count().desc())
        )
    ).all()
    return [{"name": m or "unknown", "count": int(c)} for m, c in rows]


async def backfill_usage_daily(db: AsyncSession) -> int:
    """One-time seed: rebuild usage_daily from request_logs if it's empty.

    Lets the token report show historical data logged before this table existed.
    No-op once usage_daily has any rows. Returns the number of day/key rows added.
    """
    existing = int(await db.scalar(select(func.count()).select_from(UsageDaily)) or 0)
    if existing:
        return 0
    day_col = cast(RequestLog.created_date, Date)
    rows = (
        await db.execute(
            select(
                RequestLog.api_key_id,
                func.max(RequestLog.key_prefix).label("prefix"),
                day_col.label("d"),
                func.count().label("reqs"),
                func.coalesce(func.sum(RequestLog.prompt_tokens), 0).label("pt"),
                func.coalesce(func.sum(RequestLog.completion_tokens), 0).label("ct"),
                func.coalesce(func.sum(RequestLog.total_tokens), 0).label("tt"),
            )
            .where(RequestLog.api_key_id.isnot(None))
            .group_by(RequestLog.api_key_id, day_col)
        )
    ).all()
    added = 0
    for kid, prefix, d, reqs, pt, ct, tt in rows:
        day = d if isinstance(d, date) else datetime.fromisoformat(str(d)).date()
        db.add(
            UsageDaily(
                api_key_id=int(kid), key_prefix=prefix, usage_date=day,
                request_count=int(reqs or 0), prompt_tokens=int(pt or 0),
                completion_tokens=int(ct or 0), total_tokens=int(tt or 0),
            )
        )
        added += 1
    if added:
        await db.commit()
    return added


# --- Token-usage aggregations (per-API-key daily rollup) ---
async def usage_daily_report(
    db: AsyncSession, *, key_prefix: str | None = None, days: int = 30
) -> list[dict[str, object]]:
    """Day-to-day token report over the last `days`, gap-filled with zeros.

    If `key_prefix` is given, restrict to that API key; otherwise sum all keys.
    """
    since = datetime.now(timezone.utc).date() - timedelta(days=days - 1)
    stmt = (
        select(
            UsageDaily.usage_date.label("d"),
            func.sum(UsageDaily.request_count).label("reqs"),
            func.sum(UsageDaily.prompt_tokens).label("pt"),
            func.sum(UsageDaily.completion_tokens).label("ct"),
            func.sum(UsageDaily.total_tokens).label("tt"),
        )
        .where(UsageDaily.usage_date >= since)
        .group_by(UsageDaily.usage_date)
    )
    if key_prefix:
        stmt = stmt.where(UsageDaily.key_prefix == key_prefix)
    rows = (await db.execute(stmt)).all()
    by_day: dict[date, dict[str, int]] = {}
    for d, reqs, pt, ct, tt in rows:
        key = d if isinstance(d, date) else datetime.fromisoformat(str(d)).date()
        by_day[key] = {
            "requests": int(reqs or 0),
            "prompt_tokens": int(pt or 0),
            "completion_tokens": int(ct or 0),
            "total_tokens": int(tt or 0),
        }
    today = datetime.now(timezone.utc).date()
    out: list[dict[str, object]] = []
    for i in range(days - 1, -1, -1):
        d = today - timedelta(days=i)
        v = by_day.get(d, {"requests": 0, "prompt_tokens": 0,
                           "completion_tokens": 0, "total_tokens": 0})
        out.append({"date": d.isoformat(), "label": d.strftime("%m/%d"), **v})
    return out


async def usage_by_key(
    db: AsyncSession, *, days: int = 30
) -> list[dict[str, object]]:
    """Per-API-key token totals over the last `days`, biggest spenders first."""
    since = datetime.now(timezone.utc).date() - timedelta(days=days - 1)
    rows = (
        await db.execute(
            select(
                UsageDaily.api_key_id,
                UsageDaily.key_prefix,
                ApiKey.owner_name,
                func.sum(UsageDaily.request_count).label("reqs"),
                func.sum(UsageDaily.prompt_tokens).label("pt"),
                func.sum(UsageDaily.completion_tokens).label("ct"),
                func.sum(UsageDaily.total_tokens).label("tt"),
            )
            .join(ApiKey, ApiKey.id == UsageDaily.api_key_id, isouter=True)
            .where(UsageDaily.usage_date >= since)
            .group_by(UsageDaily.api_key_id, UsageDaily.key_prefix, ApiKey.owner_name)
            .order_by(func.sum(UsageDaily.total_tokens).desc())
        )
    ).all()
    return [
        {
            "api_key_id": int(kid),
            "key_prefix": prefix,
            "owner_name": owner or "—",
            "requests": int(reqs or 0),
            "prompt_tokens": int(pt or 0),
            "completion_tokens": int(ct or 0),
            "total_tokens": int(tt or 0),
        }
        for kid, prefix, owner, reqs, pt, ct, tt in rows
    ]


async def status_breakdown(db: AsyncSession) -> dict[str, int]:
    ok = int(
        await db.scalar(
            select(func.count()).select_from(RequestLog).where(RequestLog.status_code < 400)
        )
        or 0
    )
    client_err = int(
        await db.scalar(
            select(func.count())
            .select_from(RequestLog)
            .where(RequestLog.status_code >= 400, RequestLog.status_code < 500)
        )
        or 0
    )
    server_err = int(
        await db.scalar(
            select(func.count()).select_from(RequestLog).where(RequestLog.status_code >= 500)
        )
        or 0
    )
    return {"ok": ok, "client_error": client_err, "server_error": server_err}
