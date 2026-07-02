"""Chat request/response schemas — match the diagram's documented shapes."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    """One prior turn of the conversation, for multi-turn memory."""

    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    model: str = Field(..., examples=["llama3.1"])
    prompt: str = Field(..., min_length=1, examples=["Write an Oracle SQL query"])
    temperature: float = Field(0.2, ge=0.0, le=2.0)
    max_tokens: int | None = Field(None, ge=1, le=32000)
    stream: bool = False
    images: list[str] | None = Field(
        None, description="Optional image data URLs (base64) for vision models"
    )
    history: list[ChatMessage] | None = Field(
        None, description="Prior turns (oldest first) so the model remembers context"
    )


class ChatResponse(BaseModel):
    success: bool = True
    response: str
    model: str
    tokens: int = 0
    response_time_ms: int = 0
    request_id: str = "-"
