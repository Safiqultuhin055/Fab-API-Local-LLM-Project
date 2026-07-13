"""Server-rendered (Jinja) admin dashboard.

Optional UI from the diagram. Login sets an httpOnly cookie holding an HMAC token
(never the raw admin key). Pages render server-side from the same services the
JSON admin API uses.
"""
from __future__ import annotations

import hmac
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from app.api.deps import DbSession, OllamaDep
from app.core import security
from app.core.config import settings
from app.services import key_service, log_service, settings_service, user_service

router = APIRouter(prefix="/admin/ui", tags=["admin-ui"], include_in_schema=False)

_templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
_COOKIE = "admin_auth"


def _session_token(username: str) -> str:
    # Stateless cookie: "<username>:<mac>" where mac is HMAC(username) under the
    # server secret. Password is NOT embedded, so a password change never breaks
    # an existing session and the cookie can't be forged without HMAC_SECRET.
    mac = security.hash_api_key(f"admin-ui:session:{username}")
    return f"{username}:{mac}"


def _is_authed(request: Request) -> bool:
    tok = request.cookies.get(_COOKIE) or ""
    username, _, mac = tok.partition(":")
    if not username or not mac:
        return False
    expected = security.hash_api_key(f"admin-ui:session:{username}")
    return hmac.compare_digest(mac, expected)


def _login_redirect() -> RedirectResponse:
    return RedirectResponse(url="/admin/ui/login", status_code=303)


def _env_credentials_ok(username: str, password: str) -> bool:
    # Bootstrap / recovery fallback: the .env admin still works even if the users
    # table is empty or the DB is unreachable, so the console is never locked out.
    u_ok = hmac.compare_digest(username, settings.admin_username)
    p_ok = hmac.compare_digest(password, settings.admin_password)
    return u_ok and p_ok


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request) -> Response:
    return _templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login")
async def login(
    request: Request,
    db: DbSession,
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
) -> Response:
    # Primary path: authenticate against the users table.
    ok = False
    try:
        ok = await user_service.authenticate(db, username, password) is not None
    except Exception:
        ok = False  # DB down -> fall through to env recovery
    # Recovery path: .env admin credentials always work.
    if not ok:
        ok = _env_credentials_ok(username, password)

    if not ok:
        return _templates.TemplateResponse(
            request, "login.html",
            {"error": "Invalid username or password"}, status_code=401,
        )
    resp = RedirectResponse(url="/admin/ui", status_code=303)
    resp.set_cookie(
        _COOKIE, _session_token(username), httponly=True, samesite="lax",
        secure=settings.is_production, max_age=60 * 60 * 8,
    )
    return resp


@router.get("/logout")
async def logout() -> Response:
    resp = _login_redirect()
    resp.delete_cookie(_COOKIE)
    return resp


def _trend_svg(daily: list[dict], width: int = 760, height: int = 180) -> dict:
    """Build SVG line + area geometry for the daily-requests trend chart."""
    pad = 28
    counts = [int(d["count"]) for d in daily] or [0]
    n = len(counts)
    top = max(counts) or 1
    iw, ih = width - pad * 2, height - pad * 2
    step = iw / max(n - 1, 1)
    pts = []
    for i, c in enumerate(counts):
        x = pad + i * step
        y = pad + ih - (c / top) * ih
        pts.append((round(x, 1), round(y, 1)))
    line = " ".join(f"{x},{y}" for x, y in pts)
    area = (
        f"{pad},{pad + ih} " + line + f" {pad + (n - 1) * step},{pad + ih}"
    )
    # gridline y positions (4 bands)
    grid = [round(pad + ih - (k / 4) * ih, 1) for k in range(5)]
    labels = [
        {"x": round(pad + i * step, 1), "text": d["label"]}
        for i, d in enumerate(daily)
        if n <= 14 or i % 2 == 0
    ]
    return {
        "width": width, "height": height, "line": line, "area": area,
        "dots": pts, "grid": grid, "labels": labels, "top": top,
        "baseline": round(pad + ih, 1),
    }


