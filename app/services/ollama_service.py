"""Async Ollama client. Pure I/O layer — no web-framework types leak in here.

Wraps Ollama's /api/generate (completion + token stream) and /api/tags (models).
Maps upstream failures (down / slow / model-missing) to UpstreamError.
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx

from app.core.config import settings
from app.core.errors import NotFoundError, UpstreamError
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(slots=True)
class CompletionResult:
    text: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class OllamaService:
    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=settings.ollama_base_url,
            timeout=httpx.Timeout(settings.ollama_timeout_seconds),
        )

    async def close(self) -> None:
        await self._client.aclose()

    def _payload(
        self, model: str, prompt: str, temperature: float, max_tokens: int | None,
        images: list[str] | None = None,
    ) -> dict[str, object]:
        opts: dict[str, object] = {"temperature": temperature}
        if max_tokens is not None:
            opts["num_predict"] = max_tokens
        body: dict[str, object] = {"model": model, "prompt": prompt, "options": opts}
        if images:
            # Ollama expects raw base64 (no data: prefix).
            body["images"] = [i.split(",", 1)[-1] for i in images]
        return body

    async def complete(
        self, *, model: str, prompt: str, temperature: float,
        max_tokens: int | None, images: list[str] | None = None,
    ) -> CompletionResult:
        payload = self._payload(model, prompt, temperature, max_tokens, images)
        payload["stream"] = False
        try:
            resp = await self._client.post("/api/generate", json=payload)
        except httpx.TimeoutException as exc:
            raise UpstreamError("Ollama request timed out") from exc
        except httpx.RequestError as exc:
            raise UpstreamError(f"Cannot reach Ollama: {exc}") from exc

        if resp.status_code == 404:
            raise NotFoundError(f"Model '{model}' not found in Ollama")
        if resp.status_code >= 400:
            raise UpstreamError(f"Ollama error {resp.status_code}: {resp.text[:200]}")

        data = resp.json()
        pt = int(data.get("prompt_eval_count", 0) or 0)
        ct = int(data.get("eval_count", 0) or 0)
        return CompletionResult(
            text=data.get("response", ""),
            model=model,
            prompt_tokens=pt,
            completion_tokens=ct,
            total_tokens=pt + ct,
        )

    async def stream(
        self, *, model: str, prompt: str, temperature: float,
        max_tokens: int | None, images: list[str] | None = None,
    ) -> AsyncIterator[tuple[str, bool]]:
        """Yield (token_text, done) tuples as Ollama streams them."""
        payload = self._payload(model, prompt, temperature, max_tokens, images)
        payload["stream"] = True
        try:
            async with self._client.stream(
                "POST", "/api/generate", json=payload
            ) as resp:
                if resp.status_code == 404:
                    raise NotFoundError(f"Model '{model}' not found in Ollama")
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode(errors="replace")
                    raise UpstreamError(f"Ollama error {resp.status_code}: {body[:200]}")
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    chunk = json.loads(line)
                    yield chunk.get("response", ""), bool(chunk.get("done", False))
        except httpx.TimeoutException as exc:
            raise UpstreamError("Ollama stream timed out") from exc
        except httpx.RequestError as exc:
            raise UpstreamError(f"Cannot reach Ollama: {exc}") from exc

    async def list_models(self) -> list[dict[str, object]]:
        try:
            resp = await self._client.get("/api/tags")
        except httpx.RequestError as exc:
            raise UpstreamError(f"Cannot reach Ollama: {exc}") from exc
        if resp.status_code >= 400:
            raise UpstreamError(f"Ollama error {resp.status_code}")
        return resp.json().get("models", [])

    async def ping(self) -> bool:
        try:
            resp = await self._client.get("/api/tags")
            return resp.status_code < 500
        except httpx.RequestError:
            return False

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embeddings via Ollama /api/embeddings (one request per input)."""
        from app.core.config import settings as _s

        out: list[list[float]] = []
        for t in texts:
            try:
                resp = await self._client.post(
                    "/api/embeddings",
                    json={"model": _s.embedding_model, "prompt": t},
                )
            except httpx.RequestError as exc:
                raise UpstreamError(f"Cannot reach Ollama: {exc}") from exc
            if resp.status_code >= 400:
                raise UpstreamError(f"Ollama embeddings error {resp.status_code}")
            out.append(resp.json().get("embedding", []))
        return out
