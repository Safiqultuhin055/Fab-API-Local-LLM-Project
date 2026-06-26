"""Application configuration loaded from environment via pydantic-settings.

Single source of truth for settings. No secret literals live in code; everything
comes from the environment / .env (see .env.example).
"""
from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- App ---
    app_name: str = "Local Ollama AI Gateway"
    environment: Literal["development", "production"] = "development"
    log_level: str = "INFO"
    debug: bool = True

    # --- Database ---
    database_url: str = "sqlite+aiosqlite:///./ai_gateway.db"

    # --- Security ---
    hmac_secret: str = "dev-insecure-change-me"
    admin_api_key: str = "dev-admin-change-me"
    # Dashboard UI login (username + password). Programmatic /admin/* API still
    # uses admin_api_key via the X-ADMIN-KEY header.
    admin_username: str = "admin"
    admin_password: str = "admin123"

    # --- CORS ---
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )

    # --- LLM backend ---
    # ollama  -> native Ollama API (/api/generate)  [default]
    # openai  -> OpenAI-compatible API (LM Studio, vLLM, etc; /v1/chat/completions)
    llm_backend: Literal["ollama", "openai"] = "ollama"

    # --- Ollama ---
    ollama_base_url: str = "http://localhost:11434"
    ollama_timeout_seconds: float = 120.0
    default_model: str = "llama3.1"

    # --- OpenAI-compatible (e.g. LM Studio local server) ---
    openai_base_url: str = "http://localhost:1234/v1"
    openai_api_key: str = "lm-studio"  # LM Studio ignores the value
    openai_timeout_seconds: float = 120.0

    # --- RAG (Retrieval-Augmented Generation) ---
    embedding_model: str = "text-embedding-nomic-embed-text-v1.5"
    rag_kb_path: str = "rag/knowledge_base.md"
    rag_top_k: int = 4
    rag_chunk_chars: int = 700
    rag_chunk_overlap: int = 120
    # Min cosine similarity of the best chunk to treat the KB as relevant.
    # Above -> answer from knowledge base; below -> fall back to the model.
    rag_min_score: float = 0.4

    # --- Rate limiting ---
    rate_limit_backend: Literal["memory", "redis"] = "memory"
    redis_url: str = "redis://localhost:6379/0"
    default_tier: Literal["free", "pro", "enterprise"] = "free"

    # --- Logging / privacy ---
    log_prompt_bodies: bool = True
    log_body_max_chars: int = 2000
    log_retention_days: int = 90

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_csv(cls, v: object) -> object:
        # Allow "a,b,c" in env to become a list.
        if isinstance(v, str):
            return [item.strip() for item in v.split(",") if item.strip()]
        return v

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
