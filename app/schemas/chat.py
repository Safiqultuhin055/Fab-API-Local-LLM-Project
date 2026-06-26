"""Chat request/response schemas — match the diagram's documented shapes."""
from __future__ import annotations

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    model: str = Field(..., examples=["llama3.1"])
    prompt: str = Field(..., min_length=1, examples=["Write an Oracle SQL query"])
    temperature: float = Field(0.2, ge=0.0, le=2.0)
    max_tokens: int | None = Field(None, ge=1, le=32000)
    stream: bool = False
    images: list[str] | None = Field(
        None, description="Optional image data URLs (base64) for vision models"
    )


class ChatResponse(BaseModel):
    success: bool = True
    response: str
    model: str
    tokens: int = 0
    response_time_ms: int = 0
    request_id: str = "-"
