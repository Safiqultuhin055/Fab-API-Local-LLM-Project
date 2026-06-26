"""Liveness vs readiness. /health = no auth, no deps. /ready = checks deps."""
from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text

from app.api.deps import DbSession, OllamaDep
from app.core.config import settings

router = APIRouter(prefix="/v1", tags=["health"])


@router.get("/health")
async def health() -> dict[str, object]:
    return {"success": True, "status": "ok", "app": settings.app_name}


@router.get("/ready")
async def ready(db: DbSession, ollama: OllamaDep) -> dict[str, object]:
    checks: dict[str, bool] = {}
    try:
        await db.execute(text("SELECT 1"))
        checks["database"] = True
    except Exception:
        checks["database"] = False
    checks["ollama"] = await ollama.ping()
    ready_ok = all(checks.values())
    return {"success": ready_ok, "status": "ready" if ready_ok else "degraded", "checks": checks}
