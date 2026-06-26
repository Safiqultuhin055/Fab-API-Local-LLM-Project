"""Admin endpoints (static admin-key auth): API-key CRUD, logs, stats."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import AdminAuth, DbSession, OllamaDep
from app.core.errors import NotFoundError
from app.schemas.common import Page
from app.schemas.keys import ApiKeyCreate, ApiKeyCreated, ApiKeyOut
from app.schemas.logs import RequestLogOut, Stats
from app.services import key_service, log_service, settings_service

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[])


@router.post("/api-keys", response_model=ApiKeyCreated, status_code=201)
async def create_api_key(
    body: ApiKeyCreate, db: DbSession, _: AdminAuth
) -> ApiKeyCreated:
    row, full_key = await key_service.create_key(
        db,
        owner_name=body.owner_name,
        tier=body.tier,
        rate_limit=body.rate_limit,
        expires_at=body.expires_at,
        ip_whitelist=body.ip_whitelist,
    )
    return ApiKeyCreated(
        id=row.id,
        api_key=full_key,  # shown ONCE
        key_prefix=row.key_prefix,
        owner_name=row.owner_name,
        tier=row.tier,
        rate_limit=row.rate_limit,
        created_date=row.created_date,
    )


@router.get("/api-keys", response_model=list[ApiKeyOut])
async def list_api_keys(db: DbSession, _: AdminAuth) -> list[ApiKeyOut]:
    rows = await key_service.list_keys(db)
    return [ApiKeyOut.model_validate(r) for r in rows]


@router.delete("/api-keys/{key_id}", status_code=200)
async def delete_api_key(key_id: int, db: DbSession, _: AdminAuth) -> dict[str, object]:
    ok = await key_service.delete_key(db, key_id)
    if not ok:
        raise NotFoundError(f"API key {key_id} not found")
    return {"success": True, "deleted": key_id}


@router.get("/logs", response_model=Page[RequestLogOut])
async def list_logs(
    db: DbSession,
    _: AdminAuth,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
    model: str | None = None,
    key_prefix: str | None = None,
) -> Page[RequestLogOut]:
    rows, total = await log_service.list_logs(
        db, page=page, page_size=page_size, model=model, key_prefix=key_prefix
    )
    return Page[RequestLogOut](
        items=[RequestLogOut.model_validate(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/stats", response_model=Stats)
async def stats(db: DbSession, _: AdminAuth) -> Stats:
    total_keys, active_keys = await key_service.count_keys(db)
    return await log_service.compute_stats(
        db, total_keys=total_keys, active_keys=active_keys
    )


# --- Model management (enable/disable + default selection) ---
@router.get("/models")
async def admin_models(
    db: DbSession, ollama: OllamaDep, _: AdminAuth
) -> dict[str, object]:
    installed = await ollama.list_models()
    enabled = await settings_service.get_enabled_models(db)  # None => all allowed
    default = await settings_service.get_default_model(db)
    return {
        "success": True,
        "default_model": default,
        "restriction_active": enabled is not None,
        "models": [
            {
                "name": m.get("name"),
                "enabled": enabled is None or m.get("name") in enabled,
            }
            for m in installed
        ],
    }


@router.post("/models/{name}/enable")
async def enable_model(name: str, db: DbSession, _: AdminAuth) -> dict[str, object]:
    await settings_service.enable_model(db, name)
    return {"success": True, "model": name, "enabled": True}


@router.post("/models/{name}/disable")
async def disable_model(name: str, db: DbSession, _: AdminAuth) -> dict[str, object]:
    await settings_service.disable_model(db, name)
    return {"success": True, "model": name, "enabled": False}


@router.put("/models/default/{name}")
async def set_default_model(name: str, db: DbSession, _: AdminAuth) -> dict[str, object]:
    await settings_service.set_default_model(db, name)
    return {"success": True, "default_model": name}
