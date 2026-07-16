"""Application configuration loaded from environment via pydantic-settings.

Single source of truth for settings. No secret literals live in code; everything
comes from the environment / .env (see .env.example).
"""
from __future__ import annotations

import socket
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

    # --- Database (SQL Server only) ---
    # The gateway connects exclusively to Microsoft SQL Server. There is no
    # SQLite fallback: MSSQL_HOST + MSSQL_PASSWORD must be set and the server must
    # be reachable at startup, or the app refuses to start. Needs ODBC Driver 18
    # + aioodbc installed (the Docker image installs both).
    mssql_host: str = "192.168.153.248"
    mssql_port: int = 1433
    mssql_database: str = "ai_gateway"
    mssql_user: str = "sa"
    mssql_password: str = ""
    mssql_driver: str = "ODBC Driver 18 for SQL Server"
    # Seconds to probe SQL Server for reachability at startup before erroring.
    db_probe_timeout: float = 3.0

    # --- Security ---
    hmac_secret: str = "dev-insecure-change-me"
    # Old/other HMAC secrets (comma-separated) kept ONLY so the admin UI can still
    # decrypt+copy keys that were encrypted under a previous secret or a sibling
    # instance (e.g. server Docker vs local reload). New encryption always uses
    # hmac_secret; decrypt falls back through these. Empty by default.
    hmac_secret_fallbacks: Annotated[list[str], NoDecode] = Field(default_factory=list)
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
    # Ollama lives on the "pele" server only. Selection is automatic by machine:
    #   - running ON pele (this host holds PELE_IP) -> ollama_base_url (its own
    #     localhost).
    #   - running on a local PC -> ollama_remote_url (reach pele over the LAN).
    # See resolve_ollama_base_url() below.
    ollama_base_url: str = "http://localhost:11434"
    # Pele's Ollama as seen from another machine on the LAN.
    ollama_remote_url: str = "http://192.168.153.250:11434"
    # LAN IP that identifies the pele server. If a local network interface holds
    # this address, we're on pele and use ollama_base_url; otherwise ollama_remote_url.
    pele_ip: str = "192.168.153.250"
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
    # Max seconds to wait for the KB search (embed + rank). If retrieval takes
    # longer, give up on RAG and answer straight from the model.
    rag_timeout_seconds: float = 30.0

    # --- Rate limiting ---
    rate_limit_backend: Literal["memory", "redis"] = "memory"
    redis_url: str = "redis://localhost:6379/0"
    default_tier: Literal["free", "pro", "enterprise"] = "free"

    # --- Logging / privacy ---
    log_prompt_bodies: bool = True
    log_body_max_chars: int = 2000
    log_retention_days: int = 90

    @field_validator("cors_origins", "hmac_secret_fallbacks", mode="before")
    @classmethod
    def _split_csv(cls, v: object) -> object:
        # Allow "a,b,c" in env to become a list.
        if isinstance(v, str):
            return [item.strip() for item in v.split(",") if item.strip()]
        return v

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def mssql_url(self) -> str | None:
        """Async SQLAlchemy URL for SQL Server, or None if not configured."""
        if not (self.mssql_host and self.mssql_password):
            return None
        from urllib.parse import quote_plus

        user = quote_plus(self.mssql_user)
        pwd = quote_plus(self.mssql_password)
        driver = quote_plus(self.mssql_driver)
        return (
            f"mssql+aioodbc://{user}:{pwd}@{self.mssql_host}:{self.mssql_port}/"
            f"{self.mssql_database}?driver={driver}&TrustServerCertificate=yes"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()


def _local_ips() -> set[str]:
    """Best-effort set of IPv4/IPv6 addresses bound to this machine."""
    ips: set[str] = set()
    try:
        host = socket.gethostname()
        try:
            ips.add(socket.gethostbyname(host))
        except OSError:
            pass
        for info in socket.getaddrinfo(host, None):
            ips.add(info[4][0])
    except OSError:
        pass
    return ips


@lru_cache
def resolve_ollama_base_url() -> str:
    """Ollama lives on pele. On pele -> its own localhost; elsewhere -> pele LAN IP."""
    if settings.pele_ip and settings.pele_ip in _local_ips():
        return settings.ollama_base_url
    return settings.ollama_remote_url
