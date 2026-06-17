from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator, ConfigDict
from typing_extensions import Literal


class ProviderNewsItem(BaseModel):
    title: str
    description: str | None = None
    link: str
    original_link: str | None = None
    published_at: datetime | None = None
    publisher: str | None = None
    raw_data: dict[str, Any] = Field(default_factory=dict)


class NewsArticleRead(BaseModel):
    id: int
    provider: str
    external_id: str | None
    query: str
    title: str
    description: str | None
    link: str
    original_link: str | None
    publisher: str | None
    published_at: datetime | None
    collected_at: datetime
    available_at: datetime
    title_normalized: str
    content_hash: str
    canonical_url: str | None = None
    normalized_title: str | None = None
    content_fingerprint: str | None = None
    duplicate_group_id: str | None = None
    duplicate_of_article_id: int | None = None
    duplicate_reason: str | None = None
    is_duplicate: bool
    market_relevance_score: float = 1.0
    is_market_relevant: bool = True
    is_analysis_candidate: bool = True
    is_active: bool

    model_config = ConfigDict(from_attributes=True)


class NewsCollectionRequest(BaseModel):
    query: str
    display: int = 50
    sort: Literal["date", "sim"] = "date"
    provider: Literal["naver", "mock"] | None = None

    @field_validator("query")
    def query_length(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 1 or len(v) > 100:
            raise ValueError("query must be 1..100 chars")
        return v

    @field_validator("display")
    def display_range(cls, v: int) -> int:
        if v < 1 or v > 100:
            raise ValueError("display must be 1..100")
        return v


class NewsCollectionResult(BaseModel):
    run_id: str
    provider: str
    query: str
    requested_count: int
    received_count: int
    saved_count: int
    duplicate_count: int
    failed_count: int
    status: str
    started_at: datetime
    completed_at: datetime | None
    duration_ms: int | None


class NewsCollectionRunRead(BaseModel):
    id: int
    run_id: str
    provider: str
    query: str
    requested_count: int
    received_count: int
    saved_count: int
    duplicate_count: int
    failed_count: int
    status: str
    error_message: str | None
    started_at: datetime
    completed_at: datetime | None
    duration_ms: int | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class NewsSummary(BaseModel):
    total_articles: int
    active_articles: int
    duplicate_articles: int
    noise_articles: int = 0
    articles_last_24h: int
    latest_collected_at: datetime | None
    total_collection_runs: int
    failed_collection_runs: int


class NewsDedupeRunResult(BaseModel):
    scanned_count: int
    duplicate_count: int
    noise_count: int
    analysis_candidate_count: int
    updated_count: int


class CollectionStatus(BaseModel):
    enabled: bool
    interval_minutes: int
    last_run_at: datetime | None
    next_run_at: datetime | None
    active_profile_count: int
    total_articles: int
    articles_last_24h: int
    duplicate_articles: int
    total_collection_runs: int
    failed_collection_runs: int


class SearchTermBase(BaseModel):
    query: str
    provider: Literal["naver", "mock"] | None = None
    source_type: Literal["system", "ai", "manual"] = "manual"
    display: int | None = None
    sort: Literal["date", "sim"] = "date"
    is_active: bool = True

    @field_validator("query")
    def query_length(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 1 or len(v) > 100:
            raise ValueError("query must be 1..100 chars")
        return v

    @field_validator("display")
    def display_range(cls, v: int | None) -> int | None:
        if v is None:
            return v
        if v < 1 or v > 100:
            raise ValueError("display must be 1..100")
        return v


class SearchTermCreate(SearchTermBase):
    pass


class SearchTermUpdate(BaseModel):
    query: str | None = None
    provider: Literal["naver", "mock"] | None = None
    source_type: Literal["system", "ai", "manual"] | None = None
    display: int | None = None
    sort: Literal["date", "sim"] | None = None
    is_active: bool | None = None

    @field_validator("query")
    def query_length(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip()
        if len(v) < 1 or len(v) > 100:
            raise ValueError("query must be 1..100 chars")
        return v

    @field_validator("display")
    def display_range(cls, v: int | None) -> int | None:
        if v is None:
            return v
        if v < 1 or v > 100:
            raise ValueError("display must be 1..100")
        return v


class SearchTermRead(BaseModel):
    id: int
    query: str
    provider: str | None
    source_type: Literal["system", "ai", "manual"]
    display: int
    sort: str
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SearchTermSchedulerStatus(BaseModel):
    enabled: bool
    interval_minutes: int
