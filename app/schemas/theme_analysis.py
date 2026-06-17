from __future__ import annotations

from datetime import datetime
from typing_extensions import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ImpactDirection = Literal["positive", "negative", "neutral", "mixed"]
TimeHorizon = Literal["intraday", "short_term", "medium_term", "long_term", "unknown"]


class ThemeEvidence(BaseModel):
    news_analysis_id: int
    relevance_score: float = Field(0.0, ge=0.0, le=1.0)
    reason: str


class EntityBusinessIndustryItem(BaseModel):
    entity: str
    industries: list[str] = Field(default_factory=list, max_length=10)
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    reason: str


class SelectedThemeCandidate(BaseModel):
    theme_name: str
    theme_summary: str
    why_now: str
    impact_direction: ImpactDirection
    confidence_score: float = Field(0.0, ge=0.0, le=1.0)
    time_horizon: TimeHorizon
    related_industries: list[str] = Field(default_factory=list, max_length=10)
    related_companies: list[str] = Field(default_factory=list, max_length=15)
    issue_tags: list[str] = Field(default_factory=list, max_length=15)
    direct_impact_industries: list[str] = Field(default_factory=list, max_length=15)
    entity_business_industries: list[EntityBusinessIndustryItem] = Field(default_factory=list, max_length=15)
    market_theme_tags: list[str] = Field(default_factory=list, max_length=15)
    candidate_search_tags: list[str] = Field(default_factory=list, max_length=30)
    evidence: list[ThemeEvidence] = Field(default_factory=list, max_length=20)
    risk_factors: list[str] = Field(default_factory=list, max_length=5)


class ThemeSelectionOutput(BaseModel):
    market_overview: str
    themes: list[SelectedThemeCandidate] = Field(default_factory=list, max_length=3)
    insufficient_data_reason: str | None = None


class ThemeRunRequest(BaseModel):
    window_hours: int | None = None
    max_sources: int | None = None
    provider: Literal["openai", "mock"] = "openai"
    force: bool = False

    @field_validator("window_hours")
    def validate_window_hours(cls, v: int | None) -> int | None:
        if v is not None and (v < 1 or v > 168):
            raise ValueError("window_hours must be between 1 and 168")
        return v

    @field_validator("max_sources")
    def validate_max_sources(cls, v: int | None) -> int | None:
        if v is not None and (v < 3 or v > 200):
            raise ValueError("max_sources must be between 3 and 200")
        return v


class ThemeRunResponse(BaseModel):
    run_id: str
    status: str
    source_count: int
    selected_theme_count: int
    theme_ids: list[int] = Field(default_factory=list)
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    duration_ms: int | None = None
    insufficient_data_reason: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    model_name: str | None = None
    retryable: bool | None = None


class ThemeEvidenceRead(BaseModel):
    news_analysis_id: int
    news_article_id: int
    title: str
    publisher: str | None
    published_at: datetime | None
    summary: str | None
    relevance_score: float
    evidence_reason: str


class MarketThemeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    theme_run_id: int
    rank: int
    theme_name: str
    normalized_theme_name: str
    theme_summary: str
    why_now: str
    impact_direction: str
    confidence_score: float
    calculated_score: float
    actionability_score: float = 0.0
    price_impact_score: float = 0.0
    investable_link_score: float = 0.0
    is_investable_theme: bool = False
    theme_bucket: str = "low_actionability"
    theme_bucket_reason: str | None = None
    time_horizon: str
    related_industries_json: list[str]
    related_companies_json: list[str]
    risk_factors_json: list[str]
    issue_tags_json: list[str] = Field(default_factory=list)
    direct_impact_industries_json: list[str] = Field(default_factory=list)
    entity_business_industries_json: list[EntityBusinessIndustryItem] = Field(default_factory=list)
    market_theme_tags_json: list[str] = Field(default_factory=list)
    candidate_search_tags_json: list[str] = Field(default_factory=list)
    tag_confidence_json: dict = Field(default_factory=dict)
    evidence_count: int
    source_publisher_count: int
    created_at: datetime


class MarketThemeDetail(MarketThemeRead):
    evidence: list[ThemeEvidenceRead] = Field(default_factory=list)


class ThemeRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    run_id: str
    model_name: str
    prompt_version: str
    window_start: datetime
    window_end: datetime
    requested_source_count: int
    selected_source_count: int
    selected_theme_count: int
    status: str
    market_overview: str | None
    insufficient_data_reason: str | None
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    latency_ms: int | None
    duration_ms: int | None
    error_code: str | None = None
    error_message: str | None
    started_at: datetime
    completed_at: datetime | None
    created_at: datetime


class LatestThemesResponse(BaseModel):
    run: ThemeRunRead | None
    themes: list[MarketThemeDetail] = Field(default_factory=list)


class ThemeCandidateRunRequest(BaseModel):
    theme_run_id: int | None = None
    include_weak_industry_candidates: bool = False
    include_watchlist_themes: bool = False
    include_leveraged_inverse_etfs: bool = True
    max_stock_candidates_per_theme: int = Field(15, ge=1, le=100)
    max_etf_candidates_per_theme: int = Field(20, ge=1, le=100)


class ThemeCandidateRunResponse(BaseModel):
    run_id: str
    status: str
    theme_count: int
    stock_candidate_count: int
    etf_candidate_count: int
    ambiguous_count: int
    unmatched_count: int
    duration_ms: int | None = None


class ThemeSecurityCandidateRead(BaseModel):
    theme_id: int
    theme_name: str | None = None
    security_id: int | None = None
    security_key: str | None = None
    ticker: str | None = None
    name: str
    english_name: str | None = None
    country_code: str | None = None
    asset_type: str | None = None
    exchange_code: str | None = None
    final_candidate_score: float
    match_score: float
    evidence_score: float
    relevance_score: float
    risk_penalty_score: float
    match_status: str
    match_method: str
    source_company_name: str | None = None
    source_keyword: str | None = None
    reason_summary: str | None = None
    risk_flags: list[str] = Field(default_factory=list)


class LatestThemeCandidatesResponse(BaseModel):
    theme_id: int
    theme_name: str
    candidate_diagnostics: dict = Field(default_factory=dict)
    candidates: list[ThemeSecurityCandidateRead] = Field(default_factory=list)
