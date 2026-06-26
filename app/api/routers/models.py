"""Model listing/status endpoints (API-key auth). Reflects admin enable/disable."""
from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import ApiKeyAuth, DbSession, OllamaDep
from app.services import settings_service

router = APIRouter(prefix="/v1", tags=["models"])


@router.get("/models")
async def list_models(
    key: ApiKeyAuth, ollama: OllamaDep, db: DbSession
) -> dict[str, object]:
    installed = await ollama.list_models()
    enabled = await settings_service.get_enabled_models(db)  # None => all allowed
    default = await settings_service.get_default_model(db)
    return {
        "success": True,
        "default_model": default,
        "models": [
            {
                "name": m.get("name"),
                "size": m.get("size"),
                "enabled": enabled is None or m.get("name") in enabled,
            }
            for m in installed
        ],
    }


@router.get("/models/status")
async def models_status(
    key: ApiKeyAuth, ollama: OllamaDep, db: DbSession
) -> dict[str, object]:
    up = await ollama.ping()
    installed = await ollama.list_models() if up else []
    return {
        "success": True,
        "ollama_reachable": up,
        "installed_count": len(installed),
        "installed": [m.get("name") for m in installed],
        "default_model": await settings_service.get_default_model(db),
    }
