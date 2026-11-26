import sqlite3

from fastapi import APIRouter, Depends

from ..deps import get_db, get_settings, require_admin, token_endpoint_limited
from ..errors import ApiError
from ..repos import ClientRepo
from ..schemas import ClientCreated, CreateClientRequest, TokenRequest, TokenResponse
from ..security import sign_token
from ..settings import Settings

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/clients", response_model=ClientCreated, status_code=201, dependencies=[Depends(require_admin)])
def create_client(body: CreateClientRequest, db: sqlite3.Connection = Depends(get_db),
                  settings: Settings = Depends(get_settings)) -> ClientCreated:
    """Admin-only: register a client (tenant) and hand out its secret once."""
    created = ClientRepo(db).create(body.name, body.rate_limit_per_minute or settings.default_rate_limit_per_minute)
    return ClientCreated(**created)


@router.post("/token", response_model=TokenResponse, dependencies=[Depends(token_endpoint_limited)])
def issue_token(body: TokenRequest, db: sqlite3.Connection = Depends(get_db),
                settings: Settings = Depends(get_settings)) -> TokenResponse:
    repo = ClientRepo(db)
    row = repo.get(body.client_id)
    if row is None or not repo.verify(row, body.client_secret):
        raise ApiError(401, "unauthorized", "invalid client credentials")  # same message either way
    token = sign_token(row["client_id"], row["rate_limit_per_minute"],
                       secret=settings.jwt_secret, ttl_seconds=settings.jwt_ttl_seconds)
    return TokenResponse(access_token=token, expires_in=settings.jwt_ttl_seconds)
