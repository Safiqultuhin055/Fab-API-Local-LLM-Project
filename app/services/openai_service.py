"""OpenAI-compatible LLM client (LM Studio, vLLM, llama.cpp server, etc).

Same public interface as OllamaService so routers/deps are unchanged. Talks the
OpenAI REST shape: POST /chat/completions, GET /models. LM Studio exposes this at
http://localhost:1234/v1 once its local server is started.
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx

from app.core.config import settings
from app.core.errors import NotFoundError, UpstreamError
from app.core.logging import get_logger
from app.services.ollama_service import CompletionResult  # reuse the dataclass

logger = get_logger(__name__)


class OpenAIService:
    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=settings.openai_base_url.rstrip("/"),
            timeout=httpx.Timeout(settings.openai_timeout_seconds),
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
        )

    async def close(self) -> None:
        await self._client.aclose()

    def _payload(
        self, model: str, prompt: str, temperature: float, max_tokens: int | None,
        images: list[str] | None = None,
    ) -> dict[str, object]:
        # Multimodal: when images are present, content becomes a parts array
        # (OpenAI vision format). LM Studio accepts data: URLs for local images.
        if images:
            content: object = [{"type": "text", "text": prompt}] + [
                {"type": "image_url", "image_url": {"url": img}} for img in images
            ]
        else:
            content = prompt
        body: dict[str, object] = {
            "model": model,
            "messages": [{"role": "user", "content": content}],
            "temperature": temperature,
        }
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        return body

    async def complete(
        self, *, model: str, prompt: str, temperature: float,
        max_tokens: int | None, images: list[str] | None = None,
    ) -> CompletionResult:
        payload = self._payload(model, prompt, temperature, max_tokens, images)
        payload["stream"] = False
        try:
            resp = await self._client.post("/chat/completions", json=payload)
        except httpx.TimeoutException as exc:
            raise UpstreamError("LLM request timed out") from exc
        except httpx.RequestError as exc:
            raise UpstreamError(f"Cannot reach LLM server: {exc}") from exc

        if resp.status_code == 404:
            raise NotFoundError(f"Model '{model}' not found on the LLM server")
        if resp.status_code >= 400:
            raise UpstreamError(f"LLM error {resp.status_code}: {resp.text[:200]}")

        data = resp.json()
        text = data["choices"][0]["message"]["content"]
        usage = data.get("usage") or {}
        pt = int(usage.get("prompt_tokens", 0) or 0)
        ct = int(usage.get("completion_tokens", 0) or 0)
        return CompletionResult(
            text=text, model=data.get("model", model),
            prompt_tokens=pt, completion_tokens=ct,
            total_tokens=int(usage.get("total_tokens", pt + ct) or (pt + ct)),
        )

    async def stream(
        self, *, model: str, prompt: str, temperature: float,
        max_tokens: int | None, images: list[str] | None = None,
    ) -> AsyncIterator[tuple[str, bool]]:
        payload = self._payload(model, prompt, temperature, max_tokens, images)
        payload["stream"] = True
        try:
            async with self._client.stream(
                "POST", "/chat/completions", json=payload
            ) as resp:
                if resp.status_code == 404:
                    raise NotFoundError(f"Model '{model}' not found on the LLM server")
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode(errors="replace")
                    raise UpstreamError(f"LLM error {resp.status_code}: {body[:200]}")
                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        yield "", True
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    choice = (chunk.get("choices") or [{}])[0]
                    token = (choice.get("delta") or {}).get("content", "")
                    done = choice.get("finish_reason") is not None
                    if token or done:
                        yield token, done
        except httpx.TimeoutException as exc:
            raise UpstreamError("LLM stream timed out") from exc
        except httpx.RequestError as exc:
            raise UpstreamError(f"Cannot reach LLM server: {exc}") from exc

    async def list_models(self) -> list[dict[str, object]]:
        try:
            resp = await self._client.get("/models")
        except httpx.RequestError as exc:
            raise UpstreamError(f"Cannot reach LLM server: {exc}") from exc
        if resp.status_code >= 400:
            raise UpstreamError(f"LLM error {resp.status_code}")
        return [{"name": m.get("id"), "size": None} for m in resp.json().get("data", [])]

    async def ping(self) -> bool:
        try:
            resp = await self._client.get("/models")
            return resp.status_code < 500
        except httpx.RequestError:
            return False

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Return embedding vectors for each input via /embeddings."""
        try:
            resp = await self._client.post(
                "/embeddings",
                json={"model": settings.embedding_model, "input": texts},
            )
        except httpx.RequestError as exc:
            raise UpstreamError(f"Cannot reach embeddings server: {exc}") from exc
        if resp.status_code >= 400:
            raise UpstreamError(f"Embeddings error {resp.status_code}: {resp.text[:200]}")
        data = resp.json().get("data", [])
        return [item["embedding"] for item in data]


def build_llm_service():
    """Return the configured LLM client (Ollama-native or OpenAI-compatible)."""
    if settings.llm_backend == "openai":
        logger.info("LLM backend: OpenAI-compatible @ %s", settings.openai_base_url)
        return OpenAIService()
    from app.services.ollama_service import OllamaService

    logger.info("LLM backend: Ollama @ %s", settings.ollama_base_url)
    return OllamaService()
