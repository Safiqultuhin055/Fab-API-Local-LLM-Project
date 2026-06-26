"""Retrieval-Augmented Generation over a local knowledge base.

Pipeline:
  1. Load the knowledge-base file and split it into overlapping chunks.
  2. Embed every chunk once (via the configured LLM backend's /embeddings).
  3. On a query: embed it, cosine-rank chunks, take top-k.
  4. Build a grounded prompt (context + question) and call the LLM.

The index lives in memory and is built lazily on first use; POST /v1/rag/reindex
rebuilds it after editing the knowledge base.
"""
from __future__ import annotations

import asyncio
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.core.config import settings
from app.core.errors import APIError
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class Chunk:
    text: str
    section: str
    vector: list[float] = field(default_factory=list)


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _chunk_text(text: str, size: int, overlap: int) -> list[Chunk]:
    """Split markdown into overlapping chunks, tagging each with its heading."""
    chunks: list[Chunk] = []
    section = "General"
    # Split on blank lines into blocks; track the latest heading as the section.
    blocks = re.split(r"\n\s*\n", text)
    buf = ""
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        m = re.match(r"^#{1,6}\s+(.*)", block)
        if m:
            section = m.group(1).strip()
        if len(buf) + len(block) + 2 <= size:
            buf = f"{buf}\n\n{block}".strip()
        else:
            if buf:
                chunks.append(Chunk(text=buf, section=section))
            # carry overlap tail into the next buffer
            tail = buf[-overlap:] if overlap and buf else ""
            buf = f"{tail}\n\n{block}".strip() if tail else block
    if buf:
        chunks.append(Chunk(text=buf, section=section))
    return chunks


class RagService:
    def __init__(self, llm) -> None:
        self._llm = llm
        self._chunks: list[Chunk] = []
        self._lock = asyncio.Lock()
        self._ready = False

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def chunk_count(self) -> int:
        return len(self._chunks)

    async def ensure_indexed(self) -> None:
        if self._ready:
            return
        async with self._lock:
            if self._ready:
                return
            await self._build()

    async def reindex(self) -> int:
        async with self._lock:
            self._ready = False
            await self._build()
        return len(self._chunks)

    async def _build(self) -> None:
        path = Path(settings.rag_kb_path)
        if not path.exists():
            raise APIError(f"Knowledge base not found: {settings.rag_kb_path}")
        text = path.read_text(encoding="utf-8")
        chunks = _chunk_text(text, settings.rag_chunk_chars, settings.rag_chunk_overlap)
        if not chunks:
            self._chunks = []
            self._ready = True
            return
        vectors = await self._llm.embed([c.text for c in chunks])
        for c, v in zip(chunks, vectors):
            c.vector = v
        self._chunks = chunks
        self._ready = True
        logger.info("RAG index built: %d chunks from %s", len(chunks), path)

    async def retrieve(self, query: str, k: int | None = None) -> list[dict]:
        await self.ensure_indexed()
        if not self._chunks:
            return []
        k = k or settings.rag_top_k
        qv = (await self._llm.embed([query]))[0]
        scored = [
            {"score": _cosine(qv, c.vector), "section": c.section, "text": c.text}
            for c in self._chunks
        ]
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:k]

    @staticmethod
    def _build_prompt(query: str, hits: list[dict]) -> str:
        context = "\n\n".join(
            f"[Source {i + 1} - {h['section']}]\n{h['text']}" for i, h in enumerate(hits)
        )
        return (
            "You are a helpful assistant answering questions strictly from the CONTEXT "
            "below (a knowledge base about a person/their CV). Use only this information. "
            "If the answer is not in the context, say you don't have that information. "
            "Cite sources as [Source N] where relevant.\n\n"
            f"CONTEXT:\n{context}\n\n"
            f"QUESTION: {query}\n\nANSWER:"
        )

    async def answer(self, query: str, k: int | None = None) -> dict:
        hits = await self.retrieve(query, k)
        prompt = self._build_prompt(query, hits)
        result = await self._llm.complete(
            model=settings.default_model, prompt=prompt, temperature=0.2, max_tokens=1024
        )
        return {
            "answer": result.text,
            "sources": [
                {"section": h["section"], "score": round(h["score"], 4),
                 "preview": h["text"][:200]}
                for h in hits
            ],
            "tokens": result.total_tokens,
        }

    async def stream(self, query: str, k: int | None = None):
        hits = await self.retrieve(query, k)
        prompt = self._build_prompt(query, hits)
        async for token, done in self._llm.stream(
            model=settings.default_model, prompt=prompt, temperature=0.2, max_tokens=1024
        ):
            yield token, done, hits

    # --- Smart routing: RAG-first, automatic fallback to the model ---
    @staticmethod
    def _hybrid_prompt(query: str, hits: list[dict]) -> str:
        context = "\n\n".join(
            f"[Source {i + 1} - {h['section']}]\n{h['text']}" for i, h in enumerate(hits)
        )
        return (
            "Answer the QUESTION. First use the CONTEXT below (a personal knowledge "
            "base / CV). If the context contains the answer, answer from it and cite "
            "[Source N]. If the context does NOT contain the answer, then answer from "
            "your own general knowledge and begin with 'From general knowledge:'.\n\n"
            f"CONTEXT:\n{context}\n\nQUESTION: {query}\n\nANSWER:"
        )

    async def route(self, query: str, k: int | None = None) -> tuple[list[dict], bool, float]:
        """Retrieve, then decide: KB-relevant (use_rag) or fall back to the model."""
        hits = await self.retrieve(query, k)
        top = hits[0]["score"] if hits else 0.0
        return hits, top >= settings.rag_min_score, top

    async def smart_answer(self, query: str, k: int | None = None) -> dict:
        hits, use_rag, top = await self.route(query, k)
        prompt = self._hybrid_prompt(query, hits) if use_rag else query
        result = await self._llm.complete(
            model=settings.default_model, prompt=prompt, temperature=0.2, max_tokens=1024
        )
        return {
            "answer": result.text,
            "mode": "knowledge_base" if use_rag else "model",
            "top_score": round(top, 4),
            "sources": [{"section": h["section"], "score": round(h["score"], 4)} for h in hits]
            if use_rag else [],
            "tokens": result.total_tokens,
        }

    async def smart_stream(self, query: str, k: int | None = None):
        """Yield (token, done, meta). meta carries mode + sources (sent once)."""
        hits, use_rag, top = await self.route(query, k)
        prompt = self._hybrid_prompt(query, hits) if use_rag else query
        meta = {
            "mode": "knowledge_base" if use_rag else "model",
            "top_score": round(top, 4),
            "sources": [{"section": h["section"], "score": round(h["score"], 4)} for h in hits]
            if use_rag else [],
        }
        async for token, done in self._llm.stream(
            model=settings.default_model, prompt=prompt, temperature=0.2, max_tokens=1024
        ):
            yield token, done, meta
