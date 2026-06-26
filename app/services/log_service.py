"""Privacy-aware request logging + analytics aggregations."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import Date, case, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import RequestLog
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
    return Stats(
        total_api_keys=total_keys,
        active_api_keys=active_keys,
        total_requests=total_requests,
        today_requests=today_requests,
        error_count=error_count,
        avg_response_time_ms=round(avg_rt, 2),
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
