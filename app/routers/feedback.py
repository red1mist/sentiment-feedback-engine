import sqlite3
import time
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request

from ..db import to_iso
from ..deps import AuthedClient, get_db, rate_limited
from ..errors import ApiError
from ..repos import FeedbackRepo, decode_cursor
from ..schemas import ClassifyPreview, CreateFeedbackRequest, FeedbackOut, FeedbackPage, Label
from ..tagger import Category, tag

router = APIRouter(prefix="/feedback", tags=["feedback"])


def _classify(request: Request, text: str):
    t0 = time.perf_counter()
    sentiment = request.app.state.classifier.classify(text)
    request.app.state.metrics.record((time.perf_counter() - t0) * 1000)
    return sentiment


@router.post("/classify", response_model=ClassifyPreview)
def preview(body: CreateFeedbackRequest, request: Request, _: AuthedClient = Depends(rate_limited)) -> ClassifyPreview:
    """Classify + tag without storing (dry run)."""
    sentiment = _classify(request, body.text)
    tagging = tag(body.text)
    return ClassifyPreview(text=body.text, sentiment=sentiment.__dict__, category=tagging.category, tags=tagging.tags)


@router.post("", response_model=FeedbackOut, status_code=201)
def create(body: CreateFeedbackRequest, request: Request, client: AuthedClient = Depends(rate_limited),
           db: sqlite3.Connection = Depends(get_db)) -> FeedbackOut:
    """Classify in real time, auto-tag, persist. Returns the stored record."""
    sentiment = _classify(request, body.text)
    tagging = tag(body.text)
    row = FeedbackRepo(db).insert(client_id=client.id, text=body.text, source=body.source,
                                  category=tagging.category, tags=tagging.tags, sentiment=sentiment,
                                  metadata=body.metadata)
    return FeedbackOut(**row)


@router.get("", response_model=FeedbackPage)
def list_feedback(
    client: AuthedClient = Depends(rate_limited),
    db: sqlite3.Connection = Depends(get_db),
    category: Category | None = None,
    sentiment: Label | None = None,
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = None,
) -> FeedbackPage:
    if from_ and to and from_ >= to:
        raise ApiError(400, "bad_request", "from must be before to")
    decoded = None
    if cursor:
        decoded = decode_cursor(cursor)
        if decoded is None:
            raise ApiError(400, "bad_request", "invalid cursor")
    items, next_cursor = FeedbackRepo(db).list(
        client.id, category=category, sentiment=sentiment,
        from_=to_iso(from_) if from_ else None, to=to_iso(to) if to else None, cursor=decoded, limit=limit,
    )
    return FeedbackPage(items=items, next_cursor=next_cursor)


@router.get("/{feedback_id}", response_model=FeedbackOut)
def get_one(feedback_id: int, client: AuthedClient = Depends(rate_limited),
            db: sqlite3.Connection = Depends(get_db)) -> FeedbackOut:
    row = FeedbackRepo(db).get(client.id, feedback_id)
    if row is None:
        raise ApiError(404, "not_found", "feedback not found")
    return FeedbackOut(**row)
