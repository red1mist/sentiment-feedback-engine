"""Runtime configuration, read once from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass

DEV_JWT_SECRET = "dev-jwt-secret-change-me"
DEV_ADMIN_KEY = "dev-admin-key-change-me"


@dataclass(frozen=True)
class Settings:
    env: str = "dev"                          # dev | production
    database_path: str = "feedback.db"
    jwt_secret: str = DEV_JWT_SECRET
    jwt_ttl_seconds: int = 3600
    admin_api_key: str = DEV_ADMIN_KEY
    default_rate_limit_per_minute: int = 60
    token_endpoint_limit_per_minute: int = 10
    backend: str = "vader"                    # vader | transformers
    transformers_model: str = "distilbert-base-uncased-finetuned-sst-2-english"
    transformers_neutral_below: float = 0.60

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Settings":
        e = os.environ if env is None else env
        d = cls()
        return cls(
            env=e.get("APP_ENV", d.env).lower(),
            database_path=e.get("DATABASE_PATH", d.database_path),
            jwt_secret=e.get("JWT_SECRET", d.jwt_secret),
            jwt_ttl_seconds=int(e.get("JWT_TTL_SECONDS", d.jwt_ttl_seconds)),
            admin_api_key=e.get("ADMIN_API_KEY", d.admin_api_key),
            default_rate_limit_per_minute=int(e.get("DEFAULT_RATE_LIMIT_PER_MINUTE", d.default_rate_limit_per_minute)),
            token_endpoint_limit_per_minute=int(e.get("TOKEN_ENDPOINT_LIMIT_PER_MINUTE", d.token_endpoint_limit_per_minute)),
            backend=e.get("SENTIMENT_BACKEND", d.backend).lower(),
            transformers_model=e.get("TRANSFORMERS_MODEL", d.transformers_model),
            transformers_neutral_below=float(e.get("TRANSFORMERS_NEUTRAL_BELOW", d.transformers_neutral_below)),
        )

    def check(self) -> None:
        """Development defaults are fine on a laptop and nowhere else."""
        if self.env == "production" and (self.jwt_secret == DEV_JWT_SECRET or self.admin_api_key == DEV_ADMIN_KEY):
            raise RuntimeError("APP_ENV=production requires JWT_SECRET and ADMIN_API_KEY to be set")
        if self.backend not in ("vader", "transformers"):
            raise RuntimeError(f"SENTIMENT_BACKEND must be vader or transformers, got {self.backend!r}")
