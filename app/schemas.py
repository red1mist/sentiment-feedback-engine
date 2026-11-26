from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from .tagger import Category

Label = Literal["positive", "negative", "neutral"]
Bucket = Literal["hour", "day", "week"]
MAX_TEXT_LEN = 5000


class CreateClientRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    rate_limit_per_minute: int | None = Field(default=None, ge=1, le=100_000)

    @field_validator("name")
    @classmethod
    def strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be blank")
        return v


class ClientCreated(BaseModel):
    client_id: str
    client_secret: str = Field(..., description="shown once; only a hash is stored")
    name: str
    rate_limit_per_minute: int
    created_at: str


class TokenRequest(BaseModel):
    client_id: str = Field(..., min_length=1, max_length=200)
    client_secret: str = Field(..., min_length=1, max_length=500)


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["Bearer"] = "Bearer"
    expires_in: int


class CreateFeedbackRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=MAX_TEXT_LEN)
    source: str = Field(default="api", min_length=1, max_length=50)
    metadata: dict[str, str | int | float | bool] | None = None

    @field_validator("text")
    @classmethod
    def not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("text must not be blank")
        return v


class SentimentOut(BaseModel):
    label: Label
    score: float = Field(..., ge=-1.0, le=1.0, description="polarity, -1 .. 1")
    confidence: float = Field(..., ge=0.0, le=1.0)
    model: str


class FeedbackOut(BaseModel):
    id: int
    text: str
    source: str
    category: Category
    tags: list[str]
    sentiment: SentimentOut
    metadata: dict[str, Any] | None = None
    created_at: str


class ClassifyPreview(BaseModel):
    text: str
    sentiment: SentimentOut
    category: Category
    tags: list[str]


class FeedbackPage(BaseModel):
    items: list[FeedbackOut]
    next_cursor: str | None


class LabelCounts(BaseModel):
    total: int
    positive: int
    negative: int
    neutral: int
    avg_score: float


class CategoryRow(LabelCounts):
    category: str


class TimelineRow(LabelCounts):
    bucket: str


class TagCount(BaseModel):
    tag: str
    count: int


class Window(BaseModel):
    from_: str = Field(..., alias="from")
    to: str
    bucket: Bucket
    category: str | None

    model_config = {"populate_by_name": True}


class SummaryOut(BaseModel):
    window: Window
    totals: LabelCounts
    by_category: list[CategoryRow]
    timeline: list[TimelineRow]
    top_tags: list[TagCount]


class HealthOut(BaseModel):
    status: Literal["ok", "degraded"]
    database: Literal["ok", "down"]
    model: str


class MetricsOut(BaseModel):
    classified: int
    classify_ms_avg: float
    classify_ms_max: float
