import pytest
from fastapi.testclient import TestClient

from app.classifier import Sentiment
from app.db import connect, migrate
from app.main import create_app
from app.repos import FeedbackRepo
from app.settings import Settings

ADMIN = {"X-Admin-Key": "test-admin"}


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def now(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(database_path=str(tmp_path / "test.db"), jwt_secret="test-secret", admin_api_key="test-admin",
                    default_rate_limit_per_minute=5, token_endpoint_limit_per_minute=3)


@pytest.fixture
def app(settings, clock):
    return create_app(settings, now=clock.now)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


@pytest.fixture
def db(settings):
    conn = connect(settings.database_path)
    migrate(conn)  # idempotent; the app does the same at startup
    yield conn
    conn.close()


def register(client: TestClient, name: str = "acme", rpm: int | None = None) -> dict:
    body = {"name": name}
    if rpm is not None:
        body["rate_limit_per_minute"] = rpm
    r = client.post("/auth/clients", json=body, headers=ADMIN)
    assert r.status_code == 201, r.text
    return r.json()


def token_for(client: TestClient, created: dict) -> str:
    r = client.post("/auth/token", json={"client_id": created["client_id"], "client_secret": created["client_secret"]})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def tenant(client):
    """A registered client with a generous limit, plus its bearer headers."""
    created = register(client, "tenant", rpm=1000)
    return {"created": created, "headers": auth(token_for(client, created))}


def insert_row(db, client_id: str, *, created_at: str, category: str = "other", label: str = "neutral",
               score: float = 0.0, tags: list[str] | None = None, text: str = "x") -> dict:
    return FeedbackRepo(db).insert(client_id=client_id, text=text, source="test", category=category,
                                   tags=tags or [], sentiment=Sentiment(label, score, abs(score), "test"),
                                   created_at=created_at)
