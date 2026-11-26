"""Per-key token buckets.

A bucket holds `capacity` tokens and refills continuously at `capacity` per
window. A client may burst up to its full limit, then settles to the steady
rate; unlike a fixed window there is no 2x spike at the boundary. State is
in-process: several replicas would move the same algorithm into Redis.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class Decision:
    allowed: bool
    limit: int
    remaining: int
    retry_after_seconds: int


class TokenBucket:
    def __init__(self, capacity: int, refill_per_second: float, now: float) -> None:
        self.capacity = capacity
        self.refill_per_second = refill_per_second
        self.tokens = float(capacity)
        self.updated_at = now

    def refill(self, now: float) -> None:
        elapsed = max(0.0, now - self.updated_at)
        self.tokens = min(float(self.capacity), self.tokens + elapsed * self.refill_per_second)
        self.updated_at = now

    def take(self, now: float) -> tuple[bool, int, float]:
        """Returns (allowed, remaining whole tokens, seconds until one token)."""
        self.refill(now)
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True, int(self.tokens), 0.0
        return False, 0, (1.0 - self.tokens) / self.refill_per_second


class RateLimiter:
    def __init__(self, *, window_seconds: float = 60.0, now: Callable[[], float] = time.monotonic,
                 idle_seconds: float = 600.0, sweep_every: float = 60.0) -> None:
        self._buckets: dict[str, TokenBucket] = {}
        self._window = window_seconds
        self._now = now
        self._idle = idle_seconds
        self._sweep_every = sweep_every
        self._last_sweep = now()

    def __len__(self) -> int:
        return len(self._buckets)

    def _sweep(self, now: float) -> None:
        if now - self._last_sweep < self._sweep_every:
            return
        self._last_sweep = now
        for key in [k for k, b in self._buckets.items() if now - b.updated_at > self._idle]:
            del self._buckets[key]

    def take(self, key: str, limit: int) -> Decision:
        now = self._now()
        self._sweep(now)
        bucket = self._buckets.get(key)
        if bucket is None or bucket.capacity != limit:   # new key, or the client's limit changed
            bucket = TokenBucket(limit, limit / self._window, now)
            self._buckets[key] = bucket
        allowed, remaining, wait = bucket.take(now)
        retry_after = 0 if allowed else max(1, math.ceil(wait))
        return Decision(allowed=allowed, limit=limit, remaining=remaining, retry_after_seconds=retry_after)
