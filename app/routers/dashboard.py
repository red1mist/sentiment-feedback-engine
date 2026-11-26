import sqlite3
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query

from ..db import to_iso
from ..deps import AuthedClient, get_db, rate_limited
from ..errors import ApiError
from ..repos import FeedbackRepo
from ..schemas import Bucket, SummaryOut
from ..tagger import Category

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

MAX_WINDOW = timedelta(days=366)


@router.get("/summary", response_model=SummaryOut, response_model_by_alias=True)
def summary(
    client: AuthedClient = Depends(rate_limited),
    db: sqlite3.Connection = Depends(get_db),
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = None,
    bucket: Bucket = "day",
    category: Category | None = None,
) -> SummaryOut:
    """Counts and mean polarity per label, per category and per time bucket in
    the window (default: the last 7 days), plus the ten most frequent tags."""
    to_dt = to or datetime.now(timezone.utc)
    if to_dt.tzinfo is None:
        to_dt = to_dt.replace(tzinfo=timezone.utc)
    from_dt = from_ or to_dt - timedelta(days=7)
    if from_dt.tzinfo is None:
        from_dt = from_dt.replace(tzinfo=timezone.utc)
    if from_dt >= to_dt:
        raise ApiError(400, "bad_request", "from must be before to")
    if to_dt - from_dt > MAX_WINDOW:
        raise ApiError(400, "bad_request", f"window must be at most {MAX_WINDOW.days} days")
    result = FeedbackRepo(db).summary(client.id, from_=to_iso(from_dt), to=to_iso(to_dt), bucket=bucket, category=category)
    return SummaryOut.model_validate(result)
