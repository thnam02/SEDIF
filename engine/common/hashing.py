"""HMAC-SHA256 của mã tài khoản. Khoá là FIDES_HMAC_KEY. Thiếu khoá thì không khởi động."""

from __future__ import annotations

import hashlib
import hmac
import os

_key: bytes | None = None


def reset_key_cache() -> None:
    global _key
    _key = None


def require_key() -> bytes:
    global _key
    if _key is not None:
        return _key
    raw = os.environ.get("FIDES_HMAC_KEY", "")
    if not raw:
        raise RuntimeError("FIDES_HMAC_KEY thiếu, không khởi động")
    _key = raw.encode("utf-8")
    return _key


def account_hash(account: str) -> str:
    digest = hmac.new(require_key(), str(account).encode("utf-8"), hashlib.sha256)
    return digest.hexdigest()
