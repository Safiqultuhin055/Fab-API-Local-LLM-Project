"""Make every api_keys row copyable in the admin UI.

Two independent fixes, both idempotent:

  1. RE-ENCRYPT  - a row whose key_encrypted decrypts only under an
     HMAC_SECRET_FALLBACKS entry (e.g. it was created by the server Docker with a
     different secret) is re-encrypted under the CURRENT hmac_secret. Non-
     destructive: same key value, key_hash unchanged, just fresh ciphertext.

  2. ROTATE      - a row with NO recoverable plaintext (key_encrypted NULL, or
     ciphertext that decrypts under no known secret) cannot be copied: the
     plaintext is gone by design. --rotate regenerates a brand-new key for it
     (new key_hash / key_prefix / key_encrypted). DESTRUCTIVE: the OLD key value
     stops authenticating. The new full key is printed once - hand it to the owner.

Safety: dry-run by default. Nothing is written without --apply.

Run:
  python -m scripts.backfill_key_copy                 # report only
  python -m scripts.backfill_key_copy --apply         # re-encrypt fallback keys
  python -m scripts.backfill_key_copy --rotate --apply  # + rotate unrecoverable keys

Before running for the server-made keys, put the server's secret in .env:
  HMAC_SECRET_FALLBACKS=<the-other-hmac-secret>[,<another>]
"""
from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import false, select

from app.core import security
from app.db.base import SessionFactory, engine
from app.db.models import ApiKey


def _decrypt_state(row: ApiKey) -> tuple[str, str | None]:
    """Classify a row: ('current'|'fallback'|'unrecoverable', plaintext|None)."""
    if not row.key_encrypted:
        return "unrecoverable", None
    # Decrypts under the current secret alone?
    current_only = security._fernet_for(security.settings.hmac_secret)
    try:
        pt = current_only.decrypt(row.key_encrypted.encode("utf-8")).decode("utf-8")
        return "current", pt
    except Exception:
        pass
    # Decrypts under any configured secret (current + fallbacks)?
    pt = security.decrypt_secret(row.key_encrypted)
    if pt is not None:
        return "fallback", pt
    return "unrecoverable", None


async def main(apply: bool, rotate: bool) -> None:
    reencrypted: list[str] = []
    rotated: list[tuple[str, str]] = []  # (owner, new_full_key)
    unrecoverable_left: list[str] = []

    async with SessionFactory() as db:
        rows = (
            await db.execute(
                select(ApiKey)
                .where(ApiKey.is_deleted == false())
                .order_by(ApiKey.id)
            )
        ).scalars().all()

        for row in rows:
            state, plaintext = _decrypt_state(row)

            if state == "current":
                continue  # already copyable - nothing to do

            if state == "fallback":
                # Re-store under the current secret so it decrypts without fallback.
                if apply:
                    row.key_encrypted = security.encrypt_secret(plaintext)  # type: ignore[arg-type]
                reencrypted.append(f"#{row.id} {row.key_prefix} ({row.owner_name})")
                continue

            # state == "unrecoverable"
            if not rotate:
                unrecoverable_left.append(
                    f"#{row.id} {row.key_prefix} ({row.owner_name})"
                )
                continue

            new_full = security.generate_api_key()
            if apply:
                row.key_hash = security.hash_api_key(new_full)
                row.key_prefix = security.key_display_prefix(new_full)
                row.key_encrypted = security.encrypt_secret(new_full)
            rotated.append((f"#{row.id} {row.owner_name}", new_full))

        if apply:
            await db.commit()

    mode = "APPLIED" if apply else "DRY-RUN (no writes; pass --apply)"
    print(f"=== backfill_key_copy - {mode} ===\n")

    print(f"[re-encrypt under current secret] {len(reencrypted)} key(s):")
    for line in reencrypted:
        print(f"  - {line}")
    if not reencrypted:
        print("  (none)")

    print(f"\n[rotated - NEW key values, hand to owners] {len(rotated)} key(s):")
    for owner, full in rotated:
        print(f"  - {owner}: {full}")
    if not rotated:
        print("  (none)" + ("" if rotate else "  - pass --rotate to regenerate"))

    if unrecoverable_left:
        print(
            f"\n[unrecoverable, left as prefix-only] {len(unrecoverable_left)} key(s)"
            " - pass --rotate to regenerate them:"
        )
        for line in unrecoverable_left:
            print(f"  - {line}")

    await engine.dispose()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry-run)")
    ap.add_argument(
        "--rotate",
        action="store_true",
        help="regenerate keys whose plaintext is unrecoverable (DESTRUCTIVE)",
    )
    args = ap.parse_args()
    asyncio.run(main(apply=args.apply, rotate=args.rotate))
