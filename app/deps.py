"""FastAPI dependencies: database connection, admin key, client JWT, rate limit."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Iterator

import jwt
from fastapi import Depends, Header, Request, Response

from .db import connect
from .errors import ApiError
from .security import safe_equal, verify_token
from .settings import Settings


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_db(request: Request) -> Iterator[sqlite3.Connection]:
    conn = connect(request.app.state.settings.database_path)
    try:
        yield conn
    finally:
        conn.close()


def require_admin(settings: Settings = Depends(get_settings),
                  x_admin_key: str | None = Header(default=None, alias="X-Admin-Key")) -> None:
    if not x_admin_key or not safe_equal(x_admin_key, settings.admin_api_key):
        raise ApiError(403, "forbidden", "invalid admin key")


@dataclass(frozen=True)
class AuthedClient:
    id: str
    rate_limit_per_minute: int


def current_client(settings: Settings = Depends(get_settings),
                   authorization: str | None = Header(default=None)) -> AuthedClient:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme != "Bearer" or not token:
        raise ApiError(401, "unauthorized", "missing bearer token")
    try:
        payload = verify_token(token, secret=settings.jwt_secret)
    except jwt.ExpiredSignatureError:
        raise ApiError(401, "unauthorized", "token expired")
    except jwt.PyJWTError:
        raise ApiError(401, "unauthorized", "invalid token")
    return AuthedClient(id=str(payload["sub"]), rate_limit_per_minute=int(payload.get("rpm", 0)) or
                        settings.default_rate_limit_per_minute)


def rate_limited(request: Request, response: Response,
                 client: AuthedClient = Depends(current_client)) -> AuthedClient:
    """Authenticated + metered. Routers depend on this instead of current_client."""
    d = request.app.state.limiter.take(client.id, client.rate_limit_per_minute)
    headers = {"X-RateLimit-Limit": str(d.limit), "X-RateLimit-Remaining": str(d.remaining)}
    if not d.allowed:
        headers["Retry-After"] = str(d.retry_after_seconds)
        raise ApiError(429, "rate_limited", f"rate limit of {d.limit} requests per minute exceeded", headers=headers)
    response.headers.update(headers)
    return client


def token_endpoint_limited(request: Request, settings: Settings = Depends(get_settings)) -> None:
    """The unauthenticated token endpoint gets a per-IP bucket against brute force."""
    ip = request.client.host if request.client else "unknown"
    d = request.app.state.token_limiter.take(ip, settings.token_endpoint_limit_per_minute)
    if not d.allowed:
        raise ApiError(429, "rate_limited", "too many token requests", headers={"Retry-After": str(d.retry_after_seconds)})
