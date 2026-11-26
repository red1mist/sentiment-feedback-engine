"""Client secrets (scrypt) and client access tokens (HS256 JWT)."""

from __future__ import annotations

import hashlib
import hmac
import os
import time

import jwt

ISSUER = "feedback-engine"
ALGORITHM = "HS256"

_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1


def hash_secret(secret: str) -> str:
    """scrypt from the standard library: no dependency, memory-hard, salted."""
    salt = os.urandom(16)
    key = hashlib.scrypt(secret.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32)
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${key.hex()}"


def verify_secret(secret: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_hex, key_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        key = hashlib.scrypt(secret.encode(), salt=bytes.fromhex(salt_hex), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(key, bytes.fromhex(key_hex))
    except (ValueError, TypeError):
        return False


def safe_equal(a: str, b: str) -> bool:
    """Constant-time comparison that does not leak the length either."""
    return hmac.compare_digest(hashlib.sha256(a.encode()).digest(), hashlib.sha256(b.encode()).digest())


def sign_token(client_id: str, rate_limit_per_minute: int, *, secret: str, ttl_seconds: int) -> str:
    # The client's limit travels inside the signed token so the rate limiter
    # never needs a database lookup on the request path.
    now = int(time.time())
    payload = {"sub": client_id, "rpm": rate_limit_per_minute, "iss": ISSUER, "iat": now, "exp": now + ttl_seconds}
    return jwt.encode(payload, secret, algorithm=ALGORITHM)


def verify_token(token: str, *, secret: str) -> dict:
    """Raises jwt.PyJWTError. Pins the algorithm (rejects alg=none and key
    confusion) and the issuer, and requires sub/exp to be present."""
    return jwt.decode(
        token, secret, algorithms=[ALGORITHM], issuer=ISSUER,
        options={"require": ["exp", "sub", "iss"]},
    )
