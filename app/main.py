"""Sentiment-Driven Feedback Engine: one FastAPI application.

Clients authenticate with a JWT and are rate limited per client; feedback is
classified in-process, auto-tagged, stored in SQLite and aggregated for a
dashboard. `create_app` takes its dependencies so tests can inject a clock
and a temporary database.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from contextlib import asynccontextmanager
from typing import Callable

from fastapi import FastAPI, Request

from .classifier import Classifier, build_classifier
from .db import connect, migrate
from .errors import install_handlers
from .ratelimit import RateLimiter
from .routers import auth, dashboard, feedback, health
from .schemas import MetricsOut
from .settings import Settings

log = logging.getLogger("feedback-engine")


class Metrics:
    """Process-local counters for the classifier; enough to eyeball latency."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.count = 0
        self.total_ms = 0.0
        self.max_ms = 0.0

    def record(self, ms: float) -> None:
        with self._lock:
            self.count += 1
            self.total_ms += ms
            self.max_ms = max(self.max_ms, ms)

    def snapshot(self) -> MetricsOut:
        with self._lock:
            avg = self.total_ms / self.count if self.count else 0.0
            return MetricsOut(classified=self.count, classify_ms_avg=round(avg, 3), classify_ms_max=round(self.max_ms, 3))


def create_app(settings: Settings | None = None, *, classifier: Classifier | None = None,
               now: Callable[[], float] = time.monotonic) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.check()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        conn = connect(settings.database_path)
        try:
            migrate(conn)
        finally:
            conn.close()
        # Load the model once so the first request is not slow and a bad
        # SENTIMENT_BACKEND fails at startup rather than on first use.
        app.state.classifier = classifier or build_classifier(
            settings.backend, transformers_model=settings.transformers_model,
            transformers_neutral_below=settings.transformers_neutral_below)
        app.state.metrics = Metrics()
        app.state.limiter = RateLimiter(now=now)
        app.state.token_limiter = RateLimiter(now=now)
        log.info("database %s, classifier %s", settings.database_path, app.state.classifier.name)
        yield

    app = FastAPI(title="Sentiment-Driven Feedback Engine", version="2.0.0", lifespan=lifespan)
    app.state.settings = settings
    install_handlers(app)

    @app.middleware("http")
    async def request_id(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex
        response = await call_next(request)
        response.headers["X-Request-Id"] = rid
        return response

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(feedback.router)
    app.include_router(dashboard.router)
    return app


app = create_app()
