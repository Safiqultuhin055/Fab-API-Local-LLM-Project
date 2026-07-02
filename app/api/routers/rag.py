"""RAG endpoints: ask questions answered from the local knowledge base."""
from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.deps import AdminAuth, ApiKeyAuth, DbSession, RagDep
from app.core.config import settings
from app.core.errors import APIError
from app.core.history import format_history
from app.core.logging import request_id_ctx
from app.schemas.chat import ChatMessage
from app.services import log_service

router = APIRouter(prefix="/v1/rag", tags=["rag"])


class RagRequest(BaseModel):
    prompt: str = Field(..., min_length=1, examples=["What databases does he know?"])
    top_k: int | None = Field(None, ge=1, le=12)
    history: list[ChatMessage] | None = Field(
        None, description="Prior turns (oldest first) so the bot remembers context"
    )


@router.get("/status")
async def rag_status(key: ApiKeyAuth, rag: RagDep) -> dict[str, object]:
    return {"success": True, "ready": rag.ready, "chunks": rag.chunk_count}


@router.post("/reindex")
async def rag_reindex(_: AdminAuth, rag: RagDep) -> dict[str, object]:
    count = await rag.reindex()
    return {"success": True, "chunks": count}


_ALLOWED_EXT = {".txt", ".md", ".markdown", ".csv", ".json", ".log"}


@router.post("/documents")
async def add_document(
    key: ApiKeyAuth, rag: RagDep, file: UploadFile = File(...)
) -> dict[str, object]:
    """Upload a text/markdown document into the knowledge base, then reindex."""
    name = file.filename or "upload.txt"
    ext = Path(name).suffix.lower()
    if ext not in _ALLOWED_EXT:
        raise APIError(
            f"Unsupported file type '{ext}'. Allowed: {', '.join(sorted(_ALLOWED_EXT))}"
        )
    raw = await file.read()
    if len(raw) > 2_000_000:
        raise APIError("File too large (max 2 MB).")
    text = raw.decode("utf-8-sig", errors="ignore").strip()
    if not text:
        raise APIError("File is empty or not readable as text.")

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    block = f"\n\n## Uploaded document: {name}\n_added {ts}_\n\n{text}\n"
    kb = Path(settings.rag_kb_path)
    with kb.open("a", encoding="utf-8") as fh:
        fh.write(block)
    chunks = await rag.reindex()
    return {"success": True, "filename": name, "chars": len(text), "chunks": chunks}


@router.post("")
async def rag_answer(
    body: RagRequest, key: ApiKeyAuth, rag: RagDep, db: DbSession, request: Request
) -> dict[str, object]:
    started = time.perf_counter()
    out = await rag.answer(body.prompt, body.top_k)
    elapsed = int((time.perf_counter() - started) * 1000)
    await log_service.write_log(
        db, api_key_id=key.id, key_prefix=key.key_prefix, model="rag",
        endpoint="/v1/rag", prompt=body.prompt, response=out["answer"],
        total_tokens=out.get("tokens"), status_code=200, response_time_ms=elapsed,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"), request_id=request_id_ctx.get(),
    )
    return {"success": True, "response": out["answer"], "sources": out["sources"],
            "response_time_ms": elapsed, "request_id": request_id_ctx.get()}


@router.post("/smart")
async def rag_smart(
    body: RagRequest, key: ApiKeyAuth, rag: RagDep, db: DbSession, request: Request
) -> dict[str, object]:
    """RAG-first: answer from the knowledge base if relevant, else from the model."""
    started = time.perf_counter()
    out = await rag.smart_answer(body.prompt, body.top_k, format_history(body.history))
    elapsed = int((time.perf_counter() - started) * 1000)
    await log_service.write_log(
        db, api_key_id=key.id, key_prefix=key.key_prefix,
        model=f"smart:{out['mode']}", endpoint="/v1/rag/smart",
        prompt=body.prompt, response=out["answer"], total_tokens=out.get("tokens"),
        status_code=200, response_time_ms=elapsed,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"), request_id=request_id_ctx.get(),
    )
    return {"success": True, "response": out["answer"], "mode": out["mode"],
            "sources": out["sources"], "top_score": out["top_score"],
            "response_time_ms": elapsed, "request_id": request_id_ctx.get()}


@router.post("/smart/stream")
async def rag_smart_stream(
    body: RagRequest, key: ApiKeyAuth, rag: RagDep, db: DbSession, request: Request
) -> StreamingResponse:
    started = time.perf_counter()
    req_id = request_id_ctx.get()
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent")

    async def gen() -> AsyncIterator[str]:
        collected: list[str] = []
        mode = "model"
        sent_meta = False
        try:
            async for token, done, meta in rag.smart_stream(
                body.prompt, body.top_k, format_history(body.history)
            ):
                if not sent_meta:
                    mode = meta["mode"]
                    yield f"data: {json.dumps({'meta': meta})}\n\n"
                    sent_meta = True
                if token:
                    collected.append(token)
                    yield f"data: {json.dumps({'token': token, 'done': done})}\n\n"
                if done:
                    yield "data: [DONE]\n\n"
        except Exception as exc:  # noqa: BLE001
            yield f"data: {json.dumps({'error': str(exc)})}\n\n"
        finally:
            await log_service.write_log(
                db, api_key_id=key.id, key_prefix=key.key_prefix,
                model=f"smart:{mode}", endpoint="/v1/rag/smart/stream",
                prompt=body.prompt, response="".join(collected) or None,
                status_code=200, response_time_ms=int((time.perf_counter() - started) * 1000),
                ip_address=ip, user_agent=ua, request_id=req_id,
            )

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/stream")
async def rag_stream(
    body: RagRequest, key: ApiKeyAuth, rag: RagDep, db: DbSession, request: Request
) -> StreamingResponse:
    started = time.perf_counter()
    req_id = request_id_ctx.get()
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent")

    async def gen() -> AsyncIterator[str]:
        collected: list[str] = []
        sent_sources = False
        try:
            async for token, done, hits in rag.stream(body.prompt, body.top_k):
                if not sent_sources:
                    srcs = [{"section": h["section"], "score": round(h["score"], 4)} for h in hits]
                    yield f"data: {json.dumps({'sources': srcs})}\n\n"
                    sent_sources = True
                if token:
                    collected.append(token)
                    yield f"data: {json.dumps({'token': token, 'done': done})}\n\n"
                if done:
                    yield "data: [DONE]\n\n"
        except Exception as exc:  # noqa: BLE001
            yield f"data: {json.dumps({'error': str(exc)})}\n\n"
        finally:
            await log_service.write_log(
                db, api_key_id=key.id, key_prefix=key.key_prefix, model="rag",
                endpoint="/v1/rag/stream", prompt=body.prompt,
                response="".join(collected) or None, status_code=200,
                response_time_ms=int((time.perf_counter() - started) * 1000),
                ip_address=ip, user_agent=ua, request_id=req_id,
            )

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
