from app.ratelimit import RateLimiter, TokenBucket

from .conftest import FakeClock, auth, register, token_for


def test_token_bucket_bursts_then_refills():
    b = TokenBucket(6, 6 / 60, now=0.0)  # 6 per minute = one every 10 s
    for _ in range(6):
        assert b.take(0.0)[0] is True
    allowed, remaining, wait = b.take(0.0)
    assert (allowed, remaining, wait) == (False, 0, 10.0)

    assert b.take(5.0)[0] is False      # half a token
    assert b.take(10.0)[0] is True      # one token refilled
    assert b.take(10.0)[0] is False

    b.take(600.0)                       # long idle: capped at capacity, not accumulated
    assert b.tokens == 5.0


def test_limiter_isolates_keys_and_sweeps_idle_buckets():
    clock = FakeClock(0.0)
    limiter = RateLimiter(now=clock.now)

    assert limiter.take("a", 2).allowed and limiter.take("a", 2).allowed
    d = limiter.take("a", 2)
    assert (d.allowed, d.remaining, d.retry_after_seconds, d.limit) == (False, 0, 30, 2)
    assert limiter.take("b", 2).allowed          # other key unaffected
    assert len(limiter) == 2

    clock.advance(30)
    assert limiter.take("a", 2).allowed          # one token back

    limiter.take("a", 5)                         # changed limit -> fresh bucket with the new capacity
    assert limiter.take("a", 5).remaining == 3

    clock.advance(11 * 60)                       # idle sweep on the next call
    limiter.take("c", 1)
    assert len(limiter) == 1


def test_per_client_limit_from_jwt_is_enforced(client, clock):
    low = auth(token_for(client, register(client, "low", rpm=2)))
    high = auth(token_for(client, register(client, "high", rpm=100)))

    r = client.get("/feedback", headers=low)
    assert r.status_code == 200
    assert (r.headers["X-RateLimit-Limit"], r.headers["X-RateLimit-Remaining"]) == ("2", "1")
    assert client.get("/feedback", headers=low).status_code == 200

    r = client.get("/feedback", headers=low)
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "rate_limited"
    assert r.headers["Retry-After"] == "30"
    assert r.headers["X-RateLimit-Remaining"] == "0"

    assert client.get("/feedback", headers=high).status_code == 200   # other client unaffected
    clock.advance(30)
    assert client.get("/feedback", headers=low).status_code == 200    # recovered
