"""Application entrypoint: lifespan, middleware, routers, error handlers.

Run dev:  uvicorn app.main:app --reload
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from app.admin_ui.router import router as admin_ui_router
from app.api.routers import admin, chat, health, models, rag
from app.web.router import router as web_router
from app.core.config import settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.db.base import engine, init_db
from app.middleware.context import CorrelationMiddleware, SecurityHeadersMiddleware
from app.services.openai_service import build_llm_service
from app.services.ratelimit import build_rate_limiter

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    await init_db()
    app.state.ollama = build_llm_service()
    app.state.rate_limiter = build_rate_limiter()
    from app.services.rag_service import RagService
    app.state.rag = RagService(app.state.ollama)
    logger.info("Startup complete: %s (%s)", settings.app_name, settings.environment)
    try:
        yield
    finally:
        await app.state.ollama.close()
        await app.state.rate_limiter.close()
        await engine.dispose()
        logger.info("Shutdown complete")


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="Self-hosted REST + SSE gateway in front of a local Ollama server.",
        lifespan=lifespan,
    )

    # Middleware order: security headers outermost, then correlation, then CORS.
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(CorrelationMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_exception_handlers(app)

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        # Landing: admin dashboard, which shows the login form when unauthenticated.
        return RedirectResponse(url="/admin/ui")

    app.include_router(health.router)
    app.include_router(chat.router)
    app.include_router(models.router)
    app.include_router(rag.router)
    app.include_router(admin.router)
    app.include_router(admin_ui_router)
    app.include_router(web_router)
    return app


app = create_app()
