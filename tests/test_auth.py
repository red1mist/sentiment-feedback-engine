import time

import jwt

from .conftest import ADMIN, auth, register, token_for


def test_client_creation_requires_admin_key(client):
    assert client.post("/auth/clients", json={"name": "acme"}).status_code == 403
    assert client.post("/auth/clients", json={"name": "acme"}, headers={"X-Admin-Key": "nope"}).status_code == 403
    created = register(client, "acme", rpm=120)
    assert created["client_id"].startswith("ck_")
    assert created["client_secret"].startswith("cs_")
    assert created["rate_limit_per_minute"] == 120


def test_client_creation_validates_and_applies_default_limit(client):
    r = client.post("/auth/clients", json={}, headers=ADMIN)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"
    assert r.json()["error"]["details"][0]["path"] == "body.name"
    assert client.post("/auth/clients", json={"name": "   "}, headers=ADMIN).status_code == 422
    assert register(client, "x")["rate_limit_per_minute"] == 5


def test_secret_is_stored_hashed(client, db):
    created = register(client)
    row = db.execute("SELECT secret_hash FROM clients WHERE client_id = ?", (created["client_id"],)).fetchone()
    assert row["secret_hash"].startswith("scrypt$")
    assert created["client_secret"] not in row["secret_hash"]


def test_token_exchange_signs_client_and_rate_limit(client, settings):
    created = register(client, "acme", rpm=42)
    r = client.post("/auth/token", json={"client_id": created["client_id"], "client_secret": created["client_secret"]})
    assert r.status_code == 200
    body = r.json()
    assert body["token_type"] == "Bearer" and body["expires_in"] == 3600
    payload = jwt.decode(body["access_token"], settings.jwt_secret, algorithms=["HS256"], issuer="feedback-engine")
    assert payload["sub"] == created["client_id"]
    assert payload["rpm"] == 42


def test_bad_credentials_get_one_message(client):
    created = register(client)
    a = client.post("/auth/token", json={"client_id": created["client_id"], "client_secret": "wrong"})
    b = client.post("/auth/token", json={"client_id": "ck_nobody", "client_secret": "wrong"})
    assert a.status_code == b.status_code == 401
    assert a.json()["error"]["message"] == b.json()["error"]["message"]


def test_token_endpoint_is_rate_limited_per_ip(client):
    for _ in range(3):  # TOKEN_ENDPOINT_LIMIT_PER_MINUTE = 3
        assert client.post("/auth/token", json={"client_id": "x", "client_secret": "y"}).status_code == 401
    r = client.post("/auth/token", json={"client_id": "x", "client_secret": "y"})
    assert r.status_code == 429
    assert int(r.headers["Retry-After"]) >= 1


def test_protected_routes_reject_bad_tokens(client, settings):
    assert client.get("/feedback").status_code == 401
    assert client.get("/feedback", headers={"Authorization": "Basic abc"}).status_code == 401
    assert client.get("/feedback", headers=auth("not.a.jwt")).status_code == 401

    forged = jwt.encode({"sub": "ck_1", "rpm": 5, "iss": "feedback-engine", "exp": time.time() + 60}, "other-secret", "HS256")
    assert client.get("/feedback", headers=auth(forged)).status_code == 401

    expired = jwt.encode({"sub": "ck_1", "rpm": 5, "iss": "feedback-engine", "exp": time.time() - 10}, settings.jwt_secret, "HS256")
    r = client.get("/feedback", headers=auth(expired))
    assert r.status_code == 401 and r.json()["error"]["message"] == "token expired"

    wrong_issuer = jwt.encode({"sub": "ck_1", "rpm": 5, "iss": "someone", "exp": time.time() + 60}, settings.jwt_secret, "HS256")
    assert client.get("/feedback", headers=auth(wrong_issuer)).status_code == 401

    unsigned = jwt.encode({"sub": "ck_1", "rpm": 5, "iss": "feedback-engine", "exp": time.time() + 60}, None, algorithm="none")
    assert client.get("/feedback", headers=auth(unsigned)).status_code == 401


def test_valid_token_grants_access(client):
    created = register(client)
    r = client.get("/feedback", headers=auth(token_for(client, created)))
    assert r.status_code == 200
    assert r.json() == {"items": [], "next_cursor": None}
    assert len(r.headers["X-Request-Id"]) == 32


def test_health_and_metrics(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "database": "ok", "model": "vader-3.3.2"}
    assert client.get("/metrics").json()["classified"] == 0


def test_production_refuses_default_secrets(tmp_path):
    from app.main import create_app
    from app.settings import Settings
    import pytest

    with pytest.raises(RuntimeError):
        create_app(Settings(env="production", database_path=str(tmp_path / "p.db")))
