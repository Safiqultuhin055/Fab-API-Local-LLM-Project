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


# --- Password hashing — Argon2id when available, PBKDF2-HMAC-SHA256 fallback.
# Verification dispatches on the stored hash's scheme prefix, so hashes created
# without argon2 (e.g. a lean local venv) still verify anywhere, and vice-versa.
_PBKDF2_ITERATIONS = 240_000

try:  # argon2-cffi is in requirements.txt; may be absent in a minimal venv.
    from argon2 import PasswordHasher
    from argon2.exceptions import VerifyMismatchError

    _ph = PasswordHasher()
    _HAS_ARGON2 = True
except ImportError:
    _ph = None
    _HAS_ARGON2 = False


def _pbkdf2_hash(plain: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", plain.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt.hex()}${dk.hex()}"


def _pbkdf2_verify(plain: str, stored: str) -> bool:
    try:
        _, iters, salt_hex, hash_hex = stored.split("$")
        dk = hashlib.pbkdf2_hmac(
            "sha256", plain.encode("utf-8"), bytes.fromhex(salt_hex), int(iters)
        )
        return hmac.compare_digest(dk.hex(), hash_hex)
    except (ValueError, TypeError):
        return False


def hash_password(plain: str) -> str:
    """Hash a plaintext password. Argon2id if installed, else PBKDF2-HMAC-SHA256."""
    return _ph.hash(plain) if _HAS_ARGON2 else _pbkdf2_hash(plain)


def verify_password(plain: str, stored: str) -> bool:
    """Constant-time verify, dispatching on the stored hash's scheme prefix."""
    if stored.startswith("$argon2"):
        if not _HAS_ARGON2:
            return False
        try:
            return _ph.verify(stored, plain)
        except VerifyMismatchError:
            return False
        except Exception:
            return False
    if stored.startswith("pbkdf2_sha256$"):
        return _pbkdf2_verify(plain, stored)
    return False
