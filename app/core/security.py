"""Secret primitives: API-key generation/hashing and password hashing.

API keys are NEVER stored raw. We store:
  - key_hash : HMAC-SHA256(full_key) using HMAC_SECRET  (lookup + verification)
  - key_prefix : first 12 chars (e.g. "AI-7f3e2c6a") — non-secret, for display

The full key is returned to the caller exactly once, at creation time.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings

KEY_PREFIX = "AI-"
_KEY_RANDOM_LEN = 32  # url-safe chars after the "AI-" prefix
_DISPLAY_PREFIX_LEN = 12


def generate_api_key() -> str:
    """Return a fresh full API key: 'AI-' + 32 url-safe chars."""
    body = secrets.token_urlsafe(_KEY_RANDOM_LEN)[:_KEY_RANDOM_LEN]
    return f"{KEY_PREFIX}{body}"


def hash_api_key(full_key: str) -> str:
    """HMAC-SHA256 hash of the key, hex-encoded. Deterministic for lookup."""
    return hmac.new(
        settings.hmac_secret.encode("utf-8"),
        full_key.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def key_display_prefix(full_key: str) -> str:
    """Short non-secret prefix stored for UI display (e.g. 'AI-7f3e2c6a')."""
    return full_key[:_DISPLAY_PREFIX_LEN]


def verify_api_key(full_key: str, stored_hash: str) -> bool:
    """Constant-time compare of a presented key against a stored hash."""
    return hmac.compare_digest(hash_api_key(full_key), stored_hash)


# --- Reversible encryption of the full key (so the admin UI can re-copy it).
# Fernet (AES-128-CBC + HMAC) keyed from HMAC_SECRET. The DB never holds the
# plaintext key — only this ciphertext, which is useless without HMAC_SECRET.
def _fernet() -> Fernet:
    digest = hashlib.sha256(settings.hmac_secret.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_secret(token: str | None) -> str | None:
    if not token:
        return None
    try:
        return _fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError):
        return None


# --- Password hashing (Argon2id) — used by enterprise track ---
try:
    from argon2 import PasswordHasher
    from argon2.exceptions import VerifyMismatchError

    _ph = PasswordHasher()

    def hash_password(plain: str) -> str:
        return _ph.hash(plain)

    def verify_password(plain: str, stored: str) -> bool:
        try:
            return _ph.verify(stored, plain)
        except VerifyMismatchError:
            return False
except ImportError:  # argon2 optional at prototype stage
    def hash_password(plain: str) -> str:  # type: ignore[misc]
        raise RuntimeError("argon2-cffi not installed")

    def verify_password(plain: str, stored: str) -> bool:  # type: ignore[misc]
        raise RuntimeError("argon2-cffi not installed")
