"""Public chat endpoints: completion + SSE streaming. API-key auth + rate limited.

Each call persists a privacy-aware request_log (model, prompt, response, tokens,
latency, ip, user-agent) via log_service, which applies the retention/redaction policy.
"""
from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.api.deps import ApiKeyAuth, DbSession, OllamaDep
from app.core.errors import APIError, ForbiddenError
from app.core.history import format_history
from app.core.logging import request_id_ctx
from app.schemas.chat import ChatRequest, ChatResponse
from app.services import log_service, settings_service

router = APIRouter(prefix="/v1", tags=["chat"])


def _client_meta(request: Request) -> tuple[str | None, str | None]:
    ip = request.client.host if request.client else None
    return ip, request.headers.get("user-agent")


def _with_history(body: ChatRequest) -> str:
    """Prepend prior turns to the prompt so the model remembers context."""
    hist = format_history(body.history)
    if not hist:
        return body.prompt
    return f"{hist}\nUser: {body.prompt}\nAssistant:"


@router.post("/chat", response_model=ChatResponse)
async def chat(
    body: ChatRequest,
    key: ApiKeyAuth,
    ollama: OllamaDep,
    db: DbSession,
    request: Request,
) -> ChatResponse:
    started = time.perf_counter()
    ip, ua = _client_meta(request)
    if not await settings_service.is_model_allowed(db, body.model):
        raise ForbiddenError(f"Model '{body.model}' is disabled")
    try:
        result = await ollama.complete(
            model=body.model,
            prompt=_with_history(body),
            temperature=body.temperature,
            max_tokens=body.max_tokens,
            images=body.images,
        )
    except APIError as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        await log_service.write_log(
            db,
            api_key_id=key.id,
            key_prefix=key.key_prefix,
            model=body.model,
            endpoint="/api/v1/chat",
            prompt=body.prompt,
            response=None,
            status_code=exc.status_code,
            response_time_ms=elapsed_ms,
            ip_address=ip,
            user_agent=ua,
            request_id=request_id_ctx.get(),
            error=exc.message,
        )
        raise

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    await log_service.write_log(
        db,
        api_key_id=key.id,
        key_prefix=key.key_prefix,
        model=result.model,
        endpoint="/api/v1/chat",
        prompt=body.prompt,
        response=result.text,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        total_tokens=result.total_tokens,
        status_code=200,
        response_time_ms=elapsed_ms,
        ip_address=ip,
        user_agent=ua,
        request_id=request_id_ctx.get(),
    )
    return ChatResponse(
        response=result.text,
        model=result.model,
        tokens=result.total_tokens,
        response_time_ms=elapsed_ms,
        request_id=request_id_ctx.get(),
    )


@router.post("/chat/stream")
async def chat_stream(
    body: ChatRequest,
    key: ApiKeyAuth,
    ollama: OllamaDep,
    db: DbSession,
    request: Request,
) -> StreamingResponse:
    started = time.perf_counter()
    ip, ua = _client_meta(request)
    req_id = request_id_ctx.get()
    if not await settings_service.is_model_allowed(db, body.model):
        raise ForbiddenError(f"Model '{body.model}' is disabled")

    async def event_source() -> AsyncIterator[str]:
        collected: list[str] = []
        status_code = 200
        error: str | None = None
        try:
            async for token, done in ollama.stream(
                model=body.model,
                prompt=_with_history(body),
                temperature=body.temperature,
                max_tokens=body.max_tokens,
                images=body.images,
            ):
                collected.append(token)
                yield f"data: {json.dumps({'token': token, 'done': done})}\n\n"
                if done:
                    yield "data: [DONE]\n\n"
        except APIError as exc:
            status_code, error = exc.status_code, exc.message
            yield f"data: {json.dumps({'error': exc.message})}\n\n"
        finally:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            await log_service.write_log(
                db,
                api_key_id=key.id,
                key_prefix=key.key_prefix,
                model=body.model,
                endpoint="/api/v1/chat/stream",
                prompt=body.prompt,
                response="".join(collected) or None,
                status_code=status_code,
                response_time_ms=elapsed_ms,
                ip_address=ip,
                user_agent=ua,
                request_id=req_id,
                error=error,
            )

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