@router.get("", response_class=HTMLResponse)
async def dashboard(request: Request, db: DbSession, ollama: OllamaDep) -> Response:
    if not _is_authed(request):
        return _login_redirect()

    # Live system health for the status strip.
    from sqlalchemy import text as _text
    health = {"database": False, "ollama": False, "redis": None}
    try:
        await db.execute(_text("SELECT 1"))
        health["database"] = True
    except Exception:
        health["database"] = False
    try:
        health["ollama"] = await ollama.ping()
    except Exception:
        health["ollama"] = False
    if settings.rate_limit_backend == "redis":
        health["redis"] = True  # backend configured; deep ping omitted for speed

    total_keys, active_keys = await key_service.count_keys(db)
    stats = await log_service.compute_stats(
        db, total_keys=total_keys, active_keys=active_keys
    )
    recent, _ = await log_service.list_logs(db, page=1, page_size=8)
    daily = await log_service.requests_per_day(db, days=14)
    by_model = await log_service.requests_per_model(db)
    status = await log_service.status_breakdown(db)

    model_total = sum(m["count"] for m in by_model) or 1
    model_palette = ["#2563eb", "#7c3aed", "#0891b2", "#16a34a", "#ea580c", "#db2777"]
    models = [
        {**m, "pct": round(m["count"] / model_total * 100, 1),
         "color": model_palette[i % len(model_palette)]}
        for i, m in enumerate(by_model)
    ]
    status_total = sum(status.values()) or 1
    status_pct = {k: round(v / status_total * 100, 1) for k, v in status.items()}

    return _templates.TemplateResponse(
        request, "dashboard.html",
        {
            "stats": stats, "recent": recent, "active": "dashboard",
            "trend": _trend_svg(daily), "models": models,
            "status": status, "status_pct": status_pct,
            "health": health, "admin": settings.admin_username,
        },
    )


@router.get("/keys", response_class=HTMLResponse)
async def keys_page(request: Request, db: DbSession) -> Response:
    if not _is_authed(request):
        return _login_redirect()
    rows = await key_service.list_keys(db)
    usage = await log_service.requests_count_by_key(db)
    # A freshly created raw key is passed once via query string then discarded.
    new_key = request.query_params.get("new_key")
    keys = []
    for k in rows:
        u = usage.get(k.id, {"total": 0, "errors": 0})
        keys.append({
            "row": k, "requests": u["total"], "errors": u["errors"],
            "full_key": security.decrypt_secret(k.key_encrypted),
        })
    summary = {
        "total": len(rows),
        "active": sum(1 for k in rows if k.status == "active"),
        "paid": sum(1 for k in rows if k.tier in ("pro", "enterprise")),
        "requests": sum(u["total"] for u in usage.values()),
    }
    return _templates.TemplateResponse(
        request, "keys.html",
        {"keys": keys, "summary": summary, "new_key": new_key, "active": "keys",
         "admin": settings.admin_username},
    )


@router.post("/keys")
async def create_key(
    request: Request,
    db: DbSession,
    owner_name: Annotated[str, Form()],
    tier: Annotated[str, Form()] = "free",
    rate_limit: Annotated[int, Form()] = 1000,
    ip_whitelist: Annotated[str, Form()] = "",
) -> Response:
    if not _is_authed(request):
        return _login_redirect()
    _, full_key = await key_service.create_key(
        db, owner_name=owner_name, tier=tier, rate_limit=rate_limit,
        expires_at=None, ip_whitelist=ip_whitelist or None,
    )
    return RedirectResponse(url=f"/admin/ui/keys?new_key={full_key}", status_code=303)


@router.post("/keys/{key_id}/delete")
async def delete_key(request: Request, key_id: int, db: DbSession) -> Response:
    if not _is_authed(request):
        return _login_redirect()
    await key_service.delete_key(db, key_id)
    return RedirectResponse(url="/admin/ui/keys", status_code=303)


@router.get("/logs", response_class=HTMLResponse)
async def logs_page(
    request: Request, db: DbSession, page: int = 1
) -> Response:
    if not _is_authed(request):
        return _login_redirect()
    page = max(1, page)
    rows, total = await log_service.list_logs(db, page=page, page_size=25)
    return _templates.TemplateResponse(
        request, "logs.html",
        {"logs": rows, "page": page, "total": total, "page_size": 25,
         "active": "logs", "admin": settings.admin_username},
    )


@router.get("/models", response_class=HTMLResponse)
async def models_page(request: Request, db: DbSession, ollama: OllamaDep) -> Response:
    if not _is_authed(request):
        return _login_redirect()
    try:
        installed = await ollama.list_models()
    except Exception:
        installed = []
    enabled = await settings_service.get_enabled_models(db)
    default = await settings_service.get_default_model(db)
    models = [
        {"name": m.get("name"), "enabled": enabled is None or m.get("name") in enabled}
        for m in installed
    ]
    return _templates.TemplateResponse(
        request, "models.html",
        {"models": models, "default": default, "active": "models",
         "admin": settings.admin_username},
    )


@router.post("/models/{name}/toggle")
async def toggle_model(
    request: Request, name: str, db: DbSession, enable: Annotated[str, Form()] = "1"
) -> Response:
    if not _is_authed(request):
        return _login_redirect()
    if enable == "1":
        await settings_service.enable_model(db, name)
    else:
        await settings_service.disable_model(db, name)
    return RedirectResponse(url="/admin/ui/models", status_code=303)


@router.post("/models/default")
async def set_default(
    request: Request, db: DbSession, name: Annotated[str, Form()]
) -> Response:
    if not _is_authed(request):
        return _login_redirect()
    await settings_service.set_default_model(db, name)
    return RedirectResponse(url="/admin/ui/models", status_code=303)
